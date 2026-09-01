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
from typing import Any

_logger = logging.getLogger(__name__)

# How long the group gets to honour SIGTERM — enough for a shell to run its
# EXIT traps — before SIGKILL.
TERMINATE_GRACE_S = 5.0


def end_process_group(
    process: subprocess.Popen[Any], *, grace_s: float = TERMINATE_GRACE_S
) -> None:
  """End ``process``'s entire process group; never raises.

  ``SIGTERM`` first, so a shell's own ``EXIT`` traps get to run, then
  ``SIGKILL`` unconditionally: waiting on ``process`` only proves the direct
  child is gone, and it is the *grandchildren* this exists for.

  Signalling the group by number is safe here for a reason worth naming: the
  group id is the leader's pid, and an unreaped child keeps its pid reserved,
  so the ``Popen`` we still hold is what stops that number from coming to mean
  somebody else's process. Reap the child first and the guarantee is gone.

  Args:
    process: The spawned child, which ``start_new_session=True`` made the
      leader of its group (so its pid is the group id). Spawning it without
      that flag makes this signal the *caller's* group — check the call site
      before reusing this.
    grace_s: Seconds to wait after ``SIGTERM`` before ``SIGKILL``.
  """
  group = process.pid  # start_new_session ⇒ the child leads its own group
  for sig in (signal.SIGTERM, signal.SIGKILL):
    try:
      os.killpg(group, sig)
    except (ProcessLookupError, PermissionError):
      return  # already gone, or not ours to signal
    if sig is signal.SIGTERM:
      # Waited on, but not returned on: the direct child exiting says nothing
      # about the grandchildren this exists for, so SIGKILL follows regardless
      # (and finds an empty group harmlessly, above).
      with contextlib.suppress(subprocess.TimeoutExpired):
        _ = process.wait(timeout=grace_s)
  with contextlib.suppress(subprocess.TimeoutExpired):
    _ = process.wait(timeout=grace_s)
