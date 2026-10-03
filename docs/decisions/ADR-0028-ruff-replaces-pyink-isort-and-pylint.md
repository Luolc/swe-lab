# ADR-0028: Ruff replaces pyink, isort and pylint; basedpyright and pydoclint stay

## Status

Accepted. The owner decided the two open questions of the exploration on
2026-10-03 (continuation indent, how strictly 80 columns is enforced; see
Decision 2 and 3). The migration itself landed first, in
[#471](https://github.com/Luolc/swe-lab/pull/471) (`46d7f25`); this record
follows it. The exploration that measured everything below is
[#470](https://github.com/Luolc/swe-lab/pull/470) (draft, not merged; its
candidate head is `2a30cd4`).

Supersedes no ADR: the previous toolchain was chosen in
[horizontal task 01](../horizontal/plans/task-01-google-style-readability.md),
which is a completed task's design record, not an ADR.

## Date

2026-10-03

## Context

On `6247ad6` (the `main` the exploration measured), pre-commit ran these
Python tools. Versions are the ones inside each hook's own environment, which
is what actually checks the code; the project venv had newer ruff (0.15.20)
and basedpyright (1.39.9), so an editor using the venv could disagree with the
hooks.

| tool | job | hook version |
| --- | --- | --- |
| pyink | formatter: 2-space block indent, **4-space bracket continuation**, `pyink-use-majority-quotes` | 25.12.0 |
| isort | import sorting (`profile = "black"` plus `force_sort_within_sections`, `lexicographical`, `group_by_package`, …) | 8.0.1 |
| ruff | lint only; its formatter was excluded from every `.py` | 0.15.7 |
| basedpyright | type checking | 1.39.0 |
| pydoclint | docstring `Args:`/`Returns:`/`Raises:` against the signature | 0.9.1 |

**pylint was a leftover dependency that nothing ran.** It came in with the
project's initial commit (`955371b`) and sat in `pyproject.toml` at 4.0.6.
Under `strace -f -e trace=execve,openat`, neither
`uv run pre-commit run --all-files` nor the `pytest -m 'not docker'` run opened
a file under `site-packages/pylint/` or exec'd anything named pylint (0 and 0),
while the same traces opened pyink 23 times, isort 38 times and `_pytest` 39 /
82 times. Every `pylint` string in the traces was dist-info metadata read by uv
or by pytest's entry-point scan. So the Google pylintrc was never in force
either, and "the toolchain is strict Google pylint" was not true.

Three tools (pyink, isort, ruff) did what Ruff 0.16.10 does alone, and the
owner's long-term direction is the Astral toolchain throughout: uv, Ruff and,
when it is ready, ty.

## Decision

1. **Ruff 0.16.10 is the formatter, the import sorter and the linter.**
   pre-commit runs `ruff-check` (lint plus `I`, with `--fix`) and then
   `ruff-format`. The version is pinned in both places that can run it: the
   hook (`rev: v0.16.10`) and the venv (`ruff>=0.16.10`). pyink, isort and
   pylint are removed from the dependencies and `uv.lock`, and
   `[tool.pyink]` / `[tool.isort]` are deleted.

2. **Continuation indent is 2 spaces, like block indent.** Ruff's formatter
   has one `indent-width`; there is no separate setting for bracket
   continuation, so pyink's "block 2, continuation 4" cannot be kept. The
   owner accepted 2 on 2026-10-03. Ruff's maintainers have said they do not
   intend to add such an option (both read on 2026-10-03):
   - [discussion #8827](https://github.com/astral-sh/ruff/discussions/8827),
     "Different indent width for line indent vs hanging indent": "Ruff's
     formatter shouldn't support this option in my view because its
     philosophy is to be an opinionated formatter and supporting the above
     feature would require adding a new option which we try to avoid."
   - [discussion #10191](https://github.com/astral-sh/ruff/discussions/10191),
     "Formatter: Allow configurable soft-indent width": "I don't think that we
     would add such an option today. We consider adding more option in the
     future but our primary aim for now is black compatibility."

3. **80 columns is Ruff's E501, with its exemptions.** E501 does not flag a
   line whose overflow is a URL, a single token that cannot be split, or a
   pragma comment. The owner's rule (2026-10-03): what genuinely cannot be
   split stays unsplit. There is no stricter check on top of it.

4. **Two lint additions ride along.** `BLE` (blind except) and `PLW0129`
   (assert on a string literal) join `select`.

5. **basedpyright stays the type checker and pydoclint stays the docstring
   checker** (reasons below). ty is not adopted yet; the conditions for
   re-evaluating it, and the commands to re-run the comparison, are under
   *ty: when to look again*.

The Ruff configuration lives in `pyproject.toml` (`[tool.ruff]` and its
subtables) and the hooks in `.pre-commit-config.yaml`; those files are the
source of truth for the current settings, and
[`docs/conventions.md`](../conventions.md) describes them.

## What changes, and what it costs

### Continuation indent

This is where nearly all of the diff came from. The mechanical reformat commit
(`0297796`, `ruff check --fix` plus `ruff format`) touched 199 files,
+10729/−10859; with `git diff -w` it is 54 files, +86/−216. Ruff converges:
on the config-only commit `bc89a69`, the first pass made 3 lint fixes and
reformatted 200 files, and the second pass changed nothing.

Most of the non-whitespace hunks follow from the indent too: a continuation
line two columns shorter often fits on one line, so Ruff joins it (45 of the 76
hunks, by a rough heuristic classification).

### 80 columns keeps its exemptions

The same 21 lines exceed 80 characters before and after the migration (counted
in characters, not bytes, over every tracked `.py`/`.pyi`): 21 on `6247ad6` and
21 on #471's head `df0daa3`, matching line for line apart from three
`patches.py` keys whose quotes changed. They fall in five groups: 10 lines of
verbatim test ids in `src/swe_lab/datasets/swebench_pro/patches.py` (up to 340
characters, under the one file-level `# ruff: noqa: E501` in the repo), 3 URLs
in strings, 2 URLs in comments, 4 trailing `# noqa:` reasons and 2 Sphinx
cross-references in docstrings. The formatter created no new long line.

The rejected alternative was a local hook with no exemptions at all
(#470 built one, a 29-line `tests/max_line_length.py`, because pre-commit's
`pygrep` matches bytes and the repo's em dashes are 3 bytes each). Meeting it
meant rewriting those 21 lines: URLs split across adjacent literals no longer
open from the editor, a split test id can no longer be found by grepping for
it, and a permalink becomes a path to reassemble by hand, and every future
long URL would need the same treatment. Google's own pylintrc also exempts
URL-only and import lines (`ignore-long-lines`).

### Quotes are double everywhere

pyink chose quotes per file by majority. Ruff has no such mode, so
`quote-style` is its default, `"double"`. On `6247ad6`, a tokenize count of
non-triple-quoted strings found 9947 double-quoted and 265 single-quoted; by
file, 202 files were mostly double, 1 mostly single (`patches.py`) and 12 tied
(mostly files without strings). So the default matches what the repo already
did.

### Other formatting differences

- Ruff joins implicitly concatenated strings into one literal when the result
  fits on a line, which pyink left alone (8 hunks, plus some in the
  uncategorized remainder). The parentheses that wrapped them are not removed;
  #471 removed the two this produced by hand.
- Ruff formats expressions inside f-strings (target version 3.12 or later;
  one hunk).
- Ruff 0.16 also formats ```` ```python ```` blocks in Markdown, and the
  `ruff-format` hook's `types_or` includes `markdown`, so it would rewrite 27
  of the repo's `.md` files, `outputs/` deliverables among them.
  `[tool.ruff.format] exclude = ["*.md"]` keeps the formatter on Python
  source as before.

### Import order

isort's options map one to one onto `[tool.ruff.lint.isort]` except
`lexicographical` and `group_by_package`, which have no Ruff equivalent.
`force-sort-within-sections` already sorts by module path, so the result
differs in two cases only: `import a.b as c` and `from a.b import d` on the
same module (Ruff puts the `import` first; isort accepts that order too), and a
`from a.b import X as Y` between two plain imports from the same module (Ruff
merges the plain ones, isort kept three statements; one file, no option
restores it).

### Lint

- `BLE` makes 13 existing `# noqa: BLE001` comments mean something; they had
  been inert because the rule was never selected. Enabling it found 0
  violations.
- `PLW0129` found a real bug: an `assert '<string>'` in
  `tests/test_deepswe_dataset.py` that was missing `in script` and so always
  passed. Ruff (as configured then) and basedpyright did not report it; pylint
  with the Google rc and ty did. It is fixed, and the test still passes.
- Not added: the whole `PL` group (`PLC0415` alone reports 137 deferred
  imports, which the repo uses on purpose), `RUF100` (22 hits; the stale noqa
  markers need a decision first), `PLW1514` (open without encoding: a real gap,
  but preview in 0.16.10; worth enabling once it is stable) and `S` (2508 hits,
  mostly `assert` in tests).

### Time

With a warm cache the full `pre-commit run --all-files` barely moves (17.72 s
before, 17.29 s after, local machine, 3 runs each); basedpyright is most of it.
The difference shows without a cache, which is what CI gets: `pyink --check`
took 6.75–7.77 s cold, `ruff format --check --no-cache` 0.06–0.07 s, and
`isort --check-only` 0.61–0.66 s against a Ruff check of 0.07 s. CI timings
were not measured.

## Why basedpyright and pydoclint stay

**pydoclint.** Ruff's `D` rules and pydoclint check different things: `D`
checks that a docstring exists and is well formed; pydoclint checks that its
`Args:`, `Returns:` and `Raises:` agree with the signature. Ruff's `DOC` rules
are a port of pydoclint, but in 0.16.10 all 7 are preview and none covers an
argument missing from `Args:`. On four constructed defects (a deleted argument
still documented, arguments in the wrong order, missing `Returns:`, missing
`Raises:`):

| check | defects reported |
| --- | --- |
| Ruff stable rules, this repo's `D` config | 0 / 4 |
| Ruff `--preview --select DOC` | 3 / 4 (missed the order) |
| pydoclint 0.9.1, this repo's arguments | 4 / 4 |

On `6247ad6`, pydoclint reports nothing while preview `DOC` reports 216 (182
missing-returns, 31 missing-exception, …). pydoclint by default skips a
docstring that is only a one-line summary, which is what the style guide allows
(§3.8.3: sections may be omitted when the summary suffices); Ruff's `DOC` does
not skip it. **Re-evaluate** when Ruff's `DOC` rules are stable and can skip
one-line-summary docstrings.

**basedpyright.** ty, the Astral type checker, was compared side by side and is
not ready to replace it; the next section has the readings and what would
change the answer.

## ty: when to look again

### Readings (the baseline for any re-run)

Same SHA `6247ad6d24e25dc92085d61b0c6c053a0807a325`, in a throwaway detached
worktree with `uv sync`; ty run through `uvx` with no `[tool.ty]` config (it
found the project `.venv`, no unresolved imports); basedpyright from its hook
environment reading `[tool.basedpyright]`. Python 3.13.15.

| | ty 0.0.84 | basedpyright 1.39.0 |
| --- | --- | --- |
| time (3 runs) | 0.83 / 0.83 / 0.72 s | 13.14 / 17.77 / 14.26 s |
| exit code | 1 | 0 |
| diagnostics | 37 (30 error, 7 warning; 1 in `src`, 36 in `tests`; identical across the 3 runs) | 0 (215 files) |

30 of the 37 sit on 6 lines that carry `# pyright: ignore[...]`, which ty does
not honour (it reads `# type: ignore` and `# ty: ignore`). With all 9 such
comments removed (8 files, in the throwaway worktree, restored afterwards) and
diagnostics compared by file and line: basedpyright 9 lines, ty 13, both 6.
basedpyright alone: 3 (`reportUnusedFunction` on two decorator-registered
autouse fixtures, `reportImplicitAbstractClass` once; basedpyright-specific
rules with no ty counterpart). ty alone: 7, all `redundant-condition`, of
which 6 are the repo's `assert _definitions.ROLLOUT_KEY  # the import above is
for its side effect` idiom (noise) and 1 is the always-true assert that
`PLW0129` now catches.

### Why not now

1. ty is a 0.0.x beta, and diagnostics may change between any two versions;
   CI needs stable diagnostics. (The beta status, the release date and the
   README wording behind this point were relayed from the owner's own check
   and not re-verified by the exploration.)
2. The 9 `# pyright: ignore[...]` comments would need rewriting, or ty adds 30
   errors.
3. ty has no counterpart for basedpyright's `reportUnusedFunction` and
   `reportImplicitAbstractClass`.
4. `redundant-condition` flags the import-for-side-effect-then-assert idiom;
   either the idiom or the rule would have to go.

### Conditions

Re-run the comparison when **any** of these holds:

- ty releases 1.0, or its documentation or changelog declares diagnostics and
  configuration a stable API;
- ty reads `# pyright: ignore[...]`, or ships a tool that converts them to
  `ty: ignore` in bulk;
- ty gains rules corresponding to `reportUnusedFunction` and
  `reportImplicitAbstractClass`.

### Re-run commands

Baseline: SHA `6247ad6d24e25dc92085d61b0c6c053a0807a325`, ty 0.0.84,
basedpyright 1.39.0, Python 3.13.15; ty 37 diagnostics, basedpyright 0; with
the suppression comments stripped, 6 lines in common, 3 basedpyright-only, 7
ty-only.

```sh
# 1. A throwaway worktree on the same SHA (delete it when done)
git -C ~/dev/swe-lab worktree add --detach ~/wt/swe-lab/ty-recheck 6247ad6d24e25dc92085d61b0c6c053a0807a325
cd ~/wt/swe-lab/ty-recheck && uv sync

# 2. As-is comparison (<new version> is the ty version of the day; basedpyright is the version .pre-commit-config.yaml pins then)
uvx --from ty==<new version> ty check --output-format concise > ty.txt; echo "ty exit=$?"
uvx --from ty==0.0.84 ty check --output-format concise > ty-0.0.84.txt; echo "ty 0.0.84 exit=$?"   # control arm: should reproduce the 37
uv run pre-commit run basedpyright --all-files; echo "basedpyright exit=$?"

# 3. Strip the pyright-specific suppressions and compare line by line again (only in this throwaway worktree)
git grep -l 'pyright: ignore' -- '*.py' | xargs sed -i -E 's/  # pyright: ignore\[[A-Za-z, ]+\]//'
uvx --from ty==<new version> ty check --output-format concise > ty-stripped.txt
basedpyright --outputjson > bp-stripped.json   # the executable inside the pre-commit hook environment
git checkout -- .

# 4. Clean up
cd ~ && git -C ~/dev/swe-lab worktree remove ~/wt/swe-lab/ty-recheck
```

Run steps 2 and 3 again on the `main` HEAD of the day as well: whether ty can
replace basedpyright is decided on the code as it is then; the `6247ad6`
re-run only shows how much ty itself has changed.

## Alternatives Considered

- **Keep pyink for formatting, use Ruff only for imports and lint.** The only
  way to keep a 4-space continuation indent. Rejected: three tools where one
  does the job, for an indent the owner was willing to give up.
- **A strict 80-column hook with no exemptions.** Built and measured in #470;
  rejected by the owner for the costs listed above.
- **Replace pydoclint with Ruff's `DOC` rules.** Preview, misses a defect
  class, and floods the repo with 216 reports pydoclint rightly skips.
- **Replace basedpyright with ty now.** See *Why not now*.

## Consequences

- One tool and one pinned version for formatting, import order and lint, in
  the hook and in the venv alike.
- Every bracket continuation is now 2 spaces; code written to the old
  4-space habit is reformatted by the hook.
- "Every line is at most 80 characters" is not literally true: 21 lines
  exceed it today, each in an E501 exemption or under the one file-level noqa.
  A new long line passes only inside an E501 exemption or with a `noqa`.
- `PLW1514` (open without encoding) and `RUF100` (stale noqa) remain known,
  unaddressed gaps.
- Moving to ty is a separate decision for a future ADR, taken on a re-run of
  the commands above once a condition holds.

## What was measured, and what was not

Measured, with the command and SHA in #470's report: the hook versions and file
set, pylint never being invoked (strace with control arms), the isort/Ruff
difference in both directions, formatter convergence, the long-line counts,
the Markdown exclusion (both arms), pylint with the Google rc on the baseline,
the `D` / `DOC` / pydoclint samples, local timings, the quality bar, and the
ty/basedpyright comparison. #471 re-measured the long-line count and
convergence on its own head.

Not verified:

- CI timings of the individual hooks (only the local machine was timed).
- ty 0.0.84's release date, classifier and README wording (relayed, not
  checked).
- The Google Python Style Guide was not re-read section by section; the lint
  comparison was made by running the Google pylintrc, not against the guide's
  text.
