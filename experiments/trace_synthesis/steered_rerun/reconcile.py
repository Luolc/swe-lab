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


def tool_results(conversation: dict[str, object]) -> list[str]:
  """Return every tool result in a converted conversation, un-numbered.

  Args:
    conversation: The typed conversation.

  Returns:
    The rendered tool-result texts, in order.
  """
  return [
      _NUMBERED.sub("", str(block.get("content", "")))
      for message in conversation["messages"]
      for block in (message.get("content") or [])
      if block.get("type") == "tool_result"
  ]


def main() -> None:
  """Print the join, and exit non-zero if any side has an unmatched row."""
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
  emitted = [(int(r["seq"]), str(r["hint"])) for r in host if r.get("hint_emitted")]

  hook_logs = glob.glob(str(frozen / "rollout/ws/a*/steer_hook.local.jsonl"))
  hook = [
      json.loads(line)
      for line in pathlib.Path(hook_logs[0]).read_text().splitlines()
      if line.strip()
  ]
  applied = {int(r["seq"]) for r in hook if r.get("applied") and "seq" in r}

  conversations = sorted(glob.glob(str(frozen / "rollout/a*/conversation.json")))
  results = tool_results(json.loads(pathlib.Path(conversations[0]).read_text()))

  print(f"host judgements        : {len(host):>3}  (emitted {len(emitted)})")
  print(f"sandbox hook asks      : {len(hook):>3}  (applied {len(applied)})")
  print(f"converted tool_results : {len(results):>3}")
  print()
  print("seq | applied in sandbox | in converted trace | tool output kept")
  intact = True
  for seq, hint in emitted:
    in_hook = seq in applied
    carrying = [text for text in results if hint in text]
    kept = bool(carrying) and bool(carrying[0].split(_OPEN)[0].strip())
    print(
        f"{seq:>3} | {'yes' if in_hook else 'NO':<18}"
        f" | {'yes' if carrying else 'NO':<18}"
        f" | {'yes' if kept else 'NO'}"
    )
    intact = intact and in_hook and bool(carrying) and kept

  orphaned = sorted(applied - {seq for seq, _ in emitted})
  unapplied = sorted({seq for seq, _ in emitted} - applied)
  print()
  print(f"applied in the sandbox with no host record : {orphaned or 'none'}")
  print(f"emitted by the host, never applied         : {unapplied or 'none'}")
  # The counts must agree too: a boundary the hook asked about and the host
  # never judged is the gap that killed the first steered run, and it is
  # invisible if you only join the *hints*.
  if len(host) != len(hook):
    print(f"BOUNDARY COUNT MISMATCH: host {len(host)} vs sandbox {len(hook)}")
  ok = intact and not orphaned and not unapplied and len(host) == len(hook)
  print("RECONCILED" if ok else "GAPS FOUND")
  raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
  main()
