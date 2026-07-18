# Patch extraction — decisions (D1–D8)

> ⚠️ **Provisional — NOT source of truth (flagged 2026-07-17).**
> These decisions went through a lot of back-and-forth revision, and the written
> record is not fully trusted. **The current code is authoritative** —
> [`core/patch.py`](../../src/swe_lab/core/patch.py),
> [`rollout/`](../../src/swe_lab/rollout/), and
> [`core/datasets/swebench_pro/grading.py`](../../src/swe_lab/core/datasets/swebench_pro/grading.py).
> Read the code before relying on anything here. This log (and the grounded
> survey [`docs/patch-extraction.md`](../patch-extraction.md) it references) is
> kept as an audit trail and **is scheduled for a joint re-review** before it is
> trusted again or split into individual ADRs. Do not present D1–D8 as settled.

These were open items reviewed against [`docs/patch-extraction.md`](../patch-extraction.md)
(the grounded corner-case survey) and the implementation in
[`core/patch.py`](../../src/swe_lab/core/patch.py). They were marked decided
(user, 2026-07-17) and the code, its docstrings, and the doc were reconciled to
match at that time — but see the banner above: the record has since been called
into question. Each item keeps its rationale and records the decision; the
per-item history (doc-said / code-said / tension) is retained as the audit trail.

**Decisions at a glance**

| # | Topic | Decision | P1/later backlog |
| --- | --- | --- | --- |
| D1 | diff base | diff vs the instance's **`base_commit`** (guarantees the grading round-trip) | post-setup-commit base → **P1** |
| D2 | build-noise denylist | **none** by default (`exclude_globs` empty); no material impact today | per-ecosystem `:(exclude)` denylist → **P1** |
| D3 | binary | **happy path: text only** — `git add -N` + diff **without `--binary`**, then `strip_binary_hunks` on the host removes the residual bytes-free marker | faithful binary extract+apply → **P1** |
| D4 | diff prefixes | keep the **`-c diff.noprefix=false -c diff.mnemonicPrefix=false`** pins (empirically equivalent to `--default-prefix`, and needed for git < 2.41) | — |
| D5 | agent test-file edits | grading already restores the **golden** test files by path (see finding) | fuller all-test-file reset → **P2 / monitor** |
| D6 | eval apply tolerance | **no change** — keep the single strict `git apply -v` (matches Scale) | opt-in `--3way`/`--reject` ladder → later, behind a flag |
| D8 | empty patch | **rollout-side explicit outcome** (`empty_patch` vs `unresolved_tests_failed`), never graded as a pass | eval-side defensive guard → **P2** |

D7 (deferred silent-failure guards: gitignored-new-source, submodules, LFS) stays
deferred; add detection + logging when first needed (**P1** when it bites).

The per-item detail below is the rationale record.

## D1. Base to diff against: post-setup commit vs raw `base_commit`

- **Doc (§7, §2):** step 2 diffs `--cached ... "$BASE_COMMIT"` — i.e. against the
  instance's original `base_commit`.
- **Code:** `build_extraction_script(base_ref=...)` diffs against a caller-supplied
  `base_ref`, and its docstring specifies `base_ref` should be *"the post-setup
  commit the rollout entryscript makes right before the agent runs"* — a fresh
  commit of the container's post-provision state, **not** `base_commit`.
- **Tension:** these are different strategies. Diffing against a **post-setup
  commit** (the "commit-the-base trick", §4A.4) means any tree mutations from
  image setup / `before_repo_set_cmd` are already *in the base* and cannot leak
  into the patch — which is exactly why the code can drop the `:(exclude)`
  denylist (see D2). Diffing against raw `base_commit` (the doc) is simpler and
  matches what the grader applies against, but re-admits all of §4A.4/§4A.5's
  contamination.
- **Decision:** diff against the instance's **`base_commit`** (not a post-setup
  commit). The code passes `base_ref=base_commit` and the rollout entryscript uses
  it. Rationale: it *guarantees* the grading round-trip — the grader does
  `git reset --hard base_commit` then `git apply`, and the dataset's own gold
  patches are diffs vs `base_commit` and apply, so the convention is proven. The
  post-setup-commit base (which would auto-absorb setup noise) is a nicer but
  fiddlier option — deferred as **P1**.

