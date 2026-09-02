# Pre-registration — the in-sandbox fold check

**Written and committed before the run.** The reading below is fixed here; the
run that follows can only produce a value, not a new criterion.

## The question

Every measurement of the stdin correction channel so far is **host-side**, on
Claude Code `2.1.257`. The rollouts that will use the channel run **in
containers**, on the pinned `2.1.212` with a pinned `CLAUDE_CONFIG_DIR`.

> **Does a mid-turn correction produce the same wire shape inside the sandbox as
> it does on the host?**

This is [ADR-0013](../../../docs/decisions/ADR-0013-supervision-on-the-stdin-channel.md)'s
**refutation** condition and an acceptance condition of
[task 05](../../../docs/trace-synthesis/plans/task-05-supervisor-the-component.md#7-acceptance).

## Material, bound by digest rather than by description

The baseline is the committed artifact
`experiments/trace_synthesis/streamjson_input/runs/proxy-midturn/evidence.json`
— **not** a literal in this document and **not** a constant in a test
([#323](https://github.com/Luolc/swe-lab/pull/323) corrected that citation). The
check reads the expected `len` and `sha256` out of that file at run time.

At the time of writing it holds one text block, `len 440`,
`sha256 3ba88726…fb90c8`.

**The digest is computed by the same code that computed the baseline.** The
check imports `_wire()` from
`experiments/trace_synthesis/streamjson_input/evidence.py` and compares the
structure it returns. Re-deriving the formula here would leave open the one
question the check exists to answer.

## The arm

One run, mirroring the host arm's argv and inputs:

- same task, same correction text, same `notes.txt` fixture, same flags
  (`-p --input-format stream-json --output-format stream-json --verbose --model
  sonnet --dangerously-skip-permissions`);
- the correction is written **2 s after the `Bash` sleep `tool_use` appears**,
  as in the host arm;
- inside a container, on the pinned binary, with `capture = proxy` — the wire is
  the truth, and stream capture is known to misrender mid-turn.

## The reading, fixed before the run

| Outcome | Meaning |
| --- | --- |
| `len` **and** `sha256` both equal the baseline's | **MATCH** — task 05's acceptance condition is satisfied |
| anything else | **MISMATCH** — reported verbatim; ADR-0013's refutation path opens |
| no delivery, no wire, or the run fails mechanically | **INCOMPLETE** — not a mismatch, and not evidence either way |

**On a mismatch: no fix, no re-run, no on-the-spot severity call.** The number is
reported as measured and the owner rules on it.

## Confounds named in advance

The sandbox arm differs from the baseline in **two** ways at once — **version**
(2.1.212 against 2.1.257) and **environment** (container against host). A
mismatch therefore does **not** say which one caused it.

The disambiguating arm — 2.1.212 **on the host** — is named here and is
**deliberately not run**: it is only worth buying if the sandbox arm mismatches,
and buying it in advance would be paying for an answer we may not need.

**Upstream also differs** (this run is served through OpenRouter, the only
credential available here). The wrapper is assembled **client-side**, before any
request is sent, so upstream is not expected to touch it — stated as an
assumption rather than a finding, because this run does not test it.

## Termination and hygiene

One container, created for this run and removed when it ends, whatever the
outcome. `docker ps -q` must be `0` before it starts — the machine allows one
container at a time and another agent is queued behind this check.
