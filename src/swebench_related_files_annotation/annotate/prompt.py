"""The instruction given to the headless annotation agent.

The agent is told to read-only-explore the checked-out repo, consult the hint
materials, and write a list of relevant code snippets to a fixed output file.
Read-only behavior is requested in the prompt (not enforced by tool
restrictions) because there is no Docker environment set up here, so building,
running tests, or modifying the repo would not work anyway.
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


def build_prompt(instance: SweBenchProInstance) -> str:
  """Return the full instruction text for one instance."""
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

Guidance:
  - A single file may contribute multiple snippets when the relevant lines are
    non-contiguous; emit one snippet per contiguous range.
  - Line ranges should be reasonable, not exhaustive — enough to cover the
    relevant code without spanning the whole file.
  - Do NOT include files under `{CONTEXT_DIR}/` in your snippets; report only
    real repository code.
  - Base line numbers on the files as they exist in this checkout.

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
