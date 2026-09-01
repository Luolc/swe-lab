"""Build the committed witness for a run, and refuse to emit operator data.

The raw capture stays off-repo (`docs/conventions.md`, "What may be committed as
evidence"): a proxy log carries the operator's home path, git identity and the
whole conversation. What is committed is the minimum needed to re-derive the
run's label without it — the label, the indices it was computed from, the
correction as delivered, and the actor's next action — plus the digest of the
raw file, so the witness can be tied back to a capture that still exists on the
machine that made it.

    ./evidence.py runs/graded/mid/run_tests_first     # write evidence.json
    ./evidence.py --check runs/graded/mid/*           # verify committed ones
    ./evidence.py --scan-tracked                      # scan the whole checkout
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import subprocess
import sys
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import criterion

_HOME = str(pathlib.Path.home())
_HOME_SLUG = _HOME.replace("/", "-")

# Credential shapes, an operator path, and a git identity: anything matching is a
# blocker rather than something to redact quietly, because a match means the
# builder did not understand its input.
_BLOCKERS = (
    ("home path", re.compile(re.escape(_HOME))),
    ("home slug", re.compile(re.escape(_HOME_SLUG))),
    ("anthropic key", re.compile(r"sk-ant-[A-Za-z0-9_-]{8,}")),
    ("bearer token", re.compile(r"[Bb]earer\s+[A-Za-z0-9._-]{16,}")),
    ("long mixed run", re.compile(r"\b(?=[A-Za-z0-9_-]*[a-z])(?=[A-Za-z0-9_-]*[A-Z])(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]{40,}\b")),
)


def git_identity() -> list[str]:
  """Return the local git name and email, so they can be redacted out."""
  values = []
  for key in ("user.name", "user.email"):
    result = subprocess.run(
        ["git", "config", "--get", key], capture_output=True, text=True, check=False
    )
    if result.stdout.strip():
      values.append(result.stdout.strip())
  return values


def redact(text: str) -> str:
  out = text.replace(_HOME_SLUG, "<home-slug>").replace(_HOME, "<home>")
  for value in git_identity():
    out = out.replace(value, "<operator>")
  return out


def blockers(text: str) -> list[str]:
  return [name for name, pattern in _BLOCKERS if pattern.search(text)]


def digest(path: pathlib.Path) -> dict[str, Any]:
  raw = path.read_bytes()
  return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def build(run_dir: pathlib.Path) -> dict[str, Any]:
  """Return the witness for one run: its label, and what produced it."""
  result = criterion.classify(run_dir)
  manifest = json.loads((run_dir / "manifest.json").read_text())
  loop = criterion.agent_loop_records(
      criterion.records(run_dir / "proxy.jsonl")
  )

  witness: dict[str, Any] = {
      "run": str(run_dir.relative_to(run_dir.parents[2])),
      "arm": result["arm"],
      "fixture": result["fixture"],
      "phase": manifest.get("phase"),
      "label": result["label"],
      "claude_version": manifest.get("claude_version"),
      "model": "sonnet",
      "indices": {
          key: result[key]
          for key in (
              "agent_loop_calls",
              "trigger_index",
              "evaluation_index",
              "delivery_lag",
              "action_index",
          )
          if key in result
      },
      "correction_sent": manifest.get("correction_sent"),
      "action": result.get("action"),
      "raw_capture": digest(run_dir / "proxy.jsonl"),
  }

  # The one piece of the conversation kept verbatim: the system block that
  # carried the correction. It is what §4.3 and §5 are both about, and it is
  # text this experiment wrote rather than anything the operator typed.
  index = result.get("evaluation_index")
  if isinstance(index, int) and index < len(loop):
    body = loop[index].get("request", {}).get("body", {})
    messages = body.get("messages") or []
    carrying = [
        {"role": m.get("role"), "text": block.get("text")}
        for m in messages
        if isinstance(m, dict)
        for block in (
            m.get("content") if isinstance(m.get("content"), list) else []
        )
        if isinstance(block, dict) and criterion.MARKER in str(block.get("text"))
    ]
    witness["delivered_as"] = carrying

  return json.loads(redact(json.dumps(witness)))


def emit(run_dir: pathlib.Path) -> pathlib.Path:
  witness = build(run_dir)
  text = json.dumps(witness, indent=2, sort_keys=True) + "\n"
  found = blockers(text)
  if found:
    raise SystemExit(f"{run_dir}: refusing to write, found {found}")
  path = run_dir / "evidence.json"
  _ = path.write_text(text)
  return path


def scan_tracked() -> int:
  """Scan every git-tracked file, so a leak cannot hide in a stale artifact."""
  listing = subprocess.run(
      ["git", "ls-files"], capture_output=True, text=True, check=True
  ).stdout.split()
  bad = 0
  for name in listing:
    path = pathlib.Path(name)
    if not path.is_file():
      continue
    try:
      text = path.read_text()
    except UnicodeDecodeError:
      continue
    found = blockers(text)
    if found:
      print(f"{name}: {found}")
      bad += 1
  print("clean" if not bad else f"{bad} file(s) with blockers")
  return 1 if bad else 0


def main() -> int:
  parser = argparse.ArgumentParser()
  _ = parser.add_argument("runs", nargs="*")
  _ = parser.add_argument("--check", action="store_true")
  _ = parser.add_argument("--scan-tracked", action="store_true")
  args = parser.parse_args()

  if args.scan_tracked:
    return scan_tracked()

  bad = 0
  for run in args.runs:
    run_dir = pathlib.Path(run)
    if args.check:
      committed = json.loads((run_dir / "evidence.json").read_text())
      if committed != build(run_dir):
        print(f"{run_dir}: committed evidence differs from a rebuild")
        bad += 1
    else:
      print(emit(run_dir))
  return 1 if bad else 0


if __name__ == "__main__":
  raise SystemExit(main())
