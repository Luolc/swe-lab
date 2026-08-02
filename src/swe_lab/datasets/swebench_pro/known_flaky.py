"""Instances that flake, recorded rather than fixed — with why.

WHY THIS EXISTS
---------------
The ``fixes`` package handles the flakes it can repair *without moving the
pass/fail boundary*: a dependency version with a known bug, parallelism the
tests cannot survive, a wall clock the suite turns out not to be indifferent to.
This module is for the rest, and there are two kinds:

- **No fix exists.** The racy test is in ``fail_to_pass``, so it *is* the task.
  Patching it edits the benchmark; patching the source under test does the
  agent's job for it. ``graded=True`` marks these.
- **A fix exists but costs more than the flake.** Recorded now, deferred
  deliberately, with the shape of the fix written into ``reason`` so the
  decision can be revisited rather than rediscovered.

The two registries are not exclusive, and one instance is the proof: tutanota
``f373ac38`` carries a fix *and* an entry here. Its clock window is closed in
``fixes/tutanota_clock``; the suite-wide race that its count-based grading turns
into an all-or-nothing verdict is not, and that is what the entry records.

Either way the honest response is the same: record the measured failure rate and
stamp it onto the run, so a result carries its own caveat instead of a reader
inferring model variance from a number that was never stable.

An entry is a **measurement**, not a guess. It needs a sample size and the
conditions it was measured under, because a rate from one machine shape does not
transfer to another — these races are load-sensitive by nature.

And a clean re-run clears less than it looks: at a 1-in-64 rate, 64 rollouts
come back green 37% of the time. Read an absence of failures as *"no failures
at n=64, so the rate is under roughly 5%"*, never as a fix — one entry here was
briefly recorded as fixed on exactly that mistake.

WHAT TO DO WITH ONE
-------------------
Nothing automatic. The registry annotates; it never changes a verdict, skips a
test, or decides whether a run is retried.

Evaluation *does* retry a failed attempt (ADR-0005), but blanket and
self-discovering — deliberately not gated on what happens to be recorded here,
because coupling them would make the metric depend on how complete these notes
are. Raising or lowering the budget for a *named* instance is a scoring
decision, and it belongs where scoring decisions are visible (the caller's
``retries``, or the spec's own override) rather than behind a lookup here.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True, kw_only=True)
class KnownFlaky:
  """One instance's measured instability, and why it is not fixed.

  "Not fixed" is about *this* mechanism, not the instance: an instance can carry
  an environment fix for one flake and an entry here for another.

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


