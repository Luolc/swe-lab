"""Public test doubles for the sandbox engine.

Later tasks (the eval method, the harness) reuse these for their own
Docker-free tests, so they are shipped code, not test-local helpers.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import override

from .backend import ExecResult, SandboxBackend
from .manager import Sandbox
from .mounts import Assets, Mounts
from .observer import SandboxObserver
from .result import Contribution
from .spec import SandboxSpec


def _maybe_raise(error: Exception | None) -> None:
  """Raise the scripted error when one is set."""
  if error is not None:
    raise error


@dataclass
class FakeBackend(SandboxBackend):
  """In-memory ``SandboxBackend``: scripted results, recorded calls.

  Attributes:
    run_results: Results returned by successive ``run_script`` calls (repeating
      the last one when exhausted); defaults to a single success.
    up_error: Raised by ``up`` when set.
    run_error: Raised by ``run_script`` when set.
    down_error: Raised by ``down`` when set (to test that the manager
      swallows a misbehaving backend).
    calls: Every call as ``(method, detail)``, in order.
    scripts: The script names passed to ``run_script``, in order.
    assets: Recorded only (no container to bind-mount into) — lets a
      composition set them via ``dataclasses.replace``. Mounts still
      materialize into the workspace via the inherited default.
  """

  run_results: list[ExecResult] = field(default_factory=list)
  up_error: Exception | None = None
  run_error: Exception | None = None
  down_error: Exception | None = None
  calls: list[tuple[str, str]] = field(default_factory=list)
  scripts: list[str] = field(default_factory=list)
  assets: Assets = field(default_factory=dict)

  @override
  def with_assets(self, assets: Assets) -> SandboxBackend:
    """Return a copy carrying the merged assets (recorded, not realized)."""
    return replace(self, assets={**self.assets, **assets})

  @override
  def up(self, spec: SandboxSpec, workspace: Path) -> str:
    """Return a fake handle (or raise the scripted ``up_error``)."""
    del workspace
    self.calls.append(("up", spec.instance_id))
    _maybe_raise(self.up_error)
    return f"fake-{spec.instance_id}"

  @override
  def run_script(
      self,
      handle: str,
      script_name: str,
      *,
      timeout: float,
      env: Mapping[str, str] | None = None,
      stream_to: Path | None = None,
  ) -> ExecResult:
    """Return the next scripted result, honoring ``stream_to``.

    Args:
      handle: The handle ``up`` returned.
      script_name: The workspace script name under test (recorded, not run).
      timeout: Recorded only.
      env: Recorded only.
      stream_to: When set, the scripted stdout is written there and the
        returned result carries an empty ``stdout`` — mirroring a real
        backend's streaming contract.

    Returns:
      The next scripted ``ExecResult``.
    """
    del timeout, env  # recorded via calls only; irrelevant to the result
    self.calls.append(("run_script", handle))
    self.scripts.append(script_name)
    _maybe_raise(self.run_error)
    index = min(len(self.scripts) - 1, len(self.run_results) - 1)
    result = (
        self.run_results[index] if self.run_results else ExecResult(0, "", "")
    )
    if stream_to is not None:
      _ = stream_to.write_text(result.stdout)
      return ExecResult(result.exit_code, "", result.stderr, result.timed_out)
    return result

  @override
  def down(self, handle: str) -> None:
    """Record the teardown (or raise the scripted ``down_error``)."""
    self.calls.append(("down", handle))
    _maybe_raise(self.down_error)


@dataclass
class RecordingObserver(SandboxObserver):
  """Observer that records its hook invocations and can raise on demand.

  Attributes:
    name: Label prefixed to every recorded event.
    events: Shared event log — pass the same list to several observers to
      assert cross-observer ordering.
    raise_in: Hook name that raises ``RuntimeError`` when reached.
    contribution: Returned from ``before_destroy`` when set.
    error_contribution: Returned from ``on_error`` when set.
    extra_mounts: Returned from ``mounts``.
  """

  name: str = "obs"
  events: list[str] = field(default_factory=list)
  raise_in: str = ""
  contribution: Contribution | None = None
  error_contribution: Contribution | None = None
  extra_mounts: Mounts = field(default_factory=dict)

  def _hit(self, hook: str) -> None:
    self.events.append(f"{self.name}.{hook}")
    if self.raise_in == hook:
      raise RuntimeError(f"{self.name} scripted failure in {hook}")

  @override
  def mounts(self) -> Mounts:
    """Record and return the scripted mounts."""
    self._hit("mounts")
    return self.extra_mounts

  @override
  def before_create(self, sb: Sandbox) -> None:
    """Record the hook."""
    self._hit("before_create")

  @override
  def after_create(self, sb: Sandbox) -> None:
    """Record the hook."""
    self._hit("after_create")

  @override
  def before_destroy(self, sb: Sandbox) -> Contribution | None:
    """Record the hook and return the scripted contribution."""
    self._hit("before_destroy")
    return self.contribution

  @override
  def after_destroy(self, sb: Sandbox) -> None:
    """Record the hook."""
    self._hit("after_destroy")

  @override
  def on_error(self, sb: Sandbox, error: BaseException) -> Contribution | None:
    """Record the hook and return the scripted error contribution."""
    self._hit("on_error")
    return self.error_contribution
