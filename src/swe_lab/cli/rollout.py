"""The ``rollout`` subcommand: solve one instance, optionally graded."""

from __future__ import annotations

import contextlib
import json
import os
from typing import Annotated, Any
import zlib

from etils import epath
import typer

from swe_lab.cli.persist_wiring import run_store, run_ts
from swe_lab.cli.sandbox_wiring import invocation_config
from swe_lab.datasets.instance import TaskInstance
from swe_lab.datasets.loader import load_dataset
from swe_lab.evaluation.methods.unit_test import UnitTestEvalTask, verdict_of
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
from swe_lab.rollout import (
    CodingAgentTask,
    outcome_of,
    patch_of,
    ProxyFactory,
)
from swe_lab.sandbox import SandboxConfig
from swe_lab.sandbox.observers import PATCH_NAME
from swe_lab.workflow import (
    EntryStatus,
    Workflow,
    WorkflowEntry,
    WorkflowOutcome,
)

_ROLLOUT_SUBDIR = "rollout_workspaces"
# The entry keys of this command's workflow — also the task segment of every
# record it persists (ADR-0007 §6).
_ROLLOUT_KEY = "rollout"
_EVAL_KEY = "eval"
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
    proxy_log_dir: epath.PathLike,
) -> tuple[ClaudeCodeHarness, ProxyFactory | None]:
  """Build this CLI's harness + how it opens a trace recorder, per capture mode.

  Construction lives here, not in the task: the caller picks the agent (this
  CLI ships Claude Code) and hands the composition the built pair.

  For ``STREAM`` the agent's own event stream is the trace, so there is no
  recorder. For ``PROXY`` a host-side ``cc-reverse-proxy`` records into the
  run's workspace — where the in-container conversion reads it — and the agent
  is pointed at it (the container reaches the host through the
  ``host.docker.internal`` gateway the host backend always maps).

  Args:
    instance_id: Keys the proxy port, so concurrent rollouts never collide.
    model: The ``--model`` alias the agent runs as.
    capture: The trace-capture strategy.
    bare: Run the agent with ``--bare`` (API-key auth).
    proxy_log_dir: The workspace a proxy recording is written into. The runner
      allocates one workspace per attempt, so this is the first attempt's; a
      recorder is single-use anyway, which is why a retried PROXY rollout is
      not something this command offers.

  Returns:
    The harness, and how to open the recorder wrapped around one run
    (``None`` records nothing).
  """
  if capture is Capture.STREAM:
    return ClaudeCodeHarness(model=model, bare=bare), None
  port = port_for_index(zlib.crc32(instance_id.encode()) % _PROXY_PORT_SPAN)
  harness = ClaudeCodeHarness(
      model=model,
      capture=capture,
      proxy_base_url=f"http://{CONTAINER_PROXY_HOST}:{port}",
      bare=bare,
  )
  log_path = epath.Path(proxy_log_dir) / PROXY_LOG_NAME

  def open_recorder() -> contextlib.AbstractContextManager[object]:
    return ReverseProxy(port, log_path, build_proxy(find_repo_root()))

  return harness, open_recorder


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
    eval_retries: Annotated[
        int,
        typer.Option(
            help="Extra grading attempts after a failure, for --grade. "
            "Does not re-run the agent."
        ),
    ] = 1,
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
  output_dir = cache_root(root) / _ROLLOUT_SUBDIR / instance.instance_id
  # A re-run of a one-off command re-runs it: the previous run's attempts and
  # their workspaces go, and `resume=False` below ignores its terminal markers.
  output_dir.rmtree(missing_ok=True)
  harness, proxy_factory = _build_agent(
      instance.instance_id,
      model=model,
      capture=capture,
      bare=bare,
      proxy_log_dir=output_dir / _ROLLOUT_KEY / "ws" / "a0",
  )

  entries = [
      WorkflowEntry(
          _ROLLOUT_KEY,
          CodingAgentTask(harness=harness, proxy_factory=proxy_factory),
          timeout=timeout,
          # The agent needs the network and the auth secret; the secret travels
          # by name, so its value never reaches a command line.
          sandbox=SandboxConfig(network=True, pass_env=(auth_env,)),
      )
  ]
  if grade:
    entries.append(
        WorkflowEntry(
            _EVAL_KEY,
            UnitTestEvalTask(),  # its patch.diff input is the rollout's output
            timeout=timeout,
            sandbox=SandboxConfig(network=False),
            retries=eval_retries,
        )
    )
  workflow = Workflow(
      store=run_store(root, persist_to_t1=persist, scratch=output_dir),
      sweep_id=sweep,
      rollout_id=0,
      entries=entries,
  )
  outcome = workflow.execute(
      instance,
      backend=backend,
      sandbox=invocation_config(backend, network=True, pull=pull),
      output_dir=output_dir,
      run_ts=run_ts(),
      resume=False,
      model=model,
      extra_record=instance.run_provenance(),
  )

  summary = _summarize(
      outcome,
      instance=instance,
      output_dir=output_dir,
      grade=grade,
      persist=persist,
  )
  print(json.dumps(summary, indent=2))
  raise typer.Exit(0 if (not grade or summary["outcome"] == "resolved") else 1)


