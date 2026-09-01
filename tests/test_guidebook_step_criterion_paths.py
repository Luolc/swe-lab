"""The step judge must find its guidebook from any working directory.

`judge_steps.py` is a runnable instrument that nothing imports, so a
working-directory-dependent path inside it fails *silently*: it stays green
under every check that never runs it from somewhere else, and breaks only for
whoever follows the commands in its own `REPORT.md`.

So the guidebook is *read*, with the working directory outside the repo. That is
the only place such a path is wrong, and anything short of reading the file
passes against one.

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


def _load(name: str) -> ModuleType:
  """Import the judge by path.

  Args:
    name: Module name to register the import under.

  Returns:
    The executed module.
  """
  spec = importlib.util.spec_from_file_location(name, _JUDGE)
  assert spec is not None and spec.loader is not None
  module = importlib.util.module_from_spec(spec)
  sys.modules[spec.name] = module
  try:
    spec.loader.exec_module(module)
  finally:
    del sys.modules[spec.name]
  return module


def test_the_judge_reads_its_guidebook_from_an_unrelated_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  """The guidebook is readable with the working directory outside the repo."""
  monkeypatch.chdir(tmp_path)
  judge = _load("guidebook_step_judge")
  # read_text, not exists: a wrong relative path also *constructs* fine.
  assert judge.GUIDEBOOK.read_text().startswith("# Guidebook")
