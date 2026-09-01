"""Turn one finished ``rollout_and_unit_test`` run into an oracle-failure row.

A full sweep has already produced the failures phase B needs; this reads one
of them back out of the run's own output directory (``.cache/runs/<workflow>/
<instance_id>/``, or a copy of it) and writes the row the ``oracle_failures``
loader reads. Nothing is re-run. The verdict's per-test detail comes from the
dataset's **own grader** re-reading the persisted grading workspace, so the
row says exactly what the run's grade said, for any dataset.

Four gates stand between a run and a row, because the workflow's own exit
code distinguishes none of them: an unresolved verdict reads the same whether
the actor reasoned badly, was killed at its budget, or never started (measured
on an image that could not execute the agent binary at all). A run is a
failure sample only if the actor finished its work, was not timed out, and
exited cleanly — its grade was unresolved — and **every** recorded grading
attempt re-grades to the same verdict, so which tests fail is a fact about
the patch and not about a flaky suite. The re-grade is also checked against
the grade the run recorded: a persisted workspace that grades differently
from its own record is not the graded one. Anything else is refused, loudly,
and nothing is written.

The conversation and the patch are scanned for credential-shaped strings
before they become a dataset row, and a hit refuses the row naming only the
pattern — the value itself never reaches any output.

Run as::

    python -m swe_lab.datasets.oracle_failures.build --run-dir <dir>

with ``--dataset <name>`` naming the instance's dataset (default
``swebench_pro``) and ``--out <parquet>`` overriding where the row lands.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from dataclasses import dataclass
import datetime
import json
import re
from typing import Any, final, override

from etils import epath
import polars as pl
from pydantic import ValidationError

from swe_lab.conversation import Conversation
from swe_lab.datasets.instance import TaskInstance
from swe_lab.evaluation.verdict import Verdict
from swe_lab.paths import datasets_root
from swe_lab.sandbox import ExecResult, SandboxError, SandboxFs, SandboxSpec

from .record import (
    COLUMNS,
    DATASET_NAME,
    describe_validation_error,
    underlying_instance,
)

PARQUET_FILENAME = "oracle_failures.parquet"

# The polars schema `COLUMNS` is written with; `rollout_id` is the one
# non-text column.
SCHEMA: dict[str, type[pl.DataType]] = {
    name: (pl.Int64 if name == "rollout_id" else pl.String) for name in COLUMNS
}

# Artifact names the rollout entry is recognized by (the rollout composition's
# declared outputs), and the metric suffix a grading entry reports its answer
# under (every evaluation method reports `<method>.resolved`).
_CONVERSATION_ARTIFACT = "conversation.json"
_PATCH_ARTIFACT = "patch.diff"
_RESOLVED_SUFFIX = ".resolved"
_COMPLETE_METRIC = "agent_complete"

# Credential shapes that must never travel into a dataset row. Names only:
# a refusal message says which pattern fired, never what it matched.
_CREDENTIAL_PATTERNS: dict[str, re.Pattern[str]] = {
    "anthropic api key or oauth token": re.compile(r"sk-ant-[A-Za-z0-9_-]{8,}"),
    "openrouter api key": re.compile(r"sk-or-[A-Za-z0-9_-]{8,}"),
    "openai api key": re.compile(r"sk-proj-[A-Za-z0-9_-]{8,}"),
    "github token": re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    "hugging face token": re.compile(r"hf_[A-Za-z0-9]{20,}"),
    "bearer credential": re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]{20,}"),
}


class UnusableRunError(ValueError):
  """The run is not a usable failure sample; nothing was written."""


def scan_for_credentials(text: str) -> list[str]:
  """Name every credential pattern that matches somewhere in ``text``.

  Args:
    text: The content about to become part of a dataset row.

  Returns:
    The names of the patterns that fired, in declaration order; empty when
    none did. Never the matched values.
  """
  return [
      name
      for name, pattern in _CREDENTIAL_PATTERNS.items()
      if pattern.search(text)
  ]


@final
@dataclass(frozen=True)
class _PersistedWorkspace(SandboxFs):
  """A grading attempt's workspace directory, read back as a ``SandboxFs``.

  What a grader reads through — never executes against: the files a run left
  are evidence, and re-grading them is a pure read.
  """

  spec: SandboxSpec
  root: epath.Path

  @override
  def read(self, name: str) -> bytes:
    return (self.root / name).read_bytes()

  @override
  def exists(self, name: str) -> bool:
    return (self.root / name).is_file()

  @override
  def write(self, name: str, data: bytes, *, executable: bool = False) -> None:
    del name, data, executable
    raise SandboxError("a persisted workspace is read-only")

  @override
  def run_script(
      self,
      name: str,
      *,
      timeout: float,
      env: Mapping[str, str] | None = None,
  ) -> ExecResult:
    del name, timeout, env
    raise SandboxError("a persisted workspace cannot execute anything")

  @override
  def run_command(
      self,
      command: str,
      *,
      timeout: float,
      env: Mapping[str, str] | None = None,
  ) -> ExecResult:
    del command, timeout, env
    raise SandboxError("a persisted workspace cannot execute anything")


def _workflow_record(run_dir: epath.Path) -> tuple[epath.Path, dict[str, Any]]:
  """Locate and parse the run's one workflow record.

  Args:
    run_dir: The run's output directory.

  Returns:
    The record's path and its parsed body.

  Raises:
    UnusableRunError: If the directory holds no record, or more than one.
  """
  found = sorted(run_dir.glob("store/*/*/*/workflow.json"))
  if len(found) != 1:
    raise UnusableRunError(
        f"{run_dir} holds {len(found)} workflow record(s) under store/; a"
        " rollout_and_unit_test run leaves exactly one"
    )
  return found[0], json.loads(found[0].read_text())


def _entries(
    record: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
  """Pick the rollout and the grading entry out of the record.

  By what they produced, not by key: the rollout is the entry whose artifacts
  include the conversation and the patch, the grading entry the one whose
  metrics report a ``*.resolved`` answer.

  Args:
    record: The parsed workflow record.

  Returns:
    The rollout entry and the grading entry.

  Raises:
    UnusableRunError: If either is missing.
  """
  entries: list[dict[str, Any]] = list(record.get("entries", []))
  rollout = next(
      (
          e
          for e in entries
          if {_CONVERSATION_ARTIFACT, _PATCH_ARTIFACT}
          <= set(e.get("artifact_keys", {}))
      ),
      None,
  )
  grading = next(
      (
          e
          for e in entries
          if any(
              name.endswith(_RESOLVED_SUFFIX) for name in e.get("metrics", {})
          )
      ),
      None,
  )
  if rollout is None or grading is None:
    raise UnusableRunError(
        "the workflow record has no entry producing conversation.json +"
        " patch.diff, or none reporting a *.resolved metric; only a"
        " rollout_and_unit_test run can become a failure sample"
    )
  return rollout, grading


def _check_gates(
    record: Mapping[str, Any],
    rollout: Mapping[str, Any],
    grading: Mapping[str, Any],
) -> None:
  """Refuse anything that is not a finished actor graded unresolved.

  Args:
    record: The parsed workflow record.
    rollout: Its rollout entry.
    grading: Its grading entry.

  Raises:
    UnusableRunError: On the first gate that fails, saying which.
  """
  if record.get("succeeded") is not True:
    raise UnusableRunError(
        "the workflow did not succeed; a failed or blocked entry is an"
        " infrastructure outcome, not a reasoning failure"
    )
  metrics: Mapping[str, float] = rollout.get("metrics", {})
  if metrics.get(_COMPLETE_METRIC) != 1.0:
    raise UnusableRunError(
        f"{_COMPLETE_METRIC} is {metrics.get(_COMPLETE_METRIC)!r}, not 1.0:"
        " the actor did not finish its own work"
    )
  for name, value in metrics.items():
    if name.endswith(".timed_out") and value != 0.0:
      raise UnusableRunError(
          f"{name} is {value!r}: the actor was killed at its budget"
      )
    if name.endswith(".exit_code") and value != 0.0:
      raise UnusableRunError(
          f"{name} is {value!r}: the actor did not exit cleanly"
      )
  resolved = {
      name: value
      for name, value in grading.get("metrics", {}).items()
      if name.endswith(_RESOLVED_SUFFIX)
  }
  if any(value != 0.0 for value in resolved.values()):
    raise UnusableRunError(
        f"the grade resolved ({resolved}); this run is not a failure"
    )


def _artifact(run_dir: epath.Path, entry: Mapping[str, Any], name: str) -> str:
  """Read one of an entry's final-attempt artifacts out of the run's store."""
  key = entry["artifact_keys"][name]
  path = run_dir / "store" / key
  if not path.is_file():
    raise UnusableRunError(f"artifact {name!r} is recorded at {key} but absent")
  return path.read_text()


@final
@dataclass(frozen=True)
class _Grade:
  """A verdict as the JSON-ready facts the row keeps.

  Equality is what makes two grading attempts "the same verdict": the
  graders' summaries are deterministic (sorted lists, counts), so two
  attempts that failed the same tests compare equal.
  """

  resolved: bool
  score: float
  metrics: dict[str, float]
  summary: dict[str, object]

  @classmethod
  def of(cls, verdict: Verdict) -> _Grade:
    """Take the facts off a verdict."""
    return cls(
        resolved=verdict.resolved,
        score=verdict.score,
        metrics=verdict.metrics(),
        summary=verdict.summary(),
    )

  def as_row(self) -> dict[str, object]:
    """Return the facts as the verdict column's JSON object."""
    return {
        "resolved": self.resolved,
        "score": self.score,
        "metrics": self.metrics,
        "summary": self.summary,
    }

  def as_recorded(self, prefix: str) -> dict[str, float]:
    """Return the scalars the eval method would have recorded for this grade.

    Mirrors how the unit-test task namespaces a verdict into its metrics —
    ``<method>.score``, ``<method>.resolved`` and the verdict's own metrics
    under the same prefix.

    Args:
      prefix: The eval method's metric namespace (``unit_test``).

    Returns:
      The recorded-metric names this grade implies, with their values.
    """
    return {
        f"{prefix}.score": self.score,
        f"{prefix}.resolved": float(self.resolved),
        **{f"{prefix}.{name}": value for name, value in self.metrics.items()},
    }


