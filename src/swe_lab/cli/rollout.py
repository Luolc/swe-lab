"""The ``rollout`` subcommand: solve one instance, optionally graded."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
from typing import Annotated

import typer

from swe_lab.core.datasets.loader import load_dataset
from swe_lab.core.datasets.swebench_pro import SweBenchProInstance
from swe_lab.core.datasets.swebench_pro.unit_test import (
    compile_sandbox_spec,
    compile_unit_test,
)
from swe_lab.core.paths import cache_root, find_repo_root
from swe_lab.evaluation.methods.unit_test import run_unit_test
from swe_lab.harnesses.claude_code.constants import (
    DEFAULT_MODEL,
    OAUTH_TOKEN_ENV,
)
from swe_lab.rollout.prompt import build_solve_prompt
from swe_lab.sandbox import DockerHostBackend
from swe_lab.solve import RolloutOutcome, run_rollout

_ROLLOUT_SUBDIR = "rollout_workspaces"
_EVAL_SUBDIR = "eval_workspaces"
_DEFAULT_TIMEOUT_S = 1800.0


def rollout_cmd(
    instance_id: str,
    dataset: str = "swebench_pro",
    model: str = DEFAULT_MODEL,
    grade: Annotated[
        bool, typer.Option(help="Grade the produced patch afterwards.")
    ] = False,
    timeout: Annotated[
        float, typer.Option(help="Seconds before the agent run is killed.")
    ] = _DEFAULT_TIMEOUT_S,
    pull: Annotated[
        bool, typer.Option(help="Pull the image before running.")
    ] = True,
) -> None:
  """Run a headless agent to solve one instance in its container.

  The agent edits the repo; its patch is the worktree diff vs the base commit.
  With ``--grade`` the patch is then run through the instance's tests. An empty
  patch is never graded as a pass. Exit code is 0 unless a graded run fails.
  """
  if not os.environ.get(OAUTH_TOKEN_ENV):
    raise typer.BadParameter(
        f"{OAUTH_TOKEN_ENV} is not set; the agent cannot authenticate."
    )

  instance = load_dataset(dataset).require(instance_id)
  if not isinstance(instance, SweBenchProInstance):
    raise typer.BadParameter(
        f"dataset {dataset!r} is not wired for rollout yet"
    )

  root = find_repo_root()
  spec = compile_sandbox_spec(instance)
  prompt = build_solve_prompt(
      instance.problem_statement,
      requirements=instance.requirements,
      interface=instance.interface,
  )
  workspace = cache_root(root) / _ROLLOUT_SUBDIR / instance.instance_id
  shutil.rmtree(workspace, ignore_errors=True)

  backend = DockerHostBackend(
      network=True, pull=pull, pass_env=[OAUTH_TOKEN_ENV]
  )
  outcome = run_rollout(
      spec,
      prompt=prompt,
      model=model,
      backend=backend,
      workspace=workspace,
      timeout=timeout,
  )

  summary: dict[str, object] = {
      "instance_id": outcome.instance_id,
      "status": outcome.status.value,
      "agent_complete": outcome.complete,
      "is_empty_patch": outcome.is_empty,
      "binary_stripped": outcome.binary_stripped,
      "patch_file": str(outcome.workspace / "patch.diff"),
      "workspace": str(outcome.workspace),
  }
  resolved = _finish(summary, instance, outcome, grade, root, pull, timeout)
  print(json.dumps(summary, indent=2))
  raise typer.Exit(0 if (not grade or resolved) else 1)


def _finish(
    summary: dict[str, object],
    instance: SweBenchProInstance,
    outcome: RolloutOutcome,
    grade: bool,
    root: Path,
    pull: bool,
    timeout: float,
) -> bool:
  """Record the run's ``outcome`` string (and grade), returning ``resolved``.

  An explicit outcome makes an unresolved run's *reason* readable, never
  guessed: ``empty_patch`` (no edits — grading skipped) is distinct from
  ``unresolved_tests_failed`` (a real patch that graded false).

  Args:
    summary: The summary dict to record ``outcome``/``grade`` into.
    instance: The instance (for compiling the grade run).
    outcome: The rollout outcome (its patch is graded).
    grade: Whether to grade at all.
    root: The repo root (for cache/workspace paths).
    pull: Whether to pull the image for the grade run.
    timeout: Seconds before the grade run is killed.

  Returns:
    Whether the patch resolved the instance (always ``False`` when not graded).
  """
  if not grade:
    summary["outcome"] = "solved_not_graded"
    return False
  if outcome.is_empty:
    summary["outcome"] = "empty_patch"
    summary["grade"] = {"resolved": False, "reason": "empty_patch"}
    return False

  sandbox_spec, unit_spec = compile_unit_test(
      instance, patch=outcome.patch, repo_root=root
  )
  eval_ws = cache_root(root) / _EVAL_SUBDIR / instance.instance_id
  shutil.rmtree(eval_ws, ignore_errors=True)
  _, verdict = run_unit_test(
      sandbox_spec,
      unit_spec,
      backend=DockerHostBackend(network=False, pull=pull),
      workspace=eval_ws,
      timeout=timeout,
  )
  resolved = bool(verdict and verdict.resolved)
  summary["outcome"] = "resolved" if resolved else "unresolved_tests_failed"
  if verdict is not None:
    summary["grade"] = {
        "resolved": verdict.resolved,
        "score": verdict.score,
        "output_state": verdict.output_state.value,
        "passed": sorted(verdict.passed),
        "missing": sorted(verdict.missing),
    }
  return resolved
