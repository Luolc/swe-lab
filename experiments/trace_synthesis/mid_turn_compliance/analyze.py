"""Recompute every number in `REPORT.md` from the committed witness alone.

The raw proxy captures are off-repo by design, so `evidence.py --check` — which
rebuilds a witness from a capture — can only run on the machine that made them.
That would leave a fresh checkout unable to verify anything the report claims.

This closes that: it reads `evidence/graded.json`, which *is* committed, and
prints the arm counts, the rates, the paired flips and the §6 verdict. Every
figure in the report is reproducible from a clean clone with no network and no
credentials:

    ./analyze.py
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import criterion

HERE = pathlib.Path(__file__).resolve().parent
LABELS = (
    criterion.COMPLIED,
    criterion.NOT_COMPLIED,
    criterion.NO_NEXT_ACTION,
    criterion.NO_TRIGGER,
    criterion.NOT_DELIVERED,
)


def main() -> int:
  parser = argparse.ArgumentParser()
  _ = parser.add_argument(
      "--from-evidence", default=str(HERE / "evidence/graded.json")
  )
  args = parser.parse_args()

  witnesses = json.loads(pathlib.Path(args.from_evidence).read_text())
  summary = criterion.summarize(witnesses)
  arms = summary["arms"]

  width = max(len(label) for label in LABELS)
  print(f"{'':{width}}  " + "".join(f"{a.upper():>8}" for a in ("mid", "neg", "pos")))
  for label in LABELS:
    row = "".join(f"{arms[a]['labels'].get(label, 0):>8}" for a in ("mid", "neg", "pos"))
    print(f"{label:{width}}  {row}")
  print(f"{'denominator':{width}}  " + "".join(f"{arms[a]['denominator']:>8}" for a in ("mid", "neg", "pos")))
  print(f"{'rate':{width}}  " + "".join(f"{arms[a]['rate']:>8.3f}" for a in ("mid", "neg", "pos")))

  by_fixture: dict[str, dict[str, str]] = {}
  for witness in witnesses:
    by_fixture.setdefault(witness["fixture"], {})[witness["arm"]] = witness["label"]
  up = down = concordant = dropped = 0
  for labels in by_fixture.values():
    mid, neg = labels.get("mid"), labels.get("neg")
    if criterion.NO_TRIGGER in (mid, neg):
      dropped += 1
    elif mid == criterion.COMPLIED and neg != criterion.COMPLIED:
      up += 1
    elif neg == criterion.COMPLIED and mid != criterion.COMPLIED:
      down += 1
    else:
      concordant += 1

  print()
  print(f"mid - neg (rates)          {arms['mid']['rate'] - arms['neg']['rate']:+.3f}")
  print(f"paired: NEG-fail->MID-pass {up}")
  print(f"paired: MID-fail->NEG-pass {down}")
  print(f"paired: concordant         {concordant}")
  print(f"paired: dropped            {dropped}")
  print(f"witnesses                  {len(witnesses)}")
  # §2.2: a trigger is valid only if the predicate was false when it fired.
  print()
  print(f"{'':22}{'valid':>8}{'complied':>10}{'invalid':>9}")
  for arm in ("mid", "neg"):
    scored = [
        w for w in witnesses
        if w["arm"] == arm
        and w["label"] in (criterion.COMPLIED, criterion.NOT_COMPLIED)
    ]
    valid = [w for w in scored if not (w.get("predicate_already_true") or {}).get("at_trigger")]
    complied = [w for w in valid if w["label"] == criterion.COMPLIED]
    print(f"{arm.upper() + ' triggers':22}{len(valid):>8}{len(complied):>10}"
          f"{len(scored) - len(valid):>9}")
  redundant_compliances = [
      w for w in witnesses
      if w["arm"] == "mid"
      and w["label"] == criterion.COMPLIED
      and (w.get("predicate_already_true") or {}).get("at_trigger")
  ]
  print(f"MID compliances that were themselves redundant: "
        f"{len(redundant_compliances)}")

  print()
  print(f"VERDICT  {criterion.verdict(summary)}")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