def _check_recorded_grade(
    grading: Mapping[str, Any], workspace: epath.Path, grade: _Grade
) -> None:
  """Refuse a final workspace whose re-grade is not the grade the run recorded.

  Re-grading only says what the persisted files grade to *now*; the record
  says what they graded to *then*. A workspace that lost or changed a file
  since (an ``output.json`` gone missing grades as "nothing passed") still
  grades unresolved, so unresolved-ness alone proves nothing — every scalar
  the eval method recorded has to come back identical.

  Args:
    grading: The grading entry, with the metrics the run recorded.
    workspace: The final attempt's workspace, for the message.
    grade: Its re-derived grade.

  Raises:
    UnusableRunError: On any recorded scalar the re-grade does not reproduce.
  """
  recorded: Mapping[str, float] = grading.get("metrics", {})
  prefix = next(
      name[: -len(_RESOLVED_SUFFIX)]
      for name in recorded
      if name.endswith(_RESOLVED_SUFFIX)
  )
  mismatched = {
      name: (recorded.get(name), value)
      for name, value in grade.as_recorded(prefix).items()
      if recorded.get(name) != value
  }
  if mismatched:
    raise UnusableRunError(
        f"re-grading {workspace} disagrees with the recorded grade on"
        f" {mismatched} (recorded, re-derived); the persisted workspace is"
        " not the graded one"
    )


