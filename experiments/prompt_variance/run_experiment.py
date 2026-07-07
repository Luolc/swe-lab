"""Prompt-variance experiment harness.

Runs one instance per language N times each, saving every run's full annotation
and a compact summary line. Resumable: a run whose output file already exists is
skipped, so re-invoking after an interruption continues where it left off.

Parallelism: the languages run concurrently (different instances => different
checkouts, proxy ports, and logs, so no collisions), while the repeats within a
language run sequentially (they share one checkout / port / proxy log).

Usage:
    python experiments/prompt_variance/run_experiment.py <round> [model]

``<round>`` is a label (e.g. ``baseline``, ``v2``) that names the output subdir,
so successive prompt versions can be compared side by side.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import threading
import time

from swebench_related_files_annotation import load_dataset
from swebench_related_files_annotation.annotate.errors import UsageLimitError
from swebench_related_files_annotation.annotate.proxy import build_proxy
from swebench_related_files_annotation.annotate.runner import annotate_by_id

HERE = Path(__file__).parent

# Set when a usage/quota limit is hit; remaining runs are skipped so the round
# stops promptly instead of hammering an exhausted window.
_ABORT = threading.Event()

# One instance per language (see README for how they were chosen).
INSTANCES: dict[str, str] = {
    "go": "instance_flipt-io__flipt-e42da21a07a5ae35835ec54f74004ebd58713874",
    "python": (
        "instance_qutebrowser__qutebrowser-"
        "f91ace96223cac8161c16dd061907e138fe85111-"
        "v059c6fdc75567943479b23ebca7c07b5e9a7f34c"
    ),
    "js": (
        "instance_NodeBB__NodeBB-"
        "04998908ba6721d64eba79ae3b65a351dcfbc5b5-vnan"
    ),
    "ts": (
        "instance_element-hq__element-web-"
        "33e8edb3d508d6eefb354819ca693b7accc695e7"
    ),
}
REPEATS = 3

_SUMMARY_LOCK = threading.Lock()


def _run_language(
    round_label: str,
    lang: str,
    iid: str,
    model: str,
    runs_dir: Path,
    summary_path: Path,
) -> None:
  """Run one language's repeats sequentially (they share a checkout/port)."""
  ds = load_dataset()
  for k in range(1, REPEATS + 1):
    if _ABORT.is_set():
      print(f"abort {lang} run{k} (usage limit hit)", flush=True)
      break
    out_file = runs_dir / f"{lang}__run{k}.json"
    if out_file.exists():
      print(f"skip  {lang} run{k} (already done)", flush=True)
      continue

    start = time.time()
    try:
      result = annotate_by_id(iid, dataset=ds, model=model)
      duration = round(time.time() - start, 1)
      ann = result.annotation
      _ = out_file.write_text(ann.to_json())
      summary = {
          "round": round_label,
          "lang": lang,
          "instance_id": iid,
          "run": k,
          "complete": result.complete,
          "valid": result.is_valid,
          "snippet_count": len(ann.snippets),
          "cost_usd": ann.metadata.get("cost_usd"),
          "usage": ann.metadata.get("usage"),
          "num_turns": ann.metadata.get("num_turns"),
          "duration_s": duration,
          "validation_problems": result.validation_problems,
          "snippets": [
              {
                  "file_path": s.file_path,
                  "start": s.start_line,
                  "end": s.end_line,
                  "category": s.category.value,
              }
              for s in ann.snippets
          ],
      }
    except Exception as exc:  # record the failure and keep going
      duration = round(time.time() - start, 1)
      summary = {
          "round": round_label,
          "lang": lang,
          "instance_id": iid,
          "run": k,
          "error": f"{type(exc).__name__}: {exc}",
          "duration_s": duration,
      }
      print(f"ERROR {lang} run{k}: {exc}", flush=True)
      # A usage/quota limit will keep failing until refresh — stop the round.
      if isinstance(exc, UsageLimitError):
        _ABORT.set()

    with _SUMMARY_LOCK, summary_path.open("a") as handle:
      _ = handle.write(json.dumps(summary) + "\n")
    print(
        f"done  {lang} run{k}: "
        f"complete={summary.get('complete')} "
        f"valid={summary.get('valid')} "
        f"snippets={summary.get('snippet_count')} "
        f"cost=${summary.get('cost_usd')} "
        f"{summary.get('duration_s')}s",
        flush=True,
    )


def run_round(round_label: str, model: str = "sonnet") -> None:
  runs_dir = HERE / "runs" / round_label
  runs_dir.mkdir(parents=True, exist_ok=True)
  summary_path = runs_dir / "summary.jsonl"

  # Build the proxy once up front so concurrent runs don't race the `go build`.
  _ = build_proxy()

  with ThreadPoolExecutor(max_workers=len(INSTANCES)) as pool:
    futures = [
        pool.submit(
            _run_language,
            round_label,
            lang,
            iid,
            model,
            runs_dir,
            summary_path,
        )
        for lang, iid in INSTANCES.items()
    ]
    for future in futures:
      future.result()


if __name__ == "__main__":
  label = sys.argv[1] if len(sys.argv) > 1 else "baseline"
  chosen_model = sys.argv[2] if len(sys.argv) > 2 else "sonnet"
  run_round(label, chosen_model)
