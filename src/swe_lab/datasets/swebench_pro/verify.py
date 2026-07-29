"""Full-dataset golden verification — a SWE-Bench-Pro dataset QA tool.

Lives inside ``datasets/swebench_pro/`` (not the generic ``cli/``): its
classification is SWE-Bench-Pro semantics (``fail_to_pass`` must fail at base,
the golden patch must resolve), so it is dataset-specific by design. When more
SWE-like datasets are ported, the shared parts can be lifted to a dataset-
agnostic seam. Run it as ``python -m swe_lab.datasets.swebench_pro.verify``.

For each instance we do two graded runs in its image and check the pair:

- **base** (no patch, golden tests restored) must NOT resolve — the required
  tests fail without the fix;
- **golden** (the dataset's own patch, golden tests restored) must resolve.

A mismatch means the instance is suspect: its tests don't detect the bug
(``BASE_UNEXPECTED_PASS``) or its golden patch doesn't fix it under our harness
(``GOLDEN_FAIL``). Runs are stride-sharded (``--shard i/N``) so a GitHub Actions
matrix can burst the whole dataset in parallel; each shard appends one **T1
store shard** per instance (``RunRecord``, keyed under ``--sweep``) — resumable
(a done instance is skipped) and object-store-backed, not committed to git.
``--aggregate`` reads the sweep's shards into a summary + report. The store is a
local ``FilesystemStore`` today; task 13 points it at R2 with no change here.

The two graded runs go through ``instance.unit_test_spec`` + ``run_unit_test``
on the sandbox engine, so the verdict is a ``SweBenchProVerdict`` and the run
outcome a ``RunResult``.
"""

from __future__ import annotations

import collections
from concurrent.futures import as_completed, ThreadPoolExecutor
import contextlib
from datetime import datetime, UTC
import json
import os
import subprocess
from typing import Annotated

from etils import epath
import typer

from swe_lab.datasets.loader import load_dataset
from swe_lab.evaluation.methods.unit_test import run_unit_test
from swe_lab.paths import cache_root, find_repo_root
from swe_lab.sandbox import (
    build_sandbox,
    build_store,
    RunRecord,
    RunResult,
    RUNS_NAMESPACE,
    RunStatus,
    Store,
)

from .record import SweBenchProInstance
from .unit_test import OutputState, SweBenchProVerdict

# Verdicts.
OK = "OK"
BASE_UNEXPECTED_PASS = "BASE_UNEXPECTED_PASS"
GOLDEN_FAIL = "GOLDEN_FAIL"
ERROR = "ERROR"  # inconclusive: harness/infra failure, not a dataset finding
_VERDICTS = (OK, BASE_UNEXPECTED_PASS, GOLDEN_FAIL, ERROR)

# Verify persists each instance's result to the T1 object-store seam (one
# RunRecord shard per instance), not a committed outputs/ dir. Verification is
# inherently single-rollout (base + golden together decide one verdict), so
# every shard is rollout 0 / attempt 0; that key is deterministic, so a
# re-verify overwrites its own shard (ADR-0004 — no fake timestamp needed).
_DEFAULT_SWEEP = "golden-verify"
_STORE_SUBDIR = "store"  # the FilesystemStore root under cache_root
# One verification per instance; K-sampling is a solving concern, not a QA one.
_ROLLOUT_ID = 0
_WS_SUBDIR = "golden_verify"  # scratch workspaces under cache_root

# One graded run: the engine outcome paired with the verdict it produced
# (``None`` when grading never ran because the run failed to come up).
_Run = tuple[RunResult, SweBenchProVerdict | None]


def _store_root(root: epath.PathLike) -> epath.Path:
  """Return the run store's root directory.

  The ``runs`` namespace lives here rather than in any key (ADR-0004), so this
  is also where a sweep's aggregated report is written.
  """
  return cache_root(root) / _STORE_SUBDIR / RUNS_NAMESPACE


def _store(root: epath.PathLike) -> Store:
  """Return the T1 store verification persists its shards to."""
  return build_store("filesystem", root=_store_root(root))


