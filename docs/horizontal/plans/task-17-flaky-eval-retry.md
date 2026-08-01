# Task 17 — Retry a failed eval, and mark the verdict flaky

> **Design record** (point-in-time; may predate the landed code). **Status is
> tracked only in [`plans/README.md`](README.md)**; where this doc and the code
> disagree, the **code wins**.
>
> Implements [ADR-0005](../../decisions/ADR-0005-flaky-eval-retry.md), accepted
> 2026-08-01 with `retries` defaulting to 1.

---

## 1. Purpose & scope

A 64-rollout gold sweep of the full 731 found 22 flaky instances and a headline
number that moves 727/731 → 724/731 between runs. `known_flaky.py` records the
mechanisms but only annotates, and several of them **cannot** be fixed (the racy
test is in `fail_to_pass`, or the gold patch itself is nondeterministic). This
task acts at the metric level instead: re-run a failed eval, accept it if any
attempt resolves, and record that it needed a retry.

### In scope

- `run_unit_test` gains `retries: int` and an attempt loop.
- The `Verdict` protocol gains `attempts` / `flaky` / `with_attempts`.
- `SweBenchProVerdict` implements them; `summary()` / `metrics()` expose them.
- Failing attempts' outputs are retained as per-attempt artifacts.
- `--retries` on the `eval` and `rollout --grade` CLIs; `flaky` surfaced in the
  printed summary and the persisted record.

### Out of scope

- Changing `known_flaky.py`. It keeps annotating and never gates a retry
  (ADR-0005, Consequences/Neutral) — coupling them would make the metric depend
  on how complete our notes are.
- Retrying **rollouts** (the agent step). Only grading is retried; re-running an
  agent would be a different decision with different semantics.
- Sweep-level aggregation of `flaky` into a report. Follow-on.

---

## 2. Design

### 2.1 Where the loop goes, and why not in the observer

Grading happens in `EvalParseObserver.before_destroy`, which runs at teardown —
too late to decide whether to re-run. So the **body** owns the loop, grades each
attempt to decide, and the observer keeps its existing job of producing the one
authoritative verdict at teardown.

```
with manager.session() as sb:            # body owns the loop
  for attempt in 1 .. retries+1:
    run entryscript                      # per-attempt timeout
    if attempt > retries: stop           # budget spent
    if grader.grade(sb).resolved: stop
    retain this attempt's outputs        # else the retry overwrites them
before_destroy:  verdict = grade(sb).with_attempts(n)   # authoritative
```

The body's `grade()` is a **decision only**; the observer re-grades the final
workspace. That is a second read of a small JSON, and it keeps a single
authoritative verdict rather than two sources that could disagree.

The composition already hands the observer `exec_result` and `wall_seconds`
before teardown; `attempts` follows the same established pattern.

### 2.2 Why an attempt is a clean repeat

The entryscript opens with `git reset --hard <base>` + `git checkout <base>`,
then re-applies the patch and re-checks out the golden tests. Re-running it
therefore inherits nothing from the previous attempt except the container's warm
caches (`node_modules`, pip, Go build cache) — which is exactly what we want:
same tree, no reinstall cost.

### 2.3 `attempts` on the verdict, `flaky` derived

`Verdict` is a Protocol (a data shape, ADR-0002), and the concrete verdict is
dataset-owned, so the generic method cannot construct one. It gets a
`with_attempts(n) -> Self` the dataset implements in one line
(`dataclasses.replace`).

`flaky` is **derived, never stored**: `attempts > 1 and resolved`. A run that
failed every attempt is not flaky, it is failed — and `attempts` still records
that it was given the chances. Deriving it makes an inconsistent state
unrepresentable.

### 2.4 Retaining the failing attempt

A naive retry overwrites `output.json` / `stdout.log` / `stderr.log`, destroying
the only evidence of the flake — the exact thing this whole effort is trying to
collect. After a failed attempt each declared output is copied aside in-sandbox
to `attempt<N>.<filename>` and registered for collection.

Copied via `sb.read`/`sb.write` rather than a shell command, so it works on
every backend and in a Docker-free test.

