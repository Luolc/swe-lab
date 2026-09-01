"""End a spawned command's whole process tree, not just the child we hold.

A subprocess that backgrounds something of its own — a shell that starts a
capture proxy, a proxy that starts a helper — outlives the `Popen` object we
kept, because terminating that object signals one pid. What we actually want to
end is everything the command started.

The mechanism is two halves that only work together, and both belong to the
*parent*: spawn with ``start_new_session=True``, which makes the child the
leader of its own process group, and then signal that **group**.

**The invariant that makes the group id safe to signal**, measured on Linux
6.17 / CPython 3.13 (2026-09-01) and stated here because the code cannot say
it. A group id is a *number*, and it stops naming this tree once nothing
references it:

===============================  ======================  ====================
state                            ``killpg(pgid, 0)``     what still holds it
===============================  ======================  ====================
leader exited, not reaped        ok                      the zombie leader
leader reaped, no member left    ``ProcessLookupError``  nothing — it is free
leader reaped, grandchild alive  ok                      **a grandchild**
===============================  ======================  ====================

The third row is the counter-intuitive one and it needs stating precisely,
because the loose version of it is wrong. The kernel backs a pid and a group id
with the same ``struct pid``, so a surviving ``PIDTYPE_PGID`` member keeps that
number allocated — which is *why* the signal still resolves there, and it does
**not** mean the reaped leader's number has been handed out again. What is lost
in that row is not correctness but *control*: the guarantee has moved from the
child this code holds to a member it does not track, and it evaporates the
moment that member exits, unobserved.

So the operative rule is the one this code can actually keep:
**the leader is not reaped until the last group-directed signal is complete** —
and, correspondingly, **no group-directed signal is sent once the leader is no
longer held**, because then nothing here can say what that number names.

**This covers the deaths the parent is alive for** — a normal exit, an
exception, a timeout. It does not cover the parent being killed: the signal
below is something the parent sends, so a parent that is gone sends nothing
(see the process half of
``docs/horizontal/plans/task-35-lifecycle-ownership.md``, part D, for what does
cover that).
"""

from __future__ import annotations

import contextlib
import logging
import os
import signal
import subprocess
import time
from typing import Any

_logger = logging.getLogger(__name__)

# How long the group gets to honour SIGTERM — enough for a shell to run its
# EXIT traps — before SIGKILL.
TERMINATE_GRACE_S = 5.0

# How often the group is checked while it drains. Short: this runs on a
# teardown path a caller is waiting on.
_POLL_INTERVAL_S = 0.05


def end_process_group(
    process: subprocess.Popen[Any], *, grace_s: float = TERMINATE_GRACE_S
) -> None:
  """End ``process``'s entire process group; never raises.

  ``SIGTERM`` first, so a shell's own ``EXIT`` traps get to run, then
  ``SIGKILL``: waiting on the direct child only proves *it* is gone, and it is
  the grandchildren this exists for.

  **The child is not reaped until every group-directed signal is done**, and
  that ordering is the whole safety argument rather than a detail. A group is
  addressed by a *number* — its leader's pid — and an unreaped child is what
  keeps that number reserved. Call ``wait()`` or ``poll()`` in between and the
  reservation is released, so the final ``SIGKILL`` resolves a number the
  kernel may have reissued to somebody else's group. So the wait below reports
  the exit **without** reaping it, and the reap happens last.

  Args:
    process: The spawned child, which ``start_new_session=True`` made the
      leader of its group (so its pid is the group id). Spawning it without
      that flag makes this signal the *caller's* group — check the call site
      before reusing this.
    grace_s: Seconds the group gets to honour ``SIGTERM`` before ``SIGKILL``,
      and then again to be reaped.
  """
  group = process.pid  # start_new_session ⇒ the child leads its own group
  if not _identity_held(process):
    # Checked before the *first* signal and not only before the last: if the
    # leader was already reaped when this was called, the number stopped
    # naming that tree before we touched it, and `SIGTERM` is then the unsafe
    # act.
    _logger.warning(
        "process %d is no longer held; not signalling group %d",
        process.pid,
        group,
    )
    return
  try:
    os.killpg(group, signal.SIGTERM)
  except (ProcessLookupError, PermissionError):
    _reap(process, grace_s)  # already gone, or not ours to signal
    return
  if not _await_exit(process, grace_s):
    # Somebody else reaped the leader, so this parent no longer holds the
    # reservation and cannot say what `group` names now. Ownership unknown is
    # not permission: report it and signal nothing further.
    _logger.warning(
        "process %d was reaped elsewhere; not signalling group %d again",
        process.pid,
        group,
    )
    return
  with contextlib.suppress(ProcessLookupError, PermissionError):
    os.killpg(group, signal.SIGKILL)
  _reap(process, grace_s)