def classify(instance: SweBenchProInstance, base: _Run, golden: _Run) -> str:
  """Return the verdict for one instance from its base + golden runs.

  ``ERROR`` (either run failed to come up, or produced no gradeable output) is
  kept distinct from real dataset findings so infra flakes don't masquerade as
  them. A non-``SUCCESS`` ``RunStatus`` replaces the old timeout/infra check,
  and an ``output_state`` other than ``OK`` replaces the old ``output_found``.
  """
  for run_result, verdict in (base, golden):
    if (
        run_result.status is not RunStatus.SUCCESS
        or verdict is None
        or verdict.output_state is not OutputState.OK
    ):
      return ERROR
  _, base_verdict = base
  _, golden_verdict = golden
  # The loop above proves both verdicts are present and parsed.
  assert base_verdict is not None and golden_verdict is not None
  if not golden_verdict.resolved:
    return GOLDEN_FAIL
  if base_verdict.resolved or (
      frozenset(instance.fail_to_pass) & base_verdict.passed
  ):
    return BASE_UNEXPECTED_PASS
  return OK


def _run_json(run: _Run) -> dict[str, object]:
  run_result, verdict = run
  return {
      "status": run_result.status.value,
      "resolved": bool(verdict and verdict.resolved),
      "output_state": verdict.output_state.value if verdict else None,
      "n_passed": len(verdict.passed) if verdict else 0,
      "missing": sorted(verdict.missing) if verdict else [],
  }


def _base_json(instance: SweBenchProInstance, base: _Run) -> dict[str, object]:
  _, verdict = base
  passed = verdict.passed if verdict else frozenset()
  data = _run_json(base)
  # Diagnostics: the bug tests should NOT pass at base; the pre-existing tests
  # should. A non-empty ``pass_to_pass_missing`` means the harness is shaky.
  data["fail_to_pass_passed"] = sorted(
      frozenset(instance.fail_to_pass) & passed
  )
  data["pass_to_pass_missing"] = sorted(
      frozenset(instance.pass_to_pass) - passed
  )
  return data


def _graded_run(
    instance: SweBenchProInstance,
    *,
    patch: str | None,
    workspace: epath.PathLike,
    timeout: float,
    no_network: bool,
) -> _Run:
  """Compile and run one graded run (base or golden) in a fresh workspace.

  Args:
    instance: The instance to grade.
    patch: The diff to apply (``None`` for the base self-check).
    workspace: Host workspace for this run (wiped clean first).
    timeout: Wall-clock limit for the run, in seconds.
    no_network: Run the container offline.

  Returns:
    The engine ``RunResult`` and the verdict (``None`` if grading never ran).
  """
  unit_test_spec = instance.unit_test_spec(patch=patch)
  # The sandbox refuses a non-empty workspace; start each run clean.
  epath.Path(workspace).rmtree(missing_ok=True)
  sandbox = build_sandbox(
      "host",
      instance.sandbox_spec(),
      workspace=workspace,
      network=not no_network,
      pull=True,
  )
  return run_unit_test(
      sandbox, unit_test_spec, output_dir=workspace, timeout=timeout
  )


def _prune_image(image_ref: str) -> None:
  """Best-effort ``docker image rm -f`` — swallow any failure.

  The engine backend has no image-removal seam, so pruning shells out. A prune
  failure must never fail the instance, so every error is swallowed.

  Args:
    image_ref: The image reference to remove.
  """
  with contextlib.suppress(Exception):
    _ = subprocess.run(
        ["docker", "image", "rm", "-f", image_ref],
        check=False,
        capture_output=True,
    )


