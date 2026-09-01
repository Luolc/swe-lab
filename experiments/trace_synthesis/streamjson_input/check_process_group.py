"""Exercise the driver's cleanup path against a local child that outlives kill.

The scenarios here deliberately interrupt an agent while a 30 s tool process is
running, so the runner has to end the *group*, not the pid it holds. This check
stands in a shell that backgrounds a long `sleep` for the agent, ends it through
`Run.end()`, and asserts the grandchild is gone. It costs nothing and needs no
API access.

    python check_process_group.py
"""

from __future__ import annotations

import os
import pathlib
import signal
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import driver


def alive(pid: int) -> bool:
  """True if `pid` still exists (any state, including zombie)."""
  try:
    os.kill(pid, 0)
  except ProcessLookupError:
    return False
  except PermissionError:
    return True
  return True


def main() -> int:
  with tempfile.TemporaryDirectory() as tmp:
    out_dir = pathlib.Path(tmp) / "run"
    out_dir.mkdir()
    workdir = pathlib.Path(tmp) / "wd"
    workdir.mkdir()
    # Stands in for `claude`: a shell that backgrounds a long-lived child (the
    # agent's tool process) and prints its pid, then waits.
    script = 'sleep 300 & echo "pid=$!"; wait'
    run = driver.Run(out_dir, ["bash", "-c", script], workdir)

    grandchild = None
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and grandchild is None:
      for entry in run.events:
        event = entry["event"]
        raw = event.get("__raw__", "") if isinstance(event, dict) else ""
        if isinstance(raw, str) and raw.startswith("pid="):
          grandchild = int(raw.removeprefix("pid=").strip())
          break
      time.sleep(0.05)

    if grandchild is None:
      print("FAIL: the stand-in never reported its child pid")
      _ = run.end()
      return 1
    print(f"grandchild pid {grandchild}, alive={alive(grandchild)}")

    parent = run.proc.pid
    _ = run.end()
    time.sleep(0.5)

    ok = True
    if alive(grandchild):
      print(f"FAIL: grandchild {grandchild} survived Run.end()")
      os.kill(grandchild, signal.SIGKILL)
      ok = False
    else:
      print(f"ok: grandchild {grandchild} was ended with the group")
    if run.proc.returncode is None:
      print(f"FAIL: parent {parent} was not reaped")
      ok = False
    else:
      print(f"ok: parent {parent} reaped, returncode={run.proc.returncode}")
    if run.log.closed:
      print("ok: event log closed")
    else:
      print("FAIL: event log left open")
      ok = False
    return 0 if ok else 1


if __name__ == "__main__":
  raise SystemExit(main())
