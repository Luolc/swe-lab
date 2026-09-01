#!/usr/bin/env python3
"""Raw runs → the report's numbers, and the hint-survival check that matters.

Three questions, in order of how load-bearing they are:

1. **Did every hint the host recorded reach the converted trace?** This is the
   spec's one fatal failure mode ("a hint lost in conversion must be
   detectable"), and it is answerable only because the Supervisor logs each
   hint host-side, independently of the trace. A hint the host emitted, the
   hook applied, and the conversation does not contain is a silent loss —
   reported per arm, never averaged away.
2. **Was the tool's own output preserved?** The injected suffix must sit at the
   *end* of a tool result whose remaining text is non-empty: appended, not
   substituted. (Byte-exactness of the append itself is task 02's measurement,
   pinned there against the hook's own re-applied rewrite.)
3. **What did the actor do next?** The assistant turn immediately after each
   hinted tool result, and whether the run's own text calls the hint an
   injection — the compliance read, quoted rather than scored, because n is 1
   per arm.

And one guard that is not a question but an assertion: **interleaved thinking
reached the trace.** On the OpenRouter path it depends on header and
provider-preference injection that only `cc-reverse-proxy` performs, and losing
it is silent — the run completes and the trace looks whole while the model's own
reasoning, the whole value of the trace, is not what the spec says it is.

The guard is **two checks, because the cheap one is not sufficient** and
measuring that was the point:

- `signed > 0` — the trace carries signed reasoning blocks. A floor. Measured
  2026-09-01: a direct-to-OpenRouter `stream` run with no proxy at all scored
  10 signed blocks out of 10, so **this check passes on the degraded path** and
  cannot be read as "interleaved thinking worked".
- `thinking_replayed > 0` — prior assistant `thinking` blocks are *echoed back*
  in later request bodies. That is what interleaved thinking across turns
  actually means, and it is visible only on the wire, which is a second reason
  the proxy capture is required rather than optional.

Usage: ``uv run python experiments/trace_synthesis/steered_rerun/analyze.py``
"""

from __future__ import annotations

import json
import pathlib
import re

_HERE = pathlib.Path(__file__).resolve().parent
_RUNS = _HERE / "runs"
_FROZEN_ROOT = pathlib.Path("/home/ubuntu/dev/swe-lab-artifacts/trace_synthesis")

# What the hook wraps a hint in; the survival check looks for exactly this.
_OPEN, _CLOSE = "<oracle_hint>", "</oracle_hint>"

# Words the actor uses when it refuses a hint as an attack. Measured in task
# 02, where a refusing run said "prompt injection attempt" and "text formatted
# to impersonate a supervisor".
_OBJECTION = re.compile(
    r"prompt[- ]injection|injection attempt|impersonat|ignore (?:this|the) "
    r"(?:instruction|hint)|not from the user",
    re.IGNORECASE,
)


def load_conversation(frozen: pathlib.Path) -> dict[str, object] | None:
  """Read the typed conversation a frozen run converted its trace into.

  Args:
    frozen: The frozen run directory.

  Returns:
    The conversation, or ``None`` when the run produced none.
  """
  for path in sorted(frozen.rglob("conversation.json")):
    return json.loads(path.read_text())
  return None


def hint_records(label: str) -> list[dict[str, object]]:
  """Return the host-side judgements for one arm.

  Args:
    label: The arm's label (its ``runs/<label>/`` directory).

  Returns:
    Every judgement, hint or not, in order.
  """
  path = _RUNS / label / "hint_log.jsonl"
  if not path.is_file():
    return []
  return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def hook_records(frozen: pathlib.Path) -> list[dict[str, object]]:
  """Return the in-sandbox hook log a frozen run left behind.

  It is the second half of the loss check: the Supervisor says what it emitted,
  this says what the hook managed to apply, and the conversation says what
  survived.

  Args:
    frozen: The frozen run directory.

  Returns:
    Every hook invocation, in order.
  """
  for path in sorted(frozen.rglob("steer_hook.local.jsonl")):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
  return []


# `Read` renders its file with `<n>\t` line-number prefixes, and the hint is
# appended into that same field — so the injected block comes back numbered as
# though it were part of the file. Stripping the prefixes before matching is
# what stops the preservation check from reporting a false violation; that the
# hint *is* numbered like file content is a finding in its own right, recorded
# in REPORT.
_NUMBERED = re.compile(r"^\s*\d+\t", re.M)


def _unnumbered(content: str) -> str:
  """Return a tool result with `Read`'s line-number prefixes removed.

  Args:
    content: The rendered tool result.

  Returns:
    The same text with any leading ``<n>\t`` per line stripped.
  """
  return _NUMBERED.sub("", content)