def _summarize(
    outcome: WorkflowOutcome,
    *,
    instance: TaskInstance[Any],
    output_dir: epath.Path,
    grade: bool,
    persist: bool,
) -> dict[str, object]:
  """Build the command's JSON summary from the workflow's outcome.

  An explicit ``outcome`` string makes an unresolved run's *reason* readable,
  never guessed: ``empty_patch`` (no edits — grading skipped) is distinct from
  ``unresolved_tests_failed`` (a real patch that graded false).

  Args:
    outcome: What the workflow reported, entry by entry.
    instance: The instance solved (its provenance qualifies the result).
    output_dir: The run's directory (artifacts and workspaces live under it).
    grade: Whether grading was asked for.
    persist: Whether the run was opted into the T1 store.

  Returns:
    The summary dict, ready to print.
  """
  entries = {entry.key: entry for entry in outcome.entries}
  rollout = entries[_ROLLOUT_KEY]
  result = rollout.run.result if rollout.run is not None else None
  extract = patch_of(result) if result is not None else None
  agent = outcome_of(result) if result is not None else None
  is_empty = extract.is_empty if extract is not None else True
  # Carried even on a solve-only run: `--grade` reuses this summary, and a
  # reader of an unresolved result needs to know the instance flakes.
  summary: dict[str, object] = {
      "instance_id": instance.instance_id,
      "status": result.run.status.value if result is not None else "unknown",
      "agent_complete": agent.complete if agent is not None else False,
      "is_empty_patch": is_empty,
      "binary_stripped": (
          extract.binary_stripped if extract is not None else False
      ),
      "patch_file": str(
          result.run.artifacts.get(PATCH_NAME, output_dir / PATCH_NAME)
          if result is not None
          else output_dir / PATCH_NAME
      ),
      "workspace": str(output_dir),
  } | instance.run_provenance()
  if persist and rollout.run is not None and rollout.run.record is not None:
    summary["persisted"] = {
        "run_ts": rollout.run.record.run_ts,
        "keys": rollout.run.record.artifact_keys,
    }

  if not grade:
    summary["outcome"] = "solved_not_graded"
    return summary
  evaluation = entries[_EVAL_KEY]
  if evaluation.status is EntryStatus.EDGE_FAILED or is_empty:
    # An empty patch never reaches a container: the edge refuses to stage
    # empty bytes, which is the same answer, one container cheaper.
    summary["outcome"] = "empty_patch"
    summary["grade"] = {"resolved": False, "reason": "empty_patch"}
    return summary
  eval_result = evaluation.run.result if evaluation.run is not None else None
  verdict = verdict_of(eval_result) if eval_result is not None else None
  resolved = bool(verdict and verdict.resolved)
  summary["outcome"] = "resolved" if resolved else "unresolved_tests_failed"
  if verdict is not None:
    summary["grade"] = {
        "resolved": verdict.resolved,
        "score": verdict.score,
        # Attempts are the runner's fact now (ADR-0008), not the verdict's.
        "attempts": evaluation.run.attempts if evaluation.run else 0,
    } | verdict.summary()
  return summary
