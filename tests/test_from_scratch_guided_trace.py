"""``from_scratch_guided_trace``: five entries told apart by key, bound by edge.

Declaration level: the keys are distinct, and at bind time every input
resolves to the producer the definition intends — the guided grading to the
guided rollout, the Oracle to the blind pair. Two control arms say the checks
have teeth: drop the one binding two producers make necessary and the engine
refuses; give two grading entries one key and the declaration is refused. Then
the Oracle's second failure source over a ``FakeSandbox``, and a five-step
smoke with fake tasks through ``Workflow.execute`` — the real definition's
bindings, satisfied by tasks with the real schemas, on real store records that
the 2×2 reading is then taken from. No agent runs and no container starts.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import json
from pathlib import Path
from typing import Any, final, override

from etils import epath
import pytest

from swe_lab.conversation.observer import CONVERSATION_NAME
from swe_lab.datasets.instance import TaskInstance
from swe_lab.evaluation.unit_test import ENTRYSCRIPT_NAME, UnitTestTask
from swe_lab.git.patch import BASELINE_VERIFY_SCRIPT_NAME
from swe_lab.harnesses.claude_code import ClaudeCodeHarness
from swe_lab.harnesses.claude_code.constants import AGENT_SCRIPT_NAME
from swe_lab.rollout import CodingAgentTask, PROMPT_NAME
from swe_lab.sandbox import (
    ArtifactSchema,
    Contribution,
    ExecResult,
    FilesystemStore,
    Inline,
    Mount,
    RunStatus,
    SandboxFs,
    SandboxObserver,
)
from swe_lab.sandbox.observers import BASE_REF_NAME, PATCH_NAME
from swe_lab.sandbox.testing import FakeSandboxConfig
from swe_lab.trace_synthesis.guidebook import GUIDEBOOK_NAME
from swe_lab.trace_synthesis.guided_gain import (
    Cell,
    guided_gain,
    IncompleteRun,
)
from swe_lab.trace_synthesis.oracle import (
    oracle_prompt,
    OracleAnalysisTask,
    PRODUCED_FAILURE,
    produced_failure_prompt,
    STAGED_FAILURE,
)
from swe_lab.trace_synthesis.sample import FAILURE_NAMES
from swe_lab.workflow import (
    AttemptResult,
    EntryStatus,
    Task,
    Workflow,
    workflow_definition,
    WorkflowEntry,
    WorkflowError,
)
import swe_lab.workflow.definitions as definitions
from swe_lab.workflow.workflow import _resolve_edges

from .test_oracle_analysis import _guidebook, _LocalFakeSandbox
from .test_oracle_failures_record import _Underlying, CONVERSATION, SPEC

KEYS = [
    definitions.BASELINE_ROLLOUT_KEY,
    definitions.BASELINE_UNIT_TEST_KEY,
    definitions.ORACLE_ANALYSIS_KEY,
    definitions.GUIDED_ROLLOUT_KEY,
    definitions.GUIDED_UNIT_TEST_KEY,
]
VERDICT_ARTIFACT = PRODUCED_FAILURE.verdict

# The edge map the definition intends — every input, and who supplies it.
EXPECTED_EDGES = {
    "baseline_rollout": {},
    "baseline_unit_test": {
        PATCH_NAME: "baseline_rollout",
        BASE_REF_NAME: "baseline_rollout",
    },
    "oracle_analysis": {
        CONVERSATION_NAME: "baseline_rollout",
        PATCH_NAME: "baseline_rollout",
        BASE_REF_NAME: "baseline_rollout",
        VERDICT_ARTIFACT: "baseline_unit_test",
    },
    "guided_rollout": {GUIDEBOOK_NAME: "oracle_analysis"},
    "guided_unit_test": {
        PATCH_NAME: "guided_rollout",
        BASE_REF_NAME: "guided_rollout",
    },
}


# ─── the declaration ─────────────────────────────────────────────────────────


def test_the_five_entries_are_five_keys_and_the_key_names_the_phase():
  entries = workflow_definition("from_scratch_guided_trace")
  assert entries is definitions.FROM_SCRATCH_GUIDED_TRACE
  assert [entry.key for entry in entries] == KEYS
  assert len(set(KEYS)) == 5
  # The two solve + grade pairs are the shipped ones under new keys — same
  # tasks, same budgets, same credentials — so a record under
  # `baseline_unit_test` reads exactly like one under `unit_test`.
  baseline, grading, _, guided, guided_grading = entries
  plain, plain_grading = workflow_definition("rollout_and_unit_test")
  assert isinstance(baseline.task, CodingAgentTask)
  assert baseline.task == plain.task
  assert (baseline.timeout, baseline.sandbox) == (
      plain.timeout,
      plain.sandbox,
  )
  for entry in (grading, guided_grading):
    assert isinstance(entry.task, UnitTestTask)
    assert entry.task == plain_grading.task
    assert (entry.timeout, entry.retries) == (
        plain_grading.timeout,
        plain_grading.retries,
    )
  # …and the guided rollout is the segmented supervisor reading the guidebook,
  # as in `oracle_guided_trace`, under its own key. (Not compared whole: each
  # build of the segmented entry closes over a fresh policy factory.)
  _, guided_in_trace, _ = workflow_definition("oracle_guided_trace")
  assert isinstance(guided.task, CodingAgentTask)
  assert isinstance(guided_in_trace.task, CodingAgentTask)
  assert guided.key != guided_in_trace.key
  assert guided.task.extra_inputs == guided_in_trace.task.extra_inputs
  assert [s.name for s in guided.task.extra_inputs] == [GUIDEBOOK_NAME]
  assert isinstance(guided.task.harness, ClaudeCodeHarness)
  assert guided.task.harness.segmented is not None
  assert guided.task.harness.segmented.guidebook_name == GUIDEBOOK_NAME
  assert guided.task.harness.capture == "stream"


def test_every_edge_binds_to_the_producer_the_definition_intends(
    tmp_path: Path,
):
  # Construction validates the declaration; the bind resolves the edges with
  # an instance in hand, before any container — the `_resolve_edges` that
  # `execute` runs first, called directly so the map itself is the assertion.
  workflow = Workflow(
      store=FilesystemStore(epath.Path(tmp_path / "store")),
      sweep_id="sw",
      rollout_id=0,
      entries=definitions.FROM_SCRATCH_GUIDED_TRACE,
  )
  edges = _resolve_edges(workflow.entries, _Underlying(), provided=set())
  assert edges == EXPECTED_EDGES


def test_the_guided_grading_must_be_bound_because_two_rollouts_make_a_patch():
  # The control arm for the map above: by the time the guided grading binds,
  # `patch.diff` has two producers, and the engine refuses to pick one for
  # us. That refusal is the mechanism keeping the two verdicts apart.
  *head, guided_grading = definitions.FROM_SCRATCH_GUIDED_TRACE
  unbound = (*head, replace(guided_grading, inputs=()))
  with pytest.raises(WorkflowError, match="bind it explicitly"):
    _ = _resolve_edges(unbound, _Underlying(), provided=set())


def test_two_grading_entries_under_one_key_are_refused(tmp_path: Path):
  # The other control arm: the reason there are two grading keys at all. Give
  # the guided grading the baseline's key and the declaration is refused —
  # nothing downstream gets to overwrite the blind verdict's records.
  *head, guided_grading = definitions.FROM_SCRATCH_GUIDED_TRACE
  colliding = (
      *head,
      replace(guided_grading, key=definitions.BASELINE_UNIT_TEST_KEY),
  )
  with pytest.raises(WorkflowError, match="duplicate entry keys"):
    _ = Workflow(
        store=FilesystemStore(epath.Path(tmp_path / "store")),
        sweep_id="sw",
        rollout_id=0,
        entries=colliding,
    )


# ─── the Oracle's second failure source ──────────────────────────────────────


def test_the_staged_oracle_is_unchanged_and_the_chains_oracle_takes_inputs():
  staged = OracleAnalysisTask(harness=ClaudeCodeHarness(model="sonnet"))
  assert staged.failure is STAGED_FAILURE
  assert staged.inputs_builder is oracle_prompt
  assert [s.name for s in staged.input_schema()] == [PROMPT_NAME]

  (chained,) = (
      entry
      for entry in definitions.FROM_SCRATCH_GUIDED_TRACE
      if entry.key == definitions.ORACLE_ANALYSIS_KEY
  )
  assert isinstance(chained.task, OracleAnalysisTask)
  assert chained.task.failure_inputs is True
  assert chained.task.failure is PRODUCED_FAILURE
  assert chained.task.inputs_builder is produced_failure_prompt
  # The brief is still built in-session; the four produced files are inputs,
  # named as their producers name them — nothing is renamed on the way.
  assert [s.name for s in chained.task.input_schema()] == [
      PROMPT_NAME,
      CONVERSATION_NAME,
      PATCH_NAME,
      BASE_REF_NAME,
      VERDICT_ARTIFACT,
  ]
  assert all(s.required for s in chained.task.input_schema())
  # …and none of them is a staged name: the two contracts do not overlap.
  assert not set(FAILURE_NAMES) & {s.name for s in chained.task.input_schema()}


def _produced_failure(*, base_ref: str) -> dict[str, Mount]:
  """Stage the four files a phase-A pair leaves, as an edge would."""
  verdict = {
      "resolved": False,
      "score": 0.5,
      "metrics": {"passed": 1.0, "missing": 1.0, "required": 2.0},
      "summary": {"missing": ["t::b"], "passed": ["t::a"]},
  }
  return {
      CONVERSATION_NAME: Mount(
          Inline(CONVERSATION.model_dump_json().encode()), read_only=True
      ),
      PATCH_NAME: Mount(
          Inline(b"diff --git a/x b/x\n+wrong\n"), read_only=True
      ),
      BASE_REF_NAME: Mount(Inline(f"{base_ref}\n".encode()), read_only=True),
      VERDICT_ARTIFACT: Mount(
          Inline(json.dumps(verdict).encode()), read_only=True
      ),
  }


def test_the_oracle_reads_the_failure_the_edges_deliver(tmp_path: Path):
  # The produced-failure path, end to end on the fake: the four inputs are
  # staged, the brief names them by those names, the grading procedure is
  # compiled to apply *that* patch against the recorded baseline, and the
  # baseline is verified and restored before the Oracle runs.
  recorded_ref = "b" * 40
  assert recorded_ref != SPEC.base_commit
  workspace = tmp_path / "ws"
  workspace.mkdir()
  (workspace / GUIDEBOOK_NAME).write_text(_guidebook())
  sandbox = _LocalFakeSandbox(
      spec=SPEC,
      workspace=epath.Path(workspace),
      baseline_sha=recorded_ref,
      current_ref=SPEC.base_commit,
  )
  task = OracleAnalysisTask(
      harness=ClaudeCodeHarness(model="sonnet"), failure_inputs=True
  )

  result = task.execute(
      sandbox,
      _Underlying(),  # a plain instance: it stages no failure of its own
      output_dir=tmp_path / "out",
      timeout=60.0,
      extra_mounts=_produced_failure(base_ref=recorded_ref),
  )

  assert result.run.status is RunStatus.SUCCESS
  assert task.outputs_valid(result) is True
  assert {
      CONVERSATION_NAME,
      PATCH_NAME,
      BASE_REF_NAME,
      VERDICT_ARTIFACT,
      ENTRYSCRIPT_NAME,
  } <= set(sandbox.mount_targets)
  # the grading procedure applies the produced patch, in baseline mode
  entryscript = (workspace / ENTRYSCRIPT_NAME).read_text()
  assert entryscript.startswith(f"git apply {PATCH_NAME}\n")
  assert "patch-baseline=True" in entryscript
  # …and the tree was verified against the recorded ref and reset to it
  assert BASELINE_VERIFY_SCRIPT_NAME in sandbox.scripts
  assert sandbox.current_ref == recorded_ref
  # the brief names the files the Oracle actually has, and none it does not
  brief = (workspace / PROMPT_NAME).read_text()
  for name in (CONVERSATION_NAME, PATCH_NAME, BASE_REF_NAME, VERDICT_ARTIFACT):
    assert f"`{name}`" in brief
  for name in FAILURE_NAMES:
    assert name not in brief
  assert f"applies `{PATCH_NAME}`" in brief


def test_a_produced_failure_nobody_supplied_stops_before_the_agent(
    tmp_path: Path,
):
  # Standalone, with only the base ref staged (so the baseline verify that
  # runs first has a tree to check): the brief still builds, then the
  # in-session requiredness check refuses — an assembly error naming the
  # inputs, not an agent budget spent on a brief about files that are not
  # there.
  recorded_ref = "b" * 40
  sandbox = _LocalFakeSandbox(
      spec=SPEC,
      workspace=epath.Path(tmp_path / "ws"),
      baseline_sha=recorded_ref,
      current_ref=SPEC.base_commit,
  )
  task = OracleAnalysisTask(
      harness=ClaudeCodeHarness(model="sonnet"), failure_inputs=True
  )

  result = task.execute(
      sandbox,
      _Underlying(),
      output_dir=tmp_path / "out",
      timeout=60.0,
      extra_mounts={
          BASE_REF_NAME: Mount(
              Inline(f"{recorded_ref}\n".encode()), read_only=True
          )
      },
  )

  assert result.run.status is not RunStatus.SUCCESS
  assert "required input(s) missing" in repr(result.run.error)
  for name in (CONVERSATION_NAME, PATCH_NAME, VERDICT_ARTIFACT):
    assert name in repr(result.run.error)
  assert AGENT_SCRIPT_NAME not in sandbox.scripts


# ─── five fake steps through Workflow.execute ────────────────────────────────


@final
@dataclass
class _Emit(SandboxObserver):
  """Declare and emit fixed artifacts, plus optional metrics."""

  artifacts: dict[str, bytes]
  metrics: dict[str, float] = field(default_factory=dict)

  @override
  def output_schema(self) -> tuple[ArtifactSchema, ...]:
    return tuple(
        ArtifactSchema(name, description="a produced thing")
        for name in self.artifacts
    )

  @override
  def before_destroy(self, sb: SandboxFs) -> Contribution | None:
    del sb
    return Contribution(
        inline_artifacts=dict(self.artifacts), metrics=dict(self.metrics)
    )


@final
@dataclass
class _Step(Task):
  """A task with the real entry's schema: consumes ``needs``, emits ``makes``.

  Records what it read, so a test can tell which producer fed it.
  """

  needs: tuple[str, ...] = ()
  makes: dict[str, bytes] = field(default_factory=dict)
  metrics: dict[str, float] = field(default_factory=dict)
  seen: dict[str, bytes] = field(default_factory=dict)
  # How many leading attempts to judge invalid — a flaky grading (retried) or
  # a failing step (not retried), as the entry's retry budget decides. Keyed
  # by the attempt's result, not by call: the runner judges one attempt twice
  # (validity, then retry-desire), and both calls must agree.
  invalid_attempts: int = 0
  judged: list[int] = field(default_factory=list)

  @override
  def input_schema(self) -> tuple[ArtifactSchema, ...]:
    return tuple(
        ArtifactSchema(name, description="an upstream thing")
        for name in self.needs
    )

  @override
  def observers(
      self, instance: TaskInstance[Any]
  ) -> tuple[SandboxObserver, ...]:
    del instance
    return (_Emit(artifacts=self.makes, metrics=self.metrics),)

  @override
  def action(
      self, sb: SandboxFs, instance: TaskInstance[Any], *, timeout: float
  ) -> ExecResult:
    del instance
    for name in self.needs:
      self.seen[name] = sb.read(name)
    return sb.run_script("main.sh", timeout=timeout)

  @override
  def outputs_valid(self, result: AttemptResult) -> bool:
    if id(result) not in self.judged:
      self.judged.append(id(result))
    if self.judged.index(id(result)) < self.invalid_attempts:
      return False
    return super().outputs_valid(result)


def _solver(patch: bytes) -> _Step:
  return _Step(
      makes={
          CONVERSATION_NAME: b'{"messages": []}',
          PATCH_NAME: patch,
          BASE_REF_NAME: b"deadbeef\n",
      }
  )


def _grader(*, resolved: float, invalid_attempts: int = 0) -> _Step:
  return _Step(
      needs=(PATCH_NAME, BASE_REF_NAME),
      makes={VERDICT_ARTIFACT: b'{"resolved": true}'},
      metrics={"unit_test.resolved": resolved},
      invalid_attempts=invalid_attempts,
  )


def _oracle(*, invalid_attempts: int = 0) -> _Step:
  return _Step(
      needs=(CONVERSATION_NAME, PATCH_NAME, BASE_REF_NAME, VERDICT_ARTIFACT),
      makes={GUIDEBOOK_NAME: b"# Guidebook"},
      invalid_attempts=invalid_attempts,
  )


def _steps(
    *,
    baseline_grader: _Step,
    guided_grader: _Step,
    oracle: _Step | None = None,
) -> dict[str, _Step]:
  """Build the five fakes, keyed as the definition keys them."""
  return {
      definitions.BASELINE_ROLLOUT_KEY: _solver(b"BASELINE PATCH"),
      definitions.BASELINE_UNIT_TEST_KEY: baseline_grader,
      definitions.ORACLE_ANALYSIS_KEY: oracle or _oracle(),
      definitions.GUIDED_ROLLOUT_KEY: replace(
          _solver(b"GUIDED PATCH"), needs=(GUIDEBOOK_NAME,)
      ),
      definitions.GUIDED_UNIT_TEST_KEY: guided_grader,
  }


def _execute(
    store: FilesystemStore,
    steps: dict[str, _Step],
    *,
    output_dir: Path,
    run_ts: str,
    retries: dict[str, int] | None = None,
    resume: bool = True,
):
  """Run the five fakes under the shipped bindings, verbatim, over ``store``."""
  real = {entry.key: entry for entry in definitions.FROM_SCRATCH_GUIDED_TRACE}
  workflow = Workflow(
      store=store,
      sweep_id="sw",
      rollout_id=0,
      entries=[
          WorkflowEntry(
              key,
              task,
              timeout=10.0,
              sandbox=FakeSandboxConfig(),
              inputs=real[key].inputs,  # the shipped bindings, verbatim
              retries=(retries or {}).get(key, 0),
          )
          for key, task in steps.items()
      ],
  )
  return workflow.execute(
      _Underlying(), output_dir=output_dir, run_ts=run_ts, resume=resume
  )


def _reading(store: FilesystemStore):
  return guided_gain(
      store,
      sweep_id="sw",
      baseline_key=definitions.BASELINE_UNIT_TEST_KEY,
      guided_key=definitions.GUIDED_UNIT_TEST_KEY,
  )


def test_five_fake_steps_run_end_to_end_under_the_real_bindings(
    tmp_path: Path,
):
  """The definition's bindings, on tasks with its schemas, over a real store.

  The baseline grade *passes* here on purpose: the chain has no early exit,
  so every step still runs, and the guided grade must be read off the guided
  patch — which is what the explicit binding is for. Then the 2×2 reading is
  taken from the very records the run left.
  """
  steps = _steps(
      baseline_grader=_grader(resolved=1.0), guided_grader=_grader(resolved=0.0)
  )
  store = FilesystemStore(epath.Path(tmp_path / "store"))

  outcome = _execute(store, steps, output_dir=tmp_path / "out", run_ts="ts-0")

  assert outcome.succeeded is True
  assert [e.status for e in outcome.entries] == [EntryStatus.SUCCEEDED] * 5
  # each grader read its own rollout's patch — the second one the guided one
  baseline_grader = steps[definitions.BASELINE_UNIT_TEST_KEY]
  guided_grader = steps[definitions.GUIDED_UNIT_TEST_KEY]
  assert baseline_grader.seen[PATCH_NAME] == b"BASELINE PATCH"
  assert guided_grader.seen[PATCH_NAME] == b"GUIDED PATCH"
  # the Oracle read the blind pair
  oracle = steps[definitions.ORACLE_ANALYSIS_KEY]
  assert oracle.seen[PATCH_NAME] == b"BASELINE PATCH"
  assert oracle.seen[VERDICT_ARTIFACT] == b'{"resolved": true}'
  assert steps[definitions.GUIDED_ROLLOUT_KEY].seen[GUIDEBOOK_NAME] == (
      b"# Guidebook"
  )
  record = json.loads(store.get_bytes(outcome.record_key))
  assert record["edges"] == EXPECTED_EDGES
  # two `patch.diff`s in one run, under two task prefixes: the key is the
  # namespace, and nothing else had to be
  shards = store.read_manifests("sw")
  assert sorted(shard.task for shard in shards) == sorted(KEYS)
  patch_keys = {
      shard.task: shard.artifact_keys[PATCH_NAME]
      for shard in shards
      if PATCH_NAME in shard.artifact_keys
  }
  assert patch_keys == {
      "baseline_rollout": "sw/acme__widget-1/r0/baseline_rollout/a0/patch.diff",
      "guided_rollout": "sw/acme__widget-1/r0/guided_rollout/a0/patch.diff",
  }
  # …and the reading places this run in the cell its two grades put it in
  reading = _reading(store)
  assert [(run.cell, run.run_ts) for run in reading.runs] == [
      (Cell.REGRESSED, "ts-0")
  ]
  assert reading.incomplete == ()


# ─── re-runs: the store keeps what earlier invocations left ──────────────────


def test_a_forced_rerun_is_read_from_the_rerun_not_an_outlived_attempt(
    tmp_path: Path,
):
  """A forced re-run overwrites ``a0`` and leaves an older run's ``a1`` behind.

  ``swe_lab run`` defaults to ``resume=False`` against a T1 store that keeps
  every shard, so after a two-attempt grading followed by a one-attempt
  re-run the store holds the re-run's ``a0`` *and* the old run's ``a1``. The
  reading must take the re-run's verdict — the workflow record's — and not
  the highest attempt number on disk, which belongs to a run that no longer
  exists. Old run: baseline flaky-then-pass, guided pass. Re-run: both fail.
  Truth for the current run is ``unsolved``; the outlived ``a1`` would make it
  ``regressed``.
  """
  store = FilesystemStore(epath.Path(tmp_path / "store"))
  old = _steps(
      baseline_grader=_grader(resolved=1.0, invalid_attempts=1),
      guided_grader=_grader(resolved=1.0),
  )
  first = _execute(
      store,
      old,
      output_dir=tmp_path / "old",
      run_ts="20260906-010000",
      retries={definitions.BASELINE_UNIT_TEST_KEY: 1},
  )
  assert first.succeeded is True
  rerun = _steps(
      baseline_grader=_grader(resolved=0.0), guided_grader=_grader(resolved=0.0)
  )
  second = _execute(
      store,
      rerun,
      output_dir=tmp_path / "new",
      run_ts="20260906-020000",
      resume=False,
  )
  assert second.succeeded is True
  # the precondition the test is about: the outlived a1 really is still there
  shards = store.read_manifest(
      "sw", "acme__widget-1", 0, definitions.BASELINE_UNIT_TEST_KEY
  )
  assert [(s.attempt, s.run_ts) for s in shards] == [
      (0, "20260906-020000"),
      (1, "20260906-010000"),
  ]

  reading = _reading(store)

  assert [(run.cell, run.run_ts) for run in reading.runs] == [
      (Cell.UNSOLVED, "20260906-020000")
  ]
  assert reading.incomplete == ()


def test_a_rerun_that_stopped_early_is_not_completed_by_stale_shards(
    tmp_path: Path,
):
  """A re-run whose Oracle failed never graded a guided patch; the old one did.

  The store still holds the previous run's ``guided_unit_test/a0`` beside the
  re-run's fresh ``baseline_unit_test/a0``. Pairing them would grade a
  guidebook the current run never wrote. The current run is incomplete, and
  the reading must say so rather than borrow the stale half.
  """
  store = FilesystemStore(epath.Path(tmp_path / "store"))
  old = _steps(
      baseline_grader=_grader(resolved=1.0), guided_grader=_grader(resolved=1.0)
  )
  assert (
      _execute(
          store, old, output_dir=tmp_path / "old", run_ts="20260906-010000"
      ).succeeded
      is True
  )
  rerun = _steps(
      baseline_grader=_grader(resolved=0.0),
      guided_grader=_grader(resolved=1.0),  # never reached
      oracle=_oracle(invalid_attempts=1),
  )
  second = _execute(
      store,
      rerun,
      output_dir=tmp_path / "new",
      run_ts="20260906-020000",
      resume=False,
  )
  assert [e.status for e in second.entries] == [
      EntryStatus.SUCCEEDED,
      EntryStatus.SUCCEEDED,
      EntryStatus.FAILED,
      EntryStatus.BLOCKED,
      EntryStatus.BLOCKED,
  ]
  # the stale half is really in the store
  stale = store.read_manifest(
      "sw", "acme__widget-1", 0, definitions.GUIDED_UNIT_TEST_KEY
  )
  assert [s.run_ts for s in stale] == ["20260906-010000"]

  reading = _reading(store)

  assert reading.runs == ()
  assert reading.incomplete == (
      IncompleteRun(
          "acme__widget-1", 0, missing=(definitions.GUIDED_UNIT_TEST_KEY,)
      ),
  )
