"""The ``run`` subcommand: any registered workflow, against any instance."""

from __future__ import annotations

from collections.abc import Sequence
import json
from typing import Annotated, Any, final

from etils import epath
import typer

from swe_lab.cli.overrides import (
    apply_overrides,
    OverrideError,
    parse_overrides,
)
from swe_lab.cli.persist_wiring import run_store, run_ts
from swe_lab.datasets.instance import TaskInstance
from swe_lab.datasets.loader import load_dataset
from swe_lab.paths import cache_root, find_repo_root
from swe_lab.sandbox import ArtifactSchema, LocalFile, Mount
from swe_lab.workflow import (
    EntryOutcome,
    registered_workflows,
    Workflow,
    workflow_definition,
    WorkflowError,
    WorkflowOutcome,
)

# Imported for its registrations: the built-in workflow definitions.
import swe_lab.workflow.definitions as _definitions

assert _definitions.ROLLOUT_KEY  # the import above is for its side effect

_RUNS_SUBDIR = "runs"
# The metric every evaluation method reports; a workflow that graded something
# says so through it, which is how this command stays free of verdict-shaped
# knowledge (any method, any dataset).
_RESOLVED_METRIC_SUFFIX = ".resolved"


@final
class ExitCode:
  """What the process exit code means.

  Three, because "did it run" and "did the patch pass" are different questions
  and one code cannot answer both: a workflow that completes and grades a
  patch as failing is a *successful run* with a negative answer.
  """

  OK = 0
  FAILED = 1  # a task failed, an edge failed, or the run was refused
  UNRESOLVED = 2  # it ran, and what it graded did not resolve


def run_cmd(
    ctx: typer.Context,
    workflow: Annotated[
        str, typer.Argument(help="Registered workflow name (see --list).")
    ] = "",
    instance_id: Annotated[
        str, typer.Argument(help="The instance to run it against.")
    ] = "",
    list_: Annotated[
        bool,
        typer.Option("--list", help="List registered workflows and exit."),
    ] = False,
    dataset: Annotated[
        str, typer.Option(help="Dataset the instance belongs to.")
    ] = "swebench_pro",
    sweep: Annotated[
        str, typer.Option(help="Sweep id the run's records are keyed under.")
    ] = "adhoc",
    rollout_id: Annotated[
        int, typer.Option(help="Which sample of the instance this run is.")
    ] = 0,
    inputs: Annotated[
        list[str] | None,
        typer.Option(
            "--input",
            help="A workflow input, as NAME=PATH (or just PATH when the "
            "workflow leaves exactly one unsupplied).",
        ),
    ] = None,
    persist: Annotated[
        bool, typer.Option(help="Persist the run to the T1 store.")
    ] = False,
    resume: Annotated[
        bool,
        typer.Option(help="Honor terminal markers instead of re-running."),
    ] = False,
) -> None:
  """Run a registered workflow against one instance.

  Any field of the workflow can be adjusted for this invocation by naming its
  path: ``--<entry>.<field-path>=<value>``, as in
  ``--rollout.harness.model=opus`` or ``--unit_test.retries=2``. That includes
  where it runs: ``--rollout.sandbox=ghjob`` swaps the backend whole,
  ``--rollout.sandbox.network=false`` edits a field of it.
  """
  if list_:
    for name in registered_workflows():
      entries = workflow_definition(name)
      print(f"{name}: {', '.join(entry.key for entry in entries)}")
    raise typer.Exit(ExitCode.OK)
  if not workflow or not instance_id:
    raise typer.BadParameter("give a workflow name and an instance id")

  try:
    definition = workflow_definition(workflow)
    entries = apply_overrides(definition, parse_overrides(ctx.args))
  except (WorkflowError, OverrideError) as error:
    raise typer.BadParameter(str(error)) from error

  instance = load_dataset(dataset).require(instance_id)
  if not isinstance(instance, TaskInstance):
    raise typer.BadParameter(f"dataset {dataset!r} has no runnable instances")

  root = find_repo_root()
  output_dir = cache_root(root) / _RUNS_SUBDIR / workflow / instance.instance_id
  if not resume:
    # A one-off command re-runs: the previous run's attempts, workspaces and
    # markers go with it.
    output_dir.rmtree(missing_ok=True)
  supplied = _supplied_inputs(inputs or [], entries)

  built = Workflow(
      store=run_store(root, persist_to_t1=persist, scratch=output_dir),
      sweep_id=sweep,
      rollout_id=rollout_id,
      entries=entries,
  )
  try:
    outcome = built.execute(
        instance,
        inputs=supplied,
        output_dir=output_dir,
        run_ts=run_ts(),
        resume=resume,
        extra_record=instance.run_provenance(),
    )
  except WorkflowError as error:
    raise typer.BadParameter(_explain(error, workflow, entries)) from error

  summary = _summarize(
      outcome, workflow=workflow, instance=instance, persist=persist
  )
  print(json.dumps(summary, indent=2))
  raise typer.Exit(_exit_code(outcome))


