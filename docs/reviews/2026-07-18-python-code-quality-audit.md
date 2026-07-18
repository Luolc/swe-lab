# Code-quality audit — `src/swe_lab/` (2026-07-18)

A one-off, cross-cutting engineering review of the Python source (**not** a
dataset quality audit — that is [W3](../workstreams/w3-quality-audit/README.md)).
Scope: all of `src/swe_lab/` (~5.3k LOC); excludes `tests/`, `.cache/`,
`experiments/`. Reviewed against the `python-library-complete` skill checklists
(code-quality, security, testing, api-design, performance, documentation) plus
`bandit` and `pip-audit`.

**This is a snapshot, not a spec.** Where it and the code disagree, the code
wins. Check items off / delete them as they land; promote anything structural to
an ADR before acting on it.

> **Release intent (2026-07-18).** `swe-lab` **is** slated to be released, and
> its documentation matters — so packaging, release-management, and
> documentation findings are **first-class here, not deprioritized**. In
> particular the API surface (public docstrings) is release-facing; see
> [§ Documentation](#p1--documentation-release-facing).

## Baseline — what's already good

Genuinely above-bar, worth *not* regressing:

- `ruff check src` and `basedpyright src` both pass clean; no mutable defaults,
  no bare `except`, no `is`-vs-literal, fail-loud error handling throughout.
- No `shell=True`, no `os.system`, no `pickle` / `yaml.load` / `eval` / `exec`,
  no `tarfile`/`zip` extraction. All subprocess calls use argv lists.
- The downloaded Claude binary is sha256-verified against the release manifest.
- Committed trace records scrub auth headers (`_SENSITIVE_HEADERS`),
  `metadata.user_id`, and operator PII.
- `pip-audit`: no known dependency vulnerabilities. `bandit`: 0 High.

Almost everything below is hardening / polish, not a correctness defect. The two
exceptions (P0-2, P0-3) sit on the eval pass/fail path and deserve real
attention.

---

## P0 — fix soon

### 1. Raw OAuth token is injected into the rollout container (token-exfiltration surface)

- `rollout/runner.py:120-121` — rollout runs with `network` on **and**
  `pass_env=[OAUTH_TOKEN_ENV]` → `core/docker/provider.py:96-97` runs
  `docker run -e CLAUDE_CODE_OAUTH_TOKEN`, so the raw credential lives in the
  container env.
- Inside, `claude --dangerously-skip-permissions`
  (`rollout/entryscript.py:72-77`) drives the target repo's *own* build/test
  tooling — untrusted code — with network egress. A malicious build/test hook can
  read `$CLAUDE_CODE_OAUTH_TOKEN` and `curl` it out.
- **The annotation path already solves this**: it keeps the token host-side and
  routes the CLI through a loopback reverse proxy (`core/agent/proxy.py`); the
  token never enters an untrusted child's env/argv. Rollout abandons that
  protection.
- **Fix (structural — write an ADR first):** reuse the host-side
  `cc-reverse-proxy` for rollout too; point the in-container agent at it via
  `host.docker.internal` and inject only a dummy base URL. Short-term fallback:
  scoped/short-lived tokens + a documented trust assumption.

### 2. Corrupt/partial `output.json` is silently read as "0 tests passed" → false GOLDEN_FAIL

- `core/datasets/swebench_pro/grading.py:185-194` — `_passed_tests` does
  `except (json.JSONDecodeError, OSError): return frozenset()`. A present-but-
  unparseable result (parser crashed mid-write, truncated JSON, unreadable file)
  is indistinguishable from "genuinely nothing passed".
- `evaluate` then reports `output_found=True, resolved=False`, which
  `evaluation/verify.py:57-63` (`classify`) maps to **`GOLDEN_FAIL` (a dataset
  finding)** rather than `ERROR`. A harness/parse glitch masquerades as "the gold
  patch doesn't fix the bug".
- This is the same false-GOLDEN_FAIL class the repo already fought (truncated
  golden test names; see
  [`experiments/eval_issues/truncated_golden_test_names/`](../../experiments/eval_issues/truncated_golden_test_names/README.md)),
  reached via a different trigger.
- **Fix:** distinguish "file absent" from "file present but unparseable" — add an
  `output_parse_error` state to `EvalResult` and have `classify` map it to
  `ERROR`. Catch narrowly; don't fold `OSError` + `JSONDecodeError` into "nothing
  passed".

### 3. `grading.py` — the core pass/fail logic — has no tests

- `build_eval_script`, `evaluate`, `_passed_tests`
  (`core/datasets/swebench_pro/grading.py`, 194 LOC) are untested.
  `tests/test_swebench_pro_exec.py`'s docstring claims to cover "grading" but
  actually exercises `benchmark.py` (`image_ref`, `EvalSpec.required_tests`, …).
- `build_eval_script` and `_passed_tests` are pure — testable without Docker.
- **Fix:** add `tests/test_grading.py`: assert the generated script omits
  `git apply` when `apply_patch=False`, omits the golden checkout when
  `checkout_golden_tests=False`, picks the last line of a multi-line
  `before_repo_set_cmd`, and `shlex.quote`s a test name containing `$`/`[`. Test
  `_passed_tests` against sample parser-output JSON (including the P0-2 corrupt
  case).

---

## P1 — hardening & regression-proofing

### 4. Docker run is a "container", not a "sandbox"

- `core/docker/provider.py:88-104` — `docker run --rm` with no `--memory`,
  `--cpus`, `--pids-limit`, `--read-only`, `--user`, `--cap-drop`, or
  `--security-opt`. Untrusted code runs as the image default user (often root)
  with no resource ceiling.
- `core/datasets/swebench_pro/grading.py:120` — `evaluate(..., network=True)` by
  **default**; grading doesn't need egress (rollout does).
- **Fix:** default grading to `network=False` (opt in per-instance only when
  genuinely needed); add `--pids-limit`, `--memory`, `--cpus`, `--cap-drop=ALL`
  to `run_script`.

### 5. No security scanning in CI or pre-commit

- `.github/workflows/ci.yml` runs only pytest + pre-commit; no `bandit` /
  `pip-audit` / `detect-secrets`; no `.secrets.baseline`.
- Manual `bandit -r src/ -ll`: 0 High, 11 Medium — mainly `rollout/__main__.py:64`
  (HF `load_dataset` without a pinned revision, B615) and
  `rollout/constants.py:20` (hardcoded `/tmp/rollout-home`, B108).
- **Fix:** add a `security` CI job (`bandit -r src/ -ll`, `pip-audit`) and a
  `detect-secrets` pre-commit hook with a committed baseline. Pin the HF dataset
  revision.

### 6. No coverage measurement at all

- No `pytest-cov`, no `--cov-fail-under`, no `[tool.coverage]`. CI is the merge
  gate but nothing stops whole modules rotting untested — and they have: the
  three largest/most critical modules (`grading.py`, `agent_run.py` @482 LOC,
  `traces.py` @437 LOC) are ~untested.
- **Fix:** add `pytest-cov`; set `--cov-fail-under` to the current measured number
  and ratchet up. Prioritise pure helpers in the three modules above.

### 7. Integer validation silently truncates floats

- `pipelines/related_files/schema.py:113-121` (`_as_int`) and
  `pipelines/related_files/agent_validator.py:60-67` (`_to_int`) accept `float`
  and call `int(value)`, so `42.7 → 42`. This is the validation path meant to
  catch malformed agent output; a wrong-but-plausible line number slips through.
- **Fix:** reject non-integral floats
  (`if isinstance(v, float) and not v.is_integer(): raise ValueError(...)`) or drop
  `float` from the accepted set.

---

## P1 — documentation (release-facing)

Because `swe-lab` will be released, its public docstrings become the API
reference (Sphinx autodoc). Current state, measured:

- **Coverage is good.** Every module has a module docstring; most of the 218
  `def`/`class` definitions carry at least a one-line summary. The module-level
  narratives (e.g. `grading.py`, `schema.py`) are genuinely informative and worth
  keeping.
- **Style is not Google/Napoleon at all.** Across the whole source there are
  **0** `Args:`, `0` `Returns:`, `0` `Raises:`, `0` `Examples:` sections. The
  house style is narrative prose + Sphinx RST inline roles (`:class:`, `:mod:`,
  ``` ``literal`` ```). Autodoc will render the prose but expose no structured
  parameter/return/raise reference.
- **Verbosity is unbalanced.** Wide public signatures are under-documented while
  some trivial helpers are over-documented. Worst offender: `evaluate()`
  (`core/datasets/swebench_pro/grading.py`) takes 9 parameters — including
  `network`, `checkout_golden_tests`, `timeout`, `provider`, `workspace` — behind
  a single one-line summary, so a caller can't tell what `network=True` does
  without reading the body. Same shape in `build_eval_script` and several
  `pipelines/related_files` entry points.

### Task — Google-style docstring rewrite (planned, do later)

A dedicated pass, scoped as its own PR series. Concrete definition so it can drop
into `/plan` later:

1. **Convert public API to Google style** (Napoleon-compatible): imperative
   one-line summary → `Args:` / `Returns:` / `Raises:` for every exported symbol,
   every `__main__` entry point, and the `core/` contracts
   (`EvalSpec`, `EvalResult`, `DockerProvider`, `RepoProvider`, `Snippet`,
   `Annotation`, …). Keep the existing rich module narratives; just ensure their
   first line is a single imperative sentence.
2. **Rebalance verbosity, don't just add.** Document a parameter only where it's
   non-obvious (units, side effects, defaults with consequences like
   `network`/`checkout_golden_tests`); skip `Args:` for 1–2-param private helpers
   whose names are self-evident. Trim narrative that restates the signature.
3. **Keep the Sphinx cross-ref roles** (`:class:`/`:mod:`) — Napoleon renders them
   fine; they're an asset, not a thing to strip.
4. **Add `Example:` blocks** to the handful of top-level user-facing entry
   functions only (not internal helpers).
5. **Enforce it** so it doesn't rot: turn on ruff's pydocstyle rules with the
   google convention —
   ```toml
   [tool.ruff.lint]
   extend-select = ["D"]
   [tool.ruff.lint.pydocstyle]
   convention = "google"
   ```
   Start with `D` selectively (public-only via per-file ignores for `tests/` and
   `__main__`), fix, then keep it green. This pairs with the eventual Sphinx +
   `sphinx.ext.napoleon` setup from the documentation skill.

Sequence this **after** the P0 correctness work but it can run in parallel with
P1 hardening — it touches only docstrings.

---

## P2 — polish & consistency (batchable)

- **Unknown / `MISSING` verdicts vanish from the summary.**
  `evaluation/verify.py:238-292` — `counts` carries a `"MISSING"` fallback but the
  report only emits the four known `_VERDICTS`, so `verified` won't reconcile with
  the visible counts. Surface non-`_VERDICTS` keys (or assert none exist).
- **No single base exception.** `AnnotationError` (`core/agent/errors.py:21`),
  `DockerError` (`core/docker/provider.py:16`), `GitError`
  (`core/repo/provider.py:66`), `SyncError` (`traces.py:59`) share no ancestor —
  a batch driver can't `except SweLabError`. Add a base and reparent. Separately,
  `AnnotationError` is task-named but lives in the task-agnostic `core.agent`, and
  rollout doesn't use it — consider a neutral `AgentRunError`.
- **`dict[str, object]` for fixed-shape internal records.** `RunResult.metadata`
  (`agent_run.py`), the exchange record (`core/agent/trace.py`) — internally built
  with a known shape; a `TypedDict` would catch key typos. (Leave raw external-CLI
  JSON as `object`.)
- **`_redact_pii` forks two `git config` subprocesses per record.**
  `core/agent/trace.py:212-227` recomputes constants on every exchange; a bulk
  re-normalization of ~2924 traces would spawn ~5.8k git processes. Memoize with
  `functools.lru_cache`.
- **Smaller items:** five duplicated `save_diagnostics + raise` blocks
  (`agent_run.py:411-443`); `build_dataframe` aborts on the first bad
  `aggregate.json` without naming the file (`combine.py:84-88`); text I/O without
  explicit `encoding="utf-8"` (`verify.py:274`, `evaluation/__main__.py:55`, …);
  missing `py.typed` marker (now **relevant** — a released, basedpyright-strict
  package should ship one so downstream type-checkers consume its hints);
  `RunResult` is the lone non-`frozen` result dataclass
  (`agent_run.py:71`); unguarded `json.loads` of the last proxy line
  (`core/agent/trace.py:122`) can discard a good result on a truncated log line.

---

## Suggested sequencing

1. **P0-2 + P0-3 together** (grading parse-failure state + `test_grading.py`) —
   one focused PR; smallest change with the highest correctness payoff, and it
   directly protects the eval deliverable.
2. **P0-1** (rollout token via proxy) — write an ADR first; it's a
   security-design decision, not a drive-by edit.
3. **P1 hardening** (sandbox flags + `network=False`, security CI, coverage) —
   each an independent PR.
4. **P1 documentation** (Google-style docstring rewrite + ruff `D`/google gate +
   `py.typed`) — its own PR series; can run in parallel with #3 since it touches
   only docstrings. Release-facing, so not optional.
5. **P2** — batch into a single "consistency pass" PR.
