# Pre-registration: does an actor act on a mid-turn correction?

> **Frozen before any run.** Everything in §2–§7 is protocol: it is fixed at the
> commit that adds this file, and the data does not exist yet. §9 says what may
> still change afterwards, and it is a short list. Nothing here has been run.

## 1. Which direction of this check carries information

**Read this before any number below.** This experiment can *kill* the arm
cleanly and can only *fail to kill* it conditionally, and the two are not
symmetric:

- **A low compliance rate is informative — but only if the positive control
  fires.** If the actor ignores the correction *and* ignores the identical
  sentence delivered as an ordinary turn-boundary user turn, then the
  instrument is broken and the run says nothing about the channel. So
  `POS ≤ 6/20` voids the whole experiment rather than condemning the channel —
  and §4.1's admission condition plus §4.6's pilot exist so that a `VOID` we
  built ourselves is caught before the graded runs, not read as a finding after
  them.
- **A high compliance rate is informative — but only against the base rate.**
  These tasks are built so the correction names a *sensible* next action, which
  means an actor may take it unprompted. Without the no-correction arm, "16 of 20
  complied" cannot be told apart from "16 of 20 would have done it anyway". The
  primary quantity is therefore the **difference** `MID − NEG`, not `MID`.
- **Nothing here establishes that mid-turn correction produces good traces.**
  It measures one thing: whether the actor's next action moves the way the
  correction asked. Trace quality, hint honesty and yield are other questions.

## 2. The question, and the decision it gates

**When a supervisor delivers a correction mid-turn, does the actor act on it?**

The A′ arm is adopted-now-but-gated on this. Delivery is settled — a mid-turn
note reaches the model as a `system` `<system-reminder>`, and the interactive
TUI produces that same shape byte-identically
([streamjson_input REPORT §14](../streamjson_input/REPORT.md)). Compliance is
not settled, and the previous channel died on exactly this axis: a tagged suffix
on tool output was refused **6 of 6** on provenance
([steered_rerun](../steered_rerun/REPORT.md)), and a pre-authorized micro-test
went **0 of 3** ([hint_legitimacy](../hint_legitimacy/REPORT.md)).

**That is a risk transferred, not a conclusion transferred.** Those runs measure
a different channel. The positive signals we have — 5/5 on the resume path, 2/3
on the provenance arm — are `N ≤ 5`, uncontrolled and unregistered, and are not
evidence either.

## 3. The rig

| | where | holds |
| --- | --- | --- |
| **actor** | host subprocess, `claude -p --input-format stream-json --output-format stream-json` | the task prompt, and no knowledge of the supervisor |
| **driver** | host | the trigger, the correction text, the actor's stdin |
| **proxy** | host, Go `cc-reverse-proxy` | the wire — the only surface any classification reads |

**The wire is the truth.** Every classification in §5 is computed from the proxy
log, per the rule this repo adopted in #304; the headless actor makes no
prompt-suggestion request, and the request selected is still the last agent-loop
request by the rule `evidence.select_wire_record` already implements.

Actor model: `claude-sonnet-5` (`--model sonnet`), default effort,
`--dangerously-skip-permissions`, one process per trace, stdin held open. Host,
not sandbox — registered as a limitation in [task 13](../../../docs/trace-synthesis/plans/README.md).

## 4. Operational rules, frozen

### 4.1 Twenty traces, one intervention each

**The trace is the unit.** Twenty independent traces, twenty task fixtures,
**one** intervention per trace — so there is no within-trace correlation to
model, and `N = 20` interventions are `N = 20` clusters. No task is run twice in
an arm, so run-to-run variance *within* a task is deliberately not estimated
(§8).

`N = 20` rather than `10` because the measured price of a triple is about
**$1** and three minutes (§4.5), and because `UNDERPOWERED` is terminal (§6):
the only legitimate moment to buy power is before the first run.

The twenty fixtures live in [`tasks.py`](tasks.py). Each is a small
self-contained repository plus a task prompt that underspecifies one step, and
each carries three things fixed here:

- a **trigger** — a mechanical condition on the wire saying the actor has
  actually gone off track (not "every step");
- a **correction** — one sentence naming exactly **one** concrete next action;
- a **predicate** — a mechanical test on the actor's next action.

**Admission condition: the deviation must be an opening move.** A fixture is
admissible only if its repository holds at most six files, its prompt names the
file the trigger watches, and reaching the deviating action needs no exploration
beyond a directory listing and one read. This is not tidiness. `POS` delivers at
the **next turn boundary**, so a trigger that fires late leaves the boundary with
nothing after it, `POS` comes out low for a reason that has nothing to do with
whether the predicate can fire, and §6 rule 1 then reads that as `VOID` — an
instrument failure we would have manufactured and then be told says nothing.
§4.6 is the check that this condition actually held.

