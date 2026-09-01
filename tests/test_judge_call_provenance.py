"""A judged call must record which model answered and how it was sampled.

Two questions went unanswerable about an earlier run because neither was
recorded: whether the alias sent resolved to the same served model, and what
sampling the provider defaulted to. The alias in the request is not evidence of
the first -- an alias re-pointed upstream still looks correct in what we sent --
and a parameter left unset is invisible unless absence is written down as
absence.

Loaded by path like `test_steered_rerun_driver.py`: `experiments/` is exempt
from the code-quality hooks and is not an importable package.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import ModuleType

import pytest

_JUDGE = (
    Path(__file__).resolve().parents[1]
    / "experiments/trace_synthesis/process_supervision"
    / "guidebook_as_step_criterion/judge_steps.py"
)


@pytest.fixture(scope="module")
def judge() -> ModuleType:
  """Import the judge by path.

  Returns:
    The executed module.
  """
  spec = importlib.util.spec_from_file_location("judge_provenance", _JUDGE)
  assert spec is not None and spec.loader is not None
  module = importlib.util.module_from_spec(spec)
  sys.modules[spec.name] = module
  try:
    spec.loader.exec_module(module)
  finally:
    del sys.modules[spec.name]
  return module


def test_an_unset_sampling_parameter_is_recorded_as_unset(judge: ModuleType):
  """Every sampling key appears, so a default is readable as *not sent*."""
  sent = judge.sampling_sent({"model": "m", "max_tokens": 700, "messages": []})
  assert set(sent) == set(judge.SAMPLING_KEYS)
  assert all(value is None for value in sent.values())


def test_a_sent_sampling_parameter_is_recorded_with_its_value(
    judge: ModuleType,
):
  """A pinned parameter is distinguishable from an unpinned one."""
  sent = judge.sampling_sent({"model": "m", "temperature": 0})
  assert sent["temperature"] == 0
  assert sent["top_p"] is None


def test_the_pinned_judge_input_digest_matches_the_pre_registration():
  """The digest the scripts enforce is the one the document fixed."""
  root = Path(__file__).resolve().parents[1]
  witness_dir = (
      root
      / "experiments/trace_synthesis/process_supervision"
      / "reject_then_accept_witness"
  )
  document = (witness_dir / "PRE-REGISTRATION.md").read_text()
  for script in ("witness.py", "judge_variance.py"):
    source = (witness_dir / script).read_text()
    digest = source.split('_JUDGE_INPUT_SHA256 = (\n    "')[1].split('"')[0]
    assert digest in document, script


_VARIANCE = (
    Path(__file__).resolve().parents[1]
    / "experiments/trace_synthesis/process_supervision"
    / "reject_then_accept_witness/judge_variance.py"
)


def test_the_variance_runner_voids_before_any_judge_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  """A judge input that does not match the pinned digest spends nothing."""
  import json

  spec = importlib.util.spec_from_file_location("variance_runner", _VARIANCE)
  assert spec is not None and spec.loader is not None
  variance = importlib.util.module_from_spec(spec)
  sys.modules[spec.name] = variance
  try:
    spec.loader.exec_module(variance)
  finally:
    del sys.modules[spec.name]

  # Synthetic material, so the judge input cannot match the pinned digest.
  capture = tmp_path / "capture.jsonl"
  row = {
      "request": {"body": {"model": "m", "tools": [{"name": "t"}]}},
      "response": {
          "message": {"content": [{"type": "text", "text": "x"}]},
          "headers": {},
      },
  }
  _ = capture.write_text(
      "".join(json.dumps(row) + "\n" for _ in range(variance._STEP_INDEX + 1))
  )
  monkeypatch.setattr(variance, "_CAPTURE", capture)

  def _steps(_rollout: str) -> list[dict[str, object]]:
    return [
        {"content": "c", "step_index": i, "tool_names": []} for i in range(37)
    ]

  def _forbidden(*_args: object, **_kwargs: object) -> object:
    raise AssertionError("a judge call was issued")

  monkeypatch.setattr(variance._extract, "extract", _steps)
  monkeypatch.setattr(variance._judge, "call", _forbidden)
  out = tmp_path / "out"
  monkeypatch.setattr(sys, "argv", ["judge_variance.py", "--out-dir", str(out)])

  with pytest.raises(SystemExit) as raised:
    variance.main()
  assert "void" in str(raised.value)
  recorded = json.loads((out / "judge_input.json").read_text())
  assert recorded["classification"] == "void"
  assert (
      recorded["judge_input_sha256_observed"]
      != recorded["judge_input_sha256_expected"]
  )
  assert not (out / "judgements.jsonl").exists()
