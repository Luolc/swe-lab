# Task 04 — `unit_test` eval method + SWE-Bench-Pro compile

> **Status: PLANNED — pre-implementation.** Source of truth: the approved
> [spec](../spec.md) (§Decoupling that follows — the `Grader[V: Verdict]`
> contract; §The three axes) and tasks [02](task-02-engine-core.md)/[03](task-03-a-host-backend.md).
> Grounded in the current grader with `file:line` citations
> (`src/swe_lab/core/datasets/swebench_pro/` at `9667fff`). Open items in §7.

---

## 1. Purpose & scope

Build the evaluation **axis** (the general `Verdict`/`Grader`/`UnitTestSpec`
contract + the `unit_test` method as an engine composition) and the SWE-Bench-
Pro **compile step** that turns a dataset record into `SandboxSpec` +
`UnitTestSpec[SweBenchProVerdict]`. This is the slice that carries the two
highest-value correctness payloads from the 2026-07-18 audit: the
`output_state` verdict (P0-2 — corrupt `output.json` must be an *error*, not a
false GOLDEN_FAIL) and real tests for the script builder (P0-3 — `grading.py`
is untested today).

### In scope

- `evaluation/` axis modules: `Verdict` protocol, `Grader[V]`, `UnitTestSpec[V]`.
- `evaluation/methods/unit_test/`: the main-body runner + the eval-parse
  observer (calls `spec.grader.grade(sb.workspace)` in `before_destroy`).
- SBP side: `SweBenchProVerdict` (+ `OutputState`), `SweBenchProGrader`, and
  the compile function porting `build_eval_script`
  (`grading.py:61-126`).
- Pure unit tests for all of it (FakeBackend; zero Docker).

### Out of scope

- The eval **CLI** + parity run (task 05) and `verify.py`'s sweep port (10b).
- Deleting the old `evaluation/`/`grading.py` path — untouched until 10b; old
  and new coexist (§5.1).
- A second eval method (model-judge) — the seams only (spec Out of scope).
- Changing `classify()`'s GOLDEN_FAIL/ERROR mapping (`evaluation/verify.py`)
  — that consumer moves at 10b; the verdict *carries* the state now so the
  mapping becomes trivial then.

## 2. Module layout

New modules **coexist** inside the existing `evaluation/` package (it holds
only `__init__.py`/`__main__.py`/`verify.py` today — no filename collisions;
§5.1):

```
evaluation/
  verdict.py          Verdict protocol, Grader[V], UnitTestSpec[V]
  methods/
    __init__.py
    unit_test/
      __init__.py
      run.py          run_unit_test(manager-composition body) + EvalParseObserver

datasets/… (current core/datasets/swebench_pro/, moves at 10a):
  swebench_pro/
    unit_test.py      OutputState, SweBenchProVerdict, SweBenchProGrader
                      (stateless), REQUIRED_TESTS_NAME,
                      compile_unit_test(instance) -> (SandboxSpec, UnitTestSpec)
                      build_eval_script port (private)
```

Tests: `tests/test_unit_test_verdict.py` (grader + verdict),
`tests/test_unit_test_compile.py` (script builder matrix + adapter),
`tests/test_unit_test_method.py` (composition on FakeBackend).

## 3. Key types & signatures

