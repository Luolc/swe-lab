"""Tests for the SWE-Bench Pro record type: parsing + its runnable surface."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import override

from etils import epath
import pytest

from swe_lab.datasets.swebench_pro import (
    COLUMNS,
    SweBenchProInstance,
)
import swe_lab.datasets.swebench_pro.auxiliary as auxiliary
from swe_lab.datasets.swebench_pro.constants import (
    HARNESS_SUBDIR,
    IMAGE_REPO,
    PARQUET_FILENAME,
    PARSER_NAME,
    RUN_SCRIPT_NAME,
    WORKDIR,
)
from swe_lab.datasets.swebench_pro.fetch import ensure_swebench_pro_parquet
import swe_lab.datasets.swebench_pro.fetch as fetch
from swe_lab.paths import cache_root


def _raw(**overrides: str) -> dict[str, str]:
  """Build a minimal valid raw row, with per-test overrides."""
  base = {
      "repo": "acme/widget",
      "instance_id": "instance_acme__widget-abc-vnan",
      "base_commit": "0" * 40,
      "patch": "diff --git a/x b/x\n",
      "test_patch": "diff --git a/t b/t\n",
      "problem_statement": '"**Title**\\n\\nBody"',
      "requirements": "plain requirements text",
      "interface": '"Type: Method"',
      "repo_language": "python",
      "fail_to_pass": "['a::t1', \"b::t2\"]",
      "pass_to_pass": '["c::t3"]',
      "issue_specificity": '["major_bug"]',
      "issue_categories": '["back_end_knowledge"]',
      "before_repo_set_cmd": "git reset --hard HEAD",
      "selected_test_files_to_run": '["test/a.py"]',
      "dockerhub_tag": "acme.widget-abc",
  }
  base.update(overrides)
  return base


def test_from_raw_parses_all_field_kinds() -> None:
  inst = SweBenchProInstance.from_raw(_raw())

  # Plain string columns pass through untouched.
  assert inst.repo == "acme/widget"
  assert inst.before_repo_set_cmd == "git reset --hard HEAD"
  assert inst.dockerhub_tag == "acme.widget-abc"

  # JSON-string-wrapped text columns are unwrapped.
  assert inst.problem_statement == "**Title**\n\nBody"
  assert inst.interface == "Type: Method"

  # Raw (non-wrapped) text columns are left as-is.
  assert inst.requirements == "plain requirements text"

  # List columns become tuples of str, including mixed-quote Python reprs.
  assert inst.fail_to_pass == ("a::t1", "b::t2")
  assert inst.pass_to_pass == ("c::t3",)
  assert inst.selected_test_files_to_run == ("test/a.py",)


def test_text_unwrap_leaves_raw_leading_quote_untouched() -> None:
  # A genuinely-raw statement that merely starts with a quote is not valid JSON
  # and must be preserved verbatim.
  inst = SweBenchProInstance.from_raw(_raw(problem_statement='"unterminated'))
  assert inst.problem_statement == '"unterminated'


def test_instance_is_frozen_and_hashable() -> None:
  inst = SweBenchProInstance.from_raw(_raw())
  with pytest.raises(AttributeError):
    inst.repo = "other"  # pyright: ignore[reportAttributeAccessIssue]
  assert hash(inst) == hash(inst)


def test_missing_columns_raise() -> None:
  row = _raw()
  del row["interface"]
  with pytest.raises(ValueError, match="missing expected columns"):
    SweBenchProInstance.from_raw(row)


def test_columns_constant_has_16_entries() -> None:
  assert len(COLUMNS) == 16
  assert len(set(COLUMNS)) == 16


# ─── the runnable surface (TaskInstance) ─────────────────────────────────────


def test_sandbox_spec_is_built_from_the_instance_fields() -> None:
  spec = SweBenchProInstance.from_raw(_raw()).sandbox_spec()
  assert spec.instance_id == "instance_acme__widget-abc-vnan"
  assert spec.image_ref == f"{IMAGE_REPO}:acme.widget-abc"
  assert spec.workdir == WORKDIR
  assert spec.base_commit == "0" * 40


def test_prompt_combines_the_three_columns() -> None:
  # mirrors Scale's create_problem_statement verbatim
  prompt = SweBenchProInstance.from_raw(
      _raw(
          problem_statement="The widget crashes on empty input.",
          requirements="Must not raise on None.",
          interface="def render(widget) -> str",
      )
  ).prompt()
  assert prompt == (
      "The widget crashes on empty input.\n\n"
      "Requirements:\nMust not raise on None.\n\n"
      "New interfaces introduced:\ndef render(widget) -> str"
  )


def test_prompt_keeps_headers_when_columns_empty() -> None:
  # headers are unconditional, like the original (no per-section omission)
  prompt = SweBenchProInstance.from_raw(
      _raw(problem_statement="Just the statement.")
  ).prompt()
  assert "Just the statement." in prompt
  assert "Requirements:" in prompt
  assert "New interfaces introduced:" in prompt


def test_golden_test_checkout_cmd_is_the_last_line_of_before_cmd() -> None:
  inst = SweBenchProInstance.from_raw(
      _raw(
          before_repo_set_cmd="setup one\nsetup two\ngit checkout GOLD -- t.py"
      )
  )
  assert inst.golden_test_checkout_cmd == "git checkout GOLD -- t.py"
  empty = SweBenchProInstance.from_raw(_raw(before_repo_set_cmd="  "))
  assert empty.golden_test_checkout_cmd == ""


def _stage_harness(repo_root: Path, instance_id: str) -> None:
  harness = cache_root(repo_root) / HARNESS_SUBDIR / instance_id
  harness.mkdir(parents=True)
  _ = (harness / RUN_SCRIPT_NAME).write_text("echo run")
  _ = (harness / PARSER_NAME).write_text("print('parse')")


def test_run_script_and_parser_read_the_cached_harness(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
  # the default properties fetch-and-cache under find_repo_root(); point that
  # at a pre-staged cache so no network is touched.
  monkeypatch.setattr(auxiliary, "find_repo_root", lambda: tmp_path)
  _stage_harness(tmp_path, "instance_acme__widget-abc-vnan")
  inst = SweBenchProInstance.from_raw(_raw())
  assert inst.run_script == b"echo run"
  assert inst.parser == b"print('parse')"


def test_missing_harness_triggers_a_fetch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
  # nothing staged → the default property fetches; assert it reaches for the
  # network (raising) rather than silently succeeding.
  monkeypatch.setattr(auxiliary, "find_repo_root", lambda: tmp_path)
  inst = SweBenchProInstance.from_raw(_raw())
  with pytest.raises(Exception):  # noqa: B017 — any network/URL error is fine
    _ = inst.run_script


def test_run_script_and_parser_are_overridable() -> None:
  # the harness is the instance's business: a subclass supplies it directly,
  # so grading needs no network and no repo checkout.
  class _Embedded(SweBenchProInstance):

    @property
    @override
    def run_script(self) -> bytes:
      return b"EMBEDDED RUN"

    @property
    @override
    def parser(self) -> bytes:
      return b"EMBEDDED PARSE"

  inst = _Embedded.from_raw(_raw())
  assert inst.run_script == b"EMBEDDED RUN"
  assert inst.parser == b"EMBEDDED PARSE"


def test_a_drifted_parquet_is_rejected_not_silently_loaded(
    tmp_path: Path,
) -> None:
  # The pin anchors nothing if a present-but-wrong file is trusted: a
  # truncated download or a drifted upstream file must fail loudly, the same
  # invariant deepswe's fetch already enforces.
  data = tmp_path / "data"
  data.mkdir()
  _ = (data / PARQUET_FILENAME).write_bytes(b"not the pinned bytes")
  with pytest.raises(ValueError, match="does not match the pinned sha256"):
    _ = ensure_swebench_pro_parquet(data)


def test_a_missing_parquet_names_the_readme(tmp_path: Path) -> None:
  # The download is manual (README), so the failure a consumer sees when they
  # skipped it should point there rather than surface as a bare stat error.
  data = tmp_path / "data"
  data.mkdir()
  with pytest.raises(FileNotFoundError, match="README"):
    _ = ensure_swebench_pro_parquet(data)


def test_a_misnamed_parquet_is_named_as_misnamed_not_broken(
    tmp_path: Path,
) -> None:
  # The pin is keyed to the exact filename as well as content, so a present
  # file under any other name reads as "not found" — the same misleading-
  # symptom family conventions.md already names for a missing dataset
  # (FileNotFoundError reading as "these instances are broken"). The message
  # must say a file *is* there, under the wrong name, not just that the
  # pinned one is absent.
  data = tmp_path / "data"
  data.mkdir()
  _ = (data / "test-00000-of-00002.parquet").write_bytes(b"whatever")
  with pytest.raises(FileNotFoundError, match="test-00000-of-00002.parquet"):
    _ = ensure_swebench_pro_parquet(data)


def test_a_matching_file_is_accepted_and_returned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
  # The rejection paths above prove nothing about the accept path: a verifier
  # that always raises would pass every one of them. Pin the expected digest
  # to whatever bytes are actually written, so this test does not depend on
  # the real dataset's content or the live pin. Called twice, unmodified
  # between calls: this is a load-time check, not a one-shot admission, so a
  # second call must accept the same file the same way — guards against a
  # verifier that only works once (a moved file, a consumed handle, a
  # poisoned cache).
  data = tmp_path / "data"
  data.mkdir()
  target = data / PARQUET_FILENAME
  content = b"pretend parquet bytes"
  _ = target.write_bytes(content)
  monkeypatch.setattr(
      fetch,
      "PINNED_SWEBENCH_PRO_PARQUET_SHA256",
      hashlib.sha256(content).hexdigest(),
  )
  assert ensure_swebench_pro_parquet(data) == epath.Path(target)
  assert ensure_swebench_pro_parquet(data) == epath.Path(target)
