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
uv run pre-commit run --all-files    # the full hook set — see Formatting & lint

# The engine CLI — one entry point, per-subcommand modules (cli/<name>.py).
# `run` takes a REGISTERED WORKFLOW and an instance; any field of it is
# adjustable for that invocation by naming its path.
python -m swe_lab run --list                               # what can be run
python -m swe_lab run gold_unit_test <instance_id>         # grade an instance's gold patch
python -m swe_lab run rollout <instance_id>                # run the container agent loop
python -m swe_lab run rollout_and_unit_test <instance_id> \
    --rollout.harness.model opus --unit_test.retries 2     # …solved, graded, adjusted
python -m swe_lab run unit_test <instance_id> --input ./candidate.diff   # grade a patch you have
python -m swe_lab run git_integrity_audit <instance_id>     # agent-free: prove the purge held
python -m swe_lab.datasets.oracle_failures.build \
    --run-dir .cache/runs/rollout_and_unit_test/<instance_id>  # a finished failure → an oracle_failures row
python -m swe_lab run oracle_analysis <instance_id> --dataset oracle_failures   # phase B: the Oracle writes guidebook.md
python -m swe_lab.datasets.verify --dataset <name> --shard i/N          # golden-sweep one shard
# W1 annotation keeps its own module entrypoint:
python -m swe_lab.pipelines.related_files <instance_id> [--model sonnet|opus] [--samples 3]
```

## Secrets

Local secrets live in `.envrc.local` (gitignored; sourced by `.envrc` under
direnv), which holds **only `op://` references read at load time via
`op read`** — never a plaintext value. Copy
[`.envrc.local.example`](../.envrc.local.example) to `.envrc.local` and
`direnv allow`; the example is the complete file, one line per vault item:

