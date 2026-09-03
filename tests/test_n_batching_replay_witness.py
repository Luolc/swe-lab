"""The `N`-batching replay's committed witness, and the driver that made it.

Two things about
`experiments/trace_synthesis/n_batching_replay/` are asserted here rather than
inside the experiment, because both failures are silent and the artifacts are
committed:

- **No committed artifact carries an operator home path.** The corpus is read
  from under `$HOME`, so a manifest is the natural place for one to appear, and
  `docs/conventions.md` forbids operator PII in any committed record. This ran
  red on the first version of that experiment: all 16 manifests recorded the
  absolute path.
- **The experiment's driver reproduces the shipped `Supervisor`.** The report's
  numbers come from the driver, not from `Supervisor`, so the equivalence is
  what makes them evidence about the shipped component. The experiment's own
  `self-check` claimed this while never invoking the driver, so a driver that
  raised on every call still printed OK.

The driver lives under `experiments/`, which is exempt from the code-quality
hooks and is not an importable package, so it is loaded by path.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import ModuleType

import pytest

_DRIVER = (
    Path(__file__).resolve().parents[1]
    / "experiments/trace_synthesis/n_batching_replay/replay.py"
)


def _load_driver() -> ModuleType:
  """Import the experiment driver by path.

  Returns:
    The loaded module.
  """
  spec = importlib.util.spec_from_file_location("n_batching_replay", _DRIVER)
  assert spec is not None and spec.loader is not None
  module = importlib.util.module_from_spec(spec)
  # Registered before execution: the module's dataclasses resolve their
  # `from __future__` annotations through `sys.modules[cls.__module__]`.
  sys.modules[spec.name] = module
  spec.loader.exec_module(module)
  return module


@pytest.fixture(scope="module")
def driver() -> ModuleType:
  """Return the experiment driver module.

  Returns:
    The loaded module.
  """
  return _load_driver()


def test_committed_witnesses_carry_no_operator_home_path(
    driver: ModuleType,
) -> None:
  """No file under the experiment's `runs/` names a home directory."""
  assert driver.committed_home_paths() == []


def test_the_driver_reproduces_the_shipped_supervisor(
    driver: ModuleType,
) -> None:
  """The driver's rows and prompts match `Supervisor`'s, event for event.

  Skipped where the recorded rollout is absent: it is an off-repo corpus, and
  the row/prompt equality it establishes cannot be faked from what is in git.
  """
  if not driver.EVENT_STREAM.exists():
    pytest.skip(f"corpus absent at {driver.CORPUS_ID}")

  found = driver.driver_matches_supervisor()
  assert found["driver_rows"] == found["shipped_rows"]
  # The prompts are the observable that carries the accumulation and the
  # window: a driver can produce the right row kinds off the wrong evidence.
  assert found["driver_prompts"] == found["shipped_prompts"]
  assert len(found["driver_rows"]) == 170
