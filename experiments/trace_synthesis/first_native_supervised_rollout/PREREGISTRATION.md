# Pre-registration — first natively supervised rollout

**Frozen before the run.** `git log --follow PREREGISTRATION.md` shows when;
the run's timestamp says whether that was before. A criterion edited after the
run is not a criterion, whatever it says.

The design, method and how-to-run are in [`README.md`](README.md); this file is
only the readings and what would falsify them.

## Success criteria

Every one of these is checked against **persisted artifacts**, after the
container is gone. A criterion that can only be checked by watching the run is
not a criterion.

**A. The supervisor existed and said so.**

1. `supervisor_summary.json` is present and parses at the schema version this
   reader knows.
2. `accounted_for` is `true`.
3. The run's metrics carry no `supervision.unhealthy`.
4. `rollout_outcome` is not `SUPERVISION_FAILED`.

*(3 and 4 are not redundant with 2: 2 is what the wrapper claims, 3 is what our
observer made of it, and 4 is what the classifier did with that. A break
anywhere in that chain is the thing most worth catching on a first run, and
each link is a separate source.)*

**B. It actually judged, at least once — and it is recorded *how much*.**

5. `supervision.boundaries` ≥ 1.
6. `supervisor_log.jsonl` is non-empty and contains ≥ 1 row whose kind is a
   judgement (`spoke`, `silent`, or `unjudged` — all three mean the supervisor
   was consulted; only their absence means it never was).

*(Without B, criteria A are satisfiable by a wrapper that started, supervised
nothing, and shut down tidily — precisely the run that must not be mistaken
for evidence.)*

7. **Recorded, with no pass threshold:** `supervision.boundaries`, the number
   of assistant messages in the actor's event log, and the configured
   `judge_every_n_assistant_messages`, reported together.

*(5 and 6 cannot tell "supervised throughout" from "judged once, at the end" —
they give the same output for both. The product's claim is **process**
supervision, so a run with 200 assistant messages and `boundaries = 1` would
satisfy B while making that claim false. The expected relation is roughly
`assistant_messages / N`. **Deliberately no threshold on a first run:** a
number invented to fit one observation is not a criterion, it is a
rationalisation. The three numbers appearing together is the criterion.)*

**C. It watched the actor that actually ran — witnessed from outside it.**

The witness must not pass through the wrapper. Two are already collected by
this run:

- `native_transcript.tar.gz` — the agent CLI's own session record, whose module
  says it is written "by the agent binary's own session persistence — not by
  the pump, not by the supervisor's log, not by the converter — so it survives
  every one of those being wrong". **This is the reverse-direction witness**,
  because it is a complete record of the session rather than a view of one
  channel.
- `claude.proxy.jsonl` — the actor's HTTP traffic, a different wire entirely
  from its stdout. Used as a second, forward-direction corroboration;
  reconstructing message text from SSE deltas to get a count out of it is
  possible but is not relied on here, and that is said rather than assumed.

Not byte equality; the representations differ by construction. **The matching
rule, fixed here:** an assistant message is identified by the concatenation of
its text blocks with trailing whitespace stripped, and by the `name` of each
tool-use block in order. Two messages correspond when those agree.

8. **Forward — nothing the wrapper logged was invented.** Every assistant
   message in the wrapper's actor event log corresponds to one in the
   transcript.
9. **Reverse — nothing the actor produced was dropped.** Every assistant
   message in the transcript corresponds to one in the wrapper's actor event
   log, and the two counts are equal. Both counts are reported.

   *(8 alone is satisfied by a wrapper that drops half the actor's output: the
   dropped messages never enter the set it quantifies over, so everything it
   did log is faithfully witnessed and the reading is true while the wrapper
   watched half the run. The reviewer's control world for it: the actor emits
   four assistant messages, the wrapper records only the first and third and
   hashes that truncated log — 8 true, the digest check true, and half the run
   unsupervised. **This is the same one-directional defect as asserting a
   subset instead of set equality**, which this repo has now met in three
   places; the fix is the same one, in both directions.)*

10. **The witness is present and non-empty.** The transcript archive exists and
    yields ≥ 1 assistant message. If it does not, C **fails** — it does not
    quietly fall back to 8 alone. A correspondence assertion over an empty
    witness is true of everything.

*(The first draft of this criterion asked whether the wrapper's log matched
"what the actor produced". **There is no such observation except through the
wrapper**: it is the only reader of the actor's stdout, so both sides of that
comparison came from it. A wrapper that read the stream, dropped half, and
hashed the half it kept would have passed. A comparison whose two sides share
a source is an echo.)*

11. `summary.actor_event_log_sha256` matches the digest of the persisted event
    log. **This is integrity, not authenticity** — it establishes that the file
    was not altered after the wrapper closed it, and says nothing about
    whether its contents are what the actor emitted. That is what 8 and 9 are
    for, and this is not a substitute for either.
12. `summary.criterion_sha256` equals the digest of the criterion rendered into
    the config.

**D. Two upstreams, kept apart.**

13. `claude.proxy.jsonl` has ≥ 1 record with a 2xx response.
14. `supervisor.proxy.jsonl` has ≥ 1 record with a 2xx response.
15. For one record from each log, the **response headers are reproduced in
    full and verbatim** in the report, and then read for which upstream
    answered. The response is the far end's own output, so it is evidence;
    `--target` is our configuration, and proving it against itself is an echo.

    Both halves matter, and the first matters more. My reading is a judgement
    made without prior knowledge of what either upstream's headers look like,
    so "I looked and concluded OpenRouter" is checkable only by trusting me.
    The verbatim headers let a reader decide for themselves whether the two
    sets discriminate at all — **which is worth more than my being right.**

    **No expected header names are written down in advance.** Pre-registering
    an expectation is usually good practice, but it needs a basis, and here
    there is none; writing one now would turn "I found what I expected" into
    the effective criterion, which is screening the observation through the
    expectation.

