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

The instance is an [`oracle_failures`](task-10-oracle-failures-dataset.md)
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
| one live run producing a guidebook a human judges usable | ⬜ pending — needs Docker and an agent run; the code path is exercised docker-free end to end over a `FakeSandbox` |

## Out of scope

- **Consuming the guidebook** — neither the judge that scores unguided
  rollouts against it nor the Supervisor and hook wiring of a guided re-run
  (task 05). Which of the two is the pipeline's default is decided by the
  spec's [§15.5](../spec.md#15-success-criteria) cost comparison; nothing here
  touches injection code.
- **The policy stamp** on phase-B records (task 07).
- **Guidebook reuse across attempts** — still an [open question](../spec.md#11-open-questions).
