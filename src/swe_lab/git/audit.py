"""The git-integrity audit task: purge and prove it, with no agent involved.

The same observer the rollout contributes (ADR-0010 §3b), run on its own so a
whole dataset can be swept for purge failures *before* an expensive run —
discovering an integrity failure two hours into a 731-instance sweep is the
expensive way to learn it.

Deliberately minimal: no harness, no prompt, no patch. The task's action is a
no-op because the work happens in the observer's ``after_create`` hook, which
is exactly where it happens during a real rollout — so an audit pass means the
rollout's purge on that instance is the same code doing the same thing, not a
lookalike.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, override

from swe_lab.datasets.instance import TaskInstance
from swe_lab.sandbox import ExecResult, SandboxFs, SandboxObserver
from swe_lab.sandbox.observers import (
    GitHistoryLeakError,
    GitHistoryPurgeObserver,
)
from swe_lab.workflow import AttemptResult, Task


@dataclass
class GitIntegrityAuditTask(Task):
  """Purge (or merely measure) one instance's git history and report.

  Attributes:
    purge: Whether to purge before reporting. ``True`` audits what a rollout
      would actually get — the default, because that is the question worth
      sweeping. ``False`` characterizes the image as published, which is how
      the exposure was measured in the first place.
  """

  purge: bool = True

  @override
  def observers(self, instance: TaskInstance[Any]) -> Sequence[SandboxObserver]:
    """Return the one observer that does all the work.

    Args:
      instance: Supplies the fix commit whose absence is asserted.

    Returns:
      The purge observer, alone.
    """
    return (
        GitHistoryPurgeObserver(
            solution_sha=instance.solution_sha(), purge=self.purge
        ),
    )

  @override
  def action(
      self, sb: SandboxFs, instance: TaskInstance[Any], *, timeout: float
  ) -> ExecResult:
    """Do nothing — ``after_create`` has already purged and asserted.

    A task must have an action; this one has no work left to do, and saying so
    with a no-op is honest. Running anything here would only risk disturbing
    the state the audit just measured.

    Args:
      sb: Unused.
      instance: Unused.
      timeout: Unused.

    Returns:
      A synthetic success.
    """
    del sb, instance, timeout
    return ExecResult(0, "", "")

  @override
  def should_retry(self, result: AttemptResult) -> bool:
    """Never retry: a contaminated image is deterministic, not flaky.

    Args:
      result: The attempt to judge.

    Returns:
      ``False`` for an integrity failure; otherwise the default.
    """
    if isinstance(result.run.error, GitHistoryLeakError):
      return False
    return super().should_retry(result)
