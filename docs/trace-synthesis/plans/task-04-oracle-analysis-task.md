# Task 04 — The Oracle analysis task + guidebook schema

**Status lives in [`README.md`](README.md).** This file is the design.

## What it is

Phase B of the [spec](../spec.md#phase-b--the-oracle) as a `Task`
(ADR-0007): an agent with privileged access reads one cached failure and
writes `guidebook.md`, a staged tutorial for a future blind actor. Shipped as
`OracleAnalysisTask` in
[`src/swe_lab/trace_synthesis/oracle.py`](../../../src/swe_lab/trace_synthesis/oracle.py)
and registered as the one-entry `oracle_analysis` workflow:

```sh
python -m swe_lab run oracle_analysis <instance_id> --dataset oracle_failures
# a stronger Oracle, per invocation:
python -m swe_lab run oracle_analysis <instance_id> --dataset oracle_failures \
    --oracle_analysis.harness.model=opus
```

The instance is an [`oracle_failures`](task-11-oracle-failures-dataset.md)
record — that is what makes the workflow one entry: the failure arrives as the
record's own mounts, so no earlier task and no `--input` is needed. The task is
written against `TaskInstance`, not against that record type; a future chain
that runs phase A first can hand the same task the same instance.

## The composition

Mirrors `CodingAgentTask` — the harness supplies its mounts, observers, assets
and the main action; the task adds what the run is about — with a different
set of extras:

| Mount | From | What the Oracle sees |
|---|---|---|
| `failed_conversation.json`, `failed_verdict.json`, `failed_patch.diff` | the instance (`record.mounts()`) | the failed attempt |
| `gold_patch.diff` | `instance.gold_patch()`, when the dataset has one | the reference solution |
| `entryscript.sh` + the compiled spec's own files | `instance.unit_test_spec(apply_patch=True, patch_name="failed_patch.diff")` | the **exact grading procedure**, compiled to apply the failed patch — `bash "$SANDBOX_WORKSPACE/entryscript.sh"` reproduces the verdict being explained |
| the harness's own files | `harness.mounts(workdir)` | — |
| `prompt.md` | the task's `inputs_builder` (`oracle_prompt`), in-session | the brief |

Compiling the grading procedure for the Oracle is what makes "the golden
tests" dataset-agnostic: SWE-bench Pro checks its held-out tests out of the
solution commit inside that script, DeepSWE ships them as a patch in the
spec's mounts, and the Oracle gets whichever it is by reading — or running —
what the grader runs.

Observers: the harness's own (trace conversion, outcome, agent info; the proxy
recorder under proxy capture) plus a `GuidebookObserver`, which reads
`guidebook.md` back in `before_destroy`, validates it, registers it as an
inline artifact, and reports `guidebook.present` / `guidebook.valid` /
`guidebook.stages`. `outputs_valid` requires a present **and** valid
guidebook; an invalid one still lands as an artifact (a rejected guidebook is
evidence) and the attempt's record carries `guidebook_problems`.

## Deliberately contaminated — and pinned

