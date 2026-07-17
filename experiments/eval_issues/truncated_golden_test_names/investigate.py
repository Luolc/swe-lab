"""Reproduce + diagnose the 3 GOLDEN_FAIL instances from patch validation.

For each instance this runs the two graded Docker runs locally (base = no patch,
golden = dataset patch; both with golden tests restored), captures the useful
outputs, and checks the hypothesis that GOLDEN_FAIL is caused by *truncated test
names* in the dataset's fail_to_pass/pass_to_pass — i.e. a required name that is
a strict prefix of the real, passing test name the parser emits.

Modes:
  reproduce <keys...>   run base+golden in Docker, capture, analyze, derive fix
  verify    <keys...>   re-run golden in Docker with the FIXED spec, expect OK

Artifacts land under ./results/<key>/. Raw container logs stay in the gitignored
cache; only output.json + the harness + grepped excerpts + analysis are kept.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
from pathlib import Path
import shutil

from swe_lab.core.datasets.loader import load_dataset
from swe_lab.core.datasets.swebench_pro import evaluate, SweBenchProAdapter
from swe_lab.core.datasets.swebench_pro.grading import _passed_tests
from swe_lab.core.paths import cache_root, find_repo_root

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"

INSTANCES = {
    "nodebb": "instance_NodeBB__NodeBB-00c70ce7b0541cfc94afe567921d7668cdc8f4ac-vnan",
    "ansible": "instance_ansible__ansible-de5858f48dc9e1ce9117034e0d7e76806f420ca8-v1055803c3a812189a1133297f7f5468579283f86",
    "vuls": "instance_future-architect__vuls-bff6b7552370b55ff76d474860eead4ab5de785a-v1151a6325649aaf997cd541ebe533b53fddf1b07",
}
KEYWORD = {
    "nodebb": "ACP default",
    "ansible": "test_cache_invalid_cache_content",
    "vuls": "parseUpdatablePacksLine",
}
TIMEOUT = 3600.0


def _capture(ws: Path, dest: Path, keyword: str) -> None:
  dest.mkdir(parents=True, exist_ok=True)
  for name in ("output.json", "entryscript.sh", "run_script.sh", "parser.py"):
    if (ws / name).is_file():
      _ = shutil.copy(ws / name, dest / name)
  for log in ("stdout.log", "stderr.log"):
    if (ws / log).is_file():
      lines = (ws / log).read_text(errors="replace").splitlines()
      hits = [ln for ln in lines if keyword in ln]
      _ = (dest / f"{log}.excerpt.txt").write_text("\n".join(hits[:400]) + "\n")


def _run(spec, patch, ws, provider):
  return evaluate(
      spec,
      patch=patch,
      provider=provider,
      workspace=ws,
      checkout_golden_tests=True,
      timeout=TIMEOUT,
      network=True,
  )


def _prefix_fix(missing: list[str], passed: frozenset[str]) -> dict:
  """Map each truncated 'missing' name to the unique passed name it prefixes."""
  fixes: dict[str, str] = {}
  unresolved: list[dict] = []
  for m in missing:
    cands = sorted(p for p in passed if p.startswith(m) and p != m)
    if len(cands) == 1:
      fixes[m] = cands[0]
    else:
      unresolved.append({"missing": m, "candidates": cands})
  return {"truncated_to_full": fixes, "unresolved": unresolved}


def reproduce(keys: list[str]) -> None:
  root = find_repo_root()
  ds = load_dataset("swebench_pro")
  adapter = SweBenchProAdapter(repo_root=root)
  from swe_lab.core.docker.provider import DockerProvider

  provider = DockerProvider()
  ws_root = cache_root(root) / "exp_golden_fail"

  for key in keys:
    iid = INSTANCES[key]
    inst = ds.require(iid)
    spec = adapter.eval_spec(inst)
    provider.pull(spec.image_ref)
    rep: dict = {"key": key, "instance_id": iid, "image_ref": spec.image_ref}
    for variant, patch in (("base", None), ("golden", inst.patch)):
      ws = ws_root / key / variant
      res = _run(spec, patch, ws, provider)
      _capture(ws, RESULTS / key / variant, KEYWORD[key])
      passed = _passed_tests(ws / "output.json")
      missing = sorted(spec.required_tests - passed)
      rep[variant] = {
          "resolved": res.resolved,
          "output_found": res.output_found,
          "timed_out": res.timed_out,
          "exit_code": res.exit_code,
          "n_passed_total": len(passed),
          "n_required": len(spec.required_tests),
          "n_missing": len(missing),
          "missing": missing,
      }
    gpassed = _passed_tests(ws_root / key / "golden" / "output.json")
    fix = _prefix_fix(rep["golden"]["missing"], gpassed)
    fixed_required = set(spec.required_tests)
    for m, full in fix["truncated_to_full"].items():
      fixed_required.discard(m)
      fixed_required.add(full)
    rep["fix"] = fix
    rep["regrade_resolved_with_fix"] = fixed_required <= gpassed
    _ = (RESULTS / key / "analysis.json").write_text(json.dumps(rep, indent=2))
    print(json.dumps(rep, indent=2)[:2500], flush=True)


def _apply_fix_to_lists(inst, fixes: dict[str, str]):
  def fix(seq):
    return tuple(fixes.get(t, t) for t in seq)

  return dataclasses.replace(
      inst,
      fail_to_pass=fix(inst.fail_to_pass),
      pass_to_pass=fix(inst.pass_to_pass),
  )


def verify(keys: list[str]) -> None:
  root = find_repo_root()
  ds = load_dataset("swebench_pro")
  adapter = SweBenchProAdapter(repo_root=root)
  from swe_lab.core.docker.provider import DockerProvider

  provider = DockerProvider()
  ws_root = cache_root(root) / "exp_golden_fail"
  for key in keys:
    analysis = json.loads((RESULTS / key / "analysis.json").read_text())
    fixes = analysis["fix"]["truncated_to_full"]
    inst = _apply_fix_to_lists(ds.require(INSTANCES[key]), fixes)
    spec = adapter.eval_spec(inst)  # same harness/image; only test lists changed
    res = _run(spec, inst.patch, ws_root / key / "golden_fixed", provider)
    print(
        json.dumps(
            {"key": key, "fixed_golden_resolved": res.resolved,
             "n_missing": len(res.missing), "missing": list(res.missing)},
            indent=2,
        ),
        flush=True,
    )


def main() -> None:
  ap = argparse.ArgumentParser()
  _ = ap.add_argument("mode", choices=("reproduce", "verify"))
  _ = ap.add_argument("keys", nargs="+", choices=list(INSTANCES) + ["all"])
  args = ap.parse_args()
  keys = list(INSTANCES) if "all" in args.keys else args.keys
  if args.mode == "reproduce":
    reproduce(keys)
  else:
    verify(keys)


if __name__ == "__main__":
  main()
