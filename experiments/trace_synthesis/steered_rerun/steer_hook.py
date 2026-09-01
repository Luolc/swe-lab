#!/usr/bin/env python3
"""The in-sandbox hook: ask the host-side Supervisor, append its hint.

Runs inside the instance container, spawned by Claude Code at every tool
boundary. It holds **no** privileged information: the guidebook and the belief
state live in the host-side Supervisor process
(:mod:`supervisor`), which is the only thing this talks to. All this program
knows how to do is describe the tool boundary, ask, and — when an answer comes
back with a hint — append it to the tool's own output.

Three properties it must have, each of them a spec constraint
(``docs/trace-synthesis/spec.md`` §5, §10, §11):

- **It never nests ``claude``.** The Supervisor is a host process reached
  through a shared directory; the ``CLAUDECODE=1`` nesting guard is never
  approached and there is no recursion to explode. It is not reached over the
  network either — this box's ``ufw`` default-denies incoming, so nothing on
  the Docker bridge can open a host port at all — which leaves the sandbox with
  no route out to the Supervisor, only the workspace it already shares.
- **It only ever appends.** ``updatedToolOutput`` is a copy of the tool's own
  response object with the tagged hint appended to whichever field carries the
  text, so the tool's real bytes stay in the trace verbatim. Claude Code
  validates the object against the tool's declared output schema and silently
  falls back to the original on a mismatch, so the copy keeps the shape.
- **It fails open, loudly.** A Supervisor that is unreachable or slow must not
  wedge the actor, so the hook emits nothing and the run continues — but it
  writes a local line saying so, and the Supervisor's own host-side log is
  what the analysis reads. A hint lost silently is the one fatal failure mode.

``append_hint`` / ``tagged`` mirror ``injection_shape/hook.py``, whose
byte-exactness the task-02 analysis pinned; they are duplicated rather than
imported because this file is mounted alone into a container that has none of
this repo on it.

Usage: ``steer_hook.py <io-dir> <session-id> <log-path>``
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import uuid

# Text fields that carry a tool's output, per tool. Read nests its text one
# level down (``file.content``); Bash and the rest keep it at the top level.
TOP_LEVEL_FIELDS = ("stdout", "content", "output", "result")

# Longer than the Supervisor's own model deadline, so a slow judgement is
# waited for rather than turned into a silently dropped hint. Both sit under
# the hook timeout the settings file declares, which fails open.
_ANSWER_DEADLINE_S = 100.0
_POLL_S = 0.15


def tagged(hint: str, tag: str) -> str:
  """Wrap the hint in its marker tag.

  Args:
    hint: The hint text.
    tag: The marker tag name.

  Returns:
    The suffix to append to a tool's output.
  """
  return f"\n\n<{tag}>\n{hint}\n</{tag}>"


def append_hint(response: object, hint: str, tag: str) -> object | None:
  """Return a copy of ``response`` with the tagged hint appended.

  Args:
    response: The tool response the hook was handed.
    hint: The hint text.
    tag: The marker tag name.

  Returns:
    The rewritten response, or ``None`` when no text field was found to append
    to (in which case the hook emits nothing and the original output stands).
  """
  suffix = tagged(hint, tag)
  if isinstance(response, str):
    return response + suffix
  if not isinstance(response, dict):
    return None
  updated = dict(response)
  nested = updated.get("file")
  if isinstance(nested, dict) and isinstance(nested.get("content"), str):
    nested = dict(nested)
    nested["content"] = nested["content"] + suffix
    updated["file"] = nested
    return updated
  for field in TOP_LEVEL_FIELDS:
    if isinstance(updated.get(field), str):
      updated[field] = updated[field] + suffix
      return updated
  return None


def excerpt(value: object, head: int = 1500, tail: int = 500) -> str:
  """Render a tool payload for the Supervisor, bounded at both ends.

  The middle is what a long file dump or test log wastes; the head says what
  the call was and the tail carries the verdict line, so both are kept.

  Args:
    value: Any JSON-able tool input or response.
    head: Characters kept from the front.
    tail: Characters kept from the back.

  Returns:
    The bounded rendering.
  """
  text = value if isinstance(value, str) else json.dumps(value, default=str)
  if len(text) <= head + tail:
    return text
  dropped = len(text) - head - tail
  return f"{text[:head]}\n… [{dropped} characters elided] …\n{text[-tail:]}"


def ask(io_dir: str, payload: dict[str, object]) -> dict[str, object]:
  """Ask the host-side Supervisor to judge one tool boundary.

  The exchange is two files in a directory both sides can see: the request is
  written under a temporary name and renamed, so the watcher never reads a
  half-written file, and the answer is picked up the same way.

  Args:
    io_dir: The shared directory, inside the bind-mounted workspace.
    payload: The tool boundary description.

  Returns:
    The Supervisor's decision.

  Raises:
    TimeoutError: If no answer appears before the deadline. Failing open is
      the hook's contract; saying so in the log is what makes the loss
      detectable.
  """
  os.makedirs(io_dir, exist_ok=True)
  token = uuid.uuid4().hex
  request = os.path.join(io_dir, f"{token}.req.json")
  answer = os.path.join(io_dir, f"{token}.resp.json")
  staging = request + ".tmp"
  with open(staging, "w") as handle:
    json.dump(payload, handle)
  os.replace(staging, request)

  deadline = time.monotonic() + _ANSWER_DEADLINE_S
  while time.monotonic() < deadline:
    if os.path.exists(answer):
      with open(answer) as handle:
        decision = json.load(handle)
      for path in (answer, request):
        try:
          os.unlink(path)
        except OSError:
          pass
      return decision
    time.sleep(_POLL_S)
  raise TimeoutError(f"no answer for {token} within {_ANSWER_DEADLINE_S}s")


def main() -> None:
  """Read the hook payload, ask the Supervisor, emit at most an appended hint."""
  io_dir, session, log_path = sys.argv[1:4]
  raw = sys.stdin.read()
  try:
    hook_payload = json.loads(raw)
  except json.JSONDecodeError:
    hook_payload = {"_unparsed": raw[:2000]}

  event = hook_payload.get("hook_event_name", "")
  tool_name = hook_payload.get("tool_name", "")
  tool_response = hook_payload.get("tool_response")
  # The one identifier that is the *same value* in all three records of a run.
  # Every `tool_result` in the converted conversation carries `tool_use_id`, so
  # recording it here (and passing it to the Supervisor) turns the three-way
  # reconciliation from a positional join into an exact one — a converted
  # boundary that was dropped or duplicated cannot then hide behind matching
  # counts. Optional on purpose: if the harness stops sending it, the value is
  # `None` and `reconcile.py` says it fell back to position.
  tool_use_id = hook_payload.get("tool_use_id")
  record: dict[str, object] = {
      "t": time.time(),
      "pid": os.getpid(),
      "event": event,
      "tool": tool_name,
      "tool_use_id": tool_use_id,
  }

  # Whether this boundary can carry a hint at all, decided by the same function
  # that would apply one. `Edit` and friends answer with a structured object
  # that has no free-text field, and `updatedToolOutput` is validated against
  # the tool's declared schema — so there is nowhere to append. Telling the
  # Supervisor up front is what lets it carry the intervention to the next
  # boundary instead of spending it on one that cannot deliver.
  appendable = append_hint(tool_response, "probe", "probe") is not None
  record["appendable"] = appendable

  started = time.monotonic()
  try:
    decision = ask(
        io_dir,
        {
            "session": session,
            "event": event,
            "appendable": appendable,
            "tool_name": tool_name,
            "tool_use_id": tool_use_id,
            "tool_input": excerpt(hook_payload.get("tool_input"), 1200, 300),
            "tool_response": excerpt(tool_response),
        },
    )
  except Exception as error:  # noqa: BLE001 — fail open, but say so
    record["error"] = f"{type(error).__name__}: {error}"
    record["applied"] = False
    _append(log_path, record)
    return
  record["ask_seconds"] = round(time.monotonic() - started, 2)
  record["seq"] = decision.get("seq")

  hint = decision.get("hint")
  # Only ``PostToolUse`` can carry ``updatedToolOutput`` at all: the batch and
  # failure events accept ``additionalContext`` alone, which the default stream
  # capture drops (task 02). A failure boundary is therefore reported to the
  # Supervisor — so the belief state stays complete — and never injected into.
  if event != "PostToolUse":
    record["applied"] = False
    record["reason"] = f"{event} cannot carry updatedToolOutput"
    _append(log_path, record)
    return
  if not isinstance(hint, str) or not hint.strip():
    record["applied"] = False
    _append(log_path, record)
    return

  # The hint text itself stays out of this file: it is written host-side, in
  # the Supervisor's log, and this one lives inside the sandbox where the
  # actor could read it. The digest is enough to match the two up.
  record["hint_sha256"] = hashlib.sha256(hint.encode()).hexdigest()

  updated = append_hint(tool_response, hint, str(decision.get("tag", "oracle_hint")))
  if updated is None:
    # No text field to append to. The Supervisor already logged the hint as
    # emitted, so this line is what tells the analysis it never reached the
    # actor.
    record["applied"] = False
    record["reason"] = "no text field in tool_response"
    _append(log_path, record)
    return

  record["applied"] = True
  _append(log_path, record)
  print(
      json.dumps({
          "hookSpecificOutput": {
              "hookEventName": "PostToolUse",
              "updatedToolOutput": updated,
          }
      })
  )


def _append(path: str, record: dict[str, object]) -> None:
  """Append one JSON line to the in-sandbox hook log, best effort."""
  try:
    with open(path, "a") as handle:
      handle.write(json.dumps(record, default=str) + "\n")
  except OSError:
    pass


if __name__ == "__main__":
  main()
