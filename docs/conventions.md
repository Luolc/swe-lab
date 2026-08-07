# Conventions

The working map of this repo, written from the code — stack, commands, what each
directory means, and the hazards a fresh session (human or agent) would
otherwise learn the hard way. Pairs with [`AGENTS.md`](../AGENTS.md) (agent
behavior rules) and [`README.md`](README.md) (the map — roadmap / status).

## Stack

- **Python 3.13** (`>=3.13,<3.14`), managed with **[uv](https://docs.astral.sh/uv/)**.
- **[direnv](https://direnv.net/)** auto-activates the venv (`.envrc`).
- Runtime deps are deliberately thin: `polars` (parquet), `huggingface-hub`
  (off-repo trace storage), `etils` (its `epath` filesystem-path API — see
  Style). Everything else (Docker, git, Claude Code) is invoked as an external
  process, not imported.

## Commands

```bash
uv sync                              # create .venv + install all (incl. dev) deps
direnv allow                         # auto-activate venv on cd (or: source .venv/bin/activate)
uv run pre-commit install            # install the hooks (once)

uv run pytest                        # run the test suite
uv run pre-commit run --all-files    # ruff + pyink + isort + basedpyright + uv-lock

# The engine CLI — one entry point, per-subcommand modules (cli/<name>.py).
# `run` takes a REGISTERED WORKFLOW and an instance; any field of it is
# adjustable for that invocation by naming its path.
python -m swe_lab run --list                               # what can be run
python -m swe_lab run gold_unit_test <instance_id>         # grade an instance's gold patch
python -m swe_lab run rollout <instance_id>                # run the container agent loop
python -m swe_lab run rollout_and_unit_test <instance_id> \
    --rollout.harness.model opus --unit_test.retries 2     # …solved, graded, adjusted
python -m swe_lab run unit_test <instance_id> --input ./candidate.diff   # grade a patch you have
python -m swe_lab.datasets.swebench_pro.verify --shard i/N                       # golden-sweep one shard
# W1 annotation keeps its own module entrypoint:
python -m swe_lab.pipelines.related_files <instance_id> [--model sonnet|opus] [--samples 3]
```

## Releasing

**"Release" means both** (a) a **GitHub tag + Release** and (b) a **PyPI
publish** — they always go together; one without the other is not a release.
Publishing is automated: [`publish.yml`](../.github/workflows/publish.yml) fires
when a GitHub **Release is published** and pushes to PyPI via **Trusted
Publishing** (OIDC — no stored token; the one-time trusted-publisher link is
already set up). The tag's version **must** match `project.version` in
`pyproject.toml` (the single source of the version — there is no `__version__`).

Steps (the agent drives all of it):

1. Bump `project.version` in `pyproject.toml`; land it on `main` via the normal
   PR flow (CI green).
2. `gh release create vX.Y.Z --generate-notes` on the merged commit — this makes
   the tag **and** the Release, which triggers `publish.yml`.
3. Watch the run (`gh run watch`) and confirm the new version appears at
   <https://pypi.org/p/swe-lab>.

A PyPI version is **immutable** — never reuse a number; a bad publish needs a new
patch version.

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

## Style — Google Python Style Guide (decided 2026-07-18)

The repo follows the
[Google Python Style Guide](https://google.github.io/styleguide/pyguide.html)
with the following repo-wide choices and deviations (full plan + rationale:
[horizontal task 01](horizontal/plans/task-01-google-style-readability.md)):

- **Docstrings: Google format, imperative mood** ("Fetch rows…", not "Fetches
  rows…"), `Args:`/`Returns:`/`Raises:`/`Attributes:` sections with **2-space**
  hanging indent; sections may be omitted when the one-line summary genuinely
  suffices (§3.8.3); prose held to **80 cols** (W505). `@property` docstrings
  are noun phrases. Types live in annotations only — never repeated in
  docstrings (pydoclint runs with type checks off; basedpyright owns types).
- **Deviations from the public guide:** 2-space indentation (Google-internal
  style, via pyink); §2.2 *import-modules-not-symbols* is **waived entirely**
  (symbol imports are fine).
- **TODO format (§3.12) is deliberately not adopted**: the guide's
  issue-link-based form presumes an issue tracker and this repo doesn't use
  GitHub issues (short-term: won't). Revisit if issues are ever adopted.
- **Tests** are exempt from docstring-presence rules (D1xx, per §3.8.2.1);
  format rules still apply.
- Enforced by ruff (`D` google convention + `D401` + `D417`, `N`, `C90`,
  `W505`) and **pydoclint** (Args ↔ signature consistency) in pre-commit + CI.
- **Modern typing syntax (PEP 695):** type aliases use the `type` statement
  (`type Mounts = dict[str, Mount]`, never bare `Mounts = …` or
  `TypeAlias`); generics use the bracket form
  (`class Grader[V: Verdict](ABC)`), not `TypeVar` boilerplate.
- **Filesystem paths use `etils.epath`, not `pathlib` — loose in, strict out.**
  Annotate a path-taking **parameter** as `epath.PathLike` (accepts `str` /
  `os.PathLike` / an `epath.Path`), and a **return** — or a dataclass field /
  stored attribute holding a path — as the concrete `epath.Path`. Build paths
  with `epath.Path(...)`. Because `PathLike` includes `str`, **coerce a
  `PathLike` argument to `epath.Path` before doing path ops on it or storing it**
  (`p = epath.Path(p)`); a value merely *passed through* to another
  `PathLike`-accepting call needs no coercion. basedpyright enforces this — a
  path method on an un-coerced `PathLike` is a type error, so a missing coercion
  fails the type check rather than at runtime. Why `epath`: it is a drop-in for
  `pathlib.Path` that also addresses cloud URIs (`gs://`, `s3://`), keeping the
  store / cloud-backend direction open. Scope: library code under `src/`; test
  code may use pytest's `pathlib` `tmp_path` directly and coerce only where a
  comparison needs it (`epath.Path != pathlib.Path`). Known `epath` gaps — reach
  for the `os` module or `pathlib` at just that spot: no `.chmod()` (use
  `os.chmod(p, mode)`), no recursive `.rglob()` / `**` glob (walk with `pathlib`
  then wrap results), no `.cwd()` / `.home()`, and `.stat()` lacks `st_size`
  (use `os.path.getsize(p)`); pass `str(p)` to APIs typed for only
  `str | pathlib.Path` (e.g. polars I/O). Single/multi-`*` globs (`*.parquet`,
  `*/*/run.json`) do work. For copying a file or removing a tree, use `epath`'s
  own `p.copy(dst, overwrite=True)` and `p.rmtree(missing_ok=True)` (they mirror
  `shutil.copyfile` / `shutil.rmtree(ignore_errors=True)` — note `copy` refuses
  an existing target and `rmtree` a missing one *by default*), not `shutil`. A
  script that must stay standard-library-only (e.g. the in-container annotation
  validator) keeps `pathlib`. **Typer CLI
  entry-point parameters that take a path stay `pathlib.Path`** — Typer rejects a
  `Union` (and `PathLike` *is* `str | os.PathLike`), so a command option/argument
  uses the concrete `Path` and the body coerces to `epath.Path` where needed.
- **Interfaces — ABC/base class over Protocol**
  ([ADR-0002](decisions/ADR-0002-interface-style-abc-vs-protocol.md)): a
  behavior interface whose implementers live in-repo is an `abc.ABC` with
  `@abstractmethod` (implementers write `class Impl(Base)` + `@override`) — for
  navigation and instantiation-time enforcement (`Grader`, `Sandbox`,
  `RepoProvider`, and `Verdict` per
  [ADR-0006](decisions/ADR-0006-verdict-is-an-abc.md), which owns the `resolved`
  derivation rather than restating it per dataset). Use
  `typing.Protocol` **only** for a structural shape on data that records satisfy
  without inheriting, with no shared derivation to own (`RepoInstance`,
  `DatasetRecord`). Where partial override is normal, use a concrete base class
  with default methods (`SandboxObserver`).
- **Inject collaborators; don't construct them inside.** An entry function takes
  the *built* dependency (e.g. `Task.execute(sandbox: Sandbox, …)`), never a
  name + its construction knobs (`backend: str`, `workspace`, `pull`, `network`,
  …) that it then feeds to a builder. Coupling construction into the callee
  forces every new construction option down the whole call chain and forces a
  test to patch the builder instead of passing a `FakeSandbox`. The *caller* owns
  construction (`build_sandbox(...)`), so the builder can change freely without
  touching the entry point. (`output_dir` is the manager's own host concern — a
  legitimate parameter, distinct from the sandbox's internal `workspace`.)
  **One deliberate exception:** `run_task` / `Workflow.execute` take a backend
  *name* plus a `SandboxConfig`, because they build one sandbox **per attempt**
  over a fresh workspace they allocate — "a fresh sandbox each time" is a
  property of that loop, not a contract a passed-in factory could be trusted to
  honor. A caller that wants to hand over a built sandbox calls `execute`.
- **Dataclass wherever the class is field-shaped** — records are
  `frozen=True` dataclasses; even stateful classes (e.g. a manager holding
  config fields + a private state slot via
  `field(default=None, init=False, repr=False)`) prefer `@dataclass` over a
  long hand-written `__init__`. A hand-rolled `__init__` needs a reason.
- **Docstrings are self-contained.** A first-time reader gets no
  design-history context: define project shorthand where it's used (spell out
  what a backend/mode *is*, don't name-drop internal labels like "A-host"),
  and never cite planning docs by section (`task-NN §x.y` — those files move
  and renumber). Carry the conclusion inline; the only sanctioned doc pointer
  in code is a stable `ADR-NNNN` reference.

## Directory map

| Path | What it is |
| --- | --- |
| `src/swe_lab/sandbox/` | The **engine**: `SandboxManager` + lifecycle hooks, the merged lifecycle-bearing `Sandbox` (+ narrow `SandboxFs` view), `Mounts`/`Resource`, backends (`DockerHostSandbox` = A-host, `GitHubJobSandbox` = A-ghjob) selected via an open `build_sandbox` registry, shared observers (diff-extract). |
| `src/swe_lab/harnesses/` | The **harness axis**: `base.py` (the `Harness` ABC) + `claude_code/` (invocation, `convert`/`capture`, and the Claude Code runner utilities `binary`/`proxy`/`trace`/`errors`). |
| `src/swe_lab/datasets/` | The **dataset axis**: `load_dataset` + a name→record registry, plus per-dataset packages (`swebench_pro/`: record, run setup, unit-test compile + grader). |
| `src/swe_lab/evaluation/` | The **evaluation axis**: the `verdict` contract + one module per method (`unit_test`). |
| `src/swe_lab/conversation/` | The provider-neutral typed `Conversation` + the shared conversation observer. |
| `src/swe_lab/cli/` + `__main__.py` | The CLI entry point (`eval`/`rollout`/`promote`); `rollout.py` is the rollout composition. (`verify` — golden QA — moved into `datasets/swebench_pro/`, it is dataset-specific.) |
| `src/swe_lab/git/` | Everything about the task repo's **git state**, one module per concern: `patch.py` gets the agent's work *out* as a clean diff vs `base_commit` ([ADR-0001](decisions/ADR-0001-patch-extraction-and-grading.md)); `history.py` keeps the answer *out* by stripping future commits and proving it ([ADR-0010](decisions/ADR-0010-benchmark-integrity.md) §3b); `audit.py` is the agent-free task that sweeps a dataset for purge failures. `patch`/`history` are **pure** script builders — the observers that run them live in `sandbox/observers/`. |
| `src/swe_lab/repo/`, `paths.py` | Repo checkout providers (W1) + repo-root/cache path helpers. |
| `src/swe_lab/pipelines/related_files/` | **W1** — the annotation task (pipeline, prompts, aggregator, storage, combine). Keeps its own module entrypoint; not yet on the engine. |
| `experiments/` | Exploratory experiments + investigations. Each has a `README` (design/how-to-run) and, when it reaches conclusions, a `REPORT`; raw run artifacts under `runs/<variant>/`. Exempt from code hooks. See the [experiment playbook](experiments/playbook.md). |
| `outputs/` | **Committed deliverables** (annotation parquet + per-instance JSON). Large trace records are *not* here — they live off-repo on HF. |
| `datasets/` | Per-dataset READMEs + download instructions. The actual data files are **gitignored** and downloaded locally. |
| `docs/` | This map, the [workstream](workstreams/) detail, [decisions](decisions/), the [experiment playbook](experiments/playbook.md), and grounded specs (`patch-extraction.md`, `traces.md`). |
| (external) `cc-reverse-proxy` | The optional `--capture proxy` mode compiles this **standalone** Go project — not a submodule. Default: a sibling checkout `../cc-reverse-proxy/`; override with `CC_REVERSE_PROXY_SRC`. |
| `.cache/` | **Gitignored** — cloned repos, the pinned Claude Code linux-x64 binary, batch logs. Reproducible, never committed. |
| `packaging/claude-code-bundle/` | Builds the portable Claude Code tarball (agent + glibc + loader + `rg`) that runs on musl/Alpine, ancient glibc and distroless. `build.sh` resolves + pins the version, `Dockerfile.bundle` is the hermetic builder, `smoke-test.sh` is the target matrix. Output lands in `dist/` (**gitignored**). The artifact is **internal-use only** — private channels, never published. Design: [task 24](horizontal/plans/task-24-claude-code-portable-bundle.md). |
| `tests/` | pytest suite over the engine, axes, and tasks. |

## Source-of-truth rule

- **Code > provisional docs.** Where a doc and the code disagree, the code wins
  unless the doc is explicitly the spec being implemented. The patch-extraction
  decisions are settled in
  [ADR-0001](decisions/ADR-0001-patch-extraction-and-grading.md) (Accepted);
  [`docs/patch-extraction.md`](patch-extraction.md) is non-authoritative
  background research. For how patch
  extraction / diffing / grading actually behave, read `git/patch.py`, the
  diff-extract observer in `sandbox/observers/`, and
  `datasets/swebench_pro/unit_test.py`.
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
