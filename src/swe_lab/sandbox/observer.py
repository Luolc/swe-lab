"""The observer seam: the five lifecycle hooks plugs attach to.

Observers are how axes (dataset setup, trace capture, diff-extract, persist,
metrics) attach behavior around the one main action. Hooks *return*
contributions for the manager to aggregate — they never mutate shared state.
An observer that keeps a typed result of its own (e.g. an eval verdict field,
set in ``before_destroy``) is a **single-run object**: construct a fresh one
per composition; reuse across runs is a bug.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import override, TYPE_CHECKING

from .mounts import merge_mounts, Mounts
from .result import Contribution, merge_contributions

if TYPE_CHECKING:
  from .manager import Sandbox


class SandboxObserver:
  """No-op base for lifecycle observers; override what you need.

  Hook order in a run: ``mounts`` (collected before anything runs) →
  ``before_create`` → *(backend up)* → ``after_create`` (setup) → *(body)* →
  ``before_destroy`` (always, even on failure) → *(backend down)* →
  ``after_destroy``. ``on_error`` fires between the failure and
  ``before_destroy`` while the sandbox is still live.
  """

  def mounts(self) -> Mounts:
    """Return the files this observer needs staged into the workspace."""
    return {}

  def before_create(self, sb: Sandbox) -> None:
    """Run before the sandbox exists (``sb`` is not yet live)."""
    del sb

  def after_create(self, sb: Sandbox) -> None:
    """Run setup against the live sandbox; a raise aborts the run."""
    del sb

  def before_destroy(self, sb: Sandbox) -> Contribution | None:
    """Post-process the run; always called once the sandbox was live."""
    del sb

  def after_destroy(self, sb: Sandbox) -> None:
    """Run after the sandbox is gone (``sb`` is no longer live)."""
    del sb

  def on_error(self, sb: Sandbox, error: BaseException) -> Contribution | None:
    """React to a failed setup/body while the sandbox is still live."""
    del sb, error


@dataclass(frozen=True, slots=True)
class CompositeObserver(SandboxObserver):
  """Fan every hook out to child observers, in registration order.

  Transparent: a child's exception propagates — the manager's per-hook
  catching policy applies at this composite's boundary, exactly as it would
  to a flat list.

  Attributes:
    observers: The children, invoked in order.
  """

  observers: Sequence[SandboxObserver]

  @override
  def mounts(self) -> Mounts:
    """Merge the children's mounts (duplicate targets refused)."""
    return merge_mounts(*(child.mounts() for child in self.observers))

  @override
  def before_create(self, sb: Sandbox) -> None:
    """Fan out ``before_create``."""
    for child in self.observers:
      child.before_create(sb)

  @override
  def after_create(self, sb: Sandbox) -> None:
    """Fan out ``after_create``."""
    for child in self.observers:
      child.after_create(sb)

  @override
  def before_destroy(self, sb: Sandbox) -> Contribution | None:
    """Fan out ``before_destroy`` and merge the children's contributions."""
    return self._merged(
        [c for child in self.observers if (c := child.before_destroy(sb))]
    )

  @override
  def after_destroy(self, sb: Sandbox) -> None:
    """Fan out ``after_destroy``."""
    for child in self.observers:
      child.after_destroy(sb)

  @override
  def on_error(self, sb: Sandbox, error: BaseException) -> Contribution | None:
    """Fan out ``on_error`` and merge the children's contributions."""
    return self._merged(
        [c for child in self.observers if (c := child.on_error(sb, error))]
    )

  def _merged(self, contributions: list[Contribution]) -> Contribution | None:
    if not contributions:
      return None
    artifacts, metrics = merge_contributions(contributions)
    return Contribution(artifacts=artifacts, metrics=metrics)
