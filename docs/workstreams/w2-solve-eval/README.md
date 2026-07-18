# Workstream 2 — Solve + evaluate pipeline

**Status: 🚧 Active.** The **evaluation** subsystem is built and validated, and
the **full gold self-test sweep is done** (731/731 golden patches resolve after
an in-loader fix). **`rollout` (agent sampling) is the current focus.**

Started 2026-07-09. Build a **robust, Docker-based pipeline that actually solves
SWE-Bench Pro tasks** (an agent generates a patch) and **evaluates** them (apply
the patch, run the tests, grade). Reuse the best existing references rather than
reinvent; no existing harness fully fits, so we build our own around them.

---

## Objective & split

Two decoupled flows over a shared Docker layer:

- **`rollout`** *(agent sampling — in progress)* — run a headless coding agent
  **inside** an instance's prebuilt container and capture its full trajectory +
  the resulting patch (`git diff`). Deliberately **not** called "solver": trace
  generation is general (solving is one use; later we'll also feed a trajectory
  back in for behavioral analysis). The agent harness is **pluggable** (Claude
  Code first; Codex / OpenCode / … later) — the harness-agnostic contract is
  *patch = `git diff` of the workdir*; the trace format is per-harness.
- **`evaluation`** *(built + validated)* — apply a candidate patch, run the
  instance's tests, parse, and grade `resolved ⇔ (fail_to_pass ∪ pass_to_pass) ⊆
  passed`.

## Reference

Official harness cloned at `/Users/luoliangchen/dev/3p/scaleapi/SWE-bench_Pro-os`
(MIT). We **reuse** its prebuilt per-instance Docker Hub images
(`jefzda/sweap-images:<dockerhub_tag>`), its per-instance `run_script.sh` +
`parser.py`, and its grading rule; we **port** its `create_entryscript` logic. We
do **not** vendor its ~1000 harness files or take it as a submodule — instead we
**fetch** each instance's `run_script`/`parser` from a **pinned commit**
(`ca10a60…`, tip of `origin/main` at 2026-07-10) into a gitignored cache. Solver
references: **mini-swe-agent** (MIT; Scale's leaderboard scaffold) and the
**Claude Agent SDK** (MIT).

## Architecture — general flow + per-dataset adapter

Mirrors the `datasets/` split (general loader + per-dataset record). **General,
dataset-agnostic** code never learns a dataset's specifics; each dataset provides
an **adapter**:

- `core/benchmark.py` — the shared contract: `EvalSpec` (image ref, workdir,
  base_commit, before_repo_set_cmd, run_script/parser content, test lists,
  grading) + `BenchmarkAdapter` protocol. NB: `EvalSpec` still carries
  SWE-Bench-Pro-shaped fields (`run_script`/`parser`); the general/per-dataset
  boundary here is provisional until a second dataset forces it to firm up.
- `core/datasets/swebench_pro/` — a **package** holding **all** SWE-Bench-Pro
  run-knowledge: `record.py` (the record) + `execution.py` (`SweBenchProAdapter`:
  jefzda image ref, pinned scaleapi harness fetch, `EvalSpec` builder) +
  `grading.py` (the SBP grader — ports Scale's `create_entryscript`, stages the
  workspace, runs it, parses `output.json`, grades → `EvalResult`;
  `build_eval_script` also has `apply_patch` / `checkout_golden_tests` flags for
  dataset self-checks). The grader is dataset-specific — plain SWE-Bench has no
  `run_script`/`parser`. Adding a dataset = adding a sibling adapter package.
- `core/docker/provider.py` — general `DockerProvider` (pull; run a script in a
  bind-mounted container; `linux/amd64`).
- `evaluation/` — the general eval **CLI** only (`__main__`): pick a dataset,
  build its `EvalSpec`, hand it to that dataset's grader. CLI:
  `python -m swe_lab.evaluation <id> --gold` (grade the gold patch as a
  self-test) or `--patch-file`. Only SWE-Bench Pro is wired up today.
- `.github/workflows/eval.yml` — manual gold self-test on a GitHub-hosted runner.

## Decisions (2026-07-10)

- **Execution = GitHub Actions.** Debug on the private repo's free 2000
  min/month. First real container run on GH Actions (native amd64 — no local
  Apple-Silicon emulation; gold eval needs no secrets). Public-repo (free,
  unlimited minutes) decision deferred; if needed, a minimal public repo can
  wrap this one.
- **Job model is per-flow (updated 2026-07-13).** Two options: **(A) container
  job** (`jobs.<x>.container.image: <instance image>` — the whole job *is* the
  image) vs **(B) ubuntu runner + `docker run`** (job on ubuntu host, image run
  as a throwaway container). **NOT freely interchangeable with one CLI:** the
  current `core/docker/provider.py` is B-only (it shells `docker run`); using it
  under a container job would be docker-in-docker. Supporting A needs a separate
  "run the entryscript directly in the job, no docker" path.
  - **Runtime efficiency is equivalent.** A container is namespaced processes on
    the host kernel, not a VM. The only real perf axis is amd64 *emulation*,
    which is a local-Apple-Silicon issue, absent on GH amd64 runners, and
    orthogonal to A-vs-B. Choose by ergonomics/constraints, not speed.
  - **eval → B (shipped).** Harness (grading) stays on the host; one job can
    `docker run` many instances sequentially (economical for the 731 gold sweep);
    same code path as local; full control of `--platform` / `--network none` /
    `--rm`. `eval.yml` = `runs-on: ubuntu-latest` + `python -m ...evaluation`.
  - **rollout → A (planned).** It is naturally one-instance-per-job (the agent
    runs minutes → one patch). The Claude Code binary runs in the job shell
    (which *is* the sandbox), edits the repo, runs tests, then `git diff` — no
    container-lifecycle juggling. Caveat: the image must be a valid GH job
    container (GH injects node; minimal images can miss libs). "Mount the pinned
    Claude Code linux-x64 binary" is **orthogonal** to A-vs-B.
- **Auth.** P0 = **Claude subscription** via `CLAUDE_CODE_OAUTH_TOKEN`
  (`claude setup-token`); P1 = **OpenRouter** (Anthropic-compatible endpoint),
  Claude models only, for its more flexible limits.
- **Claude Code in the container** = mount a **pinned native linux-x64 binary**
  (downloaded at runtime to a gitignored cache — **never** committed), not an
  npm-in-a-wrapper-image (which would mean building/pushing ~731 images).
- **Trace storage** reuses the W1 pattern (HF dataset repo + manifest).

## Progress — evaluation validated ✅

Gold self-tests **resolve** end to end:

| instance | lang / runner | where | result |
| --- | --- | --- | --- |
| flipt-io/flipt | Go / `go test` | local (emulated) | resolved ✅ |
| flipt-io/flipt | Go / `go test` | GitHub Actions | resolved ✅ (~2.5 min) |
| ansible/ansible | Python / `ansible-test` | GitHub Actions | resolved ✅ (~57 s) |

Neither needed ENV-scraping nor `test_patch` (the tests exist at `base_commit`).
The GH runner is native amd64 → the whole thing (checkout + `uv sync` + dataset
download + image pull + test + grade) is ~1–2.5 min/instance and free.

### Full golden sweep done — 3 dataset-side false `GOLDEN_FAIL`s fixed *(2026-07-16)*

The **gold self-test sweep** was run across the whole dataset (GH Actions run
`29463094538`): **728/731** golden patches resolve. The **3** that did not —
NodeBB, ansible, vuls — were all diagnosed as **false negatives in the upstream
dataset, not harness bugs**: their `fail_to_pass` lists carry a handful of test
names **truncated by exactly one trailing character** (a closing `"` in seven
cases, a trailing space in one), so grading's exact string-set membership scores
those (actually-passing) tests as missing. Confirmed via local Docker repro and
cross-checked against Scale's own `swe_bench_pro_eval.py`, which fails identically
on the same data — full write-up in
[`experiments/eval_issues/truncated_golden_test_names/`](../../../experiments/eval_issues/truncated_golden_test_names/README.md).