def verify_instance(
    instance: SweBenchProInstance,
    *,
    store: Store,
    sweep: str,
    ws_root: epath.PathLike,
    timeout: float,
    no_network: bool,
    prune_images: bool,
) -> dict[str, object]:
  """Run base + golden for one instance, classify, and persist a T1 shard.

  Args:
    instance: The dataset record to verify.
    store: The T1 store the result shard is appended to.
    sweep: The sweep id this verification is keyed under.
    ws_root: Root under which the instance's scratch workspaces are staged.
    timeout: Wall-clock limit for each graded run, in seconds.
    no_network: Run the containers offline.
    prune_images: Remove the instance's image after both runs.

  Returns:
    The result record (``instance_id`` + ``verdict`` + run detail).
  """
  iid = instance.instance_id
  result: dict[str, object] = {"instance_id": iid}
  # Wall-clock stamps for this instance's verification (UTC, ISO-8601). The pair
  # doubles as a per-instance duration and lets the summary derive its span.
  result["started_at"] = datetime.now(UTC).isoformat()
  # The image is fixed by the instance; capture it up front so pruning still
  # runs even if compiling the graded runs fails.
  image_ref = instance.sandbox_spec().image_ref
  result["image_ref"] = image_ref
  try:
    base = _graded_run(
        instance,
        patch=None,
        workspace=epath.Path(ws_root) / iid / "base",
        timeout=timeout,
        no_network=no_network,
    )
    golden = _graded_run(
        instance,
        patch=instance.patch,
        workspace=epath.Path(ws_root) / iid / "golden",
        timeout=timeout,
        no_network=no_network,
    )
    result["verdict"] = classify(instance, base, golden)
    result["base"] = _base_json(instance, base)
    result["golden"] = _run_json(golden)
  except Exception as exc:  # noqa: BLE001 — any failure is an inconclusive ERROR
    result["verdict"] = ERROR
    result["error"] = repr(exc)
  finally:
    if prune_images:
      _prune_image(image_ref)
  result["finished_at"] = datetime.now(UTC).isoformat()
  # One T1 shard per instance: the verdict is the record status; the base/golden
  # detail (and any error) lives in `extra`. The key is deterministic, so a
  # re-verify overwrites in place and a sweep holds one shard per instance.
  store.append_manifest(
      RunRecord(
          sweep_id=sweep,
          instance_id=iid,
          rollout_id=_ROLLOUT_ID,
          run_ts=datetime.now(UTC).isoformat(),
          status=str(result["verdict"]),
          tier="formal",
          backend="host",
          extra={
              key: value
              for key, value in result.items()
              if key not in ("instance_id", "verdict")
          },
      )
  )
  return result


def _write_json(dest: epath.PathLike, data: dict[str, object]) -> None:
  """Write ``data`` as pretty JSON atomically (tmp file + rename)."""
  dest = epath.Path(dest)
  dest.parent.mkdir(parents=True, exist_ok=True)
  tmp = dest.with_name(dest.name + ".tmp")
  _ = tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
  _ = tmp.replace(dest)


def _parse_shard(value: str) -> tuple[int, int]:
  """Parse and validate a ``i/N`` stride-shard spec.

  Args:
    value: The raw ``--shard`` string.

  Returns:
    The ``(i, N)`` shard pair.

  Raises:
    typer.BadParameter: If ``value`` is malformed or out of range.
  """
  try:
    i_str, n_str = value.split("/", 1)
    i, n = int(i_str), int(n_str)
  except ValueError:
    raise typer.BadParameter(f"--shard must be i/N, got {value!r}") from None
  if n <= 0 or not 0 <= i < n:
    raise typer.BadParameter(f"--shard out of range: {value!r}")
  return i, n


def run(
    *,
    dataset: str,
    sweep: str,
    shard: tuple[int, int],
    jobs: int,
    limit: int,
    timeout: float,
    no_network: bool,
    prune_images: bool,
    refresh: bool,
) -> int:
  """Verify this shard's instances, appending one T1 shard per instance.

  Args:
    dataset: The dataset to verify.
    sweep: The sweep id results are keyed under in the store.
    shard: The ``(i, N)`` stride shard to run.
    jobs: Worker threads.
    limit: Cap instances this shard runs, after sharding (0 = no cap).
    timeout: Wall-clock limit for each graded run, in seconds.
    no_network: Run the containers offline.
    prune_images: Remove each image after its instance.
    refresh: Re-verify instances even if a result already exists.

  Returns:
    A process exit code (always 0).
  """
  root = find_repo_root()
  store = _store(root)
  ws_root = cache_root(root) / _WS_SUBDIR

  records = [
      rec
      for rec in load_dataset(dataset).records
      if isinstance(rec, SweBenchProInstance)
  ]
  shard_i, shard_n = shard
  todo = records[shard_i::shard_n]
  if not refresh:
    # Done is keyed by (instance, rollout), not instance alone, so a future
    # multi-rollout sweep resumes per sample. A recorded failure still counts as
    # done (ADR-0004): retrying it is `attempt`'s job, not resume's.
    done = {(r.instance_id, r.rollout_id) for r in store.read_manifests(sweep)}
    todo = [rec for rec in todo if (rec.instance_id, _ROLLOUT_ID) not in done]
  if limit:
    todo = todo[:limit]  # smoke/debug: cap instances this shard runs
  print(
      f"shard {shard_i}/{shard_n}: {len(todo)} instances to verify"
      f" ({len(records)} total, jobs={jobs})",
      flush=True,
  )

  def _task(rec: SweBenchProInstance) -> dict[str, object]:
    return verify_instance(
        rec,
        store=store,
        sweep=sweep,
        ws_root=ws_root,
        timeout=timeout,
        no_network=no_network,
        prune_images=prune_images,
    )

  counts: collections.Counter[str] = collections.Counter()
  with ThreadPoolExecutor(max_workers=jobs) as pool:
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


