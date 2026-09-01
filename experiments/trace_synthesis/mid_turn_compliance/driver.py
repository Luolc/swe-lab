"""Run one fixture in one arm, and record the wire.

The protocol this implements is `PREREGISTRATION.md`; nothing here decides
anything the criterion later reads. The driver's only judgement call is *when*
the trigger fired, and even that is recomputed independently by
`criterion.trigger_index` from the proxy log — this process watches stdout only
because a streamed `assistant` event arrives while the turn is still running,
and a proxy record does not. Mid-turn injection needs the earlier of the two.

Per arm (`PREREGISTRATION.md` §4.4):

- `mid` — on the trigger, the correction goes to stdin **immediately**, while
  the turn is still in flight, and Claude Code folds it into the running turn.
- `pos` — the correction is part of the **initial prompt**, so the actor has
  every opportunity to act on it. This is the instrument's self-check and
  nothing else: under `-p` the whole task is one turn, so a "turn boundary"
  arrives only after the work is done, and an actor answering "I already did
  that" is not a refusal — it is an actor with nothing left to do.
- `neg` — nothing is sent; the run is otherwise identical.

The fixture repository is materialized fresh for every run, so no run can see
another's edits.

    ./run_one.sh mid run_tests_first runs/mid/run_tests_first 20301
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import shutil
import subprocess
import sys
import threading
import time
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3] / "src"))

import tasks  # noqa: E402
from swe_lab.process_group import end_process_group  # noqa: E402

# The wrapper, verbatim from §4.3. The tag makes the hint identifiable in the
# trace; the body claims nothing about who wrote it; no `origin` field is set,
# so the message is unattributed.
WRAPPER = "<supervisor_note>\n{correction}\n</supervisor_note>"

RUN_TIMEOUT_S = 420.0

# The fixture repository is created OUTSIDE this checkout. Inside it, the actor
# treats the surrounding repo as part of its workspace — the pilot caught runs
# whose first action grepped the parent tree — and everything the experiment
# knows, `tasks.py` included, is then readable by the actor being measured.
WORKDIR_ROOT = pathlib.Path(
    os.environ.get("MID_TURN_WORKDIR_ROOT", "/tmp/mid-turn-compliance-workdirs")
)


def materialize(fixture: tasks.Fixture, workdir: pathlib.Path) -> None:
  """Write the fixture's repository into an empty directory."""
  if workdir.exists():
    shutil.rmtree(workdir)
  workdir.mkdir(parents=True)
  for name, body in fixture.files.items():
    path = workdir / name
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(body)


def user_event(text: str) -> dict[str, object]:
  """Return the stream-json input event for one unattributed user message."""
  return {
      "type": "user",
      "message": {"role": "user", "content": [{"type": "text", "text": text}]},
      "parent_tool_use_id": None,
  }


def tool_uses(event: object) -> list[dict[str, object]]:
  """Return the `tool_use` blocks of a streamed assistant event, if any."""
  if not isinstance(event, dict) or event.get("type") != "assistant":
    return []
  message = event.get("message")
  if not isinstance(message, dict):
    return []
  content = message.get("content")
  if not isinstance(content, list):
    return []
  return [
      block
      for block in content
      if isinstance(block, dict) and block.get("type") == "tool_use"
  ]


class Run:
  """One `claude` process, its stdout log, and the stdin the driver holds."""

  def __init__(
      self, out_dir: pathlib.Path, argv: list[str], workdir: pathlib.Path
  ) -> None:
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
        # The agent runs tools of its own; make it a session leader so
        # `end_process_group` ends those too.
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

  def send(self, text: str) -> None:
    assert self.proc.stdin is not None
    event = user_event(text)
    self._record("in", event)
    self.proc.stdin.write(json.dumps(event) + "\n")
    self.proc.stdin.flush()

  def close_stdin(self) -> None:
    assert self.proc.stdin is not None
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

  def finish(self, timeout: float) -> int:
    try:
      code = self.proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
      end_process_group(self.proc)
      code = -1
    finally:
      self.reader.join(timeout=10)
      self.log.close()
    return code

  def end(self) -> int:
    end_process_group(self.proc)
    self.reader.join(timeout=10)
    self.log.close()
    return self.proc.returncode if self.proc.returncode is not None else -1


