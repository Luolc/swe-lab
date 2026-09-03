"""Recompute every number in `REPORT.md` from `runs/`.

One command, no model calls:

    python analyze.py

The checks it applies to a correction are **frozen in `PREREGISTRATION.md` and
committed before the first run**. They are falsifiers: firing means the class of
error was reproduced; not firing means this checker did not see it, which is not
the same as its absence.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable, Mapping, Sequence
import json
import re
from typing import Any

import replay

RUNS = replay.RUNS
BUDGET = replay.BUDGET
COOLDOWN = replay.COOLDOWN

# --- Frozen check 1: a correction written with nothing in the window. --------
# `evidence_in_window == 0`. Today's first correction is the instance.

# --- Frozen check 2: a correction asserting the absence of something the -----
# record already shows. Both halves are frozen; a correction is flagged only
# when both fire.
NEGATION_PATTERNS: tuple[str, ...] = (
    r"haven'?t",
    r"have not",
    r"hasn'?t",
    r"has not",
    r"didn'?t",
    r"did not",
    r"\bnot yet\b",
    r"\byet to\b",
    r"\bno sign\b",
    r"\bnothing\b",
    r"\bstill no\b",
    r"\bdon'?t see\b",
    r"\bno output\b",
    r"\bno read\b",
    r"\bwithout\b",
)

#: token in the correction -> (where to look in the record, what to look for).
#: `tool_use` searches the serialized input of the actor's tool calls;
#: `tool_result` searches the text of the results it got back.
ARTIFACT_PREDICATES: dict[str, tuple[str, str]] = {
    "models.py": ("tool_use", "models.py"),
    "test_models.py": ("tool_use", "test_models.py"),
    "isbn.py": ("tool_use", "isbn.py"),
    "from_isbn": ("tool_result", "from_isbn"),
    "get_isbn_or_asin": ("tool_result", "get_isbn_or_asin"),
    "is_valid_identifier": ("tool_result", "is_valid_identifier"),
    "get_identifier_forms": ("tool_result", "get_identifier_forms"),
    "canonical": ("tool_result", "canonical"),
}

_NEGATION = re.compile("|".join(NEGATION_PATTERNS), re.IGNORECASE)


def record_index(
    events: Sequence[Mapping[str, Any]],
) -> tuple[list[str], list[str]]:
  """Return, per cursor, the actor's tool-call inputs and tool-result texts.

  Args:
    events: The recorded stream, in order.

  Returns:
    Two lists of length `len(events)`: the serialized tool-call inputs seen at
    each 1-based cursor, and the tool-result texts seen there.
  """
  uses: list[str] = []
  results: list[str] = []
  for event in events:
    use_text, result_text = "", ""
    if event.get("type") == "assistant":
      use_text = json.dumps(
          [
              b.get("input", {})
              for b in event["message"]["content"]
              if b.get("type") == "tool_use"
          ]
      )
    elif event.get("type") == "user":
      content = event["message"]["content"]
      if not isinstance(content, str):
        result_text = json.dumps(
            [b.get("content", "") for b in content if isinstance(b, dict)]
        )
    uses.append(use_text)
    results.append(result_text)
  return uses, results


def contradicted_by_record(
    text: str,
    cursor: int,
    uses: Sequence[str],
    results: Sequence[str],
) -> list[str]:
  """Return the artifacts this correction says are absent but the record shows.

  Args:
    text: The correction, verbatim.
    cursor: The 1-based boundary it was written at.
    uses: Per-cursor serialized tool-call inputs.
    results: Per-cursor tool-result texts.

  Returns:
    Every artifact token that appears in the correction, is covered by the
    frozen table, and whose record predicate is already true strictly before
    `cursor`. Empty when the negation half does not fire.
  """
  if not _NEGATION.search(text):
    return []
  hits: list[str] = []
  for token, (where, needle) in ARTIFACT_PREDICATES.items():
    if token.lower() not in text.lower():
      continue
    haystack = uses if where == "tool_use" else results
    if any(needle in blob for blob in haystack[: cursor - 1]):
      hits.append(token)
  return hits


def load_rows(arm: str, pass_id: str) -> list[dict[str, Any]]:
  """Return one run's judgment rows.

  Args:
    arm: The arm's directory name.
    pass_id: `"a"` or `"b"`.

  Returns:
    Every row, in order; empty when the run does not exist.
  """
  path = RUNS / arm / pass_id / "judgments.jsonl"
  if not path.exists():
    return []
  with path.open(encoding="utf-8") as handle:
    return [json.loads(line) for line in handle if line.strip()]


def speech_blocks(rows: Iterable[Mapping[str, Any]]) -> dict[str, int]:
  """Replay the budget and cooldown gates over a run's rows.

  The policy returns silence for either reason, so which gate closed is
  recovered here rather than read off a row.

  Args:
    rows: One run's rows, in order.

  Returns:
    Counts of markers, and of markers blocked by budget and by cooldown.
  """
  spoken: list[int] = []
  counts = {"markers": 0, "blocked_budget": 0, "blocked_cooldown": 0}
  for row in rows:
    if row["kind"] == "spoke":
      counts["markers"] += 1
      spoken.append(row["cursor"])
      continue
    if row.get("off_track") is not True or row.get("self_correcting") is not (
        False
    ):
      continue
    counts["markers"] += 1
    if len(spoken) >= BUDGET:
      counts["blocked_budget"] += 1
    elif spoken and row["cursor"] - spoken[-1] < COOLDOWN:
      counts["blocked_cooldown"] += 1
  return counts


def usage_of(rows: Iterable[Mapping[str, Any]]) -> dict[str, float]:
  """Total the measured tokens and cost of a run.

  Args:
    rows: One run's rows.

  Returns:
    Prompt tokens, completion tokens and provider-reported cost in USD.
  """
  totals = {"prompt_tokens": 0.0, "completion_tokens": 0.0, "cost_usd": 0.0}
  for row in rows:
    for call in row.get("calls", []):
      usage = call.get("usage") or {}
      totals["prompt_tokens"] += usage.get("prompt_tokens", 0) or 0
      totals["completion_tokens"] += usage.get("completion_tokens", 0) or 0
      totals["cost_usd"] += usage.get("cost", 0.0) or 0.0
  return totals


def span(values: Sequence[int]) -> str:
  """Return a compact min/median/max of a list of counts.

  Args:
    values: The counts.

  Returns:
    `"min M med D max X"`, or `"-"` when empty.
  """
  if not values:
    return "-"
  return (
      f"min {min(values)} med {sorted(values)[len(values) // 2]}"
      f" max {max(values)}"
  )


def summarize(
    arm: str, pass_id: str, rows: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
  """Reduce one run to the pre-registered readings.

  Args:
    arm: The arm's name.
    pass_id: `"a"` or `"b"`.
    rows: The run's rows, in order.

  Returns:
    One summary record.
  """
  judged = [r for r in rows if r["kind"] != "gap"]
  answered = [r for r in rows if r.get("off_track") is not None]
  spoke = [r for r in rows if r["kind"] == "spoke"]
  off = [r for r in answered if r["off_track"]]
  markers = [
      r
      for r in answered
      if r["off_track"] and r.get("self_correcting") is False
  ]
  return {
      "arm": arm,
      "pass": pass_id,
      "window": rows[0]["window"],
      "boundaries": len(rows),
      "answered": len(answered),
      "lapse": sum(1 for r in rows if r["kind"] == "lapse"),
      "gap": sum(1 for r in rows if r["kind"] == "gap"),
      "off_track": sum(1 for r in answered if r["off_track"]),
      "self_correcting": sum(1 for r in answered if r["self_correcting"]),
      "would_have_spoken": len(markers),
      "spoke": len(spoke),
      "spoke_cursors": [r["cursor"] for r in spoke],
      "first_off_track_cursor": off[0]["cursor"] if off else None,
      "first_off_track_assistant_turn": (
          off[0]["assistant_events_so_far"] if off else None
      ),
      "first_marker_cursor": markers[0]["cursor"] if markers else None,
      "budget_exhausted_cursor": (
          spoke[BUDGET - 1]["cursor"] if len(spoke) >= BUDGET else None
      ),
      "evidence_in_window": [r["evidence_in_window"] for r in judged],
      "evidence_dropped_by_window": [
          r["evidence_dropped_by_window"] for r in judged
      ],
      "new_evidence_dropped_by_window": [
          r["new_evidence_dropped_by_window"] for r in judged
      ],
      "rendered_nonempty_in_window": [
          r["rendered_nonempty_in_window"] for r in judged
      ],
      "prompt_chars": [r["prompt_chars"] for r in judged],
      **speech_blocks(rows),
      **usage_of(rows),
  }


def lapse_causes(rows: Iterable[Mapping[str, Any]]) -> dict[str, int]:
  """Attribute each lapse to what the provider actually returned.

  Args:
    rows: One run's rows.

  Returns:
    A count per cause.
  """
  causes: dict[str, int] = {}
  for row in rows:
    if row["kind"] != "lapse":
      continue
    call = (row.get("calls") or [{}])[0]
    if call.get("transport_error"):
      cause = "transport error"
    elif call.get("content_is_null"):
      cause = "null content"
    elif call.get("finish_reason") == "length":
      cause = "truncated at max_tokens"
    else:
      cause = f"unparseable (finish_reason={call.get('finish_reason')})"
    causes[cause] = causes.get(cause, 0) + 1
  return causes


def main() -> None:
  """Print every pre-registered reading."""
  parser = argparse.ArgumentParser(description=__doc__)
  _ = parser.parse_args()

  events = replay.load_events()
  uses, results = record_index(events)

  summaries: list[dict[str, Any]] = []
  for arm in replay.ARMS:
    for pass_id in ("a", "b"):
      rows = load_rows(arm.name, pass_id)
      if rows:
        summaries.append(summarize(arm.name, pass_id, rows))
  if not summaries:
    raise SystemExit("no runs under runs/ — run `python replay.py run --pass a`")

  print("## Per-run readings\n")
  print(
      f"| {'arm':<9} | p | bnd | answered | lapse | gap | off_track |"
      " self_correcting | would_have_spoken | spoke | cost $ |"
  )
  print("| " + " | ".join(["---"] * 11) + " |")
  for s in summaries:
    print(
        f"| {s['arm']:<9} | {s['pass']} | {s['boundaries']} | {s['answered']}"
        f" | {s['lapse']} | {s['gap']} | {s['off_track']}"
        f" | {s['self_correcting']} | {s['would_have_spoken']} | {s['spoke']}"
        f" | {s['cost_usd']:.3f} |"
    )

  print("\n## Timing of the first deviation and of the budget\n")
  print(
      "| arm | p | 1st off_track @cursor | @assistant turn |"
      " 1st marker @cursor | spoke at | budget spent by |"
  )
  print("| " + " | ".join(["---"] * 7) + " |")
  for s in summaries:
    print(
        f"| {s['arm']} | {s['pass']} | {s['first_off_track_cursor']}"
        f" | {s['first_off_track_assistant_turn']}"
        f" | {s['first_marker_cursor']} | {s['spoke_cursors']}"
        f" | {s['budget_exhausted_cursor']} |"
    )

  print("\n## What the judge was given, per judgment\n")
  print(
      "| arm | p | window | evidence in window | dropped by window |"
      " new evidence dropped | records rendering non-empty text |"
      " prompt chars |"
  )
  print("| " + " | ".join(["---"] * 8) + " |")
  for s in summaries:
    print(
        f"| {s['arm']} | {s['pass']} | {s['window']}"
        f" | {span(s['evidence_in_window'])}"
        f" | {span(s['evidence_dropped_by_window'])}"
        f" | {span(s['new_evidence_dropped_by_window'])}"
        f" | {span(s['rendered_nonempty_in_window'])}"
        f" | {span(s['prompt_chars'])} |"
    )

  print("\n## Speech gated by budget / cooldown\n")
  print("| arm | p | markers | blocked by budget | blocked by cooldown |")
  print("| " + " | ".join(["---"] * 5) + " |")
  for s in summaries:
    print(
        f"| {s['arm']} | {s['pass']} | {s['markers']} | {s['blocked_budget']}"
        f" | {s['blocked_cooldown']} |"
    )

  print("\n## Every correction, with the two frozen checks\n")
  for arm in replay.ARMS:
    for pass_id in ("a", "b"):
      for row in load_rows(arm.name, pass_id):
        if row["kind"] != "spoke":
          continue
        hits = contradicted_by_record(row["text"], row["cursor"], uses, results)
        print(
            f"- **{arm.name}/{pass_id}** cursor {row['cursor']}"
            f" (assistant events so far {row['assistant_events_so_far']}),"
            f" evidence in window {row['evidence_in_window']}"
            f" (rendering non-empty text: {row['rendered_nonempty_in_window']})"
        )
        print(f"  > {row['text']}")
        print(
            f"  - empty window: {row['evidence_in_window'] == 0}"
            f" — contradicted by record: {bool(hits)}"
            f"{' via ' + ', '.join(hits) if hits else ''}"
        )

  print("\n## Lapse causes\n")
  for arm in replay.ARMS:
    for pass_id in ("a", "b"):
      causes = lapse_causes(load_rows(arm.name, pass_id))
      if causes:
        print(f"- {arm.name}/{pass_id}: {causes}")

  calls = sum(s["boundaries"] - s["gap"] + s["spoke"] for s in summaries)
  print("\n## Totals\n")
  print(
      f"- runs: {len(summaries)}\n"
      f"- boundaries: {sum(s['boundaries'] for s in summaries)}\n"
      f"- model calls (judge + writer): {calls}\n"
      f"- prompt tokens: {int(sum(s['prompt_tokens'] for s in summaries)):,}\n"
      f"- completion tokens:"
      f" {int(sum(s['completion_tokens'] for s in summaries)):,}\n"
      f"- provider-reported cost:"
      f" ${sum(s['cost_usd'] for s in summaries):.2f}"
  )

  path = RUNS / "summary.json"
  _ = path.write_text(json.dumps(summaries, indent=2) + "\n", encoding="utf-8")
  print(f"\nwrote {path}")


if __name__ == "__main__":
  main()