Straight from the settled spec interview (Grader design, LGTM'd 2026-07-18):

```python
# ─── evaluation/verdict.py ──────────────────────────────────────────────────
class Verdict(Protocol):
  """Minimal cross-dataset surface: a scalar score in [0, 1].

  ``score`` is 1.0 for a full pass, 0.0 for none; a future rubric- or
  model-judged eval method may report an intermediate score, so sweeps and
  aggregation depend only on this scalar (they average it). ``resolved`` is the
  derived ``score >= 1.0`` convenience for binary pass/fail callers.
  """

  @property
  def score(self) -> float: ...

  @property
  def resolved(self) -> bool: ...

class Grader[V: Verdict](Protocol):
  """Dataset-owned judgment: workspace files → a verdict. Pure; no container."""

  def grade(self, workspace: Path) -> V: ...

@dataclass(frozen=True)
class UnitTestSpec[V: Verdict]:
  """What the unit_test method needs; datasets compile their records into it."""

  eval_script: str          # the main body's in-container script
  mounts: Mounts            # e.g. run_script.sh + parser.py (+ patch.diff)
  grader: Grader[V]

# ─── evaluation/methods/unit_test/run.py ────────────────────────────────────
@dataclass
class EvalParseObserver[V: Verdict](SandboxObserver):
  """Stateful (task-02 §5.4): grades in before_destroy, keeps the verdict."""

  grader: Grader[V]
  verdict: V | None = None      # None until before_destroy has run

  def before_destroy(self, sb: Sandbox) -> Contribution | None:
    self.verdict = self.grader.grade(sb.workspace)
    return None

def run_unit_test[V: Verdict](
    sandbox_spec: SandboxSpec,
    unit_spec: UnitTestSpec[V],
    *,
    backend: SandboxBackend,
    workspace: Path,
    timeout: float = 1800.0,
    observers: Sequence[SandboxObserver] = (),   # composition extras (persist…)
) -> tuple[RunResult, V | None]: ...
    # constructs a FRESH EvalParseObserver per call (single-run object),
    # composes manager(mounts=unit_spec.mounts, observers=[*observers, obs]);
    # the eval script is staged as entryscript.sh (a mount) and the body runs
    # it by path: `with manager.sandbox() as sb: sb.run("entryscript.sh")`;
    # returns (manager.result, obs.verdict). verdict is None only when the
    # run died before before_destroy could fire (early SETUP_ERROR rows of
    # the task-02 matrix) — callers gate on RunResult.status.

# ─── datasets/swebench_pro/unit_test.py ─────────────────────────────────────
class OutputState(StrEnum):
  OK = "ok"
  ABSENT = "absent"            # parser never produced output.json
  UNPARSEABLE = "unparseable"  # present but corrupt/unreadable

@dataclass(frozen=True)
class SweBenchProVerdict:
  passed: frozenset[str]
  missing: frozenset[str]      # required - passed
  output_state: OutputState

  @property
  def score(self) -> float:    # binary for unit tests: 1.0 iff a full pass
    ok = self.output_state is OutputState.OK and not self.missing
    return 1.0 if ok else 0.0

  @property
  def resolved(self) -> bool:  # derived convenience (Verdict surface)
    return self.score >= 1.0

REQUIRED_TESTS_NAME = "required_tests.json"   # the compiled expectation

@dataclass(frozen=True)
class SweBenchProGrader:
  """Stateless: grades from the workspace files alone (no per-instance state).

  Reads the parser's ``output.json`` (results) and the compiled
  ``required_tests.json`` (expectation, staged by ``compile_unit_test``), so
  the class name means what it says — one general grader, not a per-example
  closure — and any persisted workspace re-grades without the dataset record.
  """

  def grade(self, workspace: Path) -> SweBenchProVerdict:
    required = frozenset(
        json.loads((workspace / REQUIRED_TESTS_NAME).read_text())
    )
    passed, output_state = _parse_output(workspace / OUTPUT_JSON_NAME)
    return SweBenchProVerdict(passed, required - passed, output_state)

def compile_unit_test(
    instance: SweBenchProInstance,
    *,
    patch: str | None,                 # None → self-check (no git apply line)
    checkout_golden_tests: bool = True,
    repo_root: Path | None = None,
) -> tuple[SandboxSpec, UnitTestSpec[SweBenchProVerdict]]: ...
    # mounts = {entryscript.sh (the eval script, run by path), run_script.sh,
    #           parser.py, required_tests.json (=fail∪pass), patch.diff iff
    #           patch}; grader = SweBenchProGrader()  # zero fields
```

## 4. The compile step & grade step — element by element

`compile_unit_test` absorbs today's three-way split (`SweBenchProAdapter.eval_spec`
`execution.py:88-108` → `EvalSpec` → `evaluate` staging `grading.py:161-195`):

| Today | Becomes |
|---|---|
| `EvalSpec` 4 run-context fields (`benchmark.py:25-28`) | `SandboxSpec` |
| `EvalSpec` 6 grading fields (`benchmark.py:29-34`) | consumed inside `compile_unit_test`; **no longer exported** |
| harness fetch (`execution.py:53-73`, pinned commit) | unchanged, feeds mounts |
| `evaluate` writing run_script/parser/patch (`grading.py:170-179`) | `UnitTestSpec.mounts` (+ the compiled `required_tests.json`) |
| `build_eval_script` (`grading.py:61-126`) | private `_build_eval_script`, same logic, `$SANDBOX_WORKSPACE` instead of `MOUNT_AT` |
| `evaluate`'s post-run parse (`grading.py:197-210`) + `_passed_tests` (`grading.py:213-225`) | `SweBenchProGrader.grade` |
| `EvalSpec.required_tests`/`is_resolved` (`benchmark.py:36-43`) | compiled into `required_tests.json` (a mount); `grade` reads it back (see §5.4) |

