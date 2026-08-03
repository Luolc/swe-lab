"""Tests for the task layer: assembly, execution, and the two mode seams.

``Task.execute`` is exercised over a :class:`FakeSandbox` (real local-dir file
ops, scripted exec, no Docker). The observer-schema seam, the exec-outcome
handoff, the timeout promotion, and ``UnitTestEvalTask``'s UPSTREAM mode are
the behaviors new in this layer; everything the wrappers preserve is already
pinned by the composition tests, which this file deliberately does not touch.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import final, override

from etils import epath
import pytest

from swe_lab.datasets.instance import TaskInstance
from swe_lab.datasets.swebench_pro.unit_test import (
    REQUIRED_TESTS_NAME,
    SweBenchProGrader,
    SweBenchProVerdict,
)
from swe_lab.evaluation.methods.unit_test import (
    ENTRYSCRIPT_NAME,
    UnitTestEvalTask,
)
from swe_lab.evaluation.verdict import UnitTestSpec
from swe_lab.sandbox import (
    ArtifactSchema,
    ExecResult,
    Inline,
    LocalFile,
    merge_output_schemas,
    Mount,
    Mounts,
    RunStatus,
    SandboxError,
    SandboxFs,
    SandboxObserver,
    SandboxSpec,
)
from swe_lab.sandbox.observers import PATCH_NAME
from swe_lab.sandbox.testing import FakeSandbox, RecordingObserver
from swe_lab.workflow import Task, UPSTREAM

SPEC = SandboxSpec("acme__widget-1", "acme/widget:tag", "/app", "abc123")


def _fake(tmp_path: Path, **kwargs: object) -> FakeSandbox:
  return FakeSandbox(
      spec=SPEC,
      workspace=epath.Path(tmp_path / "ws"),
      **kwargs,  # pyright: ignore[reportArgumentType]
  )


@final
@dataclass(frozen=True)
class _BareInstance(TaskInstance[SweBenchProVerdict]):
  """A minimal instance: static mounts + prompt, no eval compilation."""

  instance_id: str = "acme__widget-1"
  extra_mounts: Mounts = field(default_factory=dict)

  @override
  def sandbox_spec(self) -> SandboxSpec:
    return SPEC

  @override
  def mounts(self) -> Mounts:
    return dict(self.extra_mounts)

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
      patch: str | None,
      checkout_golden_tests: bool = True,
  ) -> UnitTestSpec[SweBenchProVerdict]:
    raise NotImplementedError("this instance compiles no eval")


@final
@dataclass
class _ScriptTask(Task):
  """A task whose action runs one staged script — the smallest real task."""

  instance: TaskInstance[SweBenchProVerdict]
  task_observers: tuple[SandboxObserver, ...] = ()
  task_mounts: Mounts = field(default_factory=dict)

  @override
  def mounts(self) -> Mounts:
    return {**super().mounts(), **self.task_mounts}

  @override
  def observers(self) -> Sequence[SandboxObserver]:
    return self.task_observers

  @override
  def action(self, sb: SandboxFs, *, timeout: float) -> ExecResult:
    return sb.run_script("main.sh", timeout=timeout)


@final
@dataclass
class _DeclaringObserver(SandboxObserver):
  """Declares a fixed output schema (and nothing else)."""

  schema: tuple[ArtifactSchema, ...] = ()

  @override
  def output_schema(self) -> tuple[ArtifactSchema, ...]:
    return self.schema


@final
@dataclass
class _CarrierObserver(SandboxObserver):
  """Carries the exec-outcome fields the task layer hands off to."""

  exec_result: ExecResult | None = None
  wall_seconds: float | None = None


# ─── merge_output_schemas ────────────────────────────────────────────────────


def test_merge_output_schemas_keeps_declaration_order():
  a = ArtifactSchema("patch.diff")
  b = ArtifactSchema("conversation.json", required=False)
  assert merge_output_schemas((a,), (), (b,)) == (a, b)


def test_merge_output_schemas_refuses_a_duplicate_name():
  with pytest.raises(SandboxError, match="patch.diff"):
    merge_output_schemas(
        (ArtifactSchema("patch.diff"),), (ArtifactSchema("patch.diff"),)
    )


# ─── execute: assembly ───────────────────────────────────────────────────────


def test_execute_composes_backend_task_and_extra_observers_in_order(
    tmp_path: Path,
):
  events: list[str] = []

  class _MeteredFake(FakeSandbox):

    @override
    def observers(self) -> tuple[SandboxObserver, ...]:
      return (RecordingObserver(name="backend", events=events),)

  sandbox = _MeteredFake(spec=SPEC, workspace=epath.Path(tmp_path / "ws"))
  task = _ScriptTask(
      instance=_BareInstance(),
      task_observers=(RecordingObserver(name="task", events=events),),
  )
  result = task.execute(
      sandbox,
      output_dir=tmp_path / "out",
      timeout=10.0,
      extra_observers=(RecordingObserver(name="extra", events=events),),
  )
  assert result.run.status is RunStatus.SUCCESS
  # ADR-0007 §3: backend first (it measures the whole run), the task's own
  # next, the caller's extras last (they see the run post-processed).
  assert events == [
      "backend.mounts",
      "task.mounts",
      "extra.mounts",
      "backend.before_create",
      "task.before_create",
      "extra.before_create",
      "backend.after_create",
      "task.after_create",
      "extra.after_create",
      "backend.before_destroy",
      "task.before_destroy",
      "extra.before_destroy",
      "backend.after_destroy",
      "task.after_destroy",
      "extra.after_destroy",
  ]


def test_execute_derives_the_schema_from_every_composed_observer(
    tmp_path: Path,
):
  declared = ArtifactSchema("thing.json", description="the thing")
  task = _ScriptTask(
      instance=_BareInstance(),
      task_observers=(_DeclaringObserver(schema=(declared,)),),
  )
  result = task.execute(
      _fake(tmp_path),
      output_dir=tmp_path / "out",
      timeout=10.0,
      extra_observers=(
          _DeclaringObserver(schema=(ArtifactSchema("extra.log"),)),
      ),
  )
  assert result.output_schema == (declared, ArtifactSchema("extra.log"))


def test_execute_refuses_a_duplicate_output_name_at_assembly(tmp_path: Path):
  # Two observers claiming one store name is a composition bug: it fails
  # before anything runs, like a duplicate mount target — the sandbox never
  # comes up.
  sandbox = _fake(tmp_path)
  clashing = _DeclaringObserver(schema=(ArtifactSchema("patch.diff"),))
  task = _ScriptTask(
      instance=_BareInstance(), task_observers=(clashing, clashing)
  )
  with pytest.raises(SandboxError, match="patch.diff"):
    task.execute(sandbox, output_dir=tmp_path / "out", timeout=10.0)
  assert sandbox.calls == []  # never went up


def test_execute_refuses_a_duplicate_mount_target_at_assembly(tmp_path: Path):
  sandbox = _fake(tmp_path)
  task = _ScriptTask(
      instance=_BareInstance(),
      task_mounts={"input.json": Mount(Inline(b"{}"))},
  )
  with pytest.raises(SandboxError, match="input.json"):
    task.execute(
        sandbox,
        output_dir=tmp_path / "out",
        timeout=10.0,
        extra_mounts={"input.json": Mount(Inline(b"{}"))},
    )
  assert sandbox.calls == []


def test_execute_stages_instance_task_and_extra_mounts(tmp_path: Path):
  sandbox = _fake(tmp_path)
  task = _ScriptTask(
      instance=_BareInstance(
          extra_mounts={"instance.txt": Mount(Inline(b"I"))}
      ),
      task_mounts={"task.txt": Mount(Inline(b"T"))},
  )
  result = task.execute(
      sandbox,
      output_dir=tmp_path / "out",
      timeout=10.0,
      extra_mounts={"edge.txt": Mount(Inline(b"E"), read_only=True)},
  )
  assert result.run.status is RunStatus.SUCCESS
  for name, content in [
      ("instance.txt", "I"),
      ("task.txt", "T"),
      ("edge.txt", "E"),
  ]:
    assert (sandbox.workspace / name).read_text() == content


# ─── execute: run semantics ──────────────────────────────────────────────────


def test_execute_records_a_setup_failure_instead_of_raising(tmp_path: Path):
  task = _ScriptTask(instance=_BareInstance())
  result = task.execute(
      _fake(tmp_path, up_error=SandboxError("no docker")),
      output_dir=tmp_path / "out",
      timeout=10.0,
  )
  assert result.run.status is RunStatus.SETUP_ERROR
  assert isinstance(result.run.error, SandboxError)
  assert result.exec_result is None  # the action never ran


def test_execute_promotes_a_timed_out_action(tmp_path: Path):
  killed = ExecResult(124, "", "", timed_out=True)
  task = _ScriptTask(instance=_BareInstance())
  result = task.execute(
      _fake(tmp_path, run_results=[killed]),
      output_dir=tmp_path / "out",
      timeout=10.0,
  )
  assert result.run.status is RunStatus.TIMEOUT
  assert result.exec_result == killed


def test_execute_hands_the_outcome_to_observers_carrying_the_fields(
    tmp_path: Path,
):
  carrier = _CarrierObserver()
  bystander = RecordingObserver()  # no such fields; must be left alone
  task = _ScriptTask(
      instance=_BareInstance(), task_observers=(carrier, bystander)
  )
  result = task.execute(
      _fake(tmp_path, run_results=[ExecResult(3, "out", "err")]),
      output_dir=tmp_path / "out",
      timeout=10.0,
  )
  assert carrier.exec_result == ExecResult(3, "out", "err")
  assert carrier.wall_seconds is not None and carrier.wall_seconds >= 0.0
  assert result.exec_result == ExecResult(3, "out", "err")


def test_the_handoff_never_clobbers_the_actions_own_accounting(
    tmp_path: Path,
):
  # An eval's retry loop hands its parser the wall time it *means* (script
  # cost, excluding grading); the generic handoff must not overwrite it.
  carrier = _CarrierObserver()

  @final
  @dataclass
  class _SelfAccountingTask(Task):

    instance: TaskInstance[SweBenchProVerdict]

    @override
    def observers(self) -> Sequence[SandboxObserver]:
      return (carrier,)

    @override
    def action(self, sb: SandboxFs, *, timeout: float) -> ExecResult:
      result = sb.run_script("main.sh", timeout=timeout)
      carrier.exec_result = result
      carrier.wall_seconds = 123.0
      return result

  task = _SelfAccountingTask(instance=_BareInstance())
  task.execute(_fake(tmp_path), output_dir=tmp_path / "out", timeout=10.0)
  assert carrier.wall_seconds == 123.0


# ─── UnitTestEvalTask: patch modes ───────────────────────────────────────────


@final
@dataclass(frozen=True)
class _EvalInstance(TaskInstance[SweBenchProVerdict]):
  """An instance compiling a minimal, gradeable spec (mirrors the record).

  The fake sandbox never runs the entryscript, so the "results" are the
  ``required_tests.json`` mount plus an ``output.json`` staged as if the run
  wrote it — the same trick the composition tests use.
  """

  instance_id: str = "acme__widget-1"

  @override
  def sandbox_spec(self) -> SandboxSpec:
    return SPEC

  @override
  def prompt(self) -> str:
    return "SOLVE THIS"

  @override
  def gold_patch(self) -> str | None:
    return "GOLD"

  @override
  def unit_test_spec(
      self,
      *,
      patch: str | None,
      checkout_golden_tests: bool = True,
  ) -> UnitTestSpec[SweBenchProVerdict]:
    del checkout_golden_tests
    output = json.dumps({"tests": [{"name": "a", "status": "PASSED"}]})
    mounts: Mounts = {
        REQUIRED_TESTS_NAME: Mount(Inline(json.dumps(["a"]).encode())),
        "output.json": Mount(Inline(output.encode())),
    }
    if patch is not None:
      mounts[PATCH_NAME] = Mount(Inline(patch.encode()))
    return UnitTestSpec(
        eval_script="echo eval\n",
        mounts=mounts,
        grader=SweBenchProGrader(),
    )


def test_a_string_patch_is_a_constructor_value_not_an_input(tmp_path: Path):
  task = UnitTestEvalTask(instance=_EvalInstance(), patch="CANDIDATE")
  assert task.input_schema() == ()
  sandbox = _fake(tmp_path)
  result = task.execute(sandbox, output_dir=tmp_path / "out", timeout=10.0)
  assert result.run.status is RunStatus.SUCCESS
  assert (sandbox.workspace / PATCH_NAME).read_text() == "CANDIDATE"
  assert sandbox.scripts == [ENTRYSCRIPT_NAME]


def test_upstream_declares_the_patch_input_and_stages_no_placeholder():
  task = UnitTestEvalTask(instance=_EvalInstance(), patch=UPSTREAM)
  assert [schema.name for schema in task.input_schema()] == [PATCH_NAME]
  assert PATCH_NAME not in task.mounts()
  # …but the eval script was still compiled to apply the patch, so the
  # workflow-mounted file is what gets applied.
  assert ENTRYSCRIPT_NAME in task.mounts()


def test_upstream_grades_the_patch_mounted_by_the_workflow_edge(
    tmp_path: Path,
):
  upstream = tmp_path / "store" / PATCH_NAME
  upstream.parent.mkdir(parents=True)
  upstream.write_text("FROM UPSTREAM")
  sandbox = _fake(tmp_path)
  task = UnitTestEvalTask(instance=_EvalInstance(), patch=UPSTREAM)
  result = task.execute(
      sandbox,
      output_dir=tmp_path / "out",
      timeout=10.0,
      # what a workflow edge does: resolve input_schema() names against the
      # store and mount them read-only
      extra_mounts={
          PATCH_NAME: Mount(LocalFile(epath.Path(upstream)), read_only=True)
      },
  )
  assert result.run.status is RunStatus.SUCCESS
  assert (sandbox.workspace / PATCH_NAME).read_text() == "FROM UPSTREAM"
  verdict = task._parse.verdict
  assert verdict is not None and verdict.resolved is True


def test_grading_the_base_commit_stages_no_patch():
  task = UnitTestEvalTask(instance=_EvalInstance(), patch=None)
  assert task.input_schema() == ()
  assert PATCH_NAME not in task.mounts()


def test_the_task_output_schema_names_the_eval_outputs(tmp_path: Path):
  task = UnitTestEvalTask(instance=_EvalInstance(), patch="CANDIDATE")
  result = task.execute(
      _fake(tmp_path), output_dir=tmp_path / "out", timeout=10.0
  )
  assert "eval.entryscript.sh" in [s.name for s in result.output_schema]


# ─── CodingAgentTask: the instance path ──────────────────────────────────────


def test_coding_agent_task_defaults_the_prompt_to_the_instance(
    tmp_path: Path,
):
  # The wrapper path always passes the prompt; a workflow constructs the task
  # from the instance alone, and the instance's own prompt must reach the
  # harness. Imported here (not at module scope) to keep the fixture close.
  from swe_lab.conversation import Conversation
  from swe_lab.harnesses import Harness
  from swe_lab.rollout import CodingAgentTask

  prompts: list[str] = []

  @final
  class _PromptProbeHarness(Harness):
    """Records the prompt it was handed; stages one no-op script."""

    @property
    @override
    def name(self) -> str:
      return "probe"

    @override
    def observers(self) -> tuple[SandboxObserver, ...]:
      return ()

    @override
    def mounts(self, workdir: str) -> Mounts:
      del workdir
      return {"probe.sh": Mount(Inline(b"true\n"), executable=True)}

    @override
    def run(
        self,
        sb: SandboxFs,
        *,
        prompt: str,
        timeout: float,
        env: Mapping[str, str] | None = None,
    ) -> ExecResult:
      prompts.append(prompt)
      return sb.run_script("probe.sh", timeout=timeout, env=env)

    @override
    def native_outputs(self) -> dict[str, str]:
      return {}

    @override
    def to_conversation(self, sb: SandboxFs) -> Conversation:
      raise NotImplementedError

    @override
    def completed(self, sb: SandboxFs) -> bool:
      return False

  sandbox = _fake(tmp_path)
  task = CodingAgentTask(
      instance=_BareInstance(
          extra_mounts={"instance.txt": Mount(Inline(b"I"))}
      ),
      harness=_PromptProbeHarness(),
  )
  result = task.execute(sandbox, output_dir=tmp_path / "out", timeout=10.0)
  assert result.run.status is RunStatus.SUCCESS
  assert prompts == ["SOLVE THIS"]  # the instance's own prompt
  # total hooks: the instance's material AND the harness's files both staged
  assert (sandbox.workspace / "instance.txt").read_text() == "I"
  assert (sandbox.workspace / "probe.sh").is_file()
