"""Tests for the observer base and CompositeObserver fan-out."""

from pathlib import Path

import pytest

from swe_lab.sandbox import (
    CompositeObserver,
    Contribution,
    Mount,
    SandboxError,
    SandboxObserver,
    SandboxSpec,
)
from swe_lab.sandbox.manager import Sandbox
from swe_lab.sandbox.testing import FakeBackend, RecordingObserver


def _sb(tmp_path: Path) -> Sandbox:
  spec = SandboxSpec("inst", "img:tag", "/app", "abc123")
  return Sandbox("inst", spec, tmp_path, FakeBackend(), "fake-inst")


def test_base_observer_hooks_are_noops(tmp_path: Path):
  obs = SandboxObserver()
  sb = _sb(tmp_path)
  assert obs.mounts() == {}
  assert obs.before_destroy(sb) is None
  assert obs.on_error(sb, RuntimeError()) is None
  obs.before_create(sb)
  obs.after_create(sb)
  obs.after_destroy(sb)


def test_composite_fans_out_in_registration_order(tmp_path: Path):
  events: list[str] = []
  composite = CompositeObserver(
      [RecordingObserver("a", events), RecordingObserver("b", events)]
  )
  sb = _sb(tmp_path)
  composite.before_create(sb)
  composite.after_create(sb)
  assert composite.before_destroy(sb) is None  # no scripted contributions
  assert events == [
      "a.before_create",
      "b.before_create",
      "a.after_create",
      "b.after_create",
      "a.before_destroy",
      "b.before_destroy",
  ]


def test_composite_merges_mounts_and_contributions(tmp_path: Path):
  events: list[str] = []
  a = RecordingObserver(
      "a",
      events,
      contribution=Contribution(artifacts={"patch": tmp_path / "p"}),
      extra_mounts={"a.sh": Mount(content=b"a")},
  )
  b = RecordingObserver(
      "b",
      events,
      contribution=Contribution(metrics={"secs": 1.0}),
      extra_mounts={"b.sh": Mount(content=b"b")},
  )
  composite = CompositeObserver([a, b])
  assert set(composite.mounts()) == {"a.sh", "b.sh"}
  merged = composite.before_destroy(_sb(tmp_path))
  assert merged is not None
  assert merged.artifacts == {"patch": tmp_path / "p"}
  assert merged.metrics == {"secs": 1.0}


def test_composite_refuses_colliding_contributions(tmp_path: Path):
  clash = Contribution(metrics={"secs": 1.0})
  composite = CompositeObserver(
      [
          RecordingObserver("a", contribution=clash),
          RecordingObserver("b", contribution=clash),
      ]
  )
  with pytest.raises(SandboxError, match="metric 'secs'"):
    composite.before_destroy(_sb(tmp_path))


def test_composite_is_transparent_to_child_failures(tmp_path: Path):
  composite = CompositeObserver(
      [RecordingObserver("a", raise_in="after_create")]
  )
  with pytest.raises(RuntimeError, match="scripted failure"):
    composite.after_create(_sb(tmp_path))
