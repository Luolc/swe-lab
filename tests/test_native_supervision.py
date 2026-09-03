"""The contract the native supervision runtime is configured and read through.

Three things cross the boundary between this repository's Python and the
`rust/swe-lab-supervisor` binary, and each fails silently in its own way:

- **the config document.** The binary refuses an unknown field and defaults
  nothing, so a document that is a superset of the schema, or that leaves a
  policy number to be chosen for it, is refused at run time — inside a sandbox
  that has already been paid for. What is asserted here is the exact shape.
- **the two environment variables.** The endpoint and the credential are the
  environment's, never the document's. A credential that reached the document
  would reach a workspace artifact, which is why its absence is asserted
  against a document first shown to be a real one.
- **the terminal summary.** It is the only thing a native run may be
  classified from: the wrapper exits with the *actor's* status, so a run that
  supervised nothing can exit `0`. Every way of failing to read one has to
  reach a reader as a failure rather than as an ordinary result.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

import pytest

from swe_lab.rollout import SUPERVISION_LAPSE_METRIC, SUPERVISION_METRIC
from swe_lab.trace_synthesis.channel import (
    BOUNDARIES_METRIC,
    CORRECTIONS_METRIC,
)
from swe_lab.trace_synthesis.criterion import (
    CRITERION_PATH,
    CRITERION_SHA256,
    CriterionRejectedError,
)
from swe_lab.trace_synthesis.native_supervision import (
    CONFIG_SCHEMA_VERSION,
    CRITERION_NAME,
    NativeSupervision,
    NON_NUMERIC_FIELDS,
    NUMERIC_FIELDS,
    POLICY_KIND,
    read_terminal_summary,
    SUMMARY_FIELDS,
    SUMMARY_SCHEMA_VERSION,
    SUPERVISION_MAX_DECISION_LAG_METRIC,
    supervision_metrics,
    SUPERVISION_STALE_METRIC,
    TerminalSummary,
    UnusableSummary,
)

_SUPERVISION = NativeSupervision(
    model="anthropic/claude-sonnet-5",
    budget=3,
    cooldown=4,
    window=8,
    judge_every_n_assistant_messages=3,
    block_actor_while_judging=True,
)

_SUMMARY = {
    "schema_version": SUMMARY_SCHEMA_VERSION,
    "accounted_for": True,
    "actor_exit_code": 0,
    "supervisor_exit": "clean",
    "boundaries": 42,
    "corrections": 3,
    "lapses": 0,
    "gaps": 0,
    "stale_verdicts_discarded": 2,
    "max_decision_lag_ms": 1250,
    "criterion_sha256": CRITERION_SHA256,
    "actor_event_log_sha256": "a" * 64,
    "supervisor_log_sha256": "b" * 64,
}


def test_the_config_document_is_exactly_the_schema_the_binary_reads() -> None:
  """Every key is one `config.rs` declares, and no key is missing.

  The binary deserializes with `deny_unknown_fields` and gives no field a
  default, so the document and the schema are the same set of keys or the run
  is refused after a sandbox exists.
  """
  document = _SUPERVISION.config_document(task="Fix the failing test.")

  assert document == {
      "schema_version": CONFIG_SCHEMA_VERSION,
      "task": "Fix the failing test.",
      "criterion": {"name": "general-practice", "sha256": CRITERION_SHA256},
      "policy": {
          "kind": POLICY_KIND,
          "budget": 3,
          "cooldown": 4,
          "window": 8,
          "judge_every_n_assistant_messages": 3,
          "block_actor_while_judging": True,
      },
      "model": {"name": "anthropic/claude-sonnet-5"},
      "timeouts": {"model_call_ms": 180000, "term_grace_ms": 10000},
      "limits": {
          "max_event_line_bytes": 16777216,
          "max_actor_stdout_bytes": 1073741824,
          "max_actor_stderr_bytes": 268435456,
      },
  }


def test_the_config_carries_no_endpoint_and_no_credential() -> None:
  """Where the model is and how to authenticate never reach the document.

  Both are the environment's, passed into the sandbox by reference. The
  document is written into the workspace and is a persisted artifact, so a
  credential in it is a credential on disk.
  """
  rendered = _SUPERVISION.config_bytes(task="Fix the failing test.").decode()

  # The absence below is only evidence if the document is a real one: an empty
  # or failed render contains no credential either.
  assert json.loads(rendered)["model"]["name"] == "anthropic/claude-sonnet-5"
  for forbidden in ("endpoint", "api_key", "base_url", "Authorization"):
    assert forbidden not in rendered


def test_the_criterion_is_named_by_the_pin_not_by_the_path_it_loaded_from(
    tmp_path: Path,
) -> None:
  """A renamed copy of the artifact still names the criterion the binary has.

  The digest identifies the criterion; the name selects it out of the ones
  compiled into the binary. Taking the name from the loaded path would let a
  renamed copy — same bytes, same digest, so `load_criterion` accepts it —
  render a name no binary carries, and the run would be refused at startup for
  a criterion that is in fact the right one.

  Args:
    tmp_path: Where the renamed copy is written.
  """
  renamed = tmp_path / "renamed-criterion.md"
  _ = renamed.write_bytes(CRITERION_PATH.read_bytes())

  document = _SUPERVISION.config_document(task="t", criterion_path=renamed)

  assert document["criterion"] == {
      "name": CRITERION_NAME,
      "sha256": CRITERION_SHA256,
  }


def test_a_forged_criterion_is_refused_before_a_config_exists(
    tmp_path: Path,
) -> None:
  """A criterion that is not the pinned one yields no config at all.

  The digest in the document is what the binary re-verifies its own embedded
  copy against, so rendering one off an unverified artifact would make that
  check agree with whatever drifted.
  """
  forged = tmp_path / "general-practice.md"
  _ = forged.write_text("judge them however you like\n")

  with pytest.raises(CriterionRejectedError):
    _ = _SUPERVISION.config_document(task="t", criterion_path=forged)


@pytest.mark.parametrize(
    "field,value",
    [
        # Out of the range the Rust type admits.
        ("model", ""),
        ("budget", -1),
        ("cooldown", -1),
        ("window", 0),
        ("judge_every_n_assistant_messages", 0),
        ("model_call_ms", 0),
        ("max_event_line_bytes", 0),
        # `NonZeroU64`: zero is refused by the type, as for the two above.
        ("max_actor_stdout_bytes", 0),
        ("max_actor_stderr_bytes", 0),
        ("budget", 2**32),
        ("window", 2**32),
        ("model_call_ms", 2**64),
        # The right range, the wrong JSON type. `True` is an `int` in Python
        # and would render as `true` into a slot that deserializes a number.
        ("budget", True),
        ("window", True),
        ("max_event_line_bytes", True),
        ("max_actor_stdout_bytes", True),
        ("max_actor_stderr_bytes", True),
        ("model", 7),
        ("block_actor_while_judging", "true"),
        ("window", 8.0),
    ],
)
def test_a_value_the_runtime_cannot_honour_is_refused_here(
    field: str, value: object
) -> None:
  """Each of the binary's own rules is applied where the value is chosen.

  The binary exits `3` on a bad config — after the sandbox is up and before the
  actor starts, which costs a container to learn. Type and range both, because
  `serde` refuses the wrong JSON type as firmly as an out-of-range number and
  Python stops neither.

  Args:
    field: The field to perturb.
    value: The unusable value to give it.
  """
  with pytest.raises(ValueError, match=field):
    _ = dataclasses.replace(_SUPERVISION, **{field: value})


def test_a_zero_shutdown_grace_is_accepted_because_the_binary_accepts_it() -> (
    None
):
  """`term_grace_ms` is a plain `u64` in `config.rs`, with no non-zero rule.

  Refusing it here would make this side stricter than the runtime it
  configures — a value a reader could set in the binary's own schema and not
  through the only thing that writes that schema.
  """
  rendered = dataclasses.replace(_SUPERVISION, term_grace_ms=0).config_document(
      task="t"
  )

  assert rendered["timeouts"]["term_grace_ms"] == 0


def test_the_task_is_checked_like_every_other_value_in_the_document() -> None:
  """`task` is an argument, not a field, and lands in the document all the same.

  `config.rs` deserializes it into a `String`; a number there is a document
  refused once the sandbox exists, which is the cost this validation avoids.
  Typed `Any` because that is the caller this guards — the one whose types
  nothing checked.
  """
  not_a_string: Any = 7

  with pytest.raises(ValueError, match="task"):
    _ = _SUPERVISION.config_document(task=not_a_string)


def _leaf_paths(document: object, prefix: str = "") -> set[str]:
  """Return every leaf of a JSON document, as a dotted path.

  Args:
    document: The document, or a subtree of one.
    prefix: The path to this subtree.

  Returns:
    One dotted path per value that is not itself an object.
  """
  if not isinstance(document, dict):
    return {prefix}
  found: set[str] = set()
  for key, value in document.items():
    child = f"{prefix}.{key}" if prefix else str(key)
    found |= _leaf_paths(value, child)
  return found


def test_every_value_in_the_document_is_pinned_or_validated() -> None:
  """No value reaches the binary on Python's word alone.

  Every leaf is one of four things: a constant this side pins, a digest read
  off the verified criterion artifact, the checked `task`, or a field checked
  against the Rust type it deserializes into. A value in none of those buckets
  is one whose refusal happens inside a paid-for sandbox.
  """
  document = _SUPERVISION.config_document(task="Fix the failing test.")

  validated = {
      "model": "model.name",
      "block_actor_while_judging": "policy.block_actor_while_judging",
      "budget": "policy.budget",
      "cooldown": "policy.cooldown",
      "window": "policy.window",
      "judge_every_n_assistant_messages": (
          "policy.judge_every_n_assistant_messages"
      ),
      "model_call_ms": "timeouts.model_call_ms",
      "term_grace_ms": "timeouts.term_grace_ms",
      "max_event_line_bytes": "limits.max_event_line_bytes",
      "max_actor_stdout_bytes": "limits.max_actor_stdout_bytes",
      "max_actor_stderr_bytes": "limits.max_actor_stderr_bytes",
  }
  # Each table entry has a home in the document, and each document leaf has a
  # rule — asserted as one equality so neither direction can rot alone.
  assert set(validated) == set(NUMERIC_FIELDS) | set(NON_NUMERIC_FIELDS)
  assert _leaf_paths(document) == set(validated.values()) | {
      "schema_version",  # pinned to CONFIG_SCHEMA_VERSION
      "policy.kind",  # pinned to POLICY_KIND
      "criterion.name",  # pinned to CRITERION_NAME
      "criterion.sha256",  # the digest load_criterion just computed
      "task",  # checked in config_document
  }


def test_every_configurable_field_is_checked_against_a_rust_type() -> None:
  """No field reaches the document without a rule that mirrors the schema.

  A field added to `NativeSupervision` and to no rule renders whatever Python
  accepted for it, and the refusal then happens in the sandbox.
  """
  checked = set(NUMERIC_FIELDS) | set(NON_NUMERIC_FIELDS)

  assert checked == {
      field.name for field in dataclasses.fields(NativeSupervision)
  }


def test_the_summary_reader_covers_every_field_of_the_summary() -> None:
  """`SUMMARY_FIELDS` and `TerminalSummary` name the same fields.

  The reader validates by walking the mapping, so a field added to the
  dataclass and not to it would be constructed from an unchecked value — or
  not constructed at all.
  """
  assert set(SUMMARY_FIELDS) == {
      field.name for field in dataclasses.fields(TerminalSummary)
  }


def test_a_well_formed_summary_reads_back_what_the_wrapper_wrote() -> None:
  """The happy path: every field arrives with its value."""
  summary = read_terminal_summary(json.dumps(_SUMMARY))

  assert isinstance(summary, TerminalSummary)
  assert summary.boundaries == 42
  assert summary.corrections == 3
  assert summary.stale_verdicts_discarded == 2
  assert summary.criterion_sha256 == CRITERION_SHA256


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, "no terminal summary"),
        ("{not json", "not JSON"),
        ("[]", "not an object"),
        ('{"schema_version": 2}', "schema_version"),
    ],
)
def test_a_summary_that_cannot_be_read_says_why(
    raw: str | None, expected: str
) -> None:
  """No unreadable summary is silently tolerated.

  Args:
    raw: What was found where the summary should be.
    expected: A phrase the reason has to carry.
  """
  summary = read_terminal_summary(raw)

  assert isinstance(summary, UnusableSummary)
  assert expected in summary.reason


def test_a_missing_or_mistyped_field_makes_the_summary_unusable() -> None:
  """A summary is read as a whole; a partial one is not a partial reading."""
  without_boundaries = {k: v for k, v in _SUMMARY.items() if k != "boundaries"}
  assert isinstance(
      read_terminal_summary(json.dumps(without_boundaries)), UnusableSummary
  )

  # `True` is an `int` in Python and is not one in the summary: a count that
  # arrived as a truth value is a producer disagreeing about the field.
  as_bool = _SUMMARY | {"boundaries": True}
  assert isinstance(read_terminal_summary(json.dumps(as_bool)), UnusableSummary)


def test_a_clean_run_reports_its_counts_and_no_failure() -> None:
  """Measurements are always present; events leave no key rather than a zero."""
  summary = read_terminal_summary(json.dumps(_SUMMARY))

  metrics = supervision_metrics(summary)

  assert metrics[BOUNDARIES_METRIC] == 42.0
  assert metrics[CORRECTIONS_METRIC] == 3.0
  assert metrics[SUPERVISION_MAX_DECISION_LAG_METRIC] == 1250.0
  assert metrics[SUPERVISION_STALE_METRIC] == 2.0
  assert SUPERVISION_METRIC not in metrics
  assert SUPERVISION_LAPSE_METRIC not in metrics


def test_a_run_is_classified_from_the_summary_and_not_from_the_exit_code() -> (
    None
):
  """An actor that exited `0` under supervision that broke is not a clean run.

  This is the whole reason the summary exists: a wrapper that ran cleanly exits
  with the actor's status, so exit `0` is compatible with having supervised
  nothing.
  """
  unaccounted = _SUMMARY | {
      "accounted_for": False,
      "actor_exit_code": 0,
      "gaps": 1,
      "supervisor_exit": "unclean",
  }

  metrics = supervision_metrics(read_terminal_summary(json.dumps(unaccounted)))

  assert metrics[SUPERVISION_METRIC] == 1.0
  # Still evidence-bearing counts: the wrapper described the hole it left.
  assert metrics[BOUNDARIES_METRIC] == 42.0


def test_a_run_with_no_summary_at_all_is_a_supervision_failure() -> None:
  """The wrapper not reporting is not the same fact as it reporting a hole."""
  metrics = supervision_metrics(read_terminal_summary(None))

  assert metrics == {SUPERVISION_METRIC: 1.0}


def test_named_lapses_are_counted_and_do_not_disqualify_the_run() -> None:
  """A bounded failure keeps the run's evidence value and carries its count."""
  lapsed = _SUMMARY | {"lapses": 4}

  metrics = supervision_metrics(read_terminal_summary(json.dumps(lapsed)))

  assert metrics[SUPERVISION_LAPSE_METRIC] == 4.0
  assert SUPERVISION_METRIC not in metrics
