"""The step judge must find its guidebook from any working directory.

`judge_steps.py` resolved the guidebook through a repo-root-relative literal,
so it ran only from the repo root — while the `Reproduce` block in its own
`REPORT.md` was written with bare `python3 judge_steps.py` invocations, which
imply the directory the script lives in. Copying the documented commands got a
`FileNotFoundError`, and every test stayed green because nothing imported the
module: it is a runnable instrument, not a library.

The `--help` check that first "verified" the fix is blind to it — argparse exits
before `main` reads the guidebook — so this reads the file instead, from a
directory outside the repo, which is the only place the old literal is wrong.

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
  # read_text, not exists: the old literal also *constructs* a Path fine.
  assert judge.GUIDEBOOK.read_text().startswith("# Guidebook")