Ported script semantics that MUST survive verbatim (each is a test):

- Golden-test restore = **last line** of `before_repo_set_cmd`
  (`grading.py:91-101` — SBP's 4-line block contract; Scale does the same).
- `shlex.quote` on the joined selected tests (`grading.py:102-108` — `$` and
  `[...]` in test names).
- No ENV-scraping (`grading.py:85-89` — Docker `ENV` is baked into the image).
- `apply_patch=False` omits the `git apply` line; `checkout_golden_tests=False`
  omits the restore line (`grading.py:114-117` — the dataset self-check modes).
- Output redirection to workspace logs and the parser invocation line
  (`grading.py:118-125`).

`SweBenchProGrader.grade` fixes the P0-2 silent fold (`grading.py:213-219`
collapses present-but-corrupt into `frozenset()` — indistinguishable from "no
tests passed", which `verify.classify` then labels GOLDEN_FAIL):

```
output.json missing            → ABSENT,      score=0.0, passed=∅
output.json unreadable/corrupt → UNPARSEABLE, score=0.0, passed=∅
parsed                         → OK, passed=PASSED set, score=1.0 iff required⊆passed
```
(`resolved` is the derived `score >= 1.0`.)

`missing` stays `required - passed` in every state (as `grading.py:201`), so
report tables keep reconciling.

## 5. Design decisions

### 5.1 Strangler coexistence in the same package
The new axis modules land *next to* `__main__.py`/`verify.py` — no renames, no
collisions, old CLI untouched until 10b. Why not a temp package name: churn
for nothing; the module set is disjoint.

### 5.2 The patch is a compile input, not a dataset field
`patch` arrives per-run (gold patch, candidate file, rollout output) — it was
never dataset knowledge; today `evaluate` takes it as an argument too
(`grading.py:132`). Compiling it straight into `mounts["patch.diff"]` +
conditionally into the script keeps `apply_patch ⇔ patch is not None`
(`grading.py:161`) in one place. The stale-`output.json` hazard
(`grading.py:167-169`) is covered by task 02's workspace-hygiene decision
(02 §8 Q3), not re-implemented here.

### 5.3 Scripts reference `$SANDBOX_WORKSPACE`, not `MOUNT_AT`
Task 02 §5.5 / task 03 §5.5. The only textual change to the ported script:
`{MOUNT_AT}/{name}` → `"$SANDBOX_WORKSPACE"/{name}` (quoted). Everything else
byte-comparable to the old builder in tests (a fixture pins the old output
with `MOUNT_AT=/workspace` substituted, so the port is diffable — parity at
the text level *before* CP1 proves it at the container level).

### 5.4 The grader is stateless; `required_tests` travels through the workspace
*(Settled with the user 2026-07-21.)* An earlier draft gave `SweBenchProGrader`
a `required_tests: frozenset[str]` field — but that made a general-*sounding*
class secretly per-instance: a reader sees `SweBenchProGrader` and reasonably
expects one dataset-wide grader, not a per-example closure. Instead,
`compile_unit_test` writes the expectation (`fail_to_pass ∪ pass_to_pass`) to
**`required_tests.json`** as a mount, and `grade(workspace)` reads it back
alongside `output.json`. The grader carries **zero fields** — the name now
means what it says, and `EvalSpec.required_tests`/`is_resolved`
(`benchmark.py:36-43`) dissolve into a compiled file + the grader's set math.

The deeper win: the workspace becomes a **self-describing grading record** —
result (`output.json`) + expectation (`required_tests.json`) + how
(`run_script`/`parser`) all in one dir. A workspace persisted to T1 (task 12)
re-grades from files alone, without the original dataset record — directly
useful for the audit/reproducibility this project exists for. Cost: one
write-then-read of a tiny JSON, and the file is inert in-container (it rides
the mounts mechanism but nothing in `/workspace` reads it; the git diff is
`/app`). Alternatives rejected: adding `required_tests` to the `grade`
signature (leaks a unit-test concept into the general `Grader` protocol —
model-judge has no such thing); merely renaming to `…InstanceGrader` (labels
the smell, keeps the per-instance object, loses the self-describing workspace).
`BenchmarkAdapter` (`benchmark.py:49-54`) is not extended — retired at 10b with
`EvalSpec`; `compile_unit_test` is the new adapter surface.

### 5.5 The verdict lives on the stateful `EvalParseObserver`
Task 02 §5.4 (settled with the user 2026-07-18): the observer keeps
`verdict: V | None` as its own field; `run_unit_test` constructs a fresh
observer per call and reads it back — fully typed end to end, no generic data
channel, and the engine stays method-agnostic. Whether the verdict *also*
lands as a workspace file is deliberately **not** decided here — it becomes a
question only when persistence needs it (task 12).

### 5.6 `score` requires `OutputState.OK`
Today: `resolved = output_found and is_resolved(passed)` (`grading.py:200`) —
`output_found` + empty parse could in principle still "resolve" a required-
tests-empty instance; with the tri-state, `score` is definitionally
`1.0 if (output_state is OK and required <= passed) else 0.0`, and `resolved`
is `score >= 1.0`. Stricter and honest; no 731-sweep behavior change expected
(every instance has required tests) — CP1's parity run is the proof. The
scalar `score` (rather than a bare bool) is what lets a future rubric /
model-judge eval method report partial credit through the same `Verdict`
surface — unit-test grading simply pins it to {0.0, 1.0}.

## 6. Tests (all pure / FakeBackend; no Docker)

- **Script builder matrix (audit P0-3):** flag combinations
  (`apply_patch` × `checkout_golden_tests`) presence/absence of lines;
  multi-line `before_repo_set_cmd` → last line only; empty
  `before_repo_set_cmd` → no restore line; `$`/`[...]` test names quoted;
  text-level parity with the pinned old-builder fixture (§5.3).
- **Grader tri-state (audit P0-2):** with `output.json` **and**
  `required_tests.json` pre-written into a temp workspace, `grade(workspace)`
  over absent / corrupt / non-dict / mixed-status output → the exact
  `OutputState` + `score`/`resolved` + `missing` table of §4; unreadable
  output → UNPARSEABLE; full pass → `score == 1.0`, partial → `0.0`. The
  grader is a zero-field object — the test constructs `SweBenchProGrader()`
  and feeds it only a workspace (proving statelessness).
- **Compile:** mounts contain run_script/parser + **required_tests.json**
  (= `sorted(fail_to_pass ∪ pass_to_pass)`) (+ patch iff given) with correct
  content from a faked harness cache (no network — files pre-written to
  `harness_dir`); `SandboxSpec` fields map from the record
  (`execution.py:97-108` equivalence).
- **Composition:** `run_unit_test` on FakeBackend — the eval script is the
  single body exec; the verdict lands on the observer and in the returned
  tuple; grader runs even when the body exec fails (before_destroy semantics,
  task 02 matrix); an early setup failure returns `verdict=None` with
  `status=SETUP_ERROR`.
- **Verdict protocol:** `SweBenchProVerdict` satisfies `Verdict`
  (basedpyright enforces the generic bound at compile time — that *is* the
  test).

## 7. Dependencies

None new. PEP 695 generics (`class Grader[V: Verdict]`) — Python 3.13 ✓,
basedpyright ✓ (both already in the toolchain).

## 8. Open questions (need user confirmation)

1. **`run_unit_test` returns `tuple[RunResult, V | None]`** (§5.5) — or would
   you rather a small `UnitTestRun[V]` result dataclass? Tuple is my
   recommendation (two values; `None` verdict only on early setup failure).
2. **Old-builder parity fixture (§5.3)** — I plan to pin today's
   `build_eval_script` output for 2–3 real instances as test fixtures
   (checked-in text files, a few KB). OK to commit those?
3. **Placement of the SBP compile module** — `datasets/swebench_pro/unit_test.py`
   (my pick: the dataset owns its compile), not
   `evaluation/methods/unit_test/swebench_pro.py` (which would invert the
   dependency: the method must stay dataset-agnostic). Confirm.