**Temporary fix (in place):** rather than re-host the parquet yet, the loader
still downloads the *original* upstream parquet and corrects only these 8 entries
**in memory at load time** — see
[`core/datasets/swebench_pro/patches.py`](../../../src/swe_lab/core/datasets/swebench_pro/patches.py)
(`patch_fail_to_pass`, applied in `record.from_raw`; a no-op on every other row).
With it, all three gold-eval `resolved = true` locally → the sweep is
effectively **731/731**. **End state (TODO):** publish one fully-fixed parquet to
our own Hugging Face dataset repo and point the loader at it; then delete
`patches.py`. See [[dataset-golden-fix]] in memory.

## Next steps

**Priority (set 2026-07-14): `rollout` first**, because it takes wall-clock time
to run; while it runs we build matrix-eval + the gold sweep. A subscription
`CLAUDE_CODE_OAUTH_TOKEN` is available (stored in gitignored `.envrc.local`;
rotate after use).

1. **`rollout` — the container agent loop.** Run headless Claude Code inside each
   instance's prebuilt image, capture the trajectory + the patch. Sub-tasks:
   - **Patch extraction** is the hard, error-prone part. Its contract is settled
     in [ADR-0001](../../decisions/ADR-0001-patch-extraction-and-grading.md)
     (Accepted); the [corner-case survey](../../patch-extraction.md) is retained as
     non-authoritative background. The code is the source of truth
     ([`core/patch.py`](../../../src/swe_lab/core/patch.py),
     [`rollout/`](../../../src/swe_lab/rollout/)).
   - Extract the generic "run Claude Code headless + stream-json trace + exchange
     record" into `core/agent/` so rollout reuses it. *(Done — see
     `core/agent/`.)*
   - Mount a pinned native linux-x64 Claude Code binary (gitignored cache, never
     committed); GH Actions **container job** model (one instance per job).
2. **Close eval gap** (cheap, do alongside rollout): an **empty-patch guard**
   (D8). Confirm the **open item**: does Pro's per-instance harness reset
   agent-touched *test files*, or must we? (D5 — finding: golden test files are
   already restored.)
3. **Matrix eval** — one dispatch grading many instances in parallel (256
   matrix-cap → shard across workflows). The path to running all 731. Build while
   rollout runs.
4. **Gold self-test sweep** ✅ *(done 2026-07-16)* — see "Full golden sweep"
   above. Remaining follow-up: **publish the fully-fixed parquet to Hugging Face**
   and retire the in-memory `patches.py` stopgap.

## Patch-extraction decisions

Settled in [ADR-0001](../../decisions/ADR-0001-patch-extraction-and-grading.md)
(Accepted): text-only diff vs `base_commit`, strict `git apply` matching Scale.
The code is the source of truth.

## Open items / contingencies

- **ENV / `test_patch`.** Not needed for flipt/ansible, but Scale's entryscript
  scrapes `ENV` from the dockerfiles and some instances may need `test_patch`
  applied. Add to the adapter **only if a gold self-test fails** — the sweep will
  surface these.
- **Scale-harness brittleness to harden as we port** (from the research):
  `eval()` on dataset fields → `ast.literal_eval`; ENV scraped textually; only
  the last line of `before_repo_set_cmd` used; regex parsers are format-sensitive;
  image tag special-casing (element-web / `-vnan`). We already fetch scripts from
  a pinned commit to avoid drift.
