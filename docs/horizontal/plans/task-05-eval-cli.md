# Task 05 — Eval CLI on the engine + parity

> **Design record** (point-in-time; may predate the landed code). **Status is
> tracked only in [`plans/README.md`](README.md)**; where this doc and the code
> disagree, the **code wins**.

---

## 1. Purpose & scope

Stand up the **single CLI entry point** (`python -m swe_lab <subcommand>`) with
its first subcommand, `eval`, running one instance's grade as an engine
composition (task 04). Prove **verdict parity** with the legacy grader on real
instances — the payload for **CP1**. The old `evaluation/` package stays intact
and untouched until cutover (10b); this task adds the new path beside it.

### In scope

- `swe_lab/__main__.py`: a **table-only** dispatcher (`eval` now; `rollout`,
  `verify` land in later tasks).
- `swe_lab/cli/eval.py`: the `eval` subcommand — mirror the legacy arg surface,
  run through `compile_unit_test` + `run_unit_test` + `DockerHostBackend`.
  **Built with a CLI library (Typer), not hand-rolled argparse** — see §6.1.
- A new dedicated **parity workflow** (`eval-parity.yml`, `workflow_dispatch`)
  that runs the legacy grader and the engine grader on the same instances and
  diffs their verdicts. (The old `eval.yml` — a misnamed single-instance gold
  grade, redundant with `verify-golden.yml` — was **removed** 2026-07-22; the
  parity job is the real per-instance eval-in-CI.)
- Fast, Docker-free unit tests for the dispatcher + CLI wiring.

### Out of scope

- Porting `verify.py`'s **two-run base+golden sweep** (`classify`, sharding,
  aggregate) — that is 10b (`cli/verify.py`). Task 05's parity is the
  **single-grade** path (`evaluate` vs `run_unit_test`).
- `rollout` subcommand (task 07); deleting `evaluation/` (10b).
- Any `output_state`/GOLDEN_FAIL reclassification in the sweep — the verdict
  *carries* the state (task 04); the sweep consumes it at 10b.

## 2. Module layout

Built on **Typer** (a CLI library, not hand-rolled argparse — see §6.1). One
`Typer` app; each subcommand is a decorated typed function in its own module.

```
swe_lab/
  __main__.py    from .cli import app; app()   # `python -m swe_lab <sub>`
  cli/
    __init__.py  app = typer.Typer(); app.command()(eval_cmd); …
    eval.py      the `eval` subcommand (typed function + docstring)
```

Tests: `tests/test_cli_eval.py` (`typer.testing.CliRunner`, engine mocked).

## 3. The CLI

```python
# ─── swe_lab/cli/eval.py ────────────────────────────────────────────────────
def eval_cmd(
    instance_id: str,
    dataset: str = "swebench_pro",
    gold: Annotated[bool, typer.Option(help="Grade the instance's own gold patch")] = False,
    patch_file: Annotated[Path | None, typer.Option(help="A candidate .diff")] = None,
    timeout: float = 1800.0,
    network: bool = True,     # --network / --no-network
    pull: bool = True,        # --pull / --no-pull
) -> None:
  """Grade one instance by running its tests in a container."""
  # Typer derives the CLI from the signature; the docstring is the --help text.
  # exactly one of gold/patch_file (raise typer.BadParameter otherwise);
  # exit code via typer.Exit(0 if verdict.resolved else 1).

# ─── swe_lab/cli/__init__.py ────────────────────────────────────────────────
app = typer.Typer(add_completion=False, no_args_is_help=True)
app.command("eval")(eval_cmd)
# app.command("rollout")(rollout_cmd)  # task 07
# app.command("verify")(verify_cmd)    # 10b
```

