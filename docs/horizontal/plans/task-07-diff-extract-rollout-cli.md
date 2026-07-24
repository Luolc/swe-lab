# Task 07 — Diff-extract observer + rollout CLI

> **Status: PLANNED — pre-implementation.** Source of truth: the approved
> [spec](../spec.md), [ADR-0001](../../decisions/ADR-0001-patch-extraction-and-grading.md)
> (settled patch-extraction contract), tasks [02](task-02-engine-core.md)/
> [03](task-03-a-host-backend.md)/[04](task-04-unit-test-method.md)/
> [06](task-06-claude-code-harness.md). Grounded in the current rollout
> (`src/swe_lab/core/patch.py`, `src/swe_lab/rollout/{runner,entryscript,
> __main__}.py` at `fae1738`). Open items in §8.

---

## 1. Purpose & scope

Complete the **rollout slice**: a shared **diff-extract observer** (ADR-0001
extraction, harness-agnostic), the **`run_rollout` composition** (task 06
harness body + trace + diff-extract over the engine), explicit **outcome**
recording, and the **`rollout` CLI** on the single entry point. Grading reuses
task 04. Delivers the CP2 payload (flipt rollout end-to-end in CI).

### In scope

- `sandbox/observers/diff_extract.py`: `DiffExtractObserver` (shared) — execs
  the extraction script into the live container in `before_destroy`, strips
  binary hunks host-side, keeps the clean patch + flags.
- `evaluation`/rollout glue: `run_rollout(...) -> RolloutOutcome` composing
  harness + observers over a `DockerHostBackend`.
- `swe_lab/cli/rollout.py` + a dispatcher entry; `rollout.yml` → `python -m
  swe_lab rollout …`.
- Fast Docker-free tests; the live flipt rollout is CP2 (manual CI).

### Out of scope

- Deleting `rollout/` (10b); the proxy capture mode (task 08); the audit-P0-1
  token-via-proxy security change (its own ADR — plan §Out of scope).
- ADR-0001 **D7** silent-failure guards — documented, still unimplemented
  (ADR:50, ADR:59-71); this task preserves that status (no new guards).

## 2. Module layout

```
sandbox/
  observers/
    __init__.py
    diff_extract.py     DiffExtractObserver (shared; reuses core/patch.py)
evaluation/  (or a rollout module — §8 Q1)
  rollout.py            run_rollout(...) + RolloutOutcome
swe_lab/cli/
  rollout.py            main(argv) -> int
```

Tests: `tests/test_diff_extract_observer.py`, `tests/test_run_rollout.py`,
`tests/test_cli_rollout.py`.

## 3. Key types & signatures

