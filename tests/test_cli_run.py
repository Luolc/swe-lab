"""Tests for the ``run`` command (Typer CliRunner, on the fake backend).

Nothing about the run is mocked: the command resolves a registered workflow,
applies the invocation's overrides, and executes it — real edges, real store,
real observers. Only what reaches outside is stood in for: the dataset, and
the agent, which is **registered** here exactly as a downstream user would
register theirs (``--rollout.harness=stub``).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
import json
from pathlib import Path
import re
from typing import Any, final, override

import pytest
from typer.testing import CliRunner

from swe_lab.cli import app
import swe_lab.cli.run as run_mod
from swe_lab.conversation import Conversation
from swe_lab.datasets.instance import TaskInstance
from swe_lab.datasets.swebench_pro.unit_test import (
    REQUIRED_TESTS_NAME,
    SweBenchProGrader,
    SweBenchProVerdict,
)
from swe_lab.evaluation.unit_test import UnitTestTask
from swe_lab.evaluation.verdict import UnitTestSpec
from swe_lab.harnesses import (
    AgentOutcome,
    Harness,
    HarnessOutcomeObserver,
    register_harness,
)
from swe_lab.sandbox import (
    ExecResult,
    Inline,
    Mount,
    Mounts,
    SandboxFs,
    SandboxObserver,
    SandboxSpec,
)
from swe_lab.sandbox.observers import PATCH_NAME
from swe_lab.sandbox.observers.diff_extract import RAW_PATCH_NAME

# Importing the doubles registers the `fake` backend these runs build on.
from swe_lab.sandbox.testing import FakeSandboxConfig
from swe_lab.workflow import Workflow, WorkflowOutcome
from swe_lab.workflow.registry import workflow_definition

runner = CliRunner()
# Colour is escape sequences *between* characters, so it is stripped before a
# message is read (see `_message`).
_ANSI = re.compile("\x1b\\[[0-9;]*m")
_SPEC = SandboxSpec("acme__widget-1", "img:tag", "/app", "abc")
_INSTANCE_ID = "acme__widget-1"

# What the stub agent saw, per run. A registry builds a fresh harness for every
# invocation, so a test cannot hold the instance — it reads what it left here.
PROMPTS: list[str] = []
EDITS: list[bool] = [True]


@final
class _StubAgent(Harness):
  """A registered stand-in for an agent: records its prompt, leaves a diff."""

  @property
  @override
  def name(self) -> str:
    return "stub"

  @override
  def observers(self) -> tuple[SandboxObserver, ...]:
    return (HarnessOutcomeObserver(self),)

  @override
  def mounts(self, workdir: str) -> Mounts:
    del workdir
    return {"agent.sh": Mount(Inline(b"true\n"), executable=True)}

  @override
  def run(
      self,
      sb: SandboxFs,
      *,
      prompt: str,
      timeout: float,
      env: Mapping[str, str] | None = None,
  ) -> ExecResult:
    PROMPTS.append(prompt)
    if EDITS[0]:
      # What the agent's edits look like to the extraction observer.
      sb.write(RAW_PATCH_NAME, b"diff --git a/x b/x\n")
    return sb.run_script("agent.sh", timeout=timeout, env=env)

  @override
  def native_outputs(self) -> dict[str, str]:
    return {}

  @override
  def to_conversation(self, sb: SandboxFs) -> Conversation:
    del sb
    return Conversation(messages=[])

  @override
  def outcome(self, sb: SandboxFs) -> AgentOutcome:
    del sb
    return AgentOutcome.FINISHED


register_harness("stub", _StubAgent)


@final
class _Instance(TaskInstance[SweBenchProVerdict]):
  """A runnable instance with no concrete dataset behind it."""

  instance_id = _INSTANCE_ID

  def __init__(self, *, passed: list[str], gold: str | None) -> None:
    self._passed = passed
    self._gold = gold

  @override
  def sandbox_spec(self) -> SandboxSpec:
    return _SPEC

  @override
  def prompt(self) -> str:
    return "PROMPT: fix it"

  @override
  def gold_patch(self) -> str | None:
    return self._gold

  @override
  def unit_test_spec(
      self,
      *,
      apply_patch: bool,
      patch_name: str = PATCH_NAME,
      checkout_golden_tests: bool = True,
      patch_baseline: bool = False,
  ) -> UnitTestSpec[SweBenchProVerdict]:
    del apply_patch, checkout_golden_tests
    output = json.dumps(
        {"tests": [{"name": n, "status": "PASSED"} for n in self._passed]}
    )
    return UnitTestSpec(
        eval_script="echo eval\n",
        mounts={
            REQUIRED_TESTS_NAME: Mount(Inline(json.dumps(["a"]).encode())),
            "output.json": Mount(Inline(output.encode())),
        },
        grader=SweBenchProGrader(),
        patch_name=patch_name,
    )


@pytest.fixture(autouse=True)
def _reset() -> None:  # pyright: ignore[reportUnusedFunction]  # autouse
  """Each test starts with an agent that has said nothing and edits."""
  PROMPTS.clear()
  EDITS[0] = True


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    passed: list[str] | None = None,
    gold: str | None = "GOLD DIFF",
    patch_baseline: bool = True,
) -> None:
  """Point the command at a stand-in dataset and a throwaway repo root.

  ``patch_baseline=False`` grades against ``base_commit``, which leaves the
  unit-test workflow with a single unbound input — the shape the CLI's own
  input-binding rules are about. The shipped default declares two (the patch
  and the base it was taken against, ADR-0014).
  """
  instance = _Instance(
      passed=passed if passed is not None else ["a"], gold=gold
  )

  @final
  class _Dataset:

    def require(self, instance_id: str) -> _Instance:
      assert instance_id == _INSTANCE_ID
      return instance

  def fake_load_dataset(name: str) -> _Dataset:
    del name
    return _Dataset()

  # Entries declare where they run, so placing the run on the fake backend
  # means stamping its config onto the definition the command looks up.
  real_definition = workflow_definition

  def on_fake(name: str):
    return tuple(
        replace(
            entry,
            sandbox=FakeSandboxConfig(),
            task=(
                entry.task
                if patch_baseline or not isinstance(entry.task, UnitTestTask)
                else replace(entry.task, patch_baseline=False)
            ),
        )
        for entry in real_definition(name)
    )

  monkeypatch.setattr(run_mod, "load_dataset", fake_load_dataset)
  monkeypatch.setattr(run_mod, "find_repo_root", lambda: tmp_path)
  monkeypatch.setattr(run_mod, "workflow_definition", on_fake)


def _run(*args: str):
  return runner.invoke(app, ["run", *args])


def _message(output: str) -> str:
  """Return an error's text, free of the panel it was rendered in.

  Click draws a refusal inside a Rich box: it **wraps** the message to the
  terminal, draws borders between the lines, and — where colour is on, as in
  CI but not in a local capture — highlights fragments, which puts escape
  sequences *inside* words (``--input`` arrives as two coloured runs). A test
  that reads the message has to undo all of it, or it passes on one machine
  and fails on another for reasons that have nothing to do with the message.
  Both halves of that were found by CI rather than here.
  """
  plain = _ANSI.sub("", output)
  bordered = "".join(" " if char in "│╭╮╰╯─" else char for char in plain)
  return " ".join(bordered.split()).replace("- -", "--")


# ─── discovery ───────────────────────────────────────────────────────────────


def test_list_names_the_workflows_and_their_entries():
  result = runner.invoke(app, ["run", "--list"])
  assert result.exit_code == 0
  assert "rollout_and_unit_test: rollout, unit_test" in result.output
  assert "gold_unit_test: unit_test" in result.output


def test_an_unknown_workflow_lists_the_registered_ones(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  _wire(monkeypatch, tmp_path)
  result = _run("nope", _INSTANCE_ID)
  assert result.exit_code != 0
  assert "unknown workflow" in _message(result.output)


# ─── running ─────────────────────────────────────────────────────────────────


def test_the_chain_runs_and_grades_what_the_agent_produced(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  _wire(monkeypatch, tmp_path)
  result = _run("rollout_and_unit_test", _INSTANCE_ID, "--rollout.harness=stub")
  assert result.exit_code == run_mod.ExitCode.OK
  payload = json.loads(result.output)
  assert payload["succeeded"] is True
  assert [entry["key"] for entry in payload["entries"]] == [
      "rollout",
      "unit_test",
  ]
  # the metrics carry the answer, so the command needs no verdict knowledge
  assert payload["entries"][1]["metrics"]["unit_test.resolved"] == 1.0
  # the instance's own prompt reached the agent, through its declared input
  assert PROMPTS == ["PROMPT: fix it"]
  # …and the eval really got the agent's patch through the edge
  staged = (
      tmp_path
      / ".cache"
      / "runs"
      / "rollout_and_unit_test"
      / _INSTANCE_ID
      / "r0"
      / "unit_test"
      / "ws"
      / "a0"
      / PATCH_NAME
  )
  assert staged.read_text() == "diff --git a/x b/x\n"


def test_an_unresolved_grade_is_its_own_exit_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  # "Did it run" and "did the patch pass" are different questions: the run
  # succeeded, and the answer was no.
  _wire(monkeypatch, tmp_path, passed=[])
  result = _run(
      "rollout_and_unit_test",
      _INSTANCE_ID,
      "--rollout.harness=stub",
      "--unit_test.retries=0",
  )
  assert result.exit_code == run_mod.ExitCode.UNRESOLVED
  assert json.loads(result.output)["succeeded"] is True


def test_an_empty_patch_fails_the_run_at_the_edge(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  _wire(monkeypatch, tmp_path)
  EDITS[0] = False
  result = _run("rollout_and_unit_test", _INSTANCE_ID, "--rollout.harness=stub")
  assert result.exit_code == run_mod.ExitCode.FAILED
  payload = json.loads(result.output)
  assert payload["succeeded"] is False
  evaluation = payload["entries"][1]
  assert evaluation["status"] == "edge_failed"
  assert evaluation["missing_inputs"] == [PATCH_NAME]


# ─── inputs ──────────────────────────────────────────────────────────────────


def test_a_workflow_that_needs_an_input_says_which_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  _wire(monkeypatch, tmp_path)
  result = _run("unit_test", _INSTANCE_ID)
  assert result.exit_code != 0
  # One line, carrying the input's name, the schema's own description, and
  # the flag that supplies it.
  message = _message(result.output)
  assert "needs an input you did not supply" in message
  assert "the candidate patch to grade" in message
  assert "--input" in message


def test_one_unbound_input_needs_no_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  _wire(monkeypatch, tmp_path, patch_baseline=False)
  candidate = tmp_path / "cand.diff"
  _ = candidate.write_text("CANDIDATE")
  result = _run("unit_test", _INSTANCE_ID, "--input", str(candidate))
  assert result.exit_code == run_mod.ExitCode.OK
  staged = (
      tmp_path
      / ".cache"
      / "runs"
      / "unit_test"
      / _INSTANCE_ID
      / "r0"
      / "unit_test"
      / "ws"
      / "a0"
      / PATCH_NAME
  )
  assert staged.read_text() == "CANDIDATE"


def test_an_input_may_be_named_and_a_missing_file_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  _wire(monkeypatch, tmp_path, patch_baseline=False)
  candidate = tmp_path / "cand.diff"
  _ = candidate.write_text("CANDIDATE")
  named = _run(
      "unit_test", _INSTANCE_ID, "--input", f"{PATCH_NAME}={candidate}"
  )
  assert named.exit_code == run_mod.ExitCode.OK
  missing = _run("unit_test", _INSTANCE_ID, "--input", "./nowhere.diff")
  assert missing.exit_code != 0
  assert "is not a file" in _message(missing.output)


def test_gold_grading_is_a_workflow_not_a_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  # The definition builds its own patch from the instance, so it runs from a
  # name alone — no --input, no --gold.
  _wire(monkeypatch, tmp_path)
  result = _run("gold_unit_test", _INSTANCE_ID)
  assert result.exit_code == run_mod.ExitCode.OK
  staged = (
      tmp_path
      / ".cache"
      / "runs"
      / "gold_unit_test"
      / _INSTANCE_ID
      / "r0"
      / "unit_test"
      / "ws"
      / "a0"
      / PATCH_NAME
  )
  assert staged.read_text() == "GOLD DIFF"


# ─── overrides, end to end ───────────────────────────────────────────────────


def test_an_override_reaches_the_run_and_a_bad_one_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  _wire(monkeypatch, tmp_path, passed=[])
  # retries is the entry's, and spending it is visible in the record
  result = _run("gold_unit_test", _INSTANCE_ID, "--unit_test.retries=1")
  assert result.exit_code == run_mod.ExitCode.UNRESOLVED
  assert json.loads(result.output)["entries"][0]["attempts"] == 2

  refused = _run("gold_unit_test", _INSTANCE_ID, "--unit_test.retires=1")
  assert refused.exit_code != 0
  assert "not a field of" in _message(refused.output)


def test_persisting_writes_the_run_under_its_sweep(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  _wire(monkeypatch, tmp_path)
  result = _run("gold_unit_test", _INSTANCE_ID, "--persist", "--sweep", "sw1")
  assert result.exit_code == run_mod.ExitCode.OK
  runs = tmp_path / ".cache" / "store" / "runs" / "sw1" / _INSTANCE_ID / "r0"
  assert (runs / "unit_test" / "complete.json").is_file()
  assert (runs / "workflow.json").is_file()
  payload = json.loads(result.output)
  assert payload["record_key"] == f"sw1/{_INSTANCE_ID}/r0/workflow.json"
  assert "artifacts" in payload["entries"][0]


def test_effort_is_overridable_and_a_typo_is_refused():
  # The sweep-facing path. The agent only WARNS on an unknown effort and runs
  # at its default, so the CLI has to be the thing that refuses one — and it
  # should say what is allowed rather than just "no".
  from swe_lab.cli.overrides import (
      apply_overrides,
      OverrideError,
      parse_overrides,
  )
  from swe_lab.harnesses.claude_code import ClaudeCodeHarness
  from swe_lab.rollout import CodingAgentTask
  import swe_lab.workflow.definitions as definitions
  from swe_lab.workflow.registry import workflow_definition

  del definitions  # imported for its registration side effect
  entries = workflow_definition("rollout")
  applied = apply_overrides(
      entries, parse_overrides(["--rollout.harness.effort=xhigh"])
  )
  task = applied[0].task
  assert isinstance(task, CodingAgentTask)
  harness = task.harness
  assert isinstance(harness, ClaudeCodeHarness)
  assert harness.effort == "xhigh"

  with pytest.raises(OverrideError, match="low, medium, high, xhigh, max"):
    _ = apply_overrides(
        entries, parse_overrides(["--rollout.harness.effort=ultra"])
    )


# ─── a reported record has to be there ───────────────────────────────────────


def test_two_rollouts_of_one_instance_both_keep_their_records(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  """`--rollout-id` distinguishes samples, so one must not erase the other.

  The scratch store lives under the run's own directory and a non-resumed run
  wipes that directory — correct for a re-run, wrong across rollouts of one
  instance, which are distinct samples rather than a repeat.
  """
  _wire(monkeypatch, tmp_path)
  for rollout in ("0", "1"):
    result = _run(
        "gold_unit_test",
        _INSTANCE_ID,
        "--sweep",
        "sw1",
        "--rollout-id",
        rollout,
    )
    assert result.exit_code == run_mod.ExitCode.OK

  runs = tmp_path / ".cache" / "runs" / "gold_unit_test" / _INSTANCE_ID
  for rollout in ("r0", "r1"):
    record = (
        runs
        / rollout
        / "store"
        / "sw1"
        / _INSTANCE_ID
        / rollout
        / "workflow.json"
    )
    assert record.is_file(), f"{rollout} lost its record to the other run"


def test_a_record_that_cannot_be_read_back_fails_the_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  """Reporting a key nothing can read is a failure, not a success.

  The claim is checked rather than its causes: a wipe, a failed write, a
  mis-joined key and a full disk all produce the same successful-looking
  summary naming a key with nothing under it.

  The missing record here is **real** — the run is made to report a key that
  was never written, so the store raises its own documented error from the
  production path. Faking the exception instead would test a shape the store
  never actually produces.
  """
  _wire(monkeypatch, tmp_path)
  real_execute = Workflow.execute

  def _misreport(self: Workflow, *args: Any, **kwargs: Any) -> WorkflowOutcome:
    outcome = real_execute(self, *args, **kwargs)
    return replace(outcome, record_key="sw1/nobody/r0/workflow.json")

  monkeypatch.setattr(Workflow, "execute", _misreport)
  result = _run("gold_unit_test", _INSTANCE_ID)

  assert result.exit_code == run_mod.ExitCode.FAILED
  # Named, because an unhandled error also exits non-zero with no summary:
  # without this the test passes whether the guard caught the store's error or
  # never ran at all. (Verified by reverting the guard's except clause.)
  assert "cannot be read back" in result.output + (result.stderr or "")
  # …and nothing printed a success claim on the way out.
  assert '"succeeded": true' not in result.output