def aggregate(*, dataset: str, sweep: str) -> int:
  """Merge the sweep's per-instance shards into a summary + report.

  Args:
    dataset: The dataset the results belong to (recorded in the summary).
    sweep: The sweep id whose shards to aggregate.

  Returns:
    A process exit code (always 0).
  """
  root = find_repo_root()
  store = _store(root)
  report_dir = _store_root(root) / sweep
  records = store.read_manifests(sweep)
  counts: collections.Counter[str] = collections.Counter(
      r.status for r in records
  )
  non_ok = sorted(
      (r for r in records if r.status != OK),
      key=lambda r: (r.status, r.instance_id),
  )
  total = len(load_dataset(dataset))
  # Span of the underlying runs, from the per-instance stamps. ISO-8601 UTC
  # strings sort lexicographically, so min/max give the earliest/latest finish.
  finished = sorted(
      str(r.extra["finished_at"]) for r in records if r.extra.get("finished_at")
  )
  summary: dict[str, object] = {
      "dataset": dataset,
      "sweep": sweep,
      "generated_at": datetime.now(UTC).isoformat(),
      "first_verified_at": finished[0] if finished else None,
      "last_verified_at": finished[-1] if finished else None,
      "total": total,
      "verified": len(records),
      "counts": {v: counts.get(v, 0) for v in _VERDICTS},
      "non_ok": [
          {
              "instance_id": r.instance_id,
              "verdict": r.status,
              "error": r.extra.get("error"),
          }
          for r in non_ok
      ],
  }
  _write_json(report_dir / "summary.json", summary)
  report = _render_report(summary)
  _ = (report_dir / "report.md").write_text(report)
  print(report, flush=True)
  step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
  if step_summary:
    with open(step_summary, "a") as handle:
      _ = handle.write(report)
  return 0


def _render_report(summary: dict[str, object]) -> str:
  """Render the merged ``summary`` as a Markdown report.

  Args:
    summary: The aggregated summary dict.

  Returns:
    The report text, newline-terminated.
  """
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


def verify_cmd(
    dataset: Annotated[
        str, typer.Option(help="Dataset to verify.")
    ] = "swebench_pro",
    sweep: Annotated[
        str,
        typer.Option(help="Sweep id results are keyed under in the T1 store."),
    ] = _DEFAULT_SWEEP,
    aggregate_: Annotated[
        bool,
        typer.Option(
            "--aggregate",
            help="Merge shard results into summary.json + report.md, exit.",
        ),
    ] = False,
    shard: Annotated[
        str, typer.Option(help="Stride shard i/N (default 0/1 = all).")
    ] = "0/1",
    jobs: Annotated[int, typer.Option(help="Worker threads.")] = 1,
    limit: Annotated[
        int,
        typer.Option(help="Cap instances after sharding (0 = no cap)."),
    ] = 0,
    timeout: Annotated[
        float, typer.Option(help="Seconds before each graded run is killed.")
    ] = 1800.0,
    no_network: Annotated[
        bool, typer.Option(help="Run the containers offline.")
    ] = False,
    prune_images: Annotated[
        bool,
        typer.Option(help="docker rmi each image after its instance."),
    ] = False,
    refresh: Annotated[
        bool,
        typer.Option(help="Re-verify instances even if a result exists."),
    ] = False,
) -> None:
  """Full-dataset golden verification for SWE-Bench Pro.

  Runs base + golden for each instance in this shard and classifies the pair
  into OK / BASE_UNEXPECTED_PASS / GOLDEN_FAIL / ERROR. With ``--aggregate``,
  merges the per-instance results into a summary + report instead.
  """
  parsed_shard = _parse_shard(shard)
  code = (
      aggregate(dataset=dataset, sweep=sweep)
      if aggregate_
      else run(
          dataset=dataset,
          sweep=sweep,
          shard=parsed_shard,
          jobs=jobs,
          limit=limit,
          timeout=timeout,
          no_network=no_network,
          prune_images=prune_images,
          refresh=refresh,
      )
  )
  raise typer.Exit(code)


def main() -> None:
  """Entry point for ``python -m swe_lab.datasets.swebench_pro.verify``."""
  typer.run(verify_cmd)


if __name__ == "__main__":
  main()