def _regrade(
    run_dir: epath.Path,
    instance: TaskInstance[Any],
    grading: Mapping[str, Any],
) -> _Grade:
  """Re-read every grading attempt's workspace with the dataset's grader.

  Args:
    run_dir: The run's output directory.
    instance: The underlying instance, whose grader judges the workspaces.
    grading: The grading entry (its key and attempt count locate the
      workspaces; its metrics are the grade the run recorded).

  Returns:
    The final attempt's grade, which names the failed tests.

  Raises:
    UnusableRunError: If a workspace is gone; if the final attempt's re-grade
      is not the grade the run recorded (see :func:`_check_recorded_grade`);
      or if the attempts do not all re-grade to the same verdict — the suite
      then retried into a different set of failed tests, and which tests
      fail is a property of the suite rather than of the patch.
  """
  grader = instance.unit_test_spec(apply_patch=True).grader
  spec = instance.sandbox_spec()
  attempts = int(grading["attempts"])
  workspaces = [
      run_dir / str(grading["key"]) / "ws" / f"a{attempt}"
      for attempt in range(attempts)
  ]
  grades: list[_Grade] = []
  for workspace in workspaces:
    if not workspace.is_dir():
      raise UnusableRunError(
          f"grading workspace {workspace} is gone; the verdict cannot be"
          " re-derived without every recorded attempt"
      )
    grades.append(_Grade.of(grader.grade(_PersistedWorkspace(spec, workspace))))
  final = grades[-1]
  _check_recorded_grade(grading, workspaces[-1], final)
  differing = [i for i, grade in enumerate(grades[:-1]) if grade != final]
  if differing:
    raise UnusableRunError(
        f"grading attempt(s) {differing} re-grade to a different verdict than"
        f" the final attempt {attempts - 1} (metrics"
        f" {[grades[i].metrics for i in differing]} vs {final.metrics});"
        " which tests fail is then a property of the suite, not of the patch"
    )
  return final


