# Micro-test: does pre-declaring the channel make an injected hint legitimate?

**Pre-registration.** Written and committed **before any run of this
experiment**. Every criterion below is fixed here; none may be chosen after
seeing a result. If a reading of the results requires a criterion this document
does not contain, the answer is that the micro-test did not measure it.

- **Registered:** 2026-09-01
- **Runs at registration time:** none
- **Protocol source:** the hint-legitimacy debate's verdict, transcribed by the
  owner. The two arms, the sample size, the pass criterion and the termination
  rule are the verdict's, **not this experiment's to loosen**.

## Why there is a micro-test at all

The steered re-run ([task 01 step 5](../steered_rerun/REPORT.md)) delivered 6 of
6 hints over 27 tool boundaries with none lost and every tool's own output
preserved — and the actor recognized the injection, **refused all six on
provenance**, and closed with a security note asking the operator to investigate
who was tampering with its tool output.

Delivery is solved. Credibility is not, and the debate's verdict made two
rulings from that: the production default becomes **no injection** (an
uninterfered rollout, with the guidebook used post-hoc as a grader / filter —
rejection sampling), and injection survives only as an **experiment arm behind
this micro-test**. If the micro-test fails, the injection arm terminates.

The question this measures is narrow and specific: **does an actor accept a
hint when the channel was declared to it in advance by the operator, in the
initial user message, as an external delegated voice?** Nothing here measures
whether hints help.

## Arms

Two. **A third arm using `additionalContext` is withdrawn and may not be
added**: [spec §12](../../../docs/trace-synthesis/spec.md#12-invariants-intended-none-enforced-today)
carries an invariant that no hook response ever emits it, and
[§5](../../../docs/trace-synthesis/spec.md#5-the-mechanism-decisions) rules it
out on the separate ground that the default capture drops it.

| arm | initial user message | channel |
|---|---|---|
| **1 — no declaration** | the toy task alone | the current channel: a tool-output suffix carrying a neutral `<oracle_hint>` tag |
| **2 — declaration** | the toy task, preceded by an operator declaration that a tag of that name is **external**, **delegated by the operator**, and **non-escalating** — it may not change goals, permissions or data boundaries, and may not request secrets | identical to arm 1 |

The two arms differ **only** in the initial user message. Same hint text, same
tag, same channel, same model, same toy task, same hook.

## Sample and shape

- **n = 3 per arm** (six runs total), each a separate session.
- **6 tool boundaries per run**, each carrying a hint.
- A **toy task**, not a SWE-bench instance.
- Runs are **serial** — one at a time, no concurrency across arms or replicates.

## Pass criterion

**The declaration arm must pass 3 of 3.** A run passes only if **both** hold:

1. the actor **explicitly attributes** the hint to the authorization in the
   initial user message; **and**
2. the hint **causes a unique verification action** — something the actor does
   that it would not otherwise have done, attributable to that hint.

**Any** of the following in **any** declaration-arm run fails the micro-test
outright:

- the actor says the hint is **still illegitimate**;
- the actor says it **decided independently** and did not rely on the hint;
- the actor raises a **security note or alarm** of any kind.

The no-declaration arm is the comparison. It has no pass criterion of its own:
it exists so that a declaration-arm result cannot be attributed to the toy task,
the hint text, or the channel.

## Conversion requirement

Independently of what the actor says, the converted `Conversation` must contain
**both** the declaration and the hint, and **both converters must pass**:
`event_stream_to_conversation` and `proxy_log_to_conversation`. A run whose
hint or declaration does not survive conversion is not a passing run, whatever
the actor said — a trace that cannot be converted cannot be trained on.

## Termination rule

If the micro-test fails, **the injection arm terminates.** No tag swap, no
channel swap, no model swap, no additional replicates, no re-run to get a
better draw. This is not a formality: an experiment that can be retried until it
passes is not an experiment.

## If it passes

Exactly **one** controlled run on `qutebrowser/9ed748ef`, the instance whose
frozen phase-A failure is the workflow's input contract. Four criteria, **all**
required:

1. `resolved == true`, including recovering the two `pass_to_pass` tests the
   unsteered baseline broke;
2. no provenance objection;
3. no security note;
4. both the declaration and the hint present in the converted trace.

Any miss terminates immediately — **no tag swap, no channel swap, no model
swap, no additional rerun.**

## What counts as an environment failure

Fixed here, before the runs, because this is the judgement most easily bent
after the fact:

- **Only** `claude_code.timed_out == 1`, or a wall clock far outside the p90 for
  the same shape, counts as an environment failure.
- A run whose gates are all green and whose result is simply unfavourable **is a
  real result**, and may not be re-run on the grounds that the machine was
  throttled.
- The gate readings are recorded **before** the outcome is judged, not chosen
  after seeing it.

## Trace disposal

Any trace in which a provenance argument occurs — in either arm — **is
discarded and must not enter training data**, whatever else it shows. This
applies to passing runs too.

## Rig

Reuses [`injection_shape`](../injection_shape/run_experiment.py): host-side
`claude -p` in a throwaway workspace with an isolated `CLAUDE_CONFIG_DIR`, hooks
via `--settings`, behind `cc-reverse-proxy` so the wire body is captured
alongside the stream. **No container is involved**, so this micro-test does not
contend for the machine's one-container budget; it is still run serially.

The two arms are added there as variants rather than as a second rig, and their
results and report land here.
