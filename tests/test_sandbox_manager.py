"""Lifecycle tests for SandboxManager: ordering + the full failure matrix.

Each row of the failure matrix (the semantics spelled out in the manager's
module docstring) is one test here, asserting which hooks ran, whether
teardown happened, and the resulting status/error.
"""

from pathlib import Path
from typing import Any, override

import pytest

from swe_lab.sandbox import (
    Contribution,
    DockerHostSandbox,
    ExecResult,
    GitHubJobSandbox,
    Inline,
    LocalFile,
    Mount,
    RunStatus,
    SandboxError,
    SandboxManager,
    SandboxSpec,
)
from swe_lab.sandbox.testing import FakeSandbox, RecordingObserver

SPEC = SandboxSpec("inst", "img:tag", "/app", "abc123")


def _sandbox(tmp_path: Path, **kwargs: Any) -> FakeSandbox:
  kwargs.setdefault("workspace", tmp_path / "ws")
  return FakeSandbox(spec=SPEC, **kwargs)


def _manager(sb: FakeSandbox, **kwargs: Any) -> SandboxManager:
  kwargs.setdefault("output_dir", sb.workspace)
  return SandboxManager(sandbox=sb, **kwargs)


def _down_ran(sb: FakeSandbox) -> bool:
  return ("down", "inst") in sb.calls


def _boom(error: BaseException) -> None:
  raise error


# ─── the clean run ───────────────────────────────────────────────────────────


def test_clean_run_order_status_and_label(tmp_path: Path):
  events: list[str] = []
  sb = _sandbox(tmp_path)
  mgr = _manager(
      sb,
      observers=[
          RecordingObserver("a", events),
          RecordingObserver("b", events),
      ],
  )
  with mgr.session() as live:
    events.append("body")
    assert live is sb
  assert events == [
      "a.mounts",
      "b.mounts",
      "a.before_create",
      "b.before_create",
      "a.after_create",
      "b.after_create",
      "body",
      "a.before_destroy",
      "b.before_destroy",
      "a.after_destroy",
      "b.after_destroy",
  ]
  assert sb.calls[0] == ("up", "inst")
  assert _down_ran(sb)
  assert mgr.result.status is RunStatus.SUCCESS
  assert mgr.result.error is None
  assert mgr.result.label == "inst"


def test_mounts_materialize_after_up(tmp_path: Path):
  # Up-first (ADR-0003): the sandbox comes up, THEN the mounts are staged, so
  # the mounted file is absent when up() runs and present afterward.
  seen: list[bool] = []

  class Probe(FakeSandbox):

    @override
    def up(self) -> None:
      seen.append((self.workspace / "run.sh").is_file())
      super().up()

  sb = Probe(spec=SPEC, workspace=tmp_path / "ws")
  mgr = _manager(sb, mounts={"run.sh": Mount(Inline(b"hi"))})
  with mgr.session():
    pass
  assert seen == [False]
  assert (sb.workspace / "run.sh").read_bytes() == b"hi"


# ─── the failure matrix, row by row ──────────────────────────────────────────


def test_before_create_raises(tmp_path: Path):
  events: list[str] = []
  sb = _sandbox(tmp_path)
  mgr = _manager(
      sb,
      observers=[RecordingObserver("a", events, raise_in="before_create")],
  )
  with pytest.raises(RuntimeError, match="scripted failure"), mgr.session():
    pytest.fail("body must not run")
  assert sb.calls == []  # not live yet: no up, no down
  assert "a.on_error" not in events
  assert "a.before_destroy" not in events
  assert mgr.result.status is RunStatus.SETUP_ERROR
  assert isinstance(mgr.result.error, RuntimeError)


def test_duplicate_mount_target(tmp_path: Path):
  sb = _sandbox(tmp_path)
  mgr = _manager(
      sb,
      mounts={"x.sh": Mount(Inline(b"a"))},
      observers=[
          RecordingObserver("a", extra_mounts={"x.sh": Mount(Inline(b"b"))})
      ],
  )
  with (
      pytest.raises(SandboxError, match="duplicate mount target"),
      mgr.session(),
  ):
    pytest.fail("body must not run")
  assert sb.calls == []
  assert mgr.result.status is RunStatus.SETUP_ERROR