def build_row(run_dir: epath.PathLike, *, dataset: str) -> dict[str, Any]:
  """Build the dataset row for one finished run, or refuse.

  Args:
    run_dir: A ``rollout_and_unit_test`` run's output directory.
    dataset: The registry name of the dataset the instance belongs to.

  Returns:
    One row, keyed by :data:`~swe_lab.datasets.oracle_failures.COLUMNS`.

  Raises:
    UnusableRunError: If the run is not a usable failure sample (see the module
      docstring), or a credential pattern matches its content.
  """
  run_dir = epath.Path(run_dir)
  record_path, record = _workflow_record(run_dir)
  rollout, grading = _entries(record)
  _check_gates(record, rollout, grading)

  # The scan runs on the raw bytes, before anything parses them: a parser's
  # error message quotes the input it rejected, so parsing a credential-
  # bearing artifact first would print the credential on the way out.
  conversation = _artifact(run_dir, rollout, _CONVERSATION_ARTIFACT)
  patch = _artifact(run_dir, rollout, _PATCH_ARTIFACT)
  for label, text in (("conversation", conversation), ("patch", patch)):
    hits = scan_for_credentials(text)
    if hits:
      raise UnusableRunError(
          f"the {label} matches credential pattern(s) {hits}; refusing to"
          " build a dataset row from it"
      )
  try:
    _ = Conversation.model_validate_json(conversation)
  except ValidationError as error:
    raise UnusableRunError(
        "conversation.json is not a typed Conversation:"
        f" {describe_validation_error(error)}"
    ) from error
  if not patch.strip():
    raise UnusableRunError(
        "the failed rollout's patch is empty; nothing to analyze"
    )

  instance_id = str(record["instance_id"])
  instance = underlying_instance(dataset, instance_id)
  grade = _regrade(run_dir, instance, grading)
  # No host path: a run directory carries the operator's username on an
  # ordinary workstation, and a trace record redacts operator PII at write
  # time. The run is identified by its own store key and timestamp instead.
  provenance = {
      "source": {
          "workflow_record": str(record_path.relative_to(run_dir)),
          "sweep_id": record.get("sweep_id"),
          "run_ts": record.get("run_ts"),
          "rollout_entry": rollout["key"],
          "grading_entry": grading["key"],
          "grading_attempts": grading["attempts"],
      },
      "rollout_metrics": dict(rollout.get("metrics", {})),
      "grading_metrics": dict(grading.get("metrics", {})),
      "built_at": datetime.datetime.now(datetime.UTC).isoformat(
          timespec="seconds"
      ),
  }
  return {
      "dataset": dataset,
      "instance_id": instance_id,
      "rollout_id": int(record["rollout_id"]),
      "conversation": conversation,
      "verdict": json.dumps(grade.as_row(), indent=2, sort_keys=True),
      "patch": patch,
      "provenance": json.dumps(provenance, indent=2, sort_keys=True),
  }


