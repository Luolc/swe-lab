"""OracleAnalysisTask: the phase-B composition, docker-free.

Runs over an injected :class:`FakeSandbox` (real local-dir file ops, scripted
exec) against a static instance, with the attempt staged as the four inputs a
phase-A pair leaves, so the whole composition — mounts, observers, brief,
guidebook collection — is exercised while no agent ever spawns.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import dataclasses
import hashlib
import json
from pathlib import Path
from typing import override

from etils import epath
import pytest

from swe_lab.cli.overrides import apply_overrides, parse_overrides
from swe_lab.conversation import Conversation, Message, Role, TextBlock
from swe_lab.conversation.observer import CONVERSATION_NAME
from swe_lab.datasets.instance import TaskInstance
from swe_lab.datasets.swebench_pro.unit_test import (
    REQUIRED_TESTS_NAME,
    SweBenchProGrader,
    SweBenchProVerdict,
)
from swe_lab.evaluation.unit_test import ENTRYSCRIPT_NAME
from swe_lab.evaluation.verdict import UnitTestSpec
from swe_lab.git.patch import BASELINE_VERIFY_SCRIPT_NAME
from swe_lab.harnesses import AgentOutcome, HarnessOutcomeObserver
from swe_lab.harnesses.claude_code import ClaudeCodeHarness
from swe_lab.harnesses.claude_code.constants import AGENT_SCRIPT_NAME
from swe_lab.rollout import CodingAgentTask, PROMPT_NAME
from swe_lab.sandbox import (
    ArtifactSchema,
    ExecResult,
    Inline,
    merge_output_schemas,
    Mount,
    Mounts,
    RunResult,
    RunStatus,
    SandboxSpec,
)
from swe_lab.sandbox.observers import (
    BASE_REF_NAME,
    DiffExtractObserver,
    GitHistoryPurgeObserver,
    PATCH_NAME,
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
    ATTEMPT_VERDICT_NAME,
    GOLD_PATCH_NAME,
    guidebook_of,
    GuidebookObserver,
    OracleAnalysisTask,
    PRESENT_METRIC,
    STAGES_METRIC,
    VALID_METRIC,
)
from swe_lab.workflow import (
    AttemptResult,
    registered_workflows,
    workflow_definition,
    WorkflowEntry,
)
import swe_lab.workflow.definitions as definitions

SPEC = SandboxSpec("acme__widget-1", "acme/widget:tag", "/app", "abc123")

CONVERSATION = Conversation(
    messages=[
        Message(role=Role.USER, content=[TextBlock(text="SOLVE THIS")]),
        Message(role=Role.ASSISTANT, content=[TextBlock(text="I tried.")]),
    ]
)

# The pre-agent baseline the attempt's patch was diffed against — distinct
# from `SPEC.base_commit`, so a grading procedure that reset to the wrong tree
# is visible.
RECORDED_REF = "b" * 40
FAILED_PATCH = "diff --git a/x b/x\n+wrong\n"


@dataclasses.dataclass(frozen=True)
class _Underlying(TaskInstance[SweBenchProVerdict]):
  """The instance under analysis: static, no network."""

  instance_id: str = "acme__widget-1"
  extra_mounts: Mounts = dataclasses.field(default_factory=dict)
  fix_sha: str | None = "f" * 40

  @override
  def sandbox_spec(self) -> SandboxSpec:
    return SPEC

  @override
  def mounts(self) -> Mounts:
    return dict(self.extra_mounts)

  @override
  def required_tests(self) -> Sequence[str]:
    return ("t::a", "t::b")

  @override
  def solution_sha(self) -> str | None:
    return self.fix_sha

  @override
  def prompt(self) -> str:
    return "SOLVE THIS\n\nRequirements:\n- do it"

  @override
  def gold_patch(self) -> str | None:
    return "diff --git a/g b/g\n"

  @override
  def unit_test_spec(
      self,
      *,
      apply_patch: bool,
      patch_name: str = PATCH_NAME,
      checkout_golden_tests: bool = True,
      patch_baseline: bool = False,
  ) -> UnitTestSpec[SweBenchProVerdict]:
    return UnitTestSpec(
        eval_script=(
            f"git apply {patch_name}\n"
            f"patch-baseline={patch_baseline}\n"
            "run-tests\n"
        ),
        mounts={
            "run_script.sh": Mount(Inline(b"echo run")),
            REQUIRED_TESTS_NAME: Mount(
                Inline(json.dumps(list(self.required_tests())).encode())
            ),
        },
        grader=SweBenchProGrader(),
        patch_name=patch_name,
        native_outputs={"output.json": "output.json"},
    )

  @override
  def run_provenance(self) -> dict[str, object]:
    return {"dataset": "fake", "language": "python"}


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


def _attempt_inputs(
    *, verdict: object | None = None, base_ref: str = RECORDED_REF
) -> dict[str, Mount]:
  """Stage the four files a phase-A pair leaves, as the chain's edges would.

  Args:
    verdict: What the grading entry's verdict holds. The default is a failed
      verdict; a caller passes ``{"resolved": True, ...}`` for the arm where
      the blind attempt passed, or something unusable for the arm where nobody
      can tell.
    base_ref: The recorded pre-agent baseline.

  Returns:
    The inputs, by store name.
  """
  facts = (
      {"resolved": False, "summary": {"missing": ["t::b"]}}
      if verdict is None
      else verdict
  )
  return {
      CONVERSATION_NAME: Mount(
          Inline(CONVERSATION.model_dump_json().encode()), read_only=True
      ),
      PATCH_NAME: Mount(Inline(FAILED_PATCH.encode()), read_only=True),
      BASE_REF_NAME: Mount(Inline(f"{base_ref}\n".encode()), read_only=True),
      ATTEMPT_VERDICT_NAME: Mount(
          Inline(json.dumps(facts).encode()), read_only=True
      ),
  }


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


def _sandbox(tmp_path: Path, *, baseline_sha: str = RECORDED_REF):
  return _LocalFakeSandbox(
      spec=SPEC,
      workspace=epath.Path(tmp_path / "ws"),
      baseline_sha=baseline_sha,
      current_ref=SPEC.base_commit,
  )


def _execute(
    tmp_path: Path,
    *,
    guidebook: str | None = None,
    verdict: object | None = None,
    instance: _Underlying | None = None,
    task: OracleAnalysisTask | None = None,
) -> tuple[AttemptResult, _LocalFakeSandbox, Path]:
  workspace = tmp_path / "ws"
  if guidebook is not None:
    # The agent never runs here, so what it "wrote" is placed ahead of time.
    workspace.mkdir()
    (workspace / GUIDEBOOK_NAME).write_text(guidebook)
  sandbox = _sandbox(tmp_path)
  result = (task or _task()).execute(
      sandbox,
      instance or _Underlying(),
      output_dir=tmp_path / "out",
      timeout=60.0,
      extra_mounts=_attempt_inputs(verdict=verdict),
  )
  return result, sandbox, workspace


# ─── the invariant: phase B is contaminated on purpose, and says so ─────────


def test_the_oracle_has_no_purge_extractor_or_result_verifier():
  # The purge would strip the very history the Oracle is given; the result
  # verifier would flag a run that is contaminated by design; a guidebook is
  # not a patch. Their absence is the design, pinned here rather than
  # incidental.
  observers = _task().observers(_Underlying())
  kinds = {type(o) for o in observers}
  assert not kinds & {
      GitHistoryPurgeObserver,
      DiffExtractObserver,
      ResultVerifyObserver,
  }
  assert GuidebookObserver in kinds


def test_the_guidebook_is_the_declared_required_output():
  schema = merge_output_schemas(
      *(o.output_schema() for o in _task().observers(_Underlying()))
  )
  guidebook = next(s for s in schema if s.name == GUIDEBOOK_NAME)
  assert guidebook == ArtifactSchema(
      GUIDEBOOK_NAME,
      description="the Oracle's staged guidebook for a blind actor",
  )
  assert guidebook.required is True


# ─── the composition, end to end on the fake ─────────────────────────────────


def test_execute_stages_the_attempt_the_reference_and_the_grading_procedure(
    tmp_path: Path,
):
  result, sandbox, workspace = _execute(tmp_path)

  assert result.run.status is RunStatus.SUCCESS
  # the attempt, staged read-only under the names its producers gave it
  assert (
      Conversation.model_validate_json(
          (workspace / CONVERSATION_NAME).read_bytes()
      )
      == CONVERSATION
  )
  assert json.loads((workspace / ATTEMPT_VERDICT_NAME).read_text())[
      "summary"
  ] == {"missing": ["t::b"]}
  assert (workspace / PATCH_NAME).read_text() == FAILED_PATCH
  # the privileged extras: the reference, and the grading procedure compiled
  # to apply the attempt's patch against its recorded baseline, with the
  # dataset's grading files beside it
  assert (workspace / GOLD_PATCH_NAME).read_text() == "diff --git a/g b/g\n"
  entryscript = (workspace / ENTRYSCRIPT_NAME).read_text()
  assert entryscript.startswith(f"git apply {PATCH_NAME}\n")
  assert "patch-baseline=True" in entryscript
  assert (workspace / "run_script.sh").read_text() == "echo run"
  assert {
      CONVERSATION_NAME,
      ATTEMPT_VERDICT_NAME,
      PATCH_NAME,
      BASE_REF_NAME,
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


def test_the_attempt_verifies_and_restores_its_recorded_tree(
    tmp_path: Path,
):
  assert SPEC.base_commit != RECORDED_REF

  result, sandbox, _ = _execute(tmp_path)

  assert result.run.status is RunStatus.SUCCESS
  assert BASELINE_VERIFY_SCRIPT_NAME in sandbox.scripts
  assert sandbox.current_ref == RECORDED_REF


def test_an_attempt_with_the_wrong_tree_stops_before_the_oracle(
    tmp_path: Path,
):
  sandbox = _sandbox(tmp_path, baseline_sha="c" * 40)

  result = _task().execute(
      sandbox,
      _Underlying(),
      output_dir=tmp_path / "out",
      timeout=60.0,
      extra_mounts=_attempt_inputs(),
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
      CONVERSATION_NAME,
      ATTEMPT_VERDICT_NAME,
      PATCH_NAME,
      BASE_REF_NAME,
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


@pytest.mark.parametrize("resolved", [False, True])
def test_the_brief_offers_no_stage_label_the_schema_does_not_measure(
    tmp_path: Path, resolved: bool
) -> None:
  """The brief names the five measured labels and no alternatives to them.

  It used to offer `**Edits.**` / `**Tests.**` to "a stage that changes
  code", and two live Oracles substituted them for `**Actions.**` and
  `**Expected observations.**` in exactly that stage — so
  ``guidebook.valid`` read 0 on both guidebooks and discriminated nothing
  (#453). Both verdict branches share one template, and this pins that.
  """
  _, _, workspace = _execute(
      tmp_path, verdict={"resolved": resolved, "summary": {"missing": []}}
  )
  brief = (workspace / PROMPT_NAME).read_text()

  assert "**Edits.**" not in brief
  assert "**Tests.**" not in brief
  # …and the code-changing stage is told where those belong instead.
  assert "edits it makes under `**Actions.**`" in brief
  assert "tests it runs under\n`**Expected observations.**`" in brief


# ─── the brief branches on the verdict, and only on the verdict ──────────────


def test_a_failed_attempt_gets_the_brief_that_diagnoses_it(tmp_path: Path):
  # The control arm of the test below: same task, same files, opposite
  # verdict — and the two briefs must not be the same document.
  _, _, workspace = _execute(tmp_path)
  brief = (workspace / PROMPT_NAME).read_text()

  assert "attempted the task below and failed its graded" in brief
  assert "**Diagnose before you write.**" in brief
  assert "decision at which the attempt went wrong" in brief
  assert "**Make the failing stage a decision, not a formality.**" in brief
  # …and it never tells an Oracle that a run which failed passed.
  assert "passed its graded" not in brief
  assert "**This attempt passed.**" not in brief
  assert "guessed rather than derived" not in brief


def test_a_passed_attempt_gets_the_brief_that_finds_the_guesswork(
    tmp_path: Path,
):
  """A run that passed is analysed, not narrated as a failure.

  Run r1 of the from-scratch chain passed its graded tests, was briefed as a
  failure anyway, and its Oracle stopped to ask an operator who was not there
  — 900 s of agent time and no guidebook. The brief now says what happened,
  and asks for the steps that were guessed rather than derived.
  """
  _, _, workspace = _execute(
      tmp_path, verdict={"resolved": True, "summary": {"missing": []}}
  )
  brief = (workspace / PROMPT_NAME).read_text()

  assert "attempted the task below and passed its graded" in brief
  assert "**This attempt passed.**" in brief
  assert "guessed rather than derived" in brief
  assert "**Find what was guessed, not what went wrong.**" in brief
  assert "**Make the guessed step a decision, not a formality.**" in brief
  # …and nothing left over from the failure brief invents a failure.
  assert "failed its graded" not in brief
  assert "decision at which the attempt went wrong" not in brief
  assert "**Diagnose before you write.**" not in brief
  # The job is still a guidebook — there is no refusal path in either brief.
  assert f"write\n**`{GUIDEBOOK_NAME}`**" in brief


def test_an_unreadable_verdict_stops_the_run_instead_of_guessing(
    tmp_path: Path,
):
  # Which brief to write is a claim about what happened. With no `resolved`
  # to read, both briefs would state something nobody established — so the
  # attempt fails in the workspace, before the agent is launched, and says
  # what it could not read.
  result, sandbox, _ = _execute(
      tmp_path, verdict={"summary": {"missing": ["t::b"]}}
  )

  assert result.run.status is not RunStatus.SUCCESS
  assert AGENT_SCRIPT_NAME not in sandbox.scripts


# The two default briefs, by digest of the complete model request.
_FAILED_BRIEF_SHA = (
    "7bbfc8488acc222a9ed58ac1a4e9f21c42913a9c3f4a1fcaa2e7d62728a69b4a"
)
_PASSED_BRIEF_SHA = (
    "4e00f0315974369a04a1fdca6bf2e20a6a848145c59df777b58fe49fdf4981d1"
)


@pytest.mark.parametrize(
    ("resolved", "digest"),
    [(False, _FAILED_BRIEF_SHA), (True, _PASSED_BRIEF_SHA)],
)
def test_default_oracle_instructions_are_pinned(
    tmp_path: Path, resolved: bool, digest: str
) -> None:
  """Pin the complete rubric-aware request rather than only its builder.

  Both briefs, because both are defaults now: an edit that quietly rewrites
  the branch nobody has run yet is the one nothing else would catch.
  """
  _, _, workspace = _execute(
      tmp_path, verdict={"resolved": resolved, "summary": {"missing": []}}
  )

  assert (
      hashlib.sha256((workspace / "prompt.txt").read_bytes()).hexdigest()
      == digest
  )


def test_oracle_override_instructions_reach_only_its_model_request(
    tmp_path: Path,
) -> None:
  """The task-level override replaces the Oracle request byte for byte."""
  instructions = "ORACLE-OVERRIDE-sentinel\nKeep this exact trailing line.\n"
  task = _shipped_oracle(
      [f"--oracle_analysis.instructions={instructions}"]
  ).task
  assert isinstance(task, OracleAnalysisTask)

  result, _, workspace = _execute(tmp_path, task=task)

  assert result.run.status is RunStatus.SUCCESS
  assert (workspace / "prompt.txt").read_text() == instructions
  assert (workspace / PROMPT_NAME).read_text() != instructions


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

  _, sandbox, workspace = _execute(tmp_path, instance=_NoReference())
  brief = (workspace / PROMPT_NAME).read_text()
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
  # Nothing at all is a missing declared output, not a judgement about one:
  # this is the one guidebook-shaped thing that still fails an attempt.
  result, _, _ = _execute(tmp_path)

  assert GUIDEBOOK_NAME not in result.run.artifacts
  assert result.run.metrics[PRESENT_METRIC] == 0.0
  assert _task().outputs_valid(result) is False
  assert _task().should_retry(result) is True


def test_oracle_output_without_a_rubric_is_measured_and_kept(tmp_path: Path):
  """An imperfect guidebook is recorded, not rejected (ADR-0027).

  The control arm is ``test_a_written_guidebook_is_collected_and_the_attempt_
  is_valid`` above: the same attempt with a complete guidebook reports
  ``guidebook.valid 1`` and no problems, so the metric still discriminates
  while neither attempt is failed or retried.
  """
  result, _, _ = _execute(tmp_path, guidebook=_guidebook(include_rubric=False))

  assert result.run.metrics[VALID_METRIC] == 0.0
  assert _task().record_extra(result)["guidebook_problems"] == [
      "missing the '## Supervisor rubric' section"
  ]
  # …and the schema result changes nothing about the attempt itself.
  assert _task().outputs_valid(result) is True


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


def test_a_guidebook_missing_a_justification_is_measured_and_kept(
    tmp_path: Path,
):
  # The schema's one load-bearing field. The artifact is collected, the
  # measurement says what it lacks, and the guidebook goes downstream: a
  # re-roll buys another sample of the same writer at the price of a paid
  # run, which is what ADR-0027 refuses to spend.
  result, _, _ = _execute(
      tmp_path, guidebook=_guidebook(without="Justification")
  )

  assert GUIDEBOOK_NAME in result.run.artifacts
  assert result.run.metrics[VALID_METRIC] == 0.0
  assert _task().record_extra(result)["guidebook_problems"] == [
      "stage 1: missing the 'Justification' field"
  ]
  assert _task().outputs_valid(result) is True


def _attempt(
    *, outcome: AgentOutcome, guidebook: GuidebookObserver
) -> AttemptResult:
  """Assemble one finished attempt from the two observers that judge it.

  Args:
    outcome: How the agent's own trace says the run ended.
    guidebook: The collector, carrying whatever the Oracle wrote.

  Returns:
    A successful engine run whose declared output is present.
  """
  observer = HarnessOutcomeObserver(harness=ClaudeCodeHarness())
  observer.outcome = outcome
  return AttemptResult(
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


def test_a_schema_result_never_buys_another_paid_attempt():
  """What the check found is what the model wrote, not bad luck (ADR-0027).

  The control arm is the test below: the *same* invalid guidebook under an
  ending that happened to the agent is retried, so this pair separates "the
  guidebook is imperfect" from "the run broke" rather than agreeing on both.
  """
  invalid = GuidebookObserver(
      guidebook=_guidebook(without="Justification"),
      problems=("stage 1: missing the 'Justification' field",),
  )
  result = _attempt(outcome=AgentOutcome.FINISHED, guidebook=invalid)

  assert invalid.valid is False
  assert _task().outputs_valid(result) is True
  assert _task().should_retry(result) is False


def test_the_shipped_oracle_entries_carry_no_retry_budget():
  """The policy above is dormant on every path we ship.

  `should_retry` describes endings worth another attempt, and `run_task`
  loops `range(retries + 1)` — so with a budget of 0 the answer is never
  acted on. Pinned rather than described, because a docstring that promises
  behaviour the shipped entry cannot reach is the defect this test exists to
  keep from coming back; if someone gives an Oracle entry a budget, they have
  to come here and say so.

  **Derived from the registry, not from a list of workflow names.** A
  hand-written list is green about the definitions it happens to name — the
  first version of this test missed a workflow — so the set is every
  registered entry whose task is this one, and a new workflow joins it by
  existing.
  """
  budgets = {
      (name, entry.key): entry.retries
      for name in registered_workflows()
      for entry in workflow_definition(name)
      if isinstance(entry.task, OracleAnalysisTask)
  }

  # The positive half: the set is not empty, so an enumeration that found
  # nothing cannot pass as "every one of them is 0".
  assert set(budgets) == {("from_scratch_guided_trace", "oracle_analysis")}
  assert set(budgets.values()) == {0}


def test_a_caller_who_pays_for_a_retry_gets_this_policy():
  """The control arm: the budget is the only thing keeping it dormant.

  An operator raises it per run — the entry field is overridable — and the
  policy is then reachable exactly as written, which is why the method stays
  rather than being deleted as unreachable.
  """
  entry = _shipped_oracle(["--oracle_analysis.retries=1"])

  assert entry.retries == 1
  assert isinstance(entry.task, OracleAnalysisTask)
  crashed = _attempt(
      outcome=AgentOutcome.EXECUTION_ERROR,
      guidebook=GuidebookObserver(guidebook=_guidebook()),
  )
  assert entry.task.should_retry(crashed) is True


def test_an_ending_that_happened_to_the_agent_is_retried():
  # The exceptional case retrying still exists for — and the control arm of
  # the test above: an invalid guidebook, so the only thing that differs is
  # how the run ended.
  invalid = GuidebookObserver(
      guidebook=_guidebook(without="Justification"),
      problems=("stage 1: missing the 'Justification' field",),
  )
  result = _attempt(outcome=AgentOutcome.EXECUTION_ERROR, guidebook=invalid)

  assert _task().outputs_valid(result) is True  # it did produce a guidebook
  assert _task().should_retry(result) is True  # …but the crash was ours


# ─── the shipped definition ──────────────────────────────────────────────────


def _shipped_oracle(overrides: list[str]) -> WorkflowEntry:
  """Return the chain's Oracle entry after an operator's per-run overrides."""
  return next(
      entry
      for entry in apply_overrides(
          workflow_definition("from_scratch_guided_trace"),
          parse_overrides(overrides),
      )
      if entry.key == definitions.ORACLE_ANALYSIS_KEY
  )


def test_the_chains_oracle_runs_the_rollouts_agent():
  entry = _shipped_oracle([])
  assert entry.key == definitions.ORACLE_ANALYSIS_KEY == "oracle_analysis"
  assert isinstance(entry.task, OracleAnalysisTask)
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
