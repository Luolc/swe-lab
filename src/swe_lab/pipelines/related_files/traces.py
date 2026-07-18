"""Off-repo storage for the full-conversation trace records.

The ``*.last_exchange.json`` files hold the entire agent conversation and are
large (tens to hundreds of KB each), so they are kept out of git and pushed to a
Hugging Face dataset repo instead. Only a small ``traces_manifest.json`` (per
trace: sha256 + size, plus the repo id, the HF revision the push produced, and
the git commit it was generated at) is version-controlled, so the committed
deliverable stays self-describing and any trace can be fetched and
integrity-checked on demand.

Three states must agree — **local files**, the **git manifest**, and the **HF
revision**. Git is the source of truth for "latest"; the manifest pins an exact
HF revision + per-file hashes, and these commands keep the three reconciled:

    push          upload local traces, guarded by an optimistic-concurrency
                  check (fails if HF advanced past your manifest); rewrites the
                  manifest at the new revision.
    push --mirror also delete remote traces absent locally (make HF == local).
    push --force  skip the concurrency guard (last-writer-wins).
    fetch         download every trace the manifest names, at its pinned
                  revision, verifying sha256 (skips already-correct files).
    status        compare local vs manifest vs HF head and say who is ahead.
    adopt-remote  take HF head as truth: download it and rewrite the manifest
                  (for when HF was pushed but its manifest was never committed).

See ``docs/traces.md`` for the full model and the reconciliation decision table.

Auth: set ``HF_TOKEN`` (e.g. in ``.envrc.local``) or run ``hf auth login``.

    python -m ...tasks.related_files.traces <action> [--dataset ...]
"""

from __future__ import annotations

import argparse
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, UTC
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

from huggingface_hub import hf_hub_download, HfApi
from huggingface_hub.errors import HfHubHTTPError

from swe_lab.core.paths import find_repo_root, outputs_root

from .storage import DEFAULT_DATASET, TASK_DIRNAME

DEFAULT_REPO_ID = "luolc/swe-lab-traces"
REPO_TYPE = "dataset"
TRACE_SUFFIX = ".last_exchange.json"
MANIFEST_NAME = "traces_manifest.json"
_UPLOAD_PATTERN = f"**/*{TRACE_SUFFIX}"


class SyncError(RuntimeError):
  """A push/pull was refused because the three states have diverged."""


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


def _git_head(root: Path) -> str | None:
  """The repo's current commit sha, or None if git is unavailable."""
  try:
    out = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
  except (OSError, subprocess.SubprocessError):
    return None
  return out.stdout.strip() if out.returncode == 0 else None


def _hf_head(repo_id: str, api: HfApi) -> str | None:
  """The HF repo's current head revision, or None if it does not exist yet."""
  try:
    info = api.repo_info(repo_id, repo_type=REPO_TYPE)
  except HfHubHTTPError:
    return None
  return getattr(info, "sha", None)


def _load_manifest(dataset: str, root: Path) -> dict[str, object] | None:
  path = manifest_path(dataset, repo_root=root)
  if not path.is_file():
    return None
  return json.loads(path.read_text())


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
      "git_commit": _git_head(root),
      "dataset": dataset,
      "generated_at": datetime.now(UTC).isoformat(),
      "num_traces": len(traces),
      "total_bytes": total,
      "traces": traces,
  }


def _write_manifest(
    manifest: dict[str, object], dataset: str, root: Path
) -> Path:
  out = manifest_path(dataset, repo_root=root)
  _ = out.write_text(json.dumps(manifest, indent=2) + "\n")
  return out


