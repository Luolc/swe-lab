"""Compact per-instance QA for the batch runs.

Prints validity and how well the aggregate covers the gold patch's **existing**
code files (files the patch *creates* are excluded, since they can't be read at
the base commit). Usage:

    python experiments/batch_annotation/qa_check.py <instance_id>
    python experiments/batch_annotation/qa_check.py round2 8   # by round + line
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys
from typing import Any

from swebench_related_files_annotation import load_dataset
from swebench_related_files_annotation.datasets.swebench_pro import (
    SweBenchProInstance,
)

HERE = Path(__file__).parent
REPO_ROOT = HERE.parent.parent


def _is_new_file(patch: str, path: str) -> bool:
  pat = r"diff --git a/" + re.escape(path) + r" b/" + re.escape(path)
  m = re.search(pat + r"\n(.*?)(?=\ndiff --git |\Z)", patch, re.S)
  return "new file mode" in (m.group(1) if m else "")


def check(instance_id: str) -> None:
  inst = load_dataset().require(instance_id)
  if not isinstance(inst, SweBenchProInstance):
    raise TypeError(instance_id)
  base = REPO_ROOT / "annotations" / "swebench_pro" / instance_id
  agg: dict[str, Any] = json.loads((base / "aggregate.json").read_text())
  meta = agg.get("metadata", {})
  gold = re.findall(r"diff --git a/(\S+) b/", inst.patch)
  agg_files = {s["file_path"] for s in agg["snippets"]}
  existing = [g for g in gold if not _is_new_file(inst.patch, g)]
  covered = [g for g in existing if g in agg_files]
  missed = [g for g in existing if g not in agg_files]
  cand = [
      len(json.loads((base / f"candidate_{k}.json").read_text())["snippets"])
      for k in (1, 2, 3)
  ]
  valid = bool(meta.get("complete")) and meta.get("invalid_snippet_count") == 0
  print(f"repo: {inst.repo}")
  print(f"problem: {inst.problem_statement[:140].replace(chr(10), ' ')}")
  print(
      f"valid={valid} complete={meta.get('complete')} "
      f"snippets={len(agg['snippets'])} | "
      f"existing-gold {len(covered)}/{len(existing)} | cand {cand}"
  )
  if missed:
    print(f"  MISSED existing gold: {missed}")


if __name__ == "__main__":
  arg = sys.argv[1]
  if arg.startswith("instance_"):
    check(arg)
  else:
    ids = (HERE / f"{arg}_ids.txt").read_text().split()
    check(ids[int(sys.argv[2]) - 1])
