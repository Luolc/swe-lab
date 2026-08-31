# Migration pickup (temporary — dev-tokyo → dev-oregon)

**Written 2026-08-31.** This machine (dev-tokyo) is being migrated to
dev-oregon; local transcripts and session memory will be lost, so this repo
must stand on its own. Written for a fresh Claude session on dev-oregon with
zero memory of any prior session, reading only this repo.

**This file is temporary.** Delete it once the Oregon session has picked up —
that deletion is the pickup session's first PR, or part of it. It is
deliberately not linked from `docs/doc-map.md` (that map is for durable docs;
this one won't outlive its own pickup).

## 1. Where you are

`main` is merged through PR #252 (`8915acd`); see `git log --oneline` for
anything landed since. Orient with:

- Repo map + status snapshot: [`docs/README.md`](README.md)
- Working rules: [`AGENTS.md`](../AGENTS.md) (aligned with the cross-repo
  rules as of 2026-08-31)
- Codebase map, commands, hazards: [`docs/conventions.md`](conventions.md)

## 2. The one in-flight item — PR #253 (vault rename staging)

PR #253 renames 1Password vault references `op://workstation` →
`op://dev-shared` (6 refs). It carries `Verdict: LGTM`, approved SHA
`03027d92e394390ed570ebf4c6e80ee8159f04de`, and is **deliberately not
merged** — `butler-orchestra` issues the flip order once the 1Password vault
is actually renamed (machine-setup ADR-0016).

On the flip order:

```sh
gh pr merge 253 --squash --delete-branch --match-head-commit 03027d92e394390ed570ebf4c6e80ee8159f04de
```

Then, machine-locally: update `.envrc.local` (3 refs, `workstation` →
`dev-shared`), `direnv allow`, and verify status-only —
`[ -n "$VAR" ] && echo set` for each of the 3 vars (never echo the value).

If #253 is already merged by the time you read this, only the machine-local
steps above may still remain.

## 3. Machine setup on a fresh box

Pointer list, not a restatement — see [`docs/conventions.md`](conventions.md)
for the full picture:

- `uv sync` (uv fetches Python 3.13)
- `direnv allow`
- `uv run pre-commit install`
- Quality gate: `uv run pre-commit run --all-files` + `uv run pytest`
- Secrets: copy `.envrc.local.example` → `.envrc.local` (gitignored; `op://`
  references only — vault name per whether the §2 flip has happened yet).
  Requires the machine-setup-provisioned `OP_SERVICE_ACCOUNT_TOKEN`. See
  [`docs/conventions.md`](conventions.md#secrets) and machine-setup
  ADR-0013 / ADR-0016.

## 4. Review standard

User-level `~/.agents/skills/pr-review` (+ `python-review` for Python
changes) stacked with this repo's `swelab-pr-review` /
`swelab-python-review` — see [`AGENTS.md`](../AGENTS.md). `code-review-and-quality`
is **no longer** the standard.

## 5. What was decided recently and where it's recorded

- Plugin enable removed — PR #249
- Secrets via 1Password `op read` + guard — PR #250, #251
- `AGENTS.md` alignment + repo review skills (`swelab-pr-review` /
  `swelab-python-review`) — PR #252
- Vault rename staged (not yet flipped) — PR #253, see §2 above

No `SLK*` checklist by decision — the user-level K1–K8 checks suffice.
Skills-lock local entries use raw-file sha256.

## 6. Next work, in priority order

From the reconciled snapshot in [`docs/README.md`](README.md) — pointer only,
detail lives where linked:

1. Horizontal tasks [25](horizontal/plans/README.md) (git-history purge) and
   [15](horizontal/plans/README.md) (extensibility seam proof) — both P0.
2. Horizontal task [13](horizontal/plans/README.md) (R2 store + CI wiring) /
   CP4 — ask-first (needs the user to provision the R2 bucket + token first).
3. W2 rollout mainline — matrix eval across the full SWE-Bench-Pro set:
   [`docs/workstreams/w2-solve-eval/todo.md`](workstreams/w2-solve-eval/todo.md)
   tasks 2–4.
4. Human checkpoints CP1–CP5 (see
   [`docs/horizontal/plans/README.md`](horizontal/plans/README.md)) are all
   still open.

## 7. Orchestra topology

This repo's workspace runs a resident `swelab-orchestra` plus per-task pairs,
per the cross-repo multi-agent rules (`~/.agents/AGENTS.md`).
`butler-orchestra` is the cross-repo dispatcher.
