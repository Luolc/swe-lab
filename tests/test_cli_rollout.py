"""Tests for the rollout CLI wiring (Typer CliRunner, on the fake backend).

The command's own machinery is real here — the workflow, the edge from the
agent's patch to the grading task, the store — and only what reaches outside
is stood in for: the dataset, and the agent (a stub harness, so no Claude Code
binary is provisioned and no process is spawned). Sandboxes come from the
registered ``fake`` backend, whose file operations are real over a local
directory.
"""

from collections.abc import Mapping
import contextlib
import json
from pathlib import Path
from typing import final, override

import pytest
from typer.testing import CliRunner

from swe_lab.cli import app
import swe_lab.cli.rollout as rollout_mod
from swe_lab.conversation import Conversation
from swe_lab.datasets.instance import TaskInstance
from swe_lab.datasets.swebench_pro.unit_test import (
    REQUIRED_TESTS_NAME,
    SweBenchProGrader,
    SweBenchProVerdict,
)
from swe_lab.evaluation.verdict import UnitTestSpec
from swe_lab.harnesses import Harness, HarnessOutcomeObserver
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

# Importing the doubles registers the `fake` backend the CLI runs on here.
from swe_lab.sandbox.testing import FakeSandboxConfig

runner = CliRunner()
TOKEN = "CLAUDE_CODE_OAUTH_TOKEN"
API_KEY = "ANTHROPIC_API_KEY"
_SPEC = SandboxSpec("acme__widget-1", "img:tag", "/app", "abc")


@final
class _Instance(TaskInstance[SweBenchProVerdict]):
  """A runnable instance with no concrete dataset behind it."""

  instance_id = "acme__widget-1"
  problem_statement = "fix it"

  def __init__(self, *, passed: list[str]) -> None:
    self._passed = passed

  @override
  def sandbox_spec(self) -> SandboxSpec:
    return _SPEC

  @override
  def prompt(self) -> str:
    return f"PROMPT: {self.problem_statement}"

  @override
  def gold_patch(self) -> str:
    return "GOLD"

  @override
  def unit_test_spec(
      self,
      *,
      apply_patch: bool,
      patch_name: str = PATCH_NAME,
      checkout_golden_tests: bool = True,
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


@final
class _StubAgent(Harness):
  """Stands in for the agent: records its prompt, leaves a diff behind."""

  def __init__(self, *, edits: bool, prompts: list[str]) -> None:
    self._edits = edits
    self.prompts = prompts

  @property
  @override
  def name(self) -> str:
    return "stub"

  @override
  def observers(self) -> tuple[SandboxObserver, ...]:
    # The completion signal is the outcome observer's to report — a harness
    # picks its own (ADR-0007 §3), and this one wants that half.
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
    self.prompts.append(prompt)
    if self._edits:
      # What the agent's edits look like to the extraction observer: the raw
      # diff its script would have produced (the fake backend runs no script).
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
  def completed(self, sb: SandboxFs) -> bool:
    del sb
    return True


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    edits: bool = True,
    passed: list[str] | None = None,
) -> dict[str, object]:
  """Stand in for the dataset and the agent; keep everything else real."""
  calls: dict[str, object] = {"prompts": []}
  config = FakeSandboxConfig()
  calls["config"] = config

  @final
  class _Dataset:

    def require(self, instance_id: str) -> _Instance:
      calls["required"] = instance_id
      return _Instance(passed=passed if passed is not None else ["a"])

  def fake_build_agent(
      instance_id: str,
      *,
      model: str,
      capture: object,
      bare: bool,
      proxy_log_dir: object,
  ) -> tuple[Harness, contextlib.AbstractContextManager[object]]:
    del instance_id, proxy_log_dir
    calls["model"] = model
    calls["capture"] = capture
    calls["bare"] = bare
    prompts = calls["prompts"]
    assert isinstance(prompts, list)
    return _StubAgent(edits=edits, prompts=prompts), contextlib.nullcontext()

  monkeypatch.setenv(TOKEN, "tok")

  def fake_load_dataset(name: str) -> _Dataset:
    del name
    return _Dataset()

  def fake_invocation_config(
      backend: str, **kwargs: object
  ) -> FakeSandboxConfig:
    del backend, kwargs
    return config

  monkeypatch.setattr(rollout_mod, "load_dataset", fake_load_dataset)
  monkeypatch.setattr(rollout_mod, "find_repo_root", lambda: tmp_path)
  monkeypatch.setattr(rollout_mod, "_build_agent", fake_build_agent)
  monkeypatch.setattr(rollout_mod, "invocation_config", fake_invocation_config)
  return calls


def test_help_lists_rollout():
  top = runner.invoke(app, ["--help"])
  assert top.exit_code == 0
  assert "rollout" in top.output  # the subcommand is listed
  sub = runner.invoke(app, ["rollout", "--help"])
  assert sub.exit_code == 0


