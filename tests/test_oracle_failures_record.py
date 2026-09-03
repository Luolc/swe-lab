"""The oracle-failure record: parsing, delegation, and the failure mounts."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, override

import polars as pl
import pytest

from swe_lab.conversation import Conversation, Message, Role, TextBlock
from swe_lab.datasets.instance import TaskInstance
from swe_lab.datasets.loader import load_dataset
from swe_lab.datasets.oracle_failures import COLUMNS, OracleFailureInstance
import swe_lab.datasets.oracle_failures.record as record_module
from swe_lab.datasets.swebench_pro.unit_test import (
    REQUIRED_TESTS_NAME,
    SweBenchProGrader,
    SweBenchProVerdict,
)
from swe_lab.evaluation.verdict import UnitTestSpec
from swe_lab.sandbox import Inline, Mount, Mounts, SandboxSpec
from swe_lab.sandbox.observers import BASE_REF_NAME, PATCH_NAME
from swe_lab.trace_synthesis.sample import (
    FAILED_CONVERSATION_NAME,
    FAILED_PATCH_NAME,
    FAILED_VERDICT_NAME,
)

SPEC = SandboxSpec("acme__widget-1", "acme/widget:tag", "/app", "abc123")

CONVERSATION = Conversation(
    messages=[
        Message(role=Role.USER, content=[TextBlock(text="SOLVE THIS")]),
        Message(role=Role.ASSISTANT, content=[TextBlock(text="I tried.")]),
    ]
)


@dataclass(frozen=True)
class _Underlying(TaskInstance[SweBenchProVerdict]):
  """The instance a failure record delegates to: static, no network."""

  instance_id: str = "acme__widget-1"
  extra_mounts: Mounts = field(default_factory=dict)
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


def _row(**overrides: Any) -> dict[str, Any]:
  row: dict[str, Any] = {
      "dataset": "fake",
      "instance_id": "acme__widget-1",
      "rollout_id": 3,
      "conversation": CONVERSATION.model_dump_json(),
      "verdict": json.dumps(
          {"resolved": False, "summary": {"missing": ["t::b"]}}
      ),
      "patch": "diff --git a/x b/x\n",
      "provenance": json.dumps({"sweep_id": "adhoc"}),
  }
  row.update(overrides)
  return row


def resolve_to(
    monkeypatch: pytest.MonkeyPatch, instance: TaskInstance[Any]
) -> None:
  """Make every delegation resolve to ``instance``, no parquet involved."""

  def _resolve(dataset: str, instance_id: str) -> TaskInstance[Any]:
    del dataset, instance_id
    return instance

  monkeypatch.setattr(record_module, "underlying_instance", _resolve)


@pytest.fixture
def underlying(monkeypatch: pytest.MonkeyPatch) -> _Underlying:
  instance = _Underlying()
  resolve_to(monkeypatch, instance)
  return instance


def test_from_raw_parses_the_row_and_binds_the_underlying_instance(
    underlying: _Underlying,
):
  record = OracleFailureInstance.from_raw(_row())
  assert record.instance is underlying
  assert record.instance_id == "acme__widget-1"
  assert record.dataset == "fake"
  assert record.rollout_id == 3
  assert record.patch == "diff --git a/x b/x\n"
  assert OracleFailureInstance.COLUMNS is COLUMNS


@pytest.mark.usefixtures("underlying")
def test_a_row_whose_conversation_is_not_typed_is_refused_at_load():
  # …and refused without quoting the column: a parser's own message embeds
  # the input it rejected, which for this column is the whole conversation.
  marker = "UNIQUE-CONTENT-MARKER-91c2"
  with pytest.raises(ValueError, match="not a typed Conversation") as info:
    _ = OracleFailureInstance.from_raw(
        _row(
            conversation=json.dumps(
                {"messages": [{"role": "nobody", "content": marker}]}
            )
        )
    )
  assert marker not in str(info.value)
  assert "messages/0/role (enum)" in str(info.value)


def test_the_runnable_surface_is_the_underlying_instances(
    underlying: _Underlying,
):
  # Delegation, not a copy: the compile contract lives in the underlying
  # record and this one forwards every call to it unchanged.
  record = OracleFailureInstance.from_raw(_row())
  assert record.sandbox_spec() == SPEC
  assert record.prompt() == underlying.prompt()
  assert record.gold_patch() == underlying.gold_patch()
  assert list(record.required_tests()) == ["t::a", "t::b"]
  assert record.solution_sha() == "f" * 40
  spec = record.unit_test_spec(apply_patch=True, patch_name="cand.diff")
  assert spec.eval_script.startswith("git apply cand.diff")
  assert spec.patch_name == "cand.diff"


def test_mounts_add_the_failure_beside_the_underlying_material(
    monkeypatch: pytest.MonkeyPatch,
):
  own = {"fixture.txt": Mount(Inline(b"theirs"))}
  resolve_to(monkeypatch, _Underlying(extra_mounts=own))
  mounts = OracleFailureInstance.from_raw(_row()).mounts()

  assert set(mounts) == {
      "fixture.txt",
      FAILED_CONVERSATION_NAME,
      FAILED_VERDICT_NAME,
      FAILED_PATCH_NAME,
  }
  assert mounts["fixture.txt"] is own["fixture.txt"]
  for name in (
      FAILED_CONVERSATION_NAME,
      FAILED_VERDICT_NAME,
      FAILED_PATCH_NAME,
  ):
    assert mounts[name].read_only is True
  staged = mounts[FAILED_CONVERSATION_NAME].resource
  assert isinstance(staged, Inline)
  assert Conversation.model_validate_json(staged.content) == CONVERSATION
  patch = mounts[FAILED_PATCH_NAME].resource
  assert isinstance(patch, Inline) and patch.content == b"diff --git a/x b/x\n"


def test_a_baseline_failure_stages_its_ref_and_rebuilds_its_grading_contract(
    underlying: _Underlying,
):
  del underlying
  row = _row(
      provenance=json.dumps(
          {
              "source": {"patch_base_ref": "b" * 40},
              "sweep_id": "adhoc",
          }
      )
  )

  record = OracleFailureInstance.from_raw(row)
  mounts = record.mounts()
  spec = record.unit_test_spec(apply_patch=True, patch_name=FAILED_PATCH_NAME)

  assert record.patch_base_ref == "b" * 40
  base_ref = mounts[BASE_REF_NAME].resource
  assert isinstance(base_ref, Inline)
  assert base_ref.content == ("b" * 40).encode()
  assert "patch-baseline=True" in spec.eval_script


def test_an_older_failure_row_keeps_the_base_commit_grading_contract(
    underlying: _Underlying,
):
  del underlying
  record = OracleFailureInstance.from_raw(_row())

  spec = record.unit_test_spec(apply_patch=True, patch_name=FAILED_PATCH_NAME)

  assert record.patch_base_ref is None
  assert BASE_REF_NAME not in record.mounts()
  assert "patch-baseline=False" in spec.eval_script


@pytest.mark.usefixtures("underlying")
def test_run_provenance_names_this_dataset_and_the_source():
  record = OracleFailureInstance.from_raw(_row())
  assert record.run_provenance() == {
      "language": "python",
      "dataset": "oracle_failures",
      "source_dataset": "fake",
      "failed_rollout_id": 3,
  }


def test_the_dataset_loads_by_name_from_the_standard_layout(
    tmp_path: Path, underlying: _Underlying
):
  root = tmp_path / "datasets"
  data = root / "oracle_failures" / "data"
  data.mkdir(parents=True)
  pl.DataFrame([_row()]).write_parquet(data / "oracle_failures.parquet")

  dataset = load_dataset("oracle_failures", root=root)

  assert dataset.name == "oracle_failures"
  record = dataset.require("acme__widget-1")
  assert isinstance(record, OracleFailureInstance)
  assert isinstance(record, TaskInstance)
  assert record.instance is underlying
