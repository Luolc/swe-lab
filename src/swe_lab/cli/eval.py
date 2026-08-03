"""The ``eval`` subcommand: grade one instance's patch in a container."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from swe_lab.cli.persist_wiring import persist_run
from swe_lab.datasets.instance import TaskInstance
from swe_lab.datasets.loader import load_dataset
from swe_lab.evaluation.methods.unit_test import run_unit_test
from swe_lab.evaluation.verdict import Verdict
from swe_lab.paths import cache_root, find_repo_root
from swe_lab.sandbox import build_sandbox

_WORKSPACES_SUBDIR = "eval_workspaces"
# The task segment of this command's persisted records (ADR-0007 §6).
_TASK_KEY = "eval"


def _persistable(verdict: Verdict | None) -> dict[str, object]:
  """Return the verdict's non-numeric detail worth keeping in a run record.

  Scalars only, and chosen without knowing the dataset: a verdict's ``summary``
  also carries lists (every passed / missing test name), which would grow the
  shard with the instance — one SWE-Bench Pro instance requires 681 tests. The
  scalar entries (``output_state``, ``first_missing``) are what actually explain
  a failed grade at a glance; the counts travel as metrics.

  Args:
    verdict: The graded verdict, or ``None`` when grading never ran.

  Returns:
    ``eval_``-prefixed scalar entries, empty when there is no verdict.
  """
  if verdict is None:
    return {}
  return {
      f"eval_{key}": value
      for key, value in verdict.summary().items()
      if value is None or isinstance(value, (str, int, float, bool))
  }


def eval_cmd(
    instance_id: str,
    dataset: str = "swebench_pro",
    gold: Annotated[
        bool, typer.Option(help="Grade the instance's own gold patch.")
    ] = False,
    patch_file: Annotated[
        Path | None,
        typer.Option(help="Path to a candidate .diff to grade."),
    ] = None,
    timeout: Annotated[
        float, typer.Option(help="Seconds before each eval attempt is killed.")
    ] = 1800.0,
    retries: Annotated[
        int,
        typer.Option(
            help="Extra grading attempts after a failure (ADR-0005). The patch "
            "is identical on every attempt, so this averages out harness "
            "flakiness, not model error; 0 disables it."
        ),
    ] = 1,
    network: Annotated[
        bool, typer.Option(help="Give the container network access.")
    ] = True,
    pull: Annotated[
        bool, typer.Option(help="Pull the image before running.")
    ] = True,
    backend: Annotated[
        str,
        typer.Option(help="Sandbox backend name (host Docker, or the GH job)."),
    ] = "host",
    persist: Annotated[
        bool,
        typer.Option(help="Persist the run's result to the T1 store."),
    ] = False,
    sweep: Annotated[
        str, typer.Option(help="Sweep id the persisted run is keyed under.")
    ] = "adhoc",
) -> None:
  """Grade one instance by running its tests in its container.

  Applies the patch (``--gold`` for the instance's own gold patch, or a
  candidate via ``--patch-file``), runs the instance's test suite, and reports
  the verdict. Exit code is 0 iff the patch resolves the instance.
  """
  if gold == (patch_file is not None):
    raise typer.BadParameter("pass exactly one of --gold / --patch-file")

  instance = load_dataset(dataset).require(instance_id)
  if not isinstance(instance, TaskInstance):
    raise typer.BadParameter(f"dataset {dataset!r} is not runnable for eval")

  if gold:
    patch = instance.gold_patch()
    # Not the same as `patch=None`, which means "grade the base commit" and is a
    # legitimate request. A dataset with no reference solution cannot answer
    # `--gold` at all, and falling through would grade the wrong thing and
    # report it as the gold patch failing.
    if patch is None:
      raise typer.BadParameter(
          f"dataset {dataset!r} carries no gold patch for {instance_id}"
      )
  else:
    assert patch_file is not None  # guaranteed by the exactly-one check above
    patch = patch_file.read_text()

  root = find_repo_root()
  unit_test_spec = instance.unit_test_spec(patch=patch)
  workspace = cache_root(root) / _WORKSPACES_SUBDIR / instance.instance_id
  # The sandbox refuses a non-empty workspace; a fresh grade starts clean.
  workspace.rmtree(missing_ok=True)

  # Construct the sandbox here (the caller owns backend + its options); the eval
  # runner just receives it.
  sandbox = build_sandbox(
      backend,
      instance.sandbox_spec(),
      workspace=workspace,
      network=network,
      pull=pull,
  )
  result, verdict = run_unit_test(
      sandbox,
      unit_test_spec,
      output_dir=workspace,
      timeout=timeout,
      retries=retries,
  )

  # What makes this verdict mean less than it appears — a harness fix that
  # altered the graded tree, a measured flake rate. Surfaced in the summary as
  # well as persisted: whoever reads this JSON is the one who needs the caveat.
  provenance = instance.run_provenance()
  summary: dict[str, object] = {
      "instance_id": instance.instance_id,
      "status": result.status.value,
      "resolved": bool(verdict and verdict.resolved),
  }
  if verdict is not None:
    summary |= {"score": verdict.score} | verdict.summary()
  if result.error is not None:
    summary["error"] = repr(result.error)
  summary |= provenance
  if persist:
    record = persist_run(
        root,
        sweep=sweep,
        instance_id=instance.instance_id,
        task=_TASK_KEY,
        status=result.status.value,
        backend=backend,
        artifacts=result.artifacts,
        metrics=result.metrics,
        extra={"resolved": bool(verdict and verdict.resolved)}
        | _persistable(verdict)
        | provenance,
    )
    summary["persisted"] = {
        "run_ts": record.run_ts,
        "keys": record.artifact_keys,
    }
  print(json.dumps(summary, indent=2))
  raise typer.Exit(0 if summary["resolved"] else 1)
