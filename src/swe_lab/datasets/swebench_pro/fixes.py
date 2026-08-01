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

EXTENDING IT
------------
The registry is open (import-only extension, like ``register_sandbox``): a
consuming project hits its own broken instances, and those are its business, not
ours. Register from anywhere that gets imported before a run::

    from swe_lab.datasets.swebench_pro.fixes import register_fix, with_setup

    def _fix_instance_acme_widget_1234(spec):
      return with_setup(
          spec,
          setup='sed -i "s/localhost/127.0.0.1/" test/config.json',
          mounts={},
      )

    register_fix("instance_acme__widget-1234", _fix_instance_acme_widget_1234)

Last registration wins, so a downstream user can also deliberately replace one
of the fixes here. The bar in "WHAT A FIX MAY DO" is not enforced by code — it
cannot be — so it is on each author to hold to it; a fix that quietly makes a
task easier is indistinguishable at runtime from one that repairs the
environment, and only the docstring tells the difference.
"""

from __future__ import annotations

import base64
from collections.abc import Callable, Sequence
import hashlib

from swe_lab.evaluation.verdict import UnitTestSpec
from swe_lab.sandbox import Inline, Mount

from ._wysiwyg_tarball import TARBALL_B64
from .constants import RUN_SCRIPT_NAME
from .unit_test import SweBenchProVerdict

type SweBenchProUnitTestSpec = UnitTestSpec[SweBenchProVerdict]

# What a fix is: a compiled spec in, a fixed spec out. Pure — it must not mutate
# the spec it is given, so compiling twice yields the same thing twice.
type InstanceFix = Callable[[SweBenchProUnitTestSpec], SweBenchProUnitTestSpec]

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
  return with_setup(
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


# --- ansible a20a5270: pytest-xdist temp-directory collision ------------------

_ANSIBLE_XDIST_INSTANCE = (
    "instance_ansible__ansible-a20a52701402a12f91396549df"
    "04ac55809f68e9-v1055803c3a812189a1133297f7f546857928"
    "3f86"
)

# Runs from the repo root under `set -e`, after the golden checkout. Rewrites
# the *harness's* run script, not the repo: the only thing that changes is how
# many processes run the tests.
_ANSIBLE_XDIST_SETUP = """
script="$SANDBOX_WORKSPACE/@RUN_SCRIPT@"
units="ansible-test units"
if ! grep -q "$units --python" "$script"; then
  echo "no '$units' invocation in @RUN_SCRIPT@; the harness changed" >&2
  exit 1
fi
sed -i "s|$units --python|$units --num-workers 1 --python|" "$script"
if ! grep -q -- "--num-workers 1" "$script"; then
  echo "failed to pin ansible-test to a single worker" >&2
  exit 1
fi
"""


def _fix_instance_ansible_a20a5270(
    spec: SweBenchProUnitTestSpec,
) -> SweBenchProUnitTestSpec:
  """Pin ``ansible-test units`` to one worker, ending an xdist tmpdir race.

  The harness runs unit tests through ``ansible-test units``, which builds its
  own pytest command with **``-n auto``** hardcoded
  (``test/lib/ansible_test/_internal/units/__init__.py``), so the worker count
  is the machine's CPU count. Those workers then share one numbered basetemp —
  the observed failure is an ERROR at *setup* from worker ``[gw10]``, with a
  basetemp of ``/tmp/pytest-of-root/pytest-15`` carrying no per-worker
  ``popen-gwN`` component — and ``tmp_path_factory.mktemp`` appends a
  predictable ``…Input0`` suffix, so two workers land on the same directory and
  race a recursive skeleton copy. The loser gets ``FileNotFoundError`` mid-copy,
  which is why the failing test name moves between runs: it is whichever
  fixture lost.

  Because the rate scales with the worker count, this is *not* reliably fixed
  by retrying: on a wide machine the collision is close to certain, and it was
  still failing at three retries. Pinning the worker count removes the race
  instead of resampling it.

  ``--num-workers`` is a documented ``ansible-test units`` flag at this commit
  (``test/lib/ansible_test/_internal/cli.py``), so this asks the harness for
  something it already supports. The cost is near zero: the instance selects
  two files, only one of which is a unit test.

  **The divergence:** the harness's own invocation is edited in the workspace,
  which no other entry here does. What keeps it an environment fix is that it
  changes neither the tests nor the source under test nor the expectation — only
  how many processes run them. The instance's single ``fail_to_pass``
  (``test_extract_tar_file_outside_dir``) never failed; all 56 observed failures
  were ``pass_to_pass`` bystanders riding the same run.

  Args:
    spec: The compiled spec for this instance.

  Returns:
    The spec with the worker-count pin spliced in.
  """
  return with_setup(
      spec,
      mounts={},
      setup=_render(_ANSIBLE_XDIST_SETUP, RUN_SCRIPT=RUN_SCRIPT_NAME),
  )


# instance_id -> the fix applied to its spec after compilation.
_FIXES: dict[str, InstanceFix] = {
    _WYSIWYG_INSTANCE: _fix_instance_element_web_aec454dd,
    _ANSIBLE_XDIST_INSTANCE: _fix_instance_ansible_a20a5270,
}


def register_fix(instance_id: str, fix: InstanceFix) -> None:
  """Register a fix for one instance (import-only extension).

  For a consuming project that hits its own broken instance: register at import
  time and every graded path picks it up, with no change to this module. Write
  the fix in terms of :func:`with_setup` so its bash lands in the one window
  that survives the reset and the golden checkout.

  Last registration wins, so this can also replace a fix defined here —
  deliberate, for a downstream user who needs different treatment of the same
  instance.

  Args:
    instance_id: The instance the fix applies to.
    fix: Takes the compiled spec and returns the fixed one, mutating nothing.
  """
  _FIXES[instance_id] = fix


def fixed_instances() -> Sequence[str]:
  """Return every instance id carrying a fix, sorted (reporting / audit)."""
  return sorted(_FIXES)


def applied_fix_name(instance_id: str) -> str | None:
  """Return the name of the fix this instance gets, for the run record.

  A fix changes what ran, so a persisted grade has to say which one applied —
  otherwise two runs of the same instance are indistinguishable in the manifest
  while having graded different trees.

  Args:
    instance_id: The instance to look up.

  Returns:
    The fix's name, or ``None`` when the instance has no fix.
  """
  fix = _FIXES.get(instance_id)
  if fix is None:
    return None
  # A downstream fix may be a callable object or a partial rather than a plain
  # function; fall back to its type so the record still names *something*.
  return getattr(fix, "__name__", type(fix).__name__)
