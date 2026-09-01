"""Tests for the workflow registry and the shipped definitions.

A definition is pure declaration, so the interesting properties are: what a
registration refuses (at import, before anything can run), what a build hands
back, and that a registered name really does run end to end — here over the
``fake`` backend, with a definition this file writes itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, final, override

from etils import epath
import pytest

from swe_lab.datasets.instance import TaskInstance
from swe_lab.datasets.swebench_pro.unit_test import SweBenchProVerdict
from swe_lab.evaluation.verdict import UnitTestSpec
from swe_lab.harnesses.claude_code.constants import (
    HOST_OAUTH_TOKEN_ENV,
    SANDBOX_OAUTH_TOKEN_ENV,
)
from swe_lab.rollout import PROMPT_NAME
from swe_lab.sandbox import (
    ArtifactSchema,
    Contribution,
    ExecResult,
    FilesystemStore,
    SandboxConfig,
    SandboxFs,
    SandboxObserver,
    SandboxSpec,
)
from swe_lab.sandbox.observers import PATCH_NAME
from swe_lab.sandbox.testing import FakeSandboxConfig
from swe_lab.workflow import (
    register_workflow,
    registered_workflows,
    Task,
    Workflow,
    workflow_definition,
    WorkflowDef,
    WorkflowEntry,
    WorkflowError,
)
import swe_lab.workflow.definitions as definitions


def _on(wf: Workflow, sandbox: SandboxConfig) -> Workflow:
  """Run every entry of ``wf`` on ``sandbox`` — entries declare where they run.

  Args:
    wf: The workflow to place.
    sandbox: The config (and therefore the backend) every entry gets.

  Returns:
    A copy whose entries all declare ``sandbox``.
  """
  return replace(wf, entries=[replace(e, sandbox=sandbox) for e in wf.entries])


SPEC = SandboxSpec("acme__widget-1", "acme/widget:tag", "/app", "abc123")


@final
@dataclass(frozen=True)
class _Instance(TaskInstance[SweBenchProVerdict]):
  instance_id: str = "acme__widget-1"

  @override
  def sandbox_spec(self) -> SandboxSpec:
    return SPEC

  @override
  def prompt(self) -> str:
    return "SOLVE THIS"

  @override
  def gold_patch(self) -> str | None:
    return None

  @override
  def unit_test_spec(
      self,
      *,
      apply_patch: bool,
      patch_name: str = PATCH_NAME,
      checkout_golden_tests: bool = True,
      patch_baseline: bool = False,
  ) -> UnitTestSpec[SweBenchProVerdict]:
    raise NotImplementedError


@final
@dataclass
class _Emit(SandboxObserver):
  """Declares ``name`` and emits fixed bytes."""

  name: str

  @override
  def output_schema(self) -> tuple[ArtifactSchema, ...]:
    return (ArtifactSchema(self.name, description="a produced thing"),)

  @override
  def before_destroy(self, sb: SandboxFs) -> Contribution | None:
    del sb
    return Contribution(inline_artifacts={self.name: b"THING"})


@final
@dataclass
class _Producer(Task):
  """Emits ``thing.txt``."""

  @override
  def observers(
      self, instance: TaskInstance[Any]
  ) -> tuple[SandboxObserver, ...]:
    del instance
    return (_Emit(name="thing.txt"),)

  @override
  def action(
      self, sb: SandboxFs, instance: TaskInstance[Any], *, timeout: float
  ) -> ExecResult:
    del instance
    return sb.run_script("main.sh", timeout=timeout)


@final
@dataclass
class _Consumer(Task):
  """Requires ``thing.txt``; records what got staged."""

  seen: list[bytes] = field(default_factory=list)

  @override
  def input_schema(self) -> tuple[ArtifactSchema, ...]:
    return (ArtifactSchema("thing.txt", description="the upstream thing"),)

  @override
  def action(
      self, sb: SandboxFs, instance: TaskInstance[Any], *, timeout: float
  ) -> ExecResult:
    del instance
    self.seen.append(sb.read("thing.txt"))
    return sb.run_script("main.sh", timeout=timeout)


def test_the_built_ins_register_at_import():
  # `definitions` is imported for its registrations, and names them too.
  assert definitions.ROLLOUT_KEY == "rollout"
  assert {"rollout", "unit_test", "rollout_and_unit_test"} <= set(
      registered_workflows()
  )


def test_the_agent_token_is_host_scoped_and_sandbox_named():
  # The invariant the rename exists for, in both directions:
  #
  # - **on the host** the token is never called CLAUDE_CODE_OAUTH_TOKEN. That
  #   name in a login shell hijacks the developer's / agent's own `claude`
  #   login with an inference-only token, and Remote Control then refuses to
  #   start. `.envrc.local` exports it into every shell under this directory,
  #   so "just don't set it" is not a control that holds.
  # - **inside the sandbox** it is called exactly CLAUDE_CODE_OAUTH_TOKEN,
  #   because that is the name Claude Code itself reads. A rename here would
  #   leave the agent unauthenticated.
  #
  # docs/conventions.md → Hazards carries the story.
  assert SANDBOX_OAUTH_TOKEN_ENV == "CLAUDE_CODE_OAUTH_TOKEN"
  assert HOST_OAUTH_TOKEN_ENV != SANDBOX_OAUTH_TOKEN_ENV
  (rollout,) = workflow_definition("rollout")
  assert rollout.sandbox.pass_env == {
      SANDBOX_OAUTH_TOKEN_ENV: HOST_OAUTH_TOKEN_ENV
  }


def test_the_shipped_chain_grades_what_the_agent_produced():
  # The definition is what a `swe-lab run rollout_and_unit_test` invocation
  # gets: the agent's entry declares the credential it inherits, and the two
  # are keyed the way their records are.
  rollout, evaluation = workflow_definition("rollout_and_unit_test")
  assert (rollout.key, evaluation.key) == ("rollout", "unit_test")
  assert rollout.sandbox.network is True
  assert rollout.sandbox.pass_env == {
      "CLAUDE_CODE_OAUTH_TOKEN": "SWE_LAB_CLAUDE_CODE_OAUTH_TOKEN"
  }
  # Grading inherits no credential — only the agent needs one.
  assert evaluation.sandbox.pass_env == {}
  assert evaluation.retries == 2  # a flaky suite gets two more tries
  # the edge that makes it a chain: the agent's patch is the grader's input
  assert [s.name for s in evaluation.task.input_schema()] == [PATCH_NAME]
  # …and the grading entry supplies nothing itself, which is what lets the
  # SAME entry be the standalone `unit_test` workflow (patch from the caller)
  # and the tail of this chain (patch from the edge). A gold-patch variant
  # would need a builder, and a builder cannot coexist with either supplier —
  # so it is a separate definition, not a flag on this one.
  assert evaluation.task.inputs_builder is None
  assert workflow_definition("unit_test")[0].task is evaluation.task
  # …and the solving task builds its own prompt, so the chain needs no caller
  assert rollout.task.inputs_builder is not None
  assert [s.name for s in rollout.task.input_schema()] == [PROMPT_NAME]


def test_an_unknown_workflow_is_refused_by_name():
  with pytest.raises(WorkflowError, match="unknown workflow"):
    _ = workflow_definition("does_not_exist")


def test_a_malformed_definition_is_refused_at_registration():
  # The point of validating here: a registry full of workflows is checked when
  # the module registering them is imported, not on first use.
  duplicate: WorkflowDef = (
      WorkflowEntry("same", _Producer(), timeout=10.0),
      WorkflowEntry("same", _Consumer(), timeout=10.0),
  )
  with pytest.raises(WorkflowError, match="duplicate entry keys"):
    register_workflow("broken", duplicate)
  assert "broken" not in registered_workflows()

  dead_binding: WorkflowDef = (
      WorkflowEntry("producer", _Producer(), timeout=10.0),
      WorkflowEntry(
          "consumer", _Consumer(), timeout=10.0, inputs=("producer/other.txt",)
      ),
  )
  with pytest.raises(WorkflowError, match="does not declare"):
    register_workflow("also_broken", dead_binding)


def test_a_registered_definition_runs_by_name(tmp_path: Path):
  # End to end through the registry: one definition, any instance, and the
  # edge resolved from the store like any other chain.
  consumer = _Consumer()
  register_workflow(
      "test_chain",
      (
          WorkflowEntry("producer", _Producer(), timeout=10.0),
          WorkflowEntry("consumer", consumer, timeout=10.0),
      ),
  )
  # Built the way the CLI builds it: look the definition up by name, then
  # construct. Nothing between them here; an invocation would apply overrides.
  workflow = Workflow(
      store=FilesystemStore(epath.Path(tmp_path / "store")),
      sweep_id="sw",
      rollout_id=0,
      entries=workflow_definition("test_chain"),
  )
  outcome = _on(workflow, FakeSandboxConfig()).execute(
      _Instance(),
      output_dir=tmp_path / "out",
      run_ts="ts-0",
  )
  assert outcome.succeeded is True
  assert consumer.seen == [b"THING"]


def test_a_definition_is_reusable_across_instances(tmp_path: Path):
  # The property the whole late-binding change exists for: one declaration,
  # any number of instances, no shared state between runs.
  register_workflow(
      "test_reuse", (WorkflowEntry("producer", _Producer(), timeout=10.0),)
  )
  store = FilesystemStore(epath.Path(tmp_path / "store"))
  for index, instance_id in enumerate(["one", "two"]):
    outcome = _on(
        Workflow(
            store=store,
            sweep_id="sw",
            rollout_id=0,
            entries=workflow_definition("test_reuse"),
        ),
        FakeSandboxConfig(),
    ).execute(
        _Instance(instance_id=instance_id),
        output_dir=tmp_path / f"out{index}",
        run_ts="ts-0",
    )
    assert outcome.succeeded is True
    assert outcome.record_key == f"sw/{instance_id}/r0/workflow.json"
