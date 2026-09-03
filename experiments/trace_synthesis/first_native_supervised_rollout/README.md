# First natively supervised rollout

**Status: criteria frozen, not yet run.** The criteria live in
[`PREREGISTRATION.md`](PREREGISTRATION.md) and were fixed before the run, so
that reading the output cannot be what decides what counts as success.
Deciding afterwards is a comparison whose two sides both come from the run.

## Question

Does a rollout supervised by the in-sandbox Rust wrapper complete, and does it
leave behind an account that says so *on its own terms* — not merely a process
that exited 0?

The failure this is written against: **a run that supervised nothing and
exited 0 looks exactly like a run that was supervised throughout.** The wrapper
exits with the actor's own status when it ran cleanly, so the exit code cannot
distinguish them. Every criterion below is therefore about the record, not the
status.

## Scale

**One instance, one rollout.** The repo's boundary is >10 instances or >2
rollouts per instance; 1×1 is inside it. Not to be widened opportunistically —
if the first run is inconclusive, the next step is a second 1×1 with something
changed, not a bigger sweep.

## Method

- workflow: `native_supervised_rollout_and_unit_test`
- binary: `SWE_LAB_SUPERVISOR_BINARY` → local musl build (no release yet)
- credentials: `SWE_LAB_SUPERVISOR_API_KEY` and `CLAUDE_CODE_OAUTH_TOKEN` by
  `pass_env`; the endpoint is not passed and must not be

## Criteria

Frozen before the run, in [`PREREGISTRATION.md`](PREREGISTRATION.md). That is a
separate file with a name that says what it is, because the property being
protected is temporal: **a criterion edited after the run is not a criterion,
whatever it says.** `git log --follow PREREGISTRATION.md` shows when it froze;
the run's own timestamp says whether that was before.

## How to run

```sh
cd ~/dev/swe-lab                  # the main checkout — see below, not optional

# The wrapper is not released yet, so this names a local musl build. Read by
# code (`trace_synthesis.supervisor_binary`), transitional, and the only way
# in until a release exists.
export SWE_LAB_SUPERVISOR_BINARY=<path to swe-lab-supervisor>

uv run swe-lab run native_supervised_rollout_and_unit_test <instance_id> \
    --sweep first-native-e2e \
    --output-root ~/dev/swe-lab-artifacts
```

`swe-lab run` takes **one instance per invocation**, so one instance and one
rollout is the shape of a single command rather than something to remember not
to exceed.

**Run it from the main checkout, not a feature worktree**, and treat that line
as a step rather than a preamble. Two reasons, and the first is the one that
decides:

- **A feature worktree runs unmerged code, and the question this run asks is
  whether what is on `main` works.** No criterion below can tell "`main` is
  green" from "my branch is green", so a success obtained on a branch
  establishes nothing about `main` — and it is the kind of success that gets
  kept as data.
- `.envrc.local` exists only in the main checkout, and the fix is **not** to
  copy it into a worktree. That would put a second copy of a credential file on
  disk, and one fewer copy is always better than one more.

`--output-root` is not optional here even though the flag is: it defaults to
`.cache/runs` *inside the checkout*, which for a worktree is a directory that
will be removed. `~/dev/swe-lab-artifacts` is outside every checkout, so the
evidence outlives all of them.

Credentials reach the sandbox by name (`pass_env`) and appear in no command
line: `SWE_LAB_SUPERVISOR_API_KEY` and `CLAUDE_CODE_OAUTH_TOKEN`. **Neither is
the name your shell exports**, and `swe_lab.host_env` closes both gaps in-process
at the CLI entry point — the OAuth token narrows from the repo-scoped name, the
supervisor key is adopted from the machine-wide `OPENROUTER_API_KEYS`. Which
name each was taken from lands in the run record as
`extra["credential_env_adopted_from"]`; the values never do. The
supervisor's endpoint is **not** passed in — the harness exports it, because it
addresses a forwarder the harness starts inside that sandbox.

## Open before running

- #393 must be in `main`: without it the wrapper does not judge at boundaries,
  so criteria B cannot be met by construction and the run would be measuring
  nothing.
