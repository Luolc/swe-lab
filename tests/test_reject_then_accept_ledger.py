"""The witness run's cost ceiling must cover every call it authorizes.

A stop rule that watches only some paid calls does not bound anything: an accept
arriving just under the ceiling could still buy its two repeat judgements, and a
cache-off confirmation could follow K attempts, both landing outside the total
the run reports. So the ledger is the single place cost accrues, and "exceeds"
is tested at the boundary rather than assumed.

The digests are checked against the pre-registration in the same file, because a
pre-registration whose numbers drift from the code it governs binds nothing.

Loaded by path like `test_steered_rerun_driver.py`: `experiments/` is exempt
from the code-quality hooks and is not an importable package.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import ModuleType

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_WITNESS = (
    _ROOT
    / "experiments/trace_synthesis/process_supervision"
    / "reject_then_accept_witness/witness.py"
)
_PRE_REGISTRATION = _WITNESS.parent / "PRE-REGISTRATION.md"


@pytest.fixture(scope="module")
def witness() -> ModuleType:
  """Import the witness runner by path.

  Returns:
    The executed module.
  """
  spec = importlib.util.spec_from_file_location("witness_runner", _WITNESS)
  assert spec is not None and spec.loader is not None
  module = importlib.util.module_from_spec(spec)
  sys.modules[spec.name] = module
  try:
    spec.loader.exec_module(module)
  finally:
    del sys.modules[spec.name]
  return module


def test_the_ledger_counts_every_kind_of_billable_call(
    witness: ModuleType, tmp_path: Path
):
  """Actor, judge, repeat and cache-off calls all reach the same total."""
  ledger = witness.Ledger(tmp_path / "ledger.json")
  for kind in (
      "judge:attempt-0",
      "actor:attempt-1",
      "judge:attempt-1",
      "judge:repeat",
      "actor:cache-off",
  ):
    ledger.record(kind, {"cost": 0.10})
  assert ledger.total == pytest.approx(0.50)
  assert len(ledger.entries) == 5
  assert (tmp_path / "ledger.json").exists()


def test_the_ceiling_trips_only_once_cost_exceeds_it(
    witness: ModuleType, tmp_path: Path
):
  """At the ceiling the run may continue; past it, it may not."""
  ledger = witness.Ledger(tmp_path / "ledger.json")
  ledger.record("actor:attempt-1", {"cost": witness._COST_CEILING_USD})
  assert not ledger.exhausted
  ledger.record("judge:repeat", {"cost": 0.01})
  assert ledger.exhausted


def test_a_missing_cost_is_zero_not_an_error(
    witness: ModuleType, tmp_path: Path
):
  """A usage block without a cost must not abort the run."""
  ledger = witness.Ledger(tmp_path / "ledger.json")
  ledger.record("actor:attempt-1", {})
  ledger.record("judge:attempt-1", None)
  assert ledger.total == 0.0


def test_the_pre_registered_digests_match_the_document(witness: ModuleType):
  """The digests the code enforces are the ones the document fixed."""
  text = _PRE_REGISTRATION.read_text()
  assert witness._BODY_SHA256 in text
  assert witness._ORIGINAL_COMPLETION_SHA256 in text


def _fake_capture(tmp_path: Path, witness: ModuleType) -> Path:
  """Write a capture whose digests the test then pre-registers.

  Args:
    tmp_path: Directory for the file.
    witness: The runner module, for its canonical serializer.

  Returns:
    The capture path.
  """
  import json

  body = {
      "model": "m",
      "stream": True,
      "messages": [],
      "tools": [{"name": "t"}],
  }
  message = {
      "content": [{"type": "text", "text": "x"}],
      "stop_reason": "end_turn",
  }
  row = {
      "request": {"body": body},
      "response": {"message": message, "headers": {}},
  }
  path = tmp_path / "capture.jsonl"
  _ = path.write_text(
      "".join(json.dumps(row) + "\n" for _ in range(witness._STEP_INDEX + 1))
  )
  return path


def _arrange(
    witness: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    actor_cost: float,
    judge_cost: float,
    verdict: str = "off_track",
) -> Path:
  """Point the runner at fakes so `main` runs with no network.

  Args:
    witness: The runner module.
    monkeypatch: Patcher.
    tmp_path: Working directory.
    actor_cost: Cost each actor response reports.
    judge_cost: Cost each judge response reports.
    verdict: The verdict every judge answer carries.

  Returns:
    The output directory `main` was given.
  """
  import hashlib
  import json

  capture = _fake_capture(tmp_path, witness)
  rows = [json.loads(line) for line in capture.read_text().splitlines()]
  body = rows[witness._STEP_INDEX]["request"]["body"]
  original = rows[witness._STEP_INDEX]["response"]["message"]
  monkeypatch.setattr(witness, "_CAPTURE", capture)
  monkeypatch.setattr(
      witness,
      "_BODY_SHA256",
      hashlib.sha256(witness.canonical(body)).hexdigest(),
  )
  monkeypatch.setattr(
      witness,
      "_ORIGINAL_COMPLETION_SHA256",
      hashlib.sha256(witness.canonical(original["content"])).hexdigest(),
  )

  def _extract_steps(_rollout: str) -> list[dict[str, object]]:
    return [
        {"content": "c", "step_index": i, "tool_names": []} for i in range(37)
    ]

  def _judge(*_args: object, **_kwargs: object) -> dict[str, object]:
    return {
        "raw": json.dumps({"adjudicable": True, "verdict": verdict}),
        "usage": {"cost": judge_cost},
    }

  monkeypatch.setattr(witness._extract, "extract", _extract_steps)
  monkeypatch.setattr(witness, "_judge_completion", _judge)

  proxy_log = tmp_path / "proxy_log.jsonl"
  _ = proxy_log.write_text("")

  class _Process:

    def terminate(self) -> None:
      return None

  def _start(_out_dir: Path):
    return _Process(), 1, proxy_log

  monkeypatch.setattr(witness, "_start_proxy", _start)

  def _keys() -> list[str]:
    # No credential is read: the request never leaves the fake transport below.
    return ["not-a-key"]

  monkeypatch.setattr(witness._judge, "key_pool", _keys)

  class _Response:

    def __enter__(self):
      return self

    def __exit__(self, *_exc: object) -> None:
      return None

    def read(self) -> bytes:
      with proxy_log.open("a") as handle:
        _ = handle.write(
            json.dumps(
                {
                    "response": {
                        "message": {
                            "content": [{"type": "text", "text": "y"}],
                            "usage": {"cost": actor_cost},
                        },
                        "headers": {},
                    }
                }
            )
            + "\n"
        )
      return b"data: {}\n"

  def _urlopen(*_args: object, **_kwargs: object) -> _Response:
    return _Response()

  monkeypatch.setattr(witness.urllib.request, "urlopen", _urlopen)
  out = tmp_path / "out"
  monkeypatch.setattr(
      witness.sys, "argv", ["witness.py", "--out-dir", str(out), "--k", "3"]
  )
  return out


def test_an_actor_response_crossing_the_ceiling_stops_before_the_judge(
    witness: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  """The judge is not billed once the actor's own cost has crossed."""
  import json

  out = _arrange(
      witness,
      monkeypatch,
      tmp_path,
      actor_cost=witness._COST_CEILING_USD + 1,
      judge_cost=0.01,
  )
  witness.main()
  kinds = [
      e["kind"]
      for e in json.loads((out / "ledger.json").read_text())["entries"]
  ]
  assert kinds == ["judge:attempt-0", "actor:attempt-1"]
  assert (
      json.loads((out / "classification.json").read_text())["classification"]
      == "inconclusive"
  )


