"""Builds the Phase 0 toy fixture and reads back what the actor actually did.

The fixture is five tiny files, each holding a 32-hex nonce and a pointer to the
next one, in a scratch directory outside the repository. Three properties are
what the experiment needs and none of them is decoration:

- **ordered, deterministic tool calls**, so every arm has the same "same point"
  and a control arm is comparable at all;
- **a side-effect ledger** — `ledger()` reads the filesystem, which is the only
  witness that can disagree with what the session *recorded* happening;
- **unguessable nonces**, so Q1's recall check cannot pass on a value the model
  could have derived from the task text.

Usage: python toy_task.py <dir>
"""

from __future__ import annotations

import json
import pathlib
import secrets
import sys

STEPS = 5
SLEEP_SECONDS = 8

PROMPT = (
    f"This directory contains {STEPS} files named step1.txt … step{STEPS}.txt."
    " Read them one at a time, in order, starting with step1.txt."
    f" After reading each file, run `sleep {SLEEP_SECONDS}` with the Bash tool"
    " before reading the next one."
    " When you have read all of them, write the TOKEN values, one per line in"
    " order, into a file named result.txt in this directory."
)


def build(directory: pathlib.Path) -> dict[str, str]:
  """Creates the fixture, replacing any previous one.

  Args:
    directory: scratch directory to build the fixture in; created if absent.

  Returns:
    A mapping of step name to the nonce that step's file holds.
  """
  directory.mkdir(parents=True, exist_ok=True)
  for stale in directory.glob("*.txt"):
    stale.unlink()
  nonces: dict[str, str] = {}
  for index in range(1, STEPS + 1):
    nonce = secrets.token_hex(16)
    nonces[f"step{index}"] = nonce
    tail = (
        f"Next, read step{index + 1}.txt."
        if index < STEPS
        else "That was the last one."
    )
    _ = (directory / f"step{index}.txt").write_text(
        f"TOKEN {nonce}\n{tail}\n", encoding="utf-8"
    )
  _ = (directory / "nonces.json").write_text(json.dumps(nonces, indent=2))
  return nonces


def ledger(directory: pathlib.Path) -> dict[str, object]:
  """Reads the filesystem's account of how far the task got.

  This is deliberately independent of the session record: the two disagreeing
  is itself a finding (a tool whose effect landed without being recorded).

  Args:
    directory: the fixture directory.

  Returns:
    Which step files still exist, whether result.txt exists, and which nonces
    it contains in which order.
  """
  result = directory / "result.txt"
  body = result.read_text(encoding="utf-8") if result.exists() else ""
  nonces_file = directory / "nonces.json"
  nonces: dict[str, str] = (
      json.loads(nonces_file.read_text(encoding="utf-8"))
      if nonces_file.exists()
      else {}
  )
  return {
      "steps_present": sorted(
          p.name for p in directory.glob("step*.txt")
      ),
      "result_exists": result.exists(),
      "result_lines": [line.strip() for line in body.splitlines() if line.strip()],
      "nonces_found_in_result": [
          step for step, nonce in nonces.items() if nonce in body
      ],
  }


def main() -> int:
  """Builds the fixture at the path given on the command line.

  Returns:
    A process exit code.
  """
  directory = pathlib.Path(sys.argv[1])
  nonces = build(directory)
  print(json.dumps({"dir": str(directory), "steps": list(nonces)}, indent=2))
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