def survival(
    conversation: dict[str, object] | None, hints: list[str]
) -> list[dict[str, object]]:
  """Check each emitted hint against the converted conversation.

  Args:
    conversation: The typed conversation, or ``None``.
    hints: The hint texts the Supervisor emitted, in order.

  Returns:
    One row per hint: whether it is present, whether it sits at the end of a
    tool result, and whether that result still carries the tool's own text.
  """
  blocks = [
      block
      for message in (conversation or {}).get("messages", [])
      for block in message.get("content", [])
      if block.get("type") == "tool_result"
  ]
  rows: list[dict[str, object]] = []
  for hint in hints:
    row: dict[str, object] = {
        "hint": hint,
        "in_conversation": False,
        "appended_at_end": False,
        "tool_output_kept": False,
    }
    for block in blocks:
      content = _unnumbered(str(block.get("content", "")))
      if hint not in content:
        continue
      row["in_conversation"] = True
      suffix = f"\n\n{_OPEN}\n{hint}\n{_CLOSE}"
      row["appended_at_end"] = content.rstrip().endswith(_CLOSE)
      row["tool_output_kept"] = suffix in content and bool(
          content[: content.index(suffix)].strip()
      )
      break
    rows.append(row)
  return rows


def reactions(
    conversation: dict[str, object] | None, hints: list[str]
) -> list[dict[str, object]]:
  """Return what the actor said in the turn right after each hint.

  Args:
    conversation: The typed conversation, or ``None``.
    hints: The hint texts, in order.

  Returns:
    One row per hint: the following assistant turn's reasoning and text, and
    the tool it called next.
  """
  messages = (conversation or {}).get("messages", [])
  rows: list[dict[str, object]] = []
  for hint in hints:
    index = next(
        (
            i
            for i, message in enumerate(messages)
            for block in message.get("content", [])
            if block.get("type") == "tool_result" and hint in str(block.get("content", ""))
        ),
        None,
    )
    if index is None:
      rows.append({"hint": hint, "found": False})
      continue
    following = next(
        (m for m in messages[index + 1 :] if m.get("role") == "assistant"), {}
    )
    blocks = following.get("content", [])
    rows.append({
        "hint": hint,
        "found": True,
        "reasoning": " ".join(
            str(b.get("text", "")) for b in blocks if b.get("type") == "reasoning"
        ),
        "text": " ".join(
            str(b.get("text", "")) for b in blocks if b.get("type") == "text"
        ),
        "next_tool": next(
            (str(b.get("name")) for b in blocks if b.get("type") == "tool_use"), None
        ),
    })
  return rows


def objections(conversation: dict[str, object] | None) -> list[str]:
  """Return every assistant passage that reads the hint as an attack.

  Args:
    conversation: The typed conversation, or ``None``.

  Returns:
    The matching sentences, bounded for the report.
  """
  found: list[str] = []
  for message in (conversation or {}).get("messages", []):
    if message.get("role") != "assistant":
      continue
    for block in message.get("content", []):
      text = str(block.get("text", ""))
      for match in _OBJECTION.finditer(text):
        start = max(0, match.start() - 160)
        found.append(text[start : match.end() + 160].strip())
  return found


def deferral(judgements: list[dict[str, object]]) -> dict[str, object]:
  """Measure what carrying an unreachable intervention forward actually costs.

  The Supervisor cannot hint at a boundary whose tool response has no text
  field (`Edit` and friends), so it carries the intervention to the next
  boundary that can take one. That is a mitigation, not a fix, and it has two
  prices this makes visible:

  - **Permanent loss** — no appendable boundary arrived before the run ended.
    This is the spec's named "a hint injected after the actor's last API call
    never reaches the model" loss path, in the concrete.
  - **Latency** — how many tool boundaries passed between the Supervisor
    deciding to intervene and the actor hearing it. Not free: the actor may
    have compounded the mistake in between.

  Args:
    judgements: The host-side log for one arm, in order.

  Returns:
    Counts and the per-intervention latencies, in boundaries.
  """
  deferred = [
      int(record["seq"])
      for record in judgements
      if record.get("suppressed") == "tool response has no text field to append to"
  ]
  latencies: list[int] = []
  delivered: set[int] = set()
  for record in judgements:
    for origin in record.get("carried_from") or []:
      delivered.add(int(origin))
      latencies.append(int(record["seq"]) - int(origin))
  return {
      "interventions_deferred": len(deferred),
      "delivered_late": len(delivered),
      "lost_permanently": sorted(set(deferred) - delivered),
      "latency_boundaries": sorted(latencies),
      "max_latency": max(latencies) if latencies else 0,
  }


def reasoning(conversation: dict[str, object] | None) -> dict[str, int]:
  """Count reasoning blocks and how many carry a signature.

  Args:
    conversation: The typed conversation, or ``None``.

  Returns:
    ``total`` and ``signed`` counts. ``signed == 0`` means interleaved thinking
    did not reach the trace, whatever else the run reports.
  """
  blocks = [
      block
      for message in (conversation or {}).get("messages", [])
      for block in message.get("content", [])
      if block.get("type") == "reasoning"
  ]
  return {
      "total": len(blocks),
      "signed": sum(1 for block in blocks if block.get("signature")),
  }


