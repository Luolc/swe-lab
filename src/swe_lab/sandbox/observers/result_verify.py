"""Run the integrity rules over what the agent produced, and report.

Last in ``before_destroy``, because it reads what the observers ahead of it just
produced: the extracted patch, the converted conversation, and the git-history
report. All three already hold their results as attributes by then.

``after_create`` is not an alternative — the agent has not run, so there is no
patch and no trace. That hook belongs to the purge, which has to precede the
agent; this is its mirror image.

The rules themselves are pure and live in :mod:`swe_lab.integrity.rules`; this
is the adapter that runs them in-flight. The same rules replay over stored runs
through :mod:`swe_lab.integrity.replay`.

**This observer must never raise.** An exception in ``before_destroy`` is caught
by the manager but sets the run's error, turning a *successful* rollout into
``RUN_ERROR`` — so a bug in a detector would destroy the very run it was meant
to describe. Exactly inverted from ``GitHistoryPurgeObserver``, which is a gate
and must raise: a contaminated result is worse than none, while a broken
diagnostic is not worth a real run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
from typing import Any, override

from swe_lab.integrity.rules import (
    check_controls,
    check_patch,
    check_trace,
    merge,
    VerifierFindings,
)
from swe_lab.sandbox.observer import ArtifactSchema, SandboxObserver
from swe_lab.sandbox.result import Contribution
from swe_lab.sandbox.sandbox import SandboxFs

# What the run leaves behind for a human, or for a later model judge.
VERIFIER_ARTIFACT = "verifier.json"

_logger = logging.getLogger(__name__)


@dataclass
class ResultVerifyObserver(SandboxObserver):
  """Apply the integrity rules to one run's patch, trace and control report.

  Single-run (it holds the findings): construct a fresh one per run.

  Attributes:
    patch_source: The observer holding the extracted patch (its ``patch``
      attribute is read after it has run). ``None`` disables the patch rules.
    conversation_source: The observer holding the converted conversation.
      ``None`` disables the trace rules.
    integrity_source: The observer holding the git-history reports. ``None``
      means no purge ran, which the control rule reports as a finding in its
      own right.
    required_tests: The instance's required tests, for the hardcoding rule.
    workdir: The repo path, for the reads-outside rule.
    findings: What the rules saw; ``None`` until ``before_destroy``.
  """

  patch_source: Any = None
  conversation_source: Any = None
  integrity_source: Any = None
  required_tests: tuple[str, ...] = ()
  workdir: str = "/"
  findings: VerifierFindings | None = field(default=None, init=False)

  @override
  def output_schema(self) -> tuple[ArtifactSchema, ...]:
    """Declare the findings record — advisory, like the run it describes."""
    return (
        ArtifactSchema(
            VERIFIER_ARTIFACT,
            required=False,
            description="integrity rule findings for this run (not a verdict)",
        ),
    )

  @override
  def before_destroy(self, sb: SandboxFs) -> Contribution | None:
    """Run every rule, catching everything, and contribute the findings.

    Args:
      sb: Unused — every input is already in hand from the observers ahead.

    Returns:
      The findings as an inline artifact plus scalar metrics. Never ``None``:
      a run with nothing to report still records that the rules ran.
    """
    del sb
    try:
      self.findings = self._run_rules()
    except Exception as exc:  # noqa: BLE001 — a detector must not fail a run
      _logger.exception("result verifier failed; recording it as a finding")
      self.findings = VerifierFindings(error=repr(exc))
    return Contribution(
        inline_artifacts={
            VERIFIER_ARTIFACT: json.dumps(
                self.findings.to_dict(), indent=2
            ).encode("utf-8")
        },
        metrics=self.findings.metrics(),
    )

  def _run_rules(self) -> VerifierFindings:
    """Gather the three inputs and apply their rules.

    Returns:
      The merged findings.
    """
    patch = getattr(self.patch_source, "patch", "") or ""
    conversation = getattr(self.conversation_source, "conversation", None)
    messages: list[dict[str, Any]] = []
    if conversation is not None:
      messages = conversation.model_dump(mode="json").get("messages", [])
    integrity = None
    after = getattr(self.integrity_source, "after", None)
    before = getattr(self.integrity_source, "before", None)
    if after is not None and before is not None:
      integrity = {
          "purged": getattr(self.integrity_source, "purge", True),
          "before": before.to_dict(),
          "after": after.to_dict(),
          "violations": list(after.violations()),
      }
    return merge(
        check_patch(patch, self.required_tests),
        check_trace(messages, workdir=self.workdir),
        check_controls(integrity),
    )
