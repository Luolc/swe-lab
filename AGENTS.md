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
  - `plans/` — one **deep, source-grounded design per task**; `plans/README.md`
    is the **ordered task index + status** (the checklist). There is **no**
    separate `todo.md`.
  - `plan.md` — **optional**: a strategy doc (phase order, dependency graph,
    risks) that earns its keep only for a multi-phase migration, and is deleted
    when that migration ends. It never enumerates tasks and never carries
    status; a component without one is normal.

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
uv run pre-commit run --all-files    # the full hook set — see conventions.md
uv run pytest -m 'not docker'        # the test suite, minus the container tests
```

The **docker-marked tests are CI's job**, and CI is the required check that runs
them — they must be green before merge, but locally they start containers of
their own and collide with the one-container-at-a-time rule the moment two
agents share a box. Run them locally *only* when the change touches sandbox,
container or harness-capture paths, and then serially. The full rule, its
exception, and how to read a local failure are in
[`docs/conventions.md`](docs/conventions.md#hazards-learned-the-hard-way).

The hooks themselves are listed once, in
[`docs/conventions.md`](docs/conventions.md); `.pre-commit-config.yaml` is the
source of truth. Don't restate the list here — three drifting copies of it is
how it went stale before.

**Credential scanning runs in both places, over different inputs.** The
`gitleaks` pre-commit hook (first in `.pre-commit-config.yaml`, before any hook
that rewrites files) scans the **staged diff** — the only input domain that
contains a secret that has not been committed yet. CI scans the **full history**
— the only domain that catches one already in. Neither replaces the other.

`--no-verify` skips **all** hooks, gitleaks included. If you must use it, stage
your files and then run the staged scan by hand before committing:

```sh
gitleaks git --staged --redact --no-banner --verbose .
```

Never add a gitleaks allowlist entry to make the gate pass: anything we
introduced or could remove must be **fixed**. `.gitleaksignore` is only for a
pre-existing fact we neither introduced nor can remove at acceptable cost, and
each entry is one immutable fingerprint — never a path, rule or regex. The full
rule and the reasoning are in that file's header comment.

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
  header); **reconcile a component's `spec.md` in the PR that outdates it** — a
  spec has no forcing function of its own, so it decays into a lie unless two
  mechanical triggers are honored:
  - **An ADR that supersedes a section of a spec rewrites that section in the
    same PR** (the natural extension of ADR-first-same-PR). If you cannot point
    at the paragraph you changed, the ADR is not finished.
  - **A task flipping to ✅ re-checks that spec's Success Criteria and
    out-of-scope list in the same PR** — shipping the thing the spec calls out
    of scope is exactly how a spec starts lying.
- **Ask first:** adding a runtime dependency; changing the annotation schema,
  the engine's compile contract (`SandboxSpec` / `UnitTestSpec` — what a dataset
  compiles its record into), or the report contract; re-hosting or renaming the
  HF dataset repos; the deferred `outputs/` restructure; deleting anything under
  `outputs/` (it is a committed deliverable).
- **Never:** commit secrets / OAuth tokens / `.envrc.local` (enforced by the
  gitleaks hook + the CI history scan — see [Quality bar](#quality-bar));
  commit dataset data files or large trace records (gitignored / off-repo on HF
  by design — what that covers, and why an experiment's own committed evidence
  is not it, is in
  [`docs/conventions.md`](docs/conventions.md#what-may-be-committed-as-evidence));
  push non-trivial work straight to `main`; present the provisional patch-extraction
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
