# Experiment: is a segmented resume loop a viable supervision carrier? (Phase 0)

> **Status: design only. Nothing here has been run.** This file is the
> pre-run registration of criteria; `REPORT.md` does not exist yet.

## Why this exists

We are building **process supervision**: correcting a Claude Code actor *while*
a rollout is running, so the trace becomes process-supervision training data.
The mechanism in flight is [ADR-0013](../../../docs/decisions/ADR-0013-supervision-on-the-stdin-channel.md)'s
**A′** — a resident `claude -p --input-format stream-json` process whose stdin
carries the correction. A′'s transport is settled and measured; its
pre-registered adoption gate returned **`BELOW_BAR`** and stayed there.

The owner has proposed an alternative to hold **beside** A′ — *not* as a
replacement: run the actor in **segments**. Run a bit → stop → supervisor judges
→ resume, either carrying a correction or a neutral continue → repeat. The
argument for it is complexity: every hard problem in the A′ implementation
(concurrency barrier, `setsid` descendant freeze, folded-event accounting, judge
cancellation, read-gate blind window) exists **only** to let actor and judge run
concurrently — and that design was then serialized with `sigstop` anyway. A
segmented loop is serial by construction and those problems do not arise.

**Phase 0 answers only: does the mechanism exist and is its seam acceptable.**
No SWE-bench instance, no container, no production code path. A toy task in
`/tmp`. Phase 1 (a real instance, compared against #393's e2e trace) is not
authorized and is not designed here.

## The corrected premise this design rests on

The brief that commissioned this experiment stated as verified fact that
**`--max-turns` does not exist**. That was **wrong**, and the way it was wrong is
the repo's own recurring defect: it was established with `claude --help | grep`,
and *"hidden from `--help`"* and *"does not exist"* produce **identical output**
under that observation. A second attempt, `claude --max-turns 1 --version`, is
equally undiscriminating — `--version` short-circuits before option validation,
so a real flag, a fake flag and no flag all print the version and exit 0.

The discriminating probe hands the flag to the parser at a position where an
unknown option **must** error, and pairs it with a control arm that is known not
to exist. Run 2026-09-03, `claude` **2.1.259**:

```sh
claude -p --max-turns              # error: option '--max-turns <turns>' argument missing
claude -p --definitely-not-a-flag  # error: unknown option '--definitely-not-a-flag'
```

**The two arms print different errors; that is what makes it a check.** Neither
invocation reaches the API, so the probe is free. Re-run for every build under
test — this is `Q0` below.

The same probe, applied to the other hidden flags (same session, same build),
returns their declared argument shapes:

| flag | declared argument |
| --- | --- |
| `--max-turns` | `<turns>` |
| `--resume-session-at` | `<message id>` |
| `--resume-drops-turn` | `<message id>` |
| `--task-budget` | `<tokens>` |
| `--rewind-files` | `<user-message-id>` |

**What that establishes and what it does not.** It establishes that the option is
defined and what its option definition calls its argument. It establishes
**nothing about semantics** — `--resume-drops-turn <message id>` taking a message
id is not evidence that it drops a turn at that message. Separately, these
strings are present in the shipped binary: stop reason `max_turns`, events
`max_turns_reached` / `hit_max_turns` / `error_max_turns` /
`tengu_agent_max_turns_reached`, and a control-marker forgery guard named
`max-turns-note-forgery`. **All of the preceding sentence is "a name exists in
the binary's string table", not "behaviour observed"**; the forgery-guard name is
*suggestive* that a note is inserted on max-turns exit and that the CLI defends it
against user-content forgery, and that suggestion is exactly what `Q3` measures
rather than assumes.

## Hypothesis

> **H.** With `--max-turns N`, a Claude Code rollout can be cut at a boundary we
> choose and resumed, such that (a) the cut is machine-distinguishable from the
> model finishing on its own, (b) the records the seam adds can be removed to
> leave a message sequence isomorphic to an uninterrupted run, (c) the actor
> resumes the work instead of re-planning, re-doing or declaring victory, and
> (d) the marginal token cost over an unsegmented run stays bounded as the
> number of segments grows.

Falsifiable, and each clause has its own kill condition below. **H is a
conjunction: any clause failing sinks Phase 0 as stated**, and the report says
which one.

## What changed since the brief, and what I think is still wrong

Three of these are disagreements with the brief. They are stated up front
because the brief asked for them.

1. **The unit of `--max-turns` is the make-or-break, not the exit code.** The
   original tension — *a `-p` call ends when the model thinks it is done, which
   is too coarse for process supervision* — has **not** been dissolved by
   `--max-turns`; it has been **relocated**. If a "turn" means *one model
   response*, then `--max-turns 1` gives per-tool-call granularity and the design
   works. If a "turn" means *the whole user↔assistant exchange, tool calls
   included, until the model stops*, then `--max-turns 1` is the entire task and
   the granularity is exactly what we already had. **Nothing in the brief
   measures this**, and everything downstream is conditional on it. It is `Q2′.1`
   and it runs first.

2. **The canonical form of this design is `--max-turns 1`, and that makes the
   seam the dominant cost, not a footnote.** A supervisor does not decide "stop
   in 3 turns"; it decides "stop now, I saw something". The only way to give it
   that is to end every segment after one turn and let it judge each time. So a
   real rollout of 30–100 turns pays the seam 30–100 times. **`Q3` (what the seam
   adds) and `Q5` (what the seam costs) therefore are not "also important" — they
   become the gate**, and `Q2′` is the cheap enabling condition in front of them.
   Consequence for the method: every seam question is measured **at depth 5**, not
   at depth 1. A defect invisible in one seam — compounding reminders, the actor
   drifting toward wrap-up, cache decay — is exactly the kind that only appears
   when the seam repeats.

   **And depth 1 and depth 5 are reported as separate rows, never as a mean.** A
   mean over five seams prints the same value whether every seam is identical or
   the fifth is twice the first, so it cannot choose between those two — which is
   the only question depth was introduced to answer. Every per-seam table in
   `REPORT.md` carries one row per segment index.

3. **The seam's known content is worse than "a system reminder", and this repo
   already measured it.** [`streamjson_input` §3.3](../streamjson_input/REPORT.md)
   observed, on a kill+resume seam (N=1), a **synthetic `assistant` record**:

   ```
   user      {'isMeta': True}          'Continue from where you left off.'
   assistant {}                        'No response requested.'
   user      {'promptSource': 'sdk'}   'Correction from the operator: …'
   ```

   §14 of that report records the owner's criteria for what disqualifies a
   trace: **(a)** taking loss on tokens the actor did not generate, **(b)** a
   context shape that does not occur at inference time. The fabricated assistant
   turn violates **(a)**. Deleting it in post-processing fixes (a) — and creates
   a second problem the brief does not name, which is `Q3b`: the actor's *next*
   message was produced in a context that **contained** the records we deleted.
   A trace whose surviving assistant text was caused by records no longer in it
   is not a clean trace; it is a trace that lies about its own cause. So "can we
   post-process it away" splits into two questions with different answers, and
   only one of them is about deletion.

   Note also that this prior is about the **kill+resume** seam. Whether the
   `--max-turns` seam is the same records, fewer, or different, is unmeasured —
   the `max-turns-note-forgery` string suggests it is at least partly different.

   **Why this is a structural comparison and not an implementation wart.** A′ has
   no `Q3b` to answer, and that is measured rather than argued: a correction typed
   into the real interactive TUI and the same correction written on `-p` stdin
   fold into the wire **byte-identically** — `len 440`, `sha256 3ba88726…fb90c8`,
   both values carried by
   `streamjson_input/runs/{proxy,tui}-midturn/evidence.json` and asserted as an
   *equality* by `tests/test_streamjson_input_evidence.py` (N=1 per arm). So A′'s
   context shape is one that occurs at inference time: criterion **(b)** holds
   with **nothing deleted**, and a question about deletion's side effects cannot
   arise there. The segmented loop must therefore either show its seam also
   satisfies (b), or concede a cost A′ does not pay. **`Q3b` is consequently
   reported in `REPORT.md`'s verdict block, not in the middle** — on current
   evidence it is the likeliest deciding argument between the two designs, and
   placing it inside a section about post-processing would make a structural
   disadvantage read as a tooling detail.

4. **Demote `Q2` (kill/stop arms) harder than the brief does.** At `--max-turns 1`
   granularity we never need to kill anything, so an 8-cell arm × cut-point
   matrix is budget spent on a path we would not ship. It shrinks to **one cell**
   whose purpose is not "can we cut this way" but "**is the session file
   self-consistent when a segment dies uncleanly**" — a robustness question about
   crash recovery that we will need answered regardless. See `Q2-legacy`.

5. **`--max-budget-usd` is not a cut point but it stays on every invocation.**
   Agreed with the brief that turns are the better unit. It is retained as a
   **runaway guard** — a per-segment ceiling so a misbehaving toy run cannot burn
   the Phase 0 budget — and `Q6` shrinks to one recorded sentence.

## The questions

Every criterion below is written as a **positive chain**: a list of conditions
that must *all* hold, each of which would print something different if the
mechanism were not working. Where a check is one-directional — informative when
it fails, empty when it passes — that is stated in the sentence introducing it,
and its passing may then never be cited as support.

---

### Q0 — coordinates and flag existence (free, no API)

Recorded at the top of every run: `claude --version`, git commit, wall clock with
timezone, the model id **the response reports** (never the `--model` alias — an
alias re-pointed upstream leaves the request looking correct), and the sampling
parameters actually sent **including the ones that were not**, because an unset
parameter is invisible unless its absence is written down.

The two-arm flag probe above is re-run and its two lines are pasted into the
report verbatim. **Check direction:** if the flag vanished in a later build, the
`--max-turns` arm would print `unknown option` — the same string as the control
arm — and the whole design would be void. This one is informative in both
directions.

---

### Q2′ — is `--max-turns N` a clean, resumable cut? *(the crux)*

**Q2′.1 — What is a turn?** Run the same toy task at `--max-turns` ∈ {1, 2, 3}
and an unbounded control, and count, from the `stream-json` stdout: assistant
messages, `tool_use` blocks, `tool_result` blocks, and API requests.

*Positive chain (all must hold to conclude "a turn is one model response"):* the
run exits without the task being complete; assistant-message count equals N;
`tool_use` count is ≥ 1 and does not grow without bound; and the unbounded
control on the identical task produces strictly more of all four. **Kill
condition:** if `--max-turns 1` completes the toy task, the unit is the whole
exchange, `--max-turns` buys no granularity, and Phase 0 falls back to
`Q2-legacy` with a much worse prognosis.

*One prior, labelled for what it is.* The shipped binary's agent schema carries
the description string `Maximum number of agentic turns (API round-trips) before
stopping`. **That is a docstring in a string table, not an observation.** It
points at the favourable side — a turn is one model round-trip — and it is
exactly the kind of prior that stops people measuring, so it changes nothing
about whether `Q2'.1` runs. Three cases could make the real counting differ from
the description regardless of the description's accuracy: **parallel tool calls
inside one assistant message, sub-agents, and a compaction**. The first is
exercised here if the model batches its reads, and the count is reported against
`tool_use` blocks rather than assistant messages so the two can be told apart;
the other two are out of scope and are named under *What Phase 0 cannot
settle*. *This sub-question is reported before
any other number, because every other number is conditional on it.*

**Q2′.2 — Is the early exit distinguishable from natural completion?** From the
final `result` event of each run, record `subtype`, any stop-reason field,
`is_error`, and the process exit code — for (X) a `--max-turns`-truncated run and
(Y) a run of the same task that the model finished on its own.

*Criterion:* there must exist **at least one field whose value differs between X
and Y**. Exit code alone does not qualify unless it differs. **Kill condition:**
if X and Y are field-identical, the supervisor cannot tell "cut short" from
"done", cannot decide whether to resume or to stop, and the design does not work
— this is the brief's own kill condition and I agree with it.

**Q2′.3 — Does the cut leave a dangling `tool_use`?** Because `--max-turns` cuts
at a turn boundary, the risk from the old `Q2(ii)` moves here: an assistant
message carrying a `tool_use` with no matching `tool_result` makes the whole
conversation unsendable, and the API rejects it on resume.

*Positive chain:* parse the session transcript after the cut; every `tool_use`
id has a matching `tool_result` id; **and** the resume in `Q2′.4` returns a
non-error `result`. The second half matters because our parse could be wrong
about what the API requires — the run is the authority, the parse is the
hypothesis. If a rejection happens, the error body is captured (redacted) rather
than summarized.

**Q2′.4 — Does resume close the loop after a `--max-turns` cut?** The positive
chain is `Q1`'s, run on a `--max-turns`-truncated session instead of a naturally
finished one.

**Q2′.5 — Does the turn count reset on resume?** *(not in the brief, and it is
the difference between "works once" and "works as a loop")* Resume a truncated
session with `--max-turns 1` again, twice.

*Positive chain:* segment 2 produces exactly one assistant message and exits;
segment 3 likewise; the toy task's side-effect ledger advances by one step per
segment. **Kill condition:** if the count is cumulative over the session rather
than per invocation, segment 2 exits immediately having done nothing, the ledger
does not advance, and the loop cannot be built from this flag at fixed N. That
failure is *loud* (a segment that does no work), which is why the ledger is the
witness and not the exit code.

---

### Q1 — does `--session-id` + `--resume` carry context, headless?

The brief is right that "resume did not error" is undiscriminating: it prints the
same thing whether the session continued or a blank one started. The check is a
recall of a value that **cannot be re-derived** in segment 2.

The toy fixture writes a **32-hex nonce** into `step2.txt`. Segment 1 reads it as
part of its work. Between segments the driver **deletes the file**. Segment 2 is
asked to state the token.

*Positive chain — all six:*
1. segment 1 exits 0 and its stream shows the `Read` of `step2.txt`;
2. `step2.txt` is absent at segment 2 start (`test ! -e`, recorded);
3. segment 2 exits 0;
4. segment 2's text contains the nonce **verbatim**;
5. segment 2 issued **zero** `tool_use` blocks — so the nonce came from context,
   not from re-reading it out of some other file (the session transcript
   included);
6. **the negative-control arm** — a fresh `--session-id`, no resume, the same
   question, the same deleted file — does **not** produce the nonce.

Arm 6 is what makes 4 informative: without it, a nonce that turned out to be
guessable or derivable from the task text would pass. Arm 5 is what makes it
"context" rather than "a tool found it again".

**Also recorded (the brief asked):** whether the session file **grows** or a new
id **forks** — `ls`, size and sha256 of the project's session directory before
and after, plus the set of session-id filenames as a diff. Paths are reported
relative and redacted (see *Evidence*).

---

### Q3 — what is actually at the seam, and can it be removed?

Every seam's raw events and transcript records are **saved**, and the report
lists them one by one — role, meta flags, verbatim text — rather than
characterizing them.

**Q3a — shape isomorphism (mechanical).** Define, in `analyze.py`, a
`normalize()` that drops the records classified as seam artifacts. Compare the
resulting **role sequence** of a 5-segment run against the role sequence of an
**unsegmented control run of the same task**.

*Criterion:* the two sequences are equal, and no unclassified record survives
normalization. **What each direction establishes:** a **mismatch** is
informative — it names a record we cannot remove. A **match is weak**, because
`normalize()` is our own code and can be over-eager; so the report also prints
the full list of records `normalize()` dropped, and a reviewer checks that list
against the raw seam dump. When a check reports a violation, the checker is the
newer half and is suspected first: a reported mismatch is an observation awaiting
attribution, not a finding.

**Q3b — is a deleted record load-bearing? (the part deletion cannot fix)**
For each seam, does the surviving assistant text depend on records
`normalize()` removed? Two detectors — verbatim overlap with the seam strings,
and a hand-listed set of re-orientation phrasings — plus a **read** of every
seam's first assistant message.

*Stated in the sentence that introduces it:* **a detector's miss establishes
nothing here.** Paraphrase is exactly the form this dependency takes, and n-gram
overlap cannot express paraphrase — this repo has already published a zero from
such a detector and had it mean nothing. The detectors **rank what to read
next**; the reportable assurance is *"one reading, at one revision, by someone
who knew what to look for"*, and the raw seam text is committed so a reviewer
can do their own.

**The honest form of the answer.** The brief says the owner has accepted "there
will be system reminders, they can be post-processed", but has not accepted
"they could not be cleaned and we said they were". So the report answers in
three parts and never collapses them: *what the seam contains*, *what deletion
achieves* (Q3a), *what deletion cannot achieve* (Q3b).

---

### Q4 — does the actor re-orient?

**Labels fixed before the first run**, applied to the first assistant message of
each resumed segment:

| label | rule |
| --- | --- |
| `CONTINUE` | first action is the next undone step; no restatement of the plan |
| `RESTATE` | summarizes or re-plans first, then continues |
| `REDO` | repeats a step the ledger records as already done |
| `DONE` | declares the task complete while ledger steps remain |
| `OTHER` | anything else — reported verbatim, never folded into another label |

`REDO` and `DONE` are decided **mechanically** from the tool-call sequence
against the toy task's side-effect ledger. `CONTINUE` vs `RESTATE` needs a read;
that is stated as a judgement in the report, with the verbatim text beside it.

*Three arms, N=3 each, same task, same trigger point:*
- **R** — segmented resume (`--max-turns 1`, neutral continue);
- **Y** — **the control that isolates the restart**: the same neutral continue
  delivered on the stdin of a *live* stream-json process, i.e. the A′ shape,
  cut at the same event without any process ending;
- **Z** — unsegmented, no continue at all, as the base rate for how this task
  gets done.

Without **Y**, a `RESTATE` in R cannot be attributed to the segmentation rather
than to the neutral message itself. **N=3 per arm steers a judgement and will not
support a rate**; the report says so rather than printing three fractions with
denominators of 3.

---

### Q5 — what the seam costs

**The brief's framing needs one correction.** "If every segment is a cache miss,
cost grows quadratically in segments" — the quadratic term is **not** a property
of segmentation. An agent loop re-sends its whole history on every request, so
total input tokens are already Σ(prefix length) over requests, quadratic in
turns, in the unsegmented run too. Caching does not remove that shape; it scales
its coefficient (cache reads bill at a fraction of fresh input). So the
discriminating quantity is **not the growth shape** but the **ratio to an
unsegmented control on the same task**, and the cache columns are what explains
any gap.

Segmentation can only add two things, and both are measured directly:
- **+1 request per seam** (the resumed segment's first model call), against A′'s
  measured *zero* extra actor requests for a mid-turn injection;
- **cache misses at the seam**, if the request prefix changes.

*Per segment, from the `result` / usage events:* `input_tokens`,
`cache_read_input_tokens`, `cache_creation_input_tokens`, `output_tokens`,
`total_cost_usd`, plus the API request count. Plotted against segment index for a
**5-segment** run and its unsegmented control.

**Q5a — is the prefix preserved across a process boundary?** *Prediction, so the
measurement can falsify it:* the cache is server-side and keyed on the request
prefix, so crossing a process boundary is not itself a barrier; what breaks it is
a **changed prefix**. Seam records are appended after the last assistant message,
leaving the prefix intact, so `cache_read_input_tokens` should be **> 0** on the
first request of segment ≥ 2. The mechanism that would break it is the **dynamic
system prompt** (cwd, env, git status) re-rendering differently on the new
process — which is presumably why `--system-prompt-snapshot <on|off>` and
`--exclude-dynamic-system-prompt-sections` exist. Runs use the default
(`snapshot` on for the built-in prompt) and record which.

*The control arm that gives this discriminating power:* one run where the driver
**deliberately perturbs the dynamic section** between segments (touch a file so
`git status` differs). If `cache_read_input_tokens` collapses there and stays
high in the unperturbed arm, the instrument can see a miss and the unperturbed
green means something. Without that arm, "cache read was high" cannot be told
from "our reader is looking at the wrong field" — a poller reading a field its
workflow never populates reports the same reassuring value forever.

**Q5b — how long may the supervisor think?** *(not in the brief; it is a design
constraint, not a curiosity)* Prompt-cache entries expire. If the supervisor's
deliberation between segments exceeds the TTL, **every** seam is a full cache
miss and Q5's numbers are wrong by the cache-read discount. Two seams, identical
but for the inter-segment delay: **~0 s** and **~6 min**. Report
`cache_read_input_tokens` for both. This costs wall-clock, not dollars, and it
sets the supervisor's latency budget — which is a number the eventual design
needs and nobody currently has.

---

### Q7 — the hidden flags

`--resume-session-at <message id>` and `--resume-drops-turn <message id>` are, by
their declared argument names, about resuming from a chosen point and dropping a
turn while doing so — which would be **seam control given to us directly**, and
would change `Q3`'s answer. `--task-budget <tokens>` and `--rewind-files
<user-message-id>` are recorded but not pursued.

*Method, cheap and bounded:* feed each a plainly invalid argument and read the
error (free, no API); then **one** paid probe each on the toy session, passing a
real message id taken from the transcript, and diff the resulting first request
against the same resume without the flag.

*Reporting rule:* the outcome is one of **"observed: <what changed>"** or
**"could not determine"**. Nothing between. An option whose behaviour we inferred
from its name is written as *name observed, semantics unverified* — the same
label this file puts on the binary-string findings above, and for the same
reason: this repo has already shipped one outage from an option whose shape was
inferred from a field name.

---

### Q2-legacy — the uncleanly-killed session *(demoted, one cell)*

Not a cut mechanism. A robustness question: SIGKILL the process while a tool call
is in flight, then resume, and record whether the session is still resumable,
what the seam contains, and — the part the brief did not name — **whether the
tool's side effect landed without being recorded**. A file written by an `Edit`
whose `tool_result` never reached the session is invisible to the actor on
resume, which in a real container means it redoes the edit. The toy ledger makes
that observable: compare the filesystem against the session's record of it.

One cell, N=1 for the failure direction. If it resumes cleanly, that is
*possibility, not reliability*, and the report says so rather than reporting a
green.

### Q6 — `--max-budget-usd` *(demoted, one sentence)*

Recorded from the guard invocations that carry it anyway: exit code and `result`
subtype when the ceiling trips, and whether the session resumes afterwards. Not
developed as a cut point.

---

## The toy task

A scratch repo under `/tmp` (never the working repo), created by `toy_task.py`:

- `step1.txt` … `step5.txt`, each holding a **32-hex nonce** and a pointer to the
  next file.
- The task: read the steps in order; run `sleep 8` between each pair; write all
  five nonces into `result.txt`.

Why this shape:

- **Ordered, deterministic tool calls** give every arm the same "same point",
  which is what a control arm needs to be comparable at all.
- **A side-effect ledger.** `result.txt` and the per-step reads make *what
  actually happened* checkable against *what the session recorded*, which is what
  `Q2′.5`, `Q4`'s `REDO`/`DONE` and `Q2-legacy` all rest on.
- **The `sleep`** guarantees a wide, reliably-hit in-flight window for
  `Q2-legacy`'s mid-tool-call cut. (Borrowed from `streamjson_input`, which used
  the same device.)
- **Nonces** make `Q1`'s recall unguessable.
- It is **small**: five tiny files, no repository, no build.

## Budget and the stop rule

`--model sonnet`; `--max-budget-usd 0.25` on **every** invocation; the driver
appends each run's `total_cost_usd` to `runs/ledger.jsonl` and **refuses to
launch when the running total exceeds `$5.00`**. A ceiling that is enforced by
the runner rather than by attention is the point — a budget rule that depends on
someone remembering it is not a budget rule.

Rough shape of the spend: Q2′ ~12 invocations, Q1 3, Q4 ~15 (three arms × three
runs, segmented ones costing several), Q5 ~12 (a 5-segment run, its control, the
perturbation arm, the TTL pair), Q7 2, Q2-legacy 2, at a toy task's ~$0.02–0.10
each. **That estimate is an estimate**, typeset as prose precisely so it is not
mistaken for the constraint; the constraint is the `$5.00` ledger stop, which is
enforced.

## Evidence, and what is committed

Following the precedent set by [`streamjson_input`](../streamjson_input/README.md)
rather than inventing one: raw captures carry operator-home paths and the
operator's global `CLAUDE.md`, which names them and carries their email.
`AGENTS.md` forbids committing that.

- **Gitignored** (per-run, never committed): `events.jsonl`, `stderr.log`,
  `transcript.jsonl`, `meta.json`.
- **Committed**: `runs/<variant>/evidence.json`, built by `evidence.py` — record
  shapes, roles, meta flags, seam text, token and cost fields, the tables the
  report asserts — every string redacted, the whole artifact **re-scanned** for
  home paths, home slugs, the operator's git identity and credential shapes, and
  **nothing written when that scan finds anything**. Each file carries the sha256
  of the raw inputs it was built from, so a re-run can be matched to what was
  published.
- No credential is read, echoed or restated; runs use the machine's ambient CLI
  auth and create or modify **no** credential file. If any output shows a
  token-shaped string, the run stops and the owner is told.

## Files

| file | what it is |
| --- | --- |
| `toy_task.py` | builds the `/tmp` fixture; prints the nonces and the expected ledger |
| `driver.py` | runs one segment: launch, watch `stream-json`, apply a cut, record |
| `run_matrix.py` | drives the arms of Q1, Q2′, Q4, Q5, Q7, Q2-legacy |
| `evidence.py` | builds and re-scans the committed `runs/*/evidence.json` |
| `analyze.py` | evidence → every table in `REPORT.md`; `normalize()` lives here |
| `runs/<variant>/` | raw artifacts, one directory per run, never overwritten |

Every number in `REPORT.md` is printable by one named command, and the command
appears beside the number with its coordinates (build, session id, timestamp).

## Order of execution, and where we stop early

Sequential, because each stage can void the ones after it:

1. **Q0** — free. If the flag probe's arms match, stop.
2. **Q2′.1** — the unit. If `--max-turns 1` completes the task, stop and report:
   the design's premise is gone and `Q2-legacy` inherits the question.
3. **Q2′.2** — distinguishability. If X and Y are field-identical, stop.
4. **Q1 + Q2′.3–.5** — resume closes the loop, at depth.
5. **Q3, Q4, Q5** — the seam. These are the gate (see disagreement 2).
6. **Q7, Q2-legacy, Q6** — last, on whatever budget remains.

**Phase 1 is not authorized by this document.** If Phase 0 passes, the deliverable
is a report and a proposal — and if the approach is adopted, a **new ADR that
supersedes ADR-0013**, not an edit to it.

## What Phase 0 cannot settle

Named now, so a green report is not read as more than it is:

- **A toy task is not a rollout.** Everything here is one short task on a host,
  `sonnet`, no container. ADR-0013's own refutation condition is that host
  measurements may not describe the pinned build inside the sandbox; the same
  caveat applies to every number in this experiment, and Phase 1 exists to
  address it.
- **Nothing here measures whether supervision helps.** That is the paired-arm
  resolved-rate question, and it is unaffected by which channel wins.
- **`N=3` steers a judgement; it does not support a rate.** No percentage will be
  reported from these arms.
- **A green at `N=1` is possibility, not reliability**, everywhere it appears.
