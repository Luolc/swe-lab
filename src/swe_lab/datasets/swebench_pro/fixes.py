"""Per-instance eval fixes for upstream flakiness in SWE-Bench Pro images.

WHY THIS EXISTS
---------------
A handful of instances flake for reasons that have **nothing to do with the task
under evaluation**: the flaky tests sit in ``pass_to_pass``, not
``fail_to_pass``, so an agent can solve the task perfectly and still be graded
unresolved. That is a false negative, and a load-sensitive one — the same model
scores differently depending on how densely a batch is packed. Left alone it
shows up as model variance, which is exactly what the eval is supposed to
measure.

WHAT A FIX MAY DO
-----------------
Each entry is a **harness fix, not a task fix**. To qualify it must:

- touch no ``fail_to_pass`` test and no source under test — only the
  environment the tests run in;
- carry its upstream evidence (the issue or PR that diagnosed the bug), so a
  reader can check the reasoning rather than trust it;
- state plainly where it *diverges* from the image's pinned world, because
  "we knowingly deviate, and here is why" is the claim being defended.

Dropping entries from ``pass_to_pass`` is deliberately **not** one of the
options here: that edits the benchmark, where an environment fix does not.

HOW IT IS WIRED
---------------
``SweBenchProInstance.unit_test_spec`` compiles the spec as usual, then hands it
to :func:`apply_instance_fix`. Every graded path — ``verify.py``'s golden
self-check, ``swe-lab eval``, and rollout's ``--grade`` — goes through that one
method, so grading and the golden self-check can never disagree about which
fixes were applied.

Bash contributed by a fix is spliced in at exactly one seam: **after** the
golden test checkout and **before** the test run, still under ``set -e``.
Anything earlier is undone by ``git reset --hard`` or overwritten by the golden
checkout, and a fix that fails must abort the run loudly rather than let a
half-patched tree be graded.
"""

from __future__ import annotations

import base64
from collections.abc import Callable, Sequence
import hashlib

from swe_lab.evaluation.verdict import UnitTestSpec
from swe_lab.sandbox import Inline, Mount

from ._wysiwyg_tarball import TARBALL_B64
from .unit_test import SweBenchProVerdict

type SweBenchProUnitTestSpec = UnitTestSpec[SweBenchProVerdict]

# The eval script's own "setup is done, the test run starts here" marker (see
# ``_build_eval_script``): ``set -e`` is lifted there because a failing test
# suite is a result, not an error. Fix lines go immediately *before* it, so they
# still run under ``set -e`` and abort the run when they fail.
_RUN_MARKER = "set +e"


def apply_instance_fix(
    instance_id: str, spec: SweBenchProUnitTestSpec
) -> SweBenchProUnitTestSpec:
  """Apply this instance's environment fix, if it has one.

  Args:
    instance_id: The instance the spec was compiled for.
    spec: The freshly compiled spec.

  Returns:
    The fixed spec, or ``spec`` unchanged when the instance needs no fix (the
    overwhelmingly common case).
  """
  fix = _FIXES.get(instance_id)
  return spec if fix is None else fix(spec)


def _with_setup(
    spec: SweBenchProUnitTestSpec,
    *,
    setup: str,
    mounts: dict[str, Mount],
) -> SweBenchProUnitTestSpec:
  """Return ``spec`` with extra setup staged and spliced into its script.

  Args:
    spec: The spec to extend.
    setup: Bash to run after the golden checkout, before the test run.
    mounts: Extra files the bash needs staged in the workspace.

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


def _render(template: str, **values: str) -> str:
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


# --- element-web aec454dd: wasm/GC double-free in the wysiwyg composer -------

_WYSIWYG_INSTANCE = (
    "instance_element-hq__element-web"
    "-aec454dd6feeb93000380523cbb0b3681c0275fd-vnan"
)
_WYSIWYG_TARBALL_NAME = "matrix-wysiwyg-1.4.1.tgz"
_WYSIWYG_PACKAGE = "node_modules/@matrix-org/matrix-wysiwyg"
_WYSIWYG_VERSION = "1.4.1"

# Runs from the repo root (the script has already `cd`-ed to WORKDIR), under
# `set -e`, after the golden checkout. `node_modules` is untracked, so nothing
# earlier in the script can undo this and nothing later re-resolves it.
_WYSIWYG_SETUP = """
tarball="$SANDBOX_WORKSPACE/@TARBALL@"
actual="$(sha512sum "$tarball" | cut -d' ' -f1)"
if [ "$actual" != "@SHA512@" ]; then
  echo "matrix-wysiwyg tarball integrity mismatch: $actual" >&2
  exit 1
