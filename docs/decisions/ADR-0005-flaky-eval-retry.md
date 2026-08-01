# ADR-0005: Retry a failed eval, and mark the verdict flaky

## Status

Accepted

## Date

2026-08-01

## Context

A 64-rollout sweep of all 731 SWE-Bench Pro instances, run with the **gold
patch** and no agent, came back:

- **705** always pass, **22** flake, **0** always fail;
- whole-corpus batches at 4 rollouts landed at **727/731** and **724/731** —
  i.e. the headline number moves run to run by more than the gap between two
  models worth distinguishing.

Every one of those 22 is a defect in the corpus or the harness, never the model:
the patch under test is the dataset's own reference answer. The mechanisms are
now diagnosed in
[`known_flaky.py`](../../src/swe_lab/datasets/swebench_pro/known_flaky.py) and
[issue #123](https://github.com/Luolc/swe-lab/issues/123) — fixed wall-clock
budgets (1 s `findBy*`, 5 s and 20 s jest defaults), Go map-iteration order,
unawaited deletes, a pytest-xdist tmpdir collision, a live call to a third-party
registry, and a parser that grades a whole repo's suite as one boolean.

Fixing them one at a time is not a path. Several **cannot** be fixed at the
environment level at all: where the racy test is in `fail_to_pass` it *is* the
task, so patching it edits the benchmark and patching the source does the
agent's job. In two cases (NodeBB `22368b99`, vuls `83bcca6e`) the **gold patch
itself** is nondeterministic against the test that grades it.

So the registry, which only annotates, leaves the metric as noisy as it found
it. We need something that acts at the metric level.

## Decision

**Re-run a failed evaluation, and treat the instance as resolved if any attempt
resolves. Record how many attempts it took, and derive a `flaky` flag from
that.**

- `run_unit_test` gains `retries: int = 1` — the number of *extra* attempts
  after the first, so the default is up to two runs.
- An attempt is a fresh execution of the same entryscript. That is a clean
  repeat by construction: the script begins with `git reset --hard <base>` and
  `git checkout <base>`, then re-applies the patch and re-checks out the golden
  tests, so nothing carries over from a previous attempt except the container's
  warm caches.
- After each attempt the method grades the workspace. If the verdict resolves,
  it stops; otherwise it retries until the budget is spent.
- The verdict carries `attempts`, and `flaky` is **derived**, never stored:
  `attempts > 1 and resolved`. A run that failed every attempt is not flaky, it
  is failed — and its `attempts` still records that it was given the chances.
- Each failing attempt's `output.json` / `stdout.log` / `stderr.log` are kept as
  separate per-attempt artifacts. The failing attempt is the one worth reading,
  and it is exactly the one a naive retry would overwrite.

### Why this is not "giving the model extra tries"

**The patch does not change between attempts.** Retrying re-runs a *fixed*
candidate against a nondeterministic harness, which is categorically different
from pass@K, where K *different* solutions are sampled. Nothing about the
model's output improves with a second attempt; only the harness's noise is
averaged out.

The justification is empirical, not theoretical: the reference answer — the
most-correct patch that exists for these instances — fails the benchmark up to
16% of the time on some of them. Any metric that cannot survive its own gold
patch is measuring the harness.

## Alternatives considered

| Option | Why not |
|---|---|
| Fix each flake | Several are unfixable in principle (graded racy tests, nondeterministic gold patches). The fixable ones are worth doing anyway — this is not a substitute, see Consequences. |
| Retry only instances in `known_flaky` | Never discovers a new one, and the registry is incomplete by construction. Retry-on-failure is self-discovering. |
| Run every instance N times, take the modal result | Costs N× on *every* instance including the 705 that always pass. Retry-on-failure costs extra only where something already went wrong. |
| Report a distribution rather than a number | Honest, and worth doing alongside — but it does not let two runs be compared, which is what the number is for. |
| Ignore it | The noise (~3% of the corpus, moving run to run) is larger than the differences the eval is meant to resolve. |

## Consequences

**Good**

- Noise falls geometrically with attempts. A 1-in-9 flake (element-web
  `56c7fc19`) becomes 1-in-81 at `retries=1` and 1-in-729 at `retries=2`.
- `flaky=True` is a **discovery channel**: it names instances that failed then
  passed, which is precisely the input the `known_flaky` registry wants. The two
  mechanisms compose — retry fixes the number, the registry accumulates the
  knowledge.
- Cheap where it matters: on a gold sweep only ~3% of instances retry at all.

**Bad, and accepted knowingly**

- **Cost scales with the failure rate.** Every genuinely failing instance now
  costs `retries + 1` evaluations. On a gold sweep that is negligible; on an
  agent sweep where most patches fail, `retries=1` can nearly double eval
  wall-clock. This is why the default is 1 and not 2, and why it is a parameter
  at all.
- **A genuinely racy *patch* gets credited.** If an agent writes a solution that
  passes only sometimes, retry will eventually accept it. The `flaky` flag is
  what keeps this visible rather than silent; without it the behaviour would be
  indistinguishable from a harness flake.
- **Wall-clock worst case is `(retries + 1) × timeout`.** The timeout stays
  per-attempt, because a shared deadline would make the last attempt's budget
  depend on how slow the earlier ones were.
- The reported number becomes "resolved within N eval attempts of one patch".
  That must be disclosed wherever the number is published; it is not the same
  quantity as a single-shot rate, even though it estimates the same thing with
  less variance.

- **Retry is only correct where a pass is the expected outcome.** The dataset's
  own base self-check (`verify.py`, no patch applied) *expects* the required
  tests to fail, so retrying it would double the cost of golden verification to
  re-confirm the intended result. Callers grading an expected-failure run pass
  `retries=0`; the default suits the ordinary "this patch should work" case.

**Neutral**

- `known_flaky` keeps annotating and never gates a retry. The two are
  deliberately independent *today*: one is the metric, the other is the
  knowledge base, and coupling them would make the metric depend on how complete
  our notes are.

## Future direction (recorded, not decided here)

Once the registry is believed to cover most of the corpus's flakes, retry can be
narrowed to **registered instances only** — turning a blanket noise filter into a
targeted one, which removes both the cost on genuine failures and the credit
given to a racy patch.

That inverts the independence recorded under Neutral above, and it is only sound
once the coverage argument can actually be made (today the registry is
incomplete by construction — it holds what we have looked at). It therefore
needs its own ADR when the time comes, not an amendment to this one.
