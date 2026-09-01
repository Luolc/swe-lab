#!/usr/bin/env python3
"""The probe hook: log the payload, emit one candidate injection shape.

One program serves every variant so the only thing that differs between runs is
``PROBE_MODE``. It reads the hook payload on stdin, appends a line to
``PROBE_LOG``, and writes the candidate's hook response on stdout.

``updated_tool_output`` is the shape the experiment is really about: it copies
the tool's own response object and *appends* the tagged hint to whichever text
field carries the output, so the tool's real bytes stay in the trace verbatim.
Claude Code validates ``updatedToolOutput`` against the tool's declared output
schema and falls back to the original output on a mismatch, so the copy has to
keep the object's shape.

``append_hint`` takes its hint and tag as arguments and touches nothing global,
so ``analyze.py`` can re-apply the exact rewrite a run performed and check, byte
for byte, that it only ever appended.
"""

from __future__ import annotations

import json
import os
import sys
import time

# Text fields that carry a tool's output, per tool. Read nests its text one
# level down (``file.content``); Bash and the rest keep it at the top level.
TOP_LEVEL_FIELDS = ("stdout", "content", "output", "result")


def tagged(hint: str, tag: str) -> str:
  """Wrap the hint in its marker tag — or, with an empty tag, leave it bare.

  The bare form is the control for "is it the tag that trips Claude Code's
  prompt-injection detector, or any injected imperative text at all".

  Args:
    hint: The hint text.
    tag: The marker tag name, or ``""`` for the untagged control.

  Returns:
    The suffix to append to a tool's output.
  """
  if not tag:
    return f"\n\n{hint}"
  return f"\n\n<{tag}>\n{hint}\n</{tag}>"


def append_hint(response: object, hint: str, tag: str) -> object | None:
  """Return a copy of ``response`` with the tagged hint appended.

  Args:
    response: The tool response the hook was handed.
    hint: The hint text.
    tag: The marker tag name, or ``""`` for the untagged control.

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


def main() -> None:
  """Read the hook payload on stdin, log it, emit the configured response."""
  log = os.environ["PROBE_LOG"]
  mode = os.environ.get("PROBE_MODE", "log_only")
  hint = os.environ.get("PROBE_HINT", "hint")
  tag = os.environ.get("PROBE_TAG", "oracle_hint")

  raw = sys.stdin.read()
  try:
    payload = json.loads(raw)
  except json.JSONDecodeError:
    payload = {"_unparsed": raw}
  with open(log, "a") as fh:
    fh.write(json.dumps({"t": time.time(), "mode": mode, "stdin": payload}) + "\n")

  event = payload.get("hook_event_name", "")
  out: dict[str, object] = {}

  if mode == "log_only":
    pass
  elif mode == "updated_tool_output" and event == "PostToolUse":
    updated = append_hint(payload.get("tool_response"), hint, tag)
    if updated is not None:
      out = {
          "hookSpecificOutput": {
              "hookEventName": "PostToolUse",
              "updatedToolOutput": updated,
          }
      }
  elif mode == "post_tool_use_context" and event == "PostToolUse":
    out = {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": tagged(hint, tag).strip(),
        }
    }
  elif mode == "post_tool_batch_context" and event == "PostToolBatch":
    out = {
        "hookSpecificOutput": {
            "hookEventName": "PostToolBatch",
            "additionalContext": tagged(hint, tag).strip(),
        }
    }
  elif mode == "post_tool_use_failure_context" and event == "PostToolUseFailure":
    out = {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUseFailure",
            "additionalContext": tagged(hint, tag).strip(),
        }
    }

  if out:
    print(json.dumps(out))


if __name__ == "__main__":
  main()
