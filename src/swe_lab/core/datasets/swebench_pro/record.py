"""SWE-Bench Pro dataset: its record type and parsing rules.

This module is specific to the SWE-Bench Pro parquet — its exact column set and
the quirks of how those columns are encoded live here, not in the generic
loader. A different dataset would get its own module with its own record type;
the loader stays dataset-agnostic (see ``loader.py``).

The parquet stores all columns as raw strings, but several encode richer
structure that we normalize on load:

- **List columns** (``fail_to_pass``, ``pass_to_pass``, ``issue_specificity``,
  ``issue_categories``, ``selected_test_files_to_run``) are the Python ``repr``
  of a ``list[str]``. They are *not* always valid JSON (``fail_to_pass`` mixes
  single and double quotes), so they are parsed with ``ast.literal_eval``.
- **Text columns** (``problem_statement``, ``requirements``, ``interface``) are
  stored inconsistently: in roughly half the rows the cell is a JSON string
  literal (outer quotes, escaped newlines) that must be decoded one level; in
  the other half it is already plain text. They are unwrapped only when they
  actually decode to a JSON string, so genuinely-raw text is never mangled.

All columns are preserved on the record, including ones the read-only annotation
flow does not use yet (``dockerhub_tag``, ``before_repo_set_cmd``), so future
repo-provisioning / agent modes can rely on them without a schema change.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from dataclasses import dataclass
import json
from typing import ClassVar

from .patches import patch_fail_to_pass

# The exact column set of the SWE-Bench Pro parquet, in file order. Used to
# validate that a raw row matches what this record type expects.
COLUMNS: tuple[str, ...] = (
    "repo",
    "instance_id",
    "base_commit",
    "patch",
    "test_patch",
    "problem_statement",
    "requirements",
    "interface",
    "repo_language",
    "fail_to_pass",
    "pass_to_pass",
    "issue_specificity",
    "issue_categories",
    "before_repo_set_cmd",
    "selected_test_files_to_run",
    "dockerhub_tag",
)


def _parse_list(raw: str) -> tuple[str, ...]:
  """Parse a Python-``repr`` list-of-strings column into a tuple."""
  value = ast.literal_eval(raw)
  if not isinstance(value, list):
    raise ValueError(f"Expected a list literal, got {type(value).__name__}")
  return tuple(str(item) for item in value)


def _unwrap_text(raw: str) -> str:
  """Return the decoded text, unwrapping a JSON string literal if present.

  Only values that both start with ``"`` and decode to a JSON ``str`` are
  unwrapped; anything else is returned verbatim so genuinely-raw text that
  merely happens to contain quotes is never mangled.
  """
  if raw.startswith('"'):
    try:
      decoded = json.loads(raw)
    except json.JSONDecodeError:
      return raw
    if isinstance(decoded, str):
      return decoded
  return raw


@dataclass(frozen=True, slots=True)
class SweBenchProInstance:
  """A single SWE-Bench Pro task instance with normalized, typed fields."""

  # Column set this record type is built from; consumed by the generic loader.
  COLUMNS: ClassVar[tuple[str, ...]] = COLUMNS

  repo: str
  instance_id: str
  base_commit: str
  patch: str
  test_patch: str
  problem_statement: str
  requirements: str
  interface: str
  repo_language: str
  fail_to_pass: tuple[str, ...]
  pass_to_pass: tuple[str, ...]
  issue_specificity: tuple[str, ...]
  issue_categories: tuple[str, ...]
  before_repo_set_cmd: str
  selected_test_files_to_run: tuple[str, ...]
  dockerhub_tag: str

  @classmethod
  def from_raw(cls, raw: Mapping[str, str]) -> SweBenchProInstance:
    """Build a ``SweBenchProInstance`` from one raw parquet row.

    ``raw`` must contain exactly the expected columns as strings.
    """
    missing = [c for c in COLUMNS if c not in raw]
    if missing:
      raise ValueError(f"Row is missing expected columns: {missing}")

    return cls(
        repo=raw["repo"],
        instance_id=raw["instance_id"],
        base_commit=raw["base_commit"],
        patch=raw["patch"],
        test_patch=raw["test_patch"],
        problem_statement=_unwrap_text(raw["problem_statement"]),
        requirements=_unwrap_text(raw["requirements"]),
        interface=_unwrap_text(raw["interface"]),
        repo_language=raw["repo_language"],
        # `fail_to_pass` is corrected in memory for the three instances whose
        # upstream names are truncated (see ``patches.py``); a no-op otherwise.
        fail_to_pass=patch_fail_to_pass(
            raw["instance_id"], _parse_list(raw["fail_to_pass"])
        ),
        pass_to_pass=_parse_list(raw["pass_to_pass"]),
        issue_specificity=_parse_list(raw["issue_specificity"]),
        issue_categories=_parse_list(raw["issue_categories"]),
        before_repo_set_cmd=raw["before_repo_set_cmd"],
        selected_test_files_to_run=_parse_list(
            raw["selected_test_files_to_run"]
        ),
        dockerhub_tag=raw["dockerhub_tag"],
    )