def _identity_held(process: subprocess.Popen[Any]) -> bool:
  """Whether this parent still holds the leader, unreaped.

  Two ways it may not: this parent reaped it itself (``returncode`` is set), or
  something else did (``waitid`` reports no such child). Either way the pid —
  and with it the group id — is no longer ours to name, and the answer to that
  is to signal nothing.

  Args:
    process: The group leader.

  Returns:
    Whether a group-directed signal may still be sent.
  """
  if process.returncode is not None:
    return False
  if not hasattr(os, "waitid"):
    return True  # no way to ask, and this parent has not reaped it
  try:
    _ = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOWAIT | os.WNOHANG)
  except ChildProcessError:
    return False
  return True


def _await_exit(process: subprocess.Popen[Any], timeout_s: float) -> bool:
  """Wait for the leader to exit **without reaping it**.

  ``waitid(WNOWAIT)`` is the primitive for exactly this: it reports the exit
  and leaves the zombie in place, so the pid — and therefore the group id —
  stays reserved for the signal that follows.

  Measured on this repo's platform (Linux 6.17, CPython 3.13, 2026-09-01)
  rather than assumed, because the whole ordering rests on it: with the leader
  exited and unreaped, ``killpg(pgid, 0)`` succeeds; after ``wait()`` reaps it
  and no other member survives, the same call raises ``ProcessLookupError`` —
  the group number is free, and a later ``killpg`` on it is a lookup of a name
  that may since have been reissued.

  ``WNOHANG`` returns immediately, so this is necessarily a polling loop. Two
  of its exits mean "still ours" — the exit was observed, or the deadline
  passed with the child still unreaped — and both lead to the final
  ``SIGKILL``. The third does not: if the child has already been reaped by
  something else, the reservation is gone with it.

  Args:
    process: The group leader.
    timeout_s: How long to wait before giving up and letting ``SIGKILL`` run.

  Returns:
    Whether this parent still holds the unreaped leader, and may therefore
    still send a signal to its group id.
  """
  deadline = time.monotonic() + timeout_s
  if not hasattr(os, "waitid"):
    # No non-reaping wait available: sleep out the grace period instead.
    # Slower than polling, and safe for the same reason — nothing reaps.
    time.sleep(timeout_s)
    return True
  while time.monotonic() < deadline:
    try:
      exited = os.waitid(
          os.P_PID, process.pid, os.WEXITED | os.WNOWAIT | os.WNOHANG
      )
    except ChildProcessError:
      return False  # reaped elsewhere: the identity is no longer ours to use
    if exited is not None:
      return True
    time.sleep(_POLL_INTERVAL_S)
  return True


def _reap(process: subprocess.Popen[Any], grace_s: float) -> None:
  """Reap the child, releasing its pid. Only safe once no signal is left.

  Args:
    process: The child to reap.
    grace_s: How long to wait for it.
  """
  with contextlib.suppress(subprocess.TimeoutExpired):
    _ = process.wait(timeout=grace_s)