def test_requires_oauth_token(monkeypatch: pytest.MonkeyPatch):
  monkeypatch.delenv(TOKEN, raising=False)
  result = runner.invoke(app, ["rollout", "some-id"])
  assert result.exit_code != 0
  assert "not set" in result.output


def test_solve_not_graded_exits_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  calls = _wire(monkeypatch, tmp_path)
  result = runner.invoke(
      app, ["rollout", "acme__widget-1", "--backend", "fake"]
  )
  assert result.exit_code == 0
  payload = json.loads(result.output)
  assert payload["outcome"] == "solved_not_graded"
  assert payload["is_empty_patch"] is False
  assert payload["agent_complete"] is True
  # the dataset's prompt reached the agent — as the task's declared input,
  # built from the instance and read back out of the workspace
  assert calls["prompts"] == ["PROMPT: fix it"]
  assert calls["bare"] is False
  # non-bare passes the OAuth token by reference: the entry declared it, and
  # the runner merged that onto the invocation's config
  config = calls["config"]
  assert isinstance(config, FakeSandboxConfig)
  built = config.built[0].config
  assert isinstance(built, FakeSandboxConfig)
  assert built.pass_env == (TOKEN,)


def test_bare_requires_api_key_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  _ = _wire(monkeypatch, tmp_path)
  monkeypatch.delenv(API_KEY, raising=False)
  result = runner.invoke(app, ["rollout", "acme__widget-1", "--bare"])
  assert result.exit_code != 0
  assert API_KEY in result.output


def test_bare_passes_api_key_env_by_reference(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  calls = _wire(monkeypatch, tmp_path)
  monkeypatch.setenv(API_KEY, "sk-x")
  result = runner.invoke(
      app, ["rollout", "acme__widget-1", "--bare", "--backend", "fake"]
  )
  assert result.exit_code == 0
  assert calls["bare"] is True
  config = calls["config"]
  assert isinstance(config, FakeSandboxConfig)
  built = config.built[0].config
  assert isinstance(built, FakeSandboxConfig)
  # the key is passed by NAME (like the OAuth token), never its value
  assert built.pass_env == (API_KEY,)


def test_persist_writes_a_manifest_shard(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  _ = _wire(monkeypatch, tmp_path)
  result = runner.invoke(
      app,
      [
          "rollout",
          "acme__widget-1",
          "--backend",
          "fake",
          "--persist",
          "--sweep",
          "sw1",
      ],
  )
  assert result.exit_code == 0
  shards = list((tmp_path / ".cache" / "store" / "runs").rglob("run.json"))
  assert len(shards) == 1  # one per-run shard under the sweep
  assert (tmp_path / ".cache" / "store" / "runs" / "sw1").is_dir()
  assert "persisted" in json.loads(result.output)


def test_no_persist_keeps_the_shared_store_clean(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  _ = _wire(monkeypatch, tmp_path)
  result = runner.invoke(
      app, ["rollout", "acme__widget-1", "--backend", "fake"]
  )
  assert result.exit_code == 0
  assert not (tmp_path / ".cache" / "store" / "runs").exists()
  assert "persisted" not in json.loads(result.output)


def test_grade_chains_the_agents_patch_into_the_eval(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  # The whole point of the chain: the eval never sees the CLI's bytes, it
  # consumes what the rollout persisted, matched by store name.
  _ = _wire(monkeypatch, tmp_path, passed=["a"])
  result = runner.invoke(
      app,
      [
          "rollout",
          "acme__widget-1",
          "--grade",
          "--backend",
          "fake",
          "--persist",
      ],
  )
  assert result.exit_code == 0
  payload = json.loads(result.output)
  assert payload["outcome"] == "resolved"
  assert payload["grade"]["resolved"] is True
  assert payload["grade"]["attempts"] == 1
  # both entries persisted under their own task keys
  runs = tmp_path / ".cache" / "store" / "runs" / "adhoc" / "acme__widget-1"
  assert (runs / "r0" / "rollout" / "a0" / PATCH_NAME).is_file()
  assert (runs / "r0" / "eval" / "complete.json").is_file()
  assert (runs / "r0" / "workflow.json").is_file()
  # and the eval's container really got the agent's patch through the edge
  staged = (
      tmp_path
      / ".cache"
      / "rollout_workspaces"
      / "acme__widget-1"
      / "eval"
      / "ws"
      / "a0"
      / PATCH_NAME
  )
  assert staged.read_text() == "diff --git a/x b/x\n"


def test_empty_patch_graded_exits_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  # An agent that changed nothing never reaches a grading container: the edge
  # refuses empty bytes, which is the same answer one container cheaper.
  _ = _wire(monkeypatch, tmp_path, edits=False)
  result = runner.invoke(
      app, ["rollout", "acme__widget-1", "--grade", "--backend", "fake"]
  )
  assert result.exit_code == 1
  payload = json.loads(result.output)
  assert payload["outcome"] == "empty_patch"  # never grades as a pass
  assert payload["grade"]["reason"] == "empty_patch"
