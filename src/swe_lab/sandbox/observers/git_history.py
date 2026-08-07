"""Purge future git history before the agent runs, and prove it is gone.

Runs in ``after_create``: the sandbox is up and the repo is present, and the
agent has not started — so a failure costs no agent budget, and a *pass* means
the agent never saw the future at all.

Attaches to the **rollout only**. The evaluation sandbox is deliberately
untouched: it needs refs for its golden-test restore step, and the agent never
runs in it.

Contributed by the task rather than passed in by callers, so it cannot be
forgotten on one code path, and *not* inside a harness's invocation script — the
state of the repo is a property of the environment, not of whichever agent is
being run in it (ADR-0010 §3b).

The engine-generic mechanics — the script text and the report parsing — live in
:mod:`swe_lab.git_history`; this is the observer that drives them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
from typing import override

from swe_lab.git_history import (
    build_purge_script,
    build_report_script,
    GitHistoryReport,
)
from swe_lab.sandbox.errors import SandboxError
from swe_lab.sandbox.observer import ArtifactSchema, SandboxObserver
from swe_lab.sandbox.result import Contribution
from swe_lab.sandbox.sandbox import SandboxFs

# The report both the audit workflow and a normal rollout leave behind.
INTEGRITY_ARTIFACT = "git_integrity.json"
PURGE_SCRIPT_NAME = "git_purge.sh"
REPORT_SCRIPT_NAME = "git_report.sh"
# Metrics a sweep can aggregate without opening the artifact.
CLEAN_METRIC = "git_history_clean"
FUTURE_BEFORE_METRIC = "git_future_commits_before"
_PURGE_TIMEOUT_S = 600.0  # a large repo's `gc` is the long pole (~51s observed)
_REPORT_TIMEOUT_S = 300.0

_logger = logging.getLogger(__name__)


class GitHistoryLeakError(SandboxError):
  """The repo still exposes future history after the purge — a failed attempt.

  A ``SandboxError`` so it travels the engine's *recorded* path rather than
  escaping: ``Task.execute`` catches it, the run ends ``SETUP_ERROR`` with this
  error on the record, and the attempt is visibly failed. An integrity failure
  that crashed the process would take the record with it, and an unrecorded
  contamination is indistinguishable from a run that never happened.

  Its own type, though, because it is *not* an infrastructure fault and the two
  call for opposite responses: an infrastructure fault is worth retrying, and a
  leak is deterministic — the same image purges the same way every time, so a
  retry only buys the same verdict a container later.
  """


@dataclass
class GitHistoryPurgeObserver(SandboxObserver):
  """Strip future commits from the task repo, then assert they are gone.

  Single-run (it holds the run's reports): construct a fresh one per run.

  Attributes:
    solution_sha: The fix commit that must end up unreachable, when the caller
      knows it. Optional because deriving it belongs to the dataset, not here —
      for SWE-Bench Pro it is the trailing sha of the instance id. When absent
      the other two assertions still run, and ``future_commits`` is the
      load-bearing one anyway: it catches leaks whose sha we never knew.
    purge: Whether to actually purge. ``False`` reports only — the shape the
      audit workflow uses to measure an untouched image.
    before: The report taken before the purge; ``None`` until it has run.
    after: The report taken after; ``None`` until it has run.
  """

  solution_sha: str | None = None
  purge: bool = True
  before: GitHistoryReport | None = field(default=None, init=False)
  after: GitHistoryReport | None = field(default=None, init=False)

  @override
  def output_schema(self) -> tuple[ArtifactSchema, ...]:
    """Declare the integrity report — required, because it is the evidence."""
    return (
        ArtifactSchema(
            INTEGRITY_ARTIFACT,
            description=(
                "git-history state before/after the purge, and the assertions"
            ),
        ),
    )

  @override
  def after_create(self, sb: SandboxFs) -> None:
    """Report, purge, report again, then assert — before the agent starts.

    Args:
      sb: The live sandbox, whose repo is at ``sb.spec.workdir``.

    Raises:
      GitHistoryLeakError: If an assertion fails — the base commit is gone, the
        solution is still reachable, or a reachable commit postdates the base.
    """
    workdir = sb.spec.workdir
    self.before = self._report(sb, workdir)
    if not self.purge:
      self.after = self.before
    else:
      sb.write(
          PURGE_SCRIPT_NAME,
          build_purge_script(workdir=workdir).encode("utf-8"),
      )
      result = sb.run_script(PURGE_SCRIPT_NAME, timeout=_PURGE_TIMEOUT_S)
      if result.exit_code != 0:
        # The purge itself failing is already a leak: nothing was removed.
        raise GitHistoryLeakError(
            f"git-history purge failed (exit {result.exit_code}):"
            f" {(result.stderr or result.stdout).strip()[-500:]}"
        )
      self.after = self._report(sb, workdir)

    violations = self.after.violations()
    if violations:
      raise GitHistoryLeakError(
          "future git history is still reachable after the purge — refusing to"
          " run the agent against a contaminated repo: " + "; ".join(violations)
      )
    _logger.info(
        "git history purged: %d future commits before, %d after",
        self.before.future_commits,
        self.after.future_commits,
    )

  @override
  def before_destroy(self, sb: SandboxFs) -> Contribution | None:
    """Contribute the report as an inline artifact plus two scalar metrics.

    Inline rather than a workspace file: the observer already holds the parsed
    reports, so writing them into the sandbox only to fetch them back would be
    two transfers a remote sandbox pays for twice.

    Args:
      sb: The live sandbox (unused — everything is already in hand).

    Returns:
      The integrity report and its metrics, or ``None`` if ``after_create``
      never ran (a sandbox that failed to come up).
    """
    del sb
    if self.before is None or self.after is None:
      return None
    payload = {
        "purged": self.purge,
        "before": _as_dict(self.before),
        "after": _as_dict(self.after),
        "violations": list(self.after.violations()),
    }
    return Contribution(
        inline_artifacts={
            INTEGRITY_ARTIFACT: json.dumps(payload, indent=2).encode("utf-8")
        },
        metrics={
            CLEAN_METRIC: float(not self.after.violations()),
            FUTURE_BEFORE_METRIC: float(self.before.future_commits),
        },
    )

  def _report(self, sb: SandboxFs, workdir: str) -> GitHistoryReport:
    """Run the report script and parse its stdout.

    Args:
      sb: The live sandbox.
      workdir: The repo path to report on.

    Returns:
      The parsed report.

    Raises:
      GitHistoryLeakError: If the report cannot be produced or parsed — an
        unverifiable repo is treated exactly like a contaminated one.
    """
    sb.write(
        REPORT_SCRIPT_NAME,
        build_report_script(
            workdir=workdir, solution_sha=self.solution_sha
        ).encode("utf-8"),
    )
    result = sb.run_script(REPORT_SCRIPT_NAME, timeout=_REPORT_TIMEOUT_S)
    try:
      return GitHistoryReport.from_json(result.stdout)
    except (ValueError, KeyError) as exc:
      raise GitHistoryLeakError(
          "could not verify the repo's git history (the report script produced"
          f" no usable output, exit {result.exit_code}): {exc}"
      ) from exc


def _as_dict(report: GitHistoryReport) -> dict[str, object]:
  """Render a report for the JSON artifact (stable key order)."""
  return {
      "base_sha": report.base_sha,
      "refs": report.refs,
      "heads": report.heads,
      "tags": report.tags,
      "remote_refs": report.remote_refs,
      "remotes": report.remotes,
      "reflog": report.reflog,
      "non_ancestor_commits": report.non_ancestor_commits,
      "future_commits": report.future_commits,
      "base_reachable": report.base_reachable,
      "solution_reachable": report.solution_reachable,
  }
