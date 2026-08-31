---
name: swelab-pr-review
description: Repo-level PR review increments for swe-lab — the quality gate as acceptance test, invariant-needs-a-test, test scope, naming, doc source-of-truth, ADR placement, ask-first boundaries, docstrings and injection. Stacks on the user-level `pr-review` (`~/.agents/skills/pr-review`): the user-level skill owns the tracks, the priority axis and the Verdict format, this one only adds red lines and repo facts. Load both.
---

# swe-lab PR review (repo increments)

## Loading

- Depends on the user-level `pr-review` at `~/.agents/skills/pr-review`: this
  file has no tracks, no priority axis and no Verdict format of its own. If that
  skill is not loaded, **stop and report** instead of reviewing from here.
- Two conditions everything below leans on, restated: a **P0 must carry a
  failure scenario**, and **LGTM requires a non-empty `## Evidence`** on the
  code track.
- **Supersedes: none here.** The one supersede this repo declares (`P21`, paths)
  is in `swelab-python-review`, mounted for Python changes.
- Item ids are repo-prefixed — `SL*` here (`SLK*` is reserved for a checklist,
  which this file does not have) and `SLP*` in `swelab-python-review` — so they
  never collide with the user-level single-letter ids. Append-only: never
  renumbered, never reused. A supersede names the *user-level* id.

## Increments

- `SL1` **The quality gate is the acceptance test.** `uv run pre-commit run
  --all-files` and `uv run pytest`, run **bare** (a pipe swallows the exit code)
  in your own detached worktree at the PR head. Both exit 0 or there is no LGTM,
  and the commands with their results go in `## Evidence`. A green CI `check` is
  necessary, not sufficient.
- `SL2` **An invariant needs a test.** An *always / never / every path / exactly
  one* in a `spec.md`, an ADR or a docstring the PR adds or touches, with no
  named test that fails when it is violated → **P1**: add the test, or reword to
  "intended / today / not enforced".
- `SL3` **Test scope.** Happy path plus real business boundaries. A regression
  test is allowed only for a bug that actually happened, and lives in that bug's
  module. Negative assertions for obviously invalid input, sprayed across
  modules after one correction → ask for deletion.
- `SL4` **Naming.** An acronym is an ordinary word (`SweBenchProInstance`,
  `Http`, `JsonParser`); names are not clipped (`unit_test_spec`, never
  `unit_spec`). An established term or a deliberate short convention (`sb` for
  the narrow `SandboxFs` view) is fine.
- `SL5` **Code wins over docs, and each fact has one home.** Where a
  *provisional* doc and the code disagree, the code is right and the doc is the
  finding. A PR that changes behavior updates that behavior's single home —
  route with `docs/doc-map.md`, never write the same fact twice. Status belongs
  only in a component's `plans/README.md` or the `docs/README.md` snapshot.
- `SL6` **Decisions land in `docs/decisions/ADR-NNNN-*.md`** (this repo's
  directory name — not `docs/adr/`). An Accepted ADR is never edited to change
  its decision: a new ADR supersedes it. Re-litigating one in a PR is a finding.
- `SL7` **Ask-first boundaries are P0 without visible authorization.** Adding a
  runtime dependency; changing the annotation schema or the `EvalSpec` / report
  contract; touching or deleting anything under `outputs/` (a committed
  deliverable); re-hosting or renaming the HF dataset repos.
- `SL8` **Google-style docstrings, and inject built collaborators.** Docstrings
  are imperative ("Fetch rows…"), `Args:` matches the signature, types live in
  annotations only. An entry function takes the *built* dependency
  (`Task.execute(sandbox=…)`), not a name plus the construction knobs it feeds
  to a builder — a **design** finding, not a nit.

## Verdict example

```
Verdict: CHANGES_REQUESTED
Reviewed head: `1a2b3c4`

## Findings
pyproject.toml:34 | P0 | Adds the `httpx` runtime dependency; the PR shows no user authorization and `huggingface-hub` already carries an HTTP client | Ask first (`SL7`), or reuse the existing client
docs/workstreams/w2-solve-eval/spec.md:31 | P1 | "every attempt writes exactly one verdict" — no test fails if a second one is written | Add the named test, or reword to "intended, not enforced" (`SL2`)
src/swe_lab/cli/rollout.py:52 | P2 | `run_spec` clips the name of a `RunSpecification` value | Spell it out (`SL4`)

## Evidence
Detached worktree at 1a2b3c4: `uv run pre-commit run --all-files` exit 0; `uv run pytest` exit 0 (651 passed, 4 skipped).

## Out of scope
Formatting, the lock file, anything the quality gate already covers.
```
