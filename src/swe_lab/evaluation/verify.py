"""Full-dataset golden verification for SWE-Bench Pro.

For each instance we do two graded runs in its image and check the pair:

- **base** (no patch, golden tests restored) must NOT resolve — the required
  tests fail without the fix;
- **golden** (the dataset's own patch, golden tests restored) must resolve.

A mismatch means the instance is suspect: its tests don't detect the bug
(``BASE_UNEXPECTED_PASS``) or its golden patch doesn't fix it under our harness
(``GOLDEN_FAIL``). Runs are stride-sharded (``--shard i/N``) so a GitHub Actions
matrix can burst the whole dataset in parallel; each shard writes one JSON per
instance under ``--out-dir`` (resumable — an instance with a result is skipped),
and ``--aggregate`` merges them into a summary + report.
"""

from __future__ import annotations

import argparse
import collections
from concurrent.futures import as_completed, ThreadPoolExecutor
from datetime import datetime, UTC
import json
import os
from pathlib import Path

from swe_lab.core.benchmark import EvalSpec
from swe_lab.core.datasets.loader import load_dataset
from swe_lab.core.datasets.swebench_pro import (
    EvalResult,
    evaluate,
    SweBenchProAdapter,
    SweBenchProInstance,
)
from swe_lab.core.docker.provider import DockerError, DockerProvider
from swe_lab.core.paths import cache_root, find_repo_root

# Verdicts.
OK = "OK"
BASE_UNEXPECTED_PASS = "BASE_UNEXPECTED_PASS"
GOLDEN_FAIL = "GOLDEN_FAIL"
ERROR = "ERROR"  # inconclusive: harness/infra failure, not a dataset finding
_VERDICTS = (OK, BASE_UNEXPECTED_PASS, GOLDEN_FAIL, ERROR)

_DEFAULT_OUT = "outputs/swebench_pro/patch_validation"
_RESULTS_SUBDIR = "instances"  # per-instance JSONs under out_dir
_WS_SUBDIR = "golden_verify"  # scratch workspaces under cache_root


def classify(spec: EvalSpec, base: EvalResult, golden: EvalResult) -> str:
  """Return the verdict for one instance from its base + golden runs.

  ``ERROR`` (either run produced no gradeable output, or timed out) is kept
  distinct from real dataset findings so infra flakes don't masquerade as them.
  """
  for run in (base, golden):
    if run.timed_out or not run.output_found:
      return ERROR
  base_fail_to_pass_passed = frozenset(spec.fail_to_pass) & frozenset(
      base.passed
  )
  if not golden.resolved:
    return GOLDEN_FAIL
  if base.resolved or base_fail_to_pass_passed:
    return BASE_UNEXPECTED_PASS
  return OK


def _run_json(result: EvalResult) -> dict[str, object]:
  return {
      "resolved": result.resolved,
      "output_found": result.output_found,
      "timed_out": result.timed_out,
      "exit_code": result.exit_code,
      "n_passed": len(result.passed),
      "missing": list(result.missing),
  }


def _base_json(spec: EvalSpec, base: EvalResult) -> dict[str, object]:
  passed = frozenset(base.passed)
  data = _run_json(base)
  # Diagnostics: the bug tests should NOT pass at base; the pre-existing tests
  # should. A non-empty ``pass_to_pass_missing`` means the harness is shaky.
  data["fail_to_pass_passed"] = sorted(frozenset(spec.fail_to_pass) & passed)
  data["pass_to_pass_missing"] = sorted(frozenset(spec.pass_to_pass) - passed)
  return data


