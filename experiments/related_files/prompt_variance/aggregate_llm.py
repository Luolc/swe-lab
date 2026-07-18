"""LLM sample-and-aggregate experiment harness (thin wrapper).

The aggregation pipeline itself lives in the package
(`swe_lab.pipelines.related_files.aggregator`). This script just
feeds each instance's committed run annotations to it and records the result,
for comparing the aggregate against the individual runs.

    python experiments/related_files/prompt_variance/aggregate_llm.py \
        <round> [model]

`<round>` is an existing round whose `runs/<round>/<lang>__run{{1,2,3}}.json`
exist (e.g. `s1-v3`). Output goes to `runs/<round>-agg-llm/`.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import time
from typing import Any

from swe_lab import load_dataset
from swe_lab.pipelines.related_files.aggregator import (
    aggregate_by_id,
)

HERE = Path(__file__).parent
LANGS = ("go", "python", "js", "ts")


def _candidates(
    round_label: str, lang: str
) -> tuple[str, list[dict[str, Any]]]:
  runs_dir = HERE / "runs" / round_label
  instance_id = ""
  candidates: list[dict[str, Any]] = []
  for path in sorted(runs_dir.glob(f"{lang}__run*.json")):
    data = json.loads(path.read_text())
    instance_id = data.get("instance_id", instance_id)
    candidates.append({"snippets": data.get("snippets", [])})
  return instance_id, candidates


def _aggregate_one(
    round_label: str, lang: str, model: str, out_dir: Path, summary_path: Path
) -> None:
  out_file = out_dir / f"{lang}.json"
  if out_file.exists():
    print(f"skip  {lang} (already done)", flush=True)
    return

  start = time.time()
  try:
    instance_id, candidates = _candidates(round_label, lang)
    result = aggregate_by_id(instance_id, candidates, model=model)
    snippets = [s.to_dict() for s in result.annotation.snippets]
    _ = out_file.write_text(json.dumps({"snippets": snippets}, indent=2))
    summary: dict[str, Any] = {
        "lang": lang,
        "instance_id": instance_id,
        "agg_snippet_count": len(snippets),
        "candidate_counts": [len(c["snippets"]) for c in candidates],
        "valid": result.is_valid,
        "cost_usd": result.annotation.metadata.get("cost_usd"),
        "duration_s": round(time.time() - start, 1),
    }
  except Exception as exc:  # record and continue
    summary = {
        "lang": lang,
        "error": f"{type(exc).__name__}: {exc}",
        "duration_s": round(time.time() - start, 1),
    }
    print(f"ERROR {lang}: {exc}", flush=True)

  with summary_path.open("a") as handle:
    _ = handle.write(json.dumps(summary) + "\n")
  if "error" not in summary:
    print(
        f"done  {lang}: agg={summary.get('agg_snippet_count')} "
        f"candidates={summary.get('candidate_counts')} "
        f"valid={summary.get('valid')} cost=${summary.get('cost_usd')} "
        f"{summary.get('duration_s')}s",
        flush=True,
    )


def main(round_label: str, model: str = "sonnet") -> None:
  # Ensure the dataset loads once before the pool (aggregate_by_id reloads it,
  # but this surfaces a bad round label early).
  _ = load_dataset()
  out_dir = HERE / "runs" / f"{round_label}-agg-llm"
  out_dir.mkdir(parents=True, exist_ok=True)
  summary_path = out_dir / "summary.jsonl"

  with ThreadPoolExecutor(max_workers=len(LANGS)) as pool:
    futures = [
        pool.submit(
            _aggregate_one, round_label, lang, model, out_dir, summary_path
        )
        for lang in LANGS
    ]
    for future in futures:
      future.result()


if __name__ == "__main__":
  label = sys.argv[1] if len(sys.argv) > 1 else "s1-v3"
  chosen_model = sys.argv[2] if len(sys.argv) > 2 else "sonnet"
  main(label, chosen_model)
