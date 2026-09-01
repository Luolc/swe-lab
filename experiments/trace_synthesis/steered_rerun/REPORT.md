# Steered re-run — REPORT

| Field | Value |
| --- | --- |
| Author | swelab-steered-impl (Claude Opus 5) |
| Task | trace-synthesis [task 01](../../../docs/trace-synthesis/plans/task-01-one-instance-end-to-end.md), **step 5** |
| Design | [`README.md`](README.md) |
| Corpus | SWE-bench Pro (public test split, 731 rows) |
| Actor harness | `claude_code` 2.1.212, `bare=False`, subagents denied |
| Actor model | `anthropic/claude-sonnet-5` via OpenRouter (`https://openrouter.ai/api`) |
| Supervisor model | `anthropic/claude-opus-5` via OpenRouter |
| Box | 4 vCPU / 15 GB linux-x64 dev workstation |
| Ran | 2026-09-01 02:00–04:00 PDT |
| Repo commit | see each frozen tree's `PROVENANCE.json` |
| Regenerate | `uv run python experiments/trace_synthesis/steered_rerun/analyze.py` |

## Status

The round was **blocked for part of its span** and is not any more:
`capture="proxy"` is required on the OpenRouter path (§4), the proxy is reached
at `host.docker.internal`, and this box's `ufw` default-denies incoming, so
nothing on the Docker bridge could open a host port. That is machine state and
not this task's to change; `machine-setup` PR #138 opened `20000:20999` and
`25000:25999` to the Docker subnet and the path is now verified open
(`CONTAINER_OK 200` from inside an instance container). No rollout was spent
while it was shut — a run made then would have produced a silently degraded
trace, which is worse than no run.

**Where the round stands.** Three determinate instances are being sampled in
parallel — `navidrome/50015182`, `vuls/4c04acbd`, `qutebrowser/9ed748ef`, three
at a time, each in its own container on its own proxy port. (Per-key rotation
landed mid-harvest: the `qutebrowser` arm uses its own key, the other two were
launched before `--key-index` existed and share one. No 429 was seen either
way.) The first sample
back (`navidrome` rollout 0) **resolved**, which is the outcome the gate wants
to see before a failure on that instance means anything: a task the actor can
solve, that it sometimes does not. The steered arm runs against the first
genuine failure — exit 2 with `timed_out == 0` — that the harvest produces.

What this report carries: mechanical findings about the injection channel, the
harness and the capture path; a task-quality gate applied to all ten candidates,
five discarded with their evidence, and the result that its screens are
complementary; the input contract the phase-C workflow consumes; and two
corrections — one to a claim in an already-merged report, one to a verdict in
this one. Each is attributable to a specific run or probe.

## Contents

