"""The `N`-batching replay's committed witness, and the driver that made it.

These properties of
`experiments/trace_synthesis/n_batching_replay/` are asserted here rather than
inside the experiment, because each failure is silent and the artifacts are
committed:

- **The experiment's driver reproduces the shipped `Supervisor`.** The report's
  numbers come from the driver, not from `Supervisor`, so the equivalence is
  what makes them evidence about the shipped component. The experiment's own
  `self-check` claimed this while never invoking the driver, so a driver that
  raised on every call still printed OK.
- **A fresh run reaches its manifest.** `cmd_run` writes the judgments as it
  goes and the manifest only at the end, so a fault in that last step costs a
  whole arm's paid calls and leaves an un-provenanced directory behind. One
  landed there — an undefined name in the manifest literal — and was invisible
  because every committed artifact predated it and `self-check` stops before
  `cmd_run`.
- **A boundary the policy did not judge is recorded as such.** The driver's row
  kinds are read as the supervisor's, so collapsing `Unjudged` into a spoken
  correction writes a correction that was never decided on into the one
  artifact whose purpose is to be a faithful replay.

The driver lives under `experiments/`, which is exempt from the code-quality
hooks and is not an importable package, so it is loaded by path.

**"No committed artifact carries an operator home path" used to be asserted
here too, and is not any more** (#399). It scanned one experiment's `runs/`
under a name — `test_committed_witnesses_carry_no_operator_home_path` — that
read as a repo-wide guarantee, and #384 was filed because that name misled a
reader. `tests/test_operator_home_paths.py` now checks every tracked file, so
this file's domain is strictly inside it. `replay.py`'s own
`committed_home_paths()` stays, and is not the same check: it walks the
filesystem, so it also sees a *fresh, uncommitted* run, which no tracked-file
scan can reach.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType

import pytest

from swe_lab.trace_synthesis.supervisor import (
    Intervention,
    LOG_KIND_SILENT,
    LOG_KIND_SPOKE,
    LOG_KIND_UNJUDGED,
    Observation,
    Unjudged,
)

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


def test_the_driver_gives_unjudged_its_own_row_kind(
    driver: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
  """An `Unjudged` decision is its own row kind, never a correction.

  `SpeakWhenOffTrack.consider` answers three ways, and only one of them is a
  correction. A driver that classifies the other two together writes a `spoke`
  row for a boundary at which nothing was decided — a correction asserted by
  the very artifact whose purpose is to be a faithful replay. The row kinds
  must therefore be the shipped `Supervisor`'s own.

  Runs without the corpus and pays for no model call, so the whole of this
  check is reachable wherever the suite runs.
  """
  decisions: list[Intervention | Unjudged | None] = [
      Unjudged(reason="no actor evidence in the window"),
      None,
      Intervention(text="run the failing test before changing anything"),
  ]

  def consider(observation: Observation) -> Intervention | Unjudged | None:
    """Return the next scripted decision.

    Args:
      observation: Ignored; what the policy would judge is not what is tested.

    Returns:
      The next of `decisions`, in order.
    """
    del observation
    return decisions.pop(0)

  policy = driver._stub_policy()
  monkeypatch.setattr(policy, "consider", consider)
  events = [
      {
          "type": "assistant",
          "message": {
              "role": "assistant",
              "content": [{"type": "text", "text": f"step {index}"}],
          },
      }
      for index in range(3)
  ]

  rows = list(
      driver.replay(
          arm=driver.Arm("unjudged-probe", None),
          events=events,
          task="t",
          policy=policy,
          boundaries=(1, 2, 3),
          criterion=policy.criterion,
      )
  )

  # The vocabulary is the shipped `Supervisor`'s, not a parallel one.
  assert [row["kind"] for row in rows] == [
      LOG_KIND_UNJUDGED,
      LOG_KIND_SILENT,
      LOG_KIND_SPOKE,
  ]
  assert rows[0]["reason"] == "no actor evidence in the window"
  assert [row["text"] for row in rows] == [
      None,
      None,
      "run the failing test before changing anything",
  ]


def test_a_fresh_run_writes_its_manifest(
    driver: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """`cmd_run` completes an arm and records it, with no network call.

  The regression is real and was found in review: the manifest literal
  referenced a name bound only inside `replay`, so a fresh run wrote every
  judgment and then raised before recording what produced them.
  """
  if not driver.EVENT_STREAM.exists():
    pytest.skip(f"corpus absent at {driver.CORPUS_ID}")

  arm = next(a for a in driver.ARMS if a.name == "n10")
  recording = driver.RecordingTransport
  monkeypatch.setattr(driver, "RUNS", tmp_path)
  monkeypatch.setattr(driver, "ARMS", (arm,))
  monkeypatch.setattr(
      driver,
      "RecordingTransport",
      lambda: recording(send=driver._canned),
  )

  driver.cmd_run(argparse.Namespace(pass_id="a"))

  manifest = json.loads(
      (tmp_path / arm.name / "a" / "manifest.json").read_text()
  )
  assert manifest["arm"] == arm.name
  assert manifest["window"] == arm.window
  assert manifest["boundaries"] == 5
  assert manifest["events"] == 170
  rows = (
      (tmp_path / arm.name / "a" / "judgments.jsonl").read_text().splitlines()
  )
  assert len(rows) == manifest["boundaries"]
