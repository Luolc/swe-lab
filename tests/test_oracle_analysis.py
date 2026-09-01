"""OracleAnalysisTask: the phase-B composition, docker-free.

Runs over an injected :class:`FakeSandbox` (real local-dir file ops, scripted
exec) against an ``oracle_failures`` record built over a static underlying
instance, so the whole composition — mounts, observers, brief, guidebook
collection — is exercised while no agent ever spawns.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import override

from etils import epath
import pytest

from swe_lab.conversation import Conversation
from swe_lab.datasets.oracle_failures import OracleFailureInstance
from swe_lab.evaluation.unit_test import ENTRYSCRIPT_NAME
from swe_lab.harnesses import AgentOutcome, HarnessOutcomeObserver
from swe_lab.harnesses.claude_code import ClaudeCodeHarness
from swe_lab.rollout import CodingAgentTask, PROMPT_NAME
from swe_lab.sandbox import (
    ArtifactSchema,
    merge_output_schemas,
    Mount,
    RunResult,
    RunStatus,
)
from swe_lab.sandbox.observers import (
    DiffExtractObserver,
    GitHistoryPurgeObserver,
    ResultVerifyObserver,
)
from swe_lab.sandbox.observers.git_history_purge import PURGE_SCRIPT_NAME
from swe_lab.sandbox.testing import FakeSandbox
from swe_lab.trace_synthesis.guidebook import GUIDEBOOK_NAME, STAGE_FIELDS
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

  @override
  def _mount_one(self, target: str, mount: Mount) -> None:
    self.mount_targets.append(target)
    super()._mount_one(target, mount)

  @override
  def _dest(self, target: str) -> epath.Path:
    return epath.Path(self.workspace / target.lstrip("/"))


def _failure(underlying: _Underlying | None = None) -> OracleFailureInstance:
  return OracleFailureInstance(
      dataset="fake",
      instance_id="acme__widget-1",
      rollout_id=0,
      conversation=CONVERSATION.model_dump_json(),
      verdict=json.dumps({"resolved": False, "summary": {"missing": ["t::b"]}}),
      patch="diff --git a/x b/x\n+wrong\n",
      provenance="{}",
      instance=underlying or _Underlying(),
  )


def _guidebook(*, without: str = "") -> str:
  fields = "\n\n".join(
      f"**{name}.** …" for name in STAGE_FIELDS if name != without
  )
  return f"# Guidebook — x\n\n## Stage 1 — read\n\n{fields}\n"


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


def test_the_oracle_task_composes_no_purge_no_extractor_and_no_verifier():
  # The purge would strip the very history the Oracle is given; the verifier
  # would flag a run that is contaminated by design; a guidebook is not a
  # patch. Their absence is the design, pinned here rather than incidental.
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
