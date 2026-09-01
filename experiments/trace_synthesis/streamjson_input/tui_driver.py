"""Drive the **interactive TUI** in a pty, and record the same three surfaces.

The question this exists for: a supervisor's mid-turn note is only worth
training on if that context shape *occurs at inference time*. In the TUI a user
routinely types while the agent is working, and Claude Code folds that in
somehow. So — is the TUI's fold **the same wire shape** as
`-p --input-format stream-json`'s mid-turn fold, or a synthetic one that only
the headless path produces?

Comparing them needs the TUI driven like the headless arms: same task, same
correction, same proxy, an event-triggered injection moment, and a control run
with no interjection.

Keystrokes go to a pty; what comes back is raw terminal bytes, so it is logged
verbatim (`tui.log`) and never parsed for findings — **the findings come from
the proxy log and the session transcript**, exactly as in the headless arms. The
only thing the terminal stream is used for is noticing which startup dialog is
on screen.

    ./run_tui.sh control  runs/tui-control  20117
    ./run_tui.sh midturn  runs/tui-midturn  20118
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
import pathlib
import pty
import struct
import subprocess
import sys
import termios
import threading
import time
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "src"))

import driver
import transcripts
from swe_lab.process_group import end_process_group

ROWS, COLS = 50, 200

# Markers Claude Code exports into its own child processes. Inherited, they make
# the TUI behave as a *nested* session, which among other things stops it
# persisting a transcript — one of the three surfaces this run records. Stripped
# so the child is an ordinary user session.
INHERITED_MARKERS = (
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_EXECPATH",
    "CLAUDE_CODE_MESSAGING_SOCKET",
    "CLAUDE_CODE_MESSAGING_TOKEN",
    "CLAUDECODE",
    "CLAUDE_PID",
    "CLAUDE_EFFORT",
    "AI_AGENT",
)


def child_env() -> dict[str, str]:
  """Return the environment for the TUI: ours, minus the nesting markers."""
  env = {k: v for k, v in os.environ.items() if k not in INHERITED_MARKERS}
  env["TERM"] = "xterm-256color"
  return env

# Startup dialogs that eat the first keystrokes, and the keys that clear them.
# `Down` then `Enter` picks "Yes, I trust" — the trust dialog does not default
# to it.
DIALOGS = (
    ("Do you trust the files in this folder", "\x1b[B\r"),
    ("Do you trust the contents of this directory", "y"),
)


class TuiRun:
  """One `claude` TUI process on a pty, with its output logged verbatim."""

  def __init__(self, out_dir: pathlib.Path, argv: list[str], workdir: pathlib.Path):
    self.out_dir = out_dir
    self.master, slave = pty.openpty()
    _ = fcntl.ioctl(
        self.master, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0)
    )
    self.log = (out_dir / "tui.log").open("wb")
    self.buffer = bytearray()
    self.lock = threading.Lock()
    self.t0 = time.monotonic()
    self.keys: list[dict[str, object]] = []
    self.proc = subprocess.Popen(
        argv,
        cwd=workdir,
        stdin=slave,
        stdout=slave,
        stderr=slave,
        env=child_env(),
        start_new_session=True,
    )
    os.close(slave)
    self.reader = threading.Thread(target=self._drain, daemon=True)
    self.reader.start()

  def _drain(self) -> None:
    while True:
      try:
        chunk = os.read(self.master, 65536)
      except OSError:
        return
      if not chunk:
        return
      with self.lock:
        self.log.write(chunk)
        self.log.flush()
        self.buffer.extend(chunk)

  def screen(self) -> str:
    with self.lock:
      return self.buffer.decode("utf-8", "replace")

  def send(self, keys: str, note: str) -> None:
    """Write raw keystrokes to the pty and log what was sent, when."""
    self.keys.append({
        "dt": round(time.monotonic() - self.t0, 3),
        "note": note,
        "bytes": len(keys),
    })
    _ = os.write(self.master, keys.encode())

  def type_line(self, text: str, note: str, *, settle: float = 2.0) -> None:
    """Type `text`, let the input box settle, then press Enter.

    A long string arrives as a bracketed paste, and an `Enter` sent while the
    input box is still absorbing it is swallowed — hence the settle.
    """
    self.send(text, f"{note} (text)")
    time.sleep(settle)
    self.send("\r", f"{note} (enter)")

  def submit(self, text: str, note: str, proxy_path: pathlib.Path) -> bool:
    """Type a line and confirm, *on the wire*, that a turn actually started."""
    self.type_line(text, note)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
      if turn_started(proxy_path):
        return True
      time.sleep(0.5)
    return False

  def clear_dialogs(self, timeout: float = 25.0) -> str | None:
    """Answer whichever startup dialog appears; return which one it was."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
      screen = self.screen()
      for needle, keys in DIALOGS:
        if needle in screen:
          time.sleep(0.5)
          self.send(keys, f"dialog: {needle}")
          time.sleep(1.5)
          return needle
      time.sleep(0.25)
    return None

  def end(self) -> int:
    end_process_group(self.proc)
    self.reader.join(timeout=5)
    with self.lock:
      self.log.close()
    os.close(self.master)
    return self.proc.returncode if self.proc.returncode is not None else -1


