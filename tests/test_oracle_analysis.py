"""OracleAnalysisTask: the phase-B composition, docker-free.

Runs over an injected :class:`FakeSandbox` (real local-dir file ops, scripted
exec) against an ``oracle_failures`` record built over a static underlying
instance, so the whole composition — mounts, observers, brief, guidebook
collection — is exercised while no agent ever spawns.
"""

from __future__ import annotations

from collections.abc import Mapping
import dataclasses
import hashlib
import json
from pathlib import Path
from typing import override

from etils import epath
import pytest

from swe_lab.cli.overrides import apply_overrides, parse_overrides
from swe_lab.conversation import Conversation
from swe_lab.datasets.oracle_failures import OracleFailureInstance
from swe_lab.evaluation.unit_test import ENTRYSCRIPT_NAME
from swe_lab.git.patch import BASELINE_VERIFY_SCRIPT_NAME
from swe_lab.harnesses import AgentOutcome, HarnessOutcomeObserver
from swe_lab.harnesses.claude_code import ClaudeCodeHarness
from swe_lab.harnesses.claude_code.constants import AGENT_SCRIPT_NAME
from swe_lab.rollout import CodingAgentTask, PROMPT_NAME
from swe_lab.sandbox import (
    ArtifactSchema,
    ExecResult,
    merge_output_schemas,
    Mount,
    RunResult,
    RunStatus,
)
from swe_lab.sandbox.observers import (
    BASE_REF_NAME,
    DiffExtractObserver,
    GitHistoryPurgeObserver,
    ResultVerifyObserver,
)
from swe_lab.sandbox.observers.git_history_purge import PURGE_SCRIPT_NAME
from swe_lab.sandbox.testing import FakeSandbox
from swe_lab.trace_synthesis.guidebook import (
    GUIDEBOOK_NAME,
    RUBRIC_FIELDS,
    STAGE_FIELDS,
)
from swe_lab.trace_synthesis.oracle import (
    GOLD_PATCH_NAME,
    guidebook_of,
    GuidebookObserver,
    OracleAnalysisTask,
    PRESENT_METRIC,
    STAGES_METRIC,
    VALID_METRIC,
)
from swe_lab.trace_synthesis.sample import (
    FAILED_CONVERSATION_NAME,
    FAILED_PATCH_NAME,
    FAILED_VERDICT_NAME,
)
from swe_lab.workflow import AttemptResult, workflow_definition
import swe_lab.workflow.definitions as definitions

from .test_oracle_failures_record import _Underlying, CONVERSATION, SPEC


@dataclasses.dataclass
class _LocalFakeSandbox(FakeSandbox):
  """A ``FakeSandbox`` that records mount targets and keeps them local."""

  mount_targets: list[str] = dataclasses.field(default_factory=list)
  current_ref: str = ""

  @override
  def _mount_one(self, target: str, mount: Mount) -> None:
    self.mount_targets.append(target)
    super()._mount_one(target, mount)

  @override
  def _dest(self, target: str) -> epath.Path:
    return epath.Path(self.workspace / target.lstrip("/"))

  @override
  def run_script(
      self,
      name: str,
      *,
      timeout: float,
      env: Mapping[str, str] | None = None,
  ) -> ExecResult:
    if name == BASELINE_VERIFY_SCRIPT_NAME:
      self.calls.append(("run_script", name))
      self.scripts.append(name)
      self.script_envs.append(env)
      recorded = self.read(BASE_REF_NAME).decode().strip()
      if recorded != self.baseline_sha:
        return ExecResult(1, "", "baseline mismatch")
      self.current_ref = recorded
      return ExecResult(0, "", "")
    return super().run_script(name, timeout=timeout, env=env)


def _failure(
    underlying: _Underlying | None = None, *, patch_base_ref: str | None = None
) -> OracleFailureInstance:
  return OracleFailureInstance(
      dataset="fake",
      instance_id="acme__widget-1",
      rollout_id=0,
      conversation=CONVERSATION.model_dump_json(),
      verdict=json.dumps({"resolved": False, "summary": {"missing": ["t::b"]}}),
      patch="diff --git a/x b/x\n+wrong\n",
      provenance="{}",
      instance=underlying or _Underlying(),
      patch_base_ref=patch_base_ref,
  )


