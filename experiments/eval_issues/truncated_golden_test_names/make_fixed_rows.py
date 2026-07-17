"""Emit the 3 corrected dataset rows as JSON.

Reads each per-instance ``results/<key>/analysis.json`` (produced by
``investigate.py reproduce``) and writes ``fixed_rows.json``: the full record
with ONLY ``fail_to_pass`` / ``pass_to_pass`` corrected — each mangled/truncated
required name replaced by the exact name the instance's own parser emits for the
(passing) test. Every other field is byte-for-byte the original.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from swe_lab.core.datasets.loader import load_dataset

HERE = Path(__file__).resolve().parent
INSTANCES = {
    "nodebb": "instance_NodeBB__NodeBB-00c70ce7b0541cfc94afe567921d7668cdc8f4ac-vnan",
    "ansible": "instance_ansible__ansible-de5858f48dc9e1ce9117034e0d7e76806f420ca8-v1055803c3a812189a1133297f7f5468579283f86",
    "vuls": "instance_future-architect__vuls-bff6b7552370b55ff76d474860eead4ab5de785a-v1151a6325649aaf997cd541ebe533b53fddf1b07",
}


def main() -> None:
  ds = load_dataset("swebench_pro")
  out = []
  for key, iid in INSTANCES.items():
    analysis = json.loads((HERE / "results" / key / "analysis.json").read_text())
    fixes: dict[str, str] = analysis["fix"]["truncated_to_full"]
    inst = ds.require(iid)
    row = dataclasses.asdict(inst)
    changes = {}
    for field in ("fail_to_pass", "pass_to_pass"):
      original = list(getattr(inst, field))
      row[field] = [fixes.get(t, t) for t in original]
      changed = [{"from": t, "to": fixes[t]} for t in original if t in fixes]
      if changed:
        changes[field] = changed
    out.append({"instance_id": iid, "changes": changes, "row": row})
  _ = (HERE / "fixed_rows.json").write_text(json.dumps(out, indent=2))
  print(
      "wrote fixed_rows.json — entries changed per instance:",
      {r["instance_id"].split("-")[0]: sum(len(v) for v in r["changes"].values()) for r in out},
  )


if __name__ == "__main__":
  main()
