"""Tests for DockerHostBackend: argv construction (mocked) + live Docker."""

from dataclasses import dataclass, field
import io
from pathlib import Path
import subprocess

import pytest

from swe_lab.sandbox import (
    DockerHostBackend,
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
  handle = DockerHostBackend().up(SPEC, tmp_path)
  assert handle == "container-xyz"
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
  backend = DockerHostBackend(
      network=False,
      pull=False,
      env={"FOO": "bar"},
      pass_env=["SECRET_TOKEN"],
  )
  _ = backend.up(SPEC, tmp_path)
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


def test_up_create_failure_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  fake = _FakeDocker(results=[subprocess.CompletedProcess([], 1, "", "boom")])
  _install(monkeypatch, fake)
  with pytest.raises(SandboxError, match="docker create.*failed"):
    DockerHostBackend(pull=False).up(SPEC, tmp_path)


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
    DockerHostBackend(pull=False).up(SPEC, tmp_path)
  assert fake.last_matching("rm") == ["docker", "rm", "-f", "cid"]


def test_missing_docker_cli_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  _install(monkeypatch, _FakeDocker(raise_missing=True))
  with pytest.raises(SandboxError, match="docker CLI not found"):
    DockerHostBackend(pull=False).up(SPEC, tmp_path)


def test_run_script_argv_runs_workspace_file_and_streams(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  recorded: dict[str, list[str] | None] = {}

  def fake_run(
      argv: list[str], **kwargs: object
  ) -> subprocess.CompletedProcess[str]:
    recorded["argv"] = list(argv)
    out = kwargs.get("stdout")
    if isinstance(out, io.TextIOBase):
      _ = out.write("streamed-line\n")
    return subprocess.CompletedProcess(argv, 0, "", "")

  monkeypatch.setattr(subprocess, "run", fake_run)
  log = tmp_path / "out.log"
  result = DockerHostBackend(mount_at="/ws").run_script(
      "cid", "entryscript.sh", timeout=5.0, env={"X": "1"}, stream_to=log
  )
  argv = recorded["argv"]
  assert isinstance(argv, list)
  assert argv[:3] == ["docker", "exec", "-e"]
  assert "SANDBOX_WORKSPACE=/ws" in argv
  assert "X=1" in argv
  # runs the workspace file by its in-container path (not stdin)
  assert argv[-3:] == ["cid", "/bin/bash", "/ws/entryscript.sh"]
  assert result.stdout == ""  # streamed, not captured
  assert log.read_text() == "streamed-line\n"


def test_run_script_timeout_maps_to_124(monkeypatch: pytest.MonkeyPatch):
  def fake_run(
      argv: list[str], **kwargs: object
  ) -> subprocess.CompletedProcess[str]:
    timeout = kwargs.get("timeout")
    secs = timeout if isinstance(timeout, (int, float)) else 0.0
    raise subprocess.TimeoutExpired(argv, secs, stderr="slow")

  monkeypatch.setattr(subprocess, "run", fake_run)
  result = DockerHostBackend().run_script("cid", "slow.sh", timeout=1.0)
  assert result.exit_code == 124
  assert result.timed_out is True
  assert result.ok is False


def test_down_never_raises(monkeypatch: pytest.MonkeyPatch):
  fake = _FakeDocker(
      results=[subprocess.CompletedProcess([], 1, "", "no such")]
  )
  _install(monkeypatch, fake)
  DockerHostBackend().down("gone-cid")  # must not raise
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
  backend = DockerHostBackend()
  handle = backend.up(spec, workspace)
  try:
    # a staged script writes a file into the workspace via SANDBOX_WORKSPACE
    _stage(workspace, "write.sh", 'echo hello > "$SANDBOX_WORKSPACE"/out.txt')
    first = backend.run_script(handle, "write.sh", timeout=30.0)
    assert first.ok
    assert (workspace / "out.txt").read_text().strip() == "hello"
    # a second run sees the first's container state (persistence)
    _stage(workspace, "touch.sh", "touch /tmp/marker")
    _ = backend.run_script(handle, "touch.sh", timeout=30.0)
    _stage(workspace, "check.sh", "test -f /tmp/marker")
    second = backend.run_script(handle, "check.sh", timeout=30.0)
    assert second.ok
    # a nonzero script reports its exit code faithfully
    _stage(workspace, "fail.sh", "exit 7")
    failing = backend.run_script(handle, "fail.sh", timeout=30.0)
    assert failing.exit_code == 7
  finally:
    backend.down(handle)


@pytest.mark.docker
def test_live_manager_teardown_on_body_error(tmp_path: Path):
  spec = SandboxSpec("debian-teardown", _IMAGE, "/", "none")
  mgr = SandboxManager(
      spec=spec, backend=DockerHostBackend(), workspace=tmp_path / "ws"
  )
  with mgr.sandbox() as sb:
    handle = sb.handle
    _boom(ValueError("body boom"))
  assert mgr.result.status is RunStatus.RUN_ERROR
  # the container is gone: inspecting it fails
  probe = subprocess.run(
      ["docker", "inspect", handle],
      capture_output=True,
      text=True,
      check=False,
  )
  assert probe.returncode != 0


@pytest.mark.docker
def test_no_orphan_containers_left(tmp_path: Path):
  spec = SandboxSpec("debian-orphan", _IMAGE, "/", "none")
  mgr = SandboxManager(
      spec=spec,
      backend=DockerHostBackend(),
      workspace=tmp_path / "ws",
      mounts={"noop.sh": Mount(content=b"true\n")},
  )
  with mgr.sandbox() as sb:
    _ = sb.run("noop.sh", timeout=30.0)
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
