# The in-sandbox fold check — `MATCH`

**Verdict: `MATCH`.** A mid-turn correction delivered inside the sandbox, on the
pinned `2.1.212`, produces a **byte-identical** injected block to the host
measurement on `2.1.257`.

| | baseline (host, 2.1.257) | this run (sandbox, 2.1.212) |
| --- | --- | --- |
| injected block `len` | 440 | **440** |
| injected block `sha256` | `3ba88726…fb90c8` | **`3ba88726…fb90c8`** |
| wire messages | 7 | **7** |
| role sequence | `user, system, assistant, user, assistant, user, system` | **identical** |
| `system-reminder` blocks | 4 | **4** |
| API calls / agent-loop / side | 4 / 3 / 1 | **4 / 3 / 1** |

The reading was fixed in [`PRE-REGISTRATION.md`](PRE-REGISTRATION.md) and
committed at `bf35dc1`, before the run.

## What this settles, and what it does not

**Settles:** [ADR-0013](../../../docs/decisions/ADR-0013-supervision-on-the-stdin-channel.md)'s
**refutation** condition does not fire, and
[task 05](../../../docs/trace-synthesis/plans/task-05-supervisor-the-component.md#7-acceptance)'s
in-sandbox acceptance condition is met. The byte-identity result the attribution
decision rests on is about the artifact we ship, not only about the host.

**Does not settle:** anything about *compliance* — whether an actor acts on a
correction delivered this way. This measures shape, and only shape.

**The confounds named in advance did not have to be untangled — and equality
does not untangle them either.** The sandbox arm differs from the baseline in
version *and* environment, so a mismatch could not have said which. What this
result supports is exactly one sentence:

> **The combined change of version and environment did not alter the wrapper for
> this input.**

It does **not** support "neither difference moved the wrapper". Attributing the
*absence* of an effect to each factor separately needs an arm that varies one at
a time, and this run has none — two changes that individually altered the
wrapper and happened to offset would produce the same equality. That is
unlikely; it is also untested, and the difference between those two words is the
whole reason the disambiguating arm exists.

The disambiguating arm (2.1.212 on the host) was named in the pre-registration
and **not run**, exactly as registered: it was only worth buying on a mismatch.

## N = 1, and what that is worth

**One run, one task, one correction, one model.** This is the same N the
baseline it compares against has, and the comparison is an equality between two
single observations rather than a rate.

**What the data says is that two recorded outputs were equal.** That is not a
repeatability claim: a single pair cannot distinguish an assembly path that is
deterministic from one that happened to agree twice, because nothing here was
run twice under the same conditions. Calling it "deterministic for this input"
— as an earlier draft of this report did — reads a property of the *process* off
a single pair of *outputs*.

It is also **no** evidence about other tasks, timings, models, or non-text
content blocks. Those edges are
[task 14](../../../docs/trace-synthesis/plans/README.md), unchanged by this
result.

What it does do is what it was registered to do: **the refutation condition had
its chance to fire and did not.**

## Method

The check imports `_wire()` from
[`streamjson_input/evidence.py`](../streamjson_input/evidence.py) — **the code
that computed the baseline** — rather than re-deriving the digest. Two numbers
produced by one implementation is the comparison; two numbers produced by two
implementations would have left open the question the check exists to answer.

The expected values are read from the committed baseline artifact at run time,
not from a literal in this document or a constant in a test — the distinction
[#323](https://github.com/Luolc/swe-lab/pull/323) had to correct.

The environment mirrors the shipped harness rather than approximating it:
`IS_SANDBOX=1`, a pinned `CLAUDE_CONFIG_DIR` under `/agent-home/.claude`, the
binaries obtained through `ensure_claude_binary()` / `ensure_proxy_binary()` so
the proxy is built from the source digest the harness would have staged, and
`capture = proxy` because the wire is the truth and stream capture is known to
misrender a mid-turn message.

**Three environment facts cost three failed starts, and are written down so the
next person does not rediscover them:**

1. The instance images declare `ENTRYPOINT ["/bin/bash"]`, so `docker run …
   sleep infinity` is read as the name of a script and the container exits
   immediately (`exit 137` at the next `docker exec`).
2. `--dangerously-skip-permissions` is refused as root unless `IS_SANDBOX=1` is
   set — which is what the shipped harness does, and copying that is what makes
   this an in-sandbox measurement rather than a different environment.
3. `cc-reverse-proxy` announces itself as `Reverse proxy: localhost:<port> → …`,
   not with the word "listening"; a readiness probe grepping for the latter
   waits out its timeout and then runs anyway.

## Cost and hygiene

One container, created for this run and removed when it ended; `docker ps -q`
was `0` before it started and `0` after. One short agent run through OpenRouter;
the credential was selected inside the program from the existing key pool and
handed to the container **by name**, never on a command line.

## Artifacts

Off-repo at `swe-lab-artifacts/sandbox_fold_check/`: `summary.json`,
`proxy.jsonl` (the wire), `events.json` (the agent's stdout), `claude.stderr.log`,
`proxy.log`.