def test_a_cache_off_call_crossing_the_ceiling_is_not_complete(
    witness: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  """A cache-off call that crosses gives inconclusive, not complete."""
  import json

  # Each attempt is cheap; the K identical completions trigger cache-off, and
  # the ledger crosses only once that last call is added.
  out = _arrange(
      witness,
      monkeypatch,
      tmp_path,
      actor_cost=witness._COST_CEILING_USD / 3,
      judge_cost=0.0,
  )
  witness.main()
  entries = json.loads((out / "ledger.json").read_text())["entries"]
  assert "actor:cache-off" in [e["kind"] for e in entries]
  assert (
      json.loads((out / "classification.json").read_text())["classification"]
      == "inconclusive"
  )


def test_changed_material_stops_before_any_paid_or_started_call(
    witness: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  """A capture altered after the digest was pinned ends the run as `void`."""
  import json

  out = _arrange(
      witness, monkeypatch, tmp_path, actor_cost=0.01, judge_cost=0.01
  )
  # Pinned above against the original bytes; now the off-repo material changes.
  capture = tmp_path / "capture.jsonl"
  rows = [json.loads(line) for line in capture.read_text().splitlines()]
  rows[witness._STEP_INDEX]["request"]["body"]["model"] = "swapped"
  _ = capture.write_text("".join(json.dumps(r) + "\n" for r in rows))

  def _forbidden(*_args: object, **_kwargs: object) -> object:
    raise AssertionError("a paid or starting call was reached")

  monkeypatch.setattr(witness, "_judge_completion", _forbidden)
  monkeypatch.setattr(witness, "_start_proxy", _forbidden)
  monkeypatch.setattr(witness.urllib.request, "urlopen", _forbidden)

  with pytest.raises(SystemExit) as raised:
    witness.main()
  assert "void" in str(raised.value)
  material = json.loads((out / "material.json").read_text())
  assert material["classification"] == "void"
  assert material["body_sha256_observed"] != material["body_sha256_expected"]
  assert not (out / "ledger.json").exists()


def test_a_complete_run_of_identical_completions_records_outcome_1(
    witness: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  """K identical completions with no accept persist as outcome-1."""
  import json

  out = _arrange(
      witness, monkeypatch, tmp_path, actor_cost=0.001, judge_cost=0.001
  )
  witness.main()
  final = json.loads((out / "classification.json").read_text())
  assert final["classification"] == "outcome-1"
  assert final["distinct_completions"] == 1
  assert final["first_accept_attempt"] is None


def test_a_first_accept_records_outcome_3_with_its_attempt_number(
    witness: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  """An accepted completion persists as outcome-3 and names k."""
  import json

  out = _arrange(
      witness, monkeypatch, tmp_path, actor_cost=0.001, judge_cost=0.001
  )
  # Attempt 0 must still reject, or the run stops as `material-retired` before
  # any resend -- so the judge rejects the original, then accepts.
  calls = {"n": 0}

  def _judge(*_args: object, **_kwargs: object) -> dict[str, object]:
    calls["n"] += 1
    verdict = "off_track" if calls["n"] == 1 else "on_track"
    return {
        "raw": json.dumps({"adjudicable": True, "verdict": verdict}),
        "usage": {"cost": 0.001},
    }

  monkeypatch.setattr(witness, "_judge_completion", _judge)
  witness.main()
  final = json.loads((out / "classification.json").read_text())
  assert final["classification"] == "outcome-3"
  assert final["first_accept_attempt"] == 1


def test_an_accept_that_does_not_reproduce_is_not_a_witness(
    witness: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  """One accept and two rejecting repeats is not outcome-3."""
  import json

  out = _arrange(
      witness, monkeypatch, tmp_path, actor_cost=0.001, judge_cost=0.001
  )
  # Reject attempt 0, accept the first resend, then reject both re-judgements.
  calls = {"n": 0}

  def _judge(*_args: object, **_kwargs: object) -> dict[str, object]:
    calls["n"] += 1
    verdict = "on_track" if calls["n"] == 2 else "off_track"
    return {
        "raw": json.dumps({"adjudicable": True, "verdict": verdict}),
        "usage": {"cost": 0.001},
    }

  monkeypatch.setattr(witness, "_judge_completion", _judge)
  witness.main()
  final = json.loads((out / "classification.json").read_text())
  assert final["classification"] == "unreproduced-accept"
  assert final["accepted_of_3"] == 1
  assert final["first_accept_attempt"] == 1


def test_a_changed_original_completion_is_also_void(
    witness: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  """The gate binds attempt 0's material, not only the request body."""
  import json

  out = _arrange(
      witness, monkeypatch, tmp_path, actor_cost=0.001, judge_cost=0.001
  )
  capture = tmp_path / "capture.jsonl"
  rows = [json.loads(line) for line in capture.read_text().splitlines()]
  # The body is untouched; only the completion attempt 0 re-judges changes.
  rows[witness._STEP_INDEX]["response"]["message"]["content"] = [
      {"type": "text", "text": "swapped"}
  ]
  _ = capture.write_text("".join(json.dumps(r) + "\n" for r in rows))

  def _forbidden(*_args: object, **_kwargs: object) -> object:
    raise AssertionError("a paid or starting call was reached")

  monkeypatch.setattr(witness, "_judge_completion", _forbidden)
  monkeypatch.setattr(witness, "_start_proxy", _forbidden)
  monkeypatch.setattr(witness.urllib.request, "urlopen", _forbidden)

  with pytest.raises(SystemExit) as raised:
    witness.main()
  assert "void" in str(raised.value)
  material = json.loads((out / "material.json").read_text())
  assert material["classification"] == "void"
  assert material["body_sha256_observed"] == material["body_sha256_expected"]
  assert (
      material["original_completion_sha256_observed"]
      != material["original_completion_sha256_expected"]
  )
  assert not (out / "ledger.json").exists()


def test_an_unreadable_repeat_is_not_agreement(
    witness: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  """A malformed re-judgement classifies, rather than ending the run."""
  import json

  out = _arrange(
      witness, monkeypatch, tmp_path, actor_cost=0.001, judge_cost=0.001
  )
  calls = {"n": 0}

  def _judge(*_args: object, **_kwargs: object) -> dict[str, object]:
    calls["n"] += 1
    if calls["n"] == 1:
      raw = json.dumps({"adjudicable": True, "verdict": "off_track"})
    elif calls["n"] == 2:
      raw = json.dumps({"adjudicable": True, "verdict": "on_track"})
    else:
      raw = "{"
    return {"raw": raw, "usage": {"cost": 0.001}}

  monkeypatch.setattr(witness, "_judge_completion", _judge)
  witness.main()
  final = json.loads((out / "classification.json").read_text())
  assert final["classification"] == "unreproduced-accept"
  assert final["accepted_of_3"] == 1


def test_an_unreadable_attempt_zero_is_not_material_retired(
    witness: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  """An unreadable first answer asserts nothing, so it gets its own ending."""
  import json

  out = _arrange(
      witness, monkeypatch, tmp_path, actor_cost=0.001, judge_cost=0.001
  )

  def _judge(*_args: object, **_kwargs: object) -> dict[str, object]:
    return {"raw": "{", "usage": {"cost": 0.001}}

  def _forbidden(*_args: object, **_kwargs: object) -> object:
    raise AssertionError("a resend was started")

  monkeypatch.setattr(witness, "_judge_completion", _judge)
  monkeypatch.setattr(witness, "_start_proxy", _forbidden)
  witness.main()
  final = json.loads((out / "classification.json").read_text())
  assert final["classification"] == "judge-unparseable"
  assert final["at"] == "attempt-0"


def test_unreadable_resend_judgements_cannot_become_outcome_2(
    witness: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  """Outcome 2 needs K readable verdicts, not K unreadable ones."""
  import json

  out = _arrange(
      witness, monkeypatch, tmp_path, actor_cost=0.001, judge_cost=0.001
  )
  calls = {"n": 0}

  def _judge(*_args: object, **_kwargs: object) -> dict[str, object]:
    calls["n"] += 1
    raw = (
        json.dumps({"adjudicable": True, "verdict": "off_track"})
        if calls["n"] == 1
        else "{"
    )
    return {"raw": raw, "usage": {"cost": 0.001}}

  # Divergent completions, so outcome-1 does not apply and only the judge could
  # decide -- and it never did.
  seen = {"n": 0}
  monkeypatch.setattr(witness, "_judge_completion", _judge)
  proxy_log = tmp_path / "proxy_log.jsonl"

  class _Diverging:

    def __enter__(self) -> _Diverging:
      return self

    def __exit__(self, *_exc: object) -> None:
      return None

    def read(self) -> bytes:
      seen["n"] += 1
      with proxy_log.open("a") as handle:
        _ = handle.write(
            json.dumps(
                {
                    "response": {
                        "message": {
                            "content": [
                                {"type": "text", "text": f"v{seen['n']}"}
                            ],
                            "usage": {"cost": 0.001},
                        },
                        "headers": {},
                    }
                }
            )
            + "\n"
        )
      return b"data: {}\n"

  def _diverging(*_args: object, **_kwargs: object) -> _Diverging:
    return _Diverging()

  monkeypatch.setattr(witness.urllib.request, "urlopen", _diverging)
  witness.main()
  final = json.loads((out / "classification.json").read_text())
  assert final["classification"] == "judge-unparseable"
  assert final["at"] == "resend"
  assert final["unreadable_judgements"] == 3
  assert final["distinct_completions"] == 3


def test_identical_completions_outrank_unreadable_verdicts(
    witness: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
  """Outcome 1 is the actor's property; an unreadable judge cannot undo it."""
  import json

  out = _arrange(
      witness, monkeypatch, tmp_path, actor_cost=0.001, judge_cost=0.001
  )
  calls = {"n": 0}

  def _judge(*_args: object, **_kwargs: object) -> dict[str, object]:
    calls["n"] += 1
    raw = (
        json.dumps({"adjudicable": True, "verdict": "off_track"})
        if calls["n"] == 1
        else "{"
    )
    return {"raw": raw, "usage": {"cost": 0.001}}

  monkeypatch.setattr(witness, "_judge_completion", _judge)
  witness.main()
  final = json.loads((out / "classification.json").read_text())
  assert final["classification"] == "outcome-1"
  assert final["distinct_completions"] == 1
  assert final["unreadable_judgements"] == 3
