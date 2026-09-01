"""Drive `claude -p --input-format stream-json` and log everything, timestamped.

One process per run. Messages are sent on stdin as stream-json `user` events at
*event-triggered* moments (not fixed sleeps), so "mid-turn" means "after a
tool_use block was actually seen on stdout", not "after 3 seconds".

Every stdout line is written to `events.jsonl` wrapped with a wall-clock stamp
and a monotonic offset:

    {"t": "<iso8601>", "dt": <seconds since launch>, "dir": "out", "event": {...}}

and every stdin message is logged the same way with `"dir": "in"`.

The `claude` process is spawned as its own **session leader** and ended through
`swe_lab.process_group.end_process_group`, so the tool processes it started (a
30 s sleep, a background bash task) die with it. A scenario that deliberately
interrupts the agent must not leave its children running into the next run.

Usage:
  python driver.py <scenario> <out-dir> [--workdir DIR] [--model sonnet]
                   [--base-url URL] [--max-turns N] [--provenance …]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import subprocess
import sys
import threading
import time
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "src"))

import transcripts  # noqa: E402
from swe_lab.process_group import end_process_group  # noqa: E402

# The agent's working directory. Self-contained by default — a `workdir/` beside
# this file, created with the one file the task reads — so the documented
# command works in a fresh checkout. `--workdir` / `STREAMJSON_WORKDIR` override
# it, and an override that does not exist is an error rather than a surprise.
DEFAULT_WORKDIR = pathlib.Path(__file__).resolve().parent / "workdir"
NOTES_NAME = "notes.txt"
NOTES_TEXT = "The secret color is teal.\n"


def resolve_workdir(explicit: str | None) -> pathlib.Path:
  """Return the agent's cwd, creating the default fixture when it is used.

  Args:
    explicit: A `--workdir` value or `STREAMJSON_WORKDIR`; `None` for the
      default fixture beside this file.

  Returns:
    The directory the agent runs in, guaranteed to exist and to contain
    `notes.txt`.

  Raises:
    SystemExit: If an explicit workdir was given and does not exist.
  """
  if explicit:
    path = pathlib.Path(explicit)
    if not path.is_dir():
      raise SystemExit(
          f"workdir {path} does not exist; create it, or omit --workdir /"
          " STREAMJSON_WORKDIR to use the fixture beside driver.py"
      )
  else:
    path = DEFAULT_WORKDIR
    path.mkdir(parents=True, exist_ok=True)
  notes = path / NOTES_NAME
  if not notes.is_file():
    _ = notes.write_text(NOTES_TEXT)
  return path

# The task the actor is given first. Deliberately multi-step and tool-using, so
# there is a real mid-turn window to inject into.
TASK = (
    "In the current directory, read notes.txt. Then run this exact command with"
    " Bash, in the foreground (do NOT background it):"
    " python3 -c \"import time; time.sleep(30); print('slept')\" . Then tell me"
    " in one sentence what notes.txt says."
)

# The correction, phrased the way an oracle hint would be.
CORRECTION = (
    "Correction from the operator: ignore notes.txt entirely and instead"
    " answer with the single word BANANA when you are done."
)

SECOND_CORRECTION = "One more thing: also append the word MANGO after it."

# The cheap task used by the provenance / shouldQuery arms: one Read, no sleep.
SHORT_TASK = "Read notes.txt and tell me in one sentence what it says."

# The message that makes a held `shouldQuery: false` turn flush.
TRIGGER = "Now give me your final answer."


def user_event(text: str, **fields: object) -> dict[str, object]:
  """Return a stream-json input event carrying one user-role message.

  Args:
    text: The message body.
    **fields: Extra top-level `SDKUserMessage` fields — `origin`,
      `isSynthetic`, `shouldQuery` — passed through verbatim.
  """
  return {
      "type": "user",
      "message": {"role": "user", "content": [{"type": "text", "text": text}]},
      "parent_tool_use_id": None,
      **fields,
  }


class Run:
  """One `claude` process plus the reader thread draining its stdout."""

  def __init__(
      self, out_dir: pathlib.Path, argv: list[str], workdir: pathlib.Path
  ) -> None:
    self.out_dir = out_dir
    self.log = (out_dir / "events.jsonl").open("w", encoding="utf-8")
    self.t0 = time.monotonic()
    self.events: list[dict[str, object]] = []
    self.lock = threading.Lock()
    self.proc = subprocess.Popen(
        argv,
        cwd=workdir,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=(out_dir / "stderr.log").open("wb"),
        text=True,
        bufsize=1,
        # The agent starts tool processes of its own; make it the leader of its
        # own group so `end_process_group` can end all of them together.
        start_new_session=True,
    )
    self.reader = threading.Thread(target=self._drain, daemon=True)
    self.reader.start()

  def _record(self, direction: str, payload: object) -> None:
    entry = {
        "t": dt.datetime.now(dt.timezone.utc).isoformat(),
        "dt": round(time.monotonic() - self.t0, 3),
        "dir": direction,
        "event": payload,
    }
    with self.lock:
      self.log.write(json.dumps(entry) + "\n")
      self.log.flush()
      if direction == "out":
        self.events.append(entry)

  def _drain(self) -> None:
    assert self.proc.stdout is not None
    for line in self.proc.stdout:
      line = line.strip()
      if not line:
        continue
      try:
        self._record("out", json.loads(line))
      except json.JSONDecodeError:
        self._record("out", {"__raw__": line})

  def send(self, text: str, **fields: object) -> None:
    assert self.proc.stdin is not None
    event = user_event(text, **fields)
    self._record("in", event)
    self.proc.stdin.write(json.dumps(event) + "\n")
    self.proc.stdin.flush()

  def send_control(self, request: dict[str, object], request_id: str) -> None:
    """Write one SDK `control_request` line (the interrupt path, not a turn)."""
    assert self.proc.stdin is not None
    event = {
        "type": "control_request",
        "request_id": request_id,
        "request": request,
    }
    self._record("in", event)
    self.proc.stdin.write(json.dumps(event) + "\n")
    self.proc.stdin.flush()

  def close_stdin(self) -> None:
    assert self.proc.stdin is not None
    self._record("in", {"__closed_stdin__": True})
    self.proc.stdin.close()

  def wait_for(self, predicate, timeout: float) -> dict[str, object] | None:
    """Block until an already-seen or newly-arriving event matches."""
    deadline = time.monotonic() + timeout
    seen = 0
    while time.monotonic() < deadline:
      with self.lock:
        batch = self.events[seen:]
        seen = len(self.events)
      for entry in batch:
        if predicate(entry["event"]):
          return entry
      if self.proc.poll() is not None and seen == len(self.events):
        return None
      time.sleep(0.05)
    return None

  def finish(self, timeout: float = 300.0) -> int:
    """Wait for the agent to exit, ending its whole process group either way."""
    try:
      code = self.proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
      end_process_group(self.proc)
      code = self.proc.returncode if self.proc.returncode is not None else -1
    finally:
      self.reader.join(timeout=10)
      self.log.close()
    return code

  def end(self) -> int:
    """Kill the agent *and every process it started*, then drain the logs."""
    end_process_group(self.proc)
    self.reader.join(timeout=10)
    self.log.close()
    return self.proc.returncode if self.proc.returncode is not None else -1


def is_result(event: object) -> bool:
  return isinstance(event, dict) and event.get("type") == "result"


def is_control_response(event: object) -> bool:
  return isinstance(event, dict) and event.get("type") == "control_response"


def has_tool_use(event: object) -> bool:
  if not isinstance(event, dict) or event.get("type") != "assistant":
    return False
  message = event.get("message")
  if not isinstance(message, dict):
    return False
  content = message.get("content")
  if not isinstance(content, list):
    return False
  return any(
      isinstance(b, dict) and b.get("type") == "tool_use" for b in content
  )


def is_bash_sleep(event: object) -> bool:
  """True for the assistant event that launches the long Bash sleep."""
  if not has_tool_use(event):
    return False
  for block in event["message"]["content"]:  # type: ignore[index]
    if not isinstance(block, dict) or block.get("type") != "tool_use":
      continue
    if block.get("name") != "Bash":
      continue
    command = str((block.get("input") or {}).get("command", ""))
    if "time.sleep" in command:
      return True
  return False


def main() -> int:
  parser = argparse.ArgumentParser()
  _ = parser.add_argument("scenario")
  _ = parser.add_argument("out_dir")
  _ = parser.add_argument("--model", default="sonnet")
  _ = parser.add_argument(
      "--workdir", default=os.environ.get("STREAMJSON_WORKDIR")
  )
  _ = parser.add_argument("--base-url", default=None)
  _ = parser.add_argument("--replay-user-messages", action="store_true")
  _ = parser.add_argument("--max-turns", type=int, default=None)
  _ = parser.add_argument(
      "--provenance",
      choices=("none", "human", "synthetic"),
      default="none",
      help="provenance fields put on the *correction* message",
  )
  args = parser.parse_args()

  out_dir = pathlib.Path(args.out_dir)
  out_dir.mkdir(parents=True, exist_ok=True)
  workdir = resolve_workdir(args.workdir)
  session_id = str(uuid.uuid4())

  argv = [
      "claude",
      "-p",
      "--input-format",
      "stream-json",
      "--output-format",
      "stream-json",
      "--verbose",
      "--model",
      args.model,
      "--session-id",
      session_id,
      "--dangerously-skip-permissions",
  ]
  if args.replay_user_messages:
    argv.append("--replay-user-messages")
  if args.max_turns is not None:
    argv += ["--max-turns", str(args.max_turns)]

  env_note = {}
  if args.base_url:
    os.environ["ANTHROPIC_BASE_URL"] = args.base_url
    env_note["ANTHROPIC_BASE_URL"] = args.base_url

  meta = {
      "scenario": args.scenario,
      "session_id": session_id,
      "argv": argv,
      "env": env_note,
      "workdir": str(workdir),
      "started_at": dt.datetime.now(dt.timezone.utc).isoformat(),
      "claude_version": subprocess.run(
          ["claude", "--version"], capture_output=True, text=True, check=False
      ).stdout.strip(),
  }

  provenance: dict[str, object] = {}
  if args.provenance == "human":
    provenance = {"origin": {"kind": "human"}}
  elif args.provenance == "synthetic":
    provenance = {"isSynthetic": True}
  meta["provenance"] = {"variant": args.provenance, "fields": provenance}

  run = Run(out_dir, argv, workdir)
  timeline: list[str] = []

  def mark(note: str) -> None:
    timeline.append(f"{round(time.monotonic() - run.t0, 3)}s {note}")

  if args.scenario == "control":
    run.send(TASK)
    mark("sent task")
    _ = run.wait_for(is_result, timeout=240)
    mark("saw result")
    run.close_stdin()

  elif args.scenario == "boundary":
    run.send(TASK)
    mark("sent task")
    _ = run.wait_for(is_result, timeout=240)
    mark("saw result 1")
    run.send(CORRECTION)
    mark("sent correction at turn boundary")
    _ = run.wait_for(
        lambda e: is_result(e)
        and sum(1 for x in run.events if is_result(x["event"])) >= 2,
        timeout=240,
    )
    mark("saw result 2")
    run.send(SECOND_CORRECTION)
    mark("sent second correction (persistence probe)")
    _ = run.wait_for(
        lambda e: is_result(e)
        and sum(1 for x in run.events if is_result(x["event"])) >= 3,
        timeout=240,
    )
    mark("saw result 3")
    run.close_stdin()

  elif args.scenario == "midturn":
    run.send(TASK)
    mark("sent task")
    hit = run.wait_for(is_bash_sleep, timeout=180)
    mark(f"saw sleep tool_use: {hit is not None}")
    time.sleep(2)
    run.send(CORRECTION)
    mark("sent correction MID tool call")
    _ = run.wait_for(is_result, timeout=300)
    mark("saw result 1")
    _ = run.wait_for(
        lambda e: is_result(e)
        and sum(1 for x in run.events if is_result(x["event"])) >= 2,
        timeout=120,
    )
    mark("saw result 2 (or timed out)")
    run.close_stdin()

  elif args.scenario == "accept":
    # Provenance arm: one cheap turn, then the correction with whatever
    # provenance fields this variant carries.
    run.send(SHORT_TASK)
    mark("sent short task")
    _ = run.wait_for(is_result, timeout=180)
    mark("saw result 1")
    run.send(CORRECTION, **provenance)
    mark(f"sent correction provenance={args.provenance}")
    _ = run.wait_for(
        lambda e: is_result(e)
        and sum(1 for x in run.events if is_result(x["event"])) >= 2,
        timeout=180,
    )
    mark("saw result 2")
    run.close_stdin()

  elif args.scenario == "shouldquery":
    # `shouldQuery: false` arm: does the correction land WITHOUT an assistant
    # turn, and is it visible on the next turn that does fire?
    run.send(SHORT_TASK)
    mark("sent short task")
    _ = run.wait_for(is_result, timeout=180)
    mark("saw result 1")
    run.send(CORRECTION, shouldQuery=False)
    mark("sent correction with shouldQuery=false")
    extra = run.wait_for(
        lambda e: is_result(e)
        and sum(1 for x in run.events if is_result(x["event"])) >= 2,
        timeout=25,
    )
    mark(f"second result within 25s: {extra is not None}")
    run.send(TRIGGER)
    mark("sent trigger message")
    _ = run.wait_for(
        lambda e: is_result(e)
        and sum(1 for x in run.events if is_result(x["event"])) >= 2,
        timeout=180,
    )
    mark("saw a result after trigger")
    run.close_stdin()

  elif args.scenario == "interrupt":
    # Is there a *fine-grained* clean injection point? Cut the in-flight turn
    # with a control_request, then inject a user line at that seam.
    run.send(TASK)
    mark("sent task")
    hit = run.wait_for(is_bash_sleep, timeout=180)
    mark(f"saw sleep tool_use: {hit is not None}")
    time.sleep(2)
    run.send_control({"subtype": "interrupt"}, "interrupt-1")
    mark("sent control_request interrupt")
    stopped = run.wait_for(
        lambda e: is_result(e) or is_control_response(e), timeout=90
    )
    mark(f"saw result-or-control-response after interrupt: {stopped is not None}")
    time.sleep(2)
    run.send(CORRECTION)
    mark("sent correction after the interrupt")
    _ = run.wait_for(
        lambda e: is_result(e)
        and sum(1 for x in run.events if is_result(x["event"])) >= 2,
        timeout=180,
    )
    mark("saw a second result")
    run.close_stdin()

  elif args.scenario == "maxturns":
    # Can --max-turns chop the run into segments whose seams are clean
    # boundaries, in one session?
    run.send(TASK)
    mark("sent task")
    _ = run.wait_for(is_result, timeout=180)
    mark("saw result 1")
    run.send(CORRECTION)
    mark("sent correction after the turn limit ended segment 1")
    _ = run.wait_for(
        lambda e: is_result(e)
        and sum(1 for x in run.events if is_result(x["event"])) >= 2,
        timeout=180,
    )
    mark("saw result 2")
    run.send(TRIGGER)
    mark("sent a third message")
    _ = run.wait_for(
        lambda e: is_result(e)
        and sum(1 for x in run.events if is_result(x["event"])) >= 3,
        timeout=180,
    )
    mark("saw result 3")
    run.close_stdin()

  else:
    raise SystemExit(f"unknown scenario {args.scenario}")

  code = run.finish()
  meta["exit_code"] = code
  meta["ended_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
  meta["timeline"] = timeline
  _ = (out_dir / "meta.json").write_text(json.dumps(meta, indent=2))
  # After meta.json, which is where the session id the snapshot looks up lives.
  _ = transcripts.snapshot(out_dir)
  print(json.dumps(meta, indent=2))
  return 0


if __name__ == "__main__":
  sys.exit(main())
