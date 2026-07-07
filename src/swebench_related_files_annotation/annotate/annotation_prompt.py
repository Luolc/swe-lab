"""The annotation prompt: the instruction given to a single annotation agent.

The agent is told to read-only-explore the checked-out repo, consult the hint
materials, and write a list of relevant code snippets to a fixed output file.
Read-only behavior is requested in the prompt (not enforced by tool
restrictions) because there is no Docker environment set up here, so building,
running tests, or modifying the repo would not work anyway.

The aggregator's prompt (which reconciles several such annotations) lives
separately in ``aggregator``.
"""

from __future__ import annotations

from ..datasets.swebench_pro import SweBenchProInstance
from .schema import SnippetCategory
from .workspace import ANNOTATION_OUTPUT, CONTEXT_DIR, VALIDATOR_SCRIPT

_CATEGORY_HELP = {
    SnippetCategory.REFERENCED_FUNCTION: (
        "a function/class/block the solution must call, use, or build on"
    ),
    SnippetCategory.CONTEXT_FILE: (
        "surrounding code needed to understand how the pieces fit, even if not"
        " directly called"
    ),
    SnippetCategory.USEFUL_UNIT_TEST: (
        "an existing test that reveals the expected behavior or contract"
    ),
    SnippetCategory.INTERFACE_CONTRACT: (
        "the required interface/signature/API the fix must conform to"
    ),
    SnippetCategory.SIMILAR_PATTERN: (
        "analogous code elsewhere to mirror when writing the fix"
    ),
}


def build_annotation_prompt(instance: SweBenchProInstance) -> str:
  """Return the full annotation instruction text for one instance."""
  categories = "\n".join(
      f"  - `{cat.value}` — {help_text}"
      for cat, help_text in _CATEGORY_HELP.items()
  )
  return f"""\
You are building ground-truth annotations for a SWE-bench task. Your job is NOT
to fix the bug. Your job is to identify the **code a competent engineer would
need to read** in order to solve this task correctly, as a list of precise code
snippets.

Your working directory is a checkout of `{instance.repo}` at the task's base
commit. IMPORTANT: there is no configured environment here (no Docker, no
installed dependencies). Do NOT build, run, install, execute tests, or modify
any repo files. Work **read-only**: read and search files to understand the
code.

Hint materials are in the `{CONTEXT_DIR}/` directory — consult them freely:
  - `problem_statement.md` — what the task requires.
  - `requirements.md`, `interface.md` — expected behavior and required API.
  - `gold_patch.diff` — the reference solution (a HINT for finding relevant
    code; the snippets you report are the code one must READ to arrive at such a
    fix, not a copy of the diff).
  - `test_patch.diff` — the tests that define success.
  - `git_log.txt` — recent history.

Deliverable: write a JSON file at `{ANNOTATION_OUTPUT}` in the working
directory. It must be an object with a `snippets` array. Each snippet is:
  - `file_path` — path relative to the repo root.
  - `start_line`, `end_line` — a contiguous, inclusive 1-based line range.
  - `category` — one of:
{categories}
  - `description` — one or two sentences: why this snippet must be read and what
    role it plays in solving the task.

Exact format (a minimal example):

    {{
      "snippets": [
        {{
          "file_path": "src/foo/bar.py",
          "start_line": 42,
          "end_line": 78,
          "category": "referenced-function",
          "description": "Bar.resolve() is on the bug's call path."
        }}
      ]
    }}

Guidance — aim for tight, consistent, reproducible snippets (two careful
reviewers should produce nearly the same set):
  - Select only the files a solver genuinely must read. Do not pad with
    peripheral files (e.g. localization, generated, or schema files) unless
    clearly necessary.
  - Range = the enclosing unit. Pick the tightest contiguous range that FULLY
    covers the relevant code — normally a whole function, method, class, or
    block, from its signature/definition line to its closing line.
  - Do NOT grab a whole *large* file when only part of it is relevant. But a
    *small, fully-relevant* file (e.g. a short module of related definitions)
    may be taken whole. If several separate regions of one file matter, emit one
    snippet per region.
  - Cover the whole relevant unit, not a sub-slice: if a function or test is
    relevant, include all of it, not just a few lines from the middle.
  - No trivial snippets: do NOT emit a snippet for a single import line or a
    one-line reference. Point at where a symbol is defined or substantively
    used, not merely imported. Prefer a few meaningful snippets over many tiny
    ones.
  - A single file may contribute multiple snippets when the relevant lines are
    non-contiguous; emit one snippet per contiguous range.
  - Report only real repository code; do NOT include files under
    `{CONTEXT_DIR}/`. Base line numbers on the files as they exist in this
    checkout.

Validate before finishing: after writing `{ANNOTATION_OUTPUT}`, run

    python3 {VALIDATOR_SCRIPT}

It checks every snippet (required fields, valid category, the file exists, and
the line range is within the file) and prints, in an agent-friendly way,
exactly what is wrong. It exits non-zero if there are problems. Fix your output
and re-run until it prints `OK`. Note on line numbers: use the line numbers
shown by the Read tool; a file that ends in a newline has one extra (empty)
final line, and the validator accepts that.

Only finish once the validator prints `OK`. `{ANNOTATION_OUTPUT}` is your only
deliverable — do not modify any other file."""
