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
    ExecResult,
    Mount,
    RunStatus,
    SandboxError,
    SandboxManager,
    SandboxSpec,
)
from swe_lab.sandbox.manager import Sandbox
from swe_lab.sandbox.testing import FakeBackend, RecordingObserver

SPEC = SandboxSpec("inst", "img:tag", "/app", "abc123")


def _manager(tmp_path: Path, **kwargs: Any) -> SandboxManager:
  kwargs.setdefault("backend", FakeBackend())
  return SandboxManager(spec=SPEC, workspace=tmp_path / "ws", **kwargs)


def _down_ran(backend: FakeBackend) -> bool:
  return ("down", "fake-inst") in backend.calls


def _boom(error: BaseException) -> None:
  raise error


# ─── the clean run ───────────────────────────────────────────────────────────


def test_clean_run_order_status_and_label(tmp_path: Path):
  events: list[str] = []
  backend = FakeBackend()
  mgr = _manager(
      tmp_path,
      backend=backend,
      observers=[
          RecordingObserver("a", events),
          RecordingObserver("b", events),
      ],
  )
  with mgr.sandbox() as sb:
    events.append("body")
    assert sb.handle == "fake-inst"
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
  assert backend.calls[0] == ("up", "inst")
  assert _down_ran(backend)
  assert mgr.result.status is RunStatus.SUCCESS
  assert mgr.result.error is None
  assert mgr.result.label == "inst"


def test_mounts_materialize_before_up(tmp_path: Path):
  seen: list[bool] = []

  class Probe(FakeBackend):

    @override
    def up(self, spec: SandboxSpec, workspace: Path) -> str:
      seen.append((workspace / "run.sh").is_file())
      return super().up(spec, workspace)

  backend = Probe()
  mgr = _manager(
      tmp_path, backend=backend, mounts={"run.sh": Mount(content=b"hi")}
  )
  with mgr.sandbox():
    pass
  assert seen == [True]


# ─── the failure matrix, row by row ──────────────────────────────────────────


def test_before_create_raises(tmp_path: Path):
  events: list[str] = []
  backend = FakeBackend()
  mgr = _manager(
      tmp_path,
      backend=backend,
      observers=[RecordingObserver("a", events, raise_in="before_create")],
  )
  with pytest.raises(RuntimeError, match="scripted failure"), mgr.sandbox():
    pytest.fail("body must not run")
  assert backend.calls == []  # no up, no down
  assert "a.on_error" not in events
  assert "a.before_destroy" not in events
  assert mgr.result.status is RunStatus.SETUP_ERROR
  assert isinstance(mgr.result.error, RuntimeError)


def test_duplicate_mount_target(tmp_path: Path):
  backend = FakeBackend()
  mgr = _manager(
      tmp_path,
      backend=backend,
      mounts={"x.sh": Mount(content=b"a")},
      observers=[
          RecordingObserver("a", extra_mounts={"x.sh": Mount(content=b"b")})
      ],
  )
  with (
      pytest.raises(SandboxError, match="duplicate mount target"),
      mgr.sandbox(),
  ):
    pytest.fail("body must not run")
  assert backend.calls == []
  assert mgr.result.status is RunStatus.SETUP_ERROR


def test_materialize_missing_source(tmp_path: Path):
  backend = FakeBackend()
  mgr = _manager(
      tmp_path,
      backend=backend,
      mounts={"agent": Mount(source=tmp_path / "absent")},
  )
  with pytest.raises(SandboxError, match="does not exist"), mgr.sandbox():
    pytest.fail("body must not run")
  assert backend.calls == []
  assert mgr.result.status is RunStatus.SETUP_ERROR


def test_backend_up_raises(tmp_path: Path):
  events: list[str] = []
  backend = FakeBackend(up_error=OSError("docker gone"))
  mgr = _manager(
      tmp_path, backend=backend, observers=[RecordingObserver("a", events)]
  )
  with pytest.raises(OSError, match="docker gone"), mgr.sandbox():
    pytest.fail("body must not run")
  # up cleans its own partial state; the manager has no handle to down.
  assert backend.calls == [("up", "inst")]
  assert "a.before_destroy" not in events
  assert mgr.result.status is RunStatus.SETUP_ERROR


def test_after_create_raises(tmp_path: Path):
  events: list[str] = []
  backend = FakeBackend()
  mgr = _manager(
      tmp_path,
      backend=backend,
      observers=[
          RecordingObserver("a", events, raise_in="after_create"),
          RecordingObserver("b", events),
      ],
  )
  with pytest.raises(RuntimeError, match="scripted failure"), mgr.sandbox():
    pytest.fail("body must not run")
  assert "a.on_error" in events and "b.on_error" in events
  assert "a.before_destroy" in events and "b.before_destroy" in events
  assert _down_ran(backend)
  assert mgr.result.status is RunStatus.SETUP_ERROR


def test_body_exception_is_recorded_not_raised(tmp_path: Path):
  events: list[str] = []
  backend = FakeBackend()
  mgr = _manager(
      tmp_path, backend=backend, observers=[RecordingObserver("a", events)]
  )
  with mgr.sandbox():  # no pytest.raises: the with block exits cleanly
    _boom(ValueError("body boom"))
  assert "a.on_error" in events
  assert "a.before_destroy" in events
  assert _down_ran(backend)
  assert mgr.result.status is RunStatus.RUN_ERROR
  assert isinstance(mgr.result.error, ValueError)


