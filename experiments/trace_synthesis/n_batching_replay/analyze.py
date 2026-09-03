"""Recompute `REPORT.md`'s sections 2 to 8 from `runs/`.

Not section 1: that shape table comes from `replay.py shape`, which reads the
event stream and makes no model call. The corpus, criterion and task digests
come from `runs/*/*/manifest.json`. No single command regenerates all three.

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


RECORDED_LOG = replay.CORPUS / "supervisor.jsonl"
PROXY_LOG = replay.CORPUS / "claude_code.proxy_log.jsonl"


def admitted_by_cursor(
    events: Sequence[Mapping[str, Any]],
) -> list[int]:
  """Return, per cursor, how many records the evidence filter has admitted.

  The recorded log does not store the window size, so it is recomputed with the
  shipped filter over the same stream.

  Args:
    events: The recorded stream, in order.

  Returns:
    A list whose 1-based index `c` gives the admitted count after `c` events.
  """
  from swe_lab.harnesses.claude_code.convert import event_to_message
  from swe_lab.trace_synthesis.supervisor import EvidenceFilter

  evidence_filter = EvidenceFilter()
  counts = [0]
  admitted = 0
  for event in events:
    record, _ = evidence_filter.admit(event_to_message(event))
    if record is not None:
      admitted += 1
    counts.append(admitted)
  return counts


def recorded_rows() -> list[dict[str, Any]]:
  """Return the supervisor log of the run the corpus preserves.

  Returns:
    Every row of `supervisor.jsonl`, in order. This is the pair for the
    `replicate` arm: the same configuration, run once for real.
  """
  with RECORDED_LOG.open(encoding="utf-8") as handle:
    return [json.loads(line) for line in handle if line.strip()]


def delivery_points(
    events: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
  """Locate where each recorded correction first reached the actor.

  A correction is written at a boundary but surfaces to the actor later, when
  the CLI folds it into a request. Two independent orderings are used and
  reported side by side: the position of the note in the recorded conversation
  (whose following assistant message is then located in the event stream), and
  the first proxy-log request whose body carries the note.

  Args:
    events: The recorded stream, in order.

  Returns:
    One record per delivered correction.
  """
  conversation = json.loads(
      replay.CONVERSATION.read_text(encoding="utf-8")
  )["messages"]
  proxy = PROXY_LOG.read_text(encoding="utf-8").splitlines()
  written = [r["cursor"] for r in recorded_rows() if r["kind"] == "spoke"]

  def event_index(kind: str, needle: str) -> int | None:
    for index, event in enumerate(events, 1):
      if event.get("type") != "assistant":
        continue
      for block in event["message"]["content"]:
        if kind == "text" and block.get("type") == "text":
          if block["text"].strip() == needle.strip():
            return index
        if kind == "tool" and block.get("type") == "tool_use":
          if json.dumps(block["input"]) == needle:
            return index
    return None

  out: list[dict[str, Any]] = []
  seen = 0
  for position, message in enumerate(conversation):
    if "supervisor_note" not in json.dumps(message):
      continue
    following = conversation[position + 1]
    key: tuple[str, str] | None = None
    for block in following["content"]:
      if block.get("type") == "text":
        key = ("text", block["text"])
        break
      if block.get("type") == "tool_use":
        key = ("tool", json.dumps(block["input"]))
        break
    landed = event_index(*key) if key else None
    # The note's own text, escaped the way a JSON log line carries it, so the
    # search is over the same encoding rather than over raw characters.
    body = json.dumps(message)
    marker = body[body.index("supervisor_note") + 20 :][:40]
    entry = next(
        (i for i, line in enumerate(proxy) if marker and marker in line), None
    )
    out.append(
        {
            "written_at_cursor": written[seen] if seen < len(written) else None,
            "surfaced_before_event": landed,
            "lag_events": (
                landed - written[seen]
                if landed is not None and seen < len(written)
                else None
            ),
            "first_proxy_request": entry,
        }
    )
    seen += 1
  return out


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


def shared_cursor_agreement(
    left: tuple[str, str], right: tuple[str, str]
) -> list[dict[str, Any]]:
  """Compare two arms where both judged the same cursor.

  Their observations at a shared cursor are built from the same events by the
  same filter, so the evidence window is identical whenever the two arms share
  a window size; the only other input that can differ is `Observation.said`,
  which the prompt renders as its last section. This reports both, so a
  divergence in verdicts is read against a measured input difference rather
  than an assumed one.

  Args:
    left: `(arm, pass)` whose corrections are counted.
    right: `(arm, pass)` compared against it.

  Returns:
    One record per shared cursor.
  """
  wide = {r["cursor"]: r for r in load_rows(*left)}
  spoke_at = [c for c, r in wide.items() if r["kind"] == "spoke"]
  out: list[dict[str, Any]] = []
  for row in load_rows(*right):
    other = wide.get(row["cursor"])
    if other is None:
      continue
    out.append(
        {
            "cursor": row["cursor"],
            "left": (other.get("off_track"), other.get("self_correcting")),
            "right": (row.get("off_track"), row.get("self_correcting")),
            "same_evidence_in_window": (
                other["evidence_in_window"] == row["evidence_in_window"]
            ),
            "prompt_chars_delta": other["prompt_chars"] - row["prompt_chars"],
            # Corrections the left arm had already written when this boundary
            # was judged: strictly earlier cursors, since a correction at
            # cursor c is written after that boundary's own judgment.
            "left_had_spoken": sum(1 for c in spoke_at if c < row["cursor"]),
        }
    )
  return out


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
    elif call.get("finish_reason") in {"length", "max_tokens"}:
      # One cause, two surfaces: the answer is cut at `max_tokens` either way,
      # and whether any content bytes escaped depends only on how much of the
      # budget the model's reasoning had already taken. Reporting these as two
      # causes is what an earlier version of this function did, and it invented
      # a distinction the data does not carry. Historical OpenAI-shaped rows
      # call the reason `length`; Anthropic Messages calls it `max_tokens`.
      cause = (
          "max_tokens exhausted, no content at all"
          if call.get("content_is_null")
          else "max_tokens exhausted, content cut mid-answer"
      )
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

  every: list[tuple[bool, int, int, str | None]] = []
  for arm in replay.ARMS:
    for pass_id in ("a", "b"):
      for row in load_rows(arm.name, pass_id):
        call = (row.get("calls") or [{}])[0]
        usage = call.get("usage") or {}
        reasoning = (usage.get("completion_tokens_details") or {}).get(
            "reasoning_tokens"
        )
        if reasoning is None:
          continue
        every.append(
            (
                row["kind"] == "lapse",
                usage.get("completion_tokens", 0),
                reasoning,
                call.get("finish_reason"),
            )
        )
  lapsed = [e for e in every if e[0]]
  fine = [e for e in every if not e[0]]

  def median(values: Sequence[int]) -> float:
    """Return the median of a list of counts.

    Args:
      values: The counts.

    Returns:
      The middle value, or 0 when empty.
    """
    return sorted(values)[len(values) // 2] if values else 0

  print(
      f"\n- every lapse ({len(lapsed)}): finish_reason"
      f" {sorted({e[3] for e in lapsed})}, completion tokens median"
      f" {median([e[1] for e in lapsed])}, of which reasoning median"
      f" {median([e[2] for e in lapsed])}"
  )
  print(
      f"- every answered call ({len(fine)}): completion tokens median"
      f" {median([e[1] for e in fine])}, of which reasoning median"
      f" {median([e[2] for e in fine])}"
  )
  print(
      f"- `ModelJudge.max_tokens` is 512, so a lapse is that ceiling reached"
      f" with the answer unfinished."
  )

  def agree(subset: Sequence[Mapping[str, Any]]) -> str:
    """Return how often two arms gave the identical verdict.

    Args:
      subset: Shared-cursor records.

    Returns:
      `"k/n identical verdicts"` over the pairs where both answers parsed.
    """
    both = [s for s in subset if None not in s["left"] + s["right"]]
    if not both:
      return "no comparable pair"
    same = sum(1 for s in both if s["left"] == s["right"])
    return f"{same}/{len(both)} identical verdicts"

  print("\n## Pairs of runs, compared only where both judged the same cursor\n")
  pairs: tuple[tuple[tuple[str, str], tuple[str, str], str], ...] = (
      (
          ("replicate_budget0", "a"),
          ("replicate_budget0", "b"),
          "SAME configuration, two runs — this is run-to-run variance, and"
          " neither run ever speaks, so the prompts are byte-identical",
      ),
      (
          ("n1", "a"),
          ("n1", "b"),
          "SAME configuration, two runs; neither ever speaks",
      ),
      (
          ("replicate", "a"),
          ("replicate", "b"),
          "SAME configuration, two runs; both spoke, so prompts diverge once"
          " they do",
      ),
      (
          ("replicate", "a"),
          ("replicate_budget0", "a"),
          "same boundaries; differ ONLY in whether speech happened",
      ),
      (
          ("replicate", "b"),
          ("replicate_budget0", "b"),
          "same boundaries; differ ONLY in whether speech happened",
      ),
      (
          ("replicate_budget0", "a"),
          ("n1", "a"),
          "neither ever speaks; differ ONLY in the boundary set",
      ),
      (
          ("replicate_budget0", "b"),
          ("n1", "b"),
          "neither ever speaks; differ ONLY in the boundary set",
      ),
  )
  for left, right, why in pairs:
    shared = shared_cursor_agreement(left, right)
    if not shared:
      continue
    before = [s for s in shared if s["left_had_spoken"] == 0]
    after = [s for s in shared if s["left_had_spoken"] > 0]
    bad_window = [s for s in shared if not s["same_evidence_in_window"]]
    comparable = [s for s in shared if None not in s["left"] + s["right"]]
    off_same = sum(1 for s in comparable if s["left"][0] == s["right"][0])
    print(
        f"- **{left[0]}/{left[1]}** vs **{right[0]}/{right[1]}** — {why}:"
        f" {len(shared)} shared cursors, evidence window differs at"
        f" {len(bad_window)}; prompt-char deltas"
        f" {sorted({s['prompt_chars_delta'] for s in shared})}"
    )
    print(
        f"  - both fields: {agree(shared)};"
        f" `off_track` alone: {off_same}/{len(comparable)}"
    )
    if after and before:
      print(
          f"  - before `{left[0]}` had spoken: {agree(before)}"
          f" ({len(before)} cursors); after: {agree(after)}"
          f" ({len(after)} cursors)"
      )

  print("\n## The recorded run — the pair for `replicate`\n")
  recorded = recorded_rows()
  spoke_rows = [r for r in recorded if r["kind"] == "spoke"]
  print(
      f"- boundaries {len(recorded)},"
      f" spoke {len(spoke_rows)} at cursors"
      f" {[r['cursor'] for r in spoke_rows]},"
      f" lapses {sum(1 for r in recorded if r['kind'] == 'lapse')},"
      f" gaps {sum(1 for r in recorded if r['kind'] == 'gap')}"
  )
  admitted = admitted_by_cursor(events)
  for row in spoke_rows:
    hits = contradicted_by_record(row["text"], row["cursor"], uses, results)
    in_window = min(admitted[row["cursor"]], replay.WINDOW)
    print(f"- cursor {row['cursor']}, evidence in window {in_window}:")
    print(f"  > {row['text']}")
    print(
        f"  - empty window: {in_window == 0}"
        f" — contradicted by record: {bool(hits)}"
        f"{' via ' + ', '.join(hits) if hits else ''}"
    )
  print("\n### Where each recorded correction reached the actor\n")
  print(
      "| written at cursor | surfaced before event | lag (events) |"
      " first proxy request carrying it |"
  )
  print("| --- | --- | --- | --- |")
  for point in delivery_points(events):
    print(
        f"| {point['written_at_cursor']} | {point['surfaced_before_event']}"
        f" | {point['lag_events']} | {point['first_proxy_request']} |"
    )

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
