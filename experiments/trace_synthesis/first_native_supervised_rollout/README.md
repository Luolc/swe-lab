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
# The wrapper is not released yet, so this names a local musl build. Read by
# code (`trace_synthesis.supervisor_binary`), transitional, and the only way
# in until a release exists.
export SWE_LAB_SUPERVISOR_BINARY=<path to swe-lab-supervisor>

uv run swe-lab run native_supervised_rollout_and_unit_test <instance_id> \
    --sweep first-native-e2e \
    --output-root <a path outside this checkout>
```

`swe-lab run` takes **one instance per invocation**, so one instance and one
rollout is the shape of a single command rather than something to remember not
to exceed.

`--output-root` is not optional here even though the flag is: it defaults to
`.cache/runs` *inside this checkout*, and this is a worktree that will be
removed. The evidence has to outlive it.

Credentials reach the sandbox by name (`pass_env`) and appear in no command
line: `SWE_LAB_SUPERVISOR_API_KEY` and `CLAUDE_CODE_OAUTH_TOKEN`. The
supervisor's endpoint is **not** passed in — the harness exports it, because it
addresses a forwarder the harness starts inside that sandbox.

## Open before running

- #393 must be in `main`: without it the wrapper does not judge at boundaries,
  so criteria B cannot be met by construction and the run would be measuring
  nothing.
