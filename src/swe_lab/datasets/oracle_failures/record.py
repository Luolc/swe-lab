"""The oracle-failure record: one cached failure of an instance, runnable.

Both a ``DatasetRecord`` (the loader parses it out of the parquet
``build`` writes) and a ``TaskInstance`` (the workflows run it). The instance
half is **delegated, not copied**: the record holds the underlying dataset's
own record and forwards ``sandbox_spec`` / ``prompt`` / ``gold_patch`` /
``unit_test_spec`` and the rest to it, so the compile contract every dataset
implements is touched by nothing here. What the record adds is the failure —
the conversation, the verdict, the patch — contributed through ``mounts`` like
every other dataset's material (ADR-0007 §2).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import functools
import json
from typing import Any, ClassVar, override

from pydantic import ValidationError

from swe_lab.conversation import Conversation
from swe_lab.datasets.instance import TaskInstance
from swe_lab.evaluation.verdict import UnitTestSpec, Verdict
from swe_lab.sandbox import Inline, merge_mounts, Mount, Mounts, SandboxSpec
from swe_lab.sandbox.observers import BASE_REF_NAME, PATCH_NAME
from swe_lab.trace_synthesis.sample import (
    FAILED_CONVERSATION_NAME,
    FAILED_PATCH_NAME,
    FAILED_VERDICT_NAME,
)

DATASET_NAME = "oracle_failures"

# The parquet schema, in column order. `build` writes it, `from_raw` reads it;
# the tuple is the one home both assert against.
COLUMNS: tuple[str, ...] = (
    "dataset",  # the underlying dataset's registry name
    "instance_id",  # the underlying instance — also this record's id
    "rollout_id",  # which sample of the instance failed
    "conversation",  # the failed rollout's typed Conversation, as JSON
    "verdict",  # the grader's verdict on its patch, as JSON
    "patch",  # the patch it submitted
    "provenance",  # where the failure came from, as JSON
)


def describe_validation_error(error: ValidationError) -> str:
  """Describe a validation failure without quoting what was rejected.

  A pydantic error's own message embeds the input value it refused. For a
  column that may hold a whole conversation, that is exactly the text a
  refusal must not print — so only the error's *structure* is reported: where
  it happened and which kind of check failed.

  Args:
    error: The validation error.

  Returns:
    One line naming every location and error type, never an input value.
  """
  places = sorted(
      {
          "/".join(str(part) for part in item["loc"]) + f" ({item['type']})"
          for item in error.errors()
      }
  )
  return f"{error.error_count()} validation error(s): {', '.join(places)}"


@functools.cache
def _dataset(name: str) -> Any:
  """Load an underlying dataset once per process."""
  # Imported here, not at module scope: the loader's registry imports this
  # record type, so a top-level import would close the cycle.
  from swe_lab.datasets.loader import load_dataset

  return load_dataset(name)


def underlying_instance(dataset: str, instance_id: str) -> TaskInstance[Any]:
  """Resolve the instance a failure record delegates to.

  Args:
    dataset: The underlying dataset's registry name.
    instance_id: The instance within it.

  Returns:
    The underlying record, as a runnable instance.

  Raises:
    ValueError: If the dataset's record type is not runnable.
  """
  instance = _dataset(dataset).require(instance_id)
  if not isinstance(instance, TaskInstance):
    raise ValueError(
        f"dataset {dataset!r} has no runnable instances; an oracle failure"
        " can only delegate to a TaskInstance"
    )
  return instance


def _patch_base_ref(provenance: str) -> str | None:
  """Read the optional patch baseline without exposing provenance on error."""
  try:
    parsed = json.loads(provenance)
  except (json.JSONDecodeError, TypeError) as error:
    raise ValueError("oracle failure provenance is not valid JSON") from error
  if not isinstance(parsed, Mapping):
    raise ValueError("oracle failure provenance is not a JSON object")
  source = parsed.get("source")
  if not isinstance(source, Mapping):
    return None
  value = source.get("patch_base_ref")
  if value is not None and not isinstance(value, str):
    raise ValueError("oracle failure patch_base_ref is not text")
  return value


@dataclass(frozen=True)
class OracleFailureInstance(TaskInstance[Verdict]):
  """One cached failure of an instance from another dataset.

  The record's identity is the underlying instance's ``instance_id``: a run of
  this record lands in the store beside the runs of the instance it came from,
  and a dataset file holds one failure per instance (``build`` replaces).

  Every task run against this record stages the failure material — that is
  what ``mounts`` is for. A run that must *not* see it (a blind re-run of the
  same task) runs the underlying instance instead, which the delegation keeps
  in hand as :attr:`instance`.

  Attributes:
    COLUMNS: The parquet columns this record is parsed from.
    dataset: The underlying dataset's registry name.
    instance_id: The underlying instance's id, and this record's.
    rollout_id: Which sample of the instance failed.
    conversation: The failed rollout's typed ``Conversation``, as JSON text.
      Validated on load; kept as text because that is what gets staged.
    verdict: The grader's verdict on the failed patch, as JSON text.
    patch: The patch the failed rollout submitted.
    provenance: Where the failure came from — the run record's facts — as
      JSON text.
    instance: The underlying record, resolved on load; every part of the
      runnable surface except :meth:`mounts` forwards to it.
    patch_base_ref: The source rollout's pre-agent baseline ref, when it used
      patch-baseline extraction. Derived from provenance to keep the parquet
      schema compatible with existing rows.
  """

  COLUMNS: ClassVar[tuple[str, ...]] = COLUMNS

  dataset: str
  instance_id: str
  rollout_id: int
  conversation: str
  verdict: str
  patch: str
  provenance: str
  instance: TaskInstance[Any]
  patch_base_ref: str | None = None

  @classmethod
  def from_raw(cls, raw: Mapping[str, Any]) -> OracleFailureInstance:
    """Parse one parquet row and resolve the instance it delegates to.

    Args:
      raw: The row, keyed by column name.

    Returns:
      The record, bound to its underlying instance.

    Raises:
      ValueError: If the conversation column is not a typed ``Conversation``
        — a row a consumer could not stage is refused at load, like a
        malformed list column in any other dataset.
    """
    fields = {name: raw[name] for name in COLUMNS}
    try:
      _ = Conversation.model_validate_json(fields["conversation"])
    except ValidationError as error:
      raise ValueError(
          f"oracle failure {fields['instance_id']!r}: the conversation column"
          f" is not a typed Conversation: {describe_validation_error(error)}"
      ) from error
    fields["rollout_id"] = int(fields["rollout_id"])
    return cls(
        **fields,
        patch_base_ref=_patch_base_ref(fields["provenance"]),
        instance=underlying_instance(fields["dataset"], fields["instance_id"]),
    )

  @override
  def sandbox_spec(self) -> SandboxSpec:
    """Return the underlying instance's run context."""
    return self.instance.sandbox_spec()

  @override
  def required_tests(self) -> Sequence[str]:
    """Return the underlying instance's graded tests."""
    return self.instance.required_tests()

  @override
  def solution_sha(self) -> str | None:
    """Return the underlying instance's fix commit, if it records one."""
    return self.instance.solution_sha()

  @override
  def mounts(self) -> Mounts:
    """Stage the failure beside whatever the underlying instance stages.

    Returns:
      The underlying instance's mounts plus the three failure files and, when
      present, the patch baseline ref. All are read-only — a run reads a
      failure, it never amends one.
    """
    failure_mounts = {
        FAILED_CONVERSATION_NAME: _read_only(self.conversation),
        FAILED_VERDICT_NAME: _read_only(self.verdict),
        FAILED_PATCH_NAME: _read_only(self.patch),
    }
    if self.patch_base_ref is not None:
      failure_mounts[BASE_REF_NAME] = _read_only(self.patch_base_ref)
    return merge_mounts(
        self.instance.mounts(),
        failure_mounts,
    )

  @override
  def prompt(self) -> str:
    """Return the task statement the failed rollout was given."""
    return self.instance.prompt()

  @override
  def gold_patch(self) -> str | None:
    """Return the underlying instance's reference patch."""
    return self.instance.gold_patch()

  @override
  def unit_test_spec(
      self,
      *,
      apply_patch: bool,
      patch_name: str = PATCH_NAME,
      checkout_golden_tests: bool = True,
      patch_baseline: bool = False,
  ) -> UnitTestSpec[Verdict]:
    """Compile the underlying instance's unit-test spec for this failure.

    Args:
      apply_patch: Forwarded.
      patch_name: Forwarded.
      checkout_golden_tests: Forwarded.
      patch_baseline: Forwarded, except that grading a captured failed patch
        with a recorded baseline always restores that baseline first.

    Returns:
      The underlying dataset's spec with the source patch contract restored.
    """
    if patch_name == FAILED_PATCH_NAME and self.patch_base_ref is not None:
      patch_baseline = True
    spec: UnitTestSpec[Verdict] = self.instance.unit_test_spec(
        apply_patch=apply_patch,
        patch_name=patch_name,
        checkout_golden_tests=checkout_golden_tests,
        patch_baseline=patch_baseline,
    )
    return spec

  @override
  def run_provenance(self) -> dict[str, object]:
    """Return the underlying instance's facts, plus which failure this is.

    Returns:
      The underlying provenance with ``dataset`` naming this one, the
      underlying dataset under ``source_dataset``, and the failed rollout.
    """
    return {
        **self.instance.run_provenance(),
        "dataset": DATASET_NAME,
        "source_dataset": self.dataset,
        "failed_rollout_id": self.rollout_id,
    }


def _read_only(text: str) -> Mount:
  """Wrap text as a read-only inline mount."""
  return Mount(Inline(text.encode("utf-8")), read_only=True)
