"""The ``rollout`` subcommand: solve one instance, optionally graded."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
from typing import Annotated

import typer

from swe_lab.cli.persist_wiring import persist_run
from swe_lab.datasets.instance import TaskInstance
from swe_lab.datasets.loader import load_dataset
from swe_lab.evaluation.methods.unit_test import run_unit_test
from swe_lab.evaluation.verdict import Verdict
from swe_lab.harnesses.claude_code import Capture
from swe_lab.harnesses.claude_code.constants import (
    API_KEY_ENV,
    DEFAULT_MODEL,
    OAUTH_TOKEN_ENV,
)
from swe_lab.paths import cache_root, find_repo_root
from swe_lab.rollout import RolloutOutcome, run_rollout

_ROLLOUT_SUBDIR = "rollout_workspaces"
_EVAL_SUBDIR = "eval_workspaces"
_DEFAULT_TIMEOUT_S = 1800.0


def rollout_in_docker(
    instance_id: Annotated[
        str, typer.Argument(help="The instance to solve (e.g. acme__widget-1).")
    ],
    dataset: Annotated[
        str, typer.Option(help="Dataset the instance belongs to.")
    ] = "swebench_pro",
    model: Annotated[
        str, typer.Option(help="Model alias or id the agent runs as.")
    ] = DEFAULT_MODEL,
    grade: Annotated[
        bool, typer.Option(help="Grade the produced patch afterwards.")
    ] = False,
    timeout: Annotated[
        float, typer.Option(help="Seconds before the agent run is killed.")
    ] = _DEFAULT_TIMEOUT_S,
    pull: Annotated[
        bool, typer.Option(help="Pull the image before running.")
    ] = True,
    capture: Annotated[
        Capture, typer.Option(help="Agent-trace capture strategy.")
    ] = Capture.STREAM,
    backend: Annotated[
        str,
        typer.Option(help="Sandbox backend name (host Docker, or the GH job)."),
    ] = "host",
    bare: Annotated[
        bool,
        typer.Option(
            help="Run the agent with --bare (API-key auth; needs "
            f"{API_KEY_ENV} set)."
        ),
    ] = False,
    persist: Annotated[
        bool,
        typer.Option(help="Persist the run's artifacts to the T1 store."),
    ] = False,
    sweep: Annotated[
        str, typer.Option(help="Sweep id the persisted run is keyed under.")
    ] = "adhoc",
) -> None:
  """Run a headless agent to solve one instance in its container.

  The agent edits the repo; its patch is the worktree diff vs the base commit.
  With ``--grade`` the patch is then run through the instance's tests. An empty
  patch is never graded as a pass. Exit code is 0 unless a graded run fails.
  """
  # Bare mode disables the subscription OAuth token and authenticates with an
  # API key instead, so it needs a different secret. Either way the secret is
  # read from the ambient env and passed to the sandbox by reference (its name,
  # never its value on the command line) — see pass_env below.
  auth_env = API_KEY_ENV if bare else OAUTH_TOKEN_ENV
  if not os.environ.get(auth_env):
    raise typer.BadParameter(
        f"{auth_env} is not set; the agent cannot authenticate."
    )

  instance = load_dataset(dataset).require(instance_id)
  if not isinstance(instance, TaskInstance):
    raise typer.BadParameter(f"dataset {dataset!r} is not runnable for rollout")

  root = find_repo_root()
  spec = instance.sandbox_spec()
  prompt = instance.solve_prompt()
  workspace = cache_root(root) / _ROLLOUT_SUBDIR / instance.instance_id
  shutil.rmtree(workspace, ignore_errors=True)

  outcome = run_rollout(
      spec,
      prompt=prompt,
      model=model,
      backend=backend,
      workspace=workspace,
      timeout=timeout,
      capture=capture,
      pull=pull,
      pass_env=(auth_env,),
      bare=bare,
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
  if persist:
    record = persist_run(
        root,
        sweep=sweep,
        instance_id=outcome.instance_id,
        status=outcome.status.value,
        backend=backend,
        artifacts=outcome.artifacts,
        model=model,
        metrics=outcome.metrics,
        extra={
            "agent_complete": outcome.complete,
            "is_empty_patch": outcome.is_empty,
            "binary_stripped": outcome.binary_stripped,
        },
    )
    summary["persisted"] = {"run_ts": record.run_ts, "keys": record.artifacts}
  resolved = _finish(
      summary, instance, outcome, grade, root, pull, timeout, backend
  )
  print(json.dumps(summary, indent=2))
  raise typer.Exit(0 if (not grade or resolved) else 1)


def _finish(
    summary: dict[str, object],
    instance: TaskInstance[Verdict],
    outcome: RolloutOutcome,
    grade: bool,
    root: Path,
    pull: bool,
    timeout: float,
    backend: str,
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
    backend: Which sandbox backend to grade on.

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

  sandbox_spec = instance.sandbox_spec()
  unit_spec = instance.unit_test_spec(patch=outcome.patch, repo_root=root)
  eval_ws = cache_root(root) / _EVAL_SUBDIR / instance.instance_id
  shutil.rmtree(eval_ws, ignore_errors=True)
  _, verdict = run_unit_test(
      sandbox_spec,
      unit_spec,
      backend=backend,
      workspace=eval_ws,
      timeout=timeout,
      pull=pull,
  )
  resolved = bool(verdict and verdict.resolved)
  summary["outcome"] = "resolved" if resolved else "unresolved_tests_failed"
  if verdict is not None:
    summary["grade"] = {
        "resolved": verdict.resolved,
        "score": verdict.score,
    } | verdict.summary()
  return resolved
