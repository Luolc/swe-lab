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
        " Pooled over all 20 instances (30 failures / 1280 runs). Per instance"
        " the spread is 0–7 failures in 64: 12 flaked, 8 did not. The 8 are"
        " listed anyway because this records a *grading property*, not a"
        " prediction — they share the mechanism and simply did not lose the"
        " race in this sweep."
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

# The sweep every rate below comes from.
_SWEEP = "https://github.com/Luolc/swe-lab/issues/123#issuecomment-5150139319"

# --- element-web: SendWysiwygComposer emoji, on matrix-wysiwyg 2.x ------------

# NOT the 1.4.0 wasm double-free that ``fixes.py`` repairs. These two resolve
# ^2.0.0 / ^2.2.2, both published after matrix-rich-text-editor#635, and their
# shipped bundles confirm it: the generated `set_link_suggestion` glue has no
# `ptr = 0` ownership transfer. Same component family, different mechanism.
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
        " 4/64 and 1/64 on the two instances, pooled here"
    ),
    flaky_tests=(
        "test/components/views/rooms/wysiwyg_composer/"
        "SendWysiwygComposer-test.tsx | Emoji when isRichTextEnabled is false"
        " | Should add an emoji in an empty composer",
    ),
    graded=False,
    reason=(
        "A pass_to_pass bystander, so a fix is permitted in principle. It is"
        " NOT the wasm finalizer bug: both instances resolve matrix-wysiwyg"
        " 2.x, which already carries the #635 fix, so the 1.4.1 swap in"
        " fixes.py does not apply here. Mechanism undiagnosed — the sweep"
        " captured the test name and the rates, not a stack."
    ),
    evidence=(_SWEEP,),
)

# --- single-test flakes found by the 64-rollout sweep -------------------------

_VULS_SCAN_DEST = KnownFlaky(
    failure_rate=0.156,
    sample_size=64,
    measured_on="2026-08-01, parallel batch runner — 10/64 failures",
    flaky_tests=("Test_detectScanDest",),
    graded=True,
    reason=(
        "Go, and the joint-worst non-tutanota flake. The failing test is in"
        " fail_to_pass, so no environment fix can remove it. No error line was"
        " captured, so the failure is an assertion mismatch rather than a"
        " crash. Undiagnosed; the name suggests host/port destination"
        " detection, which would make it order- or timing-sensitive under"
        " parallel load."
    ),
    evidence=(_SWEEP,),
)

_PROTON_EXTRA_EVENTS = KnownFlaky(
    failure_rate=0.109,
    sample_size=64,
    measured_on="2026-08-01, parallel batch runner — 7/64 failures",
    flaky_tests=(
        "src/app/components/message/extras/ExtraEvents.test.tsx | does not"
        " display a summary when responding to an invitation",
    ),
    graded=True,
    reason=(
        "Fails with `TypeError: restoreConsole is not a function` — a console"
        " mock's restore handle undefined at teardown, i.e. cross-test state"
        " leakage in the harness rather than a product bug. The test is in"
        " fail_to_pass, so it is the graded task. Also seen at 3/4 in an"
        " earlier smaller sweep, so it predates recent configuration changes."
    ),
    evidence=(_SWEEP,),
)

_ANSIBLE_COLLECTION = KnownFlaky(
    failure_rate=0.078,
    sample_size=64,
    measured_on="2026-08-01, parallel batch runner — 5/64 failures",
    flaky_tests=(
        "test/units/galaxy/test_collection.py::test_warning_extra_keys",
        "test/units/galaxy/test_collection.py::test_build_ignore_files_and_folders",
        "test/units/galaxy/test_collection.py::test_build_ignore_older_release_in_root",
        "test/units/galaxy/test_collection.py::test_invalid_yaml_galaxy_file",
    ),
    graded=False,
    reason=(
        "The only entry here whose failures scatter across tests rather than"
        " pinning one — five different tests in the same module, all"
        " pass_to_pass. That pattern points at shared temporary-directory or"
        " collection-build state between tests, where whichever test loses the"
        " race is the one that reports. Undiagnosed."
    ),
    evidence=(_SWEEP,),
)

_ELEMENT_JOIN_RULE = KnownFlaky(
    failure_rate=0.031,
    sample_size=64,
    measured_on="2026-08-01, parallel batch runner — 2/64 failures",
    flaky_tests=(
        "test/components/views/settings/JoinRuleSettings-test.tsx |"
        " <JoinRuleSettings /> | should not show knock room join rule",
    ),
    graded=True,
    reason=(
        "Unrelated to the wysiwyg wasm story despite the shared repo. A"
        " negative assertion ('should not show') that passes early is the"
        " classic missing-await/waitFor shape, but this is undiagnosed — no"
        " stack was captured. In fail_to_pass, so it is the graded task."
    ),
    evidence=(_SWEEP,),
)

_TELEPORT_FN_CACHE = KnownFlaky(
    failure_rate=0.016,
    sample_size=64,
    measured_on="2026-08-01, parallel batch runner — 1/64 failures",
    flaky_tests=("TestFnCacheSanity",),
    graded=True,
    reason=(
        "Go. A TTL/expiry cache test with real timing; 1-in-64 is the shape of"
        " a narrow timing window losing under contention. In fail_to_pass."
        " Undiagnosed — one failure is barely a measurement."
    ),
    evidence=(_SWEEP,),
)

_NODEBB_SOCKET_IO = KnownFlaky(
    failure_rate=0.016,
    sample_size=64,
    measured_on="2026-08-01, parallel batch runner — 1/64 failures",
    flaky_tests=(
        "test/socket.io.js | socket.io install/upgrade plugin should toggle"
        " plugin install",
    ),
    graded=True,
    reason=(
        "Plugin install touches shared global state and a real socket, so it"
        " is order-sensitive. In fail_to_pass. Undiagnosed — one failure is"
        " barely a measurement."
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
_ANSIBLE = (
    "instance_ansible__ansible-a20a52701402a12f91396549df"
    "04ac55809f68e9-v1055803c3a812189a1133297f7f546857928"
    "3f86"
)
_JOINRULE = (
    "instance_element-hq__element-web-9a31cd0fa849da810b4"
    "fac6c6c015145e850b282-vnan"
)
_TELEPORT = (
    "instance_gravitational__teleport-78b0d8c72637df1129f"
    "b6ff84fc49ef4b5ab1288"
)
_NODEBB_SIO = (
    "instance_NodeBB__NodeBB-00c70ce7b0541cfc94afe567921d7668cdc8f4ac-vnan"
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
    _ANSIBLE: _ANSIBLE_COLLECTION,
    _JOINRULE: _ELEMENT_JOIN_RULE,
    _TELEPORT: _TELEPORT_FN_CACHE,
    _NODEBB_SIO: _NODEBB_SOCKET_IO,
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
