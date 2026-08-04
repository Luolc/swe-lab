"""The ``eval`` subcommand: grade one instance's patch in a container."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from swe_lab.cli.persist_wiring import run_store, run_ts
from swe_lab.cli.sandbox_wiring import invocation_config
from swe_lab.datasets.instance import TaskInstance
from swe_lab.datasets.loader import load_dataset
from swe_lab.evaluation.methods.unit_test import UnitTestEvalTask, verdict_of
from swe_lab.paths import cache_root, find_repo_root
from swe_lab.sandbox import Inline, Mount
from swe_lab.sandbox.observers import PATCH_NAME
from swe_lab.workflow import run_task, TaskAddress

_WORKSPACES_SUBDIR = "eval_workspaces"
# The task segment of this command's persisted records (ADR-0007 §6).
_TASK_KEY = "eval"


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
            help="Extra grading attempts after a failure. The patch "
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
    # Not the same as grading the base commit, which is a legitimate request a
    # dataset with no reference solution can still answer. `--gold` it cannot,
    # and falling through would grade the wrong thing and report it as the
    # gold patch failing.
    if patch is None:
      raise typer.BadParameter(
          f"dataset {dataset!r} carries no gold patch for {instance_id}"
      )
  else:
    assert patch_file is not None  # guaranteed by the exactly-one check above
    patch = patch_file.read_text()

  root = find_repo_root()
  output_dir = cache_root(root) / _WORKSPACES_SUBDIR / instance.instance_id
  # A re-run of a one-off command re-runs it: the previous run's attempts and
  # their workspaces go, and `resume=False` below ignores its terminal marker.
  output_dir.rmtree(missing_ok=True)

  # The patch is the task's declared input: whether it came from --gold or a
  # file, it enters the one channel a workflow edge would use.
  run = run_task(
      UnitTestEvalTask(),
      instance,
      store=run_store(root, persist_to_t1=persist, scratch=output_dir),
      address=TaskAddress(sweep_id=sweep, rollout_id=0, task=_TASK_KEY),
      backend=backend,
      sandbox=invocation_config(backend, network=network, pull=pull),
      output_dir=output_dir,
      timeout=timeout,
      retries=retries,
      resume=False,
      run_ts=run_ts(),
      extra_mounts={PATCH_NAME: Mount(Inline(patch.encode("utf-8")))},
      extra_record=instance.run_provenance(),
  )
  assert run.result is not None  # resume=False always executes

  verdict = verdict_of(run.result)
  resolved = bool(verdict and verdict.resolved)
  # What makes this verdict mean less than it appears — a harness fix that
  # altered the graded tree, a measured flake rate. Surfaced in the summary as
  # well as persisted: whoever reads this JSON is the one who needs the caveat.
  summary: dict[str, object] = {
      "instance_id": instance.instance_id,
      "status": run.result.run.status.value,
      "resolved": resolved,
      # Attempts are the runner's fact now (ADR-0008), not the verdict's: one
      # verdict grades one tree, and "it took two tries" belongs to the run.
      "attempts": run.attempts,
      "flaky": run.attempts > 1 and resolved,
  }
  if verdict is not None:
    summary |= {"score": verdict.score} | verdict.summary()
  if run.result.run.error is not None:
    summary["error"] = repr(run.result.run.error)
  summary |= instance.run_provenance()
  if persist:
    summary["persisted"] = {
        "run_ts": run.record.run_ts,
        "keys": run.record.artifact_keys,
    }
  print(json.dumps(summary, indent=2))
  raise typer.Exit(0 if resolved else 1)