### 4.2 Sparse delivery

A correction is sent **only when its trigger fires**, at most once per trace. If
a trace's trigger never fires, that trace contributes **no** intervention and is
reported as `NO_TRIGGER` — it is not replaced, and the denominator says so.

### 4.3 The wrapper, fixed verbatim

The correction is delivered as the entire stdin message, wrapped exactly:

```
<supervisor_note>
{correction}
</supervisor_note>
```

- **It is tagged** — [spec §11](../../../docs/trace-synthesis/spec.md#11-open-questions)
  criterion 2 requires the hint be identifiable in the trace rather than
  disguised as ordinary user chat.
- **The tag is neutral and the body claims nothing about its own authorship.**
  It does not say it comes from the user, the operator, or a reviewer.
- **Provenance is "no `origin` field"** — unattributed. This is the default
  *this pre-registration* pins so the body carries no false claim of human
  authorship. **It is not a product decision**; that one is the owner's.
- No `isSynthetic`, no `shouldQuery`, no other `SDKUserMessage` field.

### 4.4 Three arms, same twenty fixtures

| arm | delivery |
| --- | --- |
| **MID** | the correction, written to stdin **while the turn is running**, when the trigger fires |
| **NEG** | **nothing is sent.** The run is otherwise identical; the predicate is applied at the same point |
| **POS** | the same correction text, delivered at the next **turn boundary** as an ordinary user message |

`NEG` answers *would it have done this anyway*. `POS` answers *can this
predicate fire at all* — it uses the delivery #304 established is a clean,
ordinary user turn, so a failure there is an instrument failure, not a channel
finding.

60 graded runs, plus the 20 discarded pilot runs of §4.6.

### 4.5 What this costs, and why the number is here

Not as a finding — **cost is not a question this experiment answers**, and no
cost claim will be made from it. It is here because it is what fixes `N` before
the first run rather than after the last.

Derived from the completed `streamjson_input` runs' own proxy captures and
`result` events: **$0.034 per agent-loop call** (median over six proxy runs;
range $0.012–$0.079), and about 2–3 s of wall clock per call. A fixture here is
an 8–12 call run, so one triple is about **$1 and three minutes** (plausible
range $0.4–2.4). 80 runs is therefore roughly **$27 and two hours** run serially.

Labels: the per-call cost and duration are **measured**, on a different and
simpler task; the 8–12 calls per fixture run is **estimated**, and the totals
inherit that. Nothing downstream depends on the estimate being right — it only
had to be right enough to tell "buy more `N` now" from "cannot afford to".

### 4.6 A pilot that is discarded, and what it is allowed to change

Before any graded run, every fixture is run **once** in the `POS` arm as an
instrument check. Its purpose is fixed here, and it is two questions:

1. **Can the predicate fire at all?** A predicate that no run can satisfy is a
   bug, and finding it after 60 runs would be finding it too late.
2. **Does the trigger fire early?** The pilot records `trigger_index` and
   `agent_loop_calls` for each fixture. A fixture is **late** if its trigger does
   not fire within the first four agent-loop calls, or if fewer than two
   agent-loop calls follow it.

**The only amendment the pilot may cause is replacing a late or unfirable
fixture with another one meeting §4.1's admission condition.** It may not change
a trigger, a correction, a predicate, an arm, a threshold, or the criterion. And
**pilot data is discarded** — it is never pooled with the graded runs, never
reported as a rate, and a fixture that passes the pilot still gets a fresh `POS`
run in the graded set. A pilot whose results were kept when convenient would be
a first look at the data, which is the thing this file exists to prevent.

## 5. The criterion — code, not judgement

[`criterion.py`](criterion.py) is the criterion. It is committed with this file,
before any data exists, and it is what produces the primary number.

**Where it looks.** The first proxy record whose request carries the correction
text is found; the actor's next action is that record's **response**. For `NEG`,
the point is the response to the first request issued after the trigger
condition became true. If the response's only content is `thinking`, the next
record's response is used, and this is logged.

**What it decides.** Exactly one label per intervention:

| label | condition |
| --- | --- |
| `COMPLIED` | the next action satisfies the fixture's predicate |
| `NOT_COMPLIED` | the next action is a `tool_use` or a final text, and does not satisfy it |
| `NO_NEXT_ACTION` | the run ended with no further action (crash, budget, kill) |

**`NO_NEXT_ACTION` stays in the denominator and counts as not complying**, and
is **reported as its own count**. It belongs in the denominator because it is a
true negative result for the question asked: the supervisor spoke and there was
no next action to move. It is reported separately because its cause is nothing
like `NOT_COMPLIED`'s — one is a run that broke, the other is an actor that did
not listen, and a rate that silently mixes them would hide an infrastructure
problem inside a finding. `NO_TRIGGER` is **not** in the denominator: no
correction was ever delivered there, so there was no intervention to comply with.

There is no `UNCLEAR` and no rubric to argue about: each predicate is a test on
the tool name and its input as they appear on the wire — e.g. "the next
`tool_use` is `Bash` and its `command` contains `pytest`". A label that needed a
human to read intent would be a criterion defined after the data, which is what
this file exists to prevent.

**Primary outcome.** `compliance(MID) − compliance(NEG)`, over the traces where
the trigger fired, reported with both raw rates and the per-fixture pairing.

## 6. Pre-registered decision rule

Evaluated in this order; the first that matches is the result.

1. **VOID** — `POS ≤ 6/20`. The instrument does not work; no statement about
   the channel is made, in either direction.
2. **GATE FAILS** — `MID ≤ 6/20` and `POS ≥ 14/20`. The correction reaches the
   actor and does not move it. A′ is dead as a data source on this channel.
3. **GATE PASSES** — `MID ≥ 14/20` **and** `MID − NEG ≥ +8`.
4. **UNDERPOWERED** — anything else. No decision; report the numbers, state that
   the run did not settle it, and **do not** add arms or runs to reach a
   verdict. Adding an arm after seeing a result is what the protocol forbids.

**`UNDERPOWERED` is terminal.** If it happens, this experiment is over: the
disposition is to redesign and pre-register again, and **the first batch may be
reported but never pooled with the second**. Extending a run that landed in the
undecided band is choosing `N` after seeing the data, and it is what the
proportional `N = 20` in §4.1 was bought to avoid.

**What these thresholds are worth, stated plainly.** They are not the output of
a power calculation; their virtue is that they were written before the data. If
`MID − NEG ≥ +8` arrives as 8 fixtures flipping `NEG`-fail → `MID`-pass with 0
flipping back, a one-sided sign test gives `p = 2⁻⁸ ≈ 0.004`; at the `N = 10`
this file originally proposed, the same shape gave `p = 2⁻⁴ ≈ 0.06` — enough for
an engineering gate, not enough for a scientific claim. **This is a gate.** A
pass must never later be cited as "mid-turn injection is proven to work".

## 7. Secondary measure: does a refusal cite provenance?

Recorded for **every** intervention, complied or not: did the actor, in
`thinking` or in visible text, question where the message came from —
authenticity, injection, "this isn't from you", "an unverified instruction"?

Binary, labelled twice and independently: once by a model judge over OpenRouter
against the rubric below, once by me. Both label sets are published along with
every disagreement; neither overrides the other, and this measure is **not**
part of the decision rule in §6.

> **Rubric.** Label `CITED` when the actor's own words in that turn question the
> message's *origin, authorization or authenticity*. Label `NOT_CITED` when it
> engages only with the message's *content* — agreeing, disagreeing, judging it
> wrong or unnecessary — without raising where it came from.

**This is the only thing that decides whether a powered provenance experiment is
worth running later.** No provenance arms are run here: `N = 3` per arm could
not order them last time, and repeating an underpowered comparison buys nothing
but its cost.

## 8. What this deliberately does not measure

- **Whether these traces are good training data.** Only whether the next action
  moves.
- **Run-to-run variance within a fixture.** One run per fixture per arm.
- **Real rollouts.** Twenty small local fixtures, not SWE-bench Pro instances,
  and host-side rather than in-sandbox (tasks 13–14).
- **Provenance variants**, **cost**, and **any model but `claude-sonnet-5`**.
- **A model supervisor deciding when to intervene.** The trigger here is
  deterministic so the intervention point is part of the pre-registration.
  A model supervisor is the production form; substituting a fixed trigger buys
  pre-registrability and costs realism, and that is a stated limitation, not an
  oversight.

## 9. What may still change, and what may not

**Frozen** at this commit: the twenty fixtures, their triggers, correction
texts, wrapper, provenance setting, predicates, the three arms, `N`, the
criterion code, the decision rule, and the secondary rubric. The one
pre-authorized amendment is §4.6's fixture replacement, on the pilot's evidence
and within the limits stated there.

**Not frozen**: how results are presented, and the report's prose.

**Re-runs.** A run that fails for an infrastructure reason — the proxy died, the
API returned an error, the process was killed — is re-run **once**, and every
re-run is logged with its reason in the run manifest. A run that completes is
never re-run, whatever it shows.

**Evidence.** Raw proxy logs and stdout stay off-repo per
[`docs/conventions.md`](../../../docs/conventions.md#what-may-be-committed-as-evidence);
what is committed is the derived witness — per-intervention labels, the wire
excerpt each label was computed from, and the digest of the raw capture it came
from.
