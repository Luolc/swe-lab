"""Prompt-variance experiment harness.

Runs one instance per language N times each, saving every run's full annotation
and a compact summary line. Resumable: a run whose output file already exists is
skipped, so re-invoking after an interruption continues where it left off.

Parallelism: every (language, repeat) task runs concurrently. Each has its own
isolated checkout variant and proxy port, so nothing collides.

Usage:
    python experiments/related_files/prompt_variance/run_experiment.py \
        <round> [model] [suite]

``<round>`` names the output subdir (e.g. ``s1-v3``); ``suite`` selects the
instance set (``s1`` default, or the diverse ``s2``).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import threading
import time

from swe_lab import load_dataset
from swe_lab.core.agent.errors import UsageLimitError
from swe_lab.core.agent.proxy import build_proxy
from swe_lab.core.datasets.swebench_pro import (
    SweBenchProInstance,
)
from swe_lab.tasks.related_files.annotator import (
    annotate_instance,
)

HERE = Path(__file__).parent

# Set when a usage/quota limit is hit; remaining runs are skipped so the round
# stops promptly instead of hammering an exhausted window.
_ABORT = threading.Event()

# Instance suites: one instance per language. s2 uses different repos (and the
# real ts repo, tutanota) for diversity. See README for how they were chosen.
SUITES: dict[str, dict[str, str]] = {
    "s1": {
        "go": (
            "instance_flipt-io__flipt-"
            "e42da21a07a5ae35835ec54f74004ebd58713874"
        ),
        "python": (
            "instance_qutebrowser__qutebrowser-"
            "f91ace96223cac8161c16dd061907e138fe85111-"
            "v059c6fdc75567943479b23ebca7c07b5e9a7f34c"
        ),
        "js": (
            "instance_NodeBB__NodeBB-"
            "04998908ba6721d64eba79ae3b65a351dcfbc5b5-vnan"
        ),
        # element-web is TS code but labeled `js` in the dataset.
        "ts": (
            "instance_element-hq__element-web-"
            "33e8edb3d508d6eefb354819ca693b7accc695e7"
        ),
    },
    "s2": {
        "go": (
            "instance_navidrome__navidrome-"
            "7073d18b54da7e53274d11c9e2baef1242e8769e"
        ),
        "python": (
            "instance_internetarchive__openlibrary-"
            "4a5d2a7d24c9e4c11d3069220c0685b736d5ecde-"
            "v13642507b4fc1f8d234172bf8129942da2c2ca26"
        ),
        "js": (
            "instance_protonmail__webclients-"
            "2c3559cad02d1090985dba7e8eb5a129144d9811"
        ),
        "ts": (
            "instance_tutao__tutanota-"
            "da4edb7375c10f47f4ed3860a591c5e6557f7b5c-"
            "vbc0d9ba8f0071fbe982809910959a6ff8884dbbf"
        ),
    },
}
REPEATS = 3
# All (language, repeat) tasks run concurrently. Repeats of one instance are now
# isolated (distinct checkout variant + proxy port), so they no longer collide.
MAX_CONCURRENCY = 12

_SUMMARY_LOCK = threading.Lock()


def _port(index: int, k: int) -> int:
  """A unique proxy port per (instance, repeat), below the aggregator range."""
  return 20000 + index * 4 + (k - 1)


def _run_one(
    round_label: str,
    lang: str,
    iid: str,
    k: int,
    model: str,
    runs_dir: Path,
    summary_path: Path,
) -> None:
  """Run one (language, repeat) in its own isolated workspace/port."""
  if _ABORT.is_set():
    print(f"abort {lang} run{k} (usage limit hit)", flush=True)
    return
  out_file = runs_dir / f"{lang}__run{k}.json"
  if out_file.exists():
    print(f"skip  {lang} run{k} (already done)", flush=True)
    return

  ds = load_dataset()
  record = ds.require(iid)
  if not isinstance(record, SweBenchProInstance):
    raise TypeError(f"unexpected record type for {iid}")
  index = ds.index_of(iid)

  start = time.time()
  try:
    result = annotate_instance(
        record,
        index,
        model=model,
        variant=f"run{k}",
        port=_port(index, k),
    )
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


def run_round(
    round_label: str, model: str = "sonnet", suite: str = "s1"
) -> None:
  instances = SUITES[suite]
  runs_dir = HERE / "runs" / round_label
  runs_dir.mkdir(parents=True, exist_ok=True)
  summary_path = runs_dir / "summary.jsonl"

  # Build the proxy once up front so concurrent runs don't race the `go build`.
  _ = build_proxy()

  with ThreadPoolExecutor(max_workers=MAX_CONCURRENCY) as pool:
    futures = [
        pool.submit(
            _run_one, round_label, lang, iid, k, model, runs_dir, summary_path
        )
        for lang, iid in instances.items()
        for k in range(1, REPEATS + 1)
    ]
    for future in futures:
      future.result()


if __name__ == "__main__":
  label = sys.argv[1] if len(sys.argv) > 1 else "baseline"
  chosen_model = sys.argv[2] if len(sys.argv) > 2 else "sonnet"
  chosen_suite = sys.argv[3] if len(sys.argv) > 3 else "s1"
  run_round(label, chosen_model, chosen_suite)
