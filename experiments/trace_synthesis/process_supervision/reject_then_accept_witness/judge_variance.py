"""Judge one fixed completion repeatedly, to measure the gate's own variance.

A precondition for reading the witness experiment, not a side quest. B's loop is
*judged reject -> resend -> judged accept -> stop*, so if the gate is a random
function of its input the loop can terminate on a coin flip: the second
completion need not be better, the judge need only land the other way. "The
accepted resend was better" is only meaningful against a measured variance, and
there is no such measurement yet.

Two arms, both at `max_tokens = 2000`:

- **default sampling** (n = 20) -- the gate as it actually runs, and as B would
  ship it. Any disagreement here is enough to confound the witness, whatever its
  cause;
- **`temperature = 0`** (n = 5) -- **one-directional.** A flip here *falsifies*
  "pinning the temperature fixes this gate"; five quiet calls confirm nothing,
  since the upper bound they leave is near 0.6. It is not determinism on a
  hosted endpoint, and routing and served model are recorded but not held fixed,
  so it can never attribute the variance to sampling or to the guidebook.

Usage:
  python3 <this file> --out-dir <dir> [--n 5]

Needs `OPENROUTER_API_KEYS` in the environment; `.envrc` exports it.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import importlib.util
import json
import pathlib
import sys
import time
import types

_HERE = pathlib.Path(__file__).resolve().parent
_STUDY = _HERE.parent / "guidebook_as_step_criterion"
_CAPTURE = pathlib.Path(
    "/home/ubuntu/dev/swe-lab-artifacts/trace_synthesis"
    "/baseline-qutebrowser-rollout-0/rollout/a0/claude_code.proxy_log.jsonl"
)
_STEP_INDEX = 15
_POSITION = 14
_ROLLOUT = "baseline-qutebrowser-rollout-0"
_MAX_TOKENS = 2000
# Fixed here rather than exposed as a flag: n is pre-registered, and a knob that
# changes it would undo the pre-registration. The default arm is large enough
# that a *quiet* result still bounds something -- 0/20 puts the 95% upper bound
# on the flip rate near 0.15, while 0/5 leaves it near 0.6, which reads like
# stability and says nothing.
_ARMS = (("default", {}, 20), ("temperature-0", {"temperature": 0}, 5))
# Binds the *entire* judge input, not just the completion: the prompt is built
# from the guidebook, the preceding-steps rendering and the completion summary,
# each of which lives in an off-repo or separately edited file. Asserted before
# any paid call, so drift in any of them ends the run instead of buying
# results that look normal and are no longer about the pre-registered material.
_JUDGE_INPUT_SHA256 = (
    "57d9cb24dc0b220fe366377e8d6757aa15843679da6af5a374311f77f5fbb661")


def _load(path: pathlib.Path, name: str) -> types.ModuleType:
  """Import a module by path.

  Args:
    path: The module file.
    name: Name to register the import under.

  Returns:
    The executed module.
  """
  spec = importlib.util.spec_from_file_location(name, path)
  assert spec is not None and spec.loader is not None
  module = importlib.util.module_from_spec(spec)
  sys.modules[spec.name] = module
  spec.loader.exec_module(module)
  return module


_extract = _load(_STUDY / "extract_steps.py", "variance_extract_steps")
_judge = _load(_STUDY / "judge_steps.py", "variance_judge_steps")
# Reused so the two runs render the judge's prompt from one implementation; a
# second copy would let the digest bind text this script no longer sends.
_witness = _load(_HERE / "witness.py", "variance_witness")


def main() -> None:
  """Judge the fixed completion `n` times in each arm."""
  parser = argparse.ArgumentParser()
  parser.add_argument("--out-dir", type=pathlib.Path, required=True)
  args = parser.parse_args()
  args.out_dir.mkdir(parents=True, exist_ok=True)

  rows = [json.loads(l) for l in _CAPTURE.read_text().splitlines()]
  message = (rows[_STEP_INDEX].get("response") or {}).get("message") or {}
  steps = _extract.extract(_ROLLOUT)
  before = _witness.preceding(steps, _POSITION)
  # The same renderer the witness uses. A second copy here would let the digest
  # below bind text this script no longer sends.
  user = _witness.judge_prompt(message, before, len(steps))

  observed = hashlib.sha256(json.dumps(
      {"system": _judge.INSTRUCTIONS, "user": user},
      sort_keys=True, separators=(",", ":")).encode()).hexdigest()
  (args.out_dir / "judge_input.json").write_text(json.dumps({
      "classification": None if observed == _JUDGE_INPUT_SHA256 else "void",
      "judge_input_sha256_expected": _JUDGE_INPUT_SHA256,
      "judge_input_sha256_observed": observed,
  }, indent=2))
  if observed != _JUDGE_INPUT_SHA256:
    raise SystemExit(
        "void: the judge input does not match the pre-registered digest, so "
        "this run did not happen. Nothing was spent. See "
        f"{args.out_dir / 'judge_input.json'}")

  records, spent = [], 0.0
  for arm, extra, count in _ARMS:
    for index in range(count):
      payload = {
          "model": _judge.MODEL,
          "max_tokens": _MAX_TOKENS,
          "messages": [
              {"role": "system", "content": _judge.INSTRUCTIONS},
              {"role": "user", "content": user},
          ],
          **extra,
      }
      response = _judge.call(payload)
      raw = response["choices"][0]["message"]["content"]
      usage = response.get("usage") or {}
      spent += float(usage.get("cost") or 0)
      records.append({
          "arm": arm,
          "index": index,
          "verdict": _verdict(raw),
          "stage": _field(raw, "stage"),
          "quote": _field(raw, "quote"),
          "reason": _field(raw, "reason"),
          "raw": raw,
          "response_model": response.get("model"),
          # Recorded, not controlled: routing is free to vary between calls, so
          # an arm's behaviour is not attributable to its temperature.
          "provider": response.get("provider"),
          "sampling_sent": _judge.sampling_sent(payload),
          "max_tokens": _MAX_TOKENS,
          "usage": usage,
          "at": time.strftime("%FT%TZ", time.gmtime()),
      })
      print(f"{arm} {index}: verdict={records[-1]['verdict']} "
            f"stage={records[-1]['stage']} model={response.get('model')} "
            f"spent=${spent:.4f}")

  (args.out_dir / "judgements.jsonl").write_text(
      "".join(json.dumps(r) + "\n" for r in records))
  summary = {
      "position": _POSITION,
      "step_index": _STEP_INDEX,
      "max_tokens": _MAX_TOKENS,
      "total_usd": round(spent, 6),
      "arms": {
          arm: dict(collections.Counter(
              r["verdict"] for r in records if r["arm"] == arm))
          for arm, _extra, _count in _ARMS
      },
      "stages_cited": {
          arm: dict(collections.Counter(
              r["stage"] for r in records if r["arm"] == arm))
          for arm, _extra, _count in _ARMS
      },
      "response_models": sorted(
          {str(r["response_model"]) for r in records}),
      "providers": sorted({str(r["provider"]) for r in records}),
      "n_per_arm": {arm: count for arm, _extra, count in _ARMS},
      "_note": (
          "Counts only; no rate is implied. A quiet arm bounds rather than "
          "establishes: 0 disagreements in n puts the 95% upper bound on the "
          "flip rate near 3/n, which is NOT a finding of stability. "
          "temperature-0 can falsify 'pinning the temperature fixes this gate' "
          "and can never confirm it: routing and served model are recorded but "
          "not controlled."
      ),
  }
  (args.out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
  print(json.dumps(summary, indent=2))


def _verdict(raw: str) -> str | None:
  """Read the verdict from a judge answer.

  Args:
    raw: The answer text.

  Returns:
    The verdict, or None when the answer cannot be parsed.
  """
  return _field(raw, "verdict")


def _field(raw: str, name: str) -> object:
  """Read one field from a judge answer.

  Args:
    raw: The answer text.
    name: The field to read.

  Returns:
    The field's value, or None when the answer cannot be parsed.
  """
  try:
    parsed = json.loads(raw)
  except (json.JSONDecodeError, TypeError):
    return None
  return parsed.get(name) if isinstance(parsed, dict) else None


if __name__ == "__main__":
  main()
