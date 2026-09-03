# `N`-batching replay — what the supervisor says at each batch size

**Question.** Today's supervisor consults its judge at **every stream event**.
The proposal in [issue #375](https://github.com/Luolc/swe-lab/issues/375) is to
batch: judge once per `N` assistant messages. `N` has no measured value. This
replays the repo's own supervisor over one **recorded** rollout with `N` as the
only variable, and reports what the judge actually said at each.

It produces **no recommended `N`** — see
[`PREREGISTRATION.md`](PREREGISTRATION.md) for why, and for the frozen list of
readings. Findings: [`REPORT.md`](REPORT.md).

## Method

The corpus is `~/corpora/swe-lab/first-e2e-2026-09-02/r0/rollout/a0` (one
instance, one attempt, 170 events), read-only. Nothing re-runs the actor, no
container starts, and **zero SWE-bench Pro instances are rolled out**, so the
repo's scale gate (>10 instances or >2 rollouts/instance) is not touched.

`replay.py` drives the shipped `EvidenceFilter`, `SpeakWhenOffTrack`,
`ModelJudge` and `ModelWriter` — no policy logic is reimplemented. What it
changes is only **when** the policy is consulted.

`replay.py self-check` pins that, with no model call: it runs the **real**
driver (`replay()`) and the shipped `Supervisor` over the same 170 events with
the network replaced by a canned answer, and asserts both the row sequence
*and* the byte-identical text of all 173 model prompts — the prompts being the
observable that carries the accumulation and the window. It also asserts no
committed artifact names a home directory. The same two assertions run in
`tests/test_n_batching_replay_witness.py`, so CI enforces them; the driver
check skips there when the off-repo corpus is absent.

Because the stream is a recording, a correction this replay writes is never
delivered and never changes what the actor does next. That is the boundary of
every claim here.

## How to run

```sh
cd "$(git rev-parse --show-toplevel)/experiments/trace_synthesis/n_batching_replay"

# Deterministic, no model calls, no credentials:
uv run python replay.py shape        # batch shape of every N
uv run python replay.py self-check   # real driver == the shipped Supervisor

# Needs the gitignored SWE-bench Pro parquet (datasets/swebench_pro/README.md):
uv run python replay.py verify-task  # recovered task == instance.prompt()

# The run itself. The key is read into the environment and split inside the
# program; it never reaches a command line.
export OPENROUTER_API_KEYS=$(op read --no-newline --force \
  "op://dev-shared/openrouter-api-keys/credential")
uv run python replay.py run --pass a
uv run python replay.py run --pass b

# REPORT.md's §§2-8, recomputed from runs/. (§1 comes from `shape` above, and
# the corpus/criterion/task digests from runs/*/*/manifest.json -- no single
# command regenerates all three.)
uv run python analyze.py
```

`run` is resumable: an arm whose `manifest.json` exists is skipped, never
silently — it prints what it skipped.

## Layout

```
replay.py                  the runner; also `shape`, `self-check`, `verify-task`
analyze.py                 recomputes REPORT.md's §§2-8 from runs/
runs/<arm>/<pass>/
  judgments.jsonl          one row per boundary — the raw artifact
  manifest.json            arm, params, digests, git sha, wall clock
runs/summary.json          written by analyze.py
```

A `judgments.jsonl` row carries the judge's raw answer, the response model, the
sampling actually sent, the provider's finish reason and usage, and every
deterministic count the analysis needs. It does **not** carry the prompt text
(the criterion and task are pinned by digest in the manifest instead), so the
committed artifacts stay small — no large trace is committed.
