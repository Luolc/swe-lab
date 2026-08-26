# Task 30 — DeepSWE 1.1 as a second dataset

**Goal.** Port [DeepSWE](https://deepswe.datacurve.ai/) (Datacurve's benchmark,
v1.1) into swe-lab as a second dataset behind the existing seams — a
`deepswe` loader, a `TaskInstance`, and an eval spec that runs Datacurve's own
verifier **verbatim** — so every shipped harness and workflow runs against it
unchanged.

Status: **design, evidence-gathered 2026-08-25**. Nothing implemented.

---

## 0. What DeepSWE is

- **113 original tasks** across 91 repos, 5 languages (ts 35 / go 34 / py 34 /
  js 5 / rust 5), in the [Harbor task format](https://www.harborframework.com/docs/tasks);
  repo [`datacurve-ai/deep-swe`](https://github.com/datacurve-ai/deep-swe)
  (Apache-2.0 for Datacurve's contributions; upstream projects keep their own
  permissive licenses — `PROVENANCE.md` lists all 91, none copyleft).
- Tasks are **written from scratch** — the reference solution is not adapted
  from any existing commit/PR, so there is **no upstream fix commit to leak**.
  Verifiers are hand-written and behavioral ("accepts any solution whose
  observable behavior is correct").
- The leaderboard runs **mini-swe-agent** only, pass@1 with CIs; the blog
  explicitly invites what swe-lab does: *"A natural next step is running the
  same models under multiple harnesses, so a score can be decomposed into the
  model itself versus the scaffolding around it."*
- **v1.1** (repo `main`; git tag `v1.0.0` marks the old corpus) switched to
  Harbor's *separate verifier environment*: the agent never sees the tests,
  and grading happens in a pristine container.

## 1. Evidence — measured 2026-08-25

### Task anatomy (uniform across all 113)

```
task.toml         schema 1.3; task_id, language, repository_url,
                  base_commit_hash, per-task docker_image, limits
instruction.md    the prompt (all 113 end with "work on a new branch …
                  and commit everything when you are done")
environment/      Dockerfile reproducing the image (not needed by us)
tests/            test.sh (shared frame) + grader.py (shared, synced) +
                  config.json (f2p/p2p node ids + grade config) + test.patch
                  (the held-out tests) + Dockerfile (task image + COPY tests)
solution/         solution.patch (gold, base_commit-relative) + solve.sh
```

Uniform, verified by grep over all 113 `task.toml`: `schema_version 1.3`,
`network_mode = "no-network"` for agent **and** verifier, agent
`timeout_sec = 5400`, verifier `timeout_sec = 1800`, image
`public.ecr.aws/d3j8x8q7/swe-bench-202605:<ext_id>-v1.1`, cpus 2 / 8 GB.
`tasks/dataset.toml` names the corpus `datacurve/deep-swe-1-1` and pins a
sha256 digest per task. Grade formats: 78 ctrf, 35 junit — **internal to their
grader**, we never parse them.

### The image (pulled `abs-module-cache-flags`, anonymous ECR pull works)

- linux/amd64, ~840 MB; root; `bash`, `git`, toolchain present; cwd `/app`.
- `HEAD == base_commit_hash` exactly; branch `master`; **worktree clean in
  this image** — dirtiness is per-task, not universal (their grader's own
  comment: *"image build steps may have modified tracked files in-tree, so
  resets are per-file, never repo-wide"*).
- 652 commits of history, all ancestors of base, **no remote configured**.
  (The blog's "shallow clone with only the base commit" is imprecise; the
  property that matters — nothing after base to find — holds.)
- `/tests` and `/logs` **absent** — hidden tests exist only in the verifier
  build context.

### Their grading contract (read from the shared `grader.py` + `test.sh`)

1. `prepare`: reset **only the files `model.patch` touches** to
   `base_commit`, apply it; a patch that does not apply → `reward.json` with
   `apply_failed=1`, reward 0, suites never run (**graded**, the patch's
   fault). Then reset `test.patch`'s files and apply it loudly (a failure
   here → no `reward.json`, trap writes `reward.txt=-1` — **infra crash
   sentinel**, ungraded).
2. Run the suites (language-specific), emit ctrf/junit reports.
3. `grade`: whitelisted node ids → `reward.json`:
   `reward` binary (1 iff every f2p passes and no p2p fails), plus
   `f2p/p2p/partial` fractions. Absence == failure; worst-status-wins.

Their attribution maps 1:1 onto ours: `apply_failed` ⇒ graded unresolved;
`reward.txt=-1` ⇒ no verdict ⇒ failed attempt, retryable.

### The verifier runs verbatim under swe-lab's paradigm — proven

Their `tests/Dockerfile` is `FROM <task image>` + `COPY` four files — so
**mounting is equivalent to baking**, and swe-lab's eval sandbox needs no
image build. Measured, gold round trip:

```
docker run --network none \
  -v tasks/<id>/tests:/tests:ro \
  -v <staging>/artifacts:/logs/artifacts \   # model.patch = solution.patch
  -v <staging>/verifier:/logs/verifier \
  <task image> bash /tests/test.sh
→ reward.json: {"reward": 1, "f2p": 20/20, "p2p": 3/3, "partial": 1.0}
```

No network, no verifier image, byte-identical scripts. This is the whole eval
side of the port.

### Dirty-worktree census — measured 2026-08-25, all 113 images

Every task image was pulled, probed (`git status --porcelain` + diff capture),
and removed. Result: **112 of 113 ship a clean worktree; exactly one is
dirty** — `numba-stencil-boundary-modes`, a single uncommitted line in tracked
`numba/__init__.py` (`_min_llvmlite_version (0,47,0) → (0,46,0)`, the image's
llvmlite-compatibility shim, applied by a bare `sed -i` with no cleanliness
assertion after it).

Cleanliness is **engineered, not accidental**: 37 Dockerfiles end with a
porcelain-clean assertion (`RUN test -z "$(git status --porcelain)"`, comments
citing model.patch hygiene), 5 route lockfile drift through
`.git/info/exclude`, and v1.0→v1.1 fix notes in the Dockerfiles record exactly
this class of bug being repaired (unquoted `>=` specifiers that had littered
`/app`). The grader's per-file reset is defense in depth, not an
accommodation of widespread dirt.

Consequences for this port:

- **The contamination concern is near-dead**: worst case today is a one-line
  phantom hunk on one task. The scoped-extraction idea (§3's residue) is not
  worth building; `patch_is_empty` pollution is bounded to `numba-*`.
- **The unprotected-dirt hazard is real but tiny**: on `numba-*`, an agent
  that runs `git checkout -- .` reverts the version pin and breaks its own
  environment (llvmlite 0.46 installed, floor restored to 0.47). Upstream's
  pipeline shares this; record, don't fix.
- **`base_commit_hash` is not always 40-hex**: 110 tasks carry full shas, two
  carry 7-char short shas (`eicrud-*`, `langchain-*`) and one a 39-char
  truncation (`koota-entity-snapshot-rollback`) — all resolve as git ref
  prefixes (HEAD matches by prefix, verified), but the loader must not assume
  40-hex, and should normalize to the full sha (`rev-parse`) before it lands
  on records.

## 2. Design — mapping onto the existing seams

New package `datasets/deepswe/`, registered as `deepswe`; instance ids are the
task ids (`abs-module-cache-flags`).

| swe-lab seam | DeepSWE source |
|---|---|
| `sandbox_spec()` | `task.toml` image / `/app` / `base_commit_hash` |
| `prompt()` | `instruction.md`, verbatim (§4.3) |
| `gold_patch()` | `solution/solution.patch` |
| `required_tests()` | `f2p ∪ p2p` node ids from `tests/config.json` |
| `solution_sha()` | `None` — original tasks, no upstream fix commit; the purge stays on and its weakened assertion is the designed behavior |
| `unit_test_spec()` | mount `tests/*` at `/tests`, stage the candidate patch at the path their grader reads, run `bash /tests/test.sh`, read `reward.json` |
| verdict | `DeepSweVerdict`: `resolved = (reward == 1)`, `score = reward`; `f2p/p2p/partial/apply_failed` as metrics; `ctrf.json` + `run.log` as artifacts; absent `reward.json` (their `-1` sentinel) ⇒ no verdict ⇒ failed attempt |
| dataset acquisition | a **parquet we build and host ourselves** on a public HF dataset repo (§2b); the pinned-sha git checkout is the *builder's* source, not the loader's |

`tomllib` is stdlib — no new runtime dependency (`polars` and
`huggingface-hub` are already dependencies).

### 2b. Parquet distribution — decided 2026-08-25

Instead of every consumer cloning the upstream repo, we **materialize the
dataset once into a parquet** and host it on the public Hugging Face dataset
repo **`luolc/deep-swe-1-1-materialized`** (finalized 2026-08-25; the slug
matches upstream's own `datacurve/deep-swe-1-1`, and `materialized` names the
transformation), so the loader follows the exact `swebench_pro` path:
`datasets/deepswe/data/*.parquet` + `load_parquet` + a `COLUMNS` contract.

**Builder**: `python -m swe_lab.datasets.deepswe.build_parquet` — lives next
to the loader that consumes its schema, so the two cannot drift. Steps:

1. Shallow-fetch `datacurve-ai/deep-swe` at `PINNED_DEEPSWE_COMMIT`
   (self-verifying — a commit sha is a content hash).
2. One row per task: identity/metadata from `task.toml` (`task_id`, `ext_id`,
   `display_title`, `category`, `language`, `repository_url`,
   `base_commit_hash` **verbatim**, `docker_image`, timeouts, resources);
   file contents as columns (`instruction`, `test_sh`, `grader_py`,
   `config_json`, `test_patch`, `solution_patch`, `solve_sh`); per-row
   provenance (`upstream_repo`, `upstream_license`, parsed from
   `PROVENANCE.md`).
3. **Fixes as separate columns, never overwrites**: `base_commit` = the full
   40-hex sha, filled from a small in-builder table for the three tasks whose
   `task.toml` carries an abbreviated/truncated value (§1 census: two 7-char,
   one 39-char), values taken from the measured container `HEAD`s; identical
   to `base_commit_hash` elsewhere. The verbatim column stays, so the
   transformation is auditable per row. Same pattern as `swebench_pro`'s
   in-loader `patches.py`, but baked and visible.
4. Dataset-level metadata: `source_commit`, build-tool version, task count
   (assert 113).
5. Round-trip verification before upload: re-read the parquet and compare
   every embedded file byte-for-byte against the checkout.
6. Upload with `huggingface-hub` (`HF_TOKEN`): the parquet **plus** the
   compliance set — upstream `LICENSE` (Apache-2.0), `PROVENANCE.md`
   verbatim, and a README carrying attribution, the source commit, and the
   transformation statement ("repackaged unmodified into columns; fixes
   listed below"), per the licensing analysis (Apache-2.0 for Datacurve's
   contributions; all 91 upstream licenses permissive; attribution must
   travel with the data).

**Integrity — the pin is the anchor, the manifest is the record** (decided
2026-08-25). An HF repo is mutable, like a docker tag, so silent regeneration
is the drift to defend against — the same lesson the binary pins encode:

- **In swe-lab**: `PINNED_DEEPSWE_PARQUET_SHA256` beside the commit pin; the
  loader verifies the local parquet against it and refuses a mismatch with an
  actionable message. The trust anchor lives in the consumer — a checksum
  fetched from the same repo it checks proves only internal consistency.
- **On HF**: `manifest.json` beside the parquet (readable without pulling it,
  the claude-code-bundle precedent): source commit, parquet sha256, row
  count, the fixes applied, and a **per-task content hash** (sha256 over the
  row's canonical JSON). Parquet bytes are not deterministic across
  arrow/polars versions, so the file sha pins the *published artifact*; the
  per-task hashes are encoding-independent and answer "*which task* changed"
  on a bump — and they retire the open question about Harbor's opaque
  digests, since we now have our own with defined semantics.

**Loader**: reads the parquet exactly as `swebench_pro` does; `DeepSweInstance
.from_raw(row)` uses the normalized `base_commit` and never re-parses the
upstream repo. The hidden tests are already public upstream, so a public
parquet leaks nothing new — recorded, with the contamination-stewardship note,
in the licensing discussion.

## 3. The extraction-style decision: **default mode, not baseline**

The pairing rule from ADR-0001's amendment decides this, and the answer is
the opposite of what "Harbor images can ship dirty" suggests:

- Their grader resets touched files to **`base_commit`** and applies —
  so the patch it accepts must be `base_commit`-relative. swe-lab's **default
  extraction produces exactly that**, and their per-file reset is precisely
  what makes a `base_commit`-relative patch self-consistent even on a dirty
  image (contaminated hunks reproduce the image's own state; the score is
  unaffected).
- A **baseline-relative** patch would *break* their grader: for a file the
  image mutated, the per-file reset to `base_commit` restores a preimage the
  baseline hunk does not match → `apply_failed` → **mis-grade**.

So the shipped `deepswe` flow keeps `patch_baseline=False` on both sides.
`patch_baseline` remains what it is: for a grader that grades the image's
tree as shipped — which DeepSWE's v1.1 verifier deliberately is not.

One residue to accept (or fix cheaply): on a dirty-image task, default
extraction folds image edits into the patch, so `patch_is_empty` and the
result-verifier's signals read a did-nothing agent as having a patch. Their
own collect hook has the same property. Candidate mitigation, deferred to
implementation: record `git status --porcelain | wc -l` at rollout start as a
metric, so a contaminated-patch record is identifiable from the manifest.

## 4. Fidelity deviations — each a decision, not an accident

1. **Agent network.** Upstream agents run air-gapped (Pier grants only an LLM
   API allowlist). Our harnesses dial their APIs directly, so the shipped
   flow runs the agent sandbox with network — **and the hidden tests are in
   the public GitHub repo**, which makes "fetch the tests" the top integrity
   hazard of this port (worse than AGENTS.md injection; there is no upstream
   fix to find, but there are literal answers). Mitigations, in order:
   downstream proxy allowlists (the paradigm already in use there); a
   result-verifier rule flagging any trace reference to `datacurve` /
   `deep-swe` URLs (detection-not-gate, ADR-0010); documented deviation.
   **The verifier sandbox, by contrast, runs `network=False` faithfully** —
   proven above.
2. **Worktree diff vs commit diff.** Their collect hook diffs
   `base..HEAD` — an agent that never commits scores 0 upstream. We diff the
   worktree, which grades such an agent on its actual edits. Deliberate
   (harness-agnostic robustness, same argument as ADR-0001's amendment), and
   generous relative to the leaderboard — noted for comparability.
3. **The "commit everything" prompt line stays.** Prompt fidelity beats
   removing a now-unnecessary instruction; an agent that follows it loses
   nothing under a worktree diff.
4. **`--binary`.** Their collect uses it; we strip binary (ADR-0001 Facet 3).
   Behavioral tasks make a binary-only solution unlikely; recorded, not
   solved.
5. **Timeouts/resources.** Upstream: agent 5400 s, verifier 1800 s, 2 cpus /
   8 GB. Our defaults: 1800/1800, no cgroup limits. The `deepswe` definition
   (or overrides) should set the agent entry to 5400 s; resource caps are a
   Docker-backend gap, noted and deferred.
6. **Leaderboard comparability is limited by design** — they hold
   mini-swe-agent constant; we vary the harness. That is the point (their
   blog says so), not a defect.

## 5. Implementation order (when picked up)

1. ~~`build_parquet` + the HF repo + `datasets/deepswe/` loader + record~~ —
   **done** (parquet published + pinned; loader registered as `deepswe`,
   verifying the pin on every load; producer→consumer round-trip tested).
2. ~~`unit_test_spec` compile + `DeepSweVerdict` + golden self-check~~ —
   **done**: their verifier verbatim (tests mounted, no resets of ours), the
   `-1` sentinel raises ungraded, `apply_failed` grades zero; golden
   self-checks green across all five languages via the real CLI
   (`gold_unit_test --dataset deepswe`, eval `network=false`), including
   `koota-entity-snapshot-rollback`, which proves the short-sha fix live.
3. A `deepswe` workflow definition (agent 5400 s, eval `network=False`), full
   `rollout_and_unit_test` e2e on one task per language.
4. Gold sweep over all 113 (the W2 playbook), then the integrity rule from
   §4.1.

## 6. Open questions

- ~~Whether to verify `dataset.toml`'s per-task digests~~ — superseded by our
  own per-task content hashes in the manifest (§2b).
- Whether image pulls should be cached-and-pinned by digest rather than tag
  (`<ext_id>-v1.1` tags are mutable in principle; 113 × ~0.8 GB ≈ 95 GB if
  ever pulled wholesale).
