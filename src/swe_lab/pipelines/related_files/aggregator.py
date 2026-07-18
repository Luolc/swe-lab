"""Sample-and-aggregate: reconcile N candidate annotations into one.

Given several independent annotations of the same instance, an aggregator agent
reads the candidates + the task context + the real checkout and synthesizes a
single best annotation. This is the reliability path for the ambiguous cases a
single run varies on (test-coverage extent, whole-vs-focus on borderline files).

Like the annotator, this is a thin wrapper over :func:`agent_run.run_agent`,
differing only in the prompt and the extra ``candidates.json`` context file.
"""

from __future__ import annotations

from collections.abc import Sequence
import json
from pathlib import Path

from swe_lab.core.agent.trace import DEFAULT_CAPTURE
from swe_lab.core.datasets.loader import Dataset, load_dataset
from swe_lab.core.datasets.swebench_pro import SweBenchProInstance

from .agent_run import (
    DEFAULT_CLAUDE_TIMEOUT_S,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_MODEL,
    run_agent,
    RunResult,
)
from .workspace import ANNOTATION_OUTPUT, CONTEXT_DIR, VALIDATOR_SCRIPT

# Aggregator proxies live above the annotation port range so an aggregate can
# run alongside annotation rounds without colliding.
DEFAULT_AGG_BASE_PORT = 25000
CANDIDATES_FILE = "candidates.json"


def build_aggregator_prompt(repo: str, n_candidates: int) -> str:
  """Instruction for reconciling ``n_candidates`` annotations into one."""
  return f"""\
You are aggregating {n_candidates} independent candidate annotations of the same
SWE-Bench task into a single, best annotation. Each candidate is one attempt at
listing the code a solver must READ to fix the task; they may disagree.

Your working directory is a read-only checkout of `{repo}` at the base commit
(no environment configured — do not build, run, or modify anything). Task
context is in `{CONTEXT_DIR}/` (problem_statement, requirements, interface,
gold_patch.diff, test_patch.diff, git_log). The {n_candidates} candidates are in
`{CONTEXT_DIR}/{CANDIDATES_FILE}` — a list, each with a `snippets` array.

Decide what belongs by judgment, not by vote: for each candidate region ask
whether a solver truly needs to READ it to solve THIS task, using the problem
statement and gold patch as the ground truth. You MAY keep a good region only
one candidate found, and you MAY drop a region the majority included.

Reconcile into the single best set of snippets, each verified against the ACTUAL
repo files:
  - Files: include exactly the files a solver must read. Drop peripheral picks
    (localization / generated / schema) unless clearly needed.
  - Coverage: the final set must include every file the gold patch modifies that
    already EXISTS in this checkout (a UI template or config it edits counts) —
    even if some candidates missed it. Never include files the patch only
    creates; they do not exist here to read.
  - Ranges: choose the MOST APPROPRIATE contiguous range for each relevant unit
    — usually the whole function / method / class / block, but keep a little
    surrounding context when it genuinely aids understanding, and trim when
    candidates over-reach. It need not be the tightest possible; aim for a range
    a careful reviewer would agree with and reproduce.
  - File scope: include the parts of each file a solver must read for THIS task.
    Take a file whole only when the whole file is genuinely relevant; when only
    part of it is relevant, select the relevant unit(s) rather than the whole
    file.
  - Tests: include the complete test case(s) that define the expected behavior,
    but not the entire test file unless it is all relevant.
  - No trivial single-line import snippets.

Write the result to `{ANNOTATION_OUTPUT}` — an object with a `snippets` array;
each snippet has `file_path`, `start_line`, `end_line`, `category`
(referenced-function | context-file | useful-unit-test | interface-contract |
similar-pattern), and a one-sentence `description`. Then run

    python3 {VALIDATOR_SCRIPT}

and fix until it prints `OK`. Finish only then — the output file is your only
deliverable."""


def aggregate_instance(
    instance: SweBenchProInstance,
    index: int,
    candidates: Sequence[object],
    *,
    repo_root: Path | None = None,
    model: str = DEFAULT_MODEL,
    base_port: int = DEFAULT_AGG_BASE_PORT,
    port: int | None = None,
    variant: str = "agg",
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    claude_timeout: float = DEFAULT_CLAUDE_TIMEOUT_S,
    capture: str = DEFAULT_CAPTURE,
) -> RunResult:
  """Reconcile ``candidates`` (each an object with a ``snippets`` array)."""
  candidates_json = json.dumps({"candidates": list(candidates)}, indent=2)
  return run_agent(
      instance,
      index,
      prompt=build_aggregator_prompt(instance.repo, len(candidates)),
      kind="aggregate",
      context_files={CANDIDATES_FILE: candidates_json},
      extra_metadata={"n_candidates": len(candidates)},
      repo_root=repo_root,
      model=model,
      base_port=base_port,
      port=port,
      variant=variant,
      max_attempts=max_attempts,
      claude_timeout=claude_timeout,
      capture=capture,
  )


def aggregate_by_id(
    instance_id: str,
    candidates: Sequence[object],
    *,
    dataset: Dataset | None = None,
    model: str = DEFAULT_MODEL,
    base_port: int = DEFAULT_AGG_BASE_PORT,
    capture: str = DEFAULT_CAPTURE,
) -> RunResult:
  """Look an instance up by id and aggregate ``candidates`` for it."""
  dataset = dataset or load_dataset()
  record = dataset.require(instance_id)
  if not isinstance(record, SweBenchProInstance):
    raise TypeError(f"Unexpected record type: {type(record).__name__}")
  index = dataset.index_of(instance_id)
  return aggregate_instance(
      record,
      index,
      candidates,
      model=model,
      base_port=base_port,
      capture=capture,
  )
