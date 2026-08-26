"""Materialize DeepSWE 1.1 into the published parquet (task-30 §2b).

One row per task, built from a checkout of the upstream repo at
:data:`~swe_lab.datasets.deepswe.constants.PINNED_DEEPSWE_COMMIT`. Lives next
to the loader that will consume it so the schema has one home and the two
cannot drift.

Fixes are **separate columns, never overwrites**: ``base_commit_hash`` is the
upstream value verbatim, ``base_commit`` the normalized full sha — different
only for the three tasks whose ``task.toml`` carries an abbreviated or
truncated value, with the full shas taken from the task images' own measured
``HEAD``s (the dirty-worktree census, task-30 §1). Every transformation is
therefore auditable per row.

Run as::

    python -m swe_lab.datasets.deepswe.build_parquet [--upload]

Without ``--upload`` it builds into the cache and prints the sha256 to pin;
with it, it also publishes the parquet plus its compliance set (README,
upstream LICENSE, PROVENANCE.md, manifest.json) to the public HF repo.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tomllib
from typing import Any

import polars as pl

from swe_lab.paths import cache_root, find_repo_root

from .constants import (
    DEEPSWE_GIT_URL,
    HF_REPO_ID,
    MANIFEST_FILENAME,
    PARQUET_FILENAME,
    PINNED_DEEPSWE_COMMIT,
)

EXPECTED_TASK_COUNT = 113

# task_id -> full 40-hex sha for the three tasks whose `task.toml` carries an
# abbreviated (7-char) or truncated (39-char) `base_commit_hash`. Values are
# the task images' own `HEAD`s, measured 2026-08-25 during the census — HEAD
# is the checkout of the base commit in every image, so it IS the full form
# of the recorded prefix (prefix match verified there).
BASE_COMMIT_FIXES: dict[str, str] = {
    "eicrud-keyset-pagination-cursor": (
        "68dafce500a85227b996d8fcab466d7a0c88809e"
    ),
    "koota-entity-snapshot-rollback": (
        "72ebef44b8e024d877250f055eea60cdfaa45069"
    ),
    "langchain-request-coalescing": (
        "7cef35bfdebd22148a4c62a10bf01f1fde36e722"
    ),
}

# The row schema, in column order. The loader's COLUMNS contract will assert
# against this list; keeping it here keeps producer and consumer in one home.
COLUMNS: tuple[str, ...] = (
    "task_id",
    "ext_id",
    "display_title",
    "display_description",
    "category",
    "language",
    "repository_url",
    "base_commit_hash",  # upstream task.toml value, verbatim
    "base_commit",  # normalized full sha (== hash except the three fixes)
    "docker_image",
    "agent_timeout_sec",
    "verifier_timeout_sec",
    "cpus",
    "memory_mb",
    "storage_mb",
    "instruction",
    "test_sh",
    "grader_py",
    "config_json",
    "test_patch",
    "solution_patch",
    "solve_sh",
    "f2p",  # derived from config_json; builder asserts consistency
    "p2p",
    "upstream_repo",
    "upstream_license",
)


def ensure_checkout(dest: Path, *, commit: str = PINNED_DEEPSWE_COMMIT) -> Path:
  """Fetch the upstream repo at the pinned commit, once.

  A commit sha is a content hash, so a checkout at it needs no separate
  verification. Idempotent: an existing checkout at the right sha is reused.

  Args:
    dest: Directory to hold the checkout.
    commit: The commit to fetch.

  A failed git step propagates as ``subprocess.CalledProcessError`` (every
  step runs with ``check=True``).

  Returns:
    ``dest``, holding the checkout.
  """
  head = dest / ".git" / "HEAD"
  if head.is_file():
    at = subprocess.run(
        ["git", "-C", str(dest), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if at == commit:
      return dest
  dest.mkdir(parents=True, exist_ok=True)
  for args in (
      ["init", "-q"],
      ["fetch", "-q", "--depth", "1", DEEPSWE_GIT_URL, commit],
      ["checkout", "-q", commit],
  ):
    _ = subprocess.run(["git", "-C", str(dest), *args], check=True)
  return dest


def parse_provenance(text: str) -> dict[str, tuple[str, str]]:
  """Parse PROVENANCE.md's table into task id → (upstream repo, license).

  Args:
    text: The file's markdown.

  Returns:
    The mapping, one entry per table row.
  """
  out: dict[str, tuple[str, str]] = {}
  for line in text.splitlines():
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    if len(cells) != 3 or cells[0] in ("Task ID", "") or "---" in cells[0]:
      continue
    out[cells[0]] = (cells[1], cells[2])
  return out


def build_row(
    task_dir: Path,
    provenance: dict[str, tuple[str, str]],
    *,
    base_commit_fixes: dict[str, str] | None = None,
) -> dict[str, Any]:
  """Build one task's row from its directory.

  Args:
    task_dir: The task's directory in the checkout.
    provenance: Task id → (upstream repo, license), from PROVENANCE.md.
    base_commit_fixes: Overrides for abbreviated ``base_commit_hash`` values;
      defaults to :data:`BASE_COMMIT_FIXES`.

  Returns:
    The row, with every :data:`COLUMNS` key.

  Raises:
    ValueError: If the task is missing a provenance entry, or a fix disagrees
      with the verbatim value it claims to complete (a full sha must extend
      the recorded prefix, or the fix itself is wrong).
  """
  fixes = BASE_COMMIT_FIXES if base_commit_fixes is None else base_commit_fixes
  toml = tomllib.loads((task_dir / "task.toml").read_text())
  meta = toml["metadata"]
  env = toml["environment"]
  task_id = str(meta["task_id"])

  config_json = (task_dir / "tests" / "config.json").read_text()
  config = json.loads(config_json)

  verbatim = str(meta["base_commit_hash"])
  full = fixes.get(task_id, verbatim)
  if not full.startswith(verbatim.rstrip()):
    raise ValueError(
        f"{task_id}: base_commit fix {full!r} does not extend the recorded"
        f" prefix {verbatim!r}"
    )
  if task_id not in provenance:
    raise ValueError(f"{task_id}: no PROVENANCE.md entry")
  upstream_repo, upstream_license = provenance[task_id]

  def read(rel: str) -> str:
    # Bytes first: patches may carry non-UTF-8 hunks, and a replace-decode
    # keeps the build total rather than crashing on one exotic file.
    return (task_dir / rel).read_bytes().decode("utf-8", "replace")

  return {
      "task_id": task_id,
      "ext_id": meta["ext_id"],
      "display_title": meta["display_title"],
      "display_description": meta["display_description"],
      "category": meta["category"],
      "language": meta["language"],
      "repository_url": meta["repository_url"],
      "base_commit_hash": verbatim,
      "base_commit": full,
      "docker_image": env["docker_image"],
      "agent_timeout_sec": float(toml["agent"]["timeout_sec"]),
      "verifier_timeout_sec": float(toml["verifier"]["timeout_sec"]),
      "cpus": int(env["cpus"]),
      "memory_mb": int(env["memory_mb"]),
      "storage_mb": int(env["storage_mb"]),
      "instruction": read("instruction.md"),
      "test_sh": read("tests/test.sh"),
      "grader_py": read("tests/grader.py"),
      "config_json": config_json,
      "test_patch": read("tests/test.patch"),
      "solution_patch": read("solution/solution.patch"),
      "solve_sh": read("solution/solve.sh"),
      "f2p": list(config["f2p_node_ids"]),
      "p2p": list(config["p2p_node_ids"]),
      "upstream_repo": upstream_repo,
      "upstream_license": upstream_license,
  }


def build_rows(checkout: Path) -> list[dict[str, Any]]:
  """Build every task's row from a checkout, sorted by task id.

  Args:
    checkout: The pinned checkout of the upstream repo.

  Returns:
    The rows.
  """
  provenance = parse_provenance((checkout / "PROVENANCE.md").read_text())
  rows = [
      build_row(d, provenance)
      for d in sorted((checkout / "tasks").iterdir())
      if (d / "task.toml").is_file()
  ]
  return rows


def row_content_hash(row: dict[str, Any]) -> str:
  """Hash one row's logical content, independent of parquet encoding.

  sha256 over the row's canonical JSON (sorted keys, tight separators,
  UTF-8). Parquet bytes vary across arrow/polars versions; this does not, so
  a manifest diff can tell "same data, re-encoded" from "this task changed".

  Args:
    row: The row.

  Returns:
    ``sha256:<hex>``.
  """
  canonical = json.dumps(
      row, sort_keys=True, ensure_ascii=False, separators=(",", ":")
  )
  return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def verify_round_trip(parquet: Path, rows: list[dict[str, Any]]) -> None:
  """Re-read the written parquet and compare every row byte-for-byte.

  Args:
    parquet: The written file.
    rows: What was written.

  Raises:
    ValueError: If the file does not reproduce the rows exactly.
  """
  back = pl.read_parquet(str(parquet))
  if list(back.columns) != list(COLUMNS):
    raise ValueError(f"column mismatch: {back.columns}")
  reread = sorted(back.iter_rows(named=True), key=lambda r: r["task_id"])
  if len(reread) != len(rows):
    raise ValueError(f"row count {len(reread)} != {len(rows)}")
  for a, b in zip(rows, reread, strict=True):
    if row_content_hash(a) != row_content_hash(b):
      raise ValueError(f"round-trip mismatch on {a['task_id']}")


def build_manifest(
    rows: list[dict[str, Any]], parquet_sha256: str
) -> dict[str, Any]:
  """Assemble the manifest published beside the parquet.

  Args:
    rows: The built rows.
    parquet_sha256: sha256 hex of the written parquet file.

  Returns:
    The manifest, JSON-serializable.
  """
  return {
      "schema_version": 1,
      "dataset": HF_REPO_ID,
      "source_repo": DEEPSWE_GIT_URL,
      "source_commit": PINNED_DEEPSWE_COMMIT,
      "build_date": datetime.date.today().isoformat(),
      "task_count": len(rows),
      "parquet_file": PARQUET_FILENAME,
      "parquet_sha256": parquet_sha256,
      "hash_scheme": (
          "sha256 over each row's canonical JSON (sorted keys, separators"
          " (',', ':'), UTF-8) — encoding-independent, unlike the file sha"
      ),
      "fixes": [
          {
              "task_id": task_id,
              "column": "base_commit",
              "from": next(
                  r["base_commit_hash"] for r in rows if r["task_id"] == task_id
              ),
              "to": full,
              "reason": (
                  "task.toml carries an abbreviated/truncated sha; full value"
                  " measured from the task image's HEAD (task-30 census)"
              ),
          }
          for task_id, full in sorted(BASE_COMMIT_FIXES.items())
      ],
      "task_content_hashes": {
          row["task_id"]: row_content_hash(row) for row in rows
      },
  }


def main(argv: list[str] | None = None) -> int:
  """Build (and optionally publish) the parquet.

  Args:
    argv: CLI arguments; defaults to ``sys.argv[1:]``.

  Returns:
    Process exit code.

  Raises:
    SystemExit: If the checkout does not hold exactly the expected number of
      tasks — the upstream layout changed, so publishing would be wrong.
  """
  parser = argparse.ArgumentParser(description=__doc__)
  _ = parser.add_argument(
      "--upload",
      action="store_true",
      help="publish to the HF repo after building (needs HF_TOKEN)",
  )
  args = parser.parse_args(argv)

  root = find_repo_root()
  # Concrete pathlib from here down: this module is host-local by nature.
  work = (
      Path(str(cache_root(root)))
      / "deepswe"
      / f"build-{PINNED_DEEPSWE_COMMIT[:12]}"
  )
  checkout = ensure_checkout(work / "checkout")
  rows = build_rows(checkout)
  if len(rows) != EXPECTED_TASK_COUNT:
    raise SystemExit(
        f"expected {EXPECTED_TASK_COUNT} tasks, built {len(rows)} — the"
        " upstream layout changed; re-survey before publishing"
    )

  out_dir = work / "dist"
  out_dir.mkdir(parents=True, exist_ok=True)
  parquet = out_dir / PARQUET_FILENAME
  pl.DataFrame(rows, schema_overrides=None).select(COLUMNS).write_parquet(
      str(parquet)
  )
  verify_round_trip(parquet, rows)

  data = parquet.read_bytes()
  sha = hashlib.sha256(data).hexdigest()
  manifest = build_manifest(rows, sha)
  manifest_path = out_dir / MANIFEST_FILENAME
  _ = manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

  print(f"built    {parquet}  ({len(data) / 1e6:.1f} MB)")
  print(f"sha256   {sha}")
  print(f"manifest {manifest_path}")
  print("pin this as PINNED_DEEPSWE_PARQUET_SHA256 when publishing")

  if args.upload:
    _upload(out_dir, checkout)
  return 0


def _upload(out_dir: Path, checkout: Path) -> None:
  """Publish the artifact and its compliance set to the HF repo.

  Args:
    out_dir: Holds the parquet and manifest.
    checkout: The source checkout (for LICENSE and PROVENANCE.md).
  """
  # Imported here: the builder must not make the whole package need hub
  # access at import time.
  from huggingface_hub import HfApi

  api = HfApi()
  _ = api.create_repo(
      HF_REPO_ID, repo_type="dataset", private=False, exist_ok=True
  )
  readme = (
      Path(str(find_repo_root())) / "src/swe_lab/datasets/deepswe/HF_README.md"
  )
  uploads = [
      (out_dir / PARQUET_FILENAME, PARQUET_FILENAME),
      (out_dir / MANIFEST_FILENAME, MANIFEST_FILENAME),
      (checkout / "LICENSE", "LICENSE"),
      (checkout / "PROVENANCE.md", "PROVENANCE.md"),
      (readme, "README.md"),
  ]
  for src, dest in uploads:
    _ = api.upload_file(
        path_or_fileobj=str(src),
        path_in_repo=dest,
        repo_id=HF_REPO_ID,
        repo_type="dataset",
    )
    print(f"uploaded {dest}")
  print(f"https://huggingface.co/datasets/{HF_REPO_ID}")


if __name__ == "__main__":
  sys.exit(main())