def verify_instance(
    instance: SweBenchProInstance,
    adapter: SweBenchProAdapter,
    provider: DockerProvider,
    *,
    results_dir: Path,
    ws_root: Path,
    timeout: float,
    network: bool,
    prune_images: bool,
) -> dict[str, object]:
  """Run base + golden for one instance, classify, and persist the result.

  Args:
    instance: The dataset record to verify.
    adapter: Builds the instance's ``EvalSpec``.
    provider: Docker interface used to pull images and run the graded runs.
    results_dir: Directory receiving this instance's JSON result file.
    ws_root: Root under which the instance's scratch workspaces are staged.
    timeout: Wall-clock limit for each graded run, in seconds.
    network: Whether the containers may access the network.
    prune_images: Remove the instance's image after both runs.

  Returns:
    The persisted result record, including the verdict.
  """
  iid = instance.instance_id
  result: dict[str, object] = {"instance_id": iid}
  # Wall-clock stamps for this instance's verification (UTC, ISO-8601). The pair
  # doubles as a per-instance duration and lets the summary derive its span.
  result["started_at"] = datetime.now(UTC).isoformat()
  image_ref: str | None = None
  try:
    spec = adapter.eval_spec(instance)
    image_ref = spec.image_ref
    result["image_ref"] = image_ref
    provider.pull(image_ref)
    base = evaluate(
        spec,
        patch=None,
        provider=provider,
        workspace=ws_root / iid / "base",
        checkout_golden_tests=True,
        timeout=timeout,
        network=network,
    )
    golden = evaluate(
        spec,
        patch=instance.patch,
        provider=provider,
        workspace=ws_root / iid / "golden",
        checkout_golden_tests=True,
        timeout=timeout,
        network=network,
    )
    result["verdict"] = classify(spec, base, golden)
    result["base"] = _base_json(spec, base)
    result["golden"] = _run_json(golden)
  except DockerError as exc:
    result["verdict"] = ERROR
    result["error"] = str(exc)
  except Exception as exc:  # noqa: BLE001 — any failure is an inconclusive ERROR
    result["verdict"] = ERROR
    result["error"] = repr(exc)
  finally:
    if prune_images and image_ref is not None:
      provider.remove_image(image_ref)
  result["finished_at"] = datetime.now(UTC).isoformat()
  _write_json(results_dir / f"{iid}.json", result)
  return result


def _write_json(dest: Path, data: dict[str, object]) -> None:
  """Write ``data`` as pretty JSON atomically (tmp file + rename)."""
  dest.parent.mkdir(parents=True, exist_ok=True)
  tmp = dest.with_name(dest.name + ".tmp")
  _ = tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
  _ = tmp.replace(dest)


def _parse_shard(value: str) -> tuple[int, int]:
  try:
    i_str, n_str = value.split("/", 1)
    i, n = int(i_str), int(n_str)
  except ValueError:
    raise argparse.ArgumentTypeError(
        f"--shard must be i/N, got {value!r}"
    ) from None
  if n <= 0 or not 0 <= i < n:
    raise argparse.ArgumentTypeError(f"--shard out of range: {value!r}")
  return i, n


def _out_dir(root: Path, raw: str) -> Path:
  path = Path(raw)
  return path if path.is_absolute() else root / path


def run(args: argparse.Namespace) -> int:
  """Verify this shard's instances, writing one JSON result per instance."""
  root = find_repo_root()
  out_dir = _out_dir(root, args.out_dir)
  results_dir = out_dir / _RESULTS_SUBDIR
  ws_root = cache_root(root) / _WS_SUBDIR
  adapter = SweBenchProAdapter(repo_root=root)
  provider = DockerProvider()

  records = [
      rec
      for rec in load_dataset(args.dataset).records
      if isinstance(rec, SweBenchProInstance)
  ]
  shard_i, shard_n = args.shard
  todo = records[shard_i::shard_n]
  if not args.refresh:
    todo = [
        rec
        for rec in todo
        if not (results_dir / f"{rec.instance_id}.json").is_file()
    ]
  if args.limit:
    todo = todo[: args.limit]  # smoke/debug: cap instances this shard runs
  print(
      f"shard {shard_i}/{shard_n}: {len(todo)} instances to verify"
      f" ({len(records)} total, jobs={args.jobs})",
      flush=True,
  )

  def _task(rec: SweBenchProInstance) -> dict[str, object]:
    return verify_instance(
        rec,
        adapter,
        provider,
        results_dir=results_dir,
        ws_root=ws_root,
        timeout=args.timeout,
        network=not args.no_network,
        prune_images=args.prune_images,
    )

  counts: collections.Counter[str] = collections.Counter()
  with ThreadPoolExecutor(max_workers=args.jobs) as pool:
    futures = [pool.submit(_task, rec) for rec in todo]
    for done, future in enumerate(as_completed(futures), 1):
      result = future.result()  # verify_instance never raises
      verdict = str(result["verdict"])
      counts[verdict] += 1
      print(
          f"[{done}/{len(todo)}] {result['instance_id']}: {verdict}",
          flush=True,
      )
  print(f"shard {shard_i}/{shard_n} done: {dict(counts)}", flush=True)
  return 0


