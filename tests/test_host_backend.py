"""Tests for DockerHostSandbox: argv construction (mocked) + live Docker."""

from dataclasses import dataclass, field
from pathlib import Path
import subprocess

from etils import epath
import pytest

from swe_lab.harnesses.claude_code.constants import BINARY_AT
from swe_lab.sandbox import (
    AgentAsset,
    DockerHostSandbox,
    HostMetricsObserver,
    Inline,
    LocalFile,
    Mount,
    MountedAssetsObserver,
    RunStatus,
    SandboxError,
    SandboxManager,
    SandboxSpec,
)

from .conftest import FakeClaudeBinary

SPEC = SandboxSpec("acme__widget-1", "acme/widget:tag", "/app", "abc123")


def _boom(error: BaseException) -> None:
  """Raise ``error`` (indirection so a following assert stays reachable)."""
  raise error


# ─── unit: argv construction with subprocess mocked ──────────────────────────


@dataclass
class _FakeDocker:
  """Records docker argv and replays scripted results in call order."""

  results: list[subprocess.CompletedProcess[str]] = field(default_factory=list)
  calls: list[list[str]] = field(default_factory=list)
  raise_missing: bool = False

  def __call__(
      self, argv: list[str], **kwargs: object
  ) -> subprocess.CompletedProcess[str]:
    del kwargs
    if self.raise_missing:
      raise FileNotFoundError(2, "No such file", "docker")
    self.calls.append(list(argv))
    index = min(len(self.calls) - 1, len(self.results) - 1)
    if self.results:
      return self.results[index]
    return subprocess.CompletedProcess(argv, 0, "", "")

  def last_matching(self, subcommand: str) -> list[str]:
    for argv in reversed(self.calls):
      if argv[:2] == ["docker", subcommand]:
        return argv
    raise AssertionError(f"no docker {subcommand} call recorded")


def _ok(stdout: str = "") -> subprocess.CompletedProcess[str]:
  return subprocess.CompletedProcess([], 0, stdout, "")


def _install(monkeypatch: pytest.MonkeyPatch, fake: _FakeDocker) -> None:
  monkeypatch.setattr(subprocess, "run", fake)


def test_up_argv_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
  fake = _FakeDocker(results=[_ok(), _ok("container-xyz\n"), _ok()])
  _install(monkeypatch, fake)
  sandbox = DockerHostSandbox(spec=SPEC, workspace=epath.Path(tmp_path))
  sandbox.up()
  # the container id is now internal state, not a return value
  assert sandbox._container == "container-xyz"
  pull = fake.last_matching("pull")
  assert pull == ["docker", "pull", "--platform", "linux/amd64", SPEC.image_ref]
  create = fake.last_matching("create")
  assert "--network" not in create  # network on by default
  v = create.index("-v")
  assert create[v : v + 2] == ["-v", f"{tmp_path}:/workspace"]
  assert "--label" in create
  assert f"swe-lab-instance={SPEC.instance_id}" in create
  assert create[-5:] == [
      "--entrypoint",
      "/bin/bash",
      SPEC.image_ref,
      "-c",
      "sleep infinity",
  ]
  assert fake.last_matching("start") == ["docker", "start", "container-xyz"]


def test_up_network_off_env_and_pass_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  fake = _FakeDocker(results=[_ok("cid\n"), _ok()])
  _install(monkeypatch, fake)
  sandbox = DockerHostSandbox(
      spec=SPEC,
      workspace=epath.Path(tmp_path),
      network=False,
      pull=False,
      env={"FOO": "bar"},
      pass_env=["SECRET_TOKEN"],
  )
  sandbox.up()
  create = fake.last_matching("create")
  net = create.index("--network")
  assert create[net : net + 2] == ["--network", "none"]
  foo = create.index("FOO=bar")
  assert create[foo - 1 : foo + 1] == ["-e", "FOO=bar"]
  tok = create.index("SECRET_TOKEN")
  assert create[tok - 1 : tok + 1] == ["-e", "SECRET_TOKEN"]
  # a by-reference secret carries no value in the argv
  assert not any("SECRET_TOKEN=" in a for a in create)
  assert ["docker", "pull"] not in [c[:2] for c in fake.calls]


