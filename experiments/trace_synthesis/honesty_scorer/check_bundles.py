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
import sys

# Values that must never be recoverable from a bundle's text. `resolved` and
# `arm` are included because a bundle carrying either hands the judge the label
# directly; `screening_verdict` is the label's source.
_FORBIDDEN_FIELDS = (
    "instance_id",
    "base_commit",
    "screening_verdict",
    "resolved",
    "arm",
)


def offending_values(text: str, truth: dict[str, object]) -> list[str]:
  """Return the forbidden fields whose value appears verbatim in ``text``.

  Args:
    text: The bundle as the judge will read it.
    truth: The ground-truth record for this bundle.

  Returns:
    Field names that leaked, in declaration order.
  """
  found: list[str] = []
  for field in _FORBIDDEN_FIELDS:
    value = truth.get(field)
    # A short or boolean value would match everywhere; only substantial
    # identifiers are checkable this way, and the rest are excluded by
    # construction (they are never written into the bundle at all).
    if isinstance(value, str) and len(value) >= 8 and value in text:
      found.append(field)
  return found


def main() -> int:
  """Check every bundle against its ground truth.

  Returns:
    Process exit code: 0 only if bundles were found and none leaked.
  """
  parser = argparse.ArgumentParser(description=__doc__)
  _ = parser.add_argument("--bundles", required=True)
  _ = parser.add_argument("--truth", required=True)
  args = parser.parse_args()

  truth_by_bundle = {
      str(row["bundle"]): row
      for row in json.loads(pathlib.Path(args.truth).read_text())
  }
  bundles = sorted(pathlib.Path(args.bundles).glob("*.bundle.txt"))

  if not bundles:
    print(f"FAIL: no bundles found under {args.bundles}", file=sys.stderr)
    return 2

  failures = 0
  for bundle in bundles:
    truth = truth_by_bundle.get(bundle.name)
    if truth is None:
      print(f"FAIL {bundle.name}: no ground-truth entry; cannot be checked",
            file=sys.stderr)
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
