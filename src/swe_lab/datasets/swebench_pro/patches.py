"""Temporary in-memory corrections to the upstream SWE-Bench Pro parquet.

WHY THIS EXISTS
---------------
Three instances in the upstream SWE-Bench Pro dataset ship a `fail_to_pass`
list containing test names that are **truncated by exactly one trailing
character** (a closing `"` in seven cases, a trailing space in one). Those
names therefore do not byte-for-byte match the names the instance's own
`parser.py` emits for the very same tests, and grading is exact string-set
membership (`resolved ⇔ (fail_to_pass ∪ pass_to_pass) ⊆ passed`). The tests
actually run and PASS in the container, but the required name never matches a
passed name, so the golden patch is scored a false `GOLDEN_FAIL` — for us and
for Scale's own reference `swe_bench_pro_eval.py` alike (identical grading,
same parser, same data). Full diagnosis, Docker repro, and cross-check:
`experiments/eval_issues/truncated_golden_test_names/` (README +
fixed_rows.json).

WHAT WE DO
----------
Until the corrected dataset is published, we keep downloading the *original*
upstream parquet unchanged and patch these three rows **in memory at load
time**: each truncated `fail_to_pass` entry is replaced by the exact name the
instance's parser emits (the unique passed name it is a strict prefix of).
Only these listed entries on these three instances are touched; every other
row, column, and instance is returned byte-for-byte as stored. Applying this
here means every downstream flow (evaluation, golden self-tests, rollout
grading, annotation) sees the corrected data without knowing about the quirk.

THIS IS TEMPORARY
-----------------
This is a stopgap, not the end state. The plan is to publish a single, fully
fixed SWE-Bench Pro parquet to our own Hugging Face dataset repo; once the
loader downloads that corrected parquet, these rows will already be right and
this module can be deleted (the fix is self-limiting — `patch_fail_to_pass`
only rewrites names it actually finds, so on already-fixed data it is a no-op).
"""

# The correction table below is verbatim test-name data: each key/value is a
# single, irreducible test id that cannot be wrapped without corrupting it, so
# line-length is disabled for this file.
# ruff: noqa: E501

from __future__ import annotations

# instance_id -> {truncated_name_in_parquet: full_name_the_parser_emits}.
# Every value is its key plus exactly one dropped trailing character. Sourced
# verbatim from
# experiments/eval_issues/truncated_golden_test_names/fixed_rows.json.
_TRUNCATED_FAIL_TO_PASS_FIXES: dict[str, dict[str, str]] = {
    'instance_NodeBB__NodeBB-00c70ce7b0541cfc94afe567921d7668cdc8f4ac-vnan': {
        'test/user.js | User Digest.getSubscribers should accurately build digest list given ACP default "day': 'test/user.js | User Digest.getSubscribers should accurately build digest list given ACP default "day"',
        'test/user.js | User Digest.getSubscribers should accurately build digest list given ACP default "week': 'test/user.js | User Digest.getSubscribers should accurately build digest list given ACP default "week"',
        'test/user.js | User Digest.getSubscribers should accurately build digest list given ACP default "off': 'test/user.js | User Digest.getSubscribers should accurately build digest list given ACP default "off"',
        'test/database.js | Test database test/database/sorted.js::Sorted Set methods test/database/sorted.js::getSortedSetRange() should work with big arrays (length > 100)': 'test/database.js | Test database test/database/sorted.js::Sorted Set methods test/database/sorted.js::getSortedSetRange() should work with big arrays (length > 100) ',
    },
    'instance_ansible__ansible-de5858f48dc9e1ce9117034e0d7e76806f420ca8-v1055803c3a812189a1133297f7f5468579283f86': {
        'test/units/galaxy/test_api.py::test_cache_invalid_cache_content[{"de': 'test/units/galaxy/test_api.py::test_cache_invalid_cache_content[{"de"',
    },
    'instance_future-architect__vuls-bff6b7552370b55ff76d474860eead4ab5de785a-v1151a6325649aaf997cd541ebe533b53fddf1b07': {
        'Test_redhatBase_parseUpdatablePacksLine/centos_7.0:_"zlib"_"0"_"1.2.7"_"17.el7"_"rhui-REGION-rhel-server-releases': 'Test_redhatBase_parseUpdatablePacksLine/centos_7.0:_"zlib"_"0"_"1.2.7"_"17.el7"_"rhui-REGION-rhel-server-releases"',
        'Test_redhatBase_parseUpdatablePacksLine/centos_7.0:_"shadow-utils"_"2"_"4.1.5.1_24.el7"_"rhui-REGION-rhel-server-releases': 'Test_redhatBase_parseUpdatablePacksLine/centos_7.0:_"shadow-utils"_"2"_"4.1.5.1_24.el7"_"rhui-REGION-rhel-server-releases"',
        'Test_redhatBase_parseUpdatablePacksLine/amazon_2023:_Is_this_ok_[y/N]:_"dnf"_"0"_"4.14.0"_"1.amzn2023.0.6"_"amazonlinux': 'Test_redhatBase_parseUpdatablePacksLine/amazon_2023:_Is_this_ok_[y/N]:_"dnf"_"0"_"4.14.0"_"1.amzn2023.0.6"_"amazonlinux"',
    },
}


def patch_fail_to_pass(
    instance_id: str, fail_to_pass: tuple[str, ...]
) -> tuple[str, ...]:
  """Return ``fail_to_pass`` with any known truncated names corrected.

  A no-op for every instance except the three documented above, and even for
  those it only rewrites entries that exactly match a known truncated name —
  so once the upstream parquet is fixed (names already full), nothing changes.
  """
  fixes = _TRUNCATED_FAIL_TO_PASS_FIXES.get(instance_id)
  if not fixes:
    return fail_to_pass
  return tuple(fixes.get(name, name) for name in fail_to_pass)