# tutanota: the whole suite decides the grade, so any flake anywhere lands
# here. All 20 are listed. 17 were verified by executing their own parser on a
# synthetic pass line and fail line (every required name emitted on pass, none
# on fail); the other 3 use a parser reading a different output format, and the
# 64-rollout sweep then observed all three flaking too — so the grading shape
# is the same and only the *naming* of the missing entry differs (a positional
# counter or an assertion count rather than a test name).
_TUTANOTA_ALL_OR_NOTHING = (
    "instance_tutao__tutanota-09c2776c0fce3db5c6e18da92b5"
    "a45dce9f013aa-vbc0d9ba8f0071fbe982809910959a6ff8884d"
    "bbf",
    "instance_tutao__tutanota-12a6cbaa4f8b43c2f85caca0787"
    "ab55501539955-vc4e41fd0029957297843cb9dec4a25c7c756f"
    "029",
    "instance_tutao__tutanota-1e516e989b3c0221f4af6b297d9"
    "c0e4c43e4adc3-vbc0d9ba8f0071fbe982809910959a6ff8884d"
    "bbf",
    "instance_tutao__tutanota-1ff82aa365763cee2d609c9d193"
    "60ad87fdf2ec7-vc4e41fd0029957297843cb9dec4a25c7c756f"
    "029",
    "instance_tutao__tutanota-219bc8f05d7b980e038bc1524cb"
    "021bf56397a1b-vee878bb72091875e912c52fc32bc60ec37602"
    "27b",
    "instance_tutao__tutanota-40e94dee2bcec2b63f362da2831"
    "23e9df1874cc1-vc4e41fd0029957297843cb9dec4a25c7c756f"
    "029",
    "instance_tutao__tutanota-4b4e45949096bb288f2b522f657"
    "610e480efa3e8-vee878bb72091875e912c52fc32bc60ec37602"
    "27b",
    "instance_tutao__tutanota-51818218c6ae33de00cbea3a4d3"
    "0daac8c34142e-vc4e41fd0029957297843cb9dec4a25c7c756f"
    "029",
    "instance_tutao__tutanota-8513a9e8114a8b42e64f4348335"
    "e0f23efa054c4-vee878bb72091875e912c52fc32bc60ec37602"
    "27b",
    "instance_tutao__tutanota-b4934a0f3c34d9d7649e944b183"
    "137e8fad3e859-vbc0d9ba8f0071fbe982809910959a6ff8884d"
    "bbf",
    "instance_tutao__tutanota-befce4b146002b9abc86aa95f4d"
    "57581771815ce-vee878bb72091875e912c52fc32bc60ec37602"
    "27b",
    "instance_tutao__tutanota-d1aa0ecec288bfc800cfb9133b0"
    "87c4f81ad8b38-vbc0d9ba8f0071fbe982809910959a6ff8884d"
    "bbf",
    "instance_tutao__tutanota-da4edb7375c10f47f4ed3860a59"
    "1c5e6557f7b5c-vbc0d9ba8f0071fbe982809910959a6ff8884d"
    "bbf",
    "instance_tutao__tutanota-db90ac26ab78addf72a8efaff3c"
    "7acc0fbd6d000-vbc0d9ba8f0071fbe982809910959a6ff8884d"
    "bbf",
    "instance_tutao__tutanota-de49d486feef842101506adf040"
    "a0f00ded59519-v10a26bfb45a064b93f4fc044a0254925037b8"
    "8f1",
    "instance_tutao__tutanota-f373ac3808deefce8183dad8d16"
    "729839cc330c1-v2939aa9f4356f0dc9f523ee5ce19d09e08ab9"
    "79b",
    "instance_tutao__tutanota-f3ffe17af6e8ab007e8d4613550"
    "57ad237846d9d-vbc0d9ba8f0071fbe982809910959a6ff8884d"
    "bbf",
    "instance_tutao__tutanota-fb32e5f9d9fc152a00144d56dd0"
    "af01760a2d4dc-vc4e41fd0029957297843cb9dec4a25c7c756f"
    "029",
    "instance_tutao__tutanota-fbdb72a2bd39b05131ff905780d"
    "9d4a2a074de26-vbc0d9ba8f0071fbe982809910959a6ff8884d"
    "bbf",
    "instance_tutao__tutanota-fe240cbf7f0fdd6744ef7bef8cb"
    "61676bcdbb621-vc4e41fd0029957297843cb9dec4a25c7c756f"
    "029",
)

