# Task 11 — Start from a cached failure: the `oracle_failures` dataset

**Status lives in [`README.md`](README.md).** This file is the design.

## Why the pipeline no longer begins with a rollout

The [spec](../spec.md#3-the-pipeline) was written as a line: phase A runs a
baseline rollout and keeps the failures, phase B writes a guidebook against
one, and whatever consumes the guidebook comes after — a judge scoring
unguided rollouts against it, or a guided re-run; which of those is the
default is decided by the spec's [§15.5](../spec.md#15-success-criteria) cost
comparison, not here. The owner's ruling (2026-09-01) is that phase A is, in
practice, already paid for: by the time
this pipeline is wanted a full eval sweep has run, and its failed rollouts —
conversation, verdict and patch — are sitting in the cache. Re-running a
rollout to *reproduce* a failure we already own is a rollout paid twice.

So the pipeline's input is **a dataset of cached failures**, and it starts at
phase B. Each record names the underlying instance (which dataset, which id)
so the sandbox, prompt, gold patch and grading are reused rather than
re-implemented, and carries the one failed attempt that phase B analyses.

## What a record is

`src/swe_lab/datasets/oracle_failures/record.py`, registered in the loader as
`oracle_failures`. A parquet row with seven columns:

| Column | What it holds |
|---|---|
| `dataset` | the underlying dataset's registry name (`swebench_pro`, `deepswe`) |
| `instance_id` | the underlying instance's id — and this record's |
| `rollout_id` | which sample of the instance failed |
| `conversation` | the failed rollout's typed `Conversation`, as JSON; validated on load |
| `verdict` | the dataset grader's verdict on the failed patch, as JSON: `resolved`, `score`, its `metrics`, its `summary` (which names the failed tests) |
| `patch` | the patch the failed rollout submitted |
| `provenance` | where it came from: the run's sweep, timestamp, entry keys, both entries' metrics, when it was built |

The data file lives under `datasets/oracle_failures/data/`, gitignored like
every dataset's data (`datasets/**/data/`); the tracked half is
[`datasets/oracle_failures/README.md`](../../../datasets/oracle_failures/README.md).

## Delegation, not a copy

The record holds the underlying dataset's own record (`instance`, resolved on
load through `load_dataset`) and **forwards** `sandbox_spec`, `prompt`,
`gold_patch`, `unit_test_spec`, `required_tests` and `solution_sha` to it
unchanged. The only method it implements itself is `mounts()`, which merges
the underlying instance's mounts with the three failure files.

Why this shape and not a flattened copy of the underlying row plus the
failure:

- **The compile contract stays untouched.** `SandboxSpec` / `UnitTestSpec` —
  what a dataset compiles its record into — are ask-first territory in
  `AGENTS.md`. A delegating record cannot drift from the underlying dataset's
  compilation because it does not have one; a copied record would re-implement
  SWE-bench Pro's (and DeepSWE's) quirks a second time and drift the first
  time either changed.
- **`mounts()` is the seam that already exists for this.** ADR-0007 §2 made
  the instance the third mount source, "deciding for itself whether it stages
  anything". A failure is the dataset's material for a phase-B run, exactly as
  the run script and parser are the dataset's material for a grading run. No
  new mechanism.
- **The loader protocol allowed it.** `DatasetRecord.from_raw(raw)` takes no
  context, so the record resolves its underlying instance itself:
  `underlying_instance(dataset, instance_id)` loads the named dataset once per
  process (`functools.cache`) and `require`s the id. The import of
  `load_dataset` is function-local because the loader's registry imports this
  record type; a top-level import would close the cycle. A row naming an
  instance that does not load fails **at load**, the way a malformed list
  column does in any other dataset.

Two consequences worth stating:

- **The record's identity is the underlying instance's id.** A run of the
  record therefore lands in the store beside the instance's other runs
  (`<sweep>/<instance_id>/r<k>/oracle_analysis/…` next to `…/rollout/…`), and
  a dataset file holds **one failure per instance** — rebuilding an instance
  replaces its row, and the builder refuses a same-id row from a different
  source rather than let it overwrite. A second failure of the same instance
  is a second dataset file, not a second id; nothing needs that yet.
- **Every task run against the record sees the failure.** That is what a
  mount source means. A run that must *not* see it — any blind run of the
  task, guided or not — runs the underlying instance, which the delegation
  keeps in hand as `record.instance`. The tasks that consume a guidebook
  decide how that is wired; nothing here forces it either way.

### The names are a contract, and they live on neutral ground

The Oracle task has to name the files the record stages, and "nothing
downstream of a dataset should import a concrete one"
(`datasets/instance.py`). So the three workspace names —
`failed_conversation.json`, `failed_verdict.json`, `failed_patch.diff` — live
in [`swe_lab/trace_synthesis/sample.py`](../../../src/swe_lab/trace_synthesis/sample.py),
a leaf module the dataset record imports and the task imports; neither
imports the other. The same arrangement `PATCH_NAME` has between the
extraction side and the datasets.

## The builder

