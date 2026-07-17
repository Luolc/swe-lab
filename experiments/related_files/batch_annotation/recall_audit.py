"""Audit every annotated instance for *real source-file* recall misses.

For each instance under ``intermediate/`` it compares the aggregate's covered
files against the gold patch's **existing** files (files the patch creates are
excluded — they can't be read at the base commit). A missed file is classified:

- **acceptable** — docs / i18n / dependency-manifest / CI config. These are
  routinely (and correctly) excluded by the annotator; they are not a defect.
- **source** — an actual code file the gold patch modified that the annotation
  failed to surface. This is a genuine recall miss.

Instances with >=1 *source* miss are printed and written to a re-run list, one
id per line, so they can be re-annotated in a single MAXJOBS=2 batch.

Usage:
    python .../batch_annotation/recall_audit.py [out_ids.txt]
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys

from swe_lab import load_dataset
from swe_lab.core.datasets.swebench_pro import SweBenchProInstance
from swe_lab.tasks.related_files.storage import instance_dir

# Extensions that are documentation / static assets, not code.
_DOC_EXT = {
    ".md",
    ".mdx",
    ".rst",
    ".txt",
    ".asciidoc",
    ".adoc",
    ".po",
    ".pot",
    # image / static assets — never "source a solver must read"
    ".svg",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".webp",
}
# Bare filenames (lowercased) that are dependency manifests / lockfiles.
_MANIFEST_NAMES = {
    "go.mod",
    "go.sum",
    "package.json",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "cargo.toml",
    "cargo.lock",
    "poetry.lock",
    "pipfile",
    "pipfile.lock",
    "composer.json",
    "composer.lock",
    "gemfile",
    "gemfile.lock",
    "requirements.txt",
    "go.work",
    "go.work.sum",
}
# Bare filenames (lowercased) that are build / CI infra, not app source.
_BUILD_NAMES = {
    "makefile",
    "dockerfile",
    ".dockerignore",
    ".gitignore",
    ".gitattributes",
    ".golangci.yml",
    ".golangci.yaml",
    ".goreleaser.yml",
    ".goreleaser.yaml",
    ".editorconfig",
    ".pre-commit-config.yaml",
    "taskfile.yml",
    "taskfile.yaml",
}
# Filename suffixes that mark generated (not hand-written) code.
_GENERATED_SUFFIXES = (
    ".pb.go",
    ".pb.gw.go",
    "_grpc.pb.go",
    ".gen.go",
    ".generated.go",
    "_pb2.py",
    "_pb2_grpc.py",
)
# Substrings in the (lowercased) path that mark docs / CI / build / test-data /
# vendored / generated trees — a miss here is not an app-source recall miss.
_SKIP_DIR_HINTS = (
    "docs/",
    "doc/",
    ".github/",
    "build/",
    "testdata/",
    "/fixtures/",
    "vendor/",
    "node_modules/",
    "third_party/",
)
# Path *segments* that mark a translations / locale tree (i18n data files).
_I18N_HINTS = ("locale", "locales", "i18n", "translations", "lang", "language")


def _is_new_file(patch: str, path: str) -> bool:
  pat = r"diff --git a/" + re.escape(path) + r" b/" + re.escape(path)
  m = re.search(pat + r"\n(.*?)(?=\ndiff --git |\Z)", patch, re.S)
  return "new file mode" in (m.group(1) if m else "")


def _is_acceptable_miss(path: str) -> bool:
  p = path.lower()
  name = p.rsplit("/", 1)[-1]
  ext = "." + name.rsplit(".", 1)[-1] if "." in name else ""
  parts = p.split("/")
  if ext in _DOC_EXT:
    return True
  if name in _MANIFEST_NAMES or name in _BUILD_NAMES:
    return True
  if name.startswith(
      ("changelog", "license", "notice", "authors", "contributing")
  ):
    return True
  if name.endswith(_GENERATED_SUFFIXES):
    return True
  if any(h in p for h in _SKIP_DIR_HINTS):
    return True
  # i18n data files (typically JSON/YAML under a locale-ish directory).
  return ext in {".json", ".yaml", ".yml"} and any(
      h in parts for h in _I18N_HINTS
  )


def _source_misses(inst: SweBenchProInstance, base: Path) -> list[str] | None:
  """Real-source gold files the aggregate missed (None if no data)."""
  agg_path = base / "aggregate.json"
  if not agg_path.is_file():
    return None
  agg = json.loads(agg_path.read_text())
  covered = {s["file_path"] for s in agg["snippets"]}
  gold = re.findall(r"diff --git a/(\S+) b/", inst.patch)
  existing = [g for g in gold if not _is_new_file(inst.patch, g)]
  missed = [g for g in existing if g not in covered]
  return [m for m in missed if not _is_acceptable_miss(m)]


def main() -> int:
  out = Path(sys.argv[1]) if len(sys.argv) > 1 else None
  root = instance_dir("instance_x").parent  # intermediate/
  ids = sorted(
      d.name
      for d in root.iterdir()
      if d.is_dir() and (d / "aggregate.json").is_file()
  )
  by_id = {r.instance_id: r for r in load_dataset()}
  flagged: list[str] = []
  for iid in ids:
    inst = by_id.get(iid)
    if not isinstance(inst, SweBenchProInstance):
      continue
    misses = _source_misses(inst, instance_dir(iid))
    if misses:
      flagged.append(iid)
      short = iid.replace("instance_", "")[:52]
      print(f"{short:<54} missed source: {misses}")
  print(
      f"\n-- scanned {len(ids)} instances; {len(flagged)} w/ source misses --"
  )
  if out and flagged:
    _ = out.write_text("\n".join(flagged) + "\n")
    print(f"re-run list -> {out}")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