def main() -> int:
  parser = argparse.ArgumentParser()
  _ = parser.add_argument("arm", choices=("mid", "neg", "pos"))
  _ = parser.add_argument("fixture", choices=sorted(tasks.BY_SLUG))
  _ = parser.add_argument("out_dir")
  _ = parser.add_argument("--model", default="sonnet")
  _ = parser.add_argument("--base-url", default=None)
  _ = parser.add_argument(
      "--phase",
      choices=("pilot", "graded"),
      default="graded",
      help="pilot runs are discarded and never pooled (§4.6)",
  )
  _ = parser.add_argument("--rerun-reason", default=None)
  _ = parser.add_argument(
      "--concurrency",
      type=int,
      default=1,
      help="how many runs were in flight; a run condition, so it is recorded",
  )
  args = parser.parse_args()

  fixture = tasks.BY_SLUG[args.fixture]
  out_dir = pathlib.Path(args.out_dir)
  out_dir.mkdir(parents=True, exist_ok=True)
  workdir = WORKDIR_ROOT / f"{args.phase}-{args.arm}-{fixture.slug}"
  materialize(fixture, workdir)
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
  env_note = {}
  if args.base_url:
    os.environ["ANTHROPIC_BASE_URL"] = args.base_url
    env_note["ANTHROPIC_BASE_URL"] = args.base_url

  correction = WRAPPER.format(correction=fixture.correction)
  manifest: dict[str, object] = {
      "arm": args.arm,
      "fixture": fixture.slug,
      "phase": args.phase,
      "session_id": session_id,
      "argv": argv,
      "env": env_note,
      "correction_sent": correction if args.arm != "neg" else None,
      "correction_in_prompt": args.arm == "pos",
      "rerun_reason": args.rerun_reason,
      "concurrency": args.concurrency,
      "started_at": dt.datetime.now(dt.timezone.utc).isoformat(),
      "claude_version": subprocess.run(
          ["claude", "--version"], capture_output=True, text=True, check=False
      ).stdout.strip(),
  }

  run = Run(out_dir, argv, workdir)
  timeline: list[str] = []

  def mark(note: str) -> None:
    timeline.append(f"{round(time.monotonic() - run.t0, 3)}s {note}")

  opening = fixture.prompt
  if args.arm == "pos":
    opening = f"{fixture.prompt}\n\n{correction}"
  run.send(opening)
  mark("sent the task")

  def tripped_or_ended(event: object) -> bool:
    # `result` ends the wait too: stdin is held open, so the process does not
    # exit when the turn does, and waiting on the trigger alone would idle until
    # the timeout on every trace that never deviates.
    if isinstance(event, dict) and event.get("type") == "result":
      return True
    return any(
        fixture.trigger({"name": b.get("name"), "input": b.get("input", {})})
        for b in tool_uses(event)
    )

  seen = run.wait_for(tripped_or_ended, timeout=RUN_TIMEOUT_S)
  ended = isinstance(seen, dict) and isinstance(seen.get("event"), dict)
  hit = None if ended and seen["event"].get("type") == "result" else seen
  mark(f"trigger fired: {hit is not None}")
  manifest["trigger_fired"] = hit is not None
  if hit is not None:
    manifest["trigger_at_seconds"] = hit["dt"]

  if hit is not None and args.arm == "mid":
    # No wait: the turn is still in flight, which is the whole arm.
    run.send(correction)
    mark("sent the correction mid-turn")

  run.close_stdin()
  code = run.finish(timeout=RUN_TIMEOUT_S)

  manifest["exit_code"] = code
  manifest["timed_out"] = code == -1
  manifest["ended_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
  manifest["timeline"] = timeline
  _ = (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
  print(json.dumps(manifest, indent=2))
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