## D2. `:(exclude)` build-noise denylist: keep, drop, or make tunable

- **Doc (§7 step 1):** hardcodes the mini-swe-agent #528 denylist
  (`pyproject.toml`, `setup.cfg`, `setup.py`, `tox.ini`, `*.cfg`, `*.toml`).
- **Code:** `exclude_globs` defaults to **empty** (no denylist); the docstring
  argues it is unnecessary *given* the post-setup base of D1, but keeps the
  parameter for "the rare instance that still needs it."
- **Tension:** the post-setup base removes *setup-time* noise, but the **agent's
  own run** can still trigger a build tool that rewrites `pyproject.toml` /
  lockfiles *after* the base snapshot — that noise would still enter the patch.
  So the denylist may still be wanted as a secondary defense even with D1.
- **Decision:** **no denylist** by default — `exclude_globs` stays empty. There
  is no material impact today (an agent solving a task rarely rewrites build
  config). A per-ecosystem `:(exclude)` denylist (with logging of dropped paths,
  no silent truncation) is deferred as **P1**; the `exclude_globs` param is
  already there to wire it when needed.

## D3. Binary handling (was self-contradictory across §7 / §8 / code)

- **Doc §7 (grading):** "keep single `git apply -v` **and add `strip_binary_hunks`**
  before writing `patch.diff`."
- **Doc §8 (later, 2026-07-15):** grading "deliberately does **not** call
  `strip_binary_hunks`" because "the extractor's `git diff` omits binary by
  default so the patch reaching the grader is already binary-free."
- **Code:** the extractor's `_DIFF_FLAGS` **includes `--binary`**, which *emits*
  an applyable binary patch — the opposite of "omits binary by default." So the
  §8 premise is false as written, and §7 and §8 give opposite instructions.
- **Tension:** with `--binary` at extraction + no strip at grading, a
  binary-containing agent patch is applied verbatim by our `git apply -v` — but
  Scale **strips** binary before applying (§1), so our grade would **diverge from
  Scale's** on exactly those patches. Meanwhile §4B.1 wants faithful binary
  capture *for the trace*.
- **Decision — happy path, text only (no binary):** extraction stages new
  files with `git add -N` and diffs **without `--binary`**, so binary *bytes* are
  never serialized; the rollout runner then calls `strip_binary_hunks` to remove
  the residual bytes-free `Binary files ... differ` marker (which would otherwise
  break `git apply`). Grading applies verbatim — the patch it receives is already
  binary-free (gold patches are binary-free; rollout patches are stripped
  upstream), so it does not call `strip_binary_hunks`. **Empirically verified**
  (2026-07-17): `git add -N` + no-`--binary` and `git add -A --cached` +
  no-`--binary` produce **byte-identical** output — it is *omitting `--binary`*,
  not `-N`, that drops binary; `-N` is just the lighter intent-to-add idiom. The
  stripped text patch applies cleanly against `base_commit`. Faithful binary
  extract+apply (a two-artifact scheme) is deferred as **P1**.

## D4. `--default-prefix` vs the `-c` prefix pins (git-version compat)

- **Doc §7:** uses `git diff ... --default-prefix`.
- **Code:** deliberately **avoids** `--default-prefix` (git ≥ 2.41 only) and pins
  `-c diff.noprefix=false -c diff.mnemonicPrefix=false` instead, for the same
  `a/ b/` prefixes on any git version.
- **Decision:** keep the **`-c diff.noprefix=false -c diff.mnemonicPrefix=false`**
  pins; do **not** use `--default-prefix`. Verified (2026-07-17): the local git
  (2.39.5) does not even support `--default-prefix` (it needs git ≥ 2.41), and
  under our config isolation the `-c` pins are equivalent — both force `a/ b/`
  prefixes. The doc §7 is updated to match. No effect difference; comments in
  `core/patch.py` explain the why.

## D5. Agent-touched **test files** (§5.1) — the potential-gaming risk