### 2.5 Timeout

Per attempt, not shared: a shared deadline would make the last attempt's budget
depend on how slow the earlier ones were. Worst case is `(retries+1) × timeout`,
documented on the parameter. `wall_seconds` becomes the **total** across
attempts, since what a sweep wants from it is what the run cost.

---

## 3. Risks / open questions for review

1. **Cost.** Every genuinely failing instance now costs `retries+1` evals. On a
   gold sweep that is ~3% of instances; on an agent sweep where most patches
   fail it can nearly double eval wall-clock. **Settled at review: default 1.**
   The agreed evolution is to narrow retry to `known_flaky`-registered instances
   once the registry is believed to cover most flakes — recorded as a future
   direction in the ADR, needing its own ADR when the coverage argument can be
   made.
2. **A racy *patch* gets credited.** An agent solution that passes only
   sometimes will eventually be accepted. `flaky=True` is what keeps this
   visible; without it it would be indistinguishable from a harness flake.
3. **Metric semantics change.** The number becomes "resolved within N eval
   attempts of one fixed patch". It must be disclosed wherever published.
4. **Interaction with `RunStatus.TIMEOUT`.** Status is derived from the *last*
   attempt's `ExecResult`, so an early timeout followed by a pass reports
   SUCCESS. That is intended; flagging it because it is easy to read as a bug.

---

## 4. Work breakdown

| # | Change | Files | Size |
|---|---|---|---|
| 1 | `attempts`/`flaky`/`with_attempts` on the protocol | `evaluation/verdict.py` | XS |
| 2 | Implement on the SBP verdict + surface in `summary`/`metrics` | `datasets/swebench_pro/unit_test.py` | XS |
| 3 | Attempt loop + retention helper; `retries` param | `evaluation/methods/unit_test/run.py` | S |
| 4 | `--retries` flag; `flaky` in the summary + persisted `extra` | `cli/eval.py`, `cli/rollout.py`, `datasets/swebench_pro/verify.py` | S |
| 5 | Tests (see §5) | `tests/test_unit_test_method.py`, `tests/test_cli_eval.py` | S |
| 6 | Docs: `plans/README.md` row, conventions note | `docs/` | XS |

Steps 1–3 were written ahead of approval (out of process; parked on a branch
with no PR until the ADR was accepted). Steps 4–6 followed after approval.

One thing step 4 surfaced that the design missed: **`verify.py`'s base
self-check must run with `retries=0`.** There a failure is the *expected*
result, so retry-on-failure would double the cost of golden verification to
re-confirm the intended outcome. Only the golden run retries. This is now
recorded in ADR-0005's Consequences.

---

## 5. Verification

Unit tests, all Docker-free on `FakeSandbox` — its `run_results` list already
lets a test script a sequence of attempt outcomes:

- `retries=0` runs the entryscript exactly once (regression guard on the
  default path).
- A first-attempt pass never re-runs, and reports `attempts=1`, `flaky=False`.
- A first-attempt failure followed by a pass reports `resolved=True`,
  `attempts=2`, **`flaky=True`** — the headline behaviour.
- Exhausting the budget reports `resolved=False`, `attempts=retries+1`,
  `flaky=False` (failed, not flaky).
- A retained failing attempt lands as `attempt1.output.json` in the output dir,
  and the final `output.json` is the passing one.
- `retries=-1` raises `ValueError`.
- `wall_seconds` accumulates across attempts.

**Live check** (mine, before handing over): one local Docker `eval --gold` on a
known-good instance with `--retries 1`, asserting it still resolves and runs the
entryscript once — a happy path only. **I cannot reproduce a flake locally**;
the retry's real behaviour is only observable on the downstream batch, which is
the hand-off.

---

## 6. Definition of done

- ADR-0005 accepted.
- Quality bar green (`pytest` + `pre-commit`).
- The tests in §5 exist and fail if the loop is removed.
- One local `--gold` eval passes with `--retries 1`.
- `plans/README.md` carries the row; the ADR is linked from it.
