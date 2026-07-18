# Conventions

The working map of this repo, written from the code — stack, commands, what each
directory means, and the hazards a fresh session (human or agent) would
otherwise learn the hard way. Pairs with [`AGENTS.md`](../AGENTS.md) (agent
behavior rules) and [`README.md`](README.md) (the map — roadmap / status).

## Stack

- **Python 3.13** (`>=3.13,<3.14`), managed with **[uv](https://docs.astral.sh/uv/)**.
- **[direnv](https://direnv.net/)** auto-activates the venv (`.envrc`).
- Runtime deps are deliberately thin: `polars` (parquet), `huggingface-hub`
  (off-repo trace storage). Everything else (Docker, git, Claude Code) is invoked
  as an external process, not imported.

## Commands

```bash
uv sync                              # create .venv + install all (incl. dev) deps
direnv allow                         # auto-activate venv on cd (or: source .venv/bin/activate)
uv run pre-commit install            # install the hooks (once)

uv run pytest                        # run the test suite
uv run pre-commit run --all-files    # ruff + pyink + isort + basedpyright + uv-lock

# The three product CLIs (module entrypoints):
python -m swe_lab.pipelines.related_files <instance_id> [--model sonnet|opus] [--samples 3]
python -m swe_lab.evaluation <instance_id> --gold          # grade an instance's gold patch
python -m swe_lab.rollout <instance_id>                    # run the container agent loop
```

## Formatting & lint (enforced by pre-commit)

- **pyink** — the formatter (Google's black fork): **line length 80, 2-space
  indent, majority quotes**, `py313`. Not ruff-format (ruff's formatter is
  disabled for `.py` in `pyproject.toml`).
- **ruff** — the linter (bugbear, comprehensions, pyupgrade, simplify, …),
  `--fix`.
- **isort** — black profile, line length 80.
- **basedpyright** — type checker over `src` + `tests`.
- `experiments/` is **exempt** from the code-quality hooks (it holds exploratory
  scripts + captured artifacts, not shipped code).

## Naming (see AGENTS.md)

Strict camelCase/PascalCase for acronyms: `SweBenchProInstance`, `Http`,
`JsonParser`, `httpClient` — treat an acronym as an ordinary word. (snake_case
module names are unaffected.)

## Directory map

| Path | What it is |
| --- | --- |
| `src/swe_lab/core/` | Shared, **dataset-agnostic** infra: `datasets/` (loader + per-dataset adapter packages), `repo/` (checkout providers), `docker/` (execution), `agent/` (headless Claude Code runner + trace/binary/proxy), `patch.py`, `benchmark.py`, `paths.py`. |
| `src/swe_lab/pipelines/related_files/` | **W1** — the annotation task (pipeline, prompts, aggregator, storage, combine). |
| `src/swe_lab/evaluation/` | **W2** — the general eval CLI (apply patch → run tests → grade). |
| `src/swe_lab/rollout/` | **W2** — the container agent loop (entryscript, prompt, runner, patch extraction). |
| `experiments/` | Exploratory experiments + investigations. Each has a `README` (design/how-to-run) and, when it reaches conclusions, a `REPORT`; raw run artifacts under `runs/<variant>/`. Exempt from code hooks. See the [experiment playbook](experiments/playbook.md). |
| `outputs/` | **Committed deliverables** (annotation parquet + per-instance JSON). Large trace records are *not* here — they live off-repo on HF. |
| `datasets/` | Per-dataset READMEs + download instructions. The actual data files are **gitignored** and downloaded locally. |
| `docs/` | This map, the [workstream](workstreams/) detail, [decisions](decisions/), the [experiment playbook](experiments/playbook.md), and grounded specs (`patch-extraction.md`, `traces.md`). |
| `submodules/` | `cc-reverse-proxy` (used by the optional `--capture proxy` mode). |
| `.cache/` | **Gitignored** — cloned repos, the pinned Claude Code linux-x64 binary, batch logs. Reproducible, never committed. |
| `tests/` | pytest suite over `core` + tasks. |

## Source-of-truth rule

- **Code > provisional docs.** Where a doc and the code disagree, the code wins
  unless the doc is explicitly the spec being implemented. The patch-extraction
  decisions are settled in
  [ADR-0001](decisions/ADR-0001-patch-extraction-and-grading.md) (Accepted);
  [`docs/patch-extraction.md`](patch-extraction.md) is non-authoritative
  background research. For how patch
  extraction / diffing / grading actually behave, read `core/patch.py`,
  `rollout/`, and `core/datasets/swebench_pro/grading.py`.
- [`README.md`](README.md) is the map (roadmap + status); the
  [workstream docs](workstreams/) carry the detail.

## Hazards (learned the hard way)

- **Memory ceiling: MAXJOBS=2.** On the 16 GB dev box, ≥ 6 headless agents (or
  MAXJOBS=4 → 12 agents) swap-thrash. Streaming subprocess stdout to a file (not
  `capture_output=True`) and `killpg`-on-timeout are load-bearing — an early run
  hung for 13 h without them.
- **amd64 emulation is slow locally.** The prebuilt instance images are amd64;
  on Apple Silicon they run emulated. Real execution happens on **GitHub
  Actions** (native amd64, free minutes) — see [W2](workstreams/w2-solve-eval/).
- **Secrets never land in git.** `.envrc.local` holds the subscription
  `CLAUDE_CODE_OAUTH_TOKEN` and is **gitignored** — rotate after use. Git history
  was once scrubbed (force-pushed) of a leaked OAuth token + operator PII; don't
  reintroduce either. Trace records redact operator PII at write time.
- **`patches.py` is a stopgap.** The loader corrects 3 upstream dataset rows
  (truncated `fail_to_pass` names) **in memory**; it's a no-op on every other
  row. Retire it once the fixed parquet is published to HF. See
  [[dataset-golden-fix]].
- **`outputs/` is a deliverable, not scratch.** The annotation JSON + parquet are
  version-controlled ground truth. Dataset data files and large trace records are
  *not* in git (gitignored / on HF respectively).
- **Claude Code usage limits.** Long batch runs hit the subscription credit wall;
  the runners are built to stop cleanly on `UsageLimitError` and resume
  idempotently (skip instances whose output already exists).