def _guidebook(
    *,
    without: str = "",
    include_rubric: bool = True,
    rubric_without: str = "",
) -> str:
  fields = "\n\n".join(
      f"**{name}.** …" for name in STAGE_FIELDS if name != without
  )
  rubric_fields = "\n\n".join(
      f"**{name}.** …" for name in RUBRIC_FIELDS if name != rubric_without
  )
  rubric = (
      f"## Supervisor rubric\n\n{rubric_fields}\n\n---\n\n"
      if include_rubric
      else ""
  )
  return f"# Guidebook — x\n\n{rubric}## Stage 1 — read\n\n{fields}\n"


def _task() -> OracleAnalysisTask:
  return OracleAnalysisTask(harness=ClaudeCodeHarness(model="sonnet"))


def _execute(
    tmp_path: Path, *, guidebook: str | None = None
) -> tuple[AttemptResult, _LocalFakeSandbox, Path]:
  workspace = tmp_path / "ws"
  if guidebook is not None:
    # The agent never runs here, so what it "wrote" is placed ahead of time.
    workspace.mkdir()
    (workspace / GUIDEBOOK_NAME).write_text(guidebook)
  sandbox = _LocalFakeSandbox(spec=SPEC, workspace=epath.Path(workspace))
  result = _task().execute(
      sandbox, _failure(), output_dir=tmp_path / "out", timeout=60.0
  )
  return result, sandbox, workspace


# ─── the invariant: phase B is contaminated on purpose, and says so ─────────


def test_the_oracle_has_no_purge_extractor_or_result_verifier():
  # The purge would strip the very history the Oracle is given; the result
  # verifier would flag a run that is contaminated by design; a guidebook is
  # not a patch. Their absence is the design, pinned here rather than
  # incidental.
  observers = _task().observers(_failure())
  kinds = {type(o) for o in observers}
  assert not kinds & {
      GitHistoryPurgeObserver,
      DiffExtractObserver,
      ResultVerifyObserver,
  }
  assert GuidebookObserver in kinds


def test_the_guidebook_is_the_declared_required_output():
  schema = merge_output_schemas(
      *(o.output_schema() for o in _task().observers(_failure()))
  )
  guidebook = next(s for s in schema if s.name == GUIDEBOOK_NAME)
  assert guidebook == ArtifactSchema(
      GUIDEBOOK_NAME,
      description="the Oracle's staged guidebook for a blind actor",
  )
  assert guidebook.required is True


# ─── the composition, end to end on the fake ─────────────────────────────────


def test_execute_stages_the_failure_the_reference_and_the_grading_procedure(
    tmp_path: Path,
):
  result, sandbox, workspace = _execute(tmp_path)

  assert result.run.status is RunStatus.SUCCESS
  # the instance's own material: the failure, staged read-only
  assert (
      Conversation.model_validate_json(
          (workspace / FAILED_CONVERSATION_NAME).read_bytes()
      )
      == CONVERSATION
  )
  assert json.loads((workspace / FAILED_VERDICT_NAME).read_text())[
      "summary"
  ] == {"missing": ["t::b"]}
  assert (
      workspace / FAILED_PATCH_NAME
  ).read_text() == "diff --git a/x b/x\n+wrong\n"
  # the privileged extras: the reference, and the grading procedure compiled
  # to apply the FAILED patch, with the dataset's grading files beside it
  assert (workspace / GOLD_PATCH_NAME).read_text() == "diff --git a/g b/g\n"
  assert (
      (workspace / ENTRYSCRIPT_NAME)
      .read_text()
      .startswith(f"git apply {FAILED_PATCH_NAME}")
  )
  assert (workspace / "run_script.sh").read_text() == "echo run"
  assert {
      FAILED_CONVERSATION_NAME,
      FAILED_VERDICT_NAME,
      FAILED_PATCH_NAME,
      GOLD_PATCH_NAME,
      ENTRYSCRIPT_NAME,
  } <= set(sandbox.mount_targets)
  # the brief is the declared input, built in-session, not a mount
  brief = (workspace / PROMPT_NAME).read_text()
  assert PROMPT_NAME not in sandbox.mount_targets
  # …and the harness landed its own copy where it wants it
  assert (workspace / "prompt.txt").read_text() == brief
  # no purge ran: the history is the Oracle's to read
  assert PURGE_SCRIPT_NAME not in sandbox.scripts
  assert BASELINE_VERIFY_SCRIPT_NAME not in sandbox.scripts


