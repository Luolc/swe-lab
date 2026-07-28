# Task 10b — Cutover + deletion (core/ dissolves)

> **Design record** (point-in-time; may predate the landed code). **Status is
> tracked only in [`plans/README.md`](README.md)**; where this doc and the code
> disagree, the **code wins**.

---

## 1. Purpose & scope

Finish the cutover: port the golden-verify CLI onto the engine, **relocate the
last general pieces out of `core/`**, delete every legacy package/module, empty
`core/` entirely, switch the workflows to the new CLI, and prove the harness
seam with a stub.

### Scope note — the moves the original bullets missed

The task-index bullets named only "delete `rollout/` + `evaluation/`, retire
`benchmark.py`, empty `core/`". But an audit found `core/agent/` and
`core/patch.py` still had **new-engine** consumers (the claude_code harness and
the diff-extract observer), so emptying `core/` required relocating them too
(spec migration mapping: "general parts of `agent/` → `harnesses/claude_code/`;
`patch.py` → `sandbox/`"). Those relocations are folded in here.

## 2. What changed

### Relocations (empty core/ of its live pieces)
- `core/patch.py` → `sandbox/patch.py` (consumer: the diff-extract observer).
- `core/agent/{binary,proxy,trace,errors}.py` → `harnesses/claude_code/`
  (consumers: the harness, `solve.py`, and W1 `pipelines/`). `trace.py` is the
  legacy dict parser W1 still uses; it dies with the W1 migration, not here.

### Verify port (engine)
- `evaluation/verify.py` → `cli/verify.py` (`verify_cmd`, Typer), registered in
  the dispatcher. The two graded runs become `compile_unit_test(patch=None)` /
  `(patch=instance.patch)` + `run_unit_test`; `classify` re-expressed against
  `SweBenchProVerdict` (`resolved`/`passed`/`output_state`) + `RunStatus`
  (ERROR ⇐ non-`SUCCESS` status, `None` verdict, or `output_state != OK`). All
  sharding / resumability / aggregate / report behaviour preserved.
  `--prune-images` shells `docker image rm -f` (the engine backend has no
  remove-image).

### Deletions (legacy)
- `rollout/` package (whole) + `tests/test_rollout.py`.
- `evaluation/__main__.py`, `evaluation/verify.py`.
- `datasets/swebench_pro/grading.py` (legacy `evaluate`/`EvalResult`/
  `build_eval_script`) + the `SweBenchProAdapter`/`eval_spec` in `execution.py`
  (the `EvalSpec` mapper) + `tests/test_swebench_pro_exec.py`.
- `core/benchmark.py` (`EvalSpec`), `core/docker/` (`DockerProvider`),
  `core/agent/` (emptied), `core/__init__.py` → **`src/swe_lab/core/` no longer
  exists.**
- `.github/workflows/eval-parity.yml` — retired (its whole purpose was the
  legacy-vs-engine verdict diff for CP1, which passed; the legacy leg is gone).

### Workflows
- `verify-golden.yml` → `python -m swe_lab verify` (shard + aggregate).
- `rollout.yml` → `python -m swe_lab rollout`.

### Harness-stub seam (Success #3)
- `tests/test_harness_stub_seam.py`: a `StubHarness(Harness)` runs end-to-end
  through the real `SandboxManager` + `ConversationObserver` + `GitHubJobBackend`
  (local bash), importing only the public engine surface — proving a new harness
  composes with **zero** engine change.

## 3. Acceptance — met

- `src/swe_lab/core/` no longer exists; no `swe_lab.core.*` import remains.
- The workflows call the new CLI; `python -m swe_lab {eval,rollout,verify}` all
  register.
- Stub-harness seam test green.
- Full suite green (184), pre-commit clean.

## 4. Notes / follow-ups

- W1 (`pipelines/`) now imports the relocated modules from their new homes; W1's
  own architecture is untouched (spec: not migrated now).
- Docs still referencing old paths/commands are swept in [task 11](task-11-…).
- Live golden sweep (731/731) + flipt rollout re-run over the new CLI is **CP3**
  (user-triggered manual dispatch).
