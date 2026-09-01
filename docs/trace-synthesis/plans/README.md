# Oracle-guided trace synthesis — task index

Ordered task index + status for the trace-synthesis component (per the repo's
planning convention: [`spec.md`](../spec.md) = target design, `plans/` = one
deep design per task, indexed here). Sizes: XS=1 file · S=1–2 · M=3–5 · L=5–8
(break down if larger).

**Single source of truth for status:** this table is the *only* live status for
these tasks. Any `plans/task-NN-*.md` that appears later is a point-in-time
**design record** — don't read status from it.

**The ordering principle is the cheapest falsifier first.** The pipeline rests
on one assumption — *that a supervisor holding a good guidebook can steer a
blind agent to a correct solution at tool-call granularity* — and on one
unmeasured mechanism. The owner ruled on 2026-09-01 that the **assumption is
taken as given**, so it is task **02** that can still kill the idea: if no hook
channel yields a genuine user turn at a tool boundary, the design needs a
different medium before anything after it is worth building. Task 01 keeps its
place at the head for a different reason — it produces the first real artifacts
the later tasks are designed against. The hook-mechanics research that preceded
this index is not a task: its results are recorded in
[`spec.md` §10](../spec.md#10-what-is-measured-about-hooks).

| # | Task | Status |
|---|---|---|
| 01 | **One instance, end to end** — an automated walkthrough producing the pipeline's first real artifacts on one real instance | ⬜ |
| 02 | **Measure the injection shape** — can a hook put a *visibly external* hint at a tool boundary, and does it survive conversion? | ✅ |
| 03 | **Hint log + conversion guard** (pure, tested) | ⬜ |
| 04 | **Oracle analysis task + guidebook schema** | ⬜ |
| 05 | **Supervisor + hook wiring in the sandbox** | ⬜ |
| 06 | **Trace-quality scorer** (decide whether to build) | ⬜ |
| 07 | **The `oracle_guided_trace` workflow + integrity separation** | ⬜ |
| 08 | **Batch run: N instances, measure yield / cost / quality** | ⬜ |
| 09 | **Converge redaction onto one home and publish behind a gate** — the header/body redaction itself shipped with task 10 | ⬜ |
| 10 | **Run the capture proxy inside the sandbox** — removes the host port scheme, the firewall dependency and the tailnet exposure | ✅ |
| 11 | **Start from a cached failure** — the `oracle_failures` dataset: a record that delegates the instance and stages the failure, plus the builder from a finished run — [`task-11-oracle-failures-dataset.md`](task-11-oracle-failures-dataset.md) | ✅ First record built locally: the qutebrowser/9ed748ef baseline failure (data gitignored by design) |

---

## Task 01: One instance, end to end

**Description:** Walk the whole pipeline over a single instance and keep what it
produces: a real phase-A failure (on an instance whose gold self-test resolves)
frozen with its conversation, a guidebook written against that failure in the
[spec's shape](../spec.md#phase-b--the-oracle), and a steered re-run of a blind
actor with every injected hint logged. The run is **automated** — the existing
CLI plus scratch scripts, not a person typing hints — and uses no production
code: nothing here is wired into a workflow definition.

Per the owner's 2026-09-01 ruling the core assumption is **taken as given**
rather than tested, so this is not the design's falsifier. Its value is the
artifacts: tasks 04, 05 and 06 are designed against what a usable guidebook and
a real steered conversation actually look like. The design record is
[`task-01-one-instance-end-to-end.md`](task-01-one-instance-end-to-end.md).

- **Acceptance:** a written record of the walkthrough — which candidates
  survived gold validation, the harvested failure and where it is frozen, the
  guidebook, the hint texts and where they were injected, the resulting verdict,
  and a judgement on whether the hints stayed directional or drifted into
  specifics. A **steered run that still fails is a complete result**.
- **Verification:** an [experiment](../../experiments/playbook.md) `REPORT.md`
  — hypothesis, logged run, conclusion.
- **Dependencies:** none. **Scope:** S

## Task 02: Measure the injection shape

**Description:** Settle the spec's
[head open question](../spec.md#11-open-questions) by measurement. What shape
can a hook actually put into the conversation at a tool boundary, and which
shapes pass the three tests the question now asks — the actor sees it, it is
**marked as an external injection**, and our conversion preserves it? (The
wire-level `role` field is not the criterion; owner, 2026-09-01.) `PostToolUse`
`decision: "block"` is already measured and fails the third (it lands as an
`attachment`). The candidates — `updatedToolOutput` carrying a tagged suffix
appended to the tool's real output, `PostToolBatch`'s `decision` /
`additionalContext`, and a re-confirmation of `additionalContext` at this
version — get measured together with the two event-coverage questions
(`PostToolUseFailure` for the spinning-after-an-error case, `PostToolBatch` for
the parallel-batch case), because those change what the experiment is asking.

Each candidate is measured on **two** things: what the actor does with it, and
what our typed `Conversation` conversion does with it.

- **Acceptance:** a table of candidate → transcript shape → what the model sees
  it as → whether the converter preserves it → any observation on whether the
  actor complies; plus a recommendation for the head question and, if no
  candidate survives with its marker intact, the evidence the owner needs to
  rule on materialization.
- **Verification:** an experiment `REPORT.md` with the raw transcripts kept.
- **Dependencies:** none (runs in parallel with 01). **Scope:** S
- **Outcome:** [`experiments/trace_synthesis/injection_shape/REPORT.md`](../../../experiments/trace_synthesis/injection_shape/REPORT.md).
  `PostToolUse` `updatedToolOutput` with a tagged suffix appended to the tool's
  real output is the recommendation — the only candidate kept by **both**
  converters. Survival turned out to be a property of the converter rather than
  the channel, materialization is not needed, and two defects surfaced that the
  task did not go looking for (`proxy_log_to_conversation` keeps only the last
  thread; routing the actor through a proxy changes whether it follows a hint).

## Task 03: Hint log + conversion guard

**Description:** What is left of the [phase D](../spec.md#phase-d--collection)
step once [task 02](#task-02-measure-the-injection-shape) removed the
materialization half: a host-side log of every hint the Supervisor injected, and
a guard that cross-checks it against the converted `Conversation`. Pure
host-side code — no Docker, no model calls — which is where the correctness
risk belongs.

The load-bearing half is the **guard**: a hint that is not present in the
converted trace must fail the conversion loudly. Silently emitting a hint-less
trace is the one fatal failure mode in the spec, and task 02 found two live
routes to it — `proxy_log_to_conversation` keeps only the last proxy record's
thread, so a hint delivered inside a subagent's conversation disappears; and a
hint injected after the actor's last API call never reaches the model at all,
which is legitimate but must be recorded rather than assumed.

- **Acceptance:** the guard is pinned by a named test (a run whose hint is
  absent from the converted trace → conversion errors, no trace produced);
  round-trip tests over the typed model; `tool_use` ↔ `tool_result` pairing
  preserved.
- **Verification:** unit tests, no Docker; the full quality bar.
- **Dependencies:** 02 (its outcome decided what is left to build).
  **Scope:** S–M

## Task 04: Oracle analysis task + guidebook schema

**Description:** Phase B as a `Task`: a sandbox with the golden patch, the
golden tests and the failed conversation mounted, the **git-history purge
off**, producing a validated `guidebook.md`. The schema enforces the
`justification` field per stage — the field that makes an honest hint possible
at all.

Phase B is independently useful: a guidebook is a readable artifact even
without phase C.

- **Acceptance:** schema validation rejects a guidebook with a stage missing
  its `justification`; the task declares `guidebook.md` as an output and the
  purge-off configuration is explicit rather than incidental.
- **Verification:** unit tests for the schema; one live run producing a
  guidebook a human judges usable.
- **Dependencies:** 01 (which shapes what a usable guidebook looks like).
  **Scope:** M

## Task 05: Supervisor + hook wiring in the sandbox

**Description:** The real machinery. Hook settings injected per run
(`--settings` + an isolated `CLAUDE_CONFIG_DIR`), a host-side Supervisor called
over the API with its own credential (the hook subprocess inherits none), the
belief state kept outside the sandbox, and an intervention record written per
decision. Includes the explicit timeout policy: the default is fail-open, so a
dropped decision must be **recorded**, never silently skipped.

- **Acceptance:** the guidebook and the belief state are provably absent from
  the actor's context and mounts (named tests, per the spec's
  [invariants](../spec.md#12-invariants-intended-none-enforced-today)); a
  dropped or timed-out supervisor decision appears in the run record; the
  Supervisor's hook response can never carry `updatedInput`, a deny decision or
  `additionalContext` — the three channels
  [§5](../spec.md#5-the-mechanism-decisions) bans, `additionalContext` included
  because it is delivered as a system reminder.
- **Verification:** unit tests over the hook payload handling; one live guided
  rollout end to end.
- **Dependencies:** 02, 04. **Scope:** M–L

## Task 06: Trace-quality scorer (decide whether to build)

**Description:** A blind judge — a model seeing only the visible prefix — is no
longer needed as a *leak detector*, because keeping the hints satisfies that by
construction. The open question is whether it earns its keep as a plain
trace-**quality** score: does this trace teach anything, or did the hint do the
work? **This task starts with the decision, and may end there.**

- **Acceptance:** either a written "not worth it" with the reasoning, or a
  scorer that produces a per-trace number plus evidence it correlates with a
  human reader's judgement on a sample.
- **Verification:** if built, a scored sample with human agreement reported.
- **Dependencies:** 01 (needs real traces to judge). **Scope:** M

## Task 07: The `oracle_guided_trace` workflow + integrity separation

**Description:** Wire A→B→C→D as a registered workflow on the existing
[workflow layer](../../decisions/ADR-0007-task-and-workflow-layer.md), with the
edges resolved from the store. The integrity half is not optional: every phase
B / C record carries the oracle-guided **policy stamp**
([ADR-0010](../../decisions/ADR-0010-benchmark-integrity.md) §5), so these runs
can never be pooled with benchmark numbers, and the
[result verifier](../../horizontal/plans/task-26-result-verifier.md) is left
free to flag them as contaminated — which is correct behaviour, not a bug.

- **Acceptance:** a named test asserting the stamp is on the record and that
  aggregation across differing stamps still errors; the verifier's contaminated
  flag on an oracle-guided run is asserted as *expected*, not suppressed.
- **Verification:** unit tests plus one end-to-end workflow run.
- **Dependencies:** 03, 04, 05. **Scope:** M

## Task 08: Batch run — N instances, measure yield / cost / quality

**Description:** Run the pipeline over a batch and produce the numbers that
decide whether it scales: kept-trace yield, cost per kept trace (a baseline
rollout + eval, an oracle analysis, a guided rollout + eval, and one supervisor
call per tool call — with the guided rollout still able to fail and cost all of
it for nothing), and a quality read on the traces. Also the first real data on
the [selection question](../spec.md#11-open-questions): measured `pass@10` band
versus the cheap "failed once, gold self-test passes" proxy.

- **Acceptance:** a report with yield, cost per kept trace, and a comparison
  against rejection sampling on the same instances.
- **Verification:** an experiment `REPORT.md`.
- **Dependencies:** 07. **Scope:** L

## Task 09: Converge redaction onto one home, behind a publishing gate

**Description:** `ClaudeCodeHarness(capture="proxy")` declares
`claude.proxy.jsonl` as a native output, so the proxy log is a registered run
artifact.

**The disclosure this task opened for is fixed** (see below); what opened it is
kept here because it explains the shape of the work that is left. The proxy
recorded the headers it forwarded — its `excludedRequestHeaders` dropped only
`host` and `accept-encoding`, its response `excludedHeaders` only the hop-by-hop
four — so **every proxy-captured run stored the run's `Authorization` bearer
token and the operator's account identifiers verbatim**, and nothing under
`harnesses/claude_code/` redacted anything. It was **latent rather than live**:
those artifacts land in `.cache/` and the cache-backed T1 store, and no running
path publishes a rollout artifact — but
[`.gitignore`](../../../.gitignore) states that full-conversation trace records
live off-repo in a HF dataset repo, so whoever first published a proxy-captured
trace would have published a credential with it. Task 02 put proxy capture near
this pipeline's critical path, which is why the task is filed here.

**The header and body redaction itself shipped with
[task 10](#task-10-run-the-capture-proxy-inside-the-sandbox)**, because once the
proxy runs in the sandbox it writes straight into the workspace and "write time"
is physically inside `cc-reverse-proxy`. Credentials, the operator's
organization / workspace ids, the rate-limit representative claim, `Set-Cookie`
and the request body's `metadata.user_id` are masked there by default, and
`swe_lab.harnesses.claude_code.redaction` checks a capture from this side. What
remains is the part that was never about a header list.

**1. Converge the redaction facts onto one home.** The same fact is written down
twice — in `src` and in
[`experiments/…/run_experiment.py`](../../../experiments/trace_synthesis/injection_shape/run_experiment.py)
— and the copies have already drifted twice: once in *membership* (the accepted
set knew the representative claim and `metadata.user_id`; the `src` set was
written narrower without consulting it) and once in *representation* (the
experiment writes `<redacted>`, `src` writes `[REDACTED]`, so the `src` scanner
calls **every** committed experiment capture dirty — 790 findings, all false).
`src` owns the canonical set, the placeholder constant and the body field list;
`experiments/` imports from it and never the reverse, since `experiments/` is
exempt from the hooks. The superset assertion in
`tests/test_proxy_redaction.py` is a splint until this lands.

**2. Keep the old placeholder as a named legacy alias, with a date boundary.**
The 37 committed captures are a **record** and must not be rewritten, so the
scanner has to accept `<redacted>` too. That is not a second source of truth: a
legacy alias is *closed* and dated, whereas two live constants keep diverging.

**3. Make "unclassified" a state the scanner can report.** Redaction is a
deny-list, so by construction an unenumerated field is recorded verbatim —
"unseen fields are redacted by default" is not true today and nothing enforces
it (ADR-0012 §4). The fix is three classes on the reading side: must be masked,
deliberately kept, and **everything else reported as unclassified until someone
classifies it**. The cost is one noise event whenever an upstream adds a field;
the alternative is silent publication.

What that defends against is worth stating precisely, because it changes the
design: **the risk is changing upstream, not upstreams steadily adding fields.**
Measured 2026-09-01 — across the 37 committed captures (158 records) the
Anthropic response header space holds 26 names, and a fresh real Anthropic
response returned 25 names of which **zero** had never been seen. The one field
that *was* new arrived by switching upstream: `Set-Cookie`, on the OpenRouter
path.

**4. The publishing gate and the wider PII sweep.** Redacting the envelope is
not the same as clearing a trace for publication: bodies carry repository
contents and whatever the agent typed. The gate is what stands between a scanned
capture and a HF dataset repo, and it is the part still missing.

- **Acceptance:** one home for the set, the placeholder and the body field list,
  with `experiments/` importing from `src`; the scanner reports unclassified
  fields; a publishing path that refuses an unscanned capture or one carrying
  unclassified fields.
- **Verification:** unit tests, including one that fails if the two sets diverge
  again; the scanner run over the committed captures reports **no** false
  findings.
- **Dependencies:** [task 10](#task-10-run-the-capture-proxy-inside-the-sandbox)
  (done — it shipped the write-time redaction this builds on).
  **Gates:** publishing any proxy-captured trace — not local runs. **Scope:** S

## Task 10: Run the capture proxy inside the sandbox

**Description:** *(The state below is the **pre-task** one. The task is done:
`ProxyRecorder` is deleted, the proxy runs in the sandbox, and the three
dependencies described here are gone — see the Verification line.)*

`ProxyRecorder` started `cc-reverse-proxy` **on the host** and the container
dialled back through the Docker host gateway. That made a
**required** component — on the OpenRouter path the proxy's `X-Anthropic-Beta`
mirroring and `provider` injection are what make interleaved thinking work at
all — depend on three fragile things:

1. **A host firewall rule.** `ufw`'s `default deny (incoming)` blocks the Docker
   bridge, so every box that captures has to allow the recorder's port ranges
   explicitly — a `machine-setup` concern, on a schedule this repo does not
   control. Until one was in place this component's experiments were hard-blocked
   for a round (2026-09-01).
2. **A port derived from a dataset index.** `port_for_index(index) = base_port +
   index` ([`proxy.py`](../../../src/swe_lab/harnesses/claude_code/proxy.py))
   with **no upper bound**, and the aggregator holds a second base at 25000. The
   firewall rule therefore has to open ranges, guessed from how large a sweep
   anyone expects to run.
3. **A listener bound to every interface.** `reverse_proxy.go` binds
   `:%d`, and `machine-setup`'s `base` role already carries
   `ufw allow in on tailscale0` — so these ports are reachable from any node on
   the tailnet. That comes from the `base` role's standing rule, not from
   allowing the recorder's ranges, which is the point: it is an exposure nobody
   chose, and no capture-side change introduced it.

Moving the proxy **into the sandbox** removed all three at once. Each container
has its own network namespace, so a **hard-coded** port cannot collide by
construction; the agent dials container loopback, so **no firewall rule is
needed**; nothing binds on the host, so the **tailnet exposure disappears**. It
also makes the design backend-agnostic — `GitHubJobSandbox` is handed a job that
is already running, where a host-side proxy has nowhere to stand.

**The mechanism already exists.** The proxy is a static, standard-library-only
Go binary, and `MountedAssetsObserver` + `Mount(executable=True)` is the same
path that already places the pinned Claude Code binary in the container. Nothing
new has to be built.

**Two known costs, written down now rather than discovered later:**

- **Lifecycle moves into the invocation script** — start in the background, poll
  for readiness, flush before teardown. The log is append-only JSONL, so a
  killed process truncates at a line boundary and every already-written line
  stays complete.
- **The log lands inside the sandbox, where the agent can reach it — and that is
  a new exposure, not a level one.** An earlier version of this paragraph called
  it "level, not new" on the grounds that `event_stream.jsonl` is already in the
  workspace and already just as reachable. Both halves are wrong, and the review
  of the implementing PR established it: the host-side recorder kept its log in
  a **host** temporary directory until teardown, so the agent could not reach it
  at all while it ran; and "as reachable" is not "as sensitive" — the proxy log
  carries HTTP **headers**, which the event stream does not. One real capture
  held **65** live credentials and operator identifiers across its 13 records
  where the stream capture held none. (An earlier header-only count of the same
  file said 39; the scopes differ — the wider one also covers the request body's
  account id and the rate-limit representative claim — so the two numbers are
  not comparable.)

  What makes it acceptable is therefore a mechanism, not a comparison:
  **redaction at write time**, so an unredacted capture never exists on disk
  inside or outside the sandbox, and **no unredacted capture may enter any
  collected artifact**. Post-hoc cleanup does not satisfy this — it leaves a
  window in which the raw file is on disk and reachable.

- **Acceptance:** proxy capture works with **no host firewall rule and no port
  allocation scheme**; `machine-setup` can then drop both `ufw` ranges together
  with their `defaults` variables and the bringup-acceptance row.
- **Verification:** **met, in the constructive form** — a proxy-captured run
  that binds **no host port at all**, with the agent dialing container-local
  loopback. Deliberately *not* "one proxy-captured run on a box with the rules
  removed": that criterion is weaker than the property it tests (it shows the
  rules went unused on one run, not that nothing can depend on them) and it is
  circular, since removing the rules waits on the very change it would verify.
  What was checked: the acceptance rollout bound no host listener of its own
  (`ss -ltnp`; the sole listener in the old range belonged to another agent's
  host-side proxy), and **both `ufw` rules were still present at the time** —
  which is what makes the run evidence that they are not load-bearing rather
  than evidence that they are gone. The structural half is stronger than the
  run: the backend publishes no ports at all
  (`test_up_maps_no_host_gateway`), so there is no host port to bind.
- **Dependencies:** none. It gates nothing today (the firewall workaround has
  landed), but it removes a required component's dependency on machine-level
  configuration. **Scope:** M

## Task 11: Start from a cached failure

**Description:** Make phase A skippable. A full eval sweep has already cached
the failures phase B needs, so the pipeline's input becomes a **dataset of
cached failures** rather than a fresh rollout: the `oracle_failures` dataset,
whose record names the underlying instance (dataset + id) and carries the one
failed attempt — typed conversation, grader's verdict, submitted patch. The
record **delegates** the whole runnable surface to the underlying dataset's
record and adds the failure through `TaskInstance.mounts()` (ADR-0007 §2), so
the compile contract is touched by nothing; a builder turns a finished
`rollout_and_unit_test` run directory into a row, refusing anything that is
not a finished actor graded unresolved and anything credential-shaped. The
design record is
[`task-11-oracle-failures-dataset.md`](task-11-oracle-failures-dataset.md).

- **Acceptance:** `load_dataset("oracle_failures")` yields runnable records
  whose `sandbox_spec` / `prompt` / `gold_patch` / `unit_test_spec` are the
  underlying instance's and whose mounts stage the failure; the builder
  refuses a timed-out, crashed, unfinished or resolved run, a run whose
  persisted grading workspaces disagree with the recorded grade or with each
  other, and a credential-shaped conversation; one real record exists.
- **Verification:** unit tests over the record, the loader and the builder;
  the first record built from the qutebrowser/9ed748ef baseline failure
  (PR #265's harvest) with the parquet confirmed untracked.
- **Dependencies:** none. **Scope:** M
- **Outcome:** landed as designed. The first record's re-graded verdict names
  the same two failed tests the experiment's report diagnoses. Follow-ups are
  named in the design record: a blind run of the task — guided or not — must
  run `record.instance`, and the policy stamp on phase-B records is task 07's.