def test_a_baseline_failure_verifies_and_restores_its_recorded_tree(
    tmp_path: Path,
):
  recorded_ref = "b" * 40
  assert recorded_ref != SPEC.base_commit
  sandbox = _LocalFakeSandbox(
      spec=SPEC,
      workspace=epath.Path(tmp_path / "ws"),
      baseline_sha=recorded_ref,
      current_ref=SPEC.base_commit,
  )

  result = _task().execute(
      sandbox,
      _failure(patch_base_ref=recorded_ref),
      output_dir=tmp_path / "out",
      timeout=60.0,
  )

  assert result.run.status is RunStatus.SUCCESS
  assert sandbox.current_ref == recorded_ref


def test_a_baseline_failure_with_the_wrong_tree_stops_before_the_oracle(
    tmp_path: Path,
):
  sandbox = _LocalFakeSandbox(
      spec=SPEC,
      workspace=epath.Path(tmp_path / "ws"),
      baseline_sha="c" * 40,
      current_ref=SPEC.base_commit,
  )

  result = _task().execute(
      sandbox,
      _failure(patch_base_ref="b" * 40),
      output_dir=tmp_path / "out",
      timeout=60.0,
  )

  assert result.run.status is RunStatus.SETUP_ERROR
  assert BASELINE_VERIFY_SCRIPT_NAME in sandbox.scripts
  assert AGENT_SCRIPT_NAME not in sandbox.scripts


def test_the_brief_carries_the_task_statement_whole_and_names_the_files(
    tmp_path: Path,
):
  _, _, workspace = _execute(tmp_path)
  brief = (workspace / PROMPT_NAME).read_text()

  # verbatim and whole — an excerpt could not support an absence claim
  assert (
      "<<<TASK_STATEMENT\n" + _Underlying().prompt() + "\nTASK_STATEMENT>>>"
      in brief
  )
  for name in (
      FAILED_CONVERSATION_NAME,
      FAILED_VERDICT_NAME,
      FAILED_PATCH_NAME,
      GOLD_PATCH_NAME,
      ENTRYSCRIPT_NAME,
      GUIDEBOOK_NAME,
  ):
    assert f"`{name}`" in brief
  assert SPEC.base_commit in brief
  assert "f" * 40 in brief  # the fix commit, reachable because nothing purged
  # With a reference, the prose says so in both places the no-reference test
  # checks for silence.
  assert "the reference solution and" in brief
  assert "against the reference" in brief
  for field in STAGE_FIELDS:
    assert f"**{field}.**" in brief


def test_default_oracle_instructions_are_pinned(
    tmp_path: Path,
) -> None:
  """Pin the complete rubric-aware request rather than only its builder."""
  _, _, workspace = _execute(tmp_path)

  assert hashlib.sha256(
      (workspace / "prompt.txt").read_bytes()
  ).hexdigest() == (
      "eaa1e8a1e743f040903ae48f3eaf2cdd54f8c8dda1010dc76841ed5ce8172cd1"
  )


def test_oracle_override_instructions_reach_only_its_model_request(
    tmp_path: Path,
) -> None:
  """The task-level override replaces the Oracle request byte for byte."""
  instructions = "ORACLE-OVERRIDE-sentinel\nKeep this exact trailing line.\n"
  workspace = tmp_path / "ws"
  sandbox = _LocalFakeSandbox(spec=SPEC, workspace=epath.Path(workspace))
  (entry,) = apply_overrides(
      workflow_definition("oracle_analysis"),
      parse_overrides([f"--oracle_analysis.instructions={instructions}"]),
  )
  task = entry.task
  assert isinstance(task, OracleAnalysisTask)

  result = task.execute(
      sandbox, _failure(), output_dir=tmp_path / "out", timeout=60.0
  )

  assert result.run.status is RunStatus.SUCCESS
  assert (workspace / "prompt.txt").read_text() == instructions
  assert (workspace / PROMPT_NAME).read_text() != instructions


