"""Ask a judge, holding only the guidebook, to adjudicate each step.

The question this run exists to answer is whether a guidebook written as prose
stages can be *applied* at step granularity -- not how often it rejects. The
judge is therefore allowed, and expected, to answer "silent": a guidebook that
cannot speak to most steps fails the feasibility question no matter what its
verdicts are on the rest.
"""

import json
import os
import pathlib
import time
import urllib.request

ART = pathlib.Path(
    "/home/ubuntu/dev/swe-lab-artifacts/process_supervision/guidebook_step_criterion"
)
GUIDEBOOK = pathlib.Path(
    "experiments/trace_synthesis/steered_rerun/guidebook/qutebrowser-qtcolor.md"
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


def call(payload: dict) -> dict:
  request = urllib.request.Request(
      ENDPOINT,
      data=json.dumps(payload).encode(),
      headers={
          "Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}",
          "Content-Type": "application/json",
      },
  )
  with urllib.request.urlopen(request, timeout=180) as response:
    return json.loads(response.read())


def main() -> None:
  steps = json.loads((ART / "steps.json").read_text())
  guidebook = GUIDEBOOK.read_text()
  log = (ART / "run.log").open("a")
  out = (ART / "verdicts.jsonl").open("a")

  by_rollout: dict[str, list[dict]] = {}
  for step in steps:
    by_rollout.setdefault(step["rollout"], []).append(step)

  for rollout, group in by_rollout.items():
    for position, step in enumerate(group):
      before = "\n".join(
          f"  step {i}: {s['content'][:120]}" for i, s in enumerate(group[:position][-8:])
      ) or "  (none -- this is the first step)"
      user = (
          f"# Guidebook\n\n{guidebook}\n\n"
          f"# Preceding steps (most recent last)\n{before}\n\n"
          f"# The step to judge (step {position} of {len(group)})\n{step['content']}"
      )
      started = time.time()
      try:
        response = call({
            "model": MODEL,
            "max_tokens": 700,
            "messages": [
                {"role": "system", "content": INSTRUCTIONS},
                {"role": "user", "content": user},
            ],
        })
      except Exception as error:  # noqa: BLE001 - recorded, not swallowed
        record = {"rollout": rollout, "position": position, "error": str(error)}
        out.write(json.dumps(record) + "\n")
        out.flush()
        log.write(f"{time.strftime('%FT%TZ', time.gmtime())} ERROR {rollout} {position} {error}\n")
        log.flush()
        continue

      text = response["choices"][0]["message"]["content"]
      usage = response.get("usage") or {}
      record = {
          "rollout": rollout,
          "position": position,
          "step_index": step["step_index"],
          "tool_names": step["tool_names"],
          "raw": text,
          "usage": usage,
          "wall_seconds": round(time.time() - started, 2),
          "judged_at": time.strftime("%FT%TZ", time.gmtime()),
      }
      out.write(json.dumps(record) + "\n")
      out.flush()
      log.write(
          f"{record['judged_at']} {rollout} step {position}/{len(group)} "
          f"{record['wall_seconds']}s tokens={usage.get('total_tokens')}\n"
      )
      log.flush()

  log.close()
  out.close()


if __name__ == "__main__":
  main()