- **The risk:** a solver can game the held-out tests by editing them. SWE-Bench
  classic defends by resetting modified test files to base + removing agent-created
  test files, *then* applying the gold `test_patch` (§5.1/§5.2), with a
  **modified-vs-new** distinction that is load-bearing (issue #518).
- **Unknown:** whether SWE-Bench **Pro**'s per-instance `run_script.sh` +
  our ported `build_eval_script` already reset agent-touched test files, or
  whether nothing does. If nothing does, our grade is gameable.
- **Finding (2026-07-17):** our `build_eval_script` (in
  `core/datasets/swebench_pro/grading.py`) already restores the **golden test
  files by path** *after* applying the model patch — the last line of
  `before_repo_set_cmd` is a `git checkout <sha> -- <test files>` that runs post-
  apply, so an agent's edits to the graded test files are overwritten and cannot
  game the held-out tests. This covers the load-bearing case.
- **Residual (P2 / monitor):** only the *golden* test files are restored, not
  arbitrary agent-created/edited test files elsewhere; those don't affect the
  `fail_to_pass`/`pass_to_pass` set unless they *are* the graded tests, so risk is
  low. Revisit with a fuller all-test-file reset only if a real instance shows a
  gap.

## D6. Eval-side apply hardening ladder (opt-in deviation from Scale)

- **Doc §7 (grading):** offers an *optional* fallback ladder
  `git apply -v → git apply --3way → git apply --reject` (we hold `base_commit`,
  so `--3way` is viable), explicitly opt-in + logged, never silent.
- **State:** not built; grading is the single strict `git apply -v` (matches
  Scale).
- **Decision:** grade **strictly like Scale** — single `git apply -v`, no
  ladder, for now. The opt-in `--3way`/`--reject` hardening is a later,
  clearly-labeled experiment (user still weighing whether the extra tolerance is
  worth diverging from Scale's grade). No change to the grading path today.

## D7. Deferred guards — defer, but **log when they would fire**

Three §4 corner cases are deferred "until a real instance needs them"; the risk is
that they fail **silently**. Decision: keep them deferred **but add detection +
logging** so the gold/rollout sweep surfaces any instance that hits them, instead
of silently mis-extracting:

- **Gitignored new source files** (§4A.2) — `git add -A` silently skips them; a
  real new source file that happens to match `.gitignore` vanishes. Detect via a
  `git status --ignored` / `git check-ignore` pass and log; force-add only if it's
  genuinely source.
- **Submodules / gitlinks** (§4C.6) — `--ignore-submodules=all` is the fix; until
  then, log any `Subproject commit` line reaching the patch.
- **Git LFS pointer-vs-content** (§4B.2) — detect `filter=lfs` via `.gitattributes`
  / `git check-attr` and log; decide pull-objects vs exclude per instance.

## D8. Empty-patch guard — wire it, and on which side(s)

- **Code:** `is_effectively_empty` exists and is tested, but is **not wired** into
  extraction or grading (§8 confirms "no empty-patch guard").
- **Decision — rollout-side, with an explicit outcome:** the rollout runner
  computes `is_empty` via `is_effectively_empty(patch)` (after binary stripping),
  and the CLI records an explicit `outcome`: `empty_patch` (agent produced no
  edits — the eval is **skipped**, never graded as a pass) vs
  `unresolved_tests_failed` (a real patch that graded false) vs `resolved`. So an
  unresolved run's *reason* is read from the log, never guessed. An eval-side
  defensive guard (for a directly-supplied empty `--patch-file`) is deferred as
  **P2**.

## Where each decision landed in code (2026-07-17)

- D1/D2/D3/D4 → `core/patch.py` (`build_extraction_script`, `_DIFF_FLAGS`,
  `strip_binary_hunks`) + `rollout/entryscript.py` (diffs vs `base_commit`) +
  `rollout/runner.py` (strips binary → clean `patch.diff`, keeps raw for audit).
- D3/D8 → `rollout/runner.py` (`is_empty`, `binary_stripped`) +
  `rollout/__main__.py` (explicit `outcome`). D6 → `grading.py` unchanged (strict
  `git apply -v`). D5 → `grading.py` already restores golden test files.
- `docs/patch-extraction.md` §7–§8 reconciled to match. **P1 backlog:** post-setup
  base (D1), `:(exclude)` denylist (D2), faithful binary extract+apply (D3),
  deferred silent-failure guards (D7); **P2:** eval-side empty guard (D8), fuller
  test-file reset (D5).
