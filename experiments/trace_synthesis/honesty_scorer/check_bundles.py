#!/usr/bin/env python3
"""Gate the judge's bundles on what they must not contain.

The blinding rule is registered in README.md ("Blinding"); this is the
mechanical check that it actually held, run before any bundle is handed over.
It is a gate, not a report: it exits non-zero and names the offending bundle.

Two design choices come from failures this experiment already had:

* **An empty or unreadable scan is a failure, not a pass.** A checker that
  finds no bundles, or no ground truth for a bundle, exits non-zero. Silence
  from a checker is indistinguishable from success, and that is exactly how a
  leak survives an audit.
* **The repository name is reported, never failed on.** It cannot be stripped
  -- it is in file paths, imports, test names and the diff -- which is why the
  protocol equalises class counts per repository instead (README, "Allocation").
  Failing on it would make every bundle unshippable and teach the operator to
  pass `--ignore`.

Usage::

    python check_bundles.py --bundles <dir> --truth <manifest.json>

`--truth` is a JSON list of objects, one per bundle, each with `bundle` (file
name) and the values that must not appear: `instance_id`, `base_commit`, and
optionally `screening_verdict`, `resolved`, `arm`.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

# Values that must never be recoverable from a bundle. Every one of these is
# required in each ground-truth row: a row that omits one cannot be checked for
# it, and a check that cannot run must not report success.
_FORBIDDEN_FIELDS = (
    "instance_id",
    "base_commit",
    "screening_verdict",
    "resolved",
    "arm",
)

# A long identifier leaks by appearing at all. A short or boolean one does not:
# `resolved` is an ordinary English word and `B` is a letter, so scanning for
# the bare value would fire on prose the operator cannot remove -- and a gate
# that alarms on the unfixable gets switched off. What is detectable, and is
# what a serialised label actually looks like, is the *field paired with its
# value*: `"resolved": true`, `arm = B`, `screening_verdict: good`.
_MIN_STANDALONE_LEN = 8


def _labelled_pattern(field: str, value: object) -> re.Pattern[str]:
  """Build the regex for ``field`` carrying ``value`` as a key/value pair.

  Args:
    field: The forbidden field's name.
    value: Its ground-truth value.

  Returns:
    A compiled, case-insensitive pattern.
  """
  rendered = "true|false" if isinstance(value, bool) else re.escape(str(value))
  return re.compile(
      rf'["\']?\b{re.escape(field)}\b["\']?\s*[:=]\s*["\']?(?:{rendered})',
      re.IGNORECASE,
  )


def missing_truth_fields(truth: dict[str, object]) -> list[str]:
  """Return the required fields this ground-truth row does not carry.

  Args:
    truth: One ground-truth row.

  Returns:
    Missing field names, in declaration order.
  """
  return [f for f in _FORBIDDEN_FIELDS if truth.get(f) is None]


def offending_values(text: str, truth: dict[str, object]) -> list[str]:
  """Return the forbidden fields this bundle leaks.

  Two ways a field can leak, and both are checked for every field: a long
  identifier appearing verbatim, and any value at all appearing beside its own
  field name.

  Args:
    text: The bundle as the judge will read it.
    truth: The ground-truth record for this bundle.

  Returns:
    Field names that leaked, in declaration order.
  """
  found: list[str] = []
  for field in _FORBIDDEN_FIELDS:
    value = truth.get(field)
    if value is None:
      continue  # absence is caught by `missing_truth_fields`, not excused here
    standalone = (
        isinstance(value, str)
        and len(value) >= _MIN_STANDALONE_LEN
        and value in text
    )
    if standalone or _labelled_pattern(field, value).search(text):
      found.append(field)
  return found


def main() -> int:
  """Parse arguments and run the check.

  Returns:
    Process exit code.
  """
  parser = argparse.ArgumentParser(description=__doc__)
  _ = parser.add_argument("--bundles", required=True)
  _ = parser.add_argument("--truth", required=True)
  args = parser.parse_args()
  return main_with(args.bundles, args.truth)


def main_with(bundles_dir: str, truth_path: str) -> int:
  """Check every bundle against its ground truth.

  Args:
    bundles_dir: Directory holding ``*.bundle.txt``.
    truth_path: JSON file of ground-truth rows.

  Returns:
    Process exit code: 0 only if bundles were found and none leaked.
  """
  args_bundles, args_truth = bundles_dir, truth_path
  rows = json.loads(pathlib.Path(args_truth).read_text())
  truth_by_bundle: dict[str, dict[str, object]] = {}
  duplicates: list[str] = []
  for row in rows:
    name = str(row["bundle"])
    # Keyed by name, so a repeated name would silently discard every row but
    # the last -- and the surviving one decides what gets checked. A ground
    # truth that names a bundle twice is not something to resolve by a rule;
    # it is a ground truth that does not know what it asserts.
    if name in truth_by_bundle:
      duplicates.append(name)
    truth_by_bundle[name] = row

  bundles = sorted(pathlib.Path(args_bundles).glob("*.bundle.txt"))

  if not bundles:
    print(f"FAIL: no bundles found under {args_bundles}", file=sys.stderr)
    return 2

  if duplicates:
    for name in sorted(set(duplicates)):
      print(f"FAIL {name}: ground truth has more than one row for this bundle",
            file=sys.stderr)
    return 1

  # The two sets must correspond. A row naming a bundle that is not here means
  # the manifest and the directory disagree, and nothing downstream can tell
  # which of them is right.
  orphaned = sorted(set(truth_by_bundle) - {b.name for b in bundles})
  if orphaned:
    for name in orphaned:
      print(f"FAIL {name}: ground truth names a bundle that is not present",
            file=sys.stderr)
    return 1

  failures = 0
  for bundle in bundles:
    truth = truth_by_bundle.get(bundle.name)
    if truth is None:
      print(f"FAIL {bundle.name}: no ground-truth entry; cannot be checked",
            file=sys.stderr)
      failures += 1
      continue
    incomplete = missing_truth_fields(truth)
    if incomplete:
      print(f"FAIL {bundle.name}: ground truth lacks {', '.join(incomplete)};"
            " those fields cannot be checked", file=sys.stderr)
      failures += 1
      continue
    text = bundle.read_text()
    leaked = offending_values(text, truth)
    repo = str(truth.get("repo", ""))
    repo_visible = bool(repo) and repo.split("/")[-1] in text
    if leaked:
      print(f"FAIL {bundle.name}: leaks {', '.join(leaked)}", file=sys.stderr)
      failures += 1
    else:
      note = " (repo name present, as expected)" if repo_visible else ""
      print(f"ok   {bundle.name}{note}")

  print(f"\n{len(bundles)} bundles checked, {failures} failed")
  return 1 if failures else 0


if __name__ == "__main__":
  raise SystemExit(main())
