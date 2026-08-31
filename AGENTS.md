# AGENTS.md

Working rules for AI agents in this repo.

The project map — roadmap, status, and where everything lives — is
[`docs/README.md`](docs/README.md). Read it before starting work; it points to
everything else. For **which doc answers which question, and where a new
learning belongs**, see [`docs/doc-map.md`](docs/doc-map.md) — every fact has
exactly one home; other docs link to it, never copy it.

## How we work

Two modes, and the mode picks the method:

- **Building** (a feature or change): follow the lifecycle **spec → plan →
  build → review → ship**, guided by the skills in
  [`.agents/skills/`](.agents/skills/) (`spec-driven-development`,
  `planning-and-task-breakdown`, `incremental-implementation`,
  `test-driven-development`, `shipping-and-launch`; the canonical copies live
  there and `.claude/skills/<name>` are per-skill directory symlinks into it),
  with test-driven development and small atomic commits as the default.
  **Review** = user-level `pr-review` (`~/.agents/skills/pr-review`) + this
  repo's `swelab-pr-review`; Python changes additionally load user-level
  `python-review` + `swelab-python-review`. **An active component owns its
  planning docs in its own folder** — a workstream (`docs/workstreams/<w>/`) or
  the horizontal `docs/horizontal/` for cross-cutting / foundational work:
  - `spec.md` — the target design (what we're building and why).
  - `plan.md` — the **strategy** (phases, dependency graph, risks, DoD,
    checkpoints); it does **not** enumerate tasks.
  - `plans/` — one **deep, source-grounded design per task**; `plans/README.md`
    is the **ordered task index + status** (the checklist). There is **no**
    separate `todo.md`.

  A non-trivial effort starts from `spec.md`; add a missing task to
  `plans/README.md` (and a `plans/task-NN-*.md` when it needs design). A per-task
  plan may be *forward-looking* (design before code) or *retrospective*
  (document existing code) — for a large redesign, write the ideal target design,
  not a record of the old implementation.
- **Experimenting** (learning something — a prompt, variance, a failure, "is X
  worth building?"): follow the
  **[experiment playbook](docs/experiments/playbook.md)** — hypothesis → logged,
  timestamped run → empirical results → attributable conclusion → a `REPORT.md`.
  This is the ML side the coding skills don't cover. An experiment's report
  *feeds* a spec or a decision; don't build straight from a hunch.

Before touching code, read [`docs/conventions.md`](docs/conventions.md) (codebase
map, commands, hazards). **Source-of-truth rule:** where a doc and the code
disagree, the **code wins** unless the doc is explicitly the spec being
implemented; a doc known to have drifted is superseded or demoted — e.g.
`docs/patch-extraction.md` is non-authoritative background and the
patch-extraction decisions are settled in
[ADR-0001](docs/decisions/ADR-0001-patch-extraction-and-grading.md). Record
decisions worth remembering in [`docs/decisions/`](docs/decisions/) — and **don't
re-litigate an accepted ADR; if a decision must change, write a new ADR that
supersedes it.**

## Git & GitHub workflow

The flow itself is a **cross-repo rule** — see the Git-workflow and multi-agent
sections of `~/.agents/AGENTS.md`, and don't restate it here: branch off
`origin/main`, open a PR (`gh pr create`) with a real title and body describing
what changed and *why*, have the paired reviewer review it, and merge only on a
`Verdict: LGTM`, pinning the approved SHA
(`gh pr merge <n> --squash --delete-branch --match-head-commit <sha>`). No
auto-merge, no self-merge, no direct push of non-trivial work to `main`. Delete
the merged local branch by name; fast-forward local `main` only with
`git fetch origin && git merge --ff-only origin/main`.

Agents drive this via the `gh` CLI: don't ask the user to push, merge, or click
in the GitHub UI — do it, and report the PR link. What is specific to this repo:

- **Branch types:** `type/short-desc` with `docs/…`, `feat/…`, `fix/…`,
  `chore/…`, and `exp/…` for experiment work.
- **CI is the required check.** [`.github/workflows/ci.yml`](.github/workflows/ci.yml)
  runs `pytest` + `pre-commit` on every PR, enforced as the required `check`
  status by branch protection on `main`. Green CI is necessary but not
  sufficient — run the [quality bar](#quality-bar) locally before pushing to
  fail fast, and the LGTM is what authorizes the merge. The heavy
  `eval`/`rollout`/`verify-golden` workflows stay manual (`workflow_dispatch`).
- **Commit messages:** imperative mood, explain the *why*; end with a
  `Co-Authored-By:` trailer for the model that wrote the change.
- **Releasing.** When the user says "release", it means **both** a GitHub
  tag/Release **and** a PyPI publish — they go together. Bump `project.version`
  to match the tag, land it, then `gh release create vX.Y.Z --generate-notes`
  (which triggers the `publish.yml` Trusted-Publishing workflow). Full steps:
  [`docs/conventions.md`](docs/conventions.md#releasing).

## Quality bar

Before merge, both must be clean (see [`docs/conventions.md`](docs/conventions.md)):

```sh
uv run pre-commit run --all-files    # ruff + pyink + isort + basedpyright + uv-lock
uv run pytest                         # the test suite
```

Scope to what you touched while iterating; run the full set before merge. New
behavior gets a test; `experiments/` is exempt from the hooks.

**An invariant needs a test, or downgrade the claim.** When a `spec.md`, an ADR,
or a docstring asserts an *always / never / every path / exactly one*, the same
change adds a named test that fails when it's violated — otherwise reword the
sentence to "intended / today / not enforced". An invariant you cannot name a
test for is a wish, and it silently decays into a lie.

## Boundaries

- **Always:** run the quality bar before merge; keep the docs map
  ([`docs/README.md`](docs/README.md)) thin (detail lives in each workstream
  folder); redact operator PII in any trace record; treat the **code** as
  source of truth over a doc flagged *provisional*; keep every fact in **one
  home** (route via [`docs/doc-map.md`](docs/doc-map.md); status lives only in a
  component's `plans/README.md` / the workstream snapshot, never in a plan
  header); **reconcile a component's `spec.md` at each checkpoint and on any
  workstream-status change** — the spec has no other forcing function, so it
  rots without this (fix its Success Criteria, Open Questions, and any body
  section a landed ADR superseded).
- **Ask first:** adding a runtime dependency; changing the annotation schema or
  the `EvalSpec` / report contract; re-hosting or renaming the HF dataset repos;
  the deferred `outputs/` restructure; deleting anything under `outputs/` (it is a
  committed deliverable).
- **Never:** commit secrets / OAuth tokens / `.envrc.local`; commit dataset data
  files or large trace records (gitignored / off-repo on HF by design); push
  non-trivial work straight to `main`; present the provisional patch-extraction
  docs as authoritative.

## Language of the codebase

Repository language: English (case B in the cross-repo rules,
`~/.agents/AGENTS.md`) — all code, comments, documentation, commit messages, and
README content are written in English, regardless of the language of the
conversation.

## Naming conventions

- **Strict camelCase / PascalCase for acronyms and initialisms.** Treat an
  acronym as an ordinary word: capitalize only its first letter. Write
  `SweBenchProInstance`, not `SWEbenchProInstance`; `Http`, not `HTTP`;
  `JsonParser`, not `JSONParser`; `httpClient`, not `HTTPClient`. This keeps word
  boundaries unambiguous and casing mechanical. (snake_case identifiers such as
  module names are unaffected.)
- **Don't shorten names unnecessarily.** Name a value for what it *is*; a saved
  keystroke is never worth a reader's guess, and an ad-hoc truncation that
  drops information is confusing. A `UnitTestSpec` value is `unit_test_spec`,
  **not** `unit_spec`; spell the word out rather than clip it. (A short name that
  *is* the established term — a well-known abbreviation, or a deliberate
  convention like `sb` for the narrow `SandboxFs` view — is fine; the rule is
  against inventing a cryptic short form for a name that already reads clearly.)