fi
if [ ! -d "@PACKAGE@" ]; then
  echo "@PACKAGE@ not found; the eval image's layout changed" >&2
  exit 1
fi
find node_modules -type d -path '*/@matrix-org/matrix-wysiwyg' |
while IFS= read -r pkg; do
  rm -rf "$pkg" && mkdir -p "$pkg"
  tar -xzf "$tarball" -C "$pkg" --strip-components=1
done
installed="$(node -p "require('./@PACKAGE@/package.json').version")"
if [ "$installed" != "@VERSION@" ]; then
  echo "matrix-wysiwyg replacement did not take: got $installed" >&2
  exit 1
fi
"""


def _fix_instance_element_web_aec454dd(
    spec: SweBenchProUnitTestSpec,
) -> SweBenchProUnitTestSpec:
  """Replace ``@matrix-org/matrix-wysiwyg`` 1.4.0 with the fixed 1.4.1.

  In 1.4.0 the ``set_link_suggestion`` binding takes ``SuggestionPattern`` **by
  value**, so Rust takes ownership and drops it while the JS wrapper stays
  registered with a ``FinalizationRegistry``. When GC later runs that finalizer
  it frees already-freed memory and wasm-bindgen's borrow guard throws
  ``recursive use of an object detected which would lead to unsafe aliasing in
  rust`` — asynchronously, into whichever test happens to be running. Hence the
  flake is GC-timed: random, load-sensitive, and it moves between tests.

  Diagnosed upstream in element-hq/element-web#24951; fixed by
  matrix-org/matrix-rich-text-editor#635 (``&SuggestionPattern`` + ``clone``),
  published as 1.4.1. This instance's ``base_commit`` (2023-03-27 08:07 UTC)
  lands 2h50m *before* matrix-org/matrix-react-sdk#10458 reverted the feature
  that reaches the broken binding, so the tree is frozen mid-bug — and the 7
  ``Mentions`` tests that reach it are in ``pass_to_pass``, along with 22 more
  wysiwyg tests that fail as collateral. None of them are in ``fail_to_pass``.

  **The divergence:** the image installs with ``--frozen-lockfile`` and the
  lockfile pins 1.4.0 exactly, so this is not a neutral re-resolution — it
  knowingly overrides the lockfile. What makes it a safe trade: 1.4.1 is inside
  the ``^1.4.0`` range ``package.json`` already declares, ``dist/index.d.ts``
  is byte-identical between the two, the package has no runtime dependencies,
  and the published diff is confined to the one binding above.

  Args:
    spec: The compiled spec for this instance.

  Returns:
    The spec with the tarball staged and the replacement spliced in.
  """
  tarball = wysiwyg_tarball()
  return _with_setup(
      spec,
      mounts={_WYSIWYG_TARBALL_NAME: Mount(Inline(tarball))},
      setup=_render(
          _WYSIWYG_SETUP,
          TARBALL=_WYSIWYG_TARBALL_NAME,
          # Derived from the vendored bytes rather than written down twice, so
          # the check in the container cannot drift from what is mounted.
          SHA512=hashlib.sha512(tarball).hexdigest(),
          PACKAGE=_WYSIWYG_PACKAGE,
          VERSION=_WYSIWYG_VERSION,
      ),
  )


def wysiwyg_tarball() -> bytes:
  """Return the vendored ``@matrix-org/matrix-wysiwyg`` 1.4.1 tarball bytes."""
  return base64.b64decode(TARBALL_B64)


# instance_id -> the fix applied to its spec after compilation.
_FIXES: dict[
    str, Callable[[SweBenchProUnitTestSpec], SweBenchProUnitTestSpec]
] = {
    _WYSIWYG_INSTANCE: _fix_instance_element_web_aec454dd,
}


def fixed_instances() -> Sequence[str]:
  """Return the instance ids that carry a fix (for reporting / audit)."""
  return tuple(_FIXES)
