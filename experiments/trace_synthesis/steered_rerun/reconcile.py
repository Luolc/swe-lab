#!/usr/bin/env python3
"""Three-way reconciliation: did every hint survive, and can we prove it?

[Spec §11](../../../docs/trace-synthesis/spec.md#11-open-questions)'s one fatal
failure mode is a hint disappearing **silently**. A host-side log alone does not
rule that out — a recorder that dies stops writing rather than recording its own
death, which this round measured the hard way. What does rule it out is joining
three independent records of the same rollout and requiring no unmatched row on
any side:

1. the **host's** judgement log (`runs/<label>/hint_log.jsonl`) — what the
   Supervisor decided, written outside the sandbox;
2. the **sandbox's** hook log (`rollout/ws/a*/steer_hook.local.jsonl`) — what
   the hook was asked and what it managed to apply, written inside the
   container by a process that holds nothing;
3. the **converted** `Conversation` — what a consumer of the trace would
   actually see.

Each is produced by a different process at a different trust boundary, so a
silent loss would have to corrupt all three consistently to hide.

**Counts are not a join.** An earlier version of this program compared only the
host and hook boundary counts and printed ``RECONCILED`` on a converted trace
with a boundary deleted — the reviewer of PR #265 reproduced it as
``host 27, hook 27, converted 26, RECONCILED, exit 0``. Equal cardinality is a
necessary condition and nothing more: a converted result that is dropped, or one
that is duplicated over its neighbour, keeps every count intact.

So the check is a **per-boundary bijection**, on the strongest identity the
three records share:

* ``tool_use_id`` when it is present in all three — the same value the harness
  puts on every ``tool_result``, now recorded by the hook and carried through
  the Supervisor. This is an exact join: a dropped or duplicated boundary breaks
  it regardless of counts.
* otherwise **position plus tool name**, which is what runs recorded before the
  hook logged the id can offer. Weaker, and the output says so rather than
  letting a positional match read as an identity match.

On top of the join, the hint check is **positional and exclusive**: the
converted result at boundary *k* must carry the host's hint for *k* **and no
other boundary may carry one**. That is what catches a duplicated hint-bearing
result pasted over an unhinted boundary, which a "the hint appears somewhere"
check cannot see.


Usage::

  direnv exec . uv run python experiments/trace_synthesis/steered_rerun/reconcile.py \\
      --frozen /home/ubuntu/dev/swe-lab-artifacts/trace_synthesis/<label>-rollout-<n> \\
      --label <label> --session <label>-r<n>
"""

from __future__ import annotations

import argparse
import glob
import json
import pathlib
import re

_HERE = pathlib.Path(__file__).resolve().parent

_OPEN = "<oracle_hint>"

# `Read` renders its file with `<n>\t` prefixes and the hint is appended into
# that same field, so the injected block comes back numbered like file content.
_NUMBERED = re.compile(r"^\s*\d+\t", re.M)


def boundaries(conversation: dict[str, object]) -> list[dict[str, object]]:
  """Return the converted trace's tool boundaries, in order.

  A ``tool_result`` names the call it answers by ``tool_use_id``; the matching
  ``tool_use`` block names the tool. Pairing them gives each converted boundary
  an identity and a tool name, which is what the join needs.

  Args:
    conversation: The typed conversation.

  Returns:
    One row per boundary: ``tool_use_id``, ``tool``, and the rendered text with
    `Read`'s line-number prefixes stripped.
  """
  names: dict[str, str] = {}
  for message in conversation["messages"]:
    for block in message.get("content") or []:
      if block.get("type") == "tool_use":
        names[str(block.get("id"))] = str(block.get("name", ""))
  rows: list[dict[str, object]] = []
  for message in conversation["messages"]:
    for block in message.get("content") or []:
      if block.get("type") != "tool_result":
        continue
      identity = str(block.get("tool_use_id", ""))
      rows.append({
          "tool_use_id": identity,
          "tool": names.get(identity, ""),
          "text": _NUMBERED.sub("", str(block.get("content", ""))),
      })
  return rows


def join(
    host: list[dict[str, object]],
    hook: list[dict[str, object]],
    converted: list[dict[str, object]],
) -> tuple[list[tuple[dict[str, object], dict[str, object], dict[str, object]]], list[str], str]:
  """Match the three records boundary by boundary.

  Args:
    host: The Supervisor's judgements for this session, in order.
    hook: The in-sandbox hook's records, in order.
    converted: The converted trace's boundaries, in order.

  Returns:
    The matched triples, the problems found, and which identity was used.
  """
  problems: list[str] = []
  counts = {"host": len(host), "hook": len(hook), "converted": len(converted)}
  if len(set(counts.values())) != 1:
    problems.append(f"boundary counts disagree: {counts}")

  # Prefer the real identity. Every `tool_result` carries `tool_use_id`; the
  # hook and the host carry it only for runs recorded after it was added, so
  # the fallback is position and it is announced rather than assumed.
  ids_everywhere = (
      all(record.get("tool_use_id") for record in hook)
      and all(record.get("tool_use_id") for record in host)
      and all(row["tool_use_id"] for row in converted)
  )
  identity = "tool_use_id" if ids_everywhere else "position + tool name"

  if ids_everywhere:
    by_id = {str(row["tool_use_id"]): row for row in converted}
    if len(by_id) != len(converted):
      problems.append("converted trace repeats a tool_use_id")
    for host_row, hook_row in zip(host, hook, strict=False):
      key = str(host_row.get("tool_use_id"))
      if key != str(hook_row.get("tool_use_id")):
        problems.append(f"host/hook disagree on identity at seq {host_row.get('seq')}")
      if key not in by_id:
        problems.append(f"boundary {key} is absent from the converted trace")
    triples = [
        (host_row, hook_row, by_id[str(host_row.get("tool_use_id"))])
        for host_row, hook_row in zip(host, hook, strict=False)
        if str(host_row.get("tool_use_id")) in by_id
    ]
    return triples, problems, identity

  triples = []
  for index, (host_row, hook_row, converted_row) in enumerate(
      zip(host, hook, converted, strict=False)
  ):
    tools = {str(host_row.get("tool")), str(hook_row.get("tool")), str(converted_row["tool"])}
    if len(tools) != 1:
      problems.append(f"boundary {index}: tools disagree {sorted(tools)}")
    triples.append((host_row, hook_row, converted_row))
  return triples, problems, identity