def test_mount_absolute_asset_copied_read_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  # an asset is a read-only mount at an absolute path now: the host sandbox
  # ``docker cp``s it into the live container, then chmods it read-only there
  fake = _FakeDocker(results=[_ok("cid\n"), _ok()])
  _install(monkeypatch, fake)
  binary = tmp_path / "claude"  # outside the workspace, like the pinned binary
  _ = binary.write_bytes(b"BIN")
  sandbox = DockerHostSandbox(
      spec=SPEC, workspace=epath.Path(tmp_path / "ws"), pull=False
  )
  sandbox.up()
  sandbox.mount(
      {
          "/opt/claude-code/claude": Mount(
              LocalFile(epath.Path(binary)), executable=True, read_only=True
          )
      }
  )
  cp = fake.last_matching("cp")
  assert cp == [
      "docker",
      "cp",
      str(binary),
      "cid:/opt/claude-code/claude",
  ]
  # chmod to 0o555: executable + read-only
  assert fake.last_matching("exec") == [
      "docker",
      "exec",
      "cid",
      "chmod",
      "555",
      "/opt/claude-code/claude",
  ]


def test_up_maps_no_host_gateway(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  # Nothing in a container dials the host anymore: proxy capture runs its
  # proxy inside the sandbox, on the sandbox's own loopback. A container that
  # can still resolve the host gateway is reach we do not use and should not
  # hand out.
  fake = _FakeDocker(results=[_ok("cid\n"), _ok()])
  _install(monkeypatch, fake)
  sandbox = DockerHostSandbox(
      spec=SPEC, workspace=epath.Path(tmp_path), pull=False
  )
  sandbox.up()
  create = fake.last_matching("create")
  assert "--add-host" not in create


def test_up_create_failure_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  fake = _FakeDocker(results=[subprocess.CompletedProcess([], 1, "", "boom")])
  _install(monkeypatch, fake)
  with pytest.raises(SandboxError, match="docker create.*failed"):
    DockerHostSandbox(
        spec=SPEC, workspace=epath.Path(tmp_path), pull=False
    ).up()


def test_up_start_failure_removes_partial_container(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  fake = _FakeDocker(
      results=[
          _ok("cid\n"),  # create ok
          subprocess.CompletedProcess([], 1, "", "cannot start"),  # start fails
          _ok(),  # rm (cleanup)
      ]
  )
  _install(monkeypatch, fake)
  with pytest.raises(SandboxError, match="docker start.*failed"):
    DockerHostSandbox(
        spec=SPEC, workspace=epath.Path(tmp_path), pull=False
    ).up()
  assert fake.last_matching("rm") == ["docker", "rm", "-f", "cid"]


def test_missing_docker_cli_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  _install(monkeypatch, _FakeDocker(raise_missing=True))
  with pytest.raises(SandboxError, match="docker CLI not found"):
    DockerHostSandbox(
        spec=SPEC, workspace=epath.Path(tmp_path), pull=False
    ).up()


def test_run_script_argv_runs_workspace_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  recorded: dict[str, list[str]] = {}

  def fake_run(
      argv: list[str], **kwargs: object
  ) -> subprocess.CompletedProcess[str]:
    del kwargs
    recorded["argv"] = list(argv)
    return subprocess.CompletedProcess(argv, 0, "captured-out\n", "")

  monkeypatch.setattr(subprocess, "run", fake_run)
  sandbox = DockerHostSandbox(
      spec=SPEC, workspace=epath.Path(tmp_path), mount_at="/ws"
  )
  sandbox._container = "cid"  # pretend it is live
  result = sandbox.run_script("entryscript.sh", timeout=5.0, env={"X": "1"})
  argv = recorded["argv"]
  assert argv[:3] == ["docker", "exec", "-e"]
  assert "SANDBOX_WORKSPACE=/ws" in argv
  assert "X=1" in argv
  # runs the workspace file by its in-container path (not stdin)
  assert argv[-3:] == ["cid", "/bin/bash", "/ws/entryscript.sh"]
  assert result.stdout == "captured-out\n"  # captured in the result


def test_run_command_argv_runs_inline_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  recorded: dict[str, list[str]] = {}

  def fake_run(
      argv: list[str], **kwargs: object
  ) -> subprocess.CompletedProcess[str]:
    del kwargs
    recorded["argv"] = list(argv)
    return subprocess.CompletedProcess(argv, 0, "ok\n", "")

  monkeypatch.setattr(subprocess, "run", fake_run)
  sandbox = DockerHostSandbox(
      spec=SPEC, workspace=epath.Path(tmp_path), mount_at="/ws"
  )
  sandbox._container = "cid"  # pretend it is live
  result = sandbox.run_command("echo ok", timeout=5.0)
  argv = recorded["argv"]
  assert argv[:3] == ["docker", "exec", "-e"]
  assert "SANDBOX_WORKSPACE=/ws" in argv
  # runs the command inline under the shell (<shell> -c command)
  assert argv[-4:] == ["cid", "/bin/bash", "-c", "echo ok"]
  assert result.stdout == "ok\n"


def test_run_script_timeout_maps_to_124(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  def fake_run(
      argv: list[str], **kwargs: object
  ) -> subprocess.CompletedProcess[str]:
    timeout = kwargs.get("timeout")
    secs = timeout if isinstance(timeout, (int, float)) else 0.0
    raise subprocess.TimeoutExpired(argv, secs, stderr="slow")

  monkeypatch.setattr(subprocess, "run", fake_run)
  sandbox = DockerHostSandbox(spec=SPEC, workspace=epath.Path(tmp_path))
  sandbox._container = "cid"  # pretend it is live
  result = sandbox.run_script("slow.sh", timeout=1.0)
  assert result.exit_code == 124
  assert result.timed_out is True
  assert result.ok is False


def test_down_never_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
  fake = _FakeDocker(
      results=[subprocess.CompletedProcess([], 1, "", "no such")]
  )
  _install(monkeypatch, fake)
  sandbox = DockerHostSandbox(spec=SPEC, workspace=epath.Path(tmp_path))
  sandbox._container = "gone-cid"
  sandbox.down()  # must not raise
  assert fake.last_matching("rm") == ["docker", "rm", "-f", "gone-cid"]


# ─── integration: a real Docker daemon (auto-skipped when absent) ────────────

_IMAGE = "debian:stable-slim"


def _stage(workspace: Path, name: str, script: str) -> None:
  """Write a script into the workspace (as a mount would), for run_script."""
  _ = (workspace / name).write_text(script)


@pytest.mark.docker
def test_live_run_script_writes_and_persists_state(tmp_path: Path):
  spec = SandboxSpec("debian-probe", _IMAGE, "/", "none")
  workspace = tmp_path / "ws"
  workspace.mkdir()
  sandbox = DockerHostSandbox(spec=spec, workspace=epath.Path(workspace))
  sandbox.up()
  try:
    # a staged script writes a file into the workspace via SANDBOX_WORKSPACE
    _stage(workspace, "write.sh", 'echo hello > "$SANDBOX_WORKSPACE"/out.txt')
    first = sandbox.run_script("write.sh", timeout=30.0)
    assert first.ok
    assert (workspace / "out.txt").read_text().strip() == "hello"
    # a second run sees the first's container state (persistence)
    _stage(workspace, "touch.sh", "touch /tmp/marker")
    _ = sandbox.run_script("touch.sh", timeout=30.0)
    _stage(workspace, "check.sh", "test -f /tmp/marker")
    second = sandbox.run_script("check.sh", timeout=30.0)
    assert second.ok
    # a nonzero script reports its exit code faithfully
    _stage(workspace, "fail.sh", "exit 7")
    failing = sandbox.run_script("fail.sh", timeout=30.0)
    assert failing.exit_code == 7
  finally:
    sandbox.down()


@pytest.mark.docker
def test_live_manager_teardown_on_body_error(tmp_path: Path):
  spec = SandboxSpec("debian-teardown", _IMAGE, "/", "none")
  sandbox = DockerHostSandbox(spec=spec, workspace=epath.Path(tmp_path / "ws"))
  mgr = SandboxManager(sandbox=sandbox, output_dir=epath.Path(tmp_path / "out"))
  with mgr.session():
    container = sandbox._container  # the concrete host sandbox's container id
    _boom(ValueError("body boom"))
  assert mgr.result.status is RunStatus.RUN_ERROR
  # the container is gone: inspecting it fails
  probe = subprocess.run(
      ["docker", "inspect", container],
      capture_output=True,
      text=True,
      check=False,
  )
  assert probe.returncode != 0


@pytest.mark.docker
def test_no_orphan_containers_left(tmp_path: Path):
  spec = SandboxSpec("debian-orphan", _IMAGE, "/", "none")
  ws = tmp_path / "ws"
  mgr = SandboxManager(
      sandbox=DockerHostSandbox(spec=spec, workspace=epath.Path(ws)),
      output_dir=epath.Path(ws),
      mounts={"noop.sh": Mount(Inline(b"true\n"))},
  )
  with mgr.session() as sb:
    _ = sb.run_script("noop.sh", timeout=30.0)
  leftover = subprocess.run(
      [
          "docker",
          "ps",
          "-aq",
          "--filter",
          "label=swe-lab-instance=debian-orphan",
      ],
      capture_output=True,
      text=True,
      check=False,
  )
  assert leftover.stdout.strip() == ""


# ─── the backend's own observers (ADR-0007 §3, backend source) ───────────────


def _fake_materialize(dest: epath.Path | None) -> epath.Path:
  """Stand in for a harness's ``ensure_*``: honors the seam's contract."""
  from swe_lab.harnesses.claude_code.binary import ensure_claude_binary

  return ensure_claude_binary(dest=dest)


def test_backend_contributes_only_what_only_it_can_measure(tmp_path: Path):
  # The agent binary used to be here, hardcoded to one harness. It now arrives
  # through the provisioning seam, so a container that runs no agent (a
  # grading run, an audit) carries nothing extra.
  sandbox = DockerHostSandbox(spec=SPEC, workspace=epath.Path(tmp_path))
  assert [type(o).__name__ for o in sandbox.observers()] == [
      "HostMetricsObserver"
  ]


def test_this_backend_answers_assets_by_mounting_a_host_copy(
    tmp_path: Path, fake_claude_binary: FakeClaudeBinary
):
  # A container cannot fetch its own bytes, so this backend's answer to ANY
  # declared asset is a host copy handed over as a mount — it never learns
  # which agent asked.
  sandbox = DockerHostSandbox(spec=SPEC, workspace=epath.Path(tmp_path))
  binary = sandbox.asset_observer(
      (
          AgentAsset(
              path=BINARY_AT,
              version="2.1.212",
              fetch=_fake_materialize,
          ),
      )
  )
  assert isinstance(binary, MountedAssetsObserver)
  # It takes the HOST copy (no dest asked for) and hands it over as a mount —
  # a container cannot fetch its own, so the bytes have to travel here.
  assert binary.mounts() == {
      BINARY_AT: Mount(
          LocalFile(fake_claude_binary.cached), executable=True, read_only=True
      )
  }
  assert fake_claude_binary.destinations == [None]


def _metrics_observer(sandbox: DockerHostSandbox) -> HostMetricsObserver:
  """Pick the metrics observer out of the backend's contributions."""
  observer = next(
      o for o in sandbox.observers() if isinstance(o, HostMetricsObserver)
  )
  return observer


def _metrics_setup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake: _FakeDocker
) -> HostMetricsObserver:
  """Up a sandbox with the manager's hook order; return its observer."""
  _install(monkeypatch, fake)
  sandbox = DockerHostSandbox(spec=SPEC, workspace=epath.Path(tmp_path))
  observer = _metrics_observer(sandbox)
  # Mirror the manager: before_create → up → after_create, so the setup
  # window contains the pull the way it does in a real run.
  observer.before_create(sandbox)
  sandbox.up()
  observer.after_create(sandbox)
  return observer


def test_metrics_read_cgroup_peak_and_oom_via_the_live_container(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  fake = _FakeDocker(
      results=[
          _ok(),  # pull
          _ok("container-xyz\n"),  # create
          _ok(),  # start
          _ok("123456789\n"),  # exec cat memory.peak
          _ok("low 0\noom 2\noom_kill 2\n"),  # exec cat memory.events
          _ok("false\n"),  # inspect OOMKilled
      ]
  )
  observer = _metrics_setup(monkeypatch, tmp_path, fake)
  contribution = observer.before_destroy(observer.sandbox)
  assert contribution is not None
  metrics = contribution.metrics
  assert metrics["sandbox.peak_memory_bytes"] == 123456789.0
  # the cgroup counter wins over the (false) inspect flag: an exec'd process
  # OOM-killed mid-run counts even though the container survived it
  assert metrics["sandbox.oom_kills"] == 2.0
  assert metrics["sandbox.setup_seconds"] >= 0.0
  assert metrics["sandbox.pull_seconds"] >= 0.0


def test_metrics_degrade_to_fewer_never_raise(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  # Every read fails; the observer must contribute nothing rather than fail
  # a graded run. The failing CompletedProcess replays for every later call.
  fake = _FakeDocker(
      results=[
          _ok(),
          _ok("container-xyz\n"),
          _ok(),
          subprocess.CompletedProcess([], 1, "", "boom"),
      ]
  )
  observer = _metrics_setup(monkeypatch, tmp_path, fake)
  contribution = observer.before_destroy(observer.sandbox)
  # every docker read failed: only the timings survive, and nothing raised
  assert contribution is not None
  assert set(contribution.metrics) <= {
      "sandbox.setup_seconds",
      "sandbox.pull_seconds",
  }


@pytest.mark.docker
def test_live_run_records_runtime_metrics(tmp_path: Path):
  spec = SandboxSpec("debian-metrics", _IMAGE, "/", "none")
  ws = tmp_path / "ws"
  sandbox = DockerHostSandbox(spec=spec, workspace=epath.Path(ws), pull=False)
  mgr = SandboxManager(
      sandbox=sandbox,
      output_dir=epath.Path(ws),
      observers=list(sandbox.observers()),
      mounts={"noop.sh": Mount(Inline(b"true\n"))},
  )
  with mgr.session() as sb:
    _ = sb.run_script("noop.sh", timeout=30.0)
  metrics = mgr.result.metrics
  assert metrics["sandbox.setup_seconds"] > 0.0
  assert metrics.get("sandbox.oom_kills", 0.0) == 0.0
  # peak memory is tiered (cgroup v2 → v1); assert it only when measurable,
  # but if present it must be a sane positive number
  if "sandbox.peak_memory_bytes" in metrics:
    assert metrics["sandbox.peak_memory_bytes"] > 0.0


@pytest.mark.docker
def test_live_oom_kill_of_an_exec_is_counted(tmp_path: Path):
  # The de49d486 blind spot, reproduced on purpose: an exec'd process is
  # OOM-killed mid-run while the container itself survives. `docker inspect`
  # alone misses this (`State.OOMKilled` stays false); the cgroup's
  # `oom_kill` counter is why the metric reads `memory.events` first.
  #
  # The memory cap goes on via `docker update` *after* start, so no
  # construction knob is added just for this test.
  spec = SandboxSpec("debian-oom", _IMAGE, "/", "none")
  sandbox = DockerHostSandbox(
      spec=spec, workspace=epath.Path(tmp_path / "ws"), pull=False
  )
  observer = _metrics_observer(sandbox)
  observer.before_create(sandbox)
  sandbox.up()
  observer.after_create(sandbox)
  try:
    capped = subprocess.run(
        [
            "docker",
            "update",
            "--memory",
            "32m",
            "--memory-swap",
            "32m",
            sandbox._container,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if capped.returncode != 0:
      pytest.skip(f"docker update cannot cap memory here: {capped.stderr}")
    events = sandbox.run_command(
        "cat /sys/fs/cgroup/memory.events", timeout=10.0
    )
    if not events.ok:
      pytest.skip("cgroup v2 memory.events not readable in this container")
    # `tail /dev/zero` buffers unboundedly: the canonical in-cgroup OOM.
    sandbox.write("hog.sh", b"tail /dev/zero\n")
    hog = sandbox.run_script("hog.sh", timeout=60.0)
    assert not hog.ok  # SIGKILLed by the cgroup OOM killer
    contribution = observer.before_destroy(sandbox)
  finally:
    sandbox.down()
  assert contribution is not None
  assert contribution.metrics["sandbox.oom_kills"] >= 1.0
  # ...and the container itself was never the casualty: the setup metric is
  # still there, from a sandbox that stayed up throughout
  assert contribution.metrics["sandbox.setup_seconds"] >= 0.0


@pytest.mark.docker
def test_live_absolute_mount_creates_missing_parent_dirs(tmp_path: Path):
  # The regression the first live rollout after the transfer seam hit: an
  # asset mounted at an absolute path (the pinned agent binary at
  # /opt/claude-code/claude) lands in a container whose image has no such
  # directory, and `docker cp` refuses a destination with no parent.
  spec = SandboxSpec("debian-absmount", _IMAGE, "/", "none")
  workspace = tmp_path / "ws"
  workspace.mkdir()
  binary = tmp_path / "tool"
  _ = binary.write_text("#!/bin/sh\necho ran\n")
  sandbox = DockerHostSandbox(spec=spec, workspace=epath.Path(workspace))
  sandbox.up()
  try:
    sandbox.mount(
        {
            # two missing directory levels, from a file on the host
            "/opt/probe-dir/bin/tool": Mount(
                LocalFile(epath.Path(binary)), executable=True, read_only=True
            ),
            # and from inline bytes (the other transfer path)
            "/opt/probe-dir/etc/config": Mount(Inline(b"data\n")),
        }
    )
    ran = sandbox.run_command("/opt/probe-dir/bin/tool", timeout=30.0)
    assert ran.ok and ran.stdout.strip() == "ran"
    config = sandbox.run_command("cat /opt/probe-dir/etc/config", timeout=30.0)
    assert config.ok and config.stdout == "data\n"
    # read-only made it through the cp path too
    mode = sandbox.run_command(
        "stat -c %a /opt/probe-dir/bin/tool", timeout=30.0
    )
    assert mode.stdout.strip() == "555"
  finally:
    sandbox.down()
