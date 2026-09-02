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
from swe_lab.sandbox.observers.diff_extract import BASE_REF_NAME
from swe_lab.sandbox.testing import FakeSandboxConfig
from swe_lab.trace_synthesis.criterion import load_criterion
from swe_lab.trace_synthesis.supervisor import (
    Observation,
    SpeakWhenOffTrack,
    Verdict,
)
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


def test_the_shipped_chain_grades_what_the_agent_produced():
  # The definition is what a `swe-lab run rollout_and_unit_test` invocation
  # gets: the agent's entry declares the credential it inherits, and the two
  # are keyed the way their records are.
  rollout, evaluation = workflow_definition("rollout_and_unit_test")
  assert (rollout.key, evaluation.key) == ("rollout", "unit_test")
  assert rollout.sandbox.network is True
  assert rollout.sandbox.pass_env == ("CLAUDE_CODE_OAUTH_TOKEN",)
  # Grading inherits no credential — only the agent needs one.
  assert evaluation.sandbox.pass_env == ()
  assert evaluation.retries == 2  # a flaky suite gets two more tries
  # the edge that makes it a chain: the agent's patch is the grader's input,
  # and under the baseline default (ADR-0014) the base it was diffed against
  # travels the same edge — the grader verifies the tree it resets to against
  # that sha rather than trusting `base_commit`.
  assert [s.name for s in evaluation.task.input_schema()] == [
      PATCH_NAME,
      BASE_REF_NAME,
  ]
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


def test_the_shipped_timeouts_are_the_budgets_that_were_reasoned_about():
  # These two numbers were argued from a measurement (a p90 rollout wall clock
  # of about an hour) and they are per *attempt*, not shared across retries —
  # so a change to either one moves a CI job's worst case and should be
  # deliberate rather than incidental.
  rollout, evaluation = workflow_definition("rollout_and_unit_test")
  assert rollout.timeout == 3600.0  # an hour for the agent
  assert evaluation.timeout == 1800.0  # half an hour for the suite
  # The same suite budget wherever the suite runs, gold patch included.
  assert workflow_definition("unit_test")[0].timeout == 1800.0
  assert workflow_definition("gold_unit_test")[0].timeout == 1800.0


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


def test_the_rollout_and_grading_halves_of_the_baseline_agree() -> None:
  """The base ref is a contract, so the pair cannot disagree (ADR-0014).

  Either half alone moves the patch and the tree apart: a baseline patch
  graded against ``base_commit`` fails to apply exactly when the agent touched
  a path the image had mutated, and a ``base_commit`` patch graded against a
  baseline fails its sha verify. The two defaults are what make the naive
  composition correct, so they are asserted together rather than one at a time.
  """
  from swe_lab.evaluation.unit_test import UnitTestTask
  from swe_lab.rollout import CodingAgentTask
  from swe_lab.workflow.definitions import ROLLOUT_AND_UNIT_TEST

  coding, grading = (entry.task for entry in ROLLOUT_AND_UNIT_TEST)
  assert isinstance(coding, CodingAgentTask)
  assert isinstance(grading, UnitTestTask)
  assert coding.patch_baseline is grading.patch_baseline is True


def test_grading_a_gold_patch_stays_on_base_commit() -> None:
  """The gold patch's base *is* ``base_commit``; it has no pre-agent tree."""
  from swe_lab.evaluation.unit_test import UnitTestTask
  from swe_lab.workflow.definitions import GOLD_UNIT_TEST

  (entry,) = GOLD_UNIT_TEST
  assert isinstance(entry.task, UnitTestTask)
  assert entry.task.patch_baseline is False


def test_a_supervised_rollout_and_its_control_can_both_be_started_by_name():
  """The pipeline is startable, which is prior to it being correct.

  A supervisor that composes when configured, with nothing in the shipped
  definitions configuring it, is a capability no command can reach. Both arms
  are registered, because a treatment that can be run and a control that cannot
  measures nothing.
  """
  from swe_lab.rollout import CodingAgentTask

  names = set(registered_workflows())
  assert {
      "supervised_rollout_and_unit_test",
      "control_rollout_and_unit_test",
  } <= names

  supervised, graded = workflow_definition("supervised_rollout_and_unit_test")
  control, control_graded = workflow_definition("control_rollout_and_unit_test")
  assert isinstance(supervised.task, CodingAgentTask)
  assert isinstance(control.task, CodingAgentTask)
  assert supervised.task.supervision_factory is not None
  assert control.task.supervision_factory is not None
  # Both are chains: a supervised rollout that is not graded measures nothing
  # either.
  assert (graded.key, control_graded.key) == (
      definitions.UNIT_TEST_KEY,
      definitions.UNIT_TEST_KEY,
  )

  # …and the default stays unsupervised.
  plain, _ = workflow_definition("rollout_and_unit_test")
  assert isinstance(plain.task, CodingAgentTask)
  assert plain.task.supervision_factory is None