- [Conclusions](#conclusions)
- [1. `--bare` disables hooks](#1---bare-disables-hooks)
- [2. The channel is blind at `Edit` boundaries](#2-the-channel-is-blind-at-edit-boundaries)
- [3. The sandbox cannot reach the host on this box](#3-the-sandbox-cannot-reach-the-host-on-this-box)
- [4. The interleaved-thinking guard, and why the cheap version is not one](#4-the-interleaved-thinking-guard-and-why-the-cheap-version-is-not-one)
- [5. Task quality: ten candidates screened, five discarded](#5-task-quality-ten-candidates-screened-five-discarded)
- [6. The screens are complementary](#6-the-screens-are-complementary-and-neither-is-redundant)
- [7. A correction to the previous round](#7-a-correction-to-the-previous-round)
- [8. The failure sample is the workflow's input contract](#8-the-failure-sample-is-the-workflows-input-contract)
- [9. An unresolved verdict does not mean the actor erred](#9-an-unresolved-verdict-does-not-mean-the-actor-erred--three-ways-not-two)
- [10. What the hints actually said](#10-what-the-hints-actually-said-and-whether-they-stayed-directional)
- [Settings, and what is comparable to what](#settings-and-what-is-comparable-to-what)
- [Cost](#cost)
- [Open questions](#open-questions)

## Conclusions

1. **`--bare` disables hooks outright**, `--settings`-supplied ones included, so
   bare mode and this design are mutually exclusive. Measured directly
   ([§1](#1---bare-disables-hooks)).
2. **`updatedToolOutput` cannot carry a hint at an `Edit` boundary** — no
   free-text field in the tool's response, and the field is schema-validated.
   In the one steered run made, **all three** interventions landed on `Edit` and
   **none reached the actor** ([§2](#2-the-channel-is-blind-at-edit-boundaries)).
   The host-side log caught every one, so the spec's "a lost hint must be
   detectable" held under a real loss — which is the first time that constraint
   has been tested rather than asserted.
3. **The channel is blind precisely where steering matters most.** (2) is not a
   detail: the Supervisor's off-track verdicts cluster at the moments the actor
   commits code, and those are the moments with nowhere to append. The
   *mechanism* is certain — the schema has no field to append to, which is a
   property of the code. The *clustering* rests on **one run and three
   verdicts**, and is a hypothesis until a second steered run either repeats it
   or does not.
4. **A steered run needs `capture="proxy"` on the OpenRouter path**, and the
   obvious guard against not having it does not work: a direct run with no proxy
   at all still scored **10 signed reasoning blocks out of 10**
   ([§4](#4-the-interleaved-thinking-guard-and-why-the-cheap-version-is-not-one)).
5. **Five of ten screened SWE-bench Pro candidates are broken tasks**
   ([§5](#5-task-quality-ten-candidates-screened-five-discarded)), and the cheap
   screens are **complementary**: each catches a defect class the others cannot
   see ([§6](#6-the-screens-are-complementary-and-neither-is-redundant)). The
   fifth was found by another pair after this report called it determinate, and
   the reason the screen here missed it generalizes: **a screen that cannot fire
   and a screen that fired and found nothing are different results**, and this
   report conflated them in one direction after correctly separating them in the
   other.
6. **A hint can be lost a second way, and that one takes the log with it.** A
   `null` message content killed the Supervisor's polling thread mid-run; the
   remaining boundaries were never judged, and the host-side log — the record
   the detectability constraint depends on — stops rather than reporting the
   gap ([§2](#2-the-channel-is-blind-at-edit-boundaries)). Fail-open held and
   the in-sandbox hook log held. Fixed, and the gap is now counted rather than
   absent.
7. **An unresolved verdict has three causes, not two.** A run came back exit 2
   with `timed_out == 0` and the actor had never started — the image cannot
   execute the mounted binary. The gate is `timed_out == 0` **and**
   `agent_complete == 1` **and** `claude_code.exit_code == 0`, and
   `freeze_sample.py` refuses rather than warns
   ([§9](#9-an-unresolved-verdict-does-not-mean-the-actor-erred--three-ways-not-two)).
8. **The failure sample is a contract, not an artifact.** The workflow starts
   from an already-cached failure, so its input is a self-contained directory —
   dataset row, typed conversation, verdict, patch, provenance, manifest — and
   the next pair builds against it
   ([§8](#8-the-failure-sample-is-the-workflows-input-contract)).
9. **The previous round's "the failure is deterministic, not flaky" was an
   `n=2` artifact** ([§7](#7-a-correction-to-the-previous-round)).

## 1. `--bare` disables hooks

The round was instructed to run `bare=True`. It cannot.

One probe, run twice, differing in exactly that flag: a throwaway workspace, an
isolated `CLAUDE_CONFIG_DIR`, a `--settings` file registering a `PostToolUse`
hook that logs its payload and appends a tagged marker to `Bash`'s `stdout`, and
a prompt whose whole job is one `cat`.

| Run | hook fired | marker in the trace |
|---|---|---|
| `--bare` | **no** (hook log never created) | 0 |
| no `--bare` | yes | 3 |

The harness docstring's "minimal mode: no hooks, plugins, MCP config" is exact,
and the binary says the same thing in its own words — `hooks are disabled in
this mode (--bare)`, read out of the pinned 2.1.212 binary. **The measurement
came first; the string is independent corroboration** that this is the contract
rather than a version accident. Since hooks *are* the phase-C mechanism, bare
mode is unavailable to this design at any setting.

Bare mode was wanted for two different things, and they separate cleanly:

| what bare bought | directed replacement | verified |
|---|---|---|
| no subagents (so the proxy converter's [thread-loss defect](../injection_shape/REPORT.md) cannot bite) | `--disallowedTools …,Task` | argv, one flag |
| "the repo under test cannot inject instructions into the harness" | **`--setting-sources user`** | measured, below |

**The second half is a real exposure and it is now measured, not assumed.** With
a planted project `.claude/settings.json` and a planted `CLAUDE.md` in the
workspace:

| | our `--settings` hook | the repo's project hook | the repo's `CLAUDE.md` obeyed |
|---|---|---|---|
| no flag | fires | **fires** | **yes** |
| `--setting-sources user` | fires | no | no |

So the repo under test really can drive our harness when `bare=False`, and
`--setting-sources user` shuts that off without touching hooks. Valid sources
are `user` / `project` / `local`; `--settings` is always loaded and is not a
selectable source. The project-hook half is binary and solid; the `CLAUDE.md`
half rests on the actor not obeying a planted instruction across two runs, which
is model behaviour and therefore the weaker claim.

**For this round the exposure is nil and the flag is not used**, so that both
arms keep identical argv: all four screened candidate repos were checked at
`base_commit` and none carries `CLAUDE.md`, `.claude/`, `AGENTS.md` or
`.cursorrules`. Wiring `--setting-sources` into the production path belongs with
task 05.

Recorded in [spec §10](../../../docs/trace-synthesis/spec.md#10-what-is-measured-about-hooks).

## 2. The channel is blind at `Edit` boundaries

One steered run was made before the setting changed (openlibrary, OAuth auth,
`stream` capture — **not** comparable to anything else here, and reported only
for the mechanical fact it establishes). Frozen at
`oldsetting-steered-stream-rollout-10`.

The Supervisor judged **24 tool boundaries** and decided to intervene at three.
All three reached the actor as far as the Supervisor was concerned. **None
reached it at all.**

| seq | tool | Supervisor | hook |
|---:|---|---|---|
| 12 | `Edit` | off track, hint emitted | `no text field in tool_response` |
| 15 | `Edit` | off track, hint emitted | `no text field in tool_response` |
| 21 | `Edit` | off track, hint emitted | `no text field in tool_response` |

`Edit` answers with `{filePath, structuredPatch, userModified, …}` — a
structured object with no `stdout`/`content`/`output`/`result` string. Claude
Code validates `updatedToolOutput` against the tool's declared output schema and
falls back to the original on a mismatch, so a text field cannot be invented.
`grep -c oracle_hint` over both the raw event stream and the converted
conversation returns **0**.

Three things follow, and they point in different directions:

- **The detectability constraint held.** The spec's one hard requirement is that
  a lost hint be detectable rather than silent. The host-side log named the loss,
  the tool, and the reason, on all three. This is the first time that constraint
  has met an actual loss instead of a hypothetical one.
- **The channel is blind at the worst possible moment.** The Supervisor's
  off-track verdicts land where the actor writes code, and `Edit`/`Write` are
  exactly where nothing can be appended. A `PostToolUse` + `updatedToolOutput`
  design reaches the actor while it reads and not while it commits.
- **Task 02's recommendation is narrower than it reads.** That round exercised
  `Bash` and `Read`, both of which carry text. "The channel survives both
  converters" is true and is not the same claim as "the channel is available".

Mitigation in this round's rig, and it is a mitigation rather than a fix: the
hook now tests whether a boundary can carry a hint *before* asking, tells the
Supervisor, and the Supervisor carries an unreachable intervention forward to
the next boundary that can take one. What that costs is timeliness — the hint
arrives after the edit rather than before it — and this round has not measured
whether a late hint still steers.

### A second way to lose a hint, found by losing thirteen boundaries

The `Edit` blindness is a property of the channel. The steered run made against
the new setting hit an unrelated one that is a property of *this rig*, and it is
worth recording because its signature from the actor's side is identical to no
hint being warranted.

The Supervisor's poller is one thread. A model reply came back with
`"content": null` — the key present, the value not a string, which the response
*shape* check accepts — `json.loads(None)` raised `TypeError` out of `judge()`,
and the thread died at boundary 13. The run continued for its full length with
no Supervisor at all. Every boundary after that point: the hook wrote its
request, waited out its 100 s deadline, failed open, and the actor saw an
ordinary tool result.

| | |
|---|---|
| boundaries the hook asked about | 16 |
| judged | 13 |
| **unjudged, poller dead** | **3 and counting when the run was stopped** |
| hook turnaround when answered | 4.0–18.5 s (n=13) |
| hook turnaround when not | 100 s, then fail-open |

Three properties of the design held and one did not. **Fail-open held**: no
tool call broke, and the actor's work was never corrupted by a Supervisor
failure. **The in-sandbox hook log held**: it names all three timeouts, with
their request ids. **The host-side log did not** — the crash happened *before*
the judgement was written, so the host's own account of the run simply stops,
and the host log is the thing the spec relies on to make a loss detectable. A
loss that stops the log cannot be found in the log.

Fixed three ways, since one of them alone would leave the hole: the reply parser
treats a non-string `content` as a model error like any other; the poller cannot
die, because a raising judgement is caught per request; and a request the judge
could not answer is **still answered**, so a broken judgement never becomes a
stalled tool call. The gap is now recorded host-side as `boundaries_unjudged`
rather than being absent — [`analyze.py`](analyze.py) counts it separately from
judgements, because counting it as one would report a hole in the belief state
as coverage. The crashed run's logs are kept at
`runs/steered-qutebrowser/r10-crashed/` as the evidence for all of this.

**The general lesson is the one this report keeps re-learning**: silence is not
a measurement. A supervisor that has stopped judging and a supervisor that sees
nothing worth saying produce the same trace.

## 3. The sandbox cannot reach the host on this box

`ufw` is active with `default deny (incoming)` and allows only `tailscale0`,
`22/tcp` and `41641/udp`. Nothing on the Docker bridge (`172.17.0.0/16`) can
open a host port. Reproduction, from inside a live instance container:

```
$ getent hosts host.docker.internal
172.17.0.1      host.docker.internal
$ python3 -c "import urllib.request; urllib.request.urlopen('http://host.docker.internal:9711/', timeout=5)"
urllib.error.URLError: <urlopen error timed out>
```

with the host process demonstrably listening (`ss -tlnp` shows
`LISTEN 0.0.0.0:9711`).

Two consequences, of different sizes:

- **For the Supervisor: solved, and the fix is better than what it replaced.**
  The hook now reaches the Supervisor by dropping a request file into the
  bind-mounted workspace and waiting for the answer beside it. No network leaves
  the sandbox at all, which fits
  [spec §3](../../../docs/trace-synthesis/spec.md#phase-c--the-guided-rollout)'s
  host-side-belief-state argument better than an HTTP endpoint did.
- **For `capture="proxy"`: hard blocked.** The agent reaches the recorder by
  `ANTHROPIC_BASE_URL=http://host.docker.internal:<port>`, which is the same
  wall. This is a property of the box, not of the code, and it means the shipped
  proxy capture has never run here. Fixing it is a host firewall change and it is
  with `machine-setup`; a per-port allowance would not be enough, since
  `DEFAULT_BASE_PORT` is 20000 and each agent call takes a different port, so it
  is a range.

## 4. The interleaved-thinking guard, and why the cheap version is not one

On the OpenRouter path `cc-reverse-proxy` injects two things Claude Code does not
send for itself — it mirrors `Anthropic-Beta` to the `X-Anthropic-Beta` header
OpenRouter actually reads, and it pins `require_parameters: true` so OpenRouter
cannot route to a provider that drops `thinking`. Both failures are silent, and
what they cost is the actor's own reasoning, which is the entire value of the
trace ([spec §10](../../../docs/trace-synthesis/spec.md#10-what-is-measured-about-hooks)).

The natural guard is "assert the trace carries signed reasoning blocks". **It
does not discriminate.** Measured over the two traces already in hand:

| trace | path | reasoning blocks | signed | thinking replayed |
|---|---|---:|---:|---:|
| `oldsetting-steered-stream-rollout-10` | OAuth → the Anthropic API, `stream` | 9 | 9 | not measurable |
| `baseline-ansible-rollout-0` | **direct to OpenRouter, no proxy**, `stream` | 10 | 10 | not measurable |
| `baseline-navidrome-rollout-0` | OpenRouter **through the proxy**, `proxy` | 30 | 30 | **69** |

The degraded configuration scores a perfect 10/10 on the cheap check. Signed
reasoning in a *response* proves the model thought once; it says nothing about
whether the conversation replayed that thinking on later turns, which is what
interleaved thinking across turns means.

The third row is the first run made through the required configuration, and it
is what the discriminating check looks like when it passes: **69 captured
request bodies echo a prior assistant `thinking` block back**. The first two
rows report `not measurable` rather than `0` for the same reason the code does
— they are `stream` captures, and there is no wire to read. That distinction is
the point: a guard that cannot tell "absent" from "unobserved" would have
scored the degraded run as a pass twice over.

So [`analyze.py`](analyze.py) reports two numbers: `signed` as a floor,
annotated as passing on the degraded path, and `thinking_replayed` — how many
captured **request** bodies echo a prior assistant `thinking` block back. The
second is the real check, it is visible only on the wire, and that is an
independent reason the proxy capture is required rather than preferred. Where
there is no proxy log it reports `-1`, so "not measurable" is never read as
"measured zero".

## 5. Task quality: ten candidates screened, five discarded

~30% of SWE-bench Pro public is broken (OpenAI, 2026-07-08, which also retracts
that team's earlier recommendation to adopt the dataset;
[survey](../../../docs/research/swebench-pro-task-quality.md)). So "the rollout
failed" is not evidence the *agent* erred, and a gate runs before an instance is
used. The criterion is **determinacy**: is the behavior the hidden tests judge
pinned down by the problem statement, the requirements, the interface, and the
repo at `base_commit`?

Note what does **not** screen: issue #261 selects candidates on *mixed outcome*,
so "some rollout resolved it" is true of every candidate by construction. A coin
flip between two self-consistent readings is reachable and still not pinned.

All ten of the issue's candidates were screened here. **Five are broken** — 5 of
10, in the neighborhood of OpenAI's ~30% but not a measurement of it: this pool
was selected on mixed outcome and is ten instances.

**Screening is no longer this task's job.** A dedicated pair
(`swelab-screen-impl` / `-review`) now audits all 40 of issue #261's instances
against a three-layer criterion, and feeds confirmed candidates here as they
land. This section is what was screened before that split, kept because the
methodological result in [§6](#6-the-screens-are-complementary-and-neither-is-redundant)
came out of it — and because one of its verdicts was **wrong**, which is the
more useful half.

| Instance | diagonal screen | token screen | verdict |
|---|---|---|---|
| `internetarchive/openlibrary` `5de7de19` | **fires** | clean | **broken** — misleading prompt |
| `ansible/ansible` `c1f2df47` | clean | **fires** (`destinationAddress`) | **broken** — underspecified prompt |
| `future-architect/vuls` `abd80417` | n/a — no nouns, no types | **fires** (`parseRpmQfLine`) | **broken** — overly strict tests |
| `navidrome/navidrome` `b3980532` | clean | **fires** (`lastFMAPIKey`) | **broken** — overly strict tests |
| `navidrome/navidrome` `50015182` | clean | clean | determinate — **harvesting** |
| `future-architect/vuls` `4c04acbd` | clean | clean | determinate — **harvesting** |
| `qutebrowser/qutebrowser` `9ed748ef` | n/a — no nouns, no types | clean | determinate — **harvesting** |
| `gravitational/teleport` `b4e7cd3a` | clean | clean | **broken** — overly strict tests (**corrected**, see below) |
| `tutao/tutanota` `fe240cbf` | clean | clean | determinate — reserve |
| `protonmail/webclients` `a6e6f617` | clean | clean | determinate — reserve |

Full evidence per instance in [`task-validation/`](task-validation/), written so
a reviewer can re-derive each call rather than take it.

**openlibrary — misleading prompt.** The requirements say "**The method**"
three times; the interface says `Type: Function` three times, same three units.
Each half is self-consistent and they contradict each other, which is OpenAI's
*misleading prompt* category ("contradicts what tests require"). The agent's
module-level placement was a defensible reading of a self-contradictory spec, not
an error. (OpenAI's own published example of this category is
`OpenLibrary-77c16d5` — the same repository.)

**ansible — underspecified prompt.** Rollout 0 graded 3 passed / 1 failed, and
the failure is `test_api_parameters`, which loads a fixture the *test patch*
adds and asserts an API-key→attribute mapping:

```python
assert p.dst_address == 'annoying_user'      # fixture key: destinationAddress
assert p.peer_selection_mode == 'sequential' # fixture key: peerSelectionMode
```

Four checks, all negative: the three API spellings appear in **none** of the
prompt texts; **no** `bigip_message_routing_*` sibling module exists at
`base_commit` to copy the map from; `dst_address` appears **nowhere** in
`lib/ansible/`; and the only in-repo precedent for `destinationAddress`
(`bigiq_application_*.py`) maps it to `destination_address` — *pointing at a
different answer than the test requires*. The interface calls `Parameters` the
"base parameter container **defining API field mappings**" and never says what
they are. A hint here could only steer by supplying the mapping, which is
handing over the answer.

**vuls `abd80417` and navidrome `b3980532` — overly strict tests, by one
shared defect.** In both, a hidden test calls an **unexported symbol the prompt
never names**, while the `interface` field says *"No new interfaces are
introduced."*

```go
// vuls: the test calls a method the requirements never mention
gotPkg, gotIgnored, err := o.parseRpmQfLine(tt.args.line)

// navidrome: the test compares against a constant the requirements never name
Expect(agent.(*lastfmAgent).apiKey).To(Equal(lastFMAPIKey))
```

In each case the *behavior* is specified and the **name and signature are not**.
vuls's requirements name `pkgPs`, `postScan` and `getOwnerPkgs` and describe the
line-parsing rules in prose, but never say that parsing must be factored into a
method called `parseRpmQfLine` taking one line and returning
`(pkg, ignored, err)`. navidrome's say the constructor should "fall back to a
built-in shared API key" without naming the constant the test then compares
against by symbol. A solver that implements exactly what was asked fails to
compile against the test — which is OpenAI's *overly strict tests* ("forces
implementation detail the prompt never specifies"). The `interface` field is not
merely silent here, it is **wrong**: new unexported interfaces are precisely
what the tests require.

**teleport — the verdict this report got wrong.** It was called determinate
here. `swelab-screen-impl` called it broken, and the owner confirmed that
reading independently. The test patch's only substantive change is a parameter
type: `buildKeyLabel([]byte(tc.input), …)` becomes `buildKeyLabel(tc.input, …)`.
The expected-value table is unchanged row for row and the masking behavior
already exists at `base_commit`, so a solver that implements `MaskKeyName`
exactly as the `interface` field specifies and leaves `buildKeyLabel`'s existing
signature alone satisfies every sentence of the task and still scores zero — the
test does not compile.

**Why the screen here missed it is the interesting part.** This report checked
that the `interface` field was complete and precise *about the symbol it
describes*, and it is. It never asked whether that symbol is **the one the
grading test evaluates**. It is not: `TestBuildKeyLabel` grades `buildKeyLabel`,
and the `interface` field names only `MaskKeyName`. The dataset's own
anti-false-negative mechanism was applied to function A while grading happened
on function B.

**Five kept.** `navidrome/50015182`, `vuls/4c04acbd` and `qutebrowser/9ed748ef`
are in the harvest; the other two are reserve. The clean ones are clean for
visible reasons — qutebrowser's requirements give the exact error-message
suffixes the tests assert and the exact normalization ranges (`h` → 0–359,
others → 0–255); tutanota's interface names the enum and all four of its values.

Every one of these verdicts is **provisional in the same way**: the screens say
the *task* is well-posed. The other half of determinacy — was *the specific
thing the actor got wrong* pinned down? — cannot be answered until a failure is
harvested, and is answered per failure, not per instance.

## 6. The screens are complementary, and neither is redundant

A methodological result, not a footnote to §5: **each screen has caught a broken
task the others passed**, and the teleport correction added a third defect class
that none of the ones running here could see.

| | what the test needs | what the solver holds | caught by |
|---|---|---|---|
| **unpinned literal** | a literal the tests require | the four inputs never mention it | token screen — ansible, vuls `abd80417`, navidrome `b3980532` |
| **contradiction** | one reading | `requirements` and `interface` assert two, each self-consistent | diagonal — openlibrary |
| **coverage gap** | a symbol's exact signature | the `interface` field never names that symbol | **neither** — teleport slipped through |

- The **diagonal** (`requirements` noun vs `interface` `Type:`) caught
  openlibrary, which the token screen cannot see: *method* and *Function* both
  appear in the prompt, so no token is unpinned. The survey predicted this blind
  spot — and records that **no published detector diffs Pro's `requirements`
  against its `interface` at all**. Both fields are Pro-specific; the detectors
  in the literature were built for SWE-bench, which has neither.
- The **token screen** caught three, all invisible to the diagonal.
- **Neither catches teleport.** `string` and `[]byte` both appear in the task
  text, so no token is unpinned; the two fields agree, so the diagonal is clean.
  What is wrong is *coverage*: the graded symbol is absent from the `interface`
  field entirely. The third screen that follows — parse the symbols the test
  patch calls, diff against the `interface` field's `Name:` set, alarm on a
  graded symbol the field never names — belongs to `swelab-screen-impl`, who is
  running it across all 40.

**And the screens are not three peers — two of them are ordered.** The diagonal
compares `requirements` against `interface` *on the assumption that both are
talking about the graded symbol*. When they are not, it does not report a
contradiction and it does not report a problem: it silently reports agreement,
because the two fields it compared really do agree about a function nobody is
grading. So the coverage screen is a **precondition** for reading the diagonal,
not an alternative to it, and a diagonal run without it should report *N/A*
rather than *clean*.

That is the same failure this report made twice in a row, in opposite
directions: for vuls `abd80417` it correctly recorded the diagonal as
inapplicable rather than clean, and for teleport it recorded a clean diagonal
whose precondition did not hold. **A screen that cannot fire and a screen that
fired and found nothing are different results**, and conflating them is how a
broken task gets a passing grade from a working detector.

One thing no screen does: judge whether a defect matters to a solver. All three
are cheap syntactic filters whose output is an alarm, and every verdict in §5
was read by hand — including the one that was wrong.

## 7. A correction to the previous round

[The handmade-instance report](../handmade_instance/REPORT.md) concluded: *"The
failure is deterministic, not flaky. Three grading attempts within the run, and a
second independent rollout, all produced the identical verdict."*

**The re-grading half stands; the re-rolling half does not.** Re-harvesting the
same instance with the same command produced:

| round | samples | unresolved |
|---|---:|---:|
| previous | 2 | 2 |
| this | 3 | 1 (rollouts 0 and 1 **resolved**) |

Five samples, three unresolved. The instance is genuinely mixed-outcome —
consistent with issue #261's 1/2 — and "deterministic" was an `n=2` artifact.
The re-harvested failure (`failure-rollout-2`) reproduced the *same* failure
mode, module-level placement with the same 16 tests failing, so the mode is
stable even though the outcome is not.

This matters beyond bookkeeping: it is the reason a steered run's
resolved/unresolved column cannot be read on its own. Against a ~40% base
failure rate, one steered run that passes is not evidence of steering.

## 8. The failure sample is the workflow's input contract

The phase-C workflow does **not** re-run a rollout to find a failure. A full
rollout + test sweep has already happened by the time this pipeline is wanted,
and its traces are cached; re-running from scratch pays twice for a trace we
already own. The workflow instead starts at **write the guidebook**, over a
hand-assembled dataset row:

> `[an existing failed trace] → write the guidebook → a new actor run with the
> Supervisor and hook attached → store the resulting conversation`

So the artifact this round produces is not "a frozen run directory" — it is a
**mountable input**, and the guidebook agent and the workflow that mounts it are
built against its layout. [`freeze_sample.py`](freeze_sample.py) writes it, at
`/home/ubuntu/dev/swe-lab-artifacts/trace_synthesis/<instance_id>/`:

| File | What it is | Why it is separate |
|---|---|---|
| `instance.json` | **every** field of the dataset row — `problem_statement`, `requirements`, `interface`, `patch`, `test_patch`, `fail_to_pass`, `pass_to_pass`, `base_commit`, `repo`, … | the consumer must not need the parquet, the loader, or this repo |
| `failed_conversation.json` | the **typed** `Conversation` of the failing rollout | a consumer that parses the raw stream is re-implementing a converter this repo owns and tests |
| `verdict.json` | which required tests failed, per grading attempt | see below — this is the flakiness check, not bookkeeping |
| `patch.diff` | the patch that rollout submitted | what the Oracle diffs against the gold patch |
| `raw/` | the unconverted trace | corroboration for the typed conversation, never the primary input |
| `PROVENANCE.json` | the run's provenance plus capture mode, model, base URL, concurrency, which OpenRouter key, and that key's balance before and after | a wall clock without its concurrency, or a cost without its key, is not a measurement |
| `MANIFEST.sha256` | checksum of every file above | the repo keeps the pointer and the manifest; the bytes live off-repo |

**The invariant is self-containment**: a consumer needs the directory and
nothing else. That is what makes it a contract rather than a convention, and it
is the one property to check when changing this.

`verdict.json` carries a per-attempt breakdown for a specific reason. The
grading entry retries, so the suite runs more than once, and comparing the
attempts is a free flakiness check: **a required test that fails in every
attempt is a property of the patch; one that moves is a property of the suite.**
The qutebrowser sample reports `stable_across_attempts: true` with the same two
tests failing in all three — which is what makes it worth steering. A sample
whose verdict is not stable is not a reasoning failure, and the field says so
before anyone builds a guidebook on it.

## 9. An unresolved verdict does not mean the actor erred — three ways, not two

The standing rule was: exit 2 is a reasoning failure, exit 1 is infrastructure,
never conflate them, and check `claude_code.timed_out` first because contention
turns the former into the latter. Harvesting `protonmail/webclients` produced a
run that satisfies every part of that rule and is still not a reasoning failure:

| | value |
|---|---|
| workflow exit | **2** — unresolved |
| `claude_code.timed_out` | **0** |
| `claude_code.exit_code` | **127** |
| `claude_code.wall_seconds` | **0.69** |
| `agent_complete` | **0** |
| stderr | `/opt/claude-code/claude: cannot execute: required file not found` |
| the submitted patch | 2248 lines of `yarn.lock` and **no source change** |

The actor never started. `cannot execute: required file not found` is what bash
says when an ELF file's interpreter is missing, so that image cannot run the
mounted `linux-x64` binary at all. The grading suite then failed for the honest
reason that the function it imports was never written, and the workflow reported
the instance unresolved — which is true, and says nothing about any agent's
reasoning.

**So the gate is three checks, not two**, and both `harvest_one.sh` and
[`freeze_sample.py`](freeze_sample.py) now apply all three before a run is
written out as a failure sample:

```
claude_code.timed_out == 0    the run was not killed at its budget
agent_complete        == 1    the actor finished its work
claude_code.exit_code == 0    the actor ran at all
```

`freeze_sample.py` **refuses** rather than warns. A warning on a path that ends
in training data is a note nobody reads.

Two things follow that outlive this instance:

- **Image executability is a property of the repo family, not the instance**,
  and it is cheaper to establish than task quality: the harness already probes
  it on every run — `rollout/a0/claude.info` opens with
  `$ /opt/claude-code/claude --version` and its exit code — so *one* completed
  rollout settles the whole family. `qutebrowser`, `openlibrary`, `vuls`,
  `navidrome` and `NodeBB` are proven runnable here; `protonmail/webclients` is
  proven not.
- **A screen that runs before task quality saves the expensive screen.**
  Determinacy analysis on a family whose image cannot host the actor is work
  spent on a task that can never be sampled.

## 10. What the hints actually said, and whether they stayed directional

One of the four questions this round exists to answer is whether hints stay
**directional** — pointing at something to check — or slide into specifics that
hand over the answer. It cannot be settled by policy text, only by reading what
the Supervisor emitted, so here is all of it, verbatim, from the steered run on
`qutebrowser/9ed748ef`. The guidebook's stage 4 exists to prevent exactly the
mistake the baseline made: tightening the parser's entry condition so two
pre-existing invalid strings change which error they raise.

| # | tool | what the Supervisor said |
|---:|---|---|
| 6 | `Bash` | "Careful with rounding there — does the conversion you read in the existing parse helper round or truncate, and do your numbers match the values the existing percentage tests already expect?" |
| 9 | `Read` | "Two of those invalid strings have unbalanced parens inside an otherwise well-formed call — with your stricter gate, do they still take the same path through the parser, or do they now fall through and pick up a different error message?" |
| 12 | `Bash` | "Worth pausing on where those expected message suffixes came from — for the invalid strings the task never mentions, it'd be safer to check what the code produced *before* your edit (git can show you) than to read the expectation off your own implementation." |

Three observations, and the middle one is the uncomfortable one:

- **All three are questions or checks, not instructions.** None names the fix.
  Nothing says "keep `value.index('(')`", "don't use a regex", or "the message
  should be `must be a valid color value`" — each names *an observation to make*
  and leaves both the diagnosis and the remedy to the actor.
- **Hint 9 is close to the line.** It names the input class (unbalanced parens),
  the actor's own choice (the stricter gate), and the symptom to look for (a
  different error message). An actor that follows it has been handed the failing
  case; what it has not been handed is what to do about it. Whether that is
  "directional" or "a specific with a question mark" is the
  [specificity dial](../../../docs/trace-synthesis/spec.md#8-what-hint-specificity-now-trades)
  itself, and this report's position is that it is the strongest hint the policy
  should allow — recorded here so a reviewer can disagree with a concrete text
  rather than with a principle.
- **The Supervisor found the trap without being told which boundary it was.**
  The guidebook names the trap; nothing tells the Supervisor when the actor is
  at it. It fired at boundary 9, on a `Read`, immediately after the `Edit` where
  the actor introduced the stricter gate — which is the carry-forward working,
  not luck (see below).

**And the deferral cost is now measured**, which [§2](#2-the-channel-is-blind-at-edit-boundaries)
left open. Two of the seven off-track verdicts landed on `Edit` boundaries and
could not be delivered there:

| | |
|---|---|
| off-track verdicts | 7 |
| hints delivered | 3 |
| suppressed by cooldown | 2 |
| **unreachable at an `Edit` boundary** | **2** |
| **of those, delivered late by carry-forward** | **1**, at the next boundary |
| **lost permanently** | see `analysis.json` — `deferral.lost_permanently` |
| carry-forward latency | **1 boundary** |

So the mitigation is not theoretical: the intervention judged at boundary 8, on
an `Edit`, was carried to boundary 9 and delivered there, one boundary late.
That is the cheapest possible latency and it is not guaranteed — it happens to
be cheap here because the actor read a file immediately after editing one.

## Settings, and what is comparable to what

| | `oldsetting-steered-stream-rollout-10` | `failure-rollout-2` | `baseline-ansible-rollout-0` | this round's harvest |
|---|---|---|---|---|
| auth | OAuth | OAuth | OpenRouter key | OpenRouter key |
| base URL | Anthropic API | Anthropic API | `openrouter.ai/api` | `openrouter.ai/api` |
| actor model | `claude-sonnet-5` | `claude-sonnet-5` | `anthropic/claude-sonnet-5` | `anthropic/claude-sonnet-5` |
| `bare` | False | False | False | False |
| subagents | allowed | allowed | denied | denied |
| capture | `stream` | `stream` | `stream` | **`proxy`** |
| interleaved thinking | n/a (native API) | n/a (native API) | **not measurable** | required + asserted |
| concurrency | 1 | 1 | 1 | **3** |

**Nothing in the first three columns is comparable to the last**, and the
mechanical findings above are quoted for what they establish about the channel
and the harness, never as compliance or cost baselines.

**Wall clocks from this round are not comparable to earlier ones either.** The
harvest ran three rollouts at a time on a 4-vCPU box, each with its own instance
container, its own proxy port and its own key, so every wall-clock number
carries its concurrency. `claude_code.wall_seconds` and the workflow's own
`wall_s` are reported per run with that annotation and are not averaged across
rounds.

The concurrency also changes what an exit code means, which is the one place it
could corrupt a result rather than merely slow it: contention can turn a run
that was on its way to a reasoning failure (exit 2) into a timeout, and a
timeout reports as an infrastructure failure (exit 1). **No exit-1 run is
collected as a failure sample.** Every harvested candidate is checked for
`claude_code.timed_out == 0` before its exit code is read as anything at all.

## Cost

Accounting is **per key**, because there is nothing else for it to be per: the
25 keys in the pool are 25 separate OpenRouter accounts, each with its own
balance. Queried individually on 2026-09-01, **25 of 25 answered and all held
credit**, with remaining balances spread over **$117–$278**. There is no
account total to report and no shared pool to run down.

That is a correction. An earlier version of this section read one key's
`/api/v1/credits`, saw `total_credits` / `total_usage`, and reported the number
as the account balance — $182 where the pool actually holds about **$4,915**,
low by a factor of 27. The error is not arithmetic; it is **one observation
plus an unexamined model of what the observation meant**, and it is the third
instance of that shape in this round alone (see the box below).

The practical consequences, since they changed what we run:

- **Budget is not a constraint on this round.** At $0.6–0.9 a rollout, a single
  key absorbs two to three hundred of them. Ten candidates at several samples
  each is not close to a limit.
- **Keys are rotated for concurrency, not for thrift.** OpenRouter's documented
  model rate-limits per key, so parallel harvests take different ones
  (`--key-index`). We have not hit a 429, so that is a precaution rather than a
  measurement — the trigger to rotate is a 429, not a balance.

| Item | Runs | Cost |
|---|---:|---|
| openlibrary re-harvest (OAuth) | 3 | ~$1.9 actor |
| openlibrary steered (OAuth) | 1 | ~$0.6 actor + ~$0.83 supervisor (24 judgements, `opus-5`, ~$0.035 each) |
| ansible baseline (OpenRouter) | 1 | $0.29 |
| bare/auth probes (OpenRouter) | 2 | $0.02 |
| navidrome harvest | 0 | **killed before the first rollout completed**, on the proxy correction |

The Supervisor is not a rounding error: at ~$0.035 per boundary and 20–40
boundaries per rollout it is comparable to the actor's own cost, and it scales
with tool calls rather than with wall time.

### Three of the same mistake, in one round

Worth naming as one thing rather than three, because the fix is one habit:

| Claim | What it rested on | What it cost |
|---|---|---|
| "the failure is deterministic" | n=2, both unresolved | wrong in a merged report ([§7](#7-a-correction-to-the-previous-round)); n=3 this round resolved twice |
| "signed reasoning blocks prove the proxy path" | one property of one run, never tested against the degraded path | would have passed a silently degraded configuration ([§4](#4-the-interleaved-thinking-guard-and-why-the-cheap-version-is-not-one)) |
| "the pool shares one $182 balance" | one key's `/api/v1/credits`, read as account-level | understated the budget 27× and shaped which runs we were willing to spend |

Each is a **single observation carrying a property claim** — deterministic,
proves, shares. So: every property assertion in this report states its sample
size, and one derived from a single observation says so in the sentence that
makes it.

## Open questions

- **Does a late hint still steer?** The carry-forward mitigation in
  [§2](#2-the-channel-is-blind-at-edit-boundaries) delivers the intervention
  *after* the edit it was judged on. Whether an actor that has already written
  the code responds to a hint the way one that has not would is unmeasured, and
  it is now on the critical path.
- **Should `Write`/`MultiEdit` be treated like `Edit`?** Only `Edit` was
  observed. The likely answer is yes and it is not measured.
- **Is `PostToolUse` the right seam at all, given (2)?** `PreToolUse` sees the
  edit *before* it happens, which is where the intervention wants to be — and
  §4/§5 of the spec rule it out for reasons that predate this finding. Worth
  re-examining rather than re-litigating, and it belongs in an ADR if it moves.
- **Everything step 5 was actually for.** Whether hints steer a blind actor over
  a 20–50 call horizon, whether they stay directional, and whether the harness's
  injection guard hardens over a long rollout — all still unanswered, all
  gated on the firewall change.