The task composes **no git-history purge, no diff extraction and no result
verifier**. The purge would strip the very history the Oracle is given (the
fix commit is reachable, and the brief says so); a guidebook is not a patch;
and a run handed the answer has nothing to be verified against. The spec's
[§14](../spec.md#14-integrity-red-lines) asks that this be explicit rather
than incidental, so it is a named test —
`test_the_oracle_task_composes_no_purge_no_extractor_and_no_verifier` — with
its converse, `test_the_rollout_definitions_still_purge`, guarding the solving
entries from the other side. The integrity consequence (such records never
pool with benchmark numbers) is the policy stamp's job and lands with task 07.

## The brief

`build_oracle_prompt(instance)`, the task's `inputs_builder`. It is written
against what the two hand-written guidebooks (openlibrary, qutebrowser — PR
#263 and PR #265) taught, and two lessons are stated as rules because each was
learned by getting it wrong once:

1. **Quote the task statement whole, never in excerpt.** The brief itself
   carries the actor's full task statement verbatim between delimiters, and
   tells the Oracle that any stage resting on what the statement says quotes
   the entire field. The openlibrary guidebook once asserted that the
   interface block was *silent* about a placement it in fact stated; an
   absence claim cannot be checked against a summary, and a blind actor can
   refute it in one command.
2. **The verification stage says what a green suite cannot tell you.** Both
   failed actors ran their suites green and still failed grading, because the
   graded tests were not in the working tree they worked in. "Run the tests"
   is not a verification stage; the brief asks for the check that *does*
   discriminate.

The rest of the brief: what is in the workspace and where (`$SANDBOX_WORKSPACE`,
the repo at `base_commit`, the reachable fix commit when the dataset records
one); a method (diagnose first — verdict, patch against reference, then the
conversation; reproduce with the grading procedure when in doubt); the exact
output shape; and the spec's rules (never say you saw the answer; every
justification derivable from the statement, the repo at `base_commit` and
earlier stages only; make the failing stage a decision, not a formality;
direction over specifics).

## The guidebook schema

[`guidebook.py`](../../../src/swe_lab/trace_synthesis/guidebook.py). A
guidebook is Markdown, so the schema is the shape of the hand-written ones:
`## Stage N — <title>` sections, each carrying five bold labels — `**Goal.**`,
`**Actions.**`, `**Expected observations.**`, `**Justification.**`,
`**Exit criteria.**` (a colon after the label is accepted too). `**Edits.**` /
`**Tests.**` stay optional, as the spec's table has them.
`validate_guidebook(text)` returns every missing piece by stage number; empty
means valid. Only **presence** is checked — whether a justification is
genuinely derivable is a reader's judgement, per the spec.

## Acceptance, item by item

| Acceptance (from the index) | State |
|---|---|
| schema validation rejects a guidebook with a stage missing its `justification` | ✅ `test_a_stage_missing_its_justification_is_rejected`, and at task level `test_a_guidebook_missing_a_justification_fails_the_attempt` |
| the task declares `guidebook.md` as an output | ✅ required, `test_the_guidebook_is_the_declared_required_output` |
| the purge-off configuration is explicit rather than incidental | ✅ the named test above |
| unit tests for the schema | ✅ |
| one live run producing a guidebook a human judges usable | 🔶 one run made ([outcome](#outcome-of-the-first-live-run-2026-09-01)): the path holds and a guidebook was produced, but it failed the schema check and no human judgement has been made — the row stays open |

## The live run — what one run can and cannot show

Declared **before** the first run, because a first guidebook is the easiest
artifact to mistake for stronger evidence than it is. One `oracle_analysis`
run over the first `oracle_failures` record (qutebrowser/9ed748ef, baseline
rollout 0) on the real image with the real harness shows exactly:

- **The path is real, end to end.** Loader → delegated record → mounts (the
  failure files, the compiled grading procedure with the failed patch, the
  gold patch) → Claude Code in the sandbox → `guidebook.md` → schema
  validation → the run record's `guidebook.*` metrics. Everything before the
  agent call was already exercised over a `FakeSandbox`; the run adds the
  image, the harness and the token.
- **The brief elicits the shape at least once** — a schema-valid guidebook
  with N stages, each carrying the five fields.
- **A human can judge whether the content is usable** — the acceptance row —
  and that is a judgement over one sample, recorded as such.

It does **not** show:

- **That the justifications are derivable.** The Oracle saw the answer; a
  justification that *reads* as derivable from the statement and the repo at
  `base_commit` is not thereby shown to be. The spec makes that a reader's
  judgement, and no blind check exists yet.
- **That the guidebook helps anything.** No consumer exists — neither the
  judge that would score rollouts against it nor a guided re-run — so nothing
  about yield, steering, scoring or trace quality follows from it.
- **Generalisation or variance.** One instance, one dataset (SWE-bench Pro),
  one model, one sample: nothing about other instances or datasets, and
  nothing about how a second run of the same record would differ. Cost and
  wall-clock are one data point; task 08 measures them.
- **That the brief's rules are reliably followed.** "Quote the task statement
  whole" and "a green suite only says you broke nothing else" are
  instructions; one run shows whether the model honoured them once.
- **The record's blind-run property** (a run over `record.instance` sees no
  failure material) — a different run, not taken here.

A timeout or crash under host throttling is an environment failure and is
reported as *no result*, never as a negative finding (the box policy's rule 4).
The run's artifacts are copied to the stable artifacts path outside every
checkout (`docs/conventions.md`, hazards: a removed worktree takes its
`.cache/` with it) after a credential scan, and the guidebook is linked from
the outcome, not pasted into it.

## Outcome of the first live run (2026-09-01)

Run on branch `feat/oracle-dataset` at `284ab2b` — **before** this task was
split out of #266 into #275 and this PR. The brief, the schema check and the
task's mounts are byte-identical here; the only code that came after the run
is the pre-sandbox refusal of an instance that stages no failure, and the test
pinning the brief's two rules. Over the first `oracle_failures` record
(qutebrowser/9ed748ef, baseline rollout 0), on the real image with the real
harness: the agent finished in 310 s (`claude_code.exit_code 0`, no timeout)
and wrote a 273-line, five-stage `guidebook.md`; the workflow exited 1 because
the attempt failed validation — `guidebook.present 1`, `guidebook.valid 0`,
`guidebook.stages 5`, problem `stage 3: missing the 'Expected observations'
field`. The entry has no retries, so that was the run.

Against the declaration above:

- **The path is real, end to end** — shown: loader, delegated record, the
  three failure mounts plus the grading procedure and gold patch, Claude Code
  in the sandbox, the guidebook collected, validated and recorded as metrics.
- **The brief elicits the shape at least once** — **not shown.** Four of five
  stages carry all five fields; stage 3 lacks one. n = 1.
- **A human can judge whether the content is usable** — the material exists
  for that judgement and the judgement has not been made; the acceptance row
  stays open. Structural facts only: stage 4 is an explicit fork decision
  (whether to add structure-matching to `QtColor.to_py`), and stage 5
  verifies by hand-exercising the parser rather than trusting a self-authored
  test — the shape the two hard-won rules ask for.

None of the "does not show" items is claimed.

**A failed attempt's artifacts are still the evidence.** The attempt was
judged failed, and its `guidebook.md`, conversation and event stream are the
only record of what the brief produced. They were credential-scanned (32
files, no hits) and copied to the stable artifacts path outside every
checkout (`swe-lab-artifacts/trace_synthesis/oracle-analysis-qutebrowser-rollout-0-20260901T135011Z/`,
with a `PROVENANCE.txt`). Judging an attempt failed and discarding its output
are two different acts and must never be merged: the run layout keeps every
attempt's `a<N>/` whatever its validity, and nothing in this task or
downstream of it may change that.

**A hypothesis, not a change.** One missing field made a five-minute agent
run count for nothing. Two designs would change that, and neither is taken
here:

- `retries` on the entry — a remedy for *variance*, which n = 1 cannot
  distinguish from a *systematic* gap: a brief that reliably drops the same
  field would fail twice at double the cost.
- recording `guidebook_problems` without failing the attempt — the precedent
  is ADR-0010 §3c's *detection, never a gate*: five fields present is a
  **proxy** for usability, not usability, and a proxy should not have the
  power to destroy a possibly useful product. The refusal would move to the
  consumer, which holds the real criterion.

The second is the direction favoured. It is decided by task 08's batch
measurement, which has to answer two questions before either design is
adopted: is the missing field systematic or random, and is a guidebook with
a missing field actually unusable downstream.

## Out of scope

- **Consuming the guidebook** — neither the judge that scores unguided
  rollouts against it nor the Supervisor and hook wiring of a guided re-run
  (task 05). Which of the two is the pipeline's default is decided by the
  spec's [§15.5](../spec.md#15-success-criteria) cost comparison; nothing here
  touches injection code.
- **The policy stamp** on phase-B records (task 07).
- **Guidebook reuse across attempts** — still an [open question](../spec.md#11-open-questions).
