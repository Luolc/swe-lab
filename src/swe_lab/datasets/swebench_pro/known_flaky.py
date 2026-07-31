"""Instances that flake for reasons no harness fix can remove.

WHY THIS EXISTS
---------------
``fixes.py`` handles flakes in the *environment*: a broken dependency, a wrong
package version — things outside the task that can be repaired without touching
what counts as passing. This module is for the ones that cannot.

When the racy test is in ``fail_to_pass``, it **is** the task. Patching it edits
the benchmark, and patching the source under test does the agent's job for it.
The only honest response left is to say so: record the measured failure rate and
stamp it onto the run, so a result carries its own caveat instead of a reader
inferring model variance from a number that was never stable.

An entry is a **measurement**, not a guess. It needs a sample size and the
conditions it was measured under, because a rate from one machine shape does not
transfer to another — these races are load-sensitive by nature.

WHAT TO DO WITH ONE
-------------------
Nothing automatic. The registry annotates; it never changes a verdict, skips a
test, or retries a run. A consumer deciding to re-run a flaky instance N times
and take the modal result is making a scoring decision, and that belongs where
scoring decisions are visible — not hidden behind a lookup here.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True, kw_only=True)
class KnownFlaky:
  """One instance's measured instability, and why it cannot be fixed.

  Attributes:
    failure_rate: Fraction of runs that fail for this reason (``0.25`` = a
      quarter). Measured, never estimated.
    sample_size: How many runs the rate came from — a rate without one is an
      anecdote.
    measured_on: When and *where* it was measured (date + machine shape). These
      races are load-sensitive, so the environment is part of the datum.
    flaky_tests: The test names that actually flake, so a failure can be matched
      against this entry rather than assumed to be it.
    graded: Whether the flaky tests are in ``fail_to_pass``. When ``True`` the
      instance is graded on them, which is exactly why no environment fix can
      help — see the module docstring.
    reason: One line on the mechanism.
    evidence: Upstream issues, PRs, or commits backing the diagnosis.
  """

  failure_rate: float
  sample_size: int
  measured_on: str
  flaky_tests: tuple[str, ...]
  graded: bool
  reason: str
  evidence: tuple[str, ...] = field(default_factory=tuple)


_NODEBB_ORPHANS = (
    "instance_NodeBB__NodeBB-22368b996ee0e5f11a5189b400b33af3cc8d925a"
    "-v4fbcfae8b15e4ce5d132c408bca69ebb9cf146ed"
)

# instance_id -> what is known about its instability.
_KNOWN_FLAKY: dict[str, KnownFlaky] = {
    _NODEBB_ORPHANS: KnownFlaky(
        failure_rate=0.25,
        sample_size=32,
        measured_on="2026-07-31, parallel batch runner (8/32 failures)",
        flaky_tests=(
            "test/uploads.js | Upload Controllers library methods"
            " .cleanOrphans() should delete orphans older than the configured"
            " number of days",
        ),
        graded=True,
        reason=(
            "The gold patch deletes orphaned uploads without awaiting"
            " (`orphans.forEach((relPath) => { file.delete(...) })`, under its"
            " own comment `Note: no await. Deletion not guaranteed by method"
            " end.`), and the test re-reads the directory immediately and"
            " asserts zero orphans. Two unawaited unlinks race one readdir."
            " Upstream shipped the bug in this very commit and fixed it 11"
            " months later by awaiting the deletes; the test was never"
            " changed. So an agent that awaits — the better solution — passes"
            " deterministically, while one matching the reference flakes."
        ),
        evidence=(
            "https://github.com/NodeBB/NodeBB/commit/22368b996ee0e5f11a5189b400b33af3cc8d925a",
            "https://github.com/NodeBB/NodeBB/commit/306651902896904ae1600febb02137e2ca127a06",
        ),
    ),
}


def known_flaky(instance_id: str) -> KnownFlaky | None:
  """Return what is known about this instance's instability, if anything.

  Args:
    instance_id: The instance to look up.

  Returns:
    Its entry, or ``None`` when the instance has no measured flakiness (the
    overwhelmingly common case).
  """
  return _KNOWN_FLAKY.get(instance_id)


def flaky_instances() -> tuple[str, ...]:
  """Return every instance id carrying a known-flaky entry."""
  return tuple(_KNOWN_FLAKY)
