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
  # Serialized once; every attempt sends this exact string.
  payload = json.dumps(body).encode()
  payload_sha = hashlib.sha256(payload).hexdigest()

  steps = _extract.extract(_ROLLOUT)
  before = "\n".join(
      f"  step {i}: {s['content'][:120]}"
      for i, s in enumerate(steps[:_POSITION][-8:])
  ) or "  (none -- this is the first step)"

  process, port, proxy_log = _start_proxy(args.out_dir)
  records, spent, seen = [], 0.0, 0
  try:
    for attempt in range(args.k):
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

      verdict = _judge_completion(message, before, len(steps))
      try:
        accepted = json.loads(verdict["raw"]).get("verdict") == "on_track"
      except json.JSONDecodeError:
        accepted = False

      spent += float(usage.get("cost") or 0) + float(
          (verdict["usage"] or {}).get("cost") or 0)
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
            f"provider={records[-1]['provider_name']} spent=${spent:.4f}")
      if accepted:
        break
      if spent > _COST_CEILING_USD:
        print("cost ceiling reached; run is INCONCLUSIVE, not outcome 2")
        break
  finally:
    process.terminate()

  (args.out_dir / "attempts.jsonl").write_text(
      "".join(json.dumps(r) + "\n" for r in records))
  print(f"{len(records)} attempts -> {args.out_dir}/attempts.jsonl  "
        f"total ${spent:.4f}")


if __name__ == "__main__":
  main()