def test_an_instance_that_stages_no_failure_is_refused_before_the_sandbox(
    tmp_path: Path,
):
  # The generic run CLI defaults to swebench_pro, and an ordinary instance
  # assembles just as well — with a brief claiming three files that are not
  # there and the networked agent budget spent finding out. Refuse at
  # assembly, by the neutral names in `sample.py`, before anything is staged.
  sandbox = _LocalFakeSandbox(spec=SPEC, workspace=epath.Path(tmp_path / "ws"))
  with pytest.raises(ValueError, match="stages no failure to analyze"):
    _ = _task().execute(
        sandbox, _Underlying(), output_dir=tmp_path / "out", timeout=60.0
    )
  assert sandbox.mount_targets == []


def test_the_brief_states_the_two_hard_won_rules(tmp_path: Path):
  # Each rule is a lesson from a hand-written guidebook, and each is stated
  # as a rule because the spec's phrasing alone did not prevent the mistake:
  # an excerpt cannot support an absence claim, and a green suite is what the
  # *failed* actor saw too. Their absence from the brief is a regression.
  _, _, workspace = _execute(tmp_path)
  brief = (workspace / PROMPT_NAME).read_text()
  assert "**Quote the task statement whole, never in excerpt.**" in brief
  assert (
      "**The verification stage says what a green suite cannot tell you.**"
      in brief
  )


def test_the_oracle_brief_requires_a_rubric_alongside_the_tutorial(
    tmp_path: Path,
):
  """A compact representation must not replace the detailed one."""
  _, _, workspace = _execute(tmp_path)
  brief = (workspace / PROMPT_NAME).read_text()

  assert "## Supervisor rubric" in brief
  for field in RUBRIC_FIELDS:
    assert f"**{field}.**" in brief
  assert "## Stage 1 — <title>" in brief
  for field in STAGE_FIELDS:
    assert f"**{field}.**" in brief


def test_a_dataset_without_a_fix_commit_or_a_reference_is_briefed_honestly(
    tmp_path: Path,
):
  @dataclasses.dataclass(frozen=True)
  class _NoReference(_Underlying):
    fix_sha: str | None = None

    @override
    def gold_patch(self) -> str | None:
      return None

  sandbox = _LocalFakeSandbox(spec=SPEC, workspace=epath.Path(tmp_path / "ws"))
  _ = _task().execute(
      sandbox,
      _failure(_NoReference()),
      output_dir=tmp_path / "out",
      timeout=60.0,
  )
  brief = (tmp_path / "ws" / PROMPT_NAME).read_text()
  assert "records no upstream fix commit" in brief
  assert GOLD_PATCH_NAME not in brief
  assert GOLD_PATCH_NAME not in sandbox.mount_targets
  # …and the prose does not quietly keep promising one: neither the list of
  # what the Oracle has nor the diagnosis step mentions a reference.
  assert "reference solution" not in brief
  assert "against the reference" not in brief
  assert "the grader's verdict and the grading procedure" in brief


# ─── the output: present, valid, or not ──────────────────────────────────────


def test_a_written_guidebook_is_collected_and_the_attempt_is_valid(
    tmp_path: Path,
):
  result, _, _ = _execute(tmp_path, guidebook=_guidebook())

  assert result.run.artifacts[GUIDEBOOK_NAME].read_text() == _guidebook()
  assert result.run.metrics[PRESENT_METRIC] == 1.0
  assert result.run.metrics[VALID_METRIC] == 1.0
  assert result.run.metrics[STAGES_METRIC] == 1.0
  observer = guidebook_of(result)
  assert observer is not None and observer.valid is True
  assert _task().outputs_valid(result) is True
  assert "guidebook_problems" not in _task().record_extra(result)