```python
# ─── sandbox/observers/diff_extract.py ──────────────────────────────────────
@dataclass
class DiffExtractObserver(SandboxObserver):
  """Stateful: extract the worktree diff vs base_commit; strip binary hunks.

  Runs in before_destroy (the container is still live), so it works for ANY
  harness that edits the repo — extraction is not baked into the agent script.
  """

  exclude_globs: tuple[str, ...] = ()
  patch: str = ""              # clean, text-only diff vs base_commit (may be "")
  is_empty: bool = True
  binary_stripped: bool = False

  def before_destroy(self, sb: Sandbox) -> Contribution | None:
    script = build_extraction_script(
        workdir=sb.spec.workdir, base_ref=sb.spec.base_commit,
        output_path=f'"$SANDBOX_WORKSPACE"/{RAW_PATCH_NAME}',
        exclude_globs=self.exclude_globs,
    )
    # stage extract.sh into the workspace, then run it by path (persisted for
    # audit — same file-based exec as the pre-run scripts, not stdin)
    (sb.workspace / EXTRACT_SCRIPT_NAME).write_text(script)
    _ = sb.run(EXTRACT_SCRIPT_NAME, timeout=_EXTRACT_TIMEOUT_S)
    raw = _read_patch(sb.workspace / RAW_PATCH_NAME)
    self.patch = strip_binary_hunks(raw)
    self.binary_stripped = self.patch != raw
    self.is_empty = is_effectively_empty(self.patch)
    (sb.workspace / PATCH_NAME).write_text(self.patch)
    return Contribution(artifacts={
        "patch": sb.workspace / PATCH_NAME,
        "patch_raw": sb.workspace / RAW_PATCH_NAME,
    })

# ─── evaluation/rollout.py ──────────────────────────────────────────────────
@dataclass(frozen=True)
class RolloutOutcome:
  instance_id: str
  patch: str
  is_empty: bool
  binary_stripped: bool
  complete: bool                 # agent finished cleanly (harness-derived from raw output)
  conversation: Conversation     # canonical typed trace (task 06a)
  status: RunStatus              # engine status
  workspace: Path

def run_rollout(
    spec: SandboxSpec, *, prompt: str, model: str, backend: SandboxBackend,
    workspace: Path, timeout: float, exclude_globs: tuple[str, ...] = (),
) -> RolloutOutcome: ...
    # harness = ClaudeCodeHarness(model=model)   # dataset-agnostic — NO prompt
    # conv = ConversationObserver(producer=harness)   # shared; harness is a ConversationProducer
    # extract = DiffExtractObserver(exclude_globs)
    # backend carries the read-only binary ASSET (/opt) — task 06 §5.3:
    #   backend = replace(backend, assets=harness.assets())          # a dict
    # the prompt is dataset-derived (task 06 §5.6); the composition stages it as
    # prompt.txt, merged with the harness's own mounts (agent.sh):
    #   mounts = {PROMPT_NAME: Mount(Inline(prompt.encode()))} | harness.mounts(spec.workdir)
    # mgr = SandboxManager(spec, backend, workspace, observers=[conv, extract], mounts=mounts)
    # with mgr.sandbox() as sb: harness.run(sb, timeout=timeout)   # direct — no factory
    # assemble RolloutOutcome from extract.* + conv.conversation +
    #   the harness completion signal + mgr.result.status
```

`_read_patch` (bytes → `decode("utf-8", "backslashreplace")`, `""` when absent)
is ported verbatim from `runner.py:162-171` into `core/patch.py` (or the
observer), so the tolerant-decode contract is preserved.

## 4. Extraction as a before_destroy exec (not appended to the agent script)

Today extraction is **appended to the agent entryscript** and runs in the same
container invocation (`entryscript.py:79-84`). In the engine it becomes a
**second `exec` in `before_destroy`** against the still-live persistent
container. Element mapping:

| Today | Engine |
|---|---|
| `build_rollout_script` appends `build_extraction_script(...)` (`entryscript.py:79-84`) | harness body runs the agent only; `DiffExtractObserver.before_destroy` execs `build_extraction_script` separately |
| `raw = _read_patch(RAW_PATCH_NAME)` (`runner.py:144`) | same, in the observer |
| `patch = strip_binary_hunks(raw)` (`runner.py:145`) | same |
| write `PATCH_NAME`, keep raw for audit (`runner.py:146`) | same, + register both as artifacts |
| `is_empty=is_effectively_empty(patch)`, `binary_stripped=patch!=raw` (`runner.py:151-152`) | observer fields |

`build_extraction_script`, `strip_binary_hunks`, `is_effectively_empty` are
reused unchanged from `core/patch.py` (patch.py:121, :39, :64) — the ADR-0001
contract (worktree diff vs `base_commit`, `git add -N`, **no `--binary`**,
strip residual `Binary files … differ` host-side, isolated git config) is
preserved byte-for-byte (ADR D1/D3/D4). Only the `output_path` changes to a
`$SANDBOX_WORKSPACE`-relative path.

## 5. The rollout CLI & outcome

`cli/rollout.py` mirrors `rollout/__main__.py:39-57`:

```
instance_id (positional) · --dataset swebench_pro · --model sonnet
--grade · --timeout <DEFAULT_TIMEOUT_S> · --no-pull
```

- Guard `CLAUDE_CODE_OAUTH_TOKEN` present, else `parser.error`
  (`__main__.py:59-63`).
