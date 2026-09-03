"""No tracked file carries an operator home path — and the scan can tell.

Two things are asserted here, and the second is what makes the first mean
anything: a scan reporting nothing because the repo is clean and a scan
reporting nothing because it cannot see are the same output, and only a control
arm separates them. So every assertion that the repo is clean is paired with
one that the same detector does fire on a path of that shape.

The rule itself, the allowlist, and why this is enforced in two places rather
than one live in `tests/operator_home_paths.py`.
"""

from __future__ import annotations

import pathlib
import subprocess

# Relative: `tests` is a package, and the sibling module is also run as a
# script by the pre-commit hook, so it stays importable either way.
from .operator_home_paths import (
    home_paths_in,
    NON_OPERATOR_HOMES,
    offenders,
    operator_home_paths_in,
)

_REPO = pathlib.Path(__file__).resolve().parents[1]

# Assembled at run time on purpose. Written whole, this fixture would be a
# finding in the very file whose job is to prove the guard works, and the two
# ways out are both worse: exempting this file would put the guard's own test
# outside the guard, and adding a synthetic home to `NON_OPERATOR_HOMES` would
# stop the positive arm from being positive.
_UNKNOWN_HOME = "/Users/" + "someoperator"


def _tracked_files() -> list[pathlib.Path]:
  """Return every file git tracks, as an absolute path.

  Returns:
    The tracked files.
  """
  listing = subprocess.run(
      ["git", "-C", str(_REPO), "ls-files", "-z"],
      capture_output=True,
      check=True,
      text=True,
  ).stdout
  return [_REPO / name for name in listing.split("\0") if name]


def test_no_tracked_file_carries_an_operator_home_path() -> None:
  """The repo-wide arm: every tracked file, not one experiment's `runs/`."""
  assert offenders(_tracked_files()) == []


def test_the_scan_fires_on_a_home_path_and_not_on_the_repaired_form() -> None:
  """The control arm for the assertion above.

  Both halves are needed. Without the first, a detector that matches nothing
  makes the repo look clean; without the second, one that matches everything
  would too — it would just be red instead, and the repair this guard asks for
  (`~/...`) would have nowhere to land.
  """
  assert home_paths_in(f"cd {_UNKNOWN_HOME}/dev/swe-lab") == [_UNKNOWN_HOME]
  assert home_paths_in("cd ~/dev/swe-lab") == []
  assert home_paths_in("cd /opt/swe-lab") == []


def test_a_finding_names_the_file_and_the_line(tmp_path: pathlib.Path) -> None:
  """A finding has to be actionable: which file, which line, which match."""
  recorded = tmp_path / "cmd.txt"
  recorded.write_text(
      f"cwd=/tmp/probe/ws\nPROBE_LOG={_UNKNOWN_HOME}/dev/x/hook_log.jsonl\n"
  )
  assert offenders([recorded]) == [f"{recorded}:2: {_UNKNOWN_HOME}"]


def test_an_exemption_covers_a_value_and_never_a_file() -> None:
  """Each listed home is exempt; nothing around it is.

  This is the property that makes the allowlist safe to grow: a file holding an
  exempt value is not itself exempt, so a real home path landing next to one is
  still a finding.
  """
  for synthetic in NON_OPERATOR_HOMES:
    assert operator_home_paths_in(f"ls {synthetic}/dev/x") == []
  beside = f"ls {sorted(NON_OPERATOR_HOMES)[0]}/x and {_UNKNOWN_HOME}/y"
  assert operator_home_paths_in(beside) == [_UNKNOWN_HOME]