*(14 is the only artifact-level evidence that the supervisor's model calls
happened at all. **A known weakness, recorded rather than papered over:** the
proxy's log format has no field naming where a request went — it records
request headers/body and response status/headers only, and **not** the
response body, so headers are the only place the far end speaks for itself. So
15 is read off the response after the fact rather than asserted in advance,
and the first run reports what it saw.

If that turns out not to discriminate, **the fix is a field in the proxy's
log, not a stronger claim here** — and that route is open rather than
hypothetical, since cc-reverse-proxy is to move into this repo under `go/`,
making it our code. That is a separate PR and is not to be hung on this
experiment.)*

**E. No credential is anywhere in the record.**

16. **Every file under the run's output directory** is scanned — traversed,
    not listed. No artifact is named here on purpose: a list omits whatever is
    added next, and what is added next is exactly what a list would miss.
    Neither credential's value appears in any of them. Checked by reading the
    values inside a process and printing **only a boolean** — never echoed,
    never an argument, never through a shell.
17. `authorization` appears as `[REDACTED]` in both proxy logs — the positive
    half, so that 16 passing cannot be explained by the proxy having recorded
    no headers at all.

*(**The boundary of 16, written down because an unwritten boundary is read as
"already checked":** it finds a credential only in its literal form. A copy
that was base64'd, URL-encoded, or split across a line boundary is invisible
to it. Not widened for a first end-to-end run — the point is to say what this
check does and does not cover, not to build a scanner.)*

## When an invocation counts, and what it may be called

Frozen here because the alternative is choosing after seeing the output which
runs were "real". Read as **first match wins**; the list is exhaustive by
construction, since its last arm has no condition.

| # | condition, in order | counts? | may be re-run? |
|---|---|---|---|
| 1 | no sandbox run started (setup/harness error before the agent exec) | no | yes |
| 2 | the invocation script exited **78** — a fail-closed probe fired (no wrapper, no credential) | no | yes, after fixing what it named |
| 3 | `rollout_outcome` is `SYSTEM_FAILED` or `OOM_KILLED` | no | yes |
| 4 | `rollout_outcome` is `TIMED_OUT` | **yes** | no |
| 5 | `rollout_outcome` is `SUPERVISION_FAILED` | **yes** | no |
| 6 | anything else | **yes** | no |

Arms 1–3 are failures of the machinery around the experiment, not readings
about supervision; arm 2 is in fact evidence the guard works. Arms 4 and 5 are
real supervised runs that ended badly, and **a bad ending is a result** — they
are evaluated against the criteria exactly as arm 6 is, and will fail several.

**At most one re-run**, and only for arms 1–3. A second occurrence of the same
arm stops and goes to the user rather than becoming a third attempt: the
1×1 scale is not a budget to spend down. A re-run never replaces a run that
counted.

## The one sentence each result licenses

The verdict is computed, not chosen, and **a criterion that cannot be evaluated
is a criterion that failed.** There is deliberately no "inconclusive" arm: an
artifact missing for reasons "unrelated to supervision" is exactly the judgement
call that hands the choice back to whoever writes the report.

| verdict | condition | the sentence it licenses, and no stronger |
|---|---|---|
| `supervised` | every criterion 1–17 passes | "One rollout was supervised end to end by the in-sandbox wrapper: its own account says so, an independent witness confirms it watched the whole actor stream, and both upstreams answered." |
| `not-supervised` | any criterion fails | "This run did not demonstrate supervision. The criteria that failed were: …" — naming every one, not the first. |

Neither sentence may be widened to the runtime. **`n = 1`**: this says a
supervised rollout happened once, not that the native supervisor works, is
reliable, or is ready. Any claim of that shape needs a different experiment and
should not borrow this one's evidence.

## What would falsify, and is still a result

A run that fails any criterion is reported as it happened. The first run
failing is more informative than the first run passing, and a failure that is
explained is worth more than a pass that is assumed. Specifically expected as
plausible first-run failures: the wrapper cannot reach the proxy (network
namespace), the config is refused (a hand-mirrored field drifted), or the
actor's argv arrives wrong (quoting).

**No criterion is to be relaxed to make the run pass.** A run recorded as
successful under a loosened criterion is worse than a failed run, because it
enters the record as data.

## What each criterion's evidence comes from

Written out because the defect this whole file was nearly built on is a
comparison whose two sides share a source. If a row's two columns name the
same producer, the row is an echo and not a check.

| # | the claim | who produced the evidence |
|---|---|---|
| 2 | `accounted_for` | the wrapper, about itself |
| 3 | no `supervision.unhealthy` | our observer, reading the wrapper |
| 4 | `rollout_outcome` | the classifier, reading the metrics |
| 7 | boundaries vs assistant messages | the wrapper, and the actor's own log |
| 8 | the wrapper invented nothing | **the transcript — never the wrapper** |
| 9 | the wrapper dropped nothing | **the transcript — never the wrapper** |
| 10 | the witness exists at all | the agent CLI, which wrote it |
| 11 | the file is unaltered | the wrapper, about its own file (integrity only) |
| 15 | which upstream answered | the upstream, in its response headers |
| 17 | headers were recorded | the proxy |