def reconcile(
    host: list[dict[str, object]],
    hook: list[dict[str, object]],
    converted: list[dict[str, object]],
) -> tuple[list[str], list[str], str]:
  """Reconcile the three records and return everything wrong with them.

  Args:
    host: The Supervisor's judgements for one session, in order.
    hook: The in-sandbox hook's records, in order.
    converted: The converted trace's boundaries, from ``boundaries``.

  Returns:
    The problems found, one printable row per hint-bearing boundary, and the
    identity the join used.
  """
  triples, problems, identity = join(host, hook, converted)
  rows: list[str] = []
  for host_row, hook_row, converted_row in triples:
    seq = host_row.get("seq")
    expected = str(host_row["hint"]) if host_row.get("hint_emitted") else ""
    text = str(converted_row["text"])
    present = expected in text if expected else _OPEN in text
    kept = bool(text.split(_OPEN)[0].strip())
    tool = str(converted_row["tool"])[:6]
    if expected:
      if not present:
        problems.append(f"seq {seq}: hint missing from the converted trace")
      if not hook_row.get("applied"):
        problems.append(f"seq {seq}: host emitted, sandbox did not apply")
      if present and not kept:
        problems.append(f"seq {seq}: the tool's own output is gone")
      rows.append(
          f"{seq:>3} | {tool:<6} | {'yes' if hook_row.get('applied') else 'NO':<7}"
          f" | yes           | {'yes' if present else 'NO':<12}"
          f" | {'yes' if kept else 'NO'}"
      )
    elif present:
      # A hint where the host judged none: either the sandbox applied one the
      # host never recorded, or a hint-bearing result was duplicated over this
      # boundary. Both are exactly what equal counts cannot see.
      problems.append(
          f"seq {seq}: the trace carries a hint the host never emitted"
      )
      rows.append(
          f"{seq:>3} | {tool:<6} | {'yes' if hook_row.get('applied') else 'no':<7}"
          " | no            | UNEXPECTED   | -"
      )

  applied = {int(r["seq"]) for r in hook if r.get("applied") and "seq" in r}
  emitted = {int(r["seq"]) for r in host if r.get("hint_emitted")}
  problems += [
      f"seq {seq}: applied in the sandbox with no host record"
      for seq in sorted(applied - emitted)
  ]
  problems += [
      f"seq {seq}: emitted by the host, never applied"
      for seq in sorted(emitted - applied)
  ]
  return problems, rows, identity


def main() -> None:
  """Print the join, and exit non-zero if anything failed to reconcile."""
  parser = argparse.ArgumentParser(description=__doc__)
  _ = parser.add_argument("--frozen", required=True)
  _ = parser.add_argument("--label", required=True)
  _ = parser.add_argument("--session", required=True)
  args = parser.parse_args()

  frozen = pathlib.Path(args.frozen)
  host = [
      record
      for line in (_HERE / "runs" / args.label / "hint_log.jsonl").read_text().splitlines()
      if line.strip()
      for record in [json.loads(line)]
      if record.get("session") == args.session
  ]
  hook_logs = glob.glob(str(frozen / "rollout/ws/a*/steer_hook.local.jsonl"))
  hook = [
      json.loads(line)
      for line in pathlib.Path(hook_logs[0]).read_text().splitlines()
      if line.strip()
  ]
  conversations = sorted(glob.glob(str(frozen / "rollout/a*/conversation.json")))
  converted = boundaries(json.loads(pathlib.Path(conversations[0]).read_text()))

  problems, rows, identity = reconcile(host, hook, converted)

  print(f"host judgements        : {len(host):>3}")
  print(f"sandbox hook asks      : {len(hook):>3}")
  print(f"converted tool_results : {len(converted):>3}")
  print(f"joined on              : {identity}")
  if identity != "tool_use_id":
    print("  (positional: this run predates the hook recording tool_use_id,")
    print("   so a boundary swapped for another of the same tool would pass)")
  print()
  print("seq | tool | applied | hint expected | hint present | output kept")
  for row in rows:
    print(row)
  print()
  for problem in problems:
    print(f"GAP: {problem}")
  print("RECONCILED" if not problems else f"GAPS FOUND ({len(problems)})")
  raise SystemExit(0 if not problems else 1)


if __name__ == "__main__":
  main()