`python -m swe_lab.datasets.oracle_failures.build --run-dir <dir>` turns one
finished `rollout_and_unit_test` run into a row. The **input contract is the
run's own output directory** — `.cache/runs/rollout_and_unit_test/<instance_id>/`
as the CLI leaves it, or a frozen copy of it — and nothing else: no
experiment-specific summary file, no re-run.

What it does, in order:

1. **Finds the run's workflow record** (`store/*/*/*/workflow.json`; exactly
   one) and picks the two entries **by what they produced**, not by key: the
   rollout is the entry whose artifacts include `conversation.json` and
   `patch.diff`, the grading entry the one reporting a `*.resolved` metric.
   The builder therefore imports nothing from `workflow.definitions`.
2. **Applies the gates.** An unresolved verdict is not evidence the actor
   erred: it reads the same when the actor was killed at its budget, crashed,
   or never started — measured on an image that could not execute the agent
   binary (PR #265's report, §9). So: the workflow `succeeded`;
   `agent_complete == 1`; every `*.timed_out == 0` and every
   `*.exit_code == 0` on the rollout entry; every `*.resolved == 0` on the
   grading entry. Any failure **refuses**, names the gate, and writes nothing.
3. **Reads the artifacts** the record points at (`store/<artifact key>`),
   validates the conversation as a typed `Conversation`, refuses an empty
   patch.
4. **Scans for credentials — on the raw artifacts, before anything parses
   them.** The conversation and the patch are matched against
   credential-shaped patterns (Anthropic / OpenRouter / OpenAI keys, GitHub
   and Hugging Face tokens, bearer strings); a hit refuses the row naming
   **only the pattern**. The order matters: a parser's error message quotes
   the input it rejected, so validating first would print a token on the way
   to refusing it. For the same reason the typed-`Conversation` check that
   follows reports only *where* and *what kind* of failure (`messages/0/content
   (list_type)`), never the value — as does the record's own load-time check.
   The value never reaches a message, a log or the parquet. The scan is a
   guard on a deliverable, not a redaction pass — see
   [task 09](README.md#task-09-redact-the-production-proxy-capture) for the
   proxy log, which is not part of a row at all.
5. **Re-grades the final grading attempt's persisted workspace**
   (`<run>/<grading key>/ws/a<attempts-1>/`) with the dataset's own grader,
   obtained through `instance.unit_test_spec(...).grader`. The verdict column
   is that grader's `resolved` / `score` / `metrics()` / `summary()`. This is
   what makes the per-test detail dataset-agnostic — SWE-bench Pro's
   `output.json` and DeepSWE's `ctrf.json` are read by the code that already
   knows how — and it doubles as a consistency check: a workspace that
   re-grades as resolved is not the graded one, and the row is refused.
6. **Writes the row**, replacing any earlier row for the same instance —
   from the **same** source dataset. The file is indexed by instance id
   alone, so a same-id row from another source is a collision, refused with
   the file left as it was; a second source that shares ids gets its own file
   (`--out`).

Rejected along the way: parsing `unit_test.output.json` directly (SWE-bench
Pro's shape only); reusing the experiment's sample-directory layout as the
dataset format (the loader reads parquet, and a directory-per-sample format
would have needed its own loader); a separate "assemble the parquet" step
(one command per failure is the whole workflow).

## The first record

Built 2026-09-01 from the frozen `baseline-qutebrowser-rollout-0` run harvested
by the steered re-run experiment (PR #265) —
`instance_qutebrowser__qutebrowser-9ed748effa8f3bcd804612d9291da017b514e12f-v363c8a7e5ccdf6968fc7ab84a2053ac78036691d`,
rollout 0, the **baseline** arm (no hints). The gates all pass
(`agent_complete 1.0`, `claude_code.exit_code 0`, `claude_code.timed_out 0`,
`unit_test.resolved 0.0`), the credential scan is clean, and the re-graded
verdict names the same two failed tests the experiment's report diagnoses —
`TestQtColor::test_invalid[rgb((1, 2, 3)-must be a valid color value]` and
`…[rgb(1, 2, 3))-must be a valid color value]` — out of 985 required.
`git check-ignore` confirms the parquet is untracked. The row's `provenance`
column records the source run's workflow-record key, sweep and timestamp —
never the run directory's host path, which names the operator on an ordinary
workstation and a trace record must not carry.

## Out of scope

- **Consuming the guidebook.** Nothing here delivers a hint or scores a
  rollout; the record only makes phase B startable. Whether the guidebook then
  judges unguided rollouts or steers a guided one is decided downstream
  ([spec §15.5](../spec.md#15-success-criteria)), and a blind run of either
  kind runs `record.instance`.
- **The policy stamp.** Records of runs over this dataset are contaminated by
  construction (the Oracle sees the answer) and must never pool with benchmark
  numbers; the stamp (ADR-0010 §5) lands with the workflow in task 07.
- **Publishing.** The conversation column is scanned, not redacted, and the
  dataset is local. Publishing a row is gated on task 09's redaction story
  like every other trace.