def test_the_two_arms_put_the_actor_in_the_same_environment():
  """Comparability comes from the harness, not from a flag.

  Both arms run the actor through the same invocation script — same capture,
  same live channel, same relay — so what the actor receives differs by the
  corrections alone. If the control were simply the unsupervised definition,
  the arms would differ in the script itself and the comparison would be about
  the channel rather than about the corrections.
  """
  from swe_lab.rollout import CodingAgentTask

  supervised, _ = workflow_definition("supervised_rollout_and_unit_test")
  control, _ = workflow_definition("control_rollout_and_unit_test")
  assert isinstance(supervised.task, CodingAgentTask)
  assert isinstance(control.task, CodingAgentTask)
  assert supervised.task.harness == control.task.harness
  assert supervised.timeout == control.timeout
  assert supervised.sandbox == control.sandbox
  # …and the policies are the same policy, on the same criterion, with the
  # same window and cooldown. Only the budget differs — the object-level form
  # of "matched everywhere the arms have to be matched".
  treatment_policy = _policy_of(supervised)
  control_policy = _policy_of(control)
  assert type(treatment_policy) is type(control_policy)
  assert treatment_policy.criterion == control_policy.criterion
  assert (treatment_policy.window, treatment_policy.cooldown) == (
      control_policy.window,
      control_policy.cooldown,
  )
  assert (treatment_policy.budget, control_policy.budget) == (3, 0)


def test_the_shipped_supervised_arm_carries_the_pinned_criterion():
  """The criterion gate is on the path a command actually takes.

  Building this definition's observers is what loads and digest-checks the
  criterion, and it happens while the observers are assembled — before any
  sandbox exists. Asserted against the *shipped* definition rather than a
  hand-built one, since a gate on a composition nobody runs gates nothing.
  """
  from swe_lab.rollout import CodingAgentTask
  from swe_lab.trace_synthesis.channel import SupervisedRun
  from swe_lab.trace_synthesis.criterion import CRITERION_SHA256
  from swe_lab.trace_synthesis.supervisor import SpeakWhenOffTrack

  supervised, _ = workflow_definition("supervised_rollout_and_unit_test")
  assert isinstance(supervised.task, CodingAgentTask)
  watchers = [
      o
      for o in supervised.task.observers(_Instance())
      if isinstance(o, SupervisedRun)
  ]
  assert len(watchers) == 1
  policy = watchers[0].policy
  assert isinstance(policy, SpeakWhenOffTrack)
  assert policy.criterion.digest == CRITERION_SHA256


def _policy_of(entry: WorkflowEntry) -> SpeakWhenOffTrack:
  """Return the policy the entry's supervision builds.

  Args:
    entry: A supervised rollout entry.

  Returns:
    Its policy.
  """
  from swe_lab.rollout import CodingAgentTask
  from swe_lab.trace_synthesis.channel import SupervisedRun

  assert isinstance(entry.task, CodingAgentTask)
  factory = entry.task.supervision_factory
  assert factory is not None
  built = factory("solve it")
  assert isinstance(built, SupervisedRun)
  policy = built.policy
  assert isinstance(policy, SpeakWhenOffTrack)
  return policy


def test_the_control_arm_pays_the_same_judge_calls_as_the_treatment():
  """The judge runs at both shipped budgets; only the writer differs.

  The budget gates *speech* and never gates judgement: the policy consults the
  judge at every boundary and records what it would have said before the
  budget is consulted. The two budgets here are the shipped ones, tied to the
  two registered arms by the assertion above. Asserted over behaviour rather
  than over fields, because "same type, different budget" does not by itself
  say the judge still runs.

  Call counts are the whole of it — nothing here measures latency or cost, and
  what matched judge calls buy a comparison is stated once, at
  `workflow.definitions.CONTROL_BUDGET`.
  """
  readings: dict[int, tuple[int, int, int, int]] = {}
  for budget in (3, 0):
    counted = {"judge": 0, "writer": 0}

    def judge(
        observation: Observation, criterion: Any, counted: Any = counted
    ) -> Verdict:
      del observation, criterion
      counted["judge"] += 1
      return Verdict(off_track=True, self_correcting=False, reason="drifting")

    def writer(
        observation: Observation, criterion: Any, counted: Any = counted
    ) -> str:
      del observation, criterion
      counted["writer"] += 1
      return "worth another look at the failing test"

    policy = SpeakWhenOffTrack(
        judge=judge,
        writer=writer,
        criterion=load_criterion(),
        budget=budget,
    )
    spoke = sum(
        policy.consider(
            Observation(task="t", evidence=(), cursor=cursor, said=())
        )
        is not None
        for cursor in range(1, 13)
    )
    readings[budget] = (
        counted["judge"],
        counted["writer"],
        spoke,
        len(policy.markers),
    )

  treatment, control = readings[3], readings[0]
  # Same judge calls and the same would-have-spoken markers…
  assert treatment[0] == control[0] == 12
  assert treatment[3] == control[3] == 12
  # …and they part company only after a correction has been decided on.
  assert (treatment[1], treatment[2]) == (3, 3)
  assert (control[1], control[2]) == (0, 0)
