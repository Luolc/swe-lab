# Task 10a — Moves: `datasets/`, `paths`, `repo/` to top level

> **Status: PLANNED — pre-implementation.** Source of truth: the approved
> [spec](../spec.md) §"Project Structure (DECIDED 2026-07-18)" (flat top-level
> axis packages; `core/` dissolves). Purely mechanical, **zero behavior change**.
> Grounded in the current tree at the post-`#51` `main`. The full core-dissolve
> (relocating `agent/`, `patch.py`; deleting `docker/`, `benchmark.py`) belongs
> to [10b](task-10b-cutover.md) — 10a is only the three clean package moves.

---

## 1. Purpose & scope

Relocate three packages out of `core/` to the flat top level per the spec's
migration mapping, updating every import. No logic changes; the full suite and
W1's CLI stay green.

### In scope

- `git mv src/swe_lab/core/datasets → src/swe_lab/datasets`
- `git mv src/swe_lab/core/paths.py → src/swe_lab/paths.py`
- `git mv src/swe_lab/core/repo → src/swe_lab/repo`
- Rewrite every import site (`src/` + `tests/`).

### Out of scope (→ 10b)

- Relocating `core/agent/` and `core/patch.py` (they have new-engine consumers);
  deleting `core/docker/`, `core/benchmark.py`; emptying `core/`; deleting the
  legacy `rollout/` + `evaluation/` legacy modules. 10a leaves `core/` holding
  `agent/`, `docker/`, `patch.py`, `benchmark.py`, `__init__.py` — still valid.

## 2. The moves and why the relative imports survive

The two moved packages import `paths` **relatively** —
`core/datasets/loader.py: from ..paths import datasets_root`,
`core/repo/provider.py: from ..paths import repo_cache_dir`. Because `paths.py`
moves up **in the same step**, `..paths` still resolves (now to `swe_lab.paths`)
— no edit needed inside the moved trees. The only relative imports that **break**
are the two in modules that **stay** in `core/` while `paths` leaves:

- `core/agent/proxy.py:19` `from ..paths import …`
- `core/agent/binary.py:30` `from ..paths import …`

These become absolute `from swe_lab.paths import …`. (`core/agent/` is relocated
in 10b; until then it reaches the moved `paths` by absolute import.)

## 3. Import rewrite map

| Pattern | Replacement | Sites |
|---|---|---|
| `swe_lab.core.datasets` | `swe_lab.datasets` | 36 (incl. `swebench_pro` absolute `swe_lab.core.paths` inside `execution.py`/`grading.py`) |
| `swe_lab.core.paths` | `swe_lab.paths` | 12 |
| `swe_lab.core.repo` | `swe_lab.repo` | 5 |
| `src/swe_lab/__init__.py`: `.core.datasets`, `.core.repo` | `.datasets`, `.repo` | 2 |
| `core/agent/{proxy,binary}.py`: `from ..paths import` | `from swe_lab.paths import` | 2 |

Modules that **stay** and are imported by the moved trees keep working via
absolute imports: `swe_lab.core.benchmark` (in `execution.py`/`grading.py`) and
`swe_lab.core.docker.provider` (in `grading.py`) are untouched — they still live
under `core/` after 10a.

Note: `swe_lab.paths.datasets_root()` returns the repo-root **data** dir
`datasets/` (parquet storage), which is a different tree from the new
`src/swe_lab/datasets/` **package** — no collision.

## 4. Method

1. `git mv` the three targets (preserves history).
2. Bulk-rewrite the absolute import strings across `src/` + `tests/` (mechanical
   find-replace), then the two `__init__` relative imports and the two
   `core/agent` relative-`..paths` breakages by hand.
3. `uv run pre-commit run --all-files` — isort re-sorts the changed import
   blocks; fix any residue.
4. `uv run pytest` — full green, zero behavior diff.
5. Smoke W1: `uv run python -m swe_lab.pipelines.related_files --help`.

## 5. Acceptance

- `grep -rn "swe_lab\.core\.\(datasets\|paths\|repo\)" src tests` → empty.
- Full suite green; W1 CLI still runs; quality bar clean.
- No file content changed beyond imports (and the moved files' own location).