def test_unstageable_mount_is_setup_error(tmp_path: Path):
  # A mount whose source cannot be transferred fails during mount() — after
  # up() (up-first), before the body — so the run is a SETUP_ERROR that
  # propagates and the sandbox is still torn down.
  sb = _sandbox(tmp_path)
  mgr = _manager(sb, mounts={"agent": Mount(LocalFile(tmp_path / "absent"))})
  with pytest.raises(FileNotFoundError), mgr.session():
    pytest.fail("body must not run")
  assert sb.calls[0] == ("up", "inst")
  assert _down_ran(sb)
  assert mgr.result.status is RunStatus.SETUP_ERROR


def test_sandbox_up_raises(tmp_path: Path):
  events: list[str] = []
  sb = _sandbox(tmp_path, up_error=OSError("docker gone"))
  mgr = _manager(sb, observers=[RecordingObserver("a", events)])
  with pytest.raises(OSError, match="docker gone"), mgr.session():
    pytest.fail("body must not run")
  # up cleans its own partial state; the manager never marked it live, so it
  # neither probes on_error nor calls down.
  assert sb.calls == [("up", "inst")]
  assert "a.before_destroy" not in events
  assert mgr.result.status is RunStatus.SETUP_ERROR


def test_after_create_raises(tmp_path: Path):
  events: list[str] = []
  sb = _sandbox(tmp_path)
  mgr = _manager(
      sb,
      observers=[
          RecordingObserver("a", events, raise_in="after_create"),
          RecordingObserver("b", events),
      ],
  )
  with pytest.raises(RuntimeError, match="scripted failure"), mgr.session():
    pytest.fail("body must not run")
  assert "a.on_error" in events and "b.on_error" in events
  assert "a.before_destroy" in events and "b.before_destroy" in events
  assert _down_ran(sb)
  assert mgr.result.status is RunStatus.SETUP_ERROR


def test_body_exception_is_recorded_not_raised(tmp_path: Path):
  events: list[str] = []
  sb = _sandbox(tmp_path)
  mgr = _manager(sb, observers=[RecordingObserver("a", events)])
  with mgr.session():  # no pytest.raises: the with block exits cleanly
    _boom(ValueError("body boom"))
  assert "a.on_error" in events
  assert "a.before_destroy" in events
  assert _down_ran(sb)
  assert mgr.result.status is RunStatus.RUN_ERROR
  assert isinstance(mgr.result.error, ValueError)


def test_before_destroy_raises_after_clean_body(tmp_path: Path):
  events: list[str] = []
  sb = _sandbox(tmp_path)
  mgr = _manager(
      sb,
      observers=[
          RecordingObserver("a", events, raise_in="before_destroy"),
          RecordingObserver(
              "b", events, contribution=Contribution(metrics={"m": 1.0})
          ),
      ],
  )
  with mgr.session():
    pass
  assert "b.before_destroy" in events  # the rest still ran
  assert _down_ran(sb)
  assert mgr.result.status is RunStatus.RUN_ERROR
  assert isinstance(mgr.result.error, RuntimeError)
  assert mgr.result.metrics == {"m": 1.0}  # surviving contributions kept


def test_before_destroy_raises_after_body_error_keeps_primary(tmp_path: Path):
  sb = _sandbox(tmp_path)
  mgr = _manager(
      sb, observers=[RecordingObserver("a", raise_in="before_destroy")]
  )
  with mgr.session():
    _boom(ValueError("body boom"))
  assert mgr.result.status is RunStatus.RUN_ERROR
  assert isinstance(mgr.result.error, ValueError)  # body error stays primary


def test_sandbox_down_failure_is_swallowed(tmp_path: Path):
  sb = _sandbox(tmp_path, down_error=OSError("rm failed"))
  mgr = _manager(sb)
  with mgr.session():
    pass
  assert mgr.result.status is RunStatus.SUCCESS
  assert mgr.result.error is None