def thinking_replayed(frozen: pathlib.Path) -> int:
  """Count proxied requests that echo a prior assistant ``thinking`` block.

  Interleaved thinking across turns *is* the replay: the client sends earlier
  reasoning back, signatures and all, on each subsequent request. A response
  carrying reasoning proves only that the model thought once; only the request
  side proves the conversation kept it.

  Args:
    frozen: The frozen run directory.

  Returns:
    How many captured request bodies replay at least one assistant ``thinking``
    block. ``-1`` when the run has no proxy log to read, so "not measurable
    here" never reads as "measured zero".
  """
  logs = sorted(frozen.rglob("*proxy*.jsonl"))
  if not logs:
    return -1
  replays = 0
  for line in logs[0].read_text().splitlines():
    if not line.strip():
      continue
    try:
      record = json.loads(line)
    except json.JSONDecodeError:
      continue
    messages = record.get("request", {}).get("body", {}).get("messages", [])
    if any(
        block.get("type") == "thinking"
        for message in messages
        if message.get("role") == "assistant"
        for block in (
            message.get("content", [])
            if isinstance(message.get("content"), list)
            else []
        )
    ):
      replays += 1
  return replays


def analyze(summary_path: pathlib.Path) -> dict[str, object]:
  """Assemble one rollout's numbers.

  Args:
    summary_path: A ``runs/<label>/summary-r<n>.json`` written by
      ``run_steered.py``.

  Returns:
    The rollout's analysis row.
  """
  summary = json.loads(summary_path.read_text())
  label = str(summary["label"])
  session = str(summary["session"])
  frozen = _FROZEN_ROOT / f"{label}-rollout-{summary.get('rollout_id')}"
  conversation = load_conversation(frozen) if frozen.is_dir() else None
  # One hint log per label, several rollouts per label: filter to this one, or
  # a resampled arm reports its predecessor's hints as its own.
  records = hint_records(label)
  judgements = [r for r in records if r.get("session") == session]
  # A boundary the poller could not judge is not a judgement, and counting it
  # as one would report a gap in the belief state as coverage. It has no
  # session field — the request never reached the judge — so it is counted
  # separately and only when this is the only arm in the log.
  watcher_errors = [str(r["watcher_error"]) for r in records if r.get("watcher_error")]
  emitted = [str(r["hint"]) for r in judgements if r.get("hint_emitted")]
  hooks = hook_records(frozen) if frozen.is_dir() else []
  rows = survival(conversation, emitted)
  return {
      "label": label,
      "session": session,
      "summary": summary,
      "frozen": str(frozen),
      "boundaries_judged": len(judgements),
      "supervisor_errors": sum(1 for r in judgements if r.get("model_error")),
      "boundaries_unjudged": len(watcher_errors),
      "watcher_errors": sorted(set(watcher_errors)),
      "off_track_verdicts": sum(1 for r in judgements if r.get("on_track") is False),
      "hints_emitted": len(emitted),
      "hints_suppressed": sum(1 for r in judgements if r.get("suppressed")),
      "suppression_reasons": sorted(
          {str(r["suppressed"]) for r in judgements if r.get("suppressed")}
      ),
      "deferral": deferral(judgements),
      "hook_invocations": len(hooks),
      "hook_applied": sum(1 for r in hooks if r.get("applied")),
      "hook_errors": [str(r["error"]) for r in hooks if r.get("error")],
      "messages_in_conversation": len((conversation or {}).get("messages", [])),
      "reasoning": reasoning(conversation),
      "thinking_replayed": thinking_replayed(frozen) if frozen.is_dir() else -1,
      "hints_in_conversation": sum(1 for r in rows if r["in_conversation"]),
      "hints_lost": [r["hint"] for r in rows if not r["in_conversation"]],
      "appended_not_replaced": sum(1 for r in rows if r["tool_output_kept"]),
      "objections": objections(conversation),
      "survival": rows,
      "reactions": reactions(conversation, emitted),
      "judgements": judgements,
  }


def main() -> None:
  """Analyze every rollout that left a summary, and write ``analysis.json``."""
  summaries = sorted(_RUNS.glob("*/summary-r*.json"))
  analysis = {"arms": [analyze(path) for path in summaries]}
  _ = (_HERE / "analysis.json").write_text(
      json.dumps(analysis, indent=2, ensure_ascii=False) + "\n"
  )
  for arm in analysis["arms"]:
    print(
        f"{arm['session']}: {arm['boundaries_judged']} boundaries,"
        f" {arm['hints_emitted']} hints emitted,"
        f" {arm['hints_in_conversation']} in the trace,"
        f" {len(arm['objections'])} objections,"
        f" resolved={arm['summary'].get('resolved')},"
        f" signed reasoning={arm['reasoning']['signed']},"
        f" thinking replayed={arm['thinking_replayed']},"
        f" deferred={arm['deferral']['interventions_deferred']}"
        f" (late {arm['deferral']['delivered_late']},"
        f" lost {len(arm['deferral']['lost_permanently'])})"
        + (
            " — DEGRADED, interleaved thinking absent"
            if arm["thinking_replayed"] == 0
            else " — NOT MEASURABLE (no proxy log)"
            if arm["thinking_replayed"] < 0
            else ""
        )
    )


if __name__ == "__main__":
  main()
