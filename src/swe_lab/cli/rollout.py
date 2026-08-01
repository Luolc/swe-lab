"""The ``rollout`` subcommand: solve one instance, optionally graded."""

from __future__ import annotations

import contextlib
import json
import os
from typing import Annotated
import zlib

from etils import epath
import typer

from swe_lab.cli.persist_wiring import persist_run
from swe_lab.datasets.instance import TaskInstance
from swe_lab.datasets.loader import load_dataset
from swe_lab.evaluation.methods.unit_test import run_unit_test
from swe_lab.evaluation.verdict import Verdict
from swe_lab.harnesses.claude_code import Capture, ClaudeCodeHarness
from swe_lab.harnesses.claude_code.constants import (
    API_KEY_ENV,
    CONTAINER_PROXY_HOST,
    DEFAULT_MODEL,
    OAUTH_TOKEN_ENV,
    PROXY_LOG_NAME,
)
from swe_lab.harnesses.claude_code.proxy import (
    build_proxy,
    port_for_index,
    ReverseProxy,
)
from swe_lab.paths import cache_root, find_repo_root
from swe_lab.rollout import RolloutOutcome, run_rollout
from swe_lab.sandbox import build_sandbox

_ROLLOUT_SUBDIR = "rollout_workspaces"
_EVAL_SUBDIR = "eval_workspaces"
_DEFAULT_TIMEOUT_S = 1800.0
# Proxy ports are drawn from a wide band by a stable hash of the instance id, so
# concurrent rollouts on one host never collide (mirrors W1's per-run distinct
# port discipline, which keyed off the dataset index).
_PROXY_PORT_SPAN = 10000


def _build_agent(
    instance_id: str,
    *,
    model: str,
    capture: Capture,
    bare: bool,
    output_dir: epath.PathLike,
) -> tuple[ClaudeCodeHarness, contextlib.AbstractContextManager[object]]:
  """Build this CLI's harness + its trace recorder for the capture mode.

  Construction lives here, not in ``run_rollout``: the caller picks the agent
  (this CLI ships Claude Code) and hands the composition the built pair.

  For ``STREAM`` the agent's own event stream is the trace, so there is no
  recorder. For ``PROXY`` a host-side ``cc-reverse-proxy`` records into
  ``output_dir`` and the agent is pointed at it (the container reaches the host
  through the ``host.docker.internal`` gateway the host backend always maps).

  Args:
    instance_id: Keys the proxy port, so concurrent rollouts never collide.
    model: The ``--model`` alias the agent runs as.
    capture: The trace-capture strategy.
    bare: Run the agent with ``--bare`` (API-key auth).
    output_dir: Where a proxy recording is written.

  Returns:
    The harness and a context manager held open around the run.
  """
  if capture is Capture.STREAM:
    return ClaudeCodeHarness(model=model, bare=bare), contextlib.nullcontext()
  port = port_for_index(zlib.crc32(instance_id.encode()) % _PROXY_PORT_SPAN)
  harness = ClaudeCodeHarness(
      model=model,
      capture=capture,
      proxy_base_url=f"http://{CONTAINER_PROXY_HOST}:{port}",
      bare=bare,
  )
  proxy = ReverseProxy(
      port,
      epath.Path(output_dir) / PROXY_LOG_NAME,
      build_proxy(find_repo_root()),
  )
  return harness, proxy


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
  workspace.rmtree(missing_ok=True)

  sandbox = build_sandbox(
      backend,
      spec,
      workspace=workspace,
      network=True,
      pull=pull,
      pass_env=(auth_env,),
  )
  harness, proxy = _build_agent(
      instance.instance_id,
      model=model,
      capture=capture,
      bare=bare,
      output_dir=workspace,
  )
  outcome = run_rollout(
      sandbox,
      harness,
      prompt=prompt,
      output_dir=workspace,
      timeout=timeout,
      proxy=proxy,
  )

  # Carried even on a solve-only run: `--grade` reuses this summary, and a
  # reader of an unresolved result needs to know the instance flakes.
  provenance = instance.run_provenance()
  summary: dict[str, object] = {
      "instance_id": outcome.instance_id,
      "status": outcome.status.value,
      "agent_complete": outcome.complete,
      "is_empty_patch": outcome.is_empty,
      "binary_stripped": outcome.binary_stripped,
      "patch_file": str(outcome.workspace / "patch.diff"),
      "workspace": str(outcome.workspace),
  } | provenance
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
        }
        | provenance,
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
    root: epath.PathLike,
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

  unit_test_spec = instance.unit_test_spec(patch=outcome.patch)
  eval_ws = cache_root(root) / _EVAL_SUBDIR / instance.instance_id
  eval_ws.rmtree(missing_ok=True)
  sandbox = build_sandbox(
      backend,
      instance.sandbox_spec(),
      workspace=eval_ws,
      network=False,
      pull=pull,
  )
  _, verdict = run_unit_test(
      sandbox, unit_test_spec, output_dir=eval_ws, timeout=timeout
  )
  resolved = bool(verdict and verdict.resolved)
  summary["outcome"] = "resolved" if resolved else "unresolved_tests_failed"
  if verdict is not None:
    summary["grade"] = {
        "resolved": verdict.resolved,
        "score": verdict.score,
    } | verdict.summary()
  return resolved
