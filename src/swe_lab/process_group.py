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
import pathlib
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
  kernel may have reissued to somebody else's group. So the wait below watches
  the group **without** reaping it, and the reap happens last.

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
  _await_empty(group, grace_s)
  with contextlib.suppress(ProcessLookupError, PermissionError):
    os.killpg(group, signal.SIGKILL)
  _reap(process, grace_s)


def _await_empty(group: int, timeout_s: float) -> None:
  """Wait until no live process remains in ``group``, without reaping any.

  Args:
    group: The process-group id.
    timeout_s: How long to wait before giving up and letting ``SIGKILL`` run.
  """
  deadline = time.monotonic() + timeout_s
  while time.monotonic() < deadline:
    live = _group_has_live_members(group)
    if live is None:
      # No ``/proc`` to read (a Mac). Sleeping out the grace period is slower
      # than polling and just as safe: what matters is that nothing here reaps.
      time.sleep(max(0.0, deadline - time.monotonic()))
      return
    if not live:
      return
    time.sleep(_POLL_INTERVAL_S)


def _group_has_live_members(group: int) -> bool | None:
  """Whether ``group`` still holds a process that is not already a zombie.

  A zombie is deliberately not "live": it has exited, and it is exactly the
  entry whose pid we want to keep reserved.

  Args:
    group: The process-group id.

  Returns:
    ``True`` / ``False``, or ``None`` where ``/proc`` is unavailable.
  """
  proc = pathlib.Path("/proc")
  if not proc.is_dir():
    return None
  for entry in proc.iterdir():
    if not entry.name.isdigit():
      continue
    try:
      stat = (entry / "stat").read_text()
    except OSError:
      continue  # exited while we looked, which is not live either
    # Fields are counted from after the comm field's closing paren, because
    # comm can itself contain spaces and parentheses.
    fields = stat[stat.rindex(")") + 2 :].split()
    state, process_group = fields[0], int(fields[2])
    if process_group == group and state != "Z":
      return True
  return False


def _reap(process: subprocess.Popen[Any], grace_s: float) -> None:
  """Reap the child, releasing its pid. Only safe once no signal is left.

  Args:
    process: The child to reap.
    grace_s: How long to wait for it.
  """
  with contextlib.suppress(subprocess.TimeoutExpired):
    _ = process.wait(timeout=grace_s)
