"""Tests for GitHubJobSandbox — real local bash, no Docker (the job is a shell).

Because A-ghjob execs in the local shell, these are genuine end-to-end runs that
need no Docker and no marker: they stage a script, run it, and observe output.
"""

from pathlib import Path
import stat

import pytest

from swe_lab.sandbox import (
    GitHubJobSandbox,
    Inline,
    LocalFile,
    Mount,
    RunStatus,
    SandboxManager,
    SandboxSpec,
)

SPEC = SandboxSpec("acme__widget-1", "acme/widget:tag", "/app", "abc123")


def _workspace(tmp_path: Path) -> Path:
  ws = tmp_path / "ws"
  ws.mkdir()
  return ws


def test_mount_places_assets_read_only(tmp_path: Path):
  # an asset is a read-only mount now; a fixed absolute path the test can
  # actually write to (not /opt), for both an inline and a local-file resource
  inline_at = tmp_path / "assets" / "gen.txt"
  file_src = tmp_path / "src.bin"
  _ = file_src.write_bytes(b"BIN")
  file_at = tmp_path / "assets" / "bin"
  sandbox = GitHubJobSandbox(spec=SPEC, workspace=_workspace(tmp_path))
  sandbox.up()
  sandbox.mount(
      {
          str(inline_at): Mount(Inline(b"hi"), read_only=True),
          str(file_at): Mount(LocalFile(file_src), read_only=True),
      }
  )
  assert inline_at.read_bytes() == b"hi"
  assert file_at.read_bytes() == b"BIN"
  for placed in (inline_at, file_at):
    mode = placed.stat().st_mode
    assert not mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)


def test_mount_preserves_executable_asset(tmp_path: Path):
  # an executable read-only asset (the pinned agent binary) lands at 0o555 —
  # executable so the binary runs, read-only so the run cannot modify it
  src = tmp_path / "claude"
  _ = src.write_bytes(b"#!/bin/sh\necho ok\n")
  src.chmod(0o755)
  at = tmp_path / "opt" / "claude"
  sandbox = GitHubJobSandbox(spec=SPEC, workspace=_workspace(tmp_path))
  sandbox.up()
  sandbox.mount(
      {str(at): Mount(LocalFile(src), executable=True, read_only=True)}
  )
  mode = at.stat().st_mode
  assert mode & stat.S_IXUSR  # executable
  assert not mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)  # read-only


def test_run_script_by_workspace_path_with_env(tmp_path: Path):
  ws = _workspace(tmp_path)
  sandbox = GitHubJobSandbox(spec=SPEC, workspace=ws, env={"X": "1"})
  sandbox.up()
  _ = (ws / "main.sh").write_text(
      'echo "ws=$SANDBOX_WORKSPACE x=$X tok=$TOK"\n'
  )
  result = sandbox.run_script("main.sh", timeout=5.0, env={"TOK": "t"})
  assert result.ok
  assert f"ws={ws}" in result.stdout
  assert "x=1" in result.stdout  # sandbox env
  assert "tok=t" in result.stdout  # per-run env


def test_run_command_inline_with_env(tmp_path: Path):
  ws = _workspace(tmp_path)
  sandbox = GitHubJobSandbox(spec=SPEC, workspace=ws, env={"X": "1"})
  sandbox.up()
  result = sandbox.run_command('echo "x=$X"', timeout=5.0)
  assert result.ok
  assert "x=1" in result.stdout  # sandbox env visible to an inline command


def test_run_script_streams_stdout_to_file(tmp_path: Path):
  ws = _workspace(tmp_path)
  log = tmp_path / "out.log"
  sandbox = GitHubJobSandbox(spec=SPEC, workspace=ws)
  sandbox.up()
  _ = (ws / "main.sh").write_text("echo streamed-line\n")
  result = sandbox.run_script("main.sh", timeout=5.0, stream_to=log)
  assert result.stdout == ""  # streamed, not captured
  assert log.read_text() == "streamed-line\n"


def test_run_script_nonzero_exit_is_reported(tmp_path: Path):
  ws = _workspace(tmp_path)
  sandbox = GitHubJobSandbox(spec=SPEC, workspace=ws)
  sandbox.up()
  _ = (ws / "main.sh").write_text("echo boom >&2\nexit 3\n")
  result = sandbox.run_script("main.sh", timeout=5.0)
  assert result.exit_code == 3
  assert not result.ok
  assert "boom" in result.stderr


def test_run_script_timeout_maps_to_124(tmp_path: Path):
  ws = _workspace(tmp_path)
  sandbox = GitHubJobSandbox(spec=SPEC, workspace=ws)
  sandbox.up()
  _ = (ws / "main.sh").write_text("sleep 5\n")
  result = sandbox.run_script("main.sh", timeout=0.2)
  assert result.exit_code == 124
  assert result.timed_out is True


def test_pass_env_inherits_by_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
  monkeypatch.setenv("SECRET_TOKEN", "s3cr3t")
  ws = _workspace(tmp_path)
  sandbox = GitHubJobSandbox(spec=SPEC, workspace=ws, pass_env=["SECRET_TOKEN"])
  sandbox.up()
  _ = (ws / "main.sh").write_text('echo "tok=$SECRET_TOKEN"\n')
  result = sandbox.run_script("main.sh", timeout=5.0)
  assert "tok=s3cr3t" in result.stdout


def test_down_never_raises(tmp_path: Path):
  GitHubJobSandbox(spec=SPEC, workspace=_workspace(tmp_path)).down()  # no throw


def test_manager_composition_runs_end_to_end(tmp_path: Path):
  # the whole engine over the real sandbox: a staged main writes an artifact
  ws = tmp_path / "run"
  manager = SandboxManager(
      sandbox=GitHubJobSandbox(spec=SPEC, workspace=ws),
      output_dir=ws,
      mounts={
          "main.sh": Mount(
              Inline(b'echo done > "$SANDBOX_WORKSPACE/out.txt"\n'),
              executable=True,
          )
      },
  )
  with manager.session() as sb:
    _ = sb.run_script("main.sh", timeout=5.0)
  assert manager.result.status is RunStatus.SUCCESS
  assert (ws / "out.txt").read_text() == "done\n"
