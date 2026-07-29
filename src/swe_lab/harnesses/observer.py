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
  fewer artifacts rather than a broken reference.

Paired with ``ConversationObserver``, which owns the *converted* conversation.
Between them each artifact name is claimed exactly once (the engine refuses a
collision), and neither reaches for a host path — both read through the sandbox,
so a remote sandbox works unchanged (ADR-0003).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import override

from swe_lab.sandbox import Contribution, SandboxFs, SandboxObserver

from .base import Harness

# Metric name for the agent's clean-finish signal (1.0 / 0.0 — ``Contribution``
# carries scalars, and completion has no file of its own to register).
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
    collected: The native outputs that actually landed — artifact name →
      workspace-relative filename. Empty until ``before_destroy``.
  """

  harness: Harness
  complete: bool = False
  collected: dict[str, str] = field(default_factory=dict)

  @override
  def before_destroy(self, sb: SandboxFs) -> Contribution | None:
    """Ask the harness what it produced, and whether it finished.

    Args:
      sb: The still-live sandbox, read through rather than a host path.

    Returns:
      A contribution referencing every native output present, plus the
      completion metric.
    """
    self.complete = self.harness.completed(sb)
    self.collected = {
        name: filename
        for name, filename in self.harness.native_outputs().items()
        if sb.exists(filename)  # only register what actually landed
    }
    return Contribution(
        artifacts=dict(self.collected),
        metrics={COMPLETE_METRIC: float(self.complete)},
    )
