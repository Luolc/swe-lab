# AGENTS.md

Working rules for AI agents in this repo.

The project map — roadmap, status, and where everything lives — is
[`docs/README.md`](docs/README.md). Read it before starting work; it points to
everything else.

## How we work

Two modes, and the mode picks the method:

- **Building** (a feature or change): follow the lifecycle
  `/spec → /plan → /build → /review → /ship` (slash commands in
  [`.claude/commands/`](.claude/commands/), skills in
  [`.claude/skills/`](.claude/skills/)), with test-driven development and small
  atomic commits as the default. A non-trivial feature starts from a short spec
  (`/spec` → `SPEC.md`); the plan + task list for the active workstream live in
  its folder (`docs/workstreams/<active>/plan.md` + `todo.md`) — if a task is
  missing, add it there first.
- **Experimenting** (learning something — a prompt, variance, a failure, "is X
  worth building?"): follow the
  **[experiment playbook](docs/experiments/playbook.md)** — hypothesis → logged,
  timestamped run → empirical results → attributable conclusion → a `REPORT.md`.
  This is the ML side the coding skills don't cover. An experiment's report
  *feeds* a `/spec` or a decision; don't build straight from a hunch.

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

## Git & GitHub workflow (agents drive this — no manual clicking)

This is a solo, automation-first project: **you** (the agent) own the full
git/GitHub flow via the `gh` CLI. Don't ask the user to push, merge, or click in
the GitHub UI — do it, and report the PR link.

- **Branch for non-trivial work:** `type/short-desc` (`docs/…`, `feat/…`,
  `fix/…`, `chore/…`, `exp/…`). Never commit non-trivial changes straight onto
  `main`.
- **Open a PR** (`gh pr create`) with a real title and body — what changed and
  *why*.
- **Merge:** squash-merge + delete the branch; keep `main` linear and
  always-green. Fast-forward local `main` after (`git checkout main && git pull`).
- **The pre-merge gate is the [quality bar](#quality-bar), run locally** — the
  repo's GitHub Actions workflows are heavy, manual (`workflow_dispatch`) Docker
  eval jobs, not a lightweight PR check. swe-lab is a **public** repo, so a
  lightweight `pytest` + `pre-commit` CI + branch protection + `gh pr merge --auto`
  on green is a worthwhile future addition; until it lands the gate is procedural.
- **Commit messages:** imperative mood, explain the *why*; end with a
  `Co-Authored-By:` trailer for the model that wrote the change.
- **Escape hatch:** direct-to-`main` is reserved for trivial or urgent fixes.

## Quality bar

Before merge, both must be clean (see [`docs/conventions.md`](docs/conventions.md)):

```sh
uv run pre-commit run --all-files    # ruff + pyink + isort + basedpyright + uv-lock
uv run pytest                         # the test suite
```

Scope to what you touched while iterating; run the full set before merge. New
behavior gets a test; `experiments/` is exempt from the hooks.

## Boundaries

- **Always:** run the quality bar before merge; keep the docs map
  ([`docs/README.md`](docs/README.md)) thin (detail lives in each workstream
  folder); redact operator PII in any trace record; treat the **code** as
  source of truth over a doc flagged *provisional*.
- **Ask first:** adding a runtime dependency; changing the annotation schema or
  the `EvalSpec` / report contract; re-hosting or renaming the HF dataset repos;
  the deferred `outputs/` restructure; deleting anything under `outputs/` (it is a
  committed deliverable).
- **Never:** commit secrets / OAuth tokens / `.envrc.local`; commit dataset data
  files or large trace records (gitignored / off-repo on HF by design); push
  non-trivial work straight to `main`; present the provisional patch-extraction
  docs as authoritative.

## Communicating with the user

- **Voice input.** The user often interacts via voice, so their messages are
  produced by speech-to-text and may contain transcription errors: wrong
  homophones, dropped or merged words, mis-split phrases, and mistranscribed
  proper nouns. Read for intent rather than literal text. For example, "cloud"
  can mean "Claude". When a word looks out of place, infer the intended meaning
  from context instead of taking it at face value.

- **Identifiers are especially fragile.** Speech-to-text often cannot reproduce
  the exact spelling of file paths, variable names, and function names,
  particularly special characters and separators such as dots, hyphens, dashes,
  slashes, underscores, and camelCase boundaries (e.g. "doc" may be "dot",
  "dash" and "hyphen" may be dropped or swapped). Do your best to reconstruct
  the most plausible intended identifier — cross-check it against names that
  actually exist in the codebase when possible.

- **Flag and confirm your guesses.** Whenever you infer an identifier or resolve
  an ambiguous term, call it out **explicitly** in your summary — list only the
  specific guesses you made — and ask the user to confirm each one. Be ready to
  update if a guess is wrong.

- **Latest instruction wins.** The user's thinking may not be fully formed when
  they first describe something, and they refine it as they go. Always follow
  the most recent instruction over earlier ones. If a request seems to
  contradict something said before, the latest wording takes precedence — the
  user will clarify if there is a genuine conflict.

- **Reply in the user's language — this is important and often missed.** Look at
  the language of the user's **most recent message** and write your chat reply in
  **that same language**. If they wrote in Chinese, reply in Chinese; if in
  English, reply in English. Check this on **every** turn, because the user
  switches languages mid-conversation — do not default to English out of habit
  just because the code, this file, or the earlier conversation is in English.
  The language of the repository does **not** determine the language of your
  reply. This applies to the whole chat reply, including summaries and follow-up
  questions.

## Language of the codebase

- The two rules below are independent: **what you write into the repo** is always
  English, but **how you talk to the user** follows their language (see above).
  A Chinese message still gets a Chinese reply even though any code or docs you
  produce in that same turn are written in English.
- All code, comments, documentation, commit messages, and README content must
  be written in **English**, regardless of the language the user is speaking or
  typing in.
- Accept user input in any language, but keep everything that lands in the
  repository in English.

## Naming conventions

- **Strict camelCase / PascalCase for acronyms and initialisms.** Treat an
  acronym as an ordinary word: capitalize only its first letter. Write
  `SweBenchProInstance`, not `SWEbenchProInstance`; `Http`, not `HTTP`;
  `JsonParser`, not `JSONParser`; `httpClient`, not `HTTPClient`. This keeps word
  boundaries unambiguous and casing mechanical. (snake_case identifiers such as
  module names are unaffected.)