def _supplied_inputs(
    raw: Sequence[str], entries: Sequence[Any]
) -> dict[str, Mount]:
  """Turn ``--input`` arguments into the workflow's caller inputs.

  A store name is an edge-contract detail, so when the workflow leaves exactly
  one required input unsupplied — the case for everything shipped — the name
  may be omitted and the path stands alone.

  Args:
    raw: The ``--input`` values as given.
    entries: The workflow's entries, for the single-unbound shorthand.

  Returns:
    Input name → a read-only mount of the host file.

  Raises:
    typer.BadParameter: On a repeated name, a path that is not a file, or a
      bare path where the workflow does not have exactly one unbound input.
  """
  supplied: dict[str, Mount] = {}
  for item in raw:
    name, sep, path = item.partition("=")
    if not sep:
      name, path = _sole_unbound_input(entries, item), item
    if name in supplied:
      raise typer.BadParameter(f"--input {name} given twice")
    source = epath.Path(path)
    if not source.is_file():
      raise typer.BadParameter(f"--input {name}: {path} is not a file")
    supplied[name] = Mount(LocalFile(source), read_only=True)
  return supplied


def _sole_unbound_input(entries: Sequence[Any], item: str) -> str:
  """Return the one input name a bare ``--input PATH`` can mean.

  Args:
    entries: The workflow's entries.
    item: The argument as given, for the error.

  Returns:
    The single unbound required input's name.

  Raises:
    typer.BadParameter: If the workflow has none or several.
  """
  unbound = _unbound_inputs(entries)
  if len(unbound) == 1:
    return unbound[0].name
  names = ", ".join(schema.name for schema in unbound) or "none"
  raise typer.BadParameter(
      f"--input {item}: this workflow does not have exactly one input to"
      f" supply (unbound: {names}); spell it as --input NAME=PATH"
  )


def _unbound_inputs(entries: Sequence[Any]) -> list[ArtifactSchema]:
  """Return the required inputs nothing inside the workflow supplies.

  A declared input is satisfied from inside when an earlier entry produces it
  or the consuming task builds it; what is left is what the invoker must hand
  over.

  Args:
    entries: The workflow's entries, in declared order.

  Returns:
    The unbound required inputs, in declaration order.
  """
  produced: set[str] = set()
  unbound: list[ArtifactSchema] = []
  for entry in entries:
    for schema in entry.task.input_schema():
      if (
          schema.required
          and schema.name not in produced
          and entry.task.inputs_builder is None
      ):
        unbound.append(schema)
    # An entry's own outputs satisfy the entries after it, never itself.
    produced |= _declared_outputs(entry)
  return unbound