def test_before_destroy_raises_after_clean_body(tmp_path: Path):
  events: list[str] = []
  backend = FakeBackend()
  mgr = _manager(
      tmp_path,
      backend=backend,
      observers=[
          RecordingObserver("a", events, raise_in="before_destroy"),
          RecordingObserver(
              "b", events, contribution=Contribution(metrics={"m": 1.0})
          ),
      ],
  )
  with mgr.sandbox():
    pass
  assert "b.before_destroy" in events  # the rest still ran
  assert _down_ran(backend)
  assert mgr.result.status is RunStatus.RUN_ERROR
  assert isinstance(mgr.result.error, RuntimeError)
  assert mgr.result.metrics == {"m": 1.0}  # surviving contributions kept


def test_before_destroy_raises_after_body_error_keeps_primary(tmp_path: Path):
  backend = FakeBackend()
  mgr = _manager(
      tmp_path,
      backend=backend,
      observers=[RecordingObserver("a", raise_in="before_destroy")],
  )
  with mgr.sandbox():
    _boom(ValueError("body boom"))
  assert mgr.result.status is RunStatus.RUN_ERROR
  assert isinstance(mgr.result.error, ValueError)  # body error stays primary


def test_backend_down_failure_is_swallowed(tmp_path: Path):
  backend = FakeBackend(down_error=OSError("rm failed"))
  mgr = _manager(tmp_path, backend=backend)
  with mgr.sandbox():
    pass
  assert mgr.result.status is RunStatus.SUCCESS
  assert mgr.result.error is None


def test_keyboard_interrupt_tears_down_then_propagates(tmp_path: Path):
  backend = FakeBackend()
  mgr = _manager(tmp_path, backend=backend)
  with pytest.raises(KeyboardInterrupt), mgr.sandbox():
    _boom(KeyboardInterrupt())
  assert _down_ran(backend)
  assert mgr.result.status is RunStatus.RUN_ERROR


# ─── aggregation ─────────────────────────────────────────────────────────────


def test_contributions_aggregate_across_observers(tmp_path: Path):
  backend = FakeBackend()
  mgr = _manager(
      tmp_path,
      backend=backend,
      observers=[
          RecordingObserver(
              "a",
              contribution=Contribution(
                  artifacts={"patch": tmp_path / "ws" / "p.diff"}
              ),
          ),
          RecordingObserver(
              "b", contribution=Contribution(metrics={"secs": 2.0})
          ),
      ],
  )
  with mgr.sandbox():
    pass
  assert mgr.result.artifacts == {"patch": tmp_path / "ws" / "p.diff"}
  assert mgr.result.metrics == {"secs": 2.0}


def test_colliding_contributions_error_a_clean_run(tmp_path: Path):
  clash = Contribution(metrics={"secs": 1.0})
  mgr = _manager(
      tmp_path,
      observers=[
          RecordingObserver("a", contribution=clash),
          RecordingObserver("b", contribution=clash),
      ],
  )
  with mgr.sandbox():
    pass
  assert mgr.result.status is RunStatus.RUN_ERROR
  assert isinstance(mgr.result.error, SandboxError)


# ─── guards & plumbing ───────────────────────────────────────────────────────


def test_result_gated_until_finished_and_manager_single_run(tmp_path: Path):
  mgr = _manager(tmp_path)
  with pytest.raises(SandboxError, match="not available"):
    _ = mgr.result
  with mgr.sandbox():
    pass
  assert mgr.result.status is RunStatus.SUCCESS
  with pytest.raises(SandboxError, match="already ran"), mgr.sandbox():
    pass


def test_nonempty_workspace_refused_unless_reuse(tmp_path: Path):
  ws = tmp_path / "ws"
  ws.mkdir()
  _ = (ws / "stale.txt").write_text("old")
  mgr = _manager(tmp_path)
  with pytest.raises(SandboxError, match="not empty"), mgr.sandbox():
    pytest.fail("body must not run")
  assert mgr.result.status is RunStatus.SETUP_ERROR
  reusing = SandboxManager(
      spec=SPEC, backend=FakeBackend(), workspace=ws, reuse=True
  )
  with reusing.sandbox():
    pass
  assert reusing.result.status is RunStatus.SUCCESS


def test_run_plumbing_and_streaming(tmp_path: Path):
  backend = FakeBackend(run_results=[ExecResult(0, "line1\nline2\n", "")])
  mgr = _manager(tmp_path, backend=backend)
  log = tmp_path / "out.log"
  with mgr.sandbox() as sb:
    streamed = sb.run("entryscript.sh", timeout=5.0, stream_to=log)
  assert backend.scripts == ["entryscript.sh"]
  assert streamed.stdout == ""  # streamed, not captured
  assert log.read_text() == "line1\nline2\n"


def test_run_before_live_raises(tmp_path: Path):
  events: list[str] = []

  class Prober(RecordingObserver):

    @override
    def before_create(self, sb: Sandbox) -> None:
      super().before_create(sb)
      with pytest.raises(SandboxError, match="not live"):
        sb.run("x.sh", timeout=1.0)

  mgr = _manager(tmp_path, observers=[Prober("a", events)])
  with mgr.sandbox():
    pass
  assert "a.before_create" in events
