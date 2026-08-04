# ADR-0008: Retrying a flaky eval moves to the task level

## Status

Accepted (supersedes [ADR-0005](ADR-0005-flaky-eval-retry.md))

## Date

2026-08-03

## Context

[ADR-0005](ADR-0005-flaky-eval-retry.md) established *why* a failed evaluation
is re-run: the corpus's own gold patch fails up to 16% of the time on some
instances, so a single-shot number measures the harness as much as the model.
None of that reasoning changes here, and none of it is re-litigated — the
empirical case, the "this is not pass@K" argument, and the cost trade-offs
carry over verbatim.

What changes is **where** the re-running happens. ADR-0005 shipped it as a loop
*inside* the eval composition: the same container, the same session, the same
workspace, with `git reset --hard` + `git clean -fd` between attempts and each
failed attempt's outputs copied aside so the next one could not overwrite them.
That was the only level that existed at the time.

[ADR-0007](ADR-0007-task-and-workflow-layer.md) §6 then named three nested
levels of "run it again" and built the middle one: **task retry** — a fresh
sandbox per attempt, each attempt persisted under its own `a<N>` prefix, with
the task's own `should_retry` hook deciding whether to spend budget. It also
recorded the intent that `UnitTestSpec.retries` moves onto the task.

Two mechanisms now answered the same question, and the older one answered it
worse in every respect that matters.

## Decision

**The in-run retry loop is deleted. Flake absorption is task-level retry, and
the trigger survives as a hook.**

- `_attempt_until_resolved` and the loop inside the eval task's `action` are
  gone; the action runs the entryscript exactly once.
- `UnitTestEvalTask.should_retry` returns true on an **unresolved** verdict as
  well as on an invalid attempt. That is ADR-0005's trigger, expressed where
  retrying now lives. It is not a failure: the terminal marker reads
  `outputs_valid`, so a genuinely failing patch that exhausts the budget is a
  *succeeded* task whose answer is "unresolved".
- The budget is `WorkflowEntry.retries` (or `run_task`'s), where run
  configuration lives. `UnitTestSpec.retries` and `UnitTestEvalTask.retries`
  are deleted. A consumer who has measured an instance still raises its budget
  — at the entry, which is the same visible, per-run decision ADR-0005 wanted
  it to be.
- `EvalParseObserver.attempts` / `retained` and the per-attempt copy-aside are
  deleted. They existed because attempts shared one workspace; task attempts
  share nothing, and the store already keeps each attempt's outputs apart.
- **`Verdict.attempts` and `Verdict.flaky` are deleted.** One verdict grades
  one tree. How many attempts it took is the runner's fact, and the evidence is
  the persisted attempt sequence: `a0` with `unit_test.resolved = 0`, `a1`
  with `unit_test.resolved = 1` *is* the flake record, and it carries what each
  attempt produced. A caller that wants the scalar derives it where attempts
  are counted — the CLI prints `attempts` and `flaky` from the task run's own
  report.

The eval script keeps its `git reset --hard` + `git clean -fd` + delete-stale-
outputs preamble. It is no longer needed *between* attempts (there are none),
but it is still needed to make one attempt independent of whatever the image
shipped with — and ADR-0005's reasoning for each line stands.

## Alternatives considered

| Option | Why not |
|---|---|
| Keep both levels | Two mechanisms for one question. The in-run one would keep needing its own retention, its own attempt counter on the verdict, and its own explanation of why a "verdict" carries run history. |
| Keep in-run retry for cost (warm container) | Real, and given up knowingly — see Consequences. Paying container setup per attempt is the price of isolation and of separately persisted evidence, and it is only paid where something already went wrong. |
| Keep `Verdict.attempts` as a constant `1` | A field that can only hold one value is a lie waiting to be read as data. Sweeps that read verdict-level flakiness must read record-level; that is called out rather than papered over. |
| Move the budget into `known_flaky` | ADR-0005 deliberately kept the metric independent of how complete our notes are, and that argument is unchanged. |

## Consequences

**Good**

- **Stronger isolation.** An attempt inherits nothing — not the container's
  filesystem, not its caches, not a half-written output. A flake caused by
  in-container state that survives `git clean` (a stray daemon, a warm cache, a
  port) is now absorbed rather than repeated.
- **Every attempt is evidence, in one place.** Each attempt writes its own
  record shard and its own artifacts under `…/unit_test/a<N>/`, so the failing
  attempt is readable from the store with no special retention path.
- **One mechanism, one budget, one place to configure it.** Retry now works the
  same way for an agent run, a grading run, and any future task.
- **A partial run resumes correctly.** A preempted process left no marker, so a
  later one re-runs the task from `a0` — which the in-run loop could not
  express at all.

**Bad, and accepted knowingly**

- **Container setup is paid per attempt.** ADR-0005's warm re-run is gone: a
  retried eval now pays image start, mounts, and the script's reset preamble
  again. On a gold sweep ~3% of instances retry, so this is a small absolute
  cost; on an agent sweep where most patches fail it is the same multiplier
  ADR-0005 already accepted, plus setup.
- **Reports that read `verdict.flaky` break.** The signal moved to the records.
  This is the one downstream-visible break, and it is deliberate: the flake
  belongs to the run, not to the answer.

**Neutral**

- ADR-0005's disclosure obligation is unchanged: a published number is still
  "resolved within N eval attempts of one patch", and must say so.
- ADR-0005's "Future direction" (narrow retry to registered instances once the
  registry's coverage argument can be made) survives untouched — it is a
  question about *when* to retry, not *where*.
