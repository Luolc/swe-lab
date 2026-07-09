"""Off-repo storage for the full-conversation trace records.

The ``*.last_exchange.json`` files hold the entire agent conversation and are
large (tens to hundreds of KB each), so they are kept out of git and pushed to a
Hugging Face dataset repo instead. Only a small ``traces_manifest.json``
(per trace: sha256 + size, plus the repo id + revision) is version-controlled,
so the committed deliverable stays self-describing and any trace can be fetched
and integrity-checked on demand.

CLI::

    python -m swebench_eval_lab.tasks.related_files.traces push [--dataset ...]
    python -m swebench_eval_lab.tasks.related_files.traces fetch [--dataset ...]

Auth: set ``HF_TOKEN`` (e.g. in ``.envrc.local``) or run ``hf auth login``.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterator
from datetime import datetime, UTC
import hashlib
import json
from pathlib import Path
import shutil

from huggingface_hub import hf_hub_download, HfApi

from swebench_eval_lab.core.paths import find_repo_root, outputs_root

from .storage import DEFAULT_DATASET, TASK_DIRNAME

DEFAULT_REPO_ID = "luolc/swebench-eval-lab-traces"
REPO_TYPE = "dataset"
TRACE_SUFFIX = ".last_exchange.json"
MANIFEST_NAME = "traces_manifest.json"


def _task_dir(repo_root: Path | None = None) -> Path:
  """The related-files output root (``outputs/related_files/``)."""
  return outputs_root(repo_root or find_repo_root()) / TASK_DIRNAME


def manifest_path(
    dataset: str = DEFAULT_DATASET, *, repo_root: Path | None = None
) -> Path:
  return _task_dir(repo_root) / dataset / MANIFEST_NAME


def iter_trace_files(
    dataset: str = DEFAULT_DATASET, *, repo_root: Path | None = None
) -> Iterator[Path]:
  """Yield every trace file for a dataset, sorted for deterministic output."""
  base = _task_dir(repo_root) / dataset / "intermediate"
  if not base.is_dir():
    return
  yield from sorted(base.rglob(f"*{TRACE_SUFFIX}"))


def _sha256(path: Path) -> str:
  digest = hashlib.sha256()
  with path.open("rb") as handle:
    for chunk in iter(lambda: handle.read(1 << 20), b""):
      digest.update(chunk)
  return digest.hexdigest()


def _build_manifest(
    dataset: str, repo_id: str, revision: str | None, base: Path, root: Path
) -> dict[str, object]:
  traces: dict[str, object] = {}
  total = 0
  for path in iter_trace_files(dataset, repo_root=root):
    rel = path.relative_to(base).as_posix()
    size = path.stat().st_size
    traces[rel] = {"sha256": _sha256(path), "bytes": size}
    total += size
  return {
      "repo_id": repo_id,
      "repo_type": REPO_TYPE,
      "revision": revision,
      "dataset": dataset,
      "generated_at": datetime.now(UTC).isoformat(),
      "num_traces": len(traces),
      "total_bytes": total,
      "traces": traces,
  }


def push_traces(
    dataset: str = DEFAULT_DATASET,
    *,
    repo_id: str = DEFAULT_REPO_ID,
    repo_root: Path | None = None,
    private: bool = True,
) -> Path:
  """Upload all trace files to the HF dataset repo; write the git manifest.

  The repo mirrors the local layout (``<dataset>/intermediate/<id>/<label>``);
  only ``*.last_exchange.json`` files are uploaded. Returns the manifest path.
  """
  root = repo_root or find_repo_root()
  base = _task_dir(root)
  api = HfApi()
  _ = api.create_repo(
      repo_id, repo_type=REPO_TYPE, private=private, exist_ok=True
  )
  commit = api.upload_folder(
      repo_id=repo_id,
      repo_type=REPO_TYPE,
      folder_path=str(base),
      allow_patterns=[f"**/*{TRACE_SUFFIX}"],
      commit_message=f"Sync {dataset} conversation traces",
  )
  revision = getattr(commit, "oid", None)
  manifest = _build_manifest(dataset, repo_id, revision, base, root)
  out = manifest_path(dataset, repo_root=root)
  _ = out.write_text(json.dumps(manifest, indent=2) + "\n")
  return out


def fetch_traces(
    dataset: str = DEFAULT_DATASET, *, repo_root: Path | None = None
) -> tuple[int, int]:
  """Download every trace named in the manifest, verifying sha256.

  Skips files already present with the right hash. Returns ``(ok, mismatched)``.
  """
  root = repo_root or find_repo_root()
  base = _task_dir(root)
  manifest = json.loads(manifest_path(dataset, repo_root=root).read_text())
  repo_id = str(manifest["repo_id"])
  revision = manifest.get("revision")
  traces = manifest["traces"]

  ok = 0
  mismatched = 0
  for rel, meta in traces.items():
    dest = base / rel
    want = str(meta["sha256"])
    if dest.is_file() and _sha256(dest) == want:
      ok += 1
      continue
    cached = hf_hub_download(
        repo_id=repo_id,
        repo_type=REPO_TYPE,
        filename=rel,
        revision=revision,
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    _ = shutil.copyfile(cached, dest)
    if _sha256(dest) == want:
      ok += 1
    else:
      mismatched += 1
  return ok, mismatched


def main() -> int:
  parser = argparse.ArgumentParser(
      prog="python -m swebench_eval_lab.tasks.related_files.traces",
      description="Push/fetch full-conversation traces to a HF dataset repo.",
  )
  _ = parser.add_argument("action", choices=("push", "fetch"))
  _ = parser.add_argument("--dataset", default=DEFAULT_DATASET)
  _ = parser.add_argument("--repo-id", default=DEFAULT_REPO_ID)
  args = parser.parse_args()

  if args.action == "push":
    out = push_traces(args.dataset, repo_id=args.repo_id)
    manifest = json.loads(out.read_text())
    print(
        f"pushed {manifest['num_traces']} traces"
        f" ({manifest['total_bytes'] / 1024 / 1024:.1f} MB)"
        f" to {manifest['repo_id']}@{str(manifest['revision'])[:12]}"
    )
    print(f"manifest: {out}")
  else:
    ok, mismatched = fetch_traces(args.dataset)
    print(f"fetched/verified {ok} traces, {mismatched} mismatched")
    if mismatched:
      return 1
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
