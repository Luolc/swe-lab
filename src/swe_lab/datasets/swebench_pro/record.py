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
from dataclasses import asdict, dataclass
import json
import re
from typing import ClassVar, override

from swe_lab.datasets.instance import TaskInstance
from swe_lab.evaluation.verdict import UnitTestSpec
from swe_lab.sandbox import SandboxSpec

from .auxiliary import fetch_auxiliary
from .constants import IMAGE_REPO, PATCH_NAME, WORKDIR
from .fixes import applied_fix_name, apply_instance_fix
from .known_flaky import known_flaky
from .patches import patch_fail_to_pass
from .unit_test import compile_unit_test, SweBenchProVerdict

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


# The fix commit inside an instance id (`instance_<Org>__<Repo>-<sha>[-v...]`).
# Anchored on the 40-hex shape because repo names contain hyphens.
_FIX_SHA_RE = re.compile(r"-([0-9a-f]{40})(?:-|$)")


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
class SweBenchProInstance(TaskInstance[SweBenchProVerdict]):
  """A single SWE-Bench Pro task instance with normalized, typed fields.

  Both a ``DatasetRecord`` (the loader parses it) and a ``TaskInstance`` (the
  CLIs run it). The runnable surface — ``sandbox_spec`` / ``prompt`` /
  ``gold_patch`` / ``unit_test_spec`` plus the ``run_script`` / ``parser`` /
  ``golden_test_checkout_cmd`` properties — is where "how this instance is run"
  lives, so a CLI drives it without importing anything SWE-Bench-Pro-specific,
  and a downstream user can subclass to override how any of it is obtained.
  """

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

    Args:
      raw: One parquet row, with every expected column as a raw string.

    Returns:
      The parsed instance, with list and text columns normalized.

    Raises:
      ValueError: If the row is missing expected columns, or a list column
        does not hold a list literal.
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

  @property
  def run_script(self) -> bytes:
    """The test-harness run script for this instance (its content).

    How the harness is obtained is the instance's own business: the default
    fetches it from the upstream repo and caches it (see ``fetch_auxiliary``).
    **Override this (and ``parser``) in a subclass to source it another way** —
    e.g. embedded in a future dataset column — and grading then needs no network
    or repo checkout. Grading consumes only this content (never how it was
    obtained), so it depends on neither.
    """
    run_script_path, _ = fetch_auxiliary(self.instance_id)
    return run_script_path.read_bytes()

  @property
  def parser(self) -> bytes:
    """The output parser for this instance (its content); see ``run_script``."""
    _, parser_path = fetch_auxiliary(self.instance_id)
    return parser_path.read_bytes()

  @override
  def solution_sha(self) -> str | None:
    """Return the fix commit, read out of the instance id.

    SWE-Bench Pro names an instance
    ``instance_<Org>__<Repo>-<fix_sha>[-v<env_sha>]``, so the fix commit is
    already in hand — it is **not** ``base_commit``, which is the commit
    *before* the fix and is a separate column. Matched as the first
    40-hex-character token rather than by splitting on ``-``: repo names
    contain hyphens, and the optional ``-v`` suffix is sometimes ``nan``.

    Returns:
      The 40-character fix sha, or ``None`` if the id does not carry one.
    """
    match = _FIX_SHA_RE.search(self.instance_id)
    return match.group(1) if match else None

  @property
  def golden_test_checkout_cmd(self) -> str:
    """The command restoring the held-out golden test files after a reset.

    In SWE-Bench Pro this is the **last line** of ``before_repo_set_cmd`` — a
    block whose final line restores the golden tests by path, so a candidate
    patch cannot game them (Scale takes the same ``splitlines()[-1]``); ``""``
    when absent. Exposed as a property so ``_build_eval_script`` need not know
    where it comes from: a downstream dataset can store just this line (not the
    whole block), or a subclass can override how it is derived.
    """
    before = self.before_repo_set_cmd.strip()
    return before.splitlines()[-1] if before else ""

  @override
  def sandbox_spec(self) -> SandboxSpec:
    """Return the run context (image / workdir / base commit).

    A run context is a general sandbox spec, not a grading concern, so it is
    built here from the instance's own fields rather than in the unit-test
    module.
    """
    return SandboxSpec(
        instance_id=self.instance_id,
        image_ref=f"{IMAGE_REPO}:{self.dockerhub_tag}",
        workdir=WORKDIR,
        base_commit=self.base_commit,
    )

  @override
  def prompt(self) -> str:
    """Return the SWE-Bench-Pro task prompt handed to the agent.

    Mirrors Scale's ``create_problem_statement``: the three text columns are
    concatenated under fixed headers, unconditionally, so the agent sees the
    same task text the benchmark's own harness builds.
    """
    return (
        f"{self.problem_statement}\n\n"
        f"Requirements:\n{self.requirements}\n\n"
        f"New interfaces introduced:\n{self.interface}"
    )

  @override
  def gold_patch(self) -> str:
    """Return the instance's own (gold) patch."""
    return self.patch

  @override
  def unit_test_spec(
      self,
      *,
      apply_patch: bool,
      patch_name: str = PATCH_NAME,
      checkout_golden_tests: bool = True,
  ) -> UnitTestSpec[SweBenchProVerdict]:
    """Compile this instance's unit-test evaluation spec.

    Passes the instance's fields to the grading compiler directly, so the
    unit-test module need never import this record (the dependency is one-way).

    The compiled spec then goes through ``apply_instance_fix``, which is a no-op
    for all but the few instances whose *environment* is broken upstream (see
    ``fixes.py``). Applying it here rather than in each caller is what keeps
    grading and the golden self-check from disagreeing about which fixes ran.
    """
    spec = compile_unit_test(
        apply_patch=apply_patch,
        patch_name=patch_name,
        checkout_golden_tests=checkout_golden_tests,
        base_commit=self.base_commit,
        selected_test_files_to_run=self.selected_test_files_to_run,
        golden_test_checkout_cmd=self.golden_test_checkout_cmd,
        fail_to_pass=self.fail_to_pass,
        pass_to_pass=self.pass_to_pass,
        run_script=self.run_script,
        parser=self.parser,
    )
    return apply_instance_fix(self.instance_id, spec)

  @override
  def run_provenance(self) -> dict[str, object]:
    """Declare the harness fix applied here, and any measured flakiness.

    Both change how a result should be read: a fix means the graded tree is not
    quite the image's own, and a known-flaky entry means an unresolved verdict
    may say nothing about the patch. Empty for the overwhelming majority of
    instances.

    Returns:
      JSON-serializable facts (see ``fixes.py`` and ``known_flaky.py``).
    """
    provenance: dict[str, object] = {}
    fix = applied_fix_name(self.instance_id)
    if fix is not None:
      provenance["env_fix"] = fix
    flaky = known_flaky(self.instance_id)
    if flaky is not None:
      provenance["known_flaky"] = asdict(flaky)
    return provenance