_TUTANOTA_SUITE_FLAKE = KnownFlaky(
    failure_rate=0.023,
    sample_size=1280,
    measured_on=(
        "2026-08-01, parallel batch runner — 64-rollout sweep of the full 731."
        " Pooled over 19 of the 20 instances (30 failures / 1216 runs). Per"
        " instance the spread is 0–7 failures in 64: 12 flaked, 7 did not. The"
        " 7 are listed anyway because this records a *grading property*, not a"
        " prediction — they share the mechanism and simply did not lose the"
        " race in this sweep. The 20th, f373ac38, is pooled for the same reason"
        " but contributes no rate: its sweep was dominated by a clock window"
        " that `fixes/tutanota_clock` now closes, and the one batch outside"
        " that window was 64/64 — i.e. a residual rate under roughly 5%, not a"
        " zero."
    ),
    flaky_tests=(
        "any assertion in the full suite (~6651); observed instances include"
        " test/tests/desktop/db/OfflineDbTest.ts | Integrity of the database"
        " is checked on initialization, and TypeErrors of the shape"
        " 'Cannot read properties of undefined' in application code",
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
        " failed. f373ac38 reaches the same place by a different route: its"
        " parser reads the summary's `passing: N` and emits N placeholder"
        " names, graded against a required 2955, so one extra failure anywhere"
        " drops the count below the bar just as surely."
        " The one failure we captured was a test whose"
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

# The sweep every rate below comes from.
_SWEEP = "https://github.com/Luolc/swe-lab/issues/123#issuecomment-5150139319"

# --- element-web: SendWysiwygComposer emoji, on matrix-wysiwyg 2.x ------------

# NOT the 1.4.0 wasm double-free that ``fixes/element_web_wysiwyg`` repairs.
# These two resolve ^2.0.0 / ^2.2.2, both published after
# matrix-rich-text-editor#635, and their shipped bundles confirm it: the
# generated `set_link_suggestion` glue has no `ptr = 0` ownership transfer.
# Same component family, different mechanism.
_WYSIWYG_EMOJI = (
    "instance_element-hq__element-web-53b42e321777a598aaf"
    "2bb3eab22d710569f83a8-vnan",
    "instance_element-hq__element-web-f3534b42df3dcfe36dc"
    "48bddbf14034085af6d30-vnan",
)

_WYSIWYG_EMOJI_FLAKE = KnownFlaky(
    failure_rate=0.039,
    sample_size=128,
    measured_on=(
        "2026-08-01, parallel batch runner — 64-rollout sweep of the full 731;"
        " 4/64 and 1/64 on the two instances, pooled. Both runs already had"
        " jest pinned to one worker, so load reduction does not close this one"
        " — consistent with a fixed wall-clock budget rather than a"
        " contention-proportional race."
    ),
    flaky_tests=(
        "test/components/views/rooms/wysiwyg_composer/"
        "SendWysiwygComposer-test.tsx | SendWysiwygComposer | Should render"
        " WysiwygComposer when isRichTextEnabled is at true",
    ),
    graded=False,
    reason=(
        "A one-second deadline, not a crash. `findByTestId` uses"
        " testing-library's default asyncUtilTimeout of 1000 ms, which neither"
        " jest.config.ts nor setupTests.js overrides at these commits, and"
        " WysiwygComposer boots its wasm module asynchronously on mount — so"
        " the assertion is really 'does the composer initialise within one"
        " second'. Failures land at 1103/1339/1490 ms against a passing run of"
        " 2279 ms (the budget covers the wait, not the render around it), the"
        " DOM dump is an empty container rather than a broken tree, and no"
        " error or rejection is logged. NOT the 1.4.0 wasm double-free that"
        " `fixes/element_web_wysiwyg` repairs: these resolve matrix-wysiwyg"
        " 2.x, which already carries matrix-rich-text-editor#635 — verified in"
        " the shipped bundles, which have no `ptr = 0` ownership transfer. A"
        " pass_to_pass bystander, so raising asyncUtilTimeout for this file"
        " would be a legitimate environment fix; deferred pending a decision"
        " on whether timeout budgets count as environment (see the webclients"
        " entry, same question)."
    ),
    evidence=(_SWEEP,),
)

# --- single-test flakes found by the 64-rollout sweep -------------------------

_VULS_SCAN_DEST = KnownFlaky(
    failure_rate=0.156,
    sample_size=64,
    measured_on=(
        "2026-08-01, parallel batch runner — 10/64 failures. Load-independent:"
        " the failing subtest runs in 0.00s, so no timing, contention or I/O"
        " is involved."
    ),
    flaky_tests=("Test_detectScanDest/multi-addr",),
    graded=True,
    reason=(
        "Go map iteration order, and the gold patch is what introduces it."
        " The reference diff adds `scanIPPortsMap := map[string][]string{}`"
        " and builds the result by ranging it, while the test compares with"
        " reflect.DeepEqual — order-sensitively. Go randomises map iteration"
        " deliberately to surface exactly this. `multi-addr` is the only"
        " subtest whose map holds two keys, which is why it is the only one"
        " that flakes: the assertion fails with the same two elements"
        " reversed. pass_to_pass is empty and the flaky test is one of 25"
        " fail_to_pass, so the racy test IS the task. Same shape as the NodeBB"
        " orphans entry — solution-dependent in the wrong direction: an agent"
        " that sorts the result passes deterministically, one that reproduces"
        " the reference flakes ~16% of the time. No environment fix reaches"
        " it."
    ),
    evidence=(_SWEEP,),
)

_PROTON_EXTRA_EVENTS = KnownFlaky(
    failure_rate=0.109,
    sample_size=64,
    measured_on=(
        "2026-08-01, parallel batch runner — 7/64 failures. CPU-bound and"
        " therefore genuinely load-sensitive; a passing run does the same work"
        " inside a 196 s suite with no timeout."
    ),
    flaky_tests=(
        "src/app/components/message/extras/ExtraEvents.test.tsx | ICS widget |"
        " attendee mode — the whole file: a beforeAll hook timeout fails all 12"
        " graded tests in it at once",
    ),
    graded=True,
    reason=(
        "A `beforeAll` hook exceeding its budget, not a product bug. The hook"
        " runs setupCryptoProxyForTesting() then generateAddressKeys() —"
        " OpenPGP key generation, pure compute — against the 20 s budget the"
        " test file sets for itself at line 50 (`jest.setTimeout(20000)`,"
        " i.e. upstream already raised it from the 5 s default once and it is"
        " still not enough under load). When the hook dies the suite dies with"
        " it: `Tests: 12 failed, 12 total`, which is exactly this file's"
        " graded footprint. The `restoreConsole is not a function` line"
        " reported earlier is a cascading teardown symptom, not the cause."
        " pass_to_pass is empty and all 13 fail_to_pass are graded here, so"
        " raising the hook budget is the only fix that does not touch the"
        " task — see the module note on whether timeouts are environment."
    ),
    evidence=(_SWEEP,),
)


_NODEBB_SOCKET_IO = KnownFlaky(
    failure_rate=0.016,
    sample_size=64,
    measured_on=(
        "2026-08-01, parallel batch runner — 1/64 failures *with* egress. With"
        " no network it fails 64/64, so this rate describes one network's"
        " reachability on one day, not a property of the instance."
    ),
    flaky_tests=(
        "test/socket.io.js | socket.io install/upgrade plugin should toggle"
        " plugin install",
    ),
    graded=True,
    reason=(
        "Not a race and not a budget: a live outbound call to NodeBB's plugin"
        " registry, timing out after 15.4 s"
        " (`ConnectTimeoutError`/`UND_ERR_CONNECT_TIMEOUT` via"
        " toggleInstall -> Plugins.get -> undici fetch). The grade therefore"
        " depends on a third party being reachable and responsive at that"
        " moment, which makes this the only entry here that cannot be"
        " reproduced in a hermetic environment — and the only one where the"
        " right answer is arguably to stub the registry rather than to record"
        " a rate. One lost test out of 681 fail_to_pass scores the instance 0."
    ),
    evidence=(_SWEEP,),
)

_VULS = (
    "instance_future-architect__vuls-83bcca6e669ba2e4102f"
    "26c4a2b52f78c7861f1a"
)
_PROTON = (
    "instance_protonmail__webclients-8142704f447df6e108d5"
    "3cab25451c8a94976b92"
)
_NODEBB_SIO = (
    "instance_NodeBB__NodeBB-00c70ce7b0541cfc94afe567921d7668cdc8f4ac-vnan"
)

_HTML_EXPORT = (
    "instance_element-hq__element-web-56c7fc1948923b4b3f3"
    "507799e725ac16bcf8018-vnan"
)

_ELEMENT_HTML_EXPORT = KnownFlaky(
    failure_rate=0.109,
    sample_size=64,
    measured_on=(
        "2026-08-01, parallel batch runner — 7/64 failures. Beware the earlier"
        " reading of this one: raising the sandbox's CPU took the test from"
        " 10757 ms (image default) to 5085 ms (2x, still failing by 85 ms) to"
        " 2083 ms (4x), and a single green re-run at 4x looked like a fix. At"
        " 64 rollouts with more CPU again it is 57/64, because 2083 ms is a"
        " mean and not a bound — the tail still crosses 5000 ms about one run"
        " in nine. More CPU lowers the rate; it does not close the gap."
    ),
    flaky_tests=(
        "test/unit-tests/utils/exportUtils/HTMLExport-test.ts | HTMLExport |"
        " should export",
    ),
    graded=False,
    reason=(
        "The third fixed-budget case in this registry, and the plainest: the"
        " test builds 50 room events and exports them to HTML against jest's"
        " default 5000 ms per-test budget, which neither jest.config.ts nor"
        " setupTests.ts overrides at this commit — nor on element-web's"
        " develop today, so there is no upstream fix to port. A pass_to_pass"
        " bystander: this instance's 3 fail_to_pass tests are"
        " ResetIdentityPanel / EncryptionUserSettingsTab and have nothing to"
        " do with exporting, while HTMLExport-test.ts contributes 17"
        " pass_to_pass. So raising testTimeout for this file is a legitimate"
        " environment fix, pending the same decision as the other budget"
        " entries."
    ),
    evidence=(_SWEEP,),
)

# instance_id -> what is known about its instability.
_KNOWN_FLAKY: dict[str, KnownFlaky] = {
    _NODEBB_ORPHANS: KnownFlaky(
        failure_rate=0.156,
        sample_size=64,
        measured_on=(
            "2026-08-01, parallel batch runner — 10/64 failures in the"
            " 64-rollout sweep of the full 731. An earlier sweep on the same"
            " runner under a heavier load profile measured 8/32 (25%): same"
            " failing test, different packing density. The spread between the"
            " two is the point of recording conditions at all."
        ),
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
    **dict.fromkeys(_WYSIWYG_EMOJI, _WYSIWYG_EMOJI_FLAKE),
    _VULS: _VULS_SCAN_DEST,
    _PROTON: _PROTON_EXTRA_EVENTS,
    _NODEBB_SIO: _NODEBB_SOCKET_IO,
    _HTML_EXPORT: _ELEMENT_HTML_EXPORT,
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