def _declared_outputs(entry: Any) -> set[str]:
  """Return the store names an entry's task is configured to produce.

  Read from the observers a task composes, which is where output schemas come
  from — but without an instance, so a task whose outputs are instance-derived
  contributes what it can name statically.

  Args:
    entry: The workflow entry.

  Returns:
    The declared output names, empty when they cannot be known yet.
  """
  try:
    observers = entry.task.observers(None)
  except Exception:  # noqa: BLE001 — an instance-derived schema is unknowable here
    return set()
  return {
      schema.name
      for observer in observers
      for schema in observer.output_schema()
  }


def _explain(
    error: WorkflowError, workflow: str, entries: Sequence[Any]
) -> str:
  """Turn a bind-time refusal into something a person can act on.

  The engine's message is precise and engine-flavored; when the reason is
  simply that nobody supplied an input, this says which one, quotes the
  schema's own description, and names the flag that satisfies it.

  Kept to a **single line**: the message is rendered inside a panel that clips
  what does not fit, so an actionable last line is exactly the part that gets
  lost.

  Args:
    error: The refusal from binding.
    workflow: The workflow name, for the message.
    entries: Its entries, to name the unbound inputs.

  Returns:
    The message to show.
  """
  unbound = _unbound_inputs(entries)
  if "nothing produces" not in str(error) or not unbound:
    return str(error)
  wanted = ", ".join(
      f"{schema.name} ({schema.description})"
      if schema.description
      else schema.name
      for schema in unbound
  )
  how = (
      "--input ./your-file"
      if len(unbound) == 1
      else "--input NAME=PATH, once per input"
  )
  return (
      f"workflow {workflow!r} needs an input you did not supply: {wanted}."
      f" Supply it with {how}"
  )


def _summarize(
    outcome: WorkflowOutcome,
    *,
    workflow: str,
    instance: TaskInstance[Any],
    persist: bool,
) -> dict[str, object]:
  """Build the run's JSON summary from what the run already recorded.

  One shape for every workflow: the metrics carry the answer (an evaluation
  method reports ``<method>.resolved`` itself), so nothing here knows what a
  verdict is, and a downstream user's own task summarizes for free.

  Args:
    outcome: What the workflow reported, entry by entry.
    workflow: The name invoked.
    instance: The instance run against (its provenance qualifies the result).
    persist: Whether the run went to the T1 store.

  Returns:
    The summary, ready to print.
  """
  return {
      "workflow": workflow,
      "instance_id": instance.instance_id,
      "succeeded": outcome.succeeded,
      "entries": [
          _entry_json(entry, persist=persist) for entry in outcome.entries
      ],
      "record_key": outcome.record_key,
  } | instance.run_provenance()


def _entry_json(outcome: EntryOutcome, *, persist: bool) -> dict[str, object]:
  """Summarize one entry from its own record.

  Args:
    outcome: The entry's outcome.
    persist: Whether store keys are worth printing (they are the T1 ones).

  Returns:
    The entry's summary object.
  """
  summary: dict[str, object] = {
      "key": outcome.key,
      "status": outcome.status.value,
  }
  if outcome.missing_inputs:
    summary["missing_inputs"] = list(outcome.missing_inputs)
  run = outcome.run
  if run is None:
    return summary
  summary["attempts"] = run.attempts
  summary["resumed"] = run.resumed
  summary["metrics"] = dict(run.record.metrics)
  if persist:
    summary["artifacts"] = dict(run.record.artifact_keys)
  return summary


def _exit_code(outcome: WorkflowOutcome) -> int:
  """Return the process exit code for a finished run.

  Args:
    outcome: What the workflow reported.

  Returns:
    ``OK`` when it completed and nothing graded as failing, ``UNRESOLVED``
    when a method reported an unresolved verdict, ``FAILED`` otherwise.
  """
  if not outcome.succeeded:
    return ExitCode.FAILED
  for entry in outcome.entries:
    if entry.run is None:
      continue
    for name, value in entry.run.record.metrics.items():
      if name.endswith(_RESOLVED_METRIC_SUFFIX) and value < 1.0:
        return ExitCode.UNRESOLVED
  return ExitCode.OK
