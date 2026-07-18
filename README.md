# SWE Lab

Tooling to **build, run, enrich, audit and fix SWE (a.k.a. coding agent) evaluation data**.

## Tasks

- **Related-files annotation** (`src/swe_lab/pipelines/related_files/`) —
  for each task instance, produce a ground-truth list of the code snippets a
  model needs to read to solve it. **Shipped**: 100 instances annotated & QA'd.
  See [`pipelines/related_files/README.md`](src/swe_lab/pipelines/related_files/README.md).

- **Quality auditing** *(planned)* — flag "skewed" eval examples that no longer
  measure real capability (ambiguous specs vs. overly-specific tests, broken
  environments, contamination, brittle graders), in the spirit of OpenAI's
  [*Separating signal from noise in coding evaluations*](https://openai.com/index/separating-signal-from-noise-coding-evaluations/).
  Not started; it will land as a sibling under `pipelines/`.

The overall roadmap and design live in [`docs/README.md`](docs/README.md).

## Setup

### Prerequisites

- [uv](https://docs.astral.sh/uv/) for environment and dependency management
- [direnv](https://direnv.net/) for auto-activating the environment
- Python 3.13 (uv will install it automatically if missing)

### 1. Clone with submodules

```bash
git clone --recurse-submodules https://github.com/Luolc/swe-lab.git
cd swe-lab
```

If you already cloned without `--recurse-submodules`, initialize the
submodules afterwards:

```bash
git submodule update --init --recursive
```

This checks out `cc-reverse-proxy` under `submodules/`.

### 2. Set up the environment

```bash
uv sync          # create .venv and install all (incl. dev) dependencies
direnv allow     # auto-activate the venv on cd (uses .envrc)
```

If you don't use direnv, activate manually with `source .venv/bin/activate`.

Install the pre-commit hooks (ruff, pyink, isort, basedpyright, uv-lock):

```bash
uv run pre-commit install
```

### 3. Download the datasets

Dataset data files are gitignored and must be downloaded locally. See
[`datasets/README.md`](datasets/README.md) for the list of available datasets
and per-dataset download instructions.

## [Disclaimer](DISCLAIMER.md)

This is a personal project and is not affiliated with any company. The content does not reflect any specific company's projects, products or internal work.