def test_a_missing_guidebook_fails_the_attempt(tmp_path: Path):
  result, _, _ = _execute(tmp_path)

  assert GUIDEBOOK_NAME not in result.run.artifacts
  assert result.run.metrics[PRESENT_METRIC] == 0.0
  assert _task().outputs_valid(result) is False
  assert _task().should_retry(result) is True


def test_new_oracle_output_without_a_rubric_fails_the_attempt(tmp_path: Path):
  """Read compatibility does not weaken the new phase-B write contract."""
  result, _, _ = _execute(tmp_path, guidebook=_guidebook(include_rubric=False))

  assert result.run.metrics[VALID_METRIC] == 0.0
  assert _task().outputs_valid(result) is False
  assert _task().record_extra(result)["guidebook_problems"] == [
      "missing the '## Supervisor rubric' section"
  ]


def test_the_complete_tutorial_is_collected_beside_the_rubric(tmp_path: Path):
  """A valid rubric cannot make replaced or clipped tutorial text acceptable."""
  tutorial_sentinel = "TUTORIAL-SENTINEL-keep-the-detailed-derivation"
  complete = _guidebook().replace(
      "**Actions.** …", f"**Actions.** {tutorial_sentinel}"
  )

  result, _, _ = _execute(tmp_path, guidebook=complete)
  collected = result.run.artifacts[GUIDEBOOK_NAME].read_text()

  assert collected == complete
  assert result.run.metrics[VALID_METRIC] == 1.0
  assert "## Supervisor rubric" in collected
  assert tutorial_sentinel in collected


def test_a_guidebook_missing_a_justification_fails_the_attempt(tmp_path: Path):
  # The schema's one load-bearing field. The artifact is still collected —
  # a rejected guidebook is evidence — but the attempt is not valid, and the
  # record says why.
  result, _, _ = _execute(
      tmp_path, guidebook=_guidebook(without="Justification")
  )

  assert GUIDEBOOK_NAME in result.run.artifacts
  assert result.run.metrics[VALID_METRIC] == 0.0
  assert _task().outputs_valid(result) is False
  assert _task().record_extra(result)["guidebook_problems"] == [
      "stage 1: missing the 'Justification' field"
  ]


def test_an_ending_that_happened_to_the_agent_is_retried():
  observer = HarnessOutcomeObserver(harness=ClaudeCodeHarness())
  observer.outcome = AgentOutcome.EXECUTION_ERROR
  guidebook = GuidebookObserver(guidebook=_guidebook())
  result = AttemptResult(
      run=RunResult(
          label="x",
          status=RunStatus.SUCCESS,
          artifacts={GUIDEBOOK_NAME: epath.Path("/tmp/guidebook.md")},
          metrics={},
      ),
      exec_result=None,
      output_schema=(ArtifactSchema(GUIDEBOOK_NAME),),
      observers=(observer, guidebook),
  )
  assert _task().outputs_valid(result) is True  # it did produce a guidebook
  assert _task().should_retry(result) is True  # …but the crash was ours


# ─── the shipped definition ──────────────────────────────────────────────────


def test_the_oracle_analysis_workflow_is_registered_as_one_entry():
  (entry,) = workflow_definition("oracle_analysis")
  assert entry.key == definitions.ORACLE_ANALYSIS_KEY == "oracle_analysis"
  assert isinstance(entry.task, OracleAnalysisTask)
  # runs from a name alone: the brief is built in-session, nothing to --input
  assert entry.task.inputs_builder is not None
  assert [s.name for s in entry.task.input_schema()] == [PROMPT_NAME]
  # the same agent, network and credential as the rollout's entry
  rollout = workflow_definition("rollout")[0]
  assert isinstance(rollout.task, CodingAgentTask)
  assert entry.sandbox.network is True
  assert entry.sandbox.pass_env == rollout.sandbox.pass_env
  assert entry.task.harness == rollout.task.harness


@pytest.mark.parametrize("name", ["rollout", "rollout_and_unit_test"])
def test_the_rollout_definitions_still_purge(name: str):
  # Guards the boundary from the other side: turning the purge off for the
  # Oracle must not have touched the solving entries.
  rollout = workflow_definition(name)[0]
  assert isinstance(rollout.task, CodingAgentTask)
  assert rollout.task.purge_git_history is True
