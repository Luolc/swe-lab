"""tutanota f373ac38: a graded suite that fails for 3 hours out of every 24."""

from __future__ import annotations

from etils import epath

from swe_lab.sandbox import Inline, Mount

from .._seam import (
    RegisteredFix,
    render,
    SweBenchProUnitTestSpec,
    with_setup,
)

_TUTANOTA_CLOCK_INSTANCE = (
    "instance_tutao__tutanota-f373ac3808deefce8183dad8d16729839cc330c1"
    "-v2939aa9f4356f0dc9f523ee5ce19d09e08ab979b"
)
_CLOCK_TEST = "test/tests/calendar/eventeditor/CalendarEventWhenModelTest.ts"
# The line that reads the clock. Its presence is what makes this fix necessary;
# if the test is ever rewritten, the fix must be re-derived rather than kept
# running against a file it no longer describes.
_CLOCK_MARKER = "const now = new Date()"

_SHIM_NAME = "tutanota_clock_shim.cjs"
# `NODE_OPTIONS` is split on whitespace, so the preload cannot be read from the
# workspace path (which is the caller's and may contain spaces).
_SHIM_PATH = "/tmp/swe-lab-tutanota-clock.cjs"

_TUTANOTA_CLOCK_SETUP = """
if ! grep -qF '@MARKER@' '@TEST@'; then
  echo "the clock-dependent default-times test is not in @TEST@" >&2
  exit 1
fi
cp "$SANDBOX_WORKSPACE"/@SHIM@ @SHIM_PATH@
export NODE_OPTIONS="--require @SHIM_PATH@${NODE_OPTIONS:+ $NODE_OPTIONS}"
# Assert the *shape* of what the preload produced, not that it was loaded: the
# clock is pinned, an explicit instant still passes through, and the two ways
# of reading the clock agree.
probe=$(node -e 'process.stdout.write([new Date().getUTCHours(),
  new Date(0).getTime(),
  Math.abs(Date.now() - new Date().getTime()) < 1000].join(" "))')
if [ "$probe" != "12 0 true" ]; then
  echo "clock shim did not take effect (probe: $probe)" >&2
  exit 1
fi
"""


def clock_shim() -> bytes:
  """Read the Node preload that ships beside this module."""
  shim = epath.resource_path(__name__) / "clock_shim.cjs"
  return shim.read_bytes()


def _fix_instance_tutanota_f373ac38(
    spec: SweBenchProUnitTestSpec,
) -> SweBenchProUnitTestSpec:
  """Pin the suite's wall clock, so its outcome stops depending on the hour.

  ``CalendarEventWhenModelTest.ts`` asserts that turning off a calendar event's
  all-day flag applies the model's default times. It builds the expectation from
  the process clock and the model resolves it in a **hardcoded**
  ``Europe/Berlin``, and the two agree only while they land on the same calendar
  day::

      const now = new Date()
      const eventWithDefaults = getEventWithDefaultTimes()   // next half hour
      const model = getModelBerlin({
        startTime: DateTime.fromJSDate(now, { zone: "utc" })
                           .set({ hour: 0, ... }),
        ...

  The model is handed an all-day event on the **UTC** date of ``now``, converts
  it to a Berlin day, and then places the default *time of day* — read back in
  Berlin — on that day. So the assertion holds exactly while the Berlin day of
  the next half hour is the UTC day of ``now``, and it breaks two ways:

  - **the start date rolls.** From 22:00 UTC the Berlin day is already
    tomorrow, so the model puts the default time on yesterday's date and the
    result is off by 24 hours;
  - **the end date straddles midnight.** From 21:00 UTC the next half hour is
    Berlin 23:30, so the default *end* (start + 30 min) falls on the following
    day — but an all-day event ends on the day it starts, so the model has no
    date to put it on and returns an end 24 hours early.

  Together that is **21:00–23:59 UTC, three hours of every twenty-four**, and a
  minute-by-minute sweep of a whole day against this instance's own container
  confirms exactly that window and no other. The two modes are separately
  visible in the measurements that started this: a 64-unit batch begun at 20:23
  UTC lost only its later units and only on the *end* assertion — which is
  precisely the 21:00–21:30 sub-window where the start date still agrees — while
  a batch begun at 21:59 lost all 64 on both.

  **Why this is fixed in the environment and not in the test.** The obvious
  environment lever — the container's ``TZ`` — does nothing: the test names
  ``Europe/Berlin`` explicitly and ``getNextHalfHour``'s rounding is
  offset-independent for whole-hour zones, so neither side of the comparison
  moves. And no faithful edit to the test can remove the second failure mode: an
  all-day event that becomes a timed event straddling midnight has nowhere to
  put its end, which is a real limitation of ``CalendarEventWhenModel`` that the
  test genuinely catches. Papering over it would be exactly the "widening what
  passes" this package forbids. What is illegitimate is not the assertion, it is
  that a graded suite reads the wall clock at all — so the clock is what gets
  fixed, and every assertion is left as written.

  **Upstream agrees the test, not the model, is the problem.** They commented
  this exact test out in tutao/tutanota@7949003 (2025-05-18), whose message is
  simply *"disable failing test case"*, and today's tree skips the whole spec
  unless the environment's zone is ``Europe/Berlin``. Neither remedy is
  available here: this instance is graded on a **count** of passing tests (its
  parser reads ``passing: N`` and emits ``test_1..test_N`` placeholders), so a
  test that stops running is indistinguishable from a test that fails.

  This satisfies the package's principle. The gold patch is correct — it works
  the mail and entity-cache path (``MailFacade``, ``DefaultEntityRestCache``,
  ``EntityRestClient``, ``MailIndexer``, ``InboxRuleHandler``) and touches
  nothing under ``src/calendar`` — and the broken thing is a test whose result
  is a function of what time the run happened to start.
  The pass/fail boundary does not move: for 21 of every 24 hours the suite
  already produces exactly this result, so the fix makes the common outcome the
  only outcome rather than choosing a new one.

  Args:
    spec: The compiled spec for this instance.

  Returns:
    The spec with the clock preload staged and exported for the test run.
  """
  return with_setup(
      spec,
      mounts={_SHIM_NAME: Mount(Inline(clock_shim()))},
      setup=render(
          _TUTANOTA_CLOCK_SETUP,
          TEST=_CLOCK_TEST,
          MARKER=_CLOCK_MARKER,
          SHIM=_SHIM_NAME,
          SHIM_PATH=_SHIM_PATH,
      ),
  )


TUTANOTA_CLOCK = RegisteredFix(
    instances=(_TUTANOTA_CLOCK_INSTANCE,),
    fix=_fix_instance_tutanota_f373ac38,
)