| Variable | 1Password item | Consumer |
|---|---|---|
| `HF_TOKEN` | `op://dev-shared/hf-token/credential` | HF pushes (`pipelines/related_files/traces.py`, `datasets/deepswe/build_parquet.py --upload`) |
| `SWE_LAB_CLAUDE_CODE_OAUTH_TOKEN` | `op://dev-shared/claude-code-oauth-token/credential` | the `claude_code` harness (subscription auth). **Deliberately not named `CLAUDE_CODE_OAUTH_TOKEN`** in your shell — see [Hazards](#hazards-learned-the-hard-way); the CLI copies it to that name inside its own process |
| `OPENROUTER_API_KEYS` | `op://dev-shared/openrouter-api-keys/credential` | comma-separated OpenRouter keys, **split inside the consuming program, never in a shell** — `experiments/trace_synthesis/steered_rerun/supervisor.py` (`key_pool`), `experiments/trace_synthesis/process_supervision/guidebook_as_step_criterion/judge_steps.py` |

`op read` needs `OP_SERVICE_ACCOUNT_TOKEN` in the environment. On the
workstation an interactive zsh (so every herdr pane) gets it from `~/.zshrc`; a
`bash -lc` or non-interactive `ssh` shell does **not**, so there source the
token file first (`set -a; . /etc/machine-setup/op-machine.env; set +a`) or
direnv's `op read` fails. The example guards on `OP_SERVICE_ACCOUNT_TOKEN` and
`return`s (with a stderr hint) before any `op read` when it's absent, so a
token-less shell gets no variables instead of `op` hanging on an interactive
"add an account?" prompt; each `op read --no-newline --force …` is a second,
non-interactive line of defense. swe-lab itself spawns no such shell: sandboxes
receive the already-resolved values by name through `pass_env`. The vault, the
read-only service account, the on-disk token file, and the item conventions are
**owned by machine-setup** — read, don't restate:
[ADR-0013](https://github.com/Luolc/machine-setup/blob/main/docs/adr/0013-workstation-secrets-via-service-account.md)
(the decision) and the
[workstation secrets handbook](https://github.com/Luolc/machine-setup/blob/main/docs/knowledge/workstation-secrets-setup.md)
(setup, acceptance, rotation).

Not configured (no vault item yet; set them yourself if you need them):
`OPENAI_API_KEY` (the `codex` harness), `XAI_API_KEY` (the `grok_build`
harness), and `ANTHROPIC_API_KEY` (only the `claude_code` harness's `--bare`
mode reads it).

GitHub Actions is unchanged: the rollout workflows
([`.github/workflows/rollout*.yml`](../.github/workflows/)) read the repository
secret `secrets.CLAUDE_CODE_OAUTH_TOKEN`, not 1Password, and set it in the job
under its own name — a CI job has no direnv and no interactive `claude`, so the
hazard below does not exist there and the shim has nothing to do.

## Releasing

**"Release" means both** (a) a **GitHub tag + Release** and (b) a **PyPI
publish** — they always go together; one without the other is not a release.
Publishing is automated: [`publish.yml`](../.github/workflows/publish.yml) fires
when a GitHub **Release is published** and pushes to PyPI via **Trusted
Publishing** (OIDC — no stored token; the one-time trusted-publisher link is
already set up). The tag's version **must** match `project.version` in
`pyproject.toml` (the single source of the version — there is no `__version__`).

Steps (the agent drives all of it):

1. Finish [`docs/releases/vX.Y.Z.md`](releases/) — what a consumer has to react
   to, and the migration for it. **Before** the bump lands, not after tagging:
   written from memory afterwards, the migration steps are the part that goes
   wrong. A release with nothing to react to still gets a note saying so.
2. Bump `project.version` in `pyproject.toml`; land it on `main` via the normal
   PR flow (CI green) — the note can ride the same PR.
3. `gh release create vX.Y.Z --generate-notes` on the merged commit — this makes
   the tag **and** the Release, which triggers `publish.yml`. The generated
   notes are the exhaustive PR list; the in-repo note is the curated one, so
   link to it from the Release body rather than restating either.
4. Watch the run (`gh run watch`) and confirm the new version appears at
   <https://pypi.org/p/swe-lab>.

A PyPI version is **immutable** — never reuse a number; a bad publish needs a new
patch version.

## Formatting & lint (enforced by pre-commit)

The full hook set. `.pre-commit-config.yaml` is the source of truth; this is
the one prose copy of it (`AGENTS.md` links here rather than restating it):

- **gitleaks** — credential scan, deliberately **first** so a leak is caught
  before any other hook rewrites the staged files. It scans the **staged diff**
  only; the full-history scan is CI's step of the same name, and the two input
  domains are complementary (see the comments in both files). `--redact` is
  spelled out in `args` even though the upstream entry already passes it —
  inherited it would be invisible here and a `rev` bump could drop it, and an
  unredacted scanner prints the credential it finds into the terminal and the
  session transcript. Findings are never silenced with a path or rule
  exclusion; `.gitleaksignore` holds immutable fingerprints only, under the rule
  in its header.
- **pyink** — the formatter (Google's black fork): **line length 80, 2-space
  indent, majority quotes**, `py313`. Not ruff-format (ruff's formatter is
  disabled for `.py` in `pyproject.toml`).
- **ruff** — the linter (bugbear, comprehensions, pyupgrade, simplify, …),
  `--fix`.
- **isort** — black profile, line length 80.
- **basedpyright** — type checker over `src` + `tests`.
- **pydoclint** — docstring `Args:`/`Returns:`/`Raises:` must match the
  signature; docstring *types* are deliberately unchecked (see Style).
- **uv-lock** — regenerates `uv.lock` when `pyproject.toml` changes.
- **no-stale-module-refs** (local pygrep) — fails if a deleted or renamed
  module/symbol reappears under `src/` or `tests/`; add a token to it
  whenever you remove one. `docs/` is exempt on purpose (point-in-time
  records are supposed to name retired code).

`experiments/` is **exempt** from the code-quality hooks (it holds exploratory
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
- **A regression test's docstring states the standing hazard, in the present
  tense — not the incident.** Without it a reader takes the test for redundant
  and deletes it, so the *reason* has to survive; what does not is the
  chronicle. Keep: what about this code makes the failure **silent**, and why an
  ordinary green run says nothing about it. Drop, to the PR and git log: what
  the old implementation looked like, which error it raised, that the suite was
  green at the time, and how the review went. An incident may be **named as a
  subordinate clause** when it carries what the hazard sentence cannot — a
  pointer to the full case (`test_steered_rerun_driver.py` cites #264 this way).
  The discriminant: does the reader need this to **avoid repeating** the
  failure, or to know **how we got here**?
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
| `src/swe_lab/sandbox/` | The **engine**: `SandboxManager` + lifecycle hooks, the merged lifecycle-bearing `Sandbox` (+ narrow `SandboxFs` view), `Mounts`/`Resource`, backends (`DockerHostSandbox` = A-host, `GitHubJobSandbox` = A-ghjob) selected via an open `build_sandbox` registry, and the shared observers (`diff_extract`, `git_history_purge`, `result_verify`). |
| `src/swe_lab/harnesses/` | The **harness axis**: `base.py` (the `Harness` ABC) + `registry.py`, then one package per agent — `claude_code/` (invocation, `convert`/`capture`, and the runner utilities `binary`/`proxy`/`errors` — `proxy` *builds* the in-sandbox capture proxy, it no longer runs one), `codex/`, `grok_build/`. |
| `src/swe_lab/datasets/` | The **dataset axis**: `load_dataset` + a name→record registry, plus one package per dataset (`swebench_pro/`, `deepswe/`: record, run setup, unit-test compile + grader; `oracle_failures/`: a cached failure of another dataset's instance, delegating the runnable surface to it and staging the failure through `mounts()`, with `build.py` making rows from finished runs). `verify.py` is the dataset-agnostic golden sweep (`--dataset <name>`). |
| `src/swe_lab/evaluation/` | The **evaluation axis**: the `verdict` contract + one module per method (`unit_test`). |
| `src/swe_lab/workflow/` | The **task layer** above the engine ([ADR-0007](decisions/ADR-0007-task-and-workflow-layer.md)): `task.py` (the generic `Task` — one sandbox, three hooks, one `execute`), `workflow.py` (chains tasks by matching output to input store name), `registry.py` + `definitions.py` (the workflows `run` can name: `rollout`, `unit_test`, `rollout_and_unit_test`, `gold_unit_test`, `git_integrity_audit`, `oracle_analysis`), `run_task.py` (executes one and writes its record). |
| `src/swe_lab/trace_synthesis/` | The **trace-synthesis component** ([docs/trace-synthesis/](trace-synthesis/)): `oracle.py` is phase B (`OracleAnalysisTask` — the Oracle writes `guidebook.md` for a cached failure, git-history purge off), `guidebook.py` its schema check, `sample.py` the names a failure is staged under. |
| `src/swe_lab/rollout.py` | The **rollout composition** (`CodingAgentTask`): a harness solves the bound instance under the shared observers, with optional proxy capture. Backend-, dataset- and harness-agnostic. |
| `src/swe_lab/conversation/` | The provider-neutral typed `Conversation` + the shared conversation observer. |
| `src/swe_lab/cli/` + `__main__.py` | The CLI entry point: one Typer app, one module per subcommand — `run` (any registered workflow, with `--<field>` overrides parsed by `overrides.py`) and `promote`. `host_env.py` hands the repo-scoped OAuth token back to the name a run reads (see [Hazards](#hazards-learned-the-hard-way)). Golden QA is not a subcommand: it is `python -m swe_lab.datasets.verify`. |
| `src/swe_lab/git/` | Everything about the task repo's **git state**, one module per concern: `patch.py` gets the agent's work *out* as a clean diff vs `base_commit` ([ADR-0001](decisions/ADR-0001-patch-extraction-and-grading.md)); `history.py` keeps the answer *out* by stripping future commits and proving it ([ADR-0010](decisions/ADR-0010-benchmark-integrity.md) §3b); `audit.py` is the agent-free task that sweeps a dataset for purge failures. `patch`/`history` are **pure** script builders — the observers that run them live in `sandbox/observers/`. |
| `src/swe_lab/integrity/` | **Benchmark-integrity detection** (ADR-0010 §3c/§6): `rules.py` is the pure rule core — patch rules, trace rules (an allowlist, after SWE-bench's own detector) and the audit of our own purge; `replay.py` re-runs them over a stored run. Each rule's false-positive rate is measured against the 731 gold patches and pinned as a test. **Detection, never a gate**; the observer that drives it in-flight is in `sandbox/observers/`. |
| `src/swe_lab/repo/`, `paths.py` | Repo checkout providers (W1) + repo-root/cache path helpers. |
| `src/swe_lab/pipelines/related_files/` | **W1** — the annotation task (pipeline, prompts, aggregator, storage, combine, and `host_proxy` — the one place a proxy still runs host-side, because this pipeline's agent does too). Keeps its own module entrypoint; not yet on the engine. |
| `experiments/` | Exploratory experiments + investigations. Each has a `README` (design/how-to-run) and, when it reaches conclusions, a `REPORT`; raw run artifacts under `runs/<variant>/`. Exempt from code hooks. See the [experiment playbook](experiments/playbook.md). |
| `outputs/` | **Committed deliverables** (annotation parquet + per-instance JSON). Large trace records are *not* here — they live off-repo on HF. |
| `datasets/` | Per-dataset READMEs + download instructions. The actual data files are **gitignored** and downloaded locally. |
| `docs/` | This map, the [workstream](workstreams/) detail, [decisions](decisions/), the [experiment playbook](experiments/playbook.md), and grounded specs (`patch-extraction.md`, `traces.md`). |
| (external) `cc-reverse-proxy` | The optional `--capture proxy` mode cross-compiles this **standalone** Go project to a static linux/amd64 binary and mounts it into the sandbox, which runs it on loopback ([ADR-0012](decisions/ADR-0012-in-sandbox-capture-proxy.md)) — not a submodule, and never a host process on this path. Needs a Go toolchain at first use. Default: a sibling checkout `../cc-reverse-proxy/`; override with `CC_REVERSE_PROXY_SRC`. |
| `.cache/` | **Gitignored** — cloned repos, the pinned Claude Code linux-x64 binary, batch logs. Reproducible, never committed. |
| `packaging/claude-code-bundle/` | Builds the portable Claude Code tarball (agent + glibc + loader + `rg`) that runs on musl/Alpine, ancient glibc and distroless. `build.sh` resolves + pins the version, `Dockerfile.bundle` is the hermetic builder, `smoke-test.sh` is the target matrix. Output lands in `dist/` (**gitignored**). The artifact is **internal-use only** — private channels, never published. Design: [task 24](horizontal/plans/task-24-claude-code-portable-bundle.md). |
| `tests/` | pytest suite over the engine, axes, and tasks. |

## What may be committed as evidence

`AGENTS.md` says never commit "dataset data files or large trace records
(gitignored / off-repo on HF by design)". That sentence names a mechanism but
no boundary, and in [#304](https://github.com/Luolc/swe-lab/pull/304) two
competent readers read two different answers out of it — a P0 raised on a
329 KB experiment directory (260 KB of it evidence), rebutted with what `main`
already carries, and withdrawn a round later. Neither reading was careless.
**The defect was the missing definition**, so here it is.

**The test is not the byte count. It is two questions, asked in this order:**

1. **Is this a product of *dataset scale* — one artifact per instance, growing
   with the dataset?** Then it is off-repo by design: gitignored locally,
   published on HF. The 731-instance sweeps, rollout trees, raw proxy logs of
   batch runs. Scale is the property that makes them unmanageable in git, and
   it is a property of the *pipeline*, not of any one file's size.
2. **Is this the minimum a reader needs to rederive the report's conclusions
   without leaving the repository?** Then it belongs in git, and its size is
   not by itself an objection. An experiment whose numbers can only be checked
   by re-running it on the author's machine has not reported a result; it has
   asserted one.

**Question 1 is asked first and it wins.** The two are not alternatives to
weigh: an artifact that is dataset-scale stays off-repo *even when a claim
needs all of it*, because the alternative is the dataset in git. What question
2 then buys is not an exemption but an obligation — commit a **witness**: the
derived numbers the claim actually rests on, provenance identifying the corpus
they came from (path or HF id, digest, row count), and the command that
regenerates them where the corpus exists. So the two categories are disjoint by
construction: **the corpus is off-repo, the witness is in-repo**, and no
artifact is ever both.

This is what the repo already does, in three places at three scales:

- `test_the_rule_set_stays_clean_on_the_gold_corpus` measures each integrity
  rule's false-positive rate over all 731 gold patches. The parquet is
  gitignored and CI does not download it, so the test **skips** when it is
  absent — and the numbers it would produce are pinned in the test as
  `_GOLD_FALSE_POSITIVE_BUDGET`. The corpus stayed out; the claim stayed
  checkable.
- `outputs/` commits the annotation parquet and per-instance JSON while the
  traces they were derived from live on HF.
- An experiment commits a field-reduced snapshot of an off-repo run ledger
  beside its `runs/`, with a provenance file recording the source path, its
  sha256 and which fields were dropped — the fix a
  [#306](https://github.com/Luolc/swe-lab/pull/306) review finding required
  when the report's cost figures turned out to depend on a ledger only one
  machine had.

**If no witness can carry the claim, downgrade the claim** — say plainly that
the number is rederivable only with the corpus in hand, and name the command —
rather than committing the corpus to make the sentence true.

Question 2 is not a preference — it is [the experiment
playbook](experiments/playbook.md)'s "raw artifacts, preserved" and "ground
every claim in raw data", and reviews enforce it, as that third example shows.
Which is why the #304 reading collided with it: pushing an experiment's own
evidence out of the repo satisfies one rule by breaking the other. **When two
rules appear to forbid each other, that is the signal one of them is being read
wrong** — here, "large trace records" was being read as "many bytes" when it
means "the dataset-scale corpus that HF hosts".

**Calibration, not a threshold.** These are the accepted magnitudes at one
pinned commit, recorded so a future argument starts from what the repo already
agreed to rather than from a number someone picks in the moment. **`main` moves
and these numbers move with it** — `process_supervision` gained four files
between this table being written and being reviewed — so the tree is named,
not the branch:

```sh
git ls-tree -r -l c1fd9e9 | awk '{split($5, a, "/")
    key = (a[1] == "experiments") ? a[1]"/"a[2]"/"a[3] : a[1]
    bytes[key] += $4; files[key]++}
  END {for (k in bytes) printf "%10d %5d  %s\n", bytes[k], files[k], k}' | sort -rn
```

| Committed corpus, at `c1fd9e9` | Size |
| --- | --- |
| `experiments/trace_synthesis/injection_shape/` | 8,336,053 bytes / 379 files — the largest, reviewed and merged |
| `experiments/trace_synthesis/process_supervision/` | 2,187,200 bytes / 60 files |
| `experiments/related_files/prompt_variance/` | 443,275 bytes / 88 files (a playbook exemplar) |
| `outputs/` (the committed deliverable) | 17,559,873 bytes / 3,662 files |

Nothing depends on these being current — they are evidence of what review has
accepted, not a budget anyone spends against, so a stale row misleads only if
it is read as a limit. Re-measure at a newer commit and say which.

The directory the argument was *about* is deliberately not in that table:
`streamjson_input/` measured 329 KB on an unmerged branch, and #304 is still
open. It is what prompted the definition, not a precedent for it — a magnitude
is accepted when it is on `main`, not when it has been argued for.

**No byte limit is set on purpose.** A limit would have to be either low enough
to evict `injection_shape` — an accepted corpus whose loss would cost more than
it saves — or high enough to permit anything under it, including a corpus that
fails question 1 and belongs on HF. Size correlates with the thing we care
about; it is not the thing.

**Prune by question 2, not by megabytes.** The right response to a large
evidence tree is to ask which files the report actually cites and reduce to
those (a field-reduced snapshot, a normalized evidence record) — not to delete
a small one because it *looks* big next to a source file. `runs/` is
append-only per the playbook: new variant, new directory, never an overwrite.

**None of this loosens what is absolute.** Independent of size or usefulness,
and enforced elsewhere rather than judged here: **no secrets** (the gitleaks
hook and the CI history scan — see [Quality bar](../AGENTS.md#quality-bar)) and
**no operator PII** in any committed record — home paths, names, emails,
account or organization identifiers. The other #304 P0, which was *not*
withdrawn, was exactly this: raw transcript snapshots carrying an operator home
path. A capture that must be redacted before it can be committed is redacted
first and verified after ([the redaction
module](../src/swe_lab/harnesses/claude_code/redaction.py) is the one home for
that rule).

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
- **A section whose body is history gets a framing note, not per-sentence
  edits.** When a new decision outdates a section that records measurements or
  past reasoning, add **one note at its head** saying how to read the rest, and
  rewrite only the sentences that make *current* claims. The reason is
  checkability: a reader can verify a single stated rule, but has no way to see
  what a string of per-sentence judgements missed. (First applied by
  [ADR-0013](decisions/ADR-0013-supervision-on-the-stdin-channel.md) over
  `trace-synthesis/spec.md` §10–§11.)

## Writing about downstream use

This repository is public. Where a doc, ADR, plan or commit message needs to
refer to how the library is used outside it, write the **general** case — "a
downstream consumer runs the sweep on its own infrastructure", "an internal
sandbox subclasses `Sandbox`" — rather than attributing a specific
configuration, scale or number to a specific user. Recording what broke and
what was reported is ordinary engineering history and stays; the rule is about
*whose* setup a passage describes. Text already in the repo is not swept
retroactively (owner's calibration, 2026-09-01).

## Hazards (learned the hard way)

- **The local suite and CI have different jurisdictions.** Two halves, and both
  are load-bearing when several agents share this box.

  *What to run.* The default local gate is `uv run pytest -m 'not docker'`. The
  docker-marked tests start containers of their own, which collides head-on with
  the one-container-at-a-time rule the moment two agents work in parallel:
  2026-09-01 had two leaked producer containers (`pytest-309`, `pytest-323`)
  live at once and a third agent's `test_live_two_container_chain` failing for
  want of a sandbox — while that same test was green three runs running on CI.
  So **the docker-marked tests are CI's job**; "docker tests green before merge"
  is unchanged, but CI is the required check that guarantees it. *Exception:* a
  change touching sandbox, container or harness-capture paths runs them locally
  too — **serially**, with `docker ps -q | wc -l` equal to 0 before starting and
  a check for leftovers after. Fast feedback is worth the interference there and
  nowhere else.

  *How to read a local failure.* Machine state does move the local suite — the
  same `uv run pytest` measured **220 s** while the host was CPU-throttled and
  **24.5 s** once it recovered. But the mechanical criteria for "environment,
  not result" (`claude_code.timed_out == 1`, or wall far past the p90 of
  comparable runs) are defined for **rollouts**, and neither quantity exists for
  a local test run — so that rule is **inapplicable here, not unmet**. Asking
  "were its criteria met?" quietly pulls an out-of-scope case into a rule's
  jurisdiction, where it is either misjudged or the rule gets stretched to
  swallow it. Instead: **re-run the affected test in isolation and take the
  required CI check as the verdict.** Isolated pass + green CI → proceed, and
  say in the PR description that the full local suite did not stabilize and
  where. Isolated failure → a real failure, independent of machine state. Never
  skip the isolated re-run because the box is busy: it costs one test, and
  skipping it is what turns "the machine was loaded" into an all-purpose excuse.
- **`gh pr merge --delete-branch` run from a worktree fails *after* the merge
  has already succeeded.** The merge lands; `gh` then tries to check the local
  repo back out onto `main`, which the primary checkout already holds, and exits
  non-zero with `fatal: 'main' is already used by worktree at …`. Seen
  2026-09-01 merging #295. **Do not retry it as a failed merge** — the retry
  fails differently (the PR is already merged, the branch already gone), and
  that second error reads like confirmation that the first attempt did nothing.
  Take three readings instead: `gh pr view <n> --json state` is `MERGED`, a
  `mergeCommit` exists, and the remote branch is gone.

  Then delete the local branch — **but not from the worktree that still has it
  checked out**, which is where you are standing, since `gh`'s checkout of
  `main` is exactly what failed. Git refuses:
  `error: cannot delete branch 'topic' used by worktree at …` (verified in a
  disposable repo, `rc=1`). Either move that worktree off the branch first
  (check out the next branch, or `git checkout --detach`) or
  `git worktree remove` it; both then delete cleanly (`rc=0`). For a long-lived
  worktree the first is the one you want.

  The shape outlives the command: **an error names the step that raised it,
  which can be later than the step you care about — so it is silent about
  whether the earlier step succeeded**, while a reader takes a non-zero exit as
  a verdict on the whole command. It is the mirror of a green light having
  several possible causes: a red one does too, and this red light was not about
  merging at all.
- **A check that guards a committed artifact belongs under `tests/`, even when
  it lives in `experiments/`.** `experiments/` is exempt from the code-quality
  hooks and is not an importable package, so a check written inside an
  experiment script runs only when a human runs that script — which means it
  gates nothing, and a later docs-only PR can break what it guards and still go
  out green. The line is not "experiment code vs product code", it is **what
  the check protects**: a check over throwaway scratch stays where it is, but a
  check over something *committed* — a report table, a manifest, a `.json`
  deliverable — has to be reachable from `uv run pytest`. Make it reachable by
  splitting the pure part into its own module beside the experiment and adding
  a test under `tests/` that loads it by path
  (`importlib.util.spec_from_file_location`) and feeds it the **committed**
  artifacts, so the test needs neither the dataset nor a container; the script
  then calls the same function. Precedent:
  `tests/test_injection_shape_redaction.py`, and the one that prompted the rule
  — the screening report's runnability table, whose exact-once check shipped
  reachable only by hand after the table had already gone out naming 34 of 40
  instances. Two failures make an unreachable check *worse* than no check: a
  green tautological assertion is itself a claim that the artifact **was**
  verified, which is precisely the condition under which nobody verifies it by
  hand again; and a stale explanation of a fixed mechanism is indistinguishable,
  to a reader, from a working one. Both read as coverage. So specify the
  **failure condition** a check must produce — "deleting a row turns it red" —
  rather than the shape of the assertion.
- **Ways a check reassures without checking.** Four shapes, all met in one day
  of screening work, and each looks like coverage from the outside:
  (a) **a check that cannot fail** — `sum(partition.values()) == len(rows)` is
  true by construction, so it read nothing and stayed green with a whole
  category deleted; (b) **summing is blind to permutation** — rewriting one row
  as a duplicate of another of the same size keeps every per-row count *and* the
  total correct while losing a category, so coverage has to be asserted as
  **exactly once**, which no sum implies; (c) **a defect that moved and gained a
  plausible surface**, and (d) **a fix that covered half**. The executable test
  for all four: *any check reporting "no problem found" must be able to answer —
  if the problem existed, how would it appear in this check's output?* And the
  failure mode underneath them is **enumeration standing in for universal
  quantification**: fixing the case in front of you and leaving the one a step to
  the side is what enumerated cases are for. So specify the **failure condition**
  a check must produce ("deleting a row turns it red"), never the shape of the
  assertion — and produce each failure deliberately before relying on the check.
- **A fact that is recorded but never consumed is decoration, not a
  safeguard.** Three instances in one day, none of them an oversight:
  `sandbox.oom_kills` is written on every run from the cgroup counter (with
  `docker inspect`'s `State.OOMKilled` folded in as a floor) and **read by
  nothing**; `patch_baseline` shipped alongside an ADR-0001 amendment that
  described the exact failure it prevents, **default off**, so a NodeBB image
  produced a 166 KB patch from an agent that ran nothing; and `total_cost_usd`
  was already parsed out of every trace and **never persisted** (F5 of the
  2026-07-29 review). In each, the knowledge was already in the system and had
  **no consequence** — so what is missing is not the metric, it is **making the
  metric load-bearing**. The test: **name the branch it changes.** If you
  cannot, it is not yet load-bearing — and *"which branch does this change?"* is
  therefore a question worth asking in review of any new metric, field, flag or
  recorded fact, where a plausible-looking answer is what these three lacked.
  The general shape, which the two entries above are also instances of:
  **an artifact being present gets taken for a function being present** — the
  file exists, the field is recorded, the limitation is listed, the switch is
  there; every one of them real, and not one of them changing any outcome. It
  is also the usual reason a failure arrives rendered as a normal result: the
  fact that separates the red light from the green one has already been written
  down, and nothing reads it. Worked example:
  [ADR-0015](decisions/ADR-0015-four-words-for-how-a-rollout-ends.md), which
  turns `oom_kills` from a number into a gate.
- **Evidence can exist and still not be about the thing you are claiming.** Three
  instances, same week: a PR body said the quality bar was green "at this head"
  while the run in hand came from the pre-rebase SHA on an older base; a run read
  `docker ps` and then did not gate on it; and a branch rebuilt by restoring
  files from an old SHA silently reverted what two other PRs had merged into
  those files, whose only alarm was a `git diff --stat` line count that looked
  slightly wrong. Generally: **a silent revert's only alarm is a number that
  looks slightly off, and nobody is obliged to look at that number.** Re-measure
  at the object you are describing, or describe the object you measured.
- **`git checkout <sha> -- <paths>` has overwrite semantics, not merge
  semantics — so it never conflicts, and that is exactly what makes it dangerous
  when rebuilding a branch.** The safe form is
  `git diff <base> <sha> -- <paths> | git apply --3way`, which is right *because
  it conflicts*. Related, and cheap to avoid: **never let a branch switch race a
  background quality-bar run** — one such race reported a `basedpyright` error
  against a `loader.py` line that did not exist on the branch it was read
  against.
- **A rule can be *undefined* rather than unmet, and undefined looks exactly
  like satisfied at the point of use.** The jurisdiction bullet above is one
  case (rollout criteria applied to a local test run). Two more shapes: a
  criterion that **cites a baseline which does not exist** for the arm or path
  in question — switching arms does not make it false, it makes it undefined;
  and a criterion whose author **cannot create the conditions under which it
  holds** ("resume when steal < 10%" on a box whose own agent fleet keeps it
  throttled), which is worse when it is also **self-referential** — only a
  stopped pilot can satisfy it, and satisfying it exists to restart the pilot.
  Before relying on a criterion, name the reading that would satisfy it and say
  where that reading comes from.
- **Memory ceiling: MAXJOBS=2.** On the 16 GB dev box, ≥ 6 headless agents (or
  MAXJOBS=4 → 12 agents) swap-thrash. Streaming subprocess stdout to a file (not
  `capture_output=True`) and `killpg`-on-timeout are load-bearing — an early run
  hung for 13 h without them.
- **amd64 emulation is slow locally.** The prebuilt instance images are amd64;
  on Apple Silicon they run emulated. Real execution happens on **GitHub
  Actions** (native amd64, free minutes) — see [W2](workstreams/w2-solve-eval/).
- **Secrets never land in git.** Git history was once scrubbed (force-pushed)
  of a leaked OAuth token + operator PII; don't reintroduce either. Local secrets
  are `op://` references only — see [Secrets](#secrets). Trace records redact
  operator PII at write time.
- **Never put `CLAUDE_CODE_OAUTH_TOKEN` in an interactive shell.** The `claude`
  CLI logs in with that variable the moment it sees it, and ours is an
  *inference-only* subscription token — so an interactive Claude Code started
  anywhere under this directory silently authenticates with it and Remote
  Control refuses to start ("requires a full-scope login token"). This is not
  hypothetical: `.envrc.local` used to export exactly that name, direnv put it
  in **every** shell in the repo, and agent panes had to be restarted under
  `env -u CLAUDE_CODE_OAUTH_TOKEN` (2026-08-31). So `.envrc.local` exports
  `SWE_LAB_CLAUDE_CODE_OAUTH_TOKEN` instead, and the CLI copies it to the
  canonical name **in its own process** at startup
  (`swe_lab/cli/host_env.py`; `smoke-test.sh` does the same in shell). Note
  what this is *not*: the code's own name for the variable is unchanged and
  stays `CLAUDE_CODE_OAUTH_TOKEN` everywhere — harness, `pass_env`, CI job env.
  The shell is the only place the name is a hazard, so the shell is the only
  place it is avoided. Nailed down by
  `test_the_repo_scoped_token_is_adopted_under_the_name_a_run_reads` and
  `test_an_existing_canonical_token_is_never_overwritten`.
- **A fresh git worktree starts empty-handed.** Everything gitignored lives in
  the checkout it was created in, not in git, so a new worktree has neither
  [`.envrc.local`](#secrets) nor any dataset's `data/` folder. The dataset half
  has a **misleading symptom**: every `gold_unit_test` run exits `1` with a
  `FileNotFoundError`, which reads as "these instances are all broken" rather
  than "the parquet was never downloaded here" (2026-09-01, four candidates in a
  row). Both are one command: copy `.envrc.local.example` to `.envrc.local` and
  `direnv allow`; then re-run the download in the dataset's README
  ([`datasets/`](../datasets/README.md)). Do both before spending a rollout.
- **`git worktree remove` deletes gitignored content, silently.** It refuses on
  a dirty *tracked* tree and says nothing about anything gitignored — so a
  removed worktree takes its `.cache/`, its `.envrc.local`, and any experiment
  artifact that lived under it with it, with no warning and no prompt. The
  other face of the bullet above: gitignored things live in one checkout, and
  removing that checkout is how they stop existing. This is not hypothetical —
  trace-synthesis task 01's frozen phase-A failure was gitignored inside the
  implementer's worktree, the merged PR was followed by a `git worktree
  remove`, and the raw evidence was gone while the analysis written from it
  survived in git (2026-09-01; re-harvesting it cost three rollouts). So
  anything worth keeping does **not** live in a worktree: put it on a stable
  path outside every checkout (this box uses
  `/home/ubuntu/dev/swe-lab-artifacts/`) and commit a pointer plus a sha256
  manifest instead.
- **`docker rm` on a failed run can be irreversible evidence destruction.** Only
  `/workspace` is bind-mounted, so everything the actor writes elsewhere — its
  own native event stream at `/agent-home/.claude/projects/-app/*.jsonl` most of
  all — exists **only in the container's writable layer**, with no copy on the
  host. And a host run tree of the same name is not proof the record survives:
  `.cache/runs/<instance>/` is keyed by instance, so a rerun overwrites it.
  Measured 2026-09-01: the container of a steered run whose host-side Supervisor
  died mid-flight held the *only* surviving in-sandbox record of that attempt,
  because a rerun 21 minutes later had overwritten its host tree — and it was
  still there to recover only because nobody had reaped the container. So
  `docker cp` what you need out **before** `docker rm`. Same family as the
  bullet above: look before you delete.
- **While the capture proxy runs host-side, `capture="proxy"` has host
  prerequisites, and two of them fail silently.** Every item below follows from
  that one premise — the proxy is a process on the host, bound to a host port,
  that the container dials outward. Change the premise and the list stops
  applying rather than becoming wrong in place. The agent reaches the recorder
  at `host.docker.internal:<port>`, so:
  (a) the host firewall must let the Docker bridge in — this box's `ufw`
  default-denies incoming, so proxy capture fails outright unless the recorder's
  port ranges (`20000:20999` for rollouts, `25000:25999` for the aggregator) are
  allowed; that rule belongs to `machine-setup`, and `sudo ufw status` is how you
  check it rather than assuming it;
  (b) the port must be **free**, because `ReverseProxy._wait_until_listening`
  accepts *any* listener and an unrelated process squatting on it reads as "the
  proxy is up" — a stray `python3 -m http.server` cost one rollout that failed
  with an empty proxy log, empty stderr and exit 1, and the commonest squatter
  is **the previous run's own proxy**, which is reparented to `init` and keeps
  listening when its driver is killed; and (c) the proxy's
  `--target` must match where the credential is actually valid. That last one is
  a real gap for non-Anthropic upstreams: `ReverseProxy.target` defaults to
  `https://api.anthropic.com` and its only caller — the W1 annotation path in
  `pipelines/related_files/agent_run.py` — does not pass one, while
  `cc-reverse-proxy` gates its OpenRouter behaviour on the target string
  (`isOpenRouter = strings.Contains(targetURL, "openrouter.ai")`), so a wrong
  target *also* silently drops the `X-Anthropic-Beta` mirroring and `provider`
  injection that OpenRouter needs for interleaved thinking. Verify a proxied run
  by its log, never by its exit code.
- **Agreement can qualify evidence; it cannot establish that there is any.**
  When a consistency check is used as evidence for a claim that *presupposes*
  eligible, non-empty evidence, validate that premise **separately, and first**.
  Measured 2026-09-01, and it nearly voided a deliverable: `freeze_sample`'s
  stability gate compared the set of failing required tests across a run's
  grading attempts, and a run that **resolved on its first attempt** leaves one
  such set, empty. Agreement holds, so the strictest gate in the program cleared
  the one input that contradicted the sample's whole claim — that the actor had
  failed here. Note what did *not* go wrong: the set compared was real, and its
  self-equality is a correct answer to the question the gate was asked. What was
  missing is the premise the gate was silently trusted to carry. Stability
  qualifies a validity established some other way; it never establishes one. (This is not "every agreement predicate must reject
  emptiness": `len({...}) == 1` rejects zero observations, and an API may define
  agreement over nothing as a valid neutral answer. The rule is about what you
  are entitled to *conclude* from agreement, not about how the predicate is
  written.)
- **An unresolved workflow verdict has four causes, not two.** Exit 2 means the
  grading suite did not resolve the instance; whether the *actor* erred is a
  separate question, and neither the workflow's exit code nor
  `claude_code.timed_out` answers it. A run can come back unresolved with
  `timed_out == 0` and the actor never having started — measured 2026-09-01, a
  `protonmail/webclients` image that cannot execute the mounted `linux-x64`
  binary (`cannot execute: required file not found`, `claude_code.exit_code`
  127 after 0.69 s, `agent_complete` 0) still produced an ordinary unresolved
  verdict. A fourth cause sits on the grading side, and its mechanics are the
  opposite of what you might assume: `_UNIT_TEST_RETRIES = 2` lets the suite run
  up to three times, `UnitTest.should_retry` retries **while the verdict is
  unresolved**, and `run_task` builds the terminal outcome and record from
  whatever the **last executed attempt** left behind. So the last attempt *is*
  privileged — it alone decides the verdict, and an earlier unresolved attempt
  followed by a resolved one reports as resolved. An unresolved *run* therefore
  had every attempt unresolved, but those attempts can still disagree about
  **which** required tests failed, and that disagreement is a property of the
  suite rather than of the patch. The conservative gate is to require the same
  set of failing required tests in every recorded attempt, precisely because it
  refuses to inherit the last attempt's privilege. (That one is a guard, not a
  measurement — it was identified in review, not observed here.)
  Before treating an unresolved run as evidence about reasoning, require **all
  four**: `claude_code.timed_out == 0`, `agent_complete == 1`,
  `claude_code.exit_code == 0`, and the same required-test verdict in every
  recorded grading attempt. Whether an image can host the actor at all is a
  property of the repo family and is probed for free on every run —
  `rollout/a0/claude.info` opens with `claude --version` and its exit code.
- **`patches.py` is a stopgap.** The loader corrects 3 upstream dataset rows
  (truncated `fail_to_pass` names) **in memory**; it's a no-op on every other
  row. Retire it once a fixed parquet is published to HF and the loader can
  read the corrected rows straight from the dataset.
- **`outputs/` is a deliverable, not scratch.** The annotation JSON + parquet are
  version-controlled ground truth. Dataset data files and large trace records are
  *not* in git (gitignored / on HF respectively) — where that line falls, and
  why it is not a byte count, is
  [above](#what-may-be-committed-as-evidence).
- **Claude Code usage limits.** Long batch runs hit the subscription credit wall;
  the runners are built to stop cleanly on `UsageLimitError` and resume
  idempotently (skip instances whose output already exists).
