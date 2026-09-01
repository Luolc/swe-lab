"""Nothing reaches the dataset repo without being checked at the boundary.

The check has to run on the **normalized exchange record**, because that is
what `push_traces` uploads (`*.last_exchange.json`). A capture-side check is
not a substitute: the raw proxy log and the uploaded record are different
objects, and a header can be fine in one and unexamined in the other.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from swe_lab.harnesses.claude_code.redaction import REDACTED
from swe_lab.pipelines.related_files.exchange import (
    exchange_publication_blockers,
    OperatorIdentity,
)
from swe_lab.pipelines.related_files.traces import (
    refuse_unpublishable_traces,
    UnpublishableTraceError,
)

_IDENTITY = OperatorIdentity(
    home="/Users/realperson", name="Real Person", email="real@example.com"
)


def _record(**overrides: Any) -> dict[str, Any]:
  """Build a normalized exchange record that is safe to publish."""
  record: dict[str, Any] = {
      "source": "proxy",
      "complete": True,
      "model": "claude-sonnet-4-5",
      "messages": [{"role": "user", "content": "fix the failing test"}],
      "extra_info": {
          "request_headers": {
              "Authorization": REDACTED,
              "X-Claude-Code-Session-Id": "session-kept",
          },
          "response_headers": {
              "Anthropic-Organization-Id": REDACTED,
              "Request-Id": "req_kept",
          },
          "metadata": {"user_id": REDACTED},
      },
  }
  record.update(overrides)
  return record


def test_a_clean_record_publishes() -> None:
  assert exchange_publication_blockers(_record(), identity=_IDENTITY) == []


def test_an_unmasked_credential_blocks() -> None:
  record = _record()
  record["extra_info"]["request_headers"]["Authorization"] = "Bearer real"
  assert exchange_publication_blockers(record, identity=_IDENTITY) == [
      "request_headers Authorization"
  ]


def test_an_unclassified_header_blocks() -> None:
  # The case the capture-side check cannot see: this record is perfectly
  # redacted and still carries a field nobody has judged.
  record = _record()
  record["extra_info"]["response_headers"]["X-Brand-New"] = "whatever"
  assert exchange_publication_blockers(record, identity=_IDENTITY) == [
      "response_headers X-Brand-New (unclassified)"
  ]


def test_operator_identity_in_a_message_body_blocks() -> None:
  # The body sweep. Headers can be spotless while the conversation itself
  # carries the operator's home path — Claude Code puts it in tool output.
  record = _record()
  record["messages"] = [
      {"role": "user", "content": "ls /Users/realperson/dev/swe-lab"}
  ]
  findings = exchange_publication_blockers(record, identity=_IDENTITY)
  assert findings == ["messages operator home path"]
  # The finding names the class, never the value.
  assert "/Users/realperson" not in " ".join(findings)


def test_operator_name_and_email_in_any_field_block() -> None:
  record = _record()
  record["extra_info"]["result"] = "committed as Real Person <real@example.com>"
  assert sorted(exchange_publication_blockers(record, identity=_IDENTITY)) == [
      "extra_info operator email",
      "extra_info operator git name",
  ]


def test_the_gate_refuses_the_upload(tmp_path: Path) -> None:
  # The wiring, not just the function: a bad record under the upload root
  # stops the push. Without this the gate is a function nobody calls.
  base = tmp_path / "swebench_pro" / "intermediate" / "instance_x"
  base.mkdir(parents=True)
  bad = _record()
  bad["extra_info"]["request_headers"]["Authorization"] = "Bearer real"
  (base / "candidate_1.last_exchange.json").write_text(json.dumps(bad))
  with pytest.raises(UnpublishableTraceError) as excinfo:
    refuse_unpublishable_traces(tmp_path, identity=_IDENTITY)
  assert "candidate_1.last_exchange.json" in str(excinfo.value)


def test_a_clean_tree_passes_the_gate(tmp_path: Path) -> None:
  base = tmp_path / "swebench_pro" / "intermediate" / "instance_x"
  base.mkdir(parents=True)
  (base / "candidate_1.last_exchange.json").write_text(json.dumps(_record()))
  # Other files are not uploaded, so the gate must not judge them.
  (base / "candidate_1.json").write_text(json.dumps({"secret": "Real Person"}))
  refuse_unpublishable_traces(tmp_path, identity=_IDENTITY)


def test_push_traces_refuses_before_touching_the_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """The wiring, pinned: a bad trace must stop the push, not just report.

  The direct test above passes with the call removed from ``push_traces`` —
  it exercises the function, not the gate. This one fails in that case,
  because the fake API records whether it was reached.
  """
  from swe_lab.pipelines.related_files import traces

  base = tmp_path / "outputs" / "related_files" / "swebench_pro"
  base.mkdir(parents=True)
  bad = _record()
  bad["extra_info"]["response_headers"][
      "Anthropic-Organization-Id"
  ] = "org_real"
  (base / "c1.last_exchange.json").write_text(json.dumps(bad))

  reached: list[str] = []

  class _FakeApi:

    def create_repo(self, *_args: object, **_kwargs: object) -> None:
      reached.append("create_repo")

    def upload_folder(self, *_args: object, **_kwargs: object) -> None:
      reached.append("upload_folder")

  monkeypatch.setattr(traces, "HfApi", _FakeApi)
  monkeypatch.setattr(
      OperatorIdentity,
      "of_this_machine",
      classmethod(lambda cls: _IDENTITY),
  )

  with pytest.raises(UnpublishableTraceError):
    _ = traces.push_traces(repo_root=tmp_path)
  assert (
      reached == []
  ), "the push reached the API despite an unpublishable trace"


def test_the_gate_runs_before_the_manifest_check_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  """The ordinary push path — a manifest exists — must refuse just as early.

  The no-manifest test above passes even when the gate sits after the
  concurrency guard, because that branch is skipped without a manifest
  revision. On every subsequent push the guard calls ``repo_info`` first, so a
  gate placed after it has already contacted the remote about a tree it was
  supposed to refuse.
  """
  from swe_lab.pipelines.related_files import traces

  base = tmp_path / "outputs" / "related_files" / "swebench_pro"
  base.mkdir(parents=True)
  bad = _record()
  bad["extra_info"]["response_headers"][
      "Anthropic-Organization-Id"
  ] = "org_real"
  (base / "c1.last_exchange.json").write_text(json.dumps(bad))

  reached: list[str] = []

  class _FakeApi:

    def repo_info(self, *_args: object, **_kwargs: object) -> None:
      reached.append("repo_info")

    def create_repo(self, *_args: object, **_kwargs: object) -> None:
      reached.append("create_repo")

    def upload_folder(self, *_args: object, **_kwargs: object) -> None:
      reached.append("upload_folder")

  monkeypatch.setattr(traces, "HfApi", _FakeApi)
  monkeypatch.setattr(
      OperatorIdentity, "of_this_machine", classmethod(lambda cls: _IDENTITY)
  )

  def _manifest(*_args: object, **_kwargs: object) -> dict[str, str]:
    return {"revision": "deadbeef"}

  monkeypatch.setattr(traces, "_load_manifest", _manifest)

  with pytest.raises(UnpublishableTraceError):
    _ = traces.push_traces(repo_root=tmp_path)
  assert reached == [], "the push contacted the remote despite a bad trace"
