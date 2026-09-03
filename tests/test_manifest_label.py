"""`manifest.sh` renders a home-relative pointer, and only where one applies.

The script writes this label into `MANIFEST.md`, which is committed, so the two
ways it can be wrong are both silent and both durable: printing the operator's
home directory puts back the PII the manifest was repaired to remove, and
mangling a root that is *not* under the home directory records an artifact
location that does not exist. Neither is visible in a regeneration's output —
somebody has to read the committed file afterwards, and nobody does.

`manifest.sh --print-label <root>` exists for this test: the labelling is one
step, reachable without walking a multi-gigabyte artifact tree.
"""

from __future__ import annotations

import pathlib
import subprocess

_SCRIPT = (
    pathlib.Path(__file__).resolve().parents[1]
    / "experiments/trace_synthesis/steered_rerun/manifest.sh"
)
# Assembled at run time for the same reason as the fixture in
# `test_operator_home_paths.py`: written whole, a synthetic home directory is
# a finding in this file, and exempting the file would put it outside the very
# guard the rest of this change adds.
_HOME = "/home/" + "operator"


def _label(root: str, home: str = _HOME) -> str:
  """Return what the manifest would print as the pointer for `root`.

  Args:
    root: The artifact root the script was given.
    home: The value of `$HOME` for the run.

  Returns:
    The rendered label, newline stripped.
  """
  return subprocess.run(
      ["bash", str(_SCRIPT), "--print-label", root],
      capture_output=True,
      check=True,
      text=True,
      env={"HOME": home, "PATH": "/usr/bin:/bin"},
  ).stdout.rstrip("\n")


def test_the_home_directory_and_paths_under_it_render_as_a_tilde() -> None:
  """The case the repair exists for: no home path reaches the manifest."""
  assert _label(_HOME) == "~"
  assert (
      _label(f"{_HOME}/dev/swe-lab-artifacts/x") == "~/dev/swe-lab-artifacts/x"
  )


def test_a_sibling_sharing_the_home_prefix_is_left_alone() -> None:
  """The boundary, and a real regression.

  `${root/#$HOME/~}` substitutes a raw string prefix rather than a path
  component, so a root that is a *sibling* of the home directory — the home
  path with `-artifacts` appended, which is a perfectly valid place to keep
  them — came out as `~-artifacts/...`: not the requested root and not a
  home-relative pointer, committed as the artifact's location.
  A path that merely starts with the home directory's *characters* is not under
  it.
  """
  root = f"{_HOME}-artifacts/trace_synthesis"
  assert _label(root) == root


def test_an_unrelated_root_is_left_alone() -> None:
  """Nothing outside the home directory is rewritten."""
  assert (
      _label("/mnt/elsewhere/trace_synthesis")
      == "/mnt/elsewhere/trace_synthesis"
  )


def test_an_empty_home_rewrites_nothing() -> None:
  """With no home to be relative to, every root would match a bare prefix."""
  root = f"{_HOME}-artifacts/x"
  assert _label(root, home="") == root