def aggregate(args: argparse.Namespace) -> int:
  """Merge per-instance results into ``summary.json`` + ``report.md``."""
  root = find_repo_root()
  out_dir = _out_dir(root, args.out_dir)
  results_dir = out_dir / _RESULTS_SUBDIR
  results = [
      json.loads(path.read_text())
      for path in sorted(results_dir.glob("*.json"))
  ]
  counts: collections.Counter[str] = collections.Counter(
      str(r.get("verdict", "MISSING")) for r in results
  )
  non_ok = sorted(
      (r for r in results if r.get("verdict") != OK),
      key=lambda r: (str(r.get("verdict")), str(r.get("instance_id"))),
  )
  total = len(load_dataset(args.dataset))
  # Span of the underlying runs, from the per-instance stamps. ISO-8601 UTC
  # strings sort lexicographically, so min/max give the earliest/latest finish.
  finished = sorted(
      str(r["finished_at"]) for r in results if r.get("finished_at")
  )
  summary: dict[str, object] = {
      "dataset": args.dataset,
      "generated_at": datetime.now(UTC).isoformat(),
      "first_verified_at": finished[0] if finished else None,
      "last_verified_at": finished[-1] if finished else None,
      "total": total,
      "verified": len(results),
      "counts": {v: counts.get(v, 0) for v in _VERDICTS},
      "non_ok": [
          {
              "instance_id": r.get("instance_id"),
              "verdict": r.get("verdict"),
              "error": r.get("error"),
          }
          for r in non_ok
      ],
  }
  _write_json(out_dir / "summary.json", summary)
  report = _render_report(summary)
  _ = (out_dir / "report.md").write_text(report)
  print(report, flush=True)
  step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
  if step_summary:
    with open(step_summary, "a") as handle:
      _ = handle.write(report)
  return 0


def _render_report(summary: dict[str, object]) -> str:
  counts = summary["counts"]
  assert isinstance(counts, dict)
  lines = [
      f"# Golden patch validation — {summary['dataset']}",
      "",
      f"Verified {summary['verified']} / {summary['total']} instances.",
      f"Generated at {summary['generated_at']} (runs"
      f" {summary['first_verified_at']} → {summary['last_verified_at']}).",
      "",
      "| verdict | count |",
      "| --- | --- |",
  ]
  lines += [f"| {v} | {counts.get(v, 0)} |" for v in _VERDICTS]
  non_ok = summary["non_ok"]
  assert isinstance(non_ok, list)
  if non_ok:
    lines += [
        "",
        "## Non-OK instances",
        "",
        "| instance | verdict |",
        "| --- | --- |",
    ]
    lines += [f"| {r['instance_id']} | {r['verdict']} |" for r in non_ok]
  return "\n".join(lines) + "\n"


def main() -> int:
  """Parse CLI args and dispatch to ``run`` or ``aggregate``."""
  parser = argparse.ArgumentParser(
      prog="python -m swe_lab.evaluation.verify",
      description="Full-dataset golden verification.",
  )
  _ = parser.add_argument("--dataset", default="swebench_pro")
  _ = parser.add_argument("--out-dir", default=_DEFAULT_OUT)
  _ = parser.add_argument(
      "--aggregate",
      action="store_true",
      help="merge shard results into summary.json + report.md, then exit.",
  )
  _ = parser.add_argument(
      "--shard",
      type=_parse_shard,
      default=(0, 1),
      help="stride shard i/N (default 0/1 = whole dataset).",
  )
  _ = parser.add_argument("--jobs", type=int, default=1)
  _ = parser.add_argument(
      "--limit",
      type=int,
      default=0,
      help="cap instances this shard runs, after sharding (0 = no cap).",
  )
  _ = parser.add_argument("--timeout", type=float, default=1800.0)
  _ = parser.add_argument(
      "--no-network", action="store_true", help="run containers offline."
  )
  _ = parser.add_argument(
      "--prune-images",
      action="store_true",
      help="docker rmi each image after its instance (bounds CI disk).",
  )
  _ = parser.add_argument(
      "--refresh",
      action="store_true",
      help="re-verify instances even if a result already exists.",
  )
  args = parser.parse_args()
  return aggregate(args) if args.aggregate else run(args)


if __name__ == "__main__":
  raise SystemExit(main())