Typer infers the surface from the typed signature (positional `instance_id`;
`--dataset`, `--timeout`; bool → `--gold/--no-gold`, `--network/--no-network`,
`--pull/--no-pull`), and uses the docstring for `--help`. The legacy arg surface
(`evaluation/__main__.py:32-45`) maps over one-to-one; `--patch-file` stays an
option, `--gold` a flag. Exit `0 iff resolved` (`:64-65`).
```

The legacy surface (`evaluation/__main__.py:32-45`) is reproduced verbatim plus
`--no-pull` (the engine backend pulls by default; the legacy CLI always pulled
via `evaluate`). Behavior parity: `--gold` → `instance.patch`; `--patch-file` →
file text; print the verdict as JSON; **exit `0` iff resolved**
(`evaluation/__main__.py:64-65`).

## 4. The eval composition

`cli/eval.py` maps the legacy `evaluate(...)` call
(`evaluation/__main__.py:61-63`) onto task 04:

```python
instance = load_dataset(args.dataset).require(args.instance_id)
# type-guard SweBenchProInstance (as evaluation/__main__.py:49-50)
sandbox_spec, unit_spec = compile_unit_test(instance, patch=patch)
backend = DockerHostBackend(network=not args.no_network, pull=not args.no_pull)
workspace = cache_root(root) / "eval_workspaces" / instance.instance_id
_, verdict = run_unit_test(
    sandbox_spec, unit_spec, backend=backend, workspace=workspace,
    timeout=args.timeout,
)
print(json.dumps({... verdict fields ...}, indent=2))
return 0 if verdict and verdict.resolved else 1
```

Mapping table (legacy → engine):

| Legacy | Engine |
|---|---|
| `SweBenchProAdapter().eval_spec(instance)` → `EvalSpec` (`evaluation/__main__.py:51`) | `compile_unit_test(instance, patch=…)` → `(SandboxSpec, UnitTestSpec)` |
| `evaluate(spec, patch=…, timeout=…, network=…)` (`:61-63`) | `run_unit_test(sandbox_spec, unit_spec, backend=DockerHostBackend(network=…), …)` |
| `EvalResult.resolved / .passed / .missing / .output_found` | `SweBenchProVerdict.score / .resolved / .passed / .missing / .output_state` |
| exit `0 if resolved else 1` (`:65`) | same, off `verdict.resolved` (`= score >= 1.0`) |

Note two verdict upgrades, neither of which changes the exit-code contract
(resolved ⇒ 0), so parity holds on healthy instances:
- **`score`** — the cross-dataset surface is now a scalar in [0, 1]
  (`verdict.score`), binary {0.0, 1.0} for unit-test grading; `resolved` is the
  derived `score >= 1.0`. The single-instance CLI prints both and exits off
  `resolved`; sweep aggregation (10b) will average `score`.
- **`output_state`** — legacy `output_found=True` collapses a corrupt
  `output.json` into "0 passed"; the engine verdict distinguishes
  `ok`/`absent`/`unparseable`. Printed for diagnosis.

## 5. Parity harness (the CP1 payload)

Verdict equivalence between the two graders on the same gold patch, in CI (real
Docker, native amd64):

- A dedicated `eval-parity.yml` (`workflow_dispatch`) runs, per instance id:
  - legacy: `python -m swe_lab.evaluation <id> --gold` → JSON with `resolved`,
    `passed`, `missing` (`evaluation/__main__.py:64`).
  - engine: `python -m swe_lab eval <id> --gold` → JSON verdict.
  - a small diff step asserts `resolved` (and thus `score`) matches **and** the
    `passed`/`missing` sets match; nonzero exit + a table on mismatch.
- Instance set: **flipt** (Go), **ansible** (Python) — both known to resolve
  (README progress table) — plus **one truncated-golden-names instance**
  (NodeBB / ansible / vuls) exercising the `patches.py` in-loader fix, so the
  parity covers the class that historically mis-graded.
- The verification artifact for the PR is that job's run link + the diff table
  (plan's CP1 verification).

Why not a `@pytest.mark.docker` parity test in the merge gate: flipt+ansible are
minutes each and run *twice* (both graders) — far too slow for `ci.yml`. The
merge gate keeps only the fast mocked wiring tests; the real parity is a manual
CI job the user triggers at CP1.

## 6. Design decisions

### 6.1 A CLI library (Typer), not hand-rolled argparse
Owner call (2026-07-22): don't hand-roll the CLI — a typed function should
become a command with the docstring as its help. **Typer** (v0.27) does exactly
this: it derives arguments/options from the function signature + type hints and
uses the docstring for `--help`; subcommands are `@app.command()`s on one
`Typer` app; `python -m swe_lab <sub>` works via `__main__.py` calling `app()`;
and `typer.testing.CliRunner` gives real CLI tests. This replaces the earlier
hand-rolled table-dispatcher sketch. **Dependency note (ask-first boundary):**
Typer adds a runtime dep subtree (it vendors Click but pulls `rich`,
`shellingham`, `pygments`, …) to a tool that today is polars + huggingface-hub
only — accepted deliberately for the CLI ergonomics + because the tool is
release-bound. `no_args_is_help=True` gives a clean top-level help; the growth
guard (each subcommand its own module) still holds.

### 6.2 Reuse task 04's compile+run; no new grading logic here
Everything grading-specific already lives in `compile_unit_test` /
`SweBenchProGrader` (task 04). The CLI is thin wiring. This is what makes the
parity meaningful — the engine path shares no code with the legacy `evaluate`,
so agreement is real cross-validation, not a tautology.

### 6.3 Legacy path stays until 10b
`evaluation/__main__.py` + `verify.py` are untouched, so the golden sweep keeps
working while the engine path is proven. The strangler holds: two independent
graders coexist until CP1 clears the new one. (The old `eval.yml` was removed
2026-07-22 — misnamed + redundant with `verify-golden.yml`; the new per-instance
eval-in-CI is `eval-parity.yml`.)

## 7. Dependencies

Tasks 02, 03, 04. **New runtime dep: `typer`** (§6.1 — the ask-first boundary;
owner-requested). New code lands Google-docstring'd.

## 8. Open questions (need user confirmation)

1. ~~Parity workflow placement~~ — **resolved**: a dedicated `eval-parity.yml`
   (the old `eval.yml` is removed).
2. ~~`eval.yml` cutover~~ — **resolved**: `eval.yml` removed; no cutover.
3. **Typer as a runtime dep** — confirm adding `typer` (+ its rich/shellingham
   subtree) to a deliberately-thin tool. Owner asked for the annotation-driven
   ergonomics; the alternative that stays thin is Click (explicit decorators, no
   type-hint inference) or stdlib argparse. My read: Typer, since you asked for
   exactly its model and the tool is release-bound.
4. **Dispatcher question is moot** — Typer owns dispatch + `--help`;
   `__main__.py` is just `app()`. The former hand-rolled table-dispatcher
   sketch is dropped.
