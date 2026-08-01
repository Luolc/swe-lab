"""teleport 78b0d8c7: a self-calibrating wall-clock assertion with no margin."""

from __future__ import annotations

from .._seam import (
    RegisteredFix,
    render,
    SweBenchProUnitTestSpec,
    with_setup,
)

_TELEPORT_FNCACHE_INSTANCE = (
    "instance_gravitational__teleport-78b0d8c72637df1129f"
    "b6ff84fc49ef4b5ab1288"
)
_TELEPORT_FNCACHE_TEST = "lib/cache/fncache_test.go"

# Ports the tolerance change from gravitational/teleport@5f6cc766 ("Deflake
# TestFnCacheSanity", #10250) and nothing else — see the docstring for why the
# rest of that commit must not be ported. Runs after the golden checkout.
_TELEPORT_FNCACHE_SETUP = """
t="@TEST@"
old='readCounter.Load(), 1, msg...)'
new='readCounter.Load(), 2, msg...)'
if ! grep -qF "$old" "$t"; then
  echo "the +/-1 InDelta tolerance is not in @TEST@; already deflaked?" >&2
  exit 1
fi
sed -i "s|$old|$new|" "$t"
# Upstream moved the comment with the tolerance; leaving it at "+/- 1"
# would put a lie one line above the code.
sed -i 's|within +/- 1 of the number|within +/- 2 of the number|' "$t"
if grep -qF "$old" "$t"; then
  echo "failed to widen the InDelta tolerance in @TEST@" >&2
  exit 1
fi
if [ "$(grep -cF "$new" "$t")" != "1" ]; then
  echo "expected exactly one widened tolerance in @TEST@" >&2
  exit 1
fi
if grep -q 'within +/- 1 of the number' "$t"; then
  echo "the tolerance comment in @TEST@ still says +/- 1" >&2
  exit 1
fi
if grep -q 't.Run(' "$t"; then
  echo "@TEST@ gained subtests; graded names would no longer match" >&2
  exit 1
fi
"""


def _fix_instance_teleport_78b0d8c7(
    spec: SweBenchProUnitTestSpec,
) -> SweBenchProUnitTestSpec:
  """Widen `TestFnCacheSanity`'s tolerance, as upstream did.

  The test derives its own expectation from its own elapsed time and compares
  it to the observed refresh count with no margin::

      approxReads := float64(elapsed) / float64(ttl+delay)
      require.InDelta(t, approxReads, readCounter.Load(), 1, msg...)

  with 100 concurrent goroutines and 40 ms TTLs inside a 410 ms case. When the
  goroutines are descheduled, ``elapsed`` stretches while the refresh count does
  not, so ``approxReads`` reaches 6.50 against an actual 5 and the 1.50 gap
  busts the ±1 tolerance. The assertion is not wrong about the cache; it is
  wrong about how precise its own approximation can be.

  The line immediately below it is upstream's own tell: a sibling check was
  already commented out for the same reason, and this one was left in.

  **Upstream fixed exactly this** in gravitational/teleport@5f6cc766, "Deflake
  TestFnCacheSanity (#10250)", by widening the tolerance from 1 to 2. Its commit
  message reads *"This should prevent failures where due to our approximation,
  we estimate a fractional number of reads that exceed our tolerance of 1"*, and
  the sample error it quotes (``difference was 1.4610…``) is the same shape as
  the one observed here (``1.49933…``). The comment above the assertion is
  ported alongside it, as upstream did, rather than leaving a line that still
  claims ±1 sitting above code that no longer means it.

  This instance's commit (2021-10-22) is the one that *introduced* the test;
  upstream deflaked it on 2022-02-15, so the instance is frozen in between.
  Second case in this package of a graded test repaired by porting an upstream
  deflake, after ``element_web_joinrule``.

  **Only the tolerance is ported, deliberately.** The same upstream commit also
  restructures the table into ``t.Run`` subtests, which would rename the test to
  ``TestFnCacheSanity/long ttl, short delay``. This instance grades
  ``TestFnCacheSanity`` and ``TestFnCacheCancellation`` by exact name and has no
  ``pass_to_pass``, so porting that half would make the graded names unmatchable
  and fail the instance outright. The setup asserts no ``t.Run`` appeared, so a
  future attempt to port more of that commit fails loudly.

  This satisfies the package's principle: the gold patch is correct, the test is
  what is broken, and the pass/fail boundary does not move — a ±2 tolerance on a
  self-derived approximation still fails a cache that refreshes the wrong number
  of times, it just stops failing one that was merely measured while the process
  was descheduled.

  Args:
    spec: The compiled spec for this instance.

  Returns:
    The spec with the tolerance widened after the golden checkout.
  """
  return with_setup(
      spec,
      mounts={},
      setup=render(_TELEPORT_FNCACHE_SETUP, TEST=_TELEPORT_FNCACHE_TEST),
  )


TELEPORT_FNCACHE = RegisteredFix(
    instances=(_TELEPORT_FNCACHE_INSTANCE,),
    fix=_fix_instance_teleport_78b0d8c7,
)
