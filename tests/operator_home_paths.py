#!/usr/bin/env python3
"""Operator home paths in committed files: the rule, and its two domains.

`docs/conventions.md` forbids operator PII — a home directory path included —
in any committed record. This module is the one implementation of that rule,
read by the two checks that enforce it over two different input domains:

- The **`no-operator-home-paths` pre-commit hook** runs this file as a script
  over the files of the commit being made. That is the only domain that can see
  a home path *before* it lands, while removing it is one edit rather than a
  rewrite of an artifact somebody has since cited.
- **`tests/test_operator_home_paths.py`** runs :func:`offenders` over **every
  tracked file**, on every ``pytest`` run. That is the only domain that catches
  one which got in anyway — through ``--no-verify``, from a clone where nobody
  ran ``pre-commit install``, or before this guard existed. It is also the only
  place the detector itself gets a control arm, because a hook cannot test
  itself.

Neither replaces the other. The split, and the reasoning behind it, is the
credential scan's (`AGENTS.md` -> Quality bar), applied to a second class of
thing that must not be committed.

The rule is about the *shape* of a path, never about one machine's home
directory: a guard keyed to whoever happens to run it is green on every other
machine and in CI, which is indistinguishable from not existing.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
import pathlib
import re
import sys

# An absolute home directory path, of either platform's shape. The user segment
# is required, so the `~`-relative form this guard's repair writes is not a
# finding — naming a location under a home directory is fine, naming whose it
# is on which machine is not.
#
# The pattern does not match its own source (`/(?:home|Users)/` contains no
# literal home-path prefix), so the file it lives in stays inside the guard.
_HOME_PATH = re.compile(r"/(?:home|Users)/[A-Za-z0-9._-]+")

# Home directories that belong to nobody who runs this repo, each with the
# reason it is listed.
#
# Listed by **value, never by file**: a path exemption also exempts the next,
# real leak that lands in that file, whereas a value that is demonstrably
# synthetic stays safe wherever it appears. This is the `.gitleaksignore` model
# — one immutable fact per entry, added by somebody who wrote the line and read
# by somebody who reviewed it, and a list that can only shrink.
NON_OPERATOR_HOMES: dict[str, str] = {
    "/home/vuls": (
        "SWE-bench Pro problem-statement text, copied verbatim into"
        " experiments/eval_issues/truncated_golden_test_names/fixed_rows.json."
        " It names a container user in a third-party project's issue, not an"
        " operator; redacting it would falsify the dataset record the"
        " experiment exists to examine, which is a worse defect than the one"
        " this guard is for."
    ),
    "/Users/aturing": (
        "`_FAKE_HOME` in swe_lab.pipelines.related_files.exchange — the"
        " deliberately fake identity the trace redactor substitutes, and the"
        " value its tests assert survives into the redacted blob."
    ),
    "/Users/realperson": (
        "the synthetic `OperatorIdentity` in tests/test_publication_gate.py,"
        " which exists so that the publication gate can be shown to catch an"
        " operator home path in a message body."
    ),
}


def home_paths_in(text: str) -> list[str]:
  """Return every home directory path in `text`, allowlist not applied.

  Args:
    text: The text to scan.

  Returns:
    Each match, in order, duplicates kept.
  """
  return _HOME_PATH.findall(text)


def operator_home_paths_in(text: str) -> list[str]:
  """Return the home paths in `text` that are not known to be synthetic.

  Args:
    text: The text to scan.

  Returns:
    Each offending match, in order, duplicates kept.
  """
  return [
      found for found in home_paths_in(text) if found not in NON_OPERATOR_HOMES
  ]


def offenders(paths: Iterable[pathlib.Path | str]) -> list[str]:
  """Return a `<file>:<line>: <match>` finding per operator home path.

  A file that is not valid UTF-8 is skipped: the only such tracked file here is
  a parquet, and a byte sequence inside a binary that happens to spell a home
  path is noise rather than a record anybody reads.

  Args:
    paths: The files to scan.

  Returns:
    The findings, in the order the files were given; empty when clean.
  """
  found: list[str] = []
  for entry in paths:
    path = pathlib.Path(entry)
    try:
      text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, IsADirectoryError, FileNotFoundError):
      continue
    for number, line in enumerate(text.splitlines(), start=1):
      found.extend(
          f"{path}:{number}: {match}" for match in operator_home_paths_in(line)
      )
  return found


def main(argv: Sequence[str]) -> int:
  """Report operator home paths in the given files, for the pre-commit hook.

  Args:
    argv: The files to scan.

  Returns:
    The process exit status: 1 when anything was found, 0 otherwise.
  """
  found = offenders(argv)
  for finding in found:
    print(finding)
  if found:
    print(
        "\nAn operator home path must not be committed (docs/conventions.md)."
        " Write it home-relative instead — `~/...`, and `.expanduser()` where"
        " code resolves it. A home directory that is genuinely nobody's goes"
        " in NON_OPERATOR_HOMES in tests/operator_home_paths.py, with the"
        " reason it is there."
    )
  return 1 if found else 0


if __name__ == "__main__":
  raise SystemExit(main(sys.argv[1:]))