def proxy_has_sleep_tool_use(proxy_path: pathlib.Path) -> bool:
  """True once a recorded response asks to run the long sleep command.

  The same event trigger the headless arms use, read from the wire instead of
  from stdout: the exchange whose response carries the `Bash` `tool_use` is
  logged the moment the model finishes it, which is exactly when the tool
  starts running.
  """
  if not proxy_path.is_file():
    return False
  for line in proxy_path.read_text().splitlines():
    if "time.sleep" in line and '"tool_use"' in line:
      return True
  return False


def _records(proxy_path: pathlib.Path) -> list[dict[str, object]]:
  if not proxy_path.is_file():
    return []
  return [
      json.loads(line)
      for line in proxy_path.read_text().splitlines()
      if line.strip()
  ]


def _agent_loop_records(proxy_path: pathlib.Path) -> list[dict[str, object]]:
  """Records that are the agent's own loop, not a side call.

  The CLI also makes small unrelated requests on this connection — a `quota`
  probe, a one-token title generation — told apart by carrying no `tools`.
  Counting them as turn activity would report a turn that is still running as
  finished.
  """
  return [r for r in _records(proxy_path) if r.get("request", {}).get("body", {}).get("tools")]


def turn_started(proxy_path: pathlib.Path) -> bool:
  """True once the agent loop has issued at least one request."""
  return bool(_agent_loop_records(proxy_path))


def turn_finished(proxy_path: pathlib.Path) -> bool:
  """True when the last agent-loop response ended a turn without a tool call."""
  records = _agent_loop_records(proxy_path)
  if not records:
    return False
  message = records[-1].get("response", {}).get("message", {})
  if not isinstance(message, dict):
    return False
  content = message.get("content", [])
  has_tool = any(
      isinstance(b, dict) and b.get("type") == "tool_use" for b in content
  )
  return message.get("stop_reason") == "end_turn" and not has_tool


def wait_for_finish(proxy_path: pathlib.Path, timeout: float) -> bool:
  """Wait for the agent loop to end a turn, then for the log to settle.

  Read from the wire rather than the terminal: what the TUI renders while a turn
  is in flight is a presentation detail and varies by build, while the agent
  loop's own requests are not.
  """
  deadline = time.monotonic() + timeout
  while time.monotonic() < deadline:
    if turn_finished(proxy_path):
      size = proxy_path.stat().st_size
      time.sleep(8)
      if turn_finished(proxy_path) and proxy_path.stat().st_size == size:
        return True
    time.sleep(1)
  return False


def main() -> int:
  parser = argparse.ArgumentParser()
  _ = parser.add_argument("scenario", choices=("control", "midturn"))
  _ = parser.add_argument("out_dir")
  _ = parser.add_argument("--model", default="sonnet")
  _ = parser.add_argument("--base-url", default=None)
  _ = parser.add_argument("--workdir", default=os.environ.get("STREAMJSON_WORKDIR"))
  args = parser.parse_args()

  out_dir = pathlib.Path(args.out_dir)
  out_dir.mkdir(parents=True, exist_ok=True)
  workdir = driver.resolve_workdir(args.workdir)
  proxy_path = out_dir / "proxy.jsonl"
  session_id = str(uuid.uuid4())

  env_note = {}
  if args.base_url:
    os.environ["ANTHROPIC_BASE_URL"] = args.base_url
    env_note["ANTHROPIC_BASE_URL"] = args.base_url

  argv = [
      "claude",
      "--model",
      args.model,
      "--session-id",
      session_id,
      "--dangerously-skip-permissions",
  ]
  meta: dict[str, object] = {
      "scenario": f"tui-{args.scenario}",
      "interface": "tui",
      "session_id": session_id,
      "argv": argv,
      "env": env_note,
      "workdir": str(workdir),
      "started_at": dt.datetime.now(dt.timezone.utc).isoformat(),
      "claude_version": subprocess.run(
          ["claude", "--version"], capture_output=True, text=True, check=False
      ).stdout.strip(),
  }

  run = TuiRun(out_dir, argv, workdir)
  timeline: list[str] = []

  def mark(note: str) -> None:
    timeline.append(f"{round(time.monotonic() - run.t0, 3)}s {note}")

  dialog = run.clear_dialogs()
  mark(f"startup dialog: {dialog}")
  time.sleep(3)
  started = run.submit(driver.TASK, "task", proxy_path)
  mark(f"typed the task, turn started: {started}")
  if not started:
    mark("ABORT: the TUI never started a turn")
    _ = run.end()
    meta["timeline"] = timeline
    meta["keystrokes"] = run.keys
    _ = (out_dir / "meta.json").write_text(json.dumps(meta, indent=2))
    print(json.dumps(meta, indent=2))
    return 1

  if args.scenario == "midturn":
    deadline = time.monotonic() + 180
    seen = False
    while time.monotonic() < deadline and not seen:
      seen = proxy_has_sleep_tool_use(proxy_path)
      time.sleep(0.5)
    mark(f"wire shows the sleep tool_use: {seen}")
    time.sleep(2)
    # Typed while the turn is running: no `wait_until_working` here, the turn is
    # already working. This is the whole point of the arm.
    run.type_line(driver.CORRECTION, "correction MID tool call")
    mark("typed the correction mid-turn")

  finished = wait_for_finish(proxy_path, timeout=240)
  mark(f"turn finished and the log settled: {finished}")
  code = run.end()

  meta["exit_code"] = code
  meta["ended_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
  meta["timeline"] = timeline
  meta["keystrokes"] = run.keys
  _ = (out_dir / "meta.json").write_text(json.dumps(meta, indent=2))
  _ = transcripts.snapshot(out_dir)
  print(json.dumps(meta, indent=2))
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