def push_traces(
    dataset: str = DEFAULT_DATASET,
    *,
    repo_id: str = DEFAULT_REPO_ID,
    repo_root: Path | None = None,
    private: bool = True,
    mirror: bool = False,
    force: bool = False,
) -> Path:
  """Upload all trace files to the HF dataset repo; write the git manifest.

  The repo mirrors the local layout (``<dataset>/intermediate/<id>/<label>``);
  only ``*.last_exchange.json`` files are uploaded. Unless ``force``, the push
  is guarded by an optimistic-concurrency check: it refuses if the HF head has
  moved past the revision recorded in the local manifest (someone else pushed
  since your last sync). ``mirror`` additionally deletes remote traces that are
  absent locally, making HF exactly match the local set. Returns the manifest
  path.
  """
  root = repo_root or find_repo_root()
  base = _task_dir(root)
  api = HfApi()
  manifest = _load_manifest(dataset, root)
  parent = None if manifest is None else manifest.get("revision")

  if not force and isinstance(parent, str):
    head = _hf_head(repo_id, api)
    if head is not None and head != parent:
      raise SyncError(
          f"HF head {head[:12]} has advanced past your manifest revision "
          f"{parent[:12]} — someone pushed since your last sync. Run `status`, "
          "then `git pull` + `fetch` (or `adopt-remote`) to take remote, or "
          "`push --force` / `push --mirror` to overwrite it with local."
      )

  _ = api.create_repo(
      repo_id, repo_type=REPO_TYPE, private=private, exist_ok=True
  )
  commit = api.upload_folder(
      repo_id=repo_id,
      repo_type=REPO_TYPE,
      folder_path=str(base),
      allow_patterns=[_UPLOAD_PATTERN],
      delete_patterns=[_UPLOAD_PATTERN] if mirror else None,
      # Optimistic-concurrency guard: atomically fail if HF head != parent.
      parent_commit=None
      if force
      else (parent if isinstance(parent, str) else None),
      commit_message=f"Sync {dataset} conversation traces",
  )
  revision = getattr(commit, "oid", None)
  manifest = _build_manifest(dataset, repo_id, revision, base, root)
  return _write_manifest(manifest, dataset, root)


def fetch_traces(
    dataset: str = DEFAULT_DATASET, *, repo_root: Path | None = None
) -> tuple[int, int]:
  """Download every trace named in the manifest, verifying sha256.

  Downloads at the manifest's pinned revision (reproducible — unaffected by
  later HF pushes). Skips files already present with the right hash. Returns
  ``(ok, mismatched)``.
  """
  root = repo_root or find_repo_root()
  base = _task_dir(root)
  manifest = _load_manifest(dataset, root)
  if manifest is None:
    raise FileNotFoundError(
        f"No manifest at {manifest_path(dataset, repo_root=root)}; nothing to"
        " fetch."
    )
  repo_id = str(manifest["repo_id"])
  revision = manifest.get("revision")
  traces = manifest["traces"]
  assert isinstance(traces, dict)

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
        revision=revision if isinstance(revision, str) else None,
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    _ = shutil.copyfile(cached, dest)
    if _sha256(dest) == want:
      ok += 1
    else:
      mismatched += 1
  return ok, mismatched


def adopt_remote(
    dataset: str = DEFAULT_DATASET,
    *,
    repo_id: str = DEFAULT_REPO_ID,
    repo_root: Path | None = None,
) -> Path:
  """Take HF head as truth: download every trace there and rewrite the manifest.

  For the case where HF was pushed but its manifest was never committed to git,
  so the local manifest points at a stale revision. Returns the manifest path.
  """
  root = repo_root or find_repo_root()
  base = _task_dir(root)
  api = HfApi()
  head = _hf_head(repo_id, api)
  if head is None:
    raise SyncError(f"HF repo {repo_id} does not exist / is unreachable.")
  remote = [
      f
      for f in api.list_repo_files(repo_id, repo_type=REPO_TYPE, revision=head)
      if f.endswith(TRACE_SUFFIX)
  ]
  for rel in remote:
    cached = hf_hub_download(
        repo_id=repo_id, repo_type=REPO_TYPE, filename=rel, revision=head
    )
    dest = base / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    _ = shutil.copyfile(cached, dest)
  manifest = _build_manifest(dataset, repo_id, head, base, root)
  return _write_manifest(manifest, dataset, root)


