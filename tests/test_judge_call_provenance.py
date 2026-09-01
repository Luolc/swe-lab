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