def write_row(path: epath.PathLike, row: Mapping[str, Any]) -> bool:
  """Add a row to the dataset parquet, replacing the instance's earlier row.

  One failure per instance per dataset file: re-building an instance means
  re-building it, exactly as re-running a command means re-running it. The
  file's identity is the instance id alone — that is what the loader indexes
  by, and what a run's store key carries — so a row of the **same id from
  another source dataset** is a collision, not a replacement, and is refused:
  silently dropping the other source's row would be the wrong kind of quiet.

  Args:
    path: The parquet to write (created, with its parents, when absent).
    row: The row from :func:`build_row`.

  Returns:
    Whether an earlier row for the same instance was replaced.

  Raises:
    UnusableRunError: If the file already holds this instance id from a
      different source dataset.
  """
  path = epath.Path(path)
  fresh = pl.DataFrame([dict(row)], schema=SCHEMA)
  replaced = False
  if path.is_file():
    existing = pl.read_parquet(str(path))
    same_id = existing.filter(pl.col("instance_id") == row["instance_id"])
    foreign = same_id.filter(pl.col("dataset") != row["dataset"])
    if foreign.height:
      raise UnusableRunError(
          f"{path} already holds instance {row['instance_id']!r} from"
          f" dataset {foreign['dataset'][0]!r}; a same-id row from"
          f" {row['dataset']!r} would collide (the file is indexed by instance"
          " id) — give the other source its own file with --out"
      )
    kept = existing.filter(pl.col("instance_id") != row["instance_id"])
    replaced = kept.height != existing.height
    fresh = pl.concat([kept, fresh])
  path.parent.mkdir(parents=True, exist_ok=True)
  fresh.write_parquet(str(path))
  return replaced


def default_output() -> epath.Path:
  """Return the parquet path the loader reads (``datasets/<name>/data/``)."""
  return datasets_root() / DATASET_NAME / "data" / PARQUET_FILENAME


def main() -> None:
  """Build one row and write it."""
  parser = argparse.ArgumentParser(
      description="Turn one finished rollout_and_unit_test run into an"
      " oracle_failures row."
  )
  _ = parser.add_argument(
      "--run-dir", required=True, help="the run's output directory"
  )
  _ = parser.add_argument(
      "--dataset",
      default="swebench_pro",
      help="the dataset the instance belongs to",
  )
  _ = parser.add_argument(
      "--out",
      default=None,
      help=f"the parquet to write (default: {default_output()})",
  )
  args = parser.parse_args()
  out = epath.Path(args.out) if args.out else default_output()
  try:
    row = build_row(args.run_dir, dataset=args.dataset)
  except UnusableRunError as error:
    raise SystemExit(f"refusing to build: {error}") from error
  replaced = write_row(out, row)
  verb = "replaced" if replaced else "added"
  print(f"{verb} {row['instance_id']} (rollout {row['rollout_id']}) in {out}")


if __name__ == "__main__":
  main()
