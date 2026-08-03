"""Per-instance eval fixes for defects in SWE-Bench Pro instances.

THE PRINCIPLE
-------------
One question decides everything here:

    **Is the gold patch correct, and the *test* the thing that is broken?**

If yes — a correct reference solution is being turned into a false negative by
something that is not the task — we fix it. That is the whole purpose of this
package.

If no — if the **gold patch itself** is what is wrong — we do not touch it.
NodeBB `22368b99` is the clean example: its gold patch deletes files without
awaiting, so it races the very test that grades it. No edit to the test can make
that patch correct, and bending the test to accommodate a broken reference would
be inventing a benchmark rather than repairing one. Those instances belong in
``known_flaky.py``, recorded and left alone.

The one guard on top: a fix must not make a test **looser or stricter** in a way
that changes what counts as solved. Removing a false negative is repair;
widening what passes is a false positive, and narrowing it is a new false
negative. If a change cannot be argued to leave the pass/fail boundary where it
was, it does not belong here.

Everything else follows from that. In practice the defects fall into a few
shapes, all of them "the test, not the patch":

- **the environment is wrong** — a dependency version with a known bug
  (``element_web_wysiwyg``);
- **the harness is wrong** — parallelism the tests cannot survive
  (``ansible_xdist``), a wall clock the suite turns out not to be indifferent to
  (``tutanota_clock``), or a build client that discards its own errors
  (``tutanota_build_server``);
- **the test is wrong** — it asserts something unobservable, or with a
  precision its own approximation cannot support, and upstream said so
  themselves (``element_web_joinrule``, ``teleport_fncache``).

The third is the weakest claim, because it edits a graded test. It is allowed
only when upstream fixed the same flake *in the test*, the assertion removed is
not about the instance's task, the test still runs and still asserts its
outcome, and the upstream sources are cited in the fix's docstring — so a reader
can check the reasoning instead of trusting it.

LAYOUT
------
One subpackage per fix, named for what it repairs. A fix that applies to several
instances is still one subpackage — the unit is the *defect*, not the instance,
so its reasoning is written once and its ``INSTANCES`` tuple lists everything it
covers. Anything bulky a fix needs (``element_web_wysiwyg`` vendors a 763 KB
tarball) lives beside it rather than in the shared namespace.

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

A fix that rewrites a file must assert the **shape** of what it produced, not
merely that a keyword appears. That guidance is paid for: a ``grep`` for a
wrapped call once matched a file where the rewrite had moved a semicolon inside
the call, so the guard passed on syntactically invalid JavaScript.

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
of the fixes here. The principle above is not enforced by code — it cannot be —
so it is on each author to hold to it; a fix that quietly makes a task easier is
indistinguishable at runtime from one that repairs a defect, and only the
docstring tells the difference.
"""

from __future__ import annotations

from collections.abc import Sequence

from ._seam import InstanceFix, SweBenchProUnitTestSpec, with_setup
from .ansible_xdist import ANSIBLE_XDIST
from .element_web_joinrule import ELEMENT_WEB_JOINRULE
from .element_web_wysiwyg import ELEMENT_WEB_WYSIWYG
from .teleport_fncache import TELEPORT_FNCACHE
from .tutanota_build_server import TUTANOTA_BUILD_SERVER
from .tutanota_clock import TUTANOTA_CLOCK

__all__ = [
    "InstanceFix",
    "SweBenchProUnitTestSpec",
    "applied_fix_name",
    "apply_instance_fix",
    "fixed_instances",
    "register_fix",
    "with_setup",
]

# Every fix subpackage, in no particular order — each one names the instances it
# covers, so adding a fix is adding a subpackage and a line here.
_REGISTERED = (
    ELEMENT_WEB_WYSIWYG,
    ANSIBLE_XDIST,
    ELEMENT_WEB_JOINRULE,
    TELEPORT_FNCACHE,
    TUTANOTA_CLOCK,
    TUTANOTA_BUILD_SERVER,
)

# instance_id -> the fix applied to its spec after compilation.
_FIXES: dict[str, InstanceFix] = {
    instance_id: registered.fix
    for registered in _REGISTERED
    for instance_id in registered.instances
}


def apply_instance_fix(
    instance_id: str, spec: SweBenchProUnitTestSpec
) -> SweBenchProUnitTestSpec:
  """Apply this instance's fix, if it has one.

  Args:
    instance_id: The instance the spec was compiled for.
    spec: The freshly compiled spec.

  Returns:
    The fixed spec, or ``spec`` unchanged when the instance needs no fix (the
    overwhelmingly common case).
  """
  fix = _FIXES.get(instance_id)
  return spec if fix is None else fix(spec)


def register_fix(instance_id: str, fix: InstanceFix) -> None:
  """Register a fix for one instance (import-only extension).

  For a consuming project that hits its own broken instance: register at import
  time and every graded path picks it up, with no change to this package. Write
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
