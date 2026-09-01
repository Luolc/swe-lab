# SWE Lab

Tooling to **build, run, enrich, audit and fix SWE (a.k.a. coding agent) evaluation data**.

## What's in it

- **The run engine** ([`src/swe_lab/sandbox/`](src/swe_lab/sandbox/),
  [`workflow/`](src/swe_lab/workflow/)) — one sandboxed run of a coding agent
  over a benchmark instance, composed from three plug-in axes: a **harness**
  (`claude_code`, `codex`, `grok_build`), a **dataset** (SWE-Bench Pro,
  DeepSWE 1.1) and an **eval method** (unit-test grading). A *workflow* chains
  those steps — solve, grade, or both — and is run by name:

  ```bash
  python -m swe_lab run --list                                  # what can be run
  python -m swe_lab run rollout_and_unit_test <instance_id>     # solve, then grade
  ```

- **Benchmark integrity** ([`src/swe_lab/git/`](src/swe_lab/git/),
  [`integrity/`](src/swe_lab/integrity/)) — the task repo's future is stripped
  out of the container before the agent starts (and the strip is proved), and
  each run is swept for the ways an agent can reach the answer anyway.
  Detection, never a gate —
  [ADR-0010](docs/decisions/ADR-0010-benchmark-integrity.md).

- **Related-files annotation**
  ([`src/swe_lab/pipelines/related_files/`](src/swe_lab/pipelines/related_files/README.md))
  — for each task instance, a ground-truth list of the code snippets a model
  needs to read to solve it. **Shipped**: 731/731 SWE-Bench Pro instances
  annotated & QA'd.

- **Quality auditing** *(planned)* — flag "skewed" eval examples that no longer
  measure real capability (ambiguous specs vs. overly-specific tests, broken
  environments, contamination, brittle graders), in the spirit of OpenAI's
  [*Separating signal from noise in coding evaluations*](https://openai.com/index/separating-signal-from-noise-coding-evaluations/).
  Not started; the nearest shipped work is the benchmark-integrity detection
  above.

The overall roadmap and status live in [`docs/README.md`](docs/README.md); the
codebase map, commands and hazards in
[`docs/conventions.md`](docs/conventions.md).

## Setup

### Prerequisites

- [uv](https://docs.astral.sh/uv/) for environment and dependency management
- [direnv](https://direnv.net/) for auto-activating the environment
- Python 3.13 (uv will install it automatically if missing)

### 1. Clone

```bash
git clone https://github.com/Luolc/swe-lab.git
cd swe-lab
```

Optional — the `--capture proxy` mode compiles the standalone
[`cc-reverse-proxy`](https://github.com/Luolc/cc-reverse-proxy) Go project. It is
**not** a submodule: by default it is looked up as a sibling checkout next to
this repo (`../cc-reverse-proxy/reverse_proxy.go`); clone it there, or point
`CC_REVERSE_PROXY_SRC` at its `reverse_proxy.go`. The default `stream` capture
needs none of this.

### 2. Set up the environment

```bash
uv sync          # create .venv and install all (incl. dev) dependencies
direnv allow     # auto-activate the venv on cd (uses .envrc)
```

If you don't use direnv, activate manually with `source .venv/bin/activate`.

Install the pre-commit hooks (the full set is listed in
[`docs/conventions.md`](docs/conventions.md#formatting--lint-enforced-by-pre-commit)):

```bash
uv run pre-commit install
```

### 3. Download the datasets

Dataset data files are gitignored and must be downloaded locally. See
[`datasets/README.md`](datasets/README.md) for the list of available datasets
and per-dataset download instructions.

## [Disclaimer](DISCLAIMER.md)

This is a personal project and is not affiliated with any company. The content does not reflect any specific company's projects, products or internal work.