@dataclass(frozen=True)
class Status:
  """A comparison of the three states for one dataset's traces."""

  has_manifest: bool
  manifest_revision: str | None
  hf_head: str | None
  local_ok: int
  local_changed: int
  local_missing: int
  local_extra: int

  @property
  def local_clean(self) -> bool:
    return (
        self.local_changed == 0
        and self.local_missing == 0
        and self.local_extra == 0
    )

  @property
  def in_sync_with_hf(self) -> bool:
    return self.hf_head is not None and self.manifest_revision == self.hf_head

  def recommendation(self) -> str:
    if not self.has_manifest:
      return "never pushed — run `push` to publish local traces."
    if self.hf_head is None:
      return "HF repo missing/unreachable — check auth, or `push` to create it."
    if self.local_clean and self.in_sync_with_hf:
      return "in sync — local, manifest, and HF all agree."
    if self.local_clean and not self.in_sync_with_hf:
      return (
          "HF is ahead of your manifest — `git pull` then `fetch` to take it "
          "(or `adopt-remote` if the newer manifest was never committed)."
      )
    if not self.local_clean and self.in_sync_with_hf:
      return (
          "local has un-pushed changes — `push` (add `--mirror` to also "
          "purge remote-only)."
      )
    return (
        "DIVERGED — local changed AND HF advanced. Decide the source of truth: "
        "`adopt-remote` (take HF) or `push --force`/`--mirror` (take local)."
    )


def status(
    dataset: str = DEFAULT_DATASET,
    *,
    repo_id: str = DEFAULT_REPO_ID,
    repo_root: Path | None = None,
) -> Status:
  """Compare local files, the git manifest, and the HF head."""
  root = repo_root or find_repo_root()
  base = _task_dir(root)
  manifest = _load_manifest(dataset, root)
  api = HfApi()
  hf_head = _hf_head(repo_id, api)

  if manifest is None:
    return Status(False, None, hf_head, 0, 0, 0, 0)

  traces = manifest["traces"]
  assert isinstance(traces, dict)
  local_rels = {
      p.relative_to(base).as_posix()
      for p in iter_trace_files(dataset, repo_root=root)
  }
  ok = changed = missing = 0
  for rel, meta in traces.items():
    dest = base / rel
    if not dest.is_file():
      missing += 1
    elif _sha256(dest) == str(meta["sha256"]):
      ok += 1
    else:
      changed += 1
  extra = len(local_rels - set(traces))
  rev = manifest.get("revision")
  return Status(
      True,
      rev if isinstance(rev, str) else None,
      hf_head,
      ok,
      changed,
      missing,
      extra,
  )


def _print_status(st: Status) -> None:
  rev = st.manifest_revision[:12] if st.manifest_revision else "(none)"
  head = st.hf_head[:12] if st.hf_head else "(unreachable)"
  print(
      f"local vs manifest : {st.local_ok} ok, {st.local_changed} changed, "
      f"{st.local_missing} missing, {st.local_extra} extra"
  )
  print(f"manifest revision : {rev}")
  print(f"HF head           : {head}")
  print(f"=> {st.recommendation()}")


def main() -> int:
  parser = argparse.ArgumentParser(
      prog="python -m swe_lab.pipelines.related_files.traces",
      description="Push/fetch/reconcile HF-stored conversation traces.",
  )
  _ = parser.add_argument(
      "action", choices=("push", "fetch", "status", "adopt-remote")
  )
  _ = parser.add_argument("--dataset", default=DEFAULT_DATASET)
  _ = parser.add_argument("--repo-id", default=DEFAULT_REPO_ID)
  _ = parser.add_argument(
      "--mirror", action="store_true", help="push: delete remote-only traces"
  )
  _ = parser.add_argument(
      "--force", action="store_true", help="push: skip the concurrency guard"
  )
  args = parser.parse_args()

  if args.action == "push":
    out = push_traces(
        args.dataset, repo_id=args.repo_id, mirror=args.mirror, force=args.force
    )
    manifest = json.loads(out.read_text())
    print(
        f"pushed {manifest['num_traces']} traces"
        f" ({manifest['total_bytes'] / 1024 / 1024:.1f} MB)"
        f" to {manifest['repo_id']}@{str(manifest['revision'])[:12]}"
    )
    print(f"manifest: {out}")
  elif args.action == "adopt-remote":
    out = adopt_remote(args.dataset, repo_id=args.repo_id)
    manifest = json.loads(out.read_text())
    print(
        f"adopted HF head @{str(manifest['revision'])[:12]}"
        f" ({manifest['num_traces']} traces); manifest: {out}"
    )
  elif args.action == "status":
    _print_status(status(args.dataset, repo_id=args.repo_id))
  else:
    ok, mismatched = fetch_traces(args.dataset)
    print(f"fetched/verified {ok} traces, {mismatched} mismatched")
    if mismatched:
      return 1
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
