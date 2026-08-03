"""Collect what a harness run left behind, once the agent is done.

The agent's *side effects* — the files it wrote, and whether it finished
cleanly — are the harness's own knowledge, but they have to be read while the
sandbox is still live and handed to the engine as a ``Contribution``. That is
this observer's whole job, in ``before_destroy``:

- **completion** — ``Harness.completed(sb)``, kept on the observer for the
  composition to read back *and* exported as the ``agent_complete`` metric, so a
  persisted run records "the agent crashed" distinctly from "it finished but did
  not solve the task";
- **native outputs** — every file the harness declared, *best effort*: only the
  ones that actually landed are registered, so a run that died early yields
  fewer artifacts rather than a broken reference. Each is namespaced
  ``<harness>.<role>``, because the roles themselves are generic (``stderr``)
  and would otherwise collide between harnesses and lose their provenance.

Paired with ``ConversationObserver``, which owns the *converted* conversation.
Between them each artifact name is claimed exactly once (the engine refuses a
collision), and neither reaches for a host path — both read through the sandbox,
so a remote sandbox works unchanged (ADR-0003).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import override

from swe_lab.sandbox import (
    ArtifactSchema,
    Contribution,
    ExecResult,
    qualified_name,
    SandboxFs,
    SandboxObserver,
)

from .base import Harness

# Metric name for the agent's clean-finish signal (1.0 / 0.0 — ``Contribution``
# carries scalars, and completion has no file of its own to register).
# Deliberately *not* namespaced by harness, unlike the artifacts below: one run
# has one agent, so a consumer reading pass@K metrics should not have to know
# which harness produced the run to find its completion flag.
COMPLETE_METRIC = "agent_complete"


@dataclass
class HarnessOutcomeObserver(SandboxObserver):
  """Register a harness run's byproducts and its completion signal.

  Single-run, like every stateful observer: construct a fresh one per run.

  Attributes:
    harness: The harness whose run is being collected.
    complete: Whether the agent finished cleanly; ``False`` until
      ``before_destroy`` has run (and on a run whose sandbox never came up, so
      the hook never fired).
    collected: The native outputs that actually landed — namespaced artifact
      name → workspace-relative filename. Empty until ``before_destroy``.
    exec_result: The agent execution's own result, set by the composition
      before teardown; ``None`` if the body never got to run it.
    wall_seconds: How long the agent ran, set alongside it.
  """

  harness: Harness
  complete: bool = False
  collected: dict[str, str] = field(default_factory=dict)
  exec_result: ExecResult | None = None
  wall_seconds: float | None = None

  @override
  def output_schema(self) -> tuple[ArtifactSchema, ...]:
    """Declare the harness's native byproducts, every one best-effort.

    Best-effort mirrors how they are registered: a run that died early simply
    produces fewer files, so none of them can be a hard requirement of a
    completed run.
    """
    return tuple(
        ArtifactSchema(
            qualified_name(self.harness.name, role),
            required=False,
            description=f"the {self.harness.name} run's native {role}",
        )
        for role in self.harness.native_outputs()
    )

  @override
  def before_destroy(self, sb: SandboxFs) -> Contribution | None:
    """Ask the harness what it produced, and whether it finished.

    Args:
      sb: The still-live sandbox, read through rather than a host path.

    Returns:
      A contribution referencing every native output present (namespaced by the
      harness), the agent's own stderr when it said anything, and metrics for
      how the run ended.
    """
    self.complete = self.harness.completed(sb)
    self.collected = {
        qualified_name(self.harness.name, role): filename
        for role, filename in self.harness.native_outputs().items()
        if sb.exists(filename)  # only register what actually landed
    }
    return Contribution(
        artifacts=dict(self.collected),
        inline_artifacts=self._exec_output(),
        metrics=self._metrics(),
    )

  def _exec_output(self) -> dict[str, bytes]:
    """Keep what the agent *invocation* itself printed, if anything.

    Distinct from the agent's own trace and stderr file, which are workspace
    files it redirects into: this is what the exec returned. Usually empty —
    the script ends in ``|| true`` and redirects both streams — which is why
    it is skipped rather than persisted as an empty object, and worth keeping
    when it is *not*, since then something went wrong before the redirect.
    """
    if self.exec_result is None:
      return {}
    streams = {
        "exec_stdout.log": self.exec_result.stdout,
        "exec_stderr.log": self.exec_result.stderr,
    }
    return {
        qualified_name(self.harness.name, name): text.encode("utf-8")
        for name, text in streams.items()
        if text
    }

  def _metrics(self) -> dict[str, float]:
    """Return the completion flag, plus how the execution itself ended."""
    metrics = {COMPLETE_METRIC: float(self.complete)}
    if self.wall_seconds is not None:
      metrics[qualified_name(self.harness.name, "wall_seconds")] = (
          self.wall_seconds
      )
    if self.exec_result is not None:
      metrics[qualified_name(self.harness.name, "exit_code")] = float(
          self.exec_result.exit_code
      )
      metrics[qualified_name(self.harness.name, "timed_out")] = float(
          self.exec_result.timed_out
      )
    return metrics