def test_keyboard_interrupt_tears_down_then_propagates(tmp_path: Path):
  sb = _sandbox(tmp_path)
  mgr = _manager(sb)
  with pytest.raises(KeyboardInterrupt), mgr.session():
    _boom(KeyboardInterrupt())
  assert _down_ran(sb)
  assert mgr.result.status is RunStatus.RUN_ERROR


# ─── aggregation ─────────────────────────────────────────────────────────────


def test_contributions_aggregate_across_observers(tmp_path: Path):
  sb = _sandbox(tmp_path)
  sb.workspace.mkdir(parents=True)
  _ = (sb.workspace / "p.diff").write_bytes(b"diff")
  out = tmp_path / "out"
  mgr = _manager(
      sb,
      output_dir=out,
      observers=[
          RecordingObserver(
              "a", contribution=Contribution(artifacts={"patch": "p.diff"})
          ),
          RecordingObserver(
              "b", contribution=Contribution(metrics={"secs": 2.0})
          ),
      ],
  )
  with mgr.session():
    pass
  # The collect step fetches the registered artifact out to the output dir.
  assert ("fetch", "p.diff") in sb.calls
  assert mgr.result.artifacts == {"patch": out / "p.diff"}
  assert (out / "p.diff").read_bytes() == b"diff"
  assert mgr.result.metrics == {"secs": 2.0}


def test_colliding_contributions_error_a_clean_run(tmp_path: Path):
  clash = Contribution(metrics={"secs": 1.0})
  sb = _sandbox(tmp_path)
  mgr = _manager(
      sb,
      observers=[
          RecordingObserver("a", contribution=clash),
          RecordingObserver("b", contribution=clash),
      ],
  )
  with mgr.session():
    pass
  assert mgr.result.status is RunStatus.RUN_ERROR
  assert isinstance(mgr.result.error, SandboxError)


# ─── guards & plumbing ───────────────────────────────────────────────────────


def test_result_gated_until_finished_and_manager_single_run(tmp_path: Path):
  mgr = _manager(_sandbox(tmp_path))
  with pytest.raises(SandboxError, match="not available"):
    _ = mgr.result
  with mgr.session():
    pass
  assert mgr.result.status is RunStatus.SUCCESS
  with pytest.raises(SandboxError, match="already ran"), mgr.session():
    pass


def test_nonempty_workspace_refused_unless_reuse(tmp_path: Path):
  # The refusal moved out of the manager and into each host-like sandbox's
  # up() (ADR-0003); FakeSandbox skips the check, so this is exercised on a
  # real, docker-free GitHubJobSandbox.
  ws = tmp_path / "ws"
  ws.mkdir()
  _ = (ws / "stale.txt").write_text("old")
  refusing = GitHubJobSandbox(spec=SPEC, workspace=ws)
  with pytest.raises(SandboxError, match="not empty"):
    refusing.up()
  reusing = GitHubJobSandbox(spec=SPEC, workspace=ws, reuse=True)
  reusing.up()  # reuse=True runs in the non-empty workspace without raising


def test_run_plumbing_and_streaming(tmp_path: Path):
  sb = _sandbox(tmp_path, run_results=[ExecResult(0, "line1\nline2\n", "")])
  mgr = _manager(sb)
  log = tmp_path / "out.log"
  with mgr.session() as live:
    streamed = live.run_script("entryscript.sh", timeout=5.0, stream_to=log)
  assert sb.scripts == ["entryscript.sh"]
  assert streamed.stdout == ""  # streamed, not captured
  assert log.read_text() == "line1\nline2\n"


def test_run_before_live_raises(tmp_path: Path):
  # The liveness guard lives on the real host sandbox (docker-free until an
  # exec is attempted): exec before up() raises without touching Docker.
  sb = DockerHostSandbox(spec=SPEC, workspace=tmp_path / "ws")
  with pytest.raises(SandboxError, match="not live"):
    sb.run_script("x.sh", timeout=1.0)
