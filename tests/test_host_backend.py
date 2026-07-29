"""Tests for DockerHostSandbox: argv construction (mocked) + live Docker."""

from dataclasses import dataclass, field
from pathlib import Path
import subprocess

from etils import epath
import pytest

from swe_lab.sandbox import (
    DockerHostSandbox,
    Inline,
    LocalFile,
    Mount,
    RunStatus,
    SandboxError,
    SandboxManager,
    SandboxSpec,
)

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


def test_up_always_maps_host_gateway(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  # Every container maps host.docker.internal so a host-side proxy is always
  # reachable; construction stays independent of the capture mode.
  fake = _FakeDocker(results=[_ok("cid\n"), _ok()])
  _install(monkeypatch, fake)
  sandbox = DockerHostSandbox(
      spec=SPEC, workspace=epath.Path(tmp_path), pull=False
  )
  sandbox.up()
  create = fake.last_matching("create")
  at = create.index("host.docker.internal:host-gateway")
  assert create[at - 1 : at + 1] == [
      "--add-host",
      "host.docker.internal:host-gateway",
  ]


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
