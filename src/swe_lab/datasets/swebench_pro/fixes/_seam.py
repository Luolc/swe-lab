"""The seam every fix is written against: splice bash, stage mounts.

Kept apart from the registry so a fix module can import the building blocks
without importing the package that imports it back.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from swe_lab.evaluation.verdict import UnitTestSpec
from swe_lab.sandbox import Mount

from ..unit_test import SweBenchProVerdict

type SweBenchProUnitTestSpec = UnitTestSpec[SweBenchProVerdict]

# What a fix is: a compiled spec in, a fixed spec out. Pure — it must not mutate
# the spec it is given, so compiling twice yields the same thing twice.
type InstanceFix = Callable[[SweBenchProUnitTestSpec], SweBenchProUnitTestSpec]

# ``_build_eval_script``): ``set -e`` is lifted there because a failing test
# suite is a result, not an error. Fix lines go immediately *before* it, so they
# still run under ``set -e`` and abort the run when they fail.
_RUN_MARKER = "set +e"


def with_setup(
    spec: SweBenchProUnitTestSpec,
    *,
    setup: str,
    mounts: dict[str, Mount],
) -> SweBenchProUnitTestSpec:
  """Return ``spec`` with extra setup staged and spliced into its script.

  The building block every fix is written in terms of — public so a downstream
  fix gets the placement right for free rather than doing its own string
  surgery on ``eval_script`` and landing the bash in a window where ``git reset
  --hard`` or the golden checkout wipes it.

  Args:
    spec: The spec to extend.
    setup: Bash to run after the golden checkout, before the test run. It runs
      from the repo root under ``set -e``, so a failing line aborts the run
      instead of grading a half-patched tree — make each step assert what it
      did, since a fix that silently no-ops reads as a clean run.
    mounts: Extra files the bash needs staged in the workspace, keyed by
      workspace-relative name (reachable as ``$SANDBOX_WORKSPACE/<name>``).

  Returns:
    A new spec; the original is untouched.

  Raises:
    ValueError: If the script has no unique run marker to splice against (its
      shape changed and this seam needs revisiting), or if a mount target is
      already claimed by the spec.
  """
  lines = spec.eval_script.splitlines()
  markers = [i for i, line in enumerate(lines) if line == _RUN_MARKER]
  if len(markers) != 1:
    raise ValueError(
        f"expected exactly one {_RUN_MARKER!r} line in the eval script to"
        f" splice setup before, found {len(markers)}"
    )
  at = markers[0]
  spliced = [*lines[:at], *setup.strip().splitlines(), *lines[at:]]

  clash = mounts.keys() & spec.mounts.keys()
  if clash:
    raise ValueError(f"fix mounts collide with the spec's: {sorted(clash)}")

  return UnitTestSpec(
      eval_script="\n".join(spliced) + "\n",
      mounts={**spec.mounts, **mounts},
      grader=spec.grader,
      native_outputs=dict(spec.native_outputs),
  )


def render(template: str, **values: str) -> str:
  """Substitute ``@NAME@`` placeholders in a bash template.

  Not ``str.format``/f-strings: bash reaches for ``{}`` freely (brace groups,
  ``${var}``, ``find -exec``), and doubling every one of them is exactly how a
  shell snippet stops being readable as a shell snippet.

  Args:
    template: Bash text containing ``@NAME@`` placeholders.
    **values: One value per placeholder name.

  Returns:
    The rendered bash.

  Raises:
    ValueError: If the template has no such placeholder — a rename that
      silently stopped substituting would ship ``@SHA512@`` to the container.
  """
  for name, value in values.items():
    if f"@{name}@" not in template:
      raise ValueError(f"template has no placeholder @{name}@")
    template = template.replace(f"@{name}@", value)
  return template


@dataclass(frozen=True, slots=True)
class RegisteredFix:
  """One defect, and every instance it is known to affect.

  The unit is the *defect*, not the instance: a fix that repairs several
  instances is declared once and lists them, so its reasoning is written in one
  place instead of copied per id.

  Attributes:
    instances: The instance ids this fix applies to.
    fix: Takes the compiled spec and returns the fixed one.
  """

  instances: Sequence[str]
  fix: InstanceFix
