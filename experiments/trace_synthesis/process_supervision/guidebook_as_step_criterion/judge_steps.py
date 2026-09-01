"""Ask a judge, holding only the guidebook, to adjudicate each step.

The question this run exists to answer is whether a guidebook written as prose
stages can be *applied* at step granularity -- not how often it rejects. The
judge is therefore allowed, and expected, to answer "silent".

Every record carries the `max_tokens` it was produced under, because that limit
turned out to select which steps get an answer: a judgement that needs more room
than the cap comes back with no content at all.

Usage:
  python3 <this file> --steps steps.json --out verdicts.jsonl [--max-tokens 700]

Needs `OPENROUTER_API_KEYS` in the environment; `.envrc` exports it from
`.envrc.local`, which an operator creates once from `.envrc.local.example`. It
holds several comma-separated keys and this program splits the variable itself.
Do not split it in a shell -- that is what puts a credential value into argv.
"""

import argparse
import json
import os
import pathlib
import time
import urllib.request

# Resolved against this file, not the working directory: the guidebook is a
# fixed neighbour of this script, and a cwd-relative path made the documented
# command fail from the directory the script lives in.
GUIDEBOOK = (
    pathlib.Path(__file__).resolve().parents[2]
    / "steered_rerun/guidebook/qutebrowser-qtcolor.md"
)
MODEL = "anthropic/claude-sonnet-5"
ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"

INSTRUCTIONS = """You hold a guidebook for one code-change task. You are shown ONE step of an
agent's trajectory, plus the steps before it.

Decide whether THE GUIDEBOOK ADJUDICATES THIS STEP.

Answer with JSON only:
{
  "adjudicable": true | false,
  "stage": <1-5 or null>,
  "quote": "<verbatim span from the guidebook that decides it, or null>",
  "verdict": "on_track" | "off_track" | null,
  "reason": "<one sentence>"
}

Rules:
- "adjudicable": false is a legitimate and expected answer. Use it whenever the
  guidebook says nothing about what this step does.
- Only set "verdict" when "adjudicable" is true.
- "quote" must be copied verbatim from the guidebook, so a human can check you.
- Judge only this step, not the eventual outcome."""


def key_pool() -> list[str]:
  """Return the configured key pool.

  Same shape as `steered_rerun/supervisor.py`: the variable holds several keys
  and is divided here rather than in a shell, because splitting it out there
  routes the value through argv or parameter expansion.

  Returns:
    The keys, in the order ``OPENROUTER_API_KEYS`` lists them.

  Raises:
    RuntimeError: If the variable is unset.
  """
  keys = [k for k in os.environ.get("OPENROUTER_API_KEYS", "").split(",") if k]
  if not keys:
    raise RuntimeError("OPENROUTER_API_KEYS is unset; source .envrc.local")
  return keys


# The parameters that decide whether a call is reproducible. Recorded whether or
# not they are sent: a default that was never set must be readable as *not set*,
# not as an absence that looks like nothing happened.
SAMPLING_KEYS = ("temperature", "top_p", "top_k", "seed")


def sampling_sent(payload: dict) -> dict:
  """Report which sampling parameters a request actually carried.

  Args:
    payload: The request body about to be sent.

  Returns:
    A mapping from every sampling parameter to the value sent, or None when the
    request left it to the provider's default.
  """
  return {key: payload.get(key) for key in SAMPLING_KEYS}


def call(payload: dict) -> dict:
  request = urllib.request.Request(
      ENDPOINT,
      data=json.dumps(payload).encode(),
      headers={
          "Authorization": f"Bearer {key_pool()[0]}",
          "Content-Type": "application/json",
      },
  )
  with urllib.request.urlopen(request, timeout=180) as response:
    return json.loads(response.read())


def main() -> None:
  parser = argparse.ArgumentParser()
  parser.add_argument("--steps", type=pathlib.Path, required=True)
  parser.add_argument("--out", type=pathlib.Path, required=True)
  parser.add_argument("--max-tokens", type=int, default=700)
  parser.add_argument("--only", action="append", default=[],
                      help="ROLLOUT:POSITION, repeatable; default is every step")
  args = parser.parse_args()

  wanted = {(r, int(p)) for r, p in (o.rsplit(":", 1) for o in args.only)} or None
  steps = json.loads(args.steps.read_text())
  guidebook = GUIDEBOOK.read_text()

  by_rollout: dict[str, list[dict]] = {}
  for step in steps:
    by_rollout.setdefault(step["rollout"], []).append(step)

  with args.out.open("a") as out:
    for rollout, group in by_rollout.items():
      for position, step in enumerate(group):
        if wanted is not None and (rollout, position) not in wanted:
          continue
        before = "\n".join(
            f"  step {i}: {s['content'][:120]}"
            for i, s in enumerate(group[:position][-8:])
        ) or "  (none -- this is the first step)"
        user = (
            f"# Guidebook\n\n{guidebook}\n\n"
            f"# Preceding steps (most recent last)\n{before}\n\n"
            f"# The step to judge (step {position} of {len(group)})\n{step['content']}"
        )
        started = time.time()
        record = {
            "rollout": rollout,
            "position": position,
            "step_index": step["step_index"],
            "tool_names": step["tool_names"],
            "model": MODEL,
            "max_tokens": args.max_tokens,
            "judged_at": time.strftime("%FT%TZ", time.gmtime()),
        }
        payload = {
            "model": MODEL,
            "max_tokens": args.max_tokens,
            "messages": [
                {"role": "system", "content": INSTRUCTIONS},
                {"role": "user", "content": user},
            ],
        }
        try:
          response = call(payload)
        except Exception as error:  # noqa: BLE001 - recorded, not swallowed
          record["error"] = str(error)
        else:
          record["raw"] = response["choices"][0]["message"]["content"]
          record["usage"] = response.get("usage") or {}
          # The id the response reports, not the alias we sent: an alias
          # re-pointed upstream still looks correct in the request.
          record["response_model"] = response.get("model")
          record["sampling_sent"] = sampling_sent(payload)
          record["wall_seconds"] = round(time.time() - started, 2)
        out.write(json.dumps(record) + "\n")
        out.flush()
        print(f"{record['judged_at']} {rollout} {position} "
              f"max_tokens={args.max_tokens} empty={not record.get('raw')}")


if __name__ == "__main__":
  main()
