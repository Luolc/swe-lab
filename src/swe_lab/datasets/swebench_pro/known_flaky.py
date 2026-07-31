"""Instances that flake, recorded rather than fixed — with why.

WHY THIS EXISTS
---------------
``fixes.py`` handles flakes in the *environment*: a broken dependency, a wrong
package version — things outside the task that can be repaired without touching
what counts as passing. This module is for the rest, and there are two kinds:

- **No fix exists.** The racy test is in ``fail_to_pass``, so it *is* the task.
  Patching it edits the benchmark; patching the source under test does the
  agent's job for it. ``graded=True`` marks these.
- **A fix exists but costs more than the flake.** Recorded now, deferred
  deliberately, with the shape of the fix written into ``reason`` so the
  decision can be revisited rather than rediscovered.

Either way the honest response is the same: record the measured failure rate and
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
  """One instance's measured instability, and why it is not being fixed.

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
      help. ``False`` means a fix is possible and ``reason`` says what it would
      be and why it was deferred — see the module docstring.
    reason: The mechanism, and for a deferred entry, the shape of the fix.
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


# tutanota: the whole suite decides the grade, so any flake anywhere lands here.
# Verified by executing each instance's own parser on a synthetic pass line and
# a synthetic fail line: on "All N assertions passed" every required name is
# emitted, on "1 out of N assertions failed" none is. 17 of 20 tutanota
# instances behave this way; the other 3 use a parser that reads a different
# output format, so the same probe says nothing about them either way.
_TUTANOTA_ALL_OR_NOTHING = (
    "instance_tutao__tutanota-09c2776c0fce3db5c6e18da92b5"
    "a45dce9f013aa-vbc0d9ba8f0071fbe982809910959a6ff8884dbbf",
    "instance_tutao__tutanota-12a6cbaa4f8b43c2f85caca0787"
    "ab55501539955-vc4e41fd0029957297843cb9dec4a25c7c756f029",
    "instance_tutao__tutanota-1e516e989b3c0221f4af6b297d9"
    "c0e4c43e4adc3-vbc0d9ba8f0071fbe982809910959a6ff8884dbbf",
    "instance_tutao__tutanota-1ff82aa365763cee2d609c9d193"
    "60ad87fdf2ec7-vc4e41fd0029957297843cb9dec4a25c7c756f029",
    "instance_tutao__tutanota-219bc8f05d7b980e038bc1524cb"
    "021bf56397a1b-vee878bb72091875e912c52fc32bc60ec3760227b",
    "instance_tutao__tutanota-40e94dee2bcec2b63f362da2831"
    "23e9df1874cc1-vc4e41fd0029957297843cb9dec4a25c7c756f029",
    "instance_tutao__tutanota-4b4e45949096bb288f2b522f657"
    "610e480efa3e8-vee878bb72091875e912c52fc32bc60ec3760227b",
    "instance_tutao__tutanota-8513a9e8114a8b42e64f4348335"
    "e0f23efa054c4-vee878bb72091875e912c52fc32bc60ec3760227b",
    "instance_tutao__tutanota-b4934a0f3c34d9d7649e944b183"
    "137e8fad3e859-vbc0d9ba8f0071fbe982809910959a6ff8884dbbf",
    "instance_tutao__tutanota-befce4b146002b9abc86aa95f4d"
    "57581771815ce-vee878bb72091875e912c52fc32bc60ec3760227b",
    "instance_tutao__tutanota-d1aa0ecec288bfc800cfb9133b0"
    "87c4f81ad8b38-vbc0d9ba8f0071fbe982809910959a6ff8884dbbf",
    "instance_tutao__tutanota-da4edb7375c10f47f4ed3860a59"
    "1c5e6557f7b5c-vbc0d9ba8f0071fbe982809910959a6ff8884dbbf",
    "instance_tutao__tutanota-db90ac26ab78addf72a8efaff3c"
    "7acc0fbd6d000-vbc0d9ba8f0071fbe982809910959a6ff8884dbbf",
    "instance_tutao__tutanota-f3ffe17af6e8ab007e8d4613550"
    "57ad237846d9d-vbc0d9ba8f0071fbe982809910959a6ff8884dbbf",
    "instance_tutao__tutanota-fb32e5f9d9fc152a00144d56dd0"
    "af01760a2d4dc-vc4e41fd0029957297843cb9dec4a25c7c756f029",
    "instance_tutao__tutanota-fbdb72a2bd39b05131ff905780d"
    "9d4a2a074de26-vbc0d9ba8f0071fbe982809910959a6ff8884dbbf",
    "instance_tutao__tutanota-fe240cbf7f0fdd6744ef7bef8cb"
    "61676bcdbb621-vc4e41fd0029957297843cb9dec4a25c7c756f029",
)

_TUTANOTA_SUITE_FLAKE = KnownFlaky(
    failure_rate=0.25,
    sample_size=4,
    measured_on=(
        "2026-07-31, parallel batch runner — 1 failure in 4 runs on one"
        " instance, so the rate is weak; the grading mechanism below is"
        " verified for every instance listed here"
    ),
    flaky_tests=(
        "any of ~6651 assertions in the full suite (observed once:"
        " test/tests/desktop/db/OfflineDbTest.ts | Integrity of the database"
        " is checked on initialization)",
    ),
    graded=False,
    reason=(
        "The harness runs the *entire* suite and the parser keys on one line,"
        " `All N assertions passed`. When it matches, the parser emits a"
        " hardcoded list of file names all marked passed; when it does not, it"
        " emits nothing that matches. So the grade is a single boolean — did"
        " every assertion in the repo pass — and one flaky assertion anywhere,"
        " in a test that is in neither fail_to_pass nor pass_to_pass, takes the"
        " instance from resolved to 0/107. That is a false negative on a"
        " correct patch, and output.json keeps no record of which assertion"
        " failed. The one failure we captured was a test whose"
        " `buf[len-1] ^= buf[len-1]` zeroes the last byte instead of flipping"
        " it, so its corruption is a no-op whenever that byte is already zero —"
        " but SQLCipher puts a per-page HMAC-SHA512 there, so that predicts"
        " ~1/256, not 1/4. The byte-flip is one sample of the population, not"
        " the cause. Unlike the NodeBB entry a fix does exist — a parser that"
        " reads real per-file results — but it is a per-repo rewrite (59"
        " distinct parsers across 11 repos in the pinned harness), which is"
        " more than this flake is worth today."
    ),
    evidence=(
        "https://github.com/Luolc/swe-lab/issues/123#issuecomment-5146652774",
        "https://github.com/scaleapi/SWE-bench_Pro-os/tree/ca10a60a5fcae51e6948ffe1485d4153d421e6c5/run_scripts",
    ),
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
    **dict.fromkeys(_TUTANOTA_ALL_OR_NOTHING, _TUTANOTA_SUITE_FLAKE),
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
