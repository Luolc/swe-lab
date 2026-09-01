"""End a spawned command's whole process tree, not just the child we hold.

A subprocess that backgrounds something of its own — a shell that starts a
capture proxy, a proxy that starts a helper — outlives the `Popen` object we
kept, because terminating that object signals one pid. What we actually want to
end is everything the command started.

The mechanism is two halves that only work together, and both belong to the
*parent*: spawn with ``start_new_session=True``, which makes the child the
leader of its own process group, and then signal that **group**.

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
  try:
    os.killpg(group, signal.SIGTERM)
  except (ProcessLookupError, PermissionError):
    _reap(process, grace_s)  # already gone, or not ours to signal
    return
  _await_exit(process, grace_s)
  with contextlib.suppress(ProcessLookupError, PermissionError):
    os.killpg(group, signal.SIGKILL)
  _reap(process, grace_s)


def _await_exit(process: subprocess.Popen[Any], timeout_s: float) -> None:
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

  Args:
    process: The group leader.
    timeout_s: How long to wait before giving up and letting ``SIGKILL`` run.
  """
  deadline = time.monotonic() + timeout_s
  if not hasattr(os, "waitid"):
    # No non-reaping wait available: sleep out the grace period instead.
    # Slower than polling, and safe for the same reason — nothing reaps.
    time.sleep(timeout_s)
    return
  while time.monotonic() < deadline:
    try:
      exited = os.waitid(
          os.P_PID, process.pid, os.WEXITED | os.WNOWAIT | os.WNOHANG
      )
    except ChildProcessError:
      return  # somebody else already reaped it; nothing left to wait for
    if exited is not None:
      return
    time.sleep(_POLL_INTERVAL_S)


def _reap(process: subprocess.Popen[Any], grace_s: float) -> None:
  """Reap the child, releasing its pid. Only safe once no signal is left.

  Args:
    process: The child to reap.
    grace_s: How long to wait for it.
  """
  with contextlib.suppress(subprocess.TimeoutExpired):
    _ = process.wait(timeout=grace_s)
