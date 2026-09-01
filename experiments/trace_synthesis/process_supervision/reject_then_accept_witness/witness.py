"""Re-issue one identical actor request K times and judge each completion.

Implements `DEBATE-VERDICT.md` §4 under the readings fixed in
`PRE-REGISTRATION.md`. Nothing here decides what an outcome means; that is
already written down.

The proxy is built and started by this program rather than by a documented shell
sequence, so there is no separate recipe that can drift from what actually ran.

Usage:
  python3 <this file> --out-dir <dir> [--k 10]

Needs `OPENROUTER_API_KEYS` in the environment; `.envrc` exports it.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import pathlib
import socket
import subprocess
import sys
import time
import types
import urllib.request

_HERE = pathlib.Path(__file__).resolve().parent
_STUDY = _HERE.parent / "guidebook_as_step_criterion"
_PROXY_SOURCE = pathlib.Path("/home/ubuntu/dev/cc-reverse-proxy/reverse_proxy.go")
_CAPTURE = pathlib.Path(
    "/home/ubuntu/dev/swe-lab-artifacts/trace_synthesis"
    "/baseline-qutebrowser-rollout-0/rollout/a0/claude_code.proxy_log.jsonl"
)
# The step fixed in the pre-registration: #305's first off-track step.
_STEP_INDEX = 15
_POSITION = 14
_ROLLOUT = "baseline-qutebrowser-rollout-0"
_UPSTREAM = "https://openrouter.ai/api"
_COST_CEILING_USD = 2.00
# Pre-registered digests. Canonical form is
# json.dumps(value, sort_keys=True, separators=(",", ":")).
_BODY_SHA256 = "072544ccd33384d33b280bdafed44b159685cebf5af661426654a37b0d41fd45"
_ORIGINAL_COMPLETION_SHA256 = (
    "e12278e8927ef3100498462c19218a8946bda1517bc910103403abf60aad877a")


def canonical(value: object) -> bytes:
  """Serialize a value the way the pre-registered digests were taken.

  Args:
    value: Any JSON-serializable value.

  Returns:
    The canonical bytes.
  """
  return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


class Ledger:
  """Every billable call, and whether the ceiling has been crossed.

  The stop rule is worthless if it covers only some of the calls it authorizes,
  so actor calls, judge calls, repeat judgements and the cache-off confirmation
  all land here, and the ceiling is consulted *before* issuing the next one.

  Attributes:
    entries: One record per billable call.
    path: Where the ledger is persisted after each entry.
  """

  def __init__(self, path: pathlib.Path):
    self.entries: list[dict] = []
    self.path = path

  def record(self, kind: str, usage: dict | None) -> None:
    """Add one call.

    Args:
      kind: What was paid for.
      usage: The provider's usage block, if any.
    """
    self.entries.append(
        {"kind": kind, "cost": float((usage or {}).get("cost") or 0),
         "at": time.strftime("%FT%TZ", time.gmtime())})
    self.path.write_text(json.dumps(
        {"total_usd": self.total, "entries": self.entries}, indent=2))

  @property
  def total(self) -> float:
    """Cumulative measured cost.

    Returns:
      The sum over every recorded call.
    """
    return sum(e["cost"] for e in self.entries)

  @property
  def exhausted(self) -> bool:
    """Whether the ceiling has been crossed.

    Returns:
      True when no further authorized call may be issued.
    """
    return self.total > _COST_CEILING_USD


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


# Reused, not reimplemented: the same judge and the same step rendering the
# #305 study used, so "the same guidebook judge" is literally the same code.
_extract = _load(_STUDY / "extract_steps.py", "witness_extract_steps")
_judge = _load(_STUDY / "judge_steps.py", "witness_judge_steps")


def _free_port() -> int:
  """Return a port the proxy can bind.

  Returns:
    A port number free at the moment of the call.
  """
  with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    return int(s.getsockname()[1])


def _start_proxy(out_dir: pathlib.Path) -> tuple[subprocess.Popen[bytes], int, pathlib.Path]:
  """Build and start the Go proxy.

  Args:
    out_dir: Directory for the binary and the proxy's own log.

  Returns:
    The process, its port, and the path it logs to.

  Raises:
    RuntimeError: If the proxy does not accept connections in time.
  """
  binary = out_dir / "cc-reverse-proxy"
  _ = subprocess.run(
      ["go", "build", "-o", str(binary), str(_PROXY_SOURCE)], check=True)
  port, log = _free_port(), out_dir / "proxy_log.jsonl"
  process = subprocess.Popen([
      str(binary), "--target", _UPSTREAM, "--port", str(port),
      "--output", str(log),
      # Pinned: an upstream retry inside the proxy would otherwise be an
      # invisible extra attempt.
      "--max-retries", "0",
  ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
  for _attempt in range(50):
    try:
      with socket.create_connection(("127.0.0.1", port), timeout=0.2):
        return process, port, log
    except OSError:
      time.sleep(0.1)
  process.kill()
  raise RuntimeError("the proxy never accepted a connection")


def _without_cache_control(body: dict) -> dict:
  """Return the body with every ``cache_control`` marker removed.

  Args:
    body: The request body.

  Returns:
    A copy carrying no cache directives.
  """
  if isinstance(body, dict):
    return {k: _without_cache_control(v)
            for k, v in body.items() if k != "cache_control"}
  if isinstance(body, list):
    return [_without_cache_control(v) for v in body]
  return body


def _judge_completion(message: dict, before: str, total: int) -> dict:
  """Ask the #305 judge whether the guidebook adjudicates this completion.

  Args:
    message: The assembled assistant message.
    before: Rendering of the preceding steps.
    total: Step count in the rollout, for the prompt's wording.

  Returns:
    The judge's raw answer and usage.
  """
  user = (
      f"# Guidebook\n\n{_judge.GUIDEBOOK.read_text()}\n\n"
      f"# Preceding steps (most recent last)\n{before}\n\n"
      f"# The step to judge (step {_POSITION} of {total})\n"
      f"{_extract.summarize(message)}"
  )
  response = _judge.call({
      "model": _judge.MODEL,
      "max_tokens": 2000,
      "messages": [
          {"role": "system", "content": _judge.INSTRUCTIONS},
          {"role": "user", "content": user},
      ],
  })
  return {
      "raw": response["choices"][0]["message"]["content"],
      "usage": response.get("usage") or {},
  }


def main() -> None:
  """Run the witness."""
  parser = argparse.ArgumentParser()
  parser.add_argument("--out-dir", type=pathlib.Path, required=True)
  parser.add_argument("--k", type=int, default=10)
  args = parser.parse_args()
  args.out_dir.mkdir(parents=True, exist_ok=True)

  rows = [json.loads(l) for l in _CAPTURE.read_text().splitlines()]
  body = rows[_STEP_INDEX]["request"]["body"]
  original = (rows[_STEP_INDEX].get("response") or {}).get("message") or {}

  # The capture is off-repo and mutable, so an address does not fix content:
  # checked before any paid call, and both digests are written to the results.
  observed_body = hashlib.sha256(canonical(body)).hexdigest()
  observed_original = hashlib.sha256(
      canonical(original.get("content"))).hexdigest()
  matches = (observed_body == _BODY_SHA256
             and observed_original == _ORIGINAL_COMPLETION_SHA256)
  (args.out_dir / "material.json").write_text(json.dumps({
      # `void` is its own termination: not outcome 2 (ran, nothing accepted) and
      # not `inconclusive` (ran out of budget) -- there was no run.
      "classification": None if matches else "void",
      "body_sha256_expected": _BODY_SHA256,
      "body_sha256_observed": observed_body,
      "original_completion_sha256_expected": _ORIGINAL_COMPLETION_SHA256,
      "original_completion_sha256_observed": observed_original,
      "messages": len(body.get("messages") or []),
      "tools": len(body.get("tools") or []),
      "model": body.get("model"),
  }, indent=2))
  if not matches:
    raise SystemExit(
        "void: the material does not match the pre-registered digests, so this "
        "run did not happen. It is not outcome 2 and not inconclusive; nothing "
        f"was spent. See {args.out_dir / 'material.json'}")

  # Serialized once; every attempt sends this exact string.
  payload = json.dumps(body).encode()
  payload_sha = hashlib.sha256(payload).hexdigest()

  steps = _extract.extract(_ROLLOUT)
  before = "\n".join(
      f"  step {i}: {s['content'][:120]}"
      for i, s in enumerate(steps[:_POSITION][-8:])
  ) or "  (none -- this is the first step)"

  ledger = Ledger(args.out_dir / "ledger.json")

  # 8.4 -- attempt 0: re-judge the original completion with *this* judge, so
  # the premise is not inherited from #305's run.
  attempt_zero = _judge_completion(original, before, len(steps))
  ledger.record("judge:attempt-0", attempt_zero["usage"])
  try:
    still_off = json.loads(attempt_zero["raw"]).get("verdict") == "off_track"
  except json.JSONDecodeError:
    still_off = False
  (args.out_dir / "attempt-0-original.json").write_text(
      json.dumps({"judge": attempt_zero, "still_off_track": still_off}, indent=2))
  print(f"attempt 0 (original completion) still_off_track={still_off}")
  if not still_off:
    # `material-retired`, not `void`: the material is ours, but this judge no
    # longer rejects the step -- itself a result about judge stability.
    (args.out_dir / "classification.json").write_text(json.dumps(
        {"classification": "material-retired",
         "next_step": "baseline-qutebrowser-rollout-0 position 26"}, indent=2))
    print("material-retired: this judge no longer calls the step off-track. "
          "Stopping, per pre-registration 8.4.")
    return

  process, port, proxy_log = _start_proxy(args.out_dir)
  records, seen, inconclusive = [], 0, False
  try:
    for attempt in range(args.k):
      if ledger.exhausted:
        inconclusive = True
        break
      started = time.time()
      request = urllib.request.Request(
          f"http://127.0.0.1:{port}/v1/messages", data=payload,
          headers={
              "Content-Type": "application/json",
              "Anthropic-Version": "2023-06-01",
              "X-Api-Key": _judge.key_pool()[0],
          },
      )
      with urllib.request.urlopen(request, timeout=600) as response:
        sse = response.read()
      (args.out_dir / f"attempt-{attempt}.sse").write_bytes(sse)

      # The proxy assembles the stream; read its record rather than write a
      # second assembler.
      logged = [json.loads(l) for l in proxy_log.read_text().splitlines()]
      new = logged[seen:]
      seen = len(logged)
      message = (new[-1].get("response") or {}).get("message") or {} if new else {}
      usage = message.get("usage") or {}
      headers = (new[-1].get("response") or {}).get("headers") or {} if new else {}
      completion = json.dumps(message.get("content"), sort_keys=True).encode()

      # Recorded before the next paid call, not after it: an actor response that
      # crosses the ceiling must stop the judge request, and a cost known only
      # after the judge has already been billed enforces nothing.
      ledger.record(f"actor:attempt-{attempt + 1}", usage)
      if ledger.exhausted:
        inconclusive = True
        records.append({
            "attempt": attempt,
            "sent_body_sha256": payload_sha,
            "completion_sha256": hashlib.sha256(completion).hexdigest(),
            "usage": usage,
            "judge": None,
            "accepted": False,
            "stopped_before_judging": True,
        })
        break

      verdict = _judge_completion(message, before, len(steps))
      ledger.record(f"judge:attempt-{attempt + 1}", verdict["usage"])
      try:
        accepted = json.loads(verdict["raw"]).get("verdict") == "on_track"
      except json.JSONDecodeError:
        accepted = False
      records.append({
          "attempt": attempt,
          "sent_body_sha256": payload_sha,
          "completion_sha256": hashlib.sha256(completion).hexdigest(),
          "completion_bytes": len(completion),
          "sse_bytes": len(sse),
          "stop_reason": message.get("stop_reason"),
          "provider_name": headers.get("X-Provider-Name"),
          "generation_id": headers.get("X-Generation-Id"),
          "request_id": headers.get("request-id"),
          "cf_ray": headers.get("Cf-Ray"),
          "usage": usage,
          "judge": verdict,
          "accepted": accepted,
          "proxy_max_retries": 0,
          "wall_seconds": round(time.time() - started, 2),
          "at": time.strftime("%FT%TZ", time.gmtime()),
      })
      print(f"attempt {attempt} accepted={accepted} "
            f"provider={records[-1]['provider_name']} spent=${ledger.total:.4f}")
      if accepted:
        # 8.3 -- an accept that does not reproduce is not a witness.
        repeats = []
        for _repeat in range(2):
          if ledger.exhausted:
            inconclusive = True
            break
          answer = _judge_completion(message, before, len(steps))
          ledger.record("judge:repeat", answer["usage"])
          repeats.append(answer)
        agreed = 1 + sum(
            1 for r in repeats
            if (json.loads(r["raw"]).get("verdict") if r["raw"].strip().startswith("{")
                else None) == "on_track")
        records[-1]["judge_repeats"] = repeats
        records[-1]["accepted_of_3"] = agreed
        print(f"  re-judged the same completion: accepted {agreed} of 3")
        break
      if ledger.exhausted:
        inconclusive = True
        break
  finally:
    process.terminate()

  # 8.2 -- outcome 1 has two mechanisms; this separates them. Authorized in the
  # pre-registration, not decided after seeing the result.
  shas = {r["completion_sha256"] for r in records}
  if len(records) == args.k and len(shas) == 1 and not ledger.exhausted:
    print("all K identical -- running the authorized cache-off confirmation")
    process, port, proxy_log = _start_proxy(args.out_dir)
    try:
      stripped = json.dumps(_without_cache_control(body)).encode()
      request = urllib.request.Request(
          f"http://127.0.0.1:{port}/v1/messages", data=stripped,
          headers={
              "Content-Type": "application/json",
              "Anthropic-Version": "2023-06-01",
              "X-Api-Key": _judge.key_pool()[0],
          },
      )
      with urllib.request.urlopen(request, timeout=600) as response:
        sse = response.read()
      (args.out_dir / "cache-off.sse").write_bytes(sse)
      logged = [json.loads(l) for l in proxy_log.read_text().splitlines()]
      message = (logged[-1].get("response") or {}).get("message") or {}
      ledger.record("actor:cache-off", message.get("usage") or {})
      completion = json.dumps(message.get("content"), sort_keys=True).encode()
      (args.out_dir / "cache-off.json").write_text(json.dumps({
          "completion_sha256": hashlib.sha256(completion).hexdigest(),
          "same_as_cached_runs": hashlib.sha256(completion).hexdigest() in shas,
          "usage": message.get("usage") or {},
      }, indent=2))
    finally:
      process.terminate()

  (args.out_dir / "attempts.jsonl").write_text(
      "".join(json.dumps(r) + "\n" for r in records))
  # Derived from the ledger's final state, not from a flag set earlier: the
  # cache-off call above is billable too, and a run that crossed the ceiling
  # there is inconclusive however it got there.
  inconclusive = inconclusive or ledger.exhausted
  (args.out_dir / "classification.json").write_text(json.dumps(
      {"classification": "inconclusive" if inconclusive else "complete",
       "attempts": len(records), "total_usd": round(ledger.total, 6),
       "ceiling_usd": _COST_CEILING_USD}, indent=2))
  if inconclusive:
    print("inconclusive: the cost ceiling was crossed. This is not outcome 2, "
          "which requires all K attempts.")
  print(f"{len(records)} attempts -> {args.out_dir}/attempts.jsonl  "
        f"total ${ledger.total:.4f} over {len(ledger.entries)} billable calls")


if __name__ == "__main__":
  main()