- Build spec + prompt: `compile_unit_test`'s `SandboxSpec` is reused for the
  run context; the prompt is **dataset-derived** via
  `build_solve_prompt(problem_statement, requirements=…, interface=…)`
  (SWE-Bench-Pro-specific — `prompt.py:14`, `__main__.py:69-73`; the harness is
  agnostic to it, task 06 §5.6). The composition stages it as `prompt.txt`.
- `backend = DockerHostBackend(network=True, pull=not args.no_pull,
  pass_env=[OAUTH_TOKEN_ENV])` — network on + token by-reference, exactly the
  current rollout runtime (`runner.py:129-138`); the audit-P0-1 question (token
  in the container) is **not** re-decided here.
- `outcome = run_rollout(spec, prompt=…, model=…, backend=…, workspace=…,
  timeout=…)`.
- **Outcome string + exit** (port `__main__.py:99-112`):
  - no `--grade` → `"solved_not_graded"`, exit 0.
  - `--grade` and `outcome.is_empty` → `"empty_patch"`, grade skipped
    (`{"resolved": false, "reason": "empty_patch"}`), exit 1 — **an empty patch
    is never graded as a pass** (ADR D8, ADR:51).
  - else grade via task 04: `compile_unit_test(instance,
    patch=outcome.patch)` + `run_unit_test(...)` → verdict;
    `"resolved"` if `verdict.resolved` else `"unresolved_tests_failed"`; exit
    `0 iff resolved`.
- Print the summary JSON (as `__main__.py:83-111`).

## 6. Design decisions

### 6.1 Diff-extract is a shared, harness-agnostic observer
Making extraction a `before_destroy` exec (not part of the agent script)
decouples it from the harness: any harness that edits the repo gets the same
ADR-0001 extraction for free (Codex later, or a non-agent edit). This is
exactly what the persistent container buys (spec §core model — separate execs
against one live container); the old one-shot `docker run` forced extraction
into the agent's own script.

### 6.2 Grading stays in the CLI, reusing task 04
The runner never graded (`runner.py` returns patch + flags only); grading lived
in `rollout/__main__.py:105-109` calling `evaluate`. Kept: `run_rollout`
returns the patch + trace; the **CLI** grades via task 04's
`compile_unit_test` + `run_unit_test`. So rollout and eval share one grader —
no second grading path, and the P0-2 `output_state` fix applies to graded
rollouts too.

### 6.3 Outcome carries the engine `status`
`RolloutOutcome.status` surfaces `RunStatus` (SUCCESS / SETUP_ERROR /
RUN_ERROR) so a setup failure (image pull, container up) is distinguishable
from a completed-but-empty run — richer than today's implicit
success-or-exception. The human-facing outcome string
(`solved_not_graded`/`empty_patch`/`resolved`/`unresolved_tests_failed`) is the
CLI's, layered on top.

### 6.4 D7 guards stay unimplemented
ADR-0001 D7 (gitignored-new-source, submodule, LFS silent mis-extraction) is
documented-not-built, and even its logging is unbuilt (ADR:59-71). This port
preserves that: no new guards, no behavior change vs today's extraction. Pulling
D7 in is a separate backlog item, not part of the port.

## 7. Dependencies

Tasks 02, 03, 04, 06. Reuses `core/patch.py` — no new runtime deps. New code
Google-docstring'd. `core/patch.py`'s deliberately-verbose git-diff comments
are **not** touched (owner constraint, recorded in task 01).

## 8. Open questions (need user confirmation)

1. **Where `run_rollout` lives** — a new top-level `rollout.py` module, or under
   `evaluation/`? Neither is obviously right pre-cutover; I lean a small
   top-level `rollout.py` (mirrors the CLI subcommand, sibling to `datasets/`
   post-move). Confirm.
2. **Extraction timeout** — a fixed `_EXTRACT_TIMEOUT_S` (e.g. 120 s) for the
   `before_destroy` git-diff exec, separate from the agent timeout. OK, or fold
   into one?
3. **`RolloutOutcome` grade fields** — keep grading entirely in the CLI (outcome
   holds only patch/trace/status, CLI adds the verdict), or fold an optional
   `verdict` into `RolloutOutcome` when graded? I lean CLI-only (the runner
   stays grader-free, matching today), but the summary JSON shape is the
   deciding factor — confirm.
