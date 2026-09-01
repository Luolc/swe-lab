"""The injection-shape experiment must not commit what its proxy captured.

`cc-reverse-proxy` records the headers it forwards, so a raw `proxy.jsonl`
carries the run's OAuth bearer token on the request side and the operator's
organization / workspace identifiers on the response side. The experiment's
driver redacts both the moment a run ends; these tests pin that, because the
failure is silent and the artifacts are committed.

They also pin the **gate**, which is the half that was missing on 2026-09-01:
redacting and checking that it worked are different claims, and only the second
one holds when the redactor is wrong or is pointed at a producer that never
redacted anything. A capture that fails the check is deleted, and
`test_a_capture_that_stays_dirty_is_deleted` is what keeps that a fact rather
than an intention.

The driver lives under `experiments/`, which is exempt from the code-quality
hooks and is not an importable package, so it is loaded by path.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType
from typing import Any

import pytest

from swe_lab.harnesses.claude_code.redaction import (
    REDACTED,
    unredacted_fields,
)

_DRIVER = (
    Path(__file__).resolve().parents[1]
    / "experiments/trace_synthesis/injection_shape/run_experiment.py"
)


def _load_driver() -> ModuleType:
  """Import the experiment driver by path (it runs `main()` on import)."""
  spec = importlib.util.spec_from_file_location(
      "injection_shape_driver", _DRIVER
  )
  assert spec is not None and spec.loader is not None
  module = importlib.util.module_from_spec(spec)
  argv = sys.argv
  sys.argv = ["run_experiment.py", "--list"]
  try:
    spec.loader.exec_module(module)
  finally:
    sys.argv = argv
  return module


@pytest.fixture(scope="module")
def driver() -> ModuleType:
  return _load_driver()


def _record() -> dict[str, Any]:
  """Build a proxy record shaped like the real capture, secrets and all."""
  return {
      "request": {
          "headers": {
              "Authorization": "Bearer sk-ant-oat-EXAMPLE",
              "X-Api-Key": "sk-ant-EXAMPLE",
              "Anthropic-Beta": "claude-code-20250219",
          },
          "body": {
              "model": "claude-sonnet-4-5",
              "metadata": {"user_id": "account-and-device-identifiers"},
              "messages": [{"role": "user", "content": "hello"}],
          },
      },
      "response": {
          "headers": {
              "Anthropic-Organization-Id": "0" * 8 + "-0000-0000-0000-000000",
              "anthropic-workspace-id": "wrkspc_EXAMPLE",
              "Anthropic-Ratelimit-Unified-Representative-Claim": "claim",
              "Request-Id": "req_EXAMPLE",
          },
          "message": {"role": "assistant", "content": []},
      },
  }


def test_redacts_request_credentials(driver: ModuleType) -> None:
  headers = driver.redact_record(_record())["request"]["headers"]
  assert headers["Authorization"] == REDACTED
  assert headers["X-Api-Key"] == REDACTED


def test_redacts_response_operator_identity(driver: ModuleType) -> None:
  """The half the first version of the redactor missed."""
  headers = driver.redact_record(_record())["response"]["headers"]
  assert headers["Anthropic-Organization-Id"] == REDACTED
  assert headers["anthropic-workspace-id"] == REDACTED
  assert headers["Anthropic-Ratelimit-Unified-Representative-Claim"] == REDACTED


def test_redacts_account_id_in_request_body(driver: ModuleType) -> None:
  body = driver.redact_record(_record())["request"]["body"]
  assert body["metadata"]["user_id"] == REDACTED


def test_keeps_everything_that_is_not_a_secret(driver: ModuleType) -> None:
  """Redaction must not cost the evidence the experiment rests on."""
  record = driver.redact_record(_record())
  assert (
      record["request"]["headers"]["Anthropic-Beta"] == "claude-code-20250219"
  )
  assert record["response"]["headers"]["Request-Id"] == "req_EXAMPLE"
  assert record["request"]["body"]["messages"] == [
      {"role": "user", "content": "hello"}
  ]


def test_redact_proxy_log_rewrites_the_file(
    driver: ModuleType, tmp_path: Path
) -> None:
  path = tmp_path / "proxy.jsonl"
  path.write_text(json.dumps(_record()) + "\n" + json.dumps(_record()) + "\n")
  driver.redact_proxy_log(path)
  lines = path.read_text().splitlines()
  assert len(lines) == 2
  for line in lines:
    record = json.loads(line)
    assert record["request"]["headers"]["Authorization"] == REDACTED
    assert (
        record["response"]["headers"]["Anthropic-Organization-Id"] == REDACTED
    )


def test_a_capture_that_stays_dirty_is_deleted(
    driver: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """The gate the 2026-09-01 token exposure existed for want of.

  Redacting and *checking that it worked* are different claims, and only the
  second one survives the redactor being wrong or aimed at the wrong producer
  — which is exactly what happened: the driver was spawning a proxy build with
  no redaction in it, nothing re-read the result, and a live bearer token sat
  in a capture that looked clean.

  So this drives the failure the real incident could not signal: a redactor
  that quietly does nothing. The capture must not survive it.
  """

  def redact_nothing(record: dict[str, Any]) -> dict[str, Any]:
    return record

  monkeypatch.setattr(driver, "redact_record", redact_nothing)
  path = tmp_path / "proxy.jsonl"
  path.write_text(json.dumps(_record()) + "\n")

  with pytest.raises(SystemExit) as exit_info:
    driver.redact_proxy_log(path)

  assert not path.exists()
  # The message names the offending fields and never their values — a gate
  # that prints what it caught has published it.
  message = str(exit_info.value)
  assert "Authorization" in message
  assert "sk-ant-oat-EXAMPLE" not in message


def test_a_clean_capture_survives_the_gate(
    driver: ModuleType, tmp_path: Path
) -> None:
  """The converse: the gate must not eat the evidence it is guarding."""
  path = tmp_path / "proxy.jsonl"
  path.write_text(json.dumps(_record()) + "\n")
  driver.redact_proxy_log(path)
  assert path.exists()
  assert unredacted_fields(path.read_text()) == []


def test_committed_captures_carry_no_secret() -> None:
  """Check the artifacts in the repo, not just the function that cleans them.

  Through the shared checker rather than a local copy of the rules. A previous
  version of this test enumerated the sensitive names and compared against the
  literal ``"<redacted>"``, which made it the fifth place one fact was written
  down — and the one most likely to go stale, since it guards committed files
  that nobody re-reads.
  """
  runs = _DRIVER.parent / "runs"
  offenders: list[str] = []
  for path in sorted(runs.rglob("proxy.jsonl")):
    offenders += [
        f"{path.parent.name}/{path.name} {finding}"
        for finding in unredacted_fields(path.read_text())
    ]
  assert not offenders, offenders


def test_a_timed_out_run_still_records_that_it_timed_out(
    driver: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """The one field that says "environment failure" must survive the failure.

  `TimeoutExpired` carries bytes even under `text=True`, so the timeout path —
  the only path that needs the flag — was the path where writing the capture
  raised `TypeError` and `meta.json` was never written at all.
  """
  monkeypatch.setenv("SWE_LAB_CLAUDE_CODE_OAUTH_TOKEN", "token-for-the-test")
  # The driver is idempotent against its own runs/ directory; point it at
  # the temporary one so this test neither skips nor writes into evidence.
  monkeypatch.setattr(driver, "RUNS", tmp_path / "runs")
  real_run = driver.subprocess.run

  def fake_run(argv: list[str], **_kwargs: Any) -> Any:
    if argv[:2] == ["claude", "-p"]:
      raise driver.subprocess.TimeoutExpired(
          argv, 600, output=b"partial stream\n", stderr=b"partial stderr\n"
      )
    return real_run(["true"], capture_output=True, text=True)

  monkeypatch.setattr(driver.subprocess, "run", fake_run)
  driver.run_variant("v7-baseline-no-hook-compliance", tmp_path)

  out = tmp_path / "runs" / "v7-baseline-no-hook-compliance"
  meta = json.loads((out / "meta.json").read_text())
  assert meta["timeout"] is True
  assert meta["exit_code"] == -1
  # …and whatever the run produced before the kill is kept, as text.
  assert (out / "stream.jsonl").read_text() == "partial stream\n"
  assert (out / "stderr.txt").read_text() == "partial stderr\n"
