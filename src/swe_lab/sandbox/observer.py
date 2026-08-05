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

from .errors import SandboxError
from .mounts import merge_mounts, Mounts
from .result import Contribution, merge_contributions

if TYPE_CHECKING:
  from .sandbox import SandboxFs


@dataclass(frozen=True, slots=True)
class ArtifactSchema:
  """What one artifact *is*: its store name, whether it must exist, and why.

  Direction-neutral on purpose: an observer declares the *outputs* it produces
  (:meth:`SandboxObserver.output_schema`) and a task declares the *inputs* it
  consumes with the same record — the method names carry the direction, this is
  just data. Pure data also means no parse concept: an output is the artifact
  as persisted, and how an observer computed it is the observer's business.

  Attributes:
    name: The artifact name as it appears in the store (format-suffixed:
      ``patch.diff``, ``conversation.json``).
    required: Whether the artifact must be there. Enforced in both directions,
      which fail differently. A missing required **input** is the distinct edge
      failure of ADR-0007 §5 (``EDGE_FAILED``), and is refused earlier still —
      at assembly — when nothing in the workflow produces the name at all. A
      missing required **output** fails the attempt through
      ``Task.outputs_valid``, so it is retried (§6). ``False`` means advisory:
      an artifact that never lands is omitted from the run's artifacts, and the
      record still lands without it.
    description: One line saying what this artifact is, for a reader of a
      merged schema.
  """

  name: str
  required: bool = True
  description: str = ""


def merge_output_schemas(
    *schemas: Sequence[ArtifactSchema],
) -> tuple[ArtifactSchema, ...]:
  """Merge per-observer output schemas; a duplicate store name is an error.

  The same rule :func:`~swe_lab.sandbox.mounts.merge_mounts` applies to mount
  targets, for the same reason: two observers producing one store name is a
  composition bug, and it should fail at assembly, not at persist.

  Args:
    *schemas: Output schemas, one per observer, in composition order.

  Returns:
    The union, in declaration order.

  Raises:
    SandboxError: If two schemas claim the same store name.
  """
  merged: dict[str, ArtifactSchema] = {}
  for schema in schemas:
    for artifact in schema:
      if artifact.name in merged:
        raise SandboxError(
            f"duplicate output name {artifact.name!r}: two observers declare it"
        )
      merged[artifact.name] = artifact
  return tuple(merged.values())


class SandboxObserver:
  """No-op base for lifecycle observers; override what you need.

  Hooks receive the narrow :class:`~swe_lab.sandbox.sandbox.SandboxFs` view —
  files + exec, never the lifecycle. Hook order in a run: ``mounts`` (collected
  before anything runs) → ``before_create`` → *(sandbox up + mount)* →
  ``after_create`` (setup) → *(body)* → ``before_destroy`` (always, even on
  failure) → *(collect + sandbox down)* → ``after_destroy``. ``on_error`` fires
  between the failure and ``before_destroy`` while the sandbox is still live.
  """

  def mounts(self) -> Mounts:
    """Return the files this observer needs staged into the workspace."""
    return {}

  def output_schema(self) -> Sequence[ArtifactSchema]:
    """Declare the outputs this observer produces. Default: none.

    An entry names an artifact this observer's contributions register, as it
    will appear in the store. A task's own output schema is derived by merging
    its composed observers' declarations (``merge_output_schemas``), which is
    where two observers claiming one name fails — so declare what you produce,
    and only what you produce.
    """
    return ()

  def before_create(self, sb: SandboxFs) -> None:
    """Run before the sandbox exists (``sb`` is not yet live)."""
    del sb

  def after_create(self, sb: SandboxFs) -> None:
    """Run setup against the live sandbox; a raise aborts the run."""
    del sb

  def before_destroy(self, sb: SandboxFs) -> Contribution | None:
    """Post-process the run; always called once the sandbox was live."""
    del sb

  def after_destroy(self, sb: SandboxFs) -> None:
    """Run after the sandbox is gone (``sb`` is no longer live)."""
    del sb

  def on_error(
      self, sb: SandboxFs, error: BaseException
  ) -> Contribution | None:
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
  def output_schema(self) -> Sequence[ArtifactSchema]:
    """Merge the children's output schemas (duplicate names refused)."""
    return merge_output_schemas(
        *(child.output_schema() for child in self.observers)
    )

  @override
  def before_create(self, sb: SandboxFs) -> None:
    """Fan out ``before_create``."""
    for child in self.observers:
      child.before_create(sb)

  @override
  def after_create(self, sb: SandboxFs) -> None:
    """Fan out ``after_create``."""
    for child in self.observers:
      child.after_create(sb)

  @override
  def before_destroy(self, sb: SandboxFs) -> Contribution | None:
    """Fan out ``before_destroy`` and merge the children's contributions."""
    return self._merged(
        [c for child in self.observers if (c := child.before_destroy(sb))]
    )

  @override
  def after_destroy(self, sb: SandboxFs) -> None:
    """Fan out ``after_destroy``."""
    for child in self.observers:
      child.after_destroy(sb)

  @override
  def on_error(
      self, sb: SandboxFs, error: BaseException
  ) -> Contribution | None:
    """Fan out ``on_error`` and merge the children's contributions."""
    return self._merged(
        [c for child in self.observers if (c := child.on_error(sb, error))]
    )

  def _merged(self, contributions: list[Contribution]) -> Contribution | None:
    if not contributions:
      return None
    return merge_contributions(contributions)
