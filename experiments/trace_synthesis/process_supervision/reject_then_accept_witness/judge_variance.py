"""Judge one fixed completion 20 times, to characterise the gate's flip rate.

A cheap characterisation, not a gate and not a precondition: it blocks nothing.
Whether the guidebook adjudicates a step is a **reasoned, subjective call** --
more than one reading can be defensible -- so identical answers across runs are
not required of the judge and their absence is not a defect. What is worth
knowing is simply *how often* the verdict flips on a byte-identical input, as a
number to carry into later work.

One arm, 20 calls at `max_tokens = 2000` under the provider's default sampling:
the gate as it actually runs.

Reporting discipline, which is what this measurement mostly exists to honour:
the result is a **count**. A quiet outcome is reported as `observed 0
disagreements in 20 calls`, never as "the gate is stable", and no confidence
bound is asserted -- the familiar 3/n interval needs independent, identically
distributed trials at a stationary rate, and routing and served model are
recorded here but not controlled.

Usage:
  python3 <this file> --out-dir <dir>

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
    "~/dev/swe-lab-artifacts/trace_synthesis"
    "/baseline-qutebrowser-rollout-0/rollout/a0/claude_code.proxy_log.jsonl"
).expanduser()
_STEP_INDEX = 15
_POSITION = 14
_ROLLOUT = "baseline-qutebrowser-rollout-0"
_MAX_TOKENS = 2000
# Fixed here rather than exposed as a flag: n is pre-registered, and a knob that
# Fixed rather than exposed as a flag: a knob that changes a pre-registered
# quantity undoes the pre-registration. 20 buys 20 observations cheaply and
# nothing more -- no confidence bound and no detection claim, since routing and
# served model are recorded but not controlled.
_CALLS = 20
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
  """Judge the fixed completion 20 times under default sampling."""
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
  for index in range(_CALLS):
      payload = {
          "model": _judge.MODEL,
          "max_tokens": _MAX_TOKENS,
          "messages": [
              {"role": "system", "content": _judge.INSTRUCTIONS},
              {"role": "user", "content": user},
          ],
      }
      response = _judge.call(payload)
      raw = response["choices"][0]["message"]["content"]
      usage = response.get("usage") or {}
      spent += float(usage.get("cost") or 0)
      records.append({
          "index": index,
          "verdict": _verdict(raw),
          "stage": _field(raw, "stage"),
          "quote": _field(raw, "quote"),
          "reason": _field(raw, "reason"),
          "raw": raw,
          "response_model": response.get("model"),
          # Recorded, not controlled: routing is free to vary between calls.
          "provider": response.get("provider"),
          "sampling_sent": _judge.sampling_sent(payload),
          "max_tokens": _MAX_TOKENS,
          "usage": usage,
          "at": time.strftime("%FT%TZ", time.gmtime()),
      })
      print(f"{index}: verdict={records[-1]['verdict']} "
            f"stage={records[-1]['stage']} model={response.get('model')} "
            f"spent=${spent:.4f}")

  (args.out_dir / "judgements.jsonl").write_text(
      "".join(json.dumps(r) + "\n" for r in records))
  summary = {
      "position": _POSITION,
      "step_index": _STEP_INDEX,
      "max_tokens": _MAX_TOKENS,
      "total_usd": round(spent, 6),
      "verdicts": dict(collections.Counter(r["verdict"] for r in records)),
      "stages_cited": dict(collections.Counter(r["stage"] for r in records)),
      "response_models": sorted(
          {str(r["response_model"]) for r in records}),
      "providers": sorted({str(r["provider"]) for r in records}),
      "calls": _CALLS,
      "_note": (
          "Counts only; no rate and no confidence bound are implied. A quiet "
          "result is reported as 'observed 0 disagreements in 20 calls' and "
          "nothing further: the familiar 3/n bound needs iid trials at a "
          "stationary rate, which this design does not establish, since "
          "routing and served model are recorded but not controlled."
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
