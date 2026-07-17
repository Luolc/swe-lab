# Extracting a diff patch from an agent's rollout

**Scope.** A *general* engineering reference for one deceptively hard step in the
`rollout` workstream (see [`PLAN.md`](../PLAN.md) → Workstream 2): after a coding
agent has edited a repo inside its container, capture its work as a **patch** that
our `evaluation` step can re-apply with `git apply` and grade. Getting `git diff`
"mostly right" is easy; getting it right for *every* instance — new files,
binaries, symlinks, submodules, LFS, exotic encodings, leaked git config — is
not. The failure mode is almost always **silent**: a wrong patch doesn't error,
it just grades as unresolved, or worse, applies the wrong bytes.

This document surveys how the canonical open-source harnesses extract and apply
patches, catalogs the corner cases with grounded sources, and records the
concrete choice we make and *why*.

**How to read it.** Every claim is backed by a primary source: a file:line in a
locally-cloned reference repo, or a fetched URL with a quote. Items not traceable
to a primary source are marked **[unverified]** — trust them less. This is sourced
on purpose because the failure modes are subtle; do not treat any of it as settled
folklore. If you only read one thing, read **§1 (our binding constraint)** and
**§7 (our decision)**.

**Contents.** §1 our eval-side constraint · §2 the extraction idiom ·
§3 config/environment hygiene · §4 the corner-case catalog (A staging/scope,
B content transforms, C file types, D patch transport, E apply-time) ·
§5 semantic & safety processing · §6 how other harnesses do it · §7 our decision ·
§8 gaps in our code · §9 references.

---

## 1. The binding constraint: our *apply* side dictates our *extract* side

A patch is only useful if our evaluation can apply it. Our eval mirrors Scale's
official SWE-Bench Pro harness, so **the Pro apply contract is the spec** — not
SWE-Bench classic's (which is far more forgiving; see §6).

From the primary source
`3p/scaleapi/SWE-bench_Pro-os/swe_bench_pro_eval.py`, mirrored by our
[`core/datasets/swebench_pro/grading.py`](../src/swe_lab/core/datasets/swebench_pro/grading.py):

1. **A single, strict apply — no fallback ladder.** The entryscript applies with
   exactly one command (`swe_bench_pro_eval.py:120`; our `build_eval_script`):

   ```bash
   git apply -v /workspace/patch.diff
   ```

   No `--reject`, no `patch --fuzz`, no `--3way`. Contrast SWE-Bench classic's
   three-rung ladder (§6). **If our extracted patch doesn't apply cleanly against
   `base_commit` with plain `git apply`, the instance is simply lost.** This puts
   the entire robustness burden on *extraction*: the patch must be a clean,
   well-formed git diff against the exact base commit.

2. **Binary hunks are stripped before apply.** Before writing `patch.diff` the Pro
   harness runs `strip_binary_hunks()` (`swe_bench_pro_eval.py:75-92`), deleting
   any section containing `Binary files ... differ` or `GIT binary patch`:

   ```python
   def strip_binary_hunks(patch: str) -> str:
       sections = re.split(r'(?=^diff --git )', patch, flags=re.MULTILINE)
       ...
       if re.search(r'^Binary files .* differ$', section, re.MULTILINE): continue
       if re.search(r'^GIT binary patch$', section, re.MULTILINE): continue
   ```

   **Consequence:** in Pro grading, binary file changes are *never applied* — they
   are silently dropped (Scale prints `Stripped binary diff hunks from patch`,
   `:190`). A patch whose correctness depends on a binary change is ungradeable by
   construction. We should still *extract* binaries faithfully (for the trace and
   for other harnesses) but must not expect them to affect the Pro grade.

**Net target:** a clean, `git apply`-able diff against `base_commit`; binaries are
best-effort-and-flagged, not load-bearing. Everything below serves that target.

---

## 2. The extraction idiom (and why each piece is there)

All three local reference harnesses converge on the **same core idiom** (verified
in the clones):

- **SWE-agent** — `SWE-agent/sweagent/agent/agents.py:840`, and the `submit` tool
  `SWE-agent/tools/submit/bin/submit:10-11`:
  ```bash
  git add -A && git diff --cached > /root/model.patch
  ```
  read back with an encoding guard: `patch_path.read_text(errors="backslashreplace")`.
- **mini-swe-agent** — `mini-swe-agent/src/minisweagent/config/extra/swebench.yaml:166`:
  ```
  echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && git add -A && git diff --cached
  ```
- **OpenHands** — the most defensive variant, `run_infer.py::complete_runtime()`
  (tag 0.30.0): disables the pager, removes stray nested `.git` dirs, then
  `git add -A` and **diffs against `base_commit`, not `HEAD`**, with a retry loop:
  ```python
  git config --global core.pager ""
  find . -type d -name .git -not -path "./.git"   # → rm -rf each
  git add -A
  git diff --no-color --cached {base_commit}       # retried up to 5×, growing timeout
  ```

**Why `git add -A` first?** A bare `git diff` compares the working tree to the
**index**, so a file the agent *created* is untracked and **silently omitted**
(<https://git-scm.com/docs/git-diff>). `git add -A` stages modifications,
additions, *and* deletions in one shot, and `--cached` then diffs the index.

**Why diff against `{base_commit}` (OpenHands) instead of a bare `--cached`?**
If the agent made *commits*, its changes live in `HEAD`, not the index. After
`git add -A`, the index equals `HEAD`, so `git diff --cached {base_commit}`
captures **both committed and uncommitted** agent work relative to the true base.
A bare `git diff --cached` would miss committed work. We adopt this.

The reference idiom is the *skeleton*. The rest of this doc is everything the
skeleton does **not** handle.

---

## 3. Config & environment hygiene — the whole "extract" side in one principle

**`git diff` does not simply serialize the byte difference between two blobs.** It
runs content through configurable transforms — filters, textconv, encoding
normalization, color, path quoting, prefixing — whose settings live *outside* the
repo (global/system gitconfig, environment, `.gitattributes`). Any transform that
is lossy or format-altering yields a diff that does not re-apply, or applies the
wrong bytes. A developer's `~/.gitconfig` alone can silently break extraction.

**Therefore: capture with a pinned, isolated git configuration and prefer
plumbing/`-c` overrides over whatever `git diff` the ambient shell resolves.** The
individual knobs are catalogued in §4B/§4E; the umbrella defense:

```bash
GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null GIT_CONFIG_NOSYSTEM=1 \
GIT_PAGER=cat GIT_EXTERNAL_DIFF= \
git -c core.quotepath=false -c core.autocrlf=false \
    -c color.ui=never -c diff.noprefix=false -c diff.mnemonicPrefix=false \
    -c diff.external= -c diff.colorMoved=no -c diff.wsErrorHighlight=none \
    <subcommand> --no-color --no-textconv --no-ext-diff ...
```

`GIT_CONFIG_GLOBAL`/`GIT_CONFIG_SYSTEM`/`GIT_CONFIG_NOSYSTEM` isolate from user/system
config; `git -c` overrides *everything* from files
(<https://git-scm.com/docs/git-config>). Sources for each specific knob are in §4.

---

## 4. The corner-case catalog

Each entry: **Problem → Fix (verbatim) → Source**, with confidence flags carried
from the underlying research. Grouped by *where in the pipeline* the failure
happens.

### 4A. What gets captured — staging, ignore, index, scope

**4A.1 New / untracked files** *(the #1 silent bug).* `git diff` omits untracked
files. Fix: stage first (`git add -A && git diff --cached`, §2), or `git add -N`
(intent-to-add) then diff (<https://git-scm.com/docs/git-add>) — but `-N` corrupts
*binary* new files (§4B.1), so stage binaries fully.

**4A.2 Gitignored files the agent created** *(silent omission).* `git add -A`
**respects `.gitignore`** and silently skips a new file whose path matches an
ignore rule — no error. git-add docs: naming an ignored file explicitly *errors*,
but bulk `-A` *"will silently ignore the file."*
(<https://git-scm.com/docs/git-add>). Fix: `git add -f` force-adds, but then
drags in genuine build artifacts (§4A.5) — a pollution-for-omission trade. Robust
answer: don't rely on `.gitignore` for scoping; diff a clean base and, if a real
source file is ignored, force-add it specifically. **Detection** needs a separate
`git status --ignored` / `git check-ignore` pass **[unverified: no single flag
both bulk-adds and reports skips]**.

**4A.3 `git add -A` scope / run directory.** Pre-git-2.0, `git add -A` from a
subdirectory staged only that subtree; 2.x stages the whole tree
(<https://git-scm.com/docs/git-add>, Git 2.0 release notes). `git diff --cached`
*from a subdir* also truncates its output to that subdir **[unverified — inferred
from pathspec-relative-to-CWD semantics]**. Fix: run at repo root or anchor both
with `git -C <root>` and the `:/` top pathspec (<https://git-scm.com/docs/gitglossary>).

**4A.4 Pre-existing index / dirty base.** `git diff --cached` diffs index vs
`HEAD`; anything staged or modified *before* the agent ran leaks into the patch.
SWE-Bench's fix commits a clean base first
(`swebench/harness/test_spec/python.py`): `git config user.*` + `git commit
--allow-empty -am SWE-Bench`, then diff against that. For us the container starts
at `base_commit`; ensure setup steps don't dirty the tree before the agent (else
apply the commit-the-base trick). Cross-ref SWE-Bench #465 (sanitize repo state).

**4A.5 Build-artifact / config pollution** *(real, filed bug).* Docker images pin
toolchain versions, so `git add -A` sweeps in env-driven edits (e.g. setuptools
rewriting `pyproject.toml`), breaking eval install. mini-swe-agent #528 fix, verbatim:
```bash
git add -A ':(exclude)pyproject.toml' ':(exclude)setup.cfg' ':(exclude)setup.py' \
           ':(exclude)tox.ini' ':(exclude)*.cfg' ':(exclude)*.toml' && git diff --cached
```
(<https://github.com/SWE-agent/mini-swe-agent/issues/528>). The `:(exclude)` /
`:!` pathspec magic is documented at <https://git-scm.com/docs/gitglossary>.
Moatless uses the same idea programmatically:
`[f":(exclude){p}" for p in ignore_paths]`
(<https://github.com/aorwall/moatless-tools/blob/main/moatless/repository/git.py>).
This is a fragile denylist across ecosystems (`node_modules/`, `*.egg-info/`,
`__pycache__/`, lockfiles); a clean-base diff is more general.

**4A.6 `assume-unchanged` / `skip-worktree` hide edits** *(silent omission).* Either
bit makes git *skip checking* a tracked file, so `git add -A`/`git diff` won't see
a real edit. git-update-index: *"Git will ... assume it has not changed"* and
*"does not provide a way to ignore changes to tracked files"*
(<https://git-scm.com/docs/git-update-index>). Fix: clear the bits
(`git update-index --no-assume-unchanged --no-skip-worktree`, enumerate via
`git ls-files -v | grep '^[hsS]'`), or diff a fresh base where per-index bits are
irrelevant.

**4A.7 Changes outside the repo root are uncapturable.** Edits to `/root`, `/tmp`,
`$HOME`, installed packages — a repo diff can't see them. Constrain the agent to
edit inside the repo (SWE-Bench edits under `/testbed`; our workdir is
`spec.workdir`). Mirror-image of §4A.5: out-of-repo *signal* is invisible,
in-repo *noise* contaminates.

**4A.8 Nested `.git` dirs / gitlinks (mode `160000`).** A stray nested repo (a dep
the agent cloned, a fixture that ran `git init`) is recorded by `git add -A` as a
single **gitlink** pointing at a commit that doesn't exist in eval — the real
files inside are lost and apply can't reproduce the gitlink. git warns *"adding
embedded git repository"* (<https://github.com/orgs/community/discussions/51876>).
OpenHands defends by `find . -type d -name .git -not -path "./.git"` → `rm -rf`
before diffing. We adopt this.

**4A.9 Per-worktree index.** Each `git worktree` has its **own index**; staging in
one and diffing from another yields an empty/partial patch
(<https://git-scm.com/docs/git-worktree>). Anchor staging and diff to the same
worktree. Low severity unless we use worktrees for isolation.

### 4B. How content is serialized — transforms that corrupt the diff

**4B.1 Binary files.** A text diff shows only `Binary files ... differ` and
**drops the bytes** → unapplyable. Fix: `git diff --binary` emits an applyable
binary patch (*"a binary diff that can be applied with git-apply"*,
<https://git-scm.com/docs/git-diff>); apply needs no flag (binary apply is
*"always allowed ... a no-op"*, <https://git-scm.com/docs/git-apply>). **`git add
-N` + binary is broken** (empty/empty-tree OID corrupts the diff; git-for-windows
PR #2733) — stage binaries fully and use `--cached --binary`. Moot for Pro grading
(§1 strips binaries) but do it for the trace.

**4B.2 Git LFS pointer vs content.** In an LFS repo without `git lfs install`,
checkout doesn't smudge: the working tree holds the **pointer text**
(`version https://git-lfs...`, `oid sha256:...`), so diffs are pointer-to-pointer
and can't reconstruct real bytes (<https://github.com/git-lfs/git-lfs/blob/main/docs/spec.md>,
FAQ). Detect via `.gitattributes` `filter=lfs` / `git check-attr filter`. Policy:
either ensure LFS objects are pulled, or exclude LFS paths.
**[unverified: no SWE-Bench issue names LFS specifically.]**

**4B.3 clean/smudge filters generally** (`.gitattributes` `filter=<x>`). Any
configured filter transforms content between working tree and index, so the diff
is in *cleaned* space and mismatches on apply
(<https://git-scm.com/docs/gitattributes>). Neutralize per-driver:
`-c filter.<x>.clean=cat -c filter.<x>.smudge=cat -c filter.<x>.process=`.

**4B.4 textconv / external diff drivers.** `diff.<d>.textconv` and
`diff.external`/`GIT_EXTERNAL_DIFF` make `git diff` emit a **human-readable,
NON-applyable** rendering. git-diff: textconv output *"cannot be applied"* and is
*"enabled by default only for git-diff and git-log, but not ... plumbing"*
(<https://git-scm.com/docs/git-diff>). Fix: `--no-textconv --no-ext-diff`, and
scrub `GIT_EXTERNAL_DIFF`, or use plumbing (`git diff-index -p`).

**4B.5 Color / ANSI / pager leakage.** `color.ui=always` injects ANSI escapes into
`+`/`-` lines; a pager can inject artifacts or block on non-TTY
(<https://github.com/alpine-docker/git/issues/11>). Fix: `--no-color`
(`= --color=never`, overrides config) + `GIT_PAGER=cat`/`--no-pager`. Note default
`color.diff=auto` self-disables on a pipe — the danger is an explicit `always`,
so `--no-color` is mandatory belt-and-suspenders.

**4B.6 Global gitconfig leakage into diff *format*.** A developer's `~/.gitconfig`
silently changes the serialized diff:
- `diff.noprefix=true` → no `a/`/`b/`; default `git apply -p1` then fails → needs
  `-p0` (<https://github.com/jesseduffield/lazygit/issues/3107>).
- `diff.mnemonicPrefix=true` → prefixes `i/ w/ c/ o/`; breaks parsers
  (`No such file or directory: './i/path.py'`,
  <https://github.com/PyCQA/pycodestyle/issues/701>).
- `diff.context`, `diff.algorithm`, `diff.wsErrorHighlight`, `diff.colorMoved`
  shift hunk boundaries or inject color (<https://git-scm.com/docs/diff-config>).
Fix: `--default-prefix` (*"overrides ... diff.noprefix, diff.srcPrefix,
diff.dstPrefix, diff.mnemonicPrefix"*, <https://git-scm.com/docs/diff-options>)
plus the config isolation of §3.

**4B.7 `core.autocrlf` / `text=auto` / `eol`.** Checkout normalization makes the
working tree differ from the index in EOLs with no logical change → either a huge
spurious whole-file diff or an unapplyable one. Real agent failure: SWE-agent #702
(`\r\n` vs `\n` → *"Hunk #1 FAILED ... different line endings"*,
<https://github.com/SWE-agent/SWE-agent/issues/702>). Fix: pin
`-c core.autocrlf=false -c core.eol=lf` at extraction; apply symmetrically or use
`--binary`. Renormalize workflow: `git add --renormalize .`
(<https://git-scm.com/docs/gitattributes>).

**4B.8 `working-tree-encoding` (UTF-16) & BOM.** UTF-16 files are stored UTF-8 in
the index but checked out UTF-16; without the attribute git treats them as
**binary** (no text hunk) (<https://git-scm.com/docs/gitattributes>). Not all
encodings round-trip (`core.checkRoundtripEncoding`). A UTF-8 **BOM** becomes part
of line 1 and breaks hunk apply (<https://github.com/magit/magit/issues/2832>).
Fix: diff against the UTF-8 index representation, prefer `--binary`, ensure the
apply side carries the same `.gitattributes`.

**4B.9 Non-ASCII paths / `core.quotePath`.** Default `core.quotePath=true`
double-quotes and octal-escapes paths with bytes ≥0x80 (`"a/r\303\251sum\303\251.py"`),
breaking header parsers and apply. *"Double-quotes, backslash and control
characters are always escaped regardless of the setting"*
(<https://git-scm.com/docs/git-config> core.adoc;
<https://github.com/cli/cli/issues/9114>). Fix: `-c core.quotepath=false`, and
parse `diff --git` paths tolerant of quoting.

**4B.10 NUL-byte auto-detection.** A source file that acquires a stray NUL (or is
UTF-16) is auto-classified binary → `Binary files differ`, no text hunk, unless
`--binary`. No `.gitattributes` needed.

### 4C. File-type specifics

**4C.1 Symlinks (mode `120000`).** Stored as a blob whose *content is the target
path*; the `120000` mode line is what tells apply to `symlink(2)` rather than
write a regular file — mode normalization corrupts it. `git apply` also **refuses
to write through a symlink** (security): a leading-symlink path *"can never appear
in a patch that validly applies"* unless the symlink is removed first
(<https://www.mail-archive.com/git@vger.kernel.org/msg64745.html>). Replacing a
dir with a symlink can reject the whole patch.

**4C.2 Executable bit / `core.fileMode`.** A permission-only change is a
**content-free** diff (`old mode 100644` / `new mode 100755`, no hunk). If the eval
container has `core.fileMode=false` (common on overlayfs/CIFS), git **omits the
mode lines** and the chmod is lost; conversely a umask mismatch invents phantom
mode changes (<https://ajoz.github.io/2021/10/31/git-old-mode-new-mode/>). Extract
and apply containers must agree on `core.fileMode`.

**4C.3 Empty files.** A new empty file is **header-only** (`new file mode ...`,
`index 0000000..e69de29`, no hunk); parsers that require a hunk drop it
(<https://github.com/sergeyt/parse-diff/issues/12>). Prefer `git apply` over
`patch` for these. **[partially verified: byte-level header from secondary sources.]**

**4C.4 "No newline at end of file".** git emits `\ No newline at end of file`; a
generator that omits it desyncs hunk counts → *"patch does not apply"* (go-git #936,
<https://github.com/src-d/go-git/issues/936>). Fix: generate with real `git diff`;
apply escape hatch `--inaccurate-eof` (*"works around this bug"*,
<https://git-scm.com/docs/git-apply>). Agentless has a **trailing-newline retry**:
on apply failure, append `\n` to the source and retry
(<https://github.com/OpenAutoCoder/Agentless/blob/main/agentless/util/postprocess_data.py>).

**4C.5 Renames & mode changes.** `similarity index`/`rename from,to` and
`old mode`/`new mode` are git-format extensions GNU `patch` doesn't understand
**[partially unverified — established knowledge, no single doc line]**. Keep such
patches on the `git apply` path (automatic for us). Consider `--no-renames` at
extract only if detection is unreliable.

**4C.6 Submodules (gitlinks).** A submodule change is a `Subproject commit <sha>`
gitlink requiring the commit to exist locally; in a fresh eval checkout it usually
doesn't → apply fails (<https://git-scm.com/book/en/v2/Git-Tools-Submodules>)
**[partially unverified — no specific SWE-Bench issue]**. `git apply` without
`--index` *"ignores"* submodule commits (<https://git-scm.com/docs/git-apply>).
Fix: `git diff --ignore-submodules=all` to exclude them. Deferred until a target
repo needs it.

### 4D. Patch transport — corrupting the `.patch` file itself

**4D.1 Line endings / encoding of the patch file.** Distinct from source EOLs: the
`.diff` file's *own bytes* get mangled — Windows redirection LF→CRLF, PowerShell
writing UTF-16/BOM, JSON/terminal/copy-paste normalization → *"No valid patches in
input"* (<https://openillumi.com/en/en-git-no-valid-patches-in-input-crlf-fix/>;
diagnostic: `git apply -v` shows trailing `?`,
<https://www.scivision.dev/git-apply-patch-eol/>). **Fix: write the diff as raw
bytes and read it back as bytes** — never round-trip through a text pipeline. If it
must ride in JSON, encode/decode without newline normalization. This is exactly
why SWE-Bench-style harnesses store patches as opaque byte strings and apply from a
raw file rather than `echo`-ing.

### 4E. Apply-time semantics & tolerance (all verbatim from git-apply docs)

Our Pro eval is fixed at `git apply -v` (§1), so we mostly *can't* add these on the
grading side — they matter if we ever harden eval (§7) or consume foreign patches.

- **`-p<n>`** — strip *n* leading path components; default `1`. `--no-prefix`
  diffs need `-p0`.
- **`--whitespace=<action>`** — `nowarn` (silence), `warn` (default), `fix`
  (auto-correct trailing-WS errors), `error`/`error-all` (refuse). Agentless &
  Scale-adjacent tools use `nowarn`; R2E-Gym uses **`fix`**
  (<https://github.com/R2E-Gym/R2E-Gym/blob/main/src/r2egym/agenthub/runtime/docker.py>).
- **`--ignore-whitespace`** — ignore WS changes in *context* lines (the fix when a
  patch fails only due to indentation/EOL drift in context).
- **`-C<n>`** — require *n* context lines to match (lower = fuzzier).
- **`--unidiff-zero`** — required to apply `-U0` (zero-context) diffs; git refuses
  them by default as unsafe.
- **`--recount`** — recompute hunk `@@` counts from the body (salvages
  hand-edited/miscounted headers). SWE-Bench inference does this proactively via
  `repair_patch`/`extract_minimal_patch`
  (<https://github.com/SWE-bench/SWE-bench/blob/main/swebench/inference/make_datasets/utils.py>).
- **`--inaccurate-eof`** — see §4C.4.
- **`--allow-empty`** — don't error on an empty/metadata-only patch.
- **`--3way`** — fall back to a 3-way merge when blobs are known locally (leaves
  conflict markers instead of failing); *incompatible with `--reject`*. We *have*
  `base_commit`, so `--3way` is the obvious unused mitigation if we harden eval.
- **`--directory=<root>`** — prepend a root to all paths (subtree-relative patches).
- **`--unsafe-paths`** / path traversal — `git apply` **rejects paths outside the
  working area by default** (the `../`/absolute-path guard); GNU `patch` (classic's
  fuzz rung) does **not** — historically a traversal vector (CVE-2023-23946,
  CVE-2023-25652). Real containment is the throwaway container as unprivileged user.

---

## 5. Semantic & safety-level processing (beyond raw git mechanics)

**5.1 Exclude the agent's test-file edits; the gold `test_patch` defines tests.**
A solver must not be able to game the held-out tests. SWE-Bench **resets test files
to base, then applies its own `test_patch`** (`swebench/harness/test_spec/python.py`):
```
git checkout {base_commit} {modified_test_files}   # reset modified tests
rm -f {new_test_files}                              # remove agent-created tests
git apply -v - <<'EOF' ... {test_patch} ... EOF     # apply gold tests
```
run *before* and *after* the test command. The **modified-vs-new** distinction is
load-bearing (a bare `git checkout <base>` would wipe image-setup changes, issue
#518). Test files are identified by the `diff --git a/.* b/(.*)` regex minus
`NON_TEST_EXTS = [".json",".png","csv",".txt",".md",".jpg",".jpeg",".pkl",".yml",
".yaml",".toml"]` (`constants/__init__.py`). Moatless instead filters with an
`is_test()` word-boundary check and skips test files in `generate_git_patch`.
**Relevance to us:** SWE-Bench Pro ships per-instance `run_script.sh` + `parser.py`
and applies the model patch, so how *our* pipeline handles agent-touched test files
is an **open item** — confirm whether Pro's harness resets them or whether we must.

**5.2 Ordering: reset tests → apply MODEL patch → apply `test_patch` → run.** Model
patch first, gold tests on top; because tests were reset first, the model can't
conflict with the later `test_patch` (<https://www.swebench.com/SWE-bench/guides/evaluation/>).

**5.3 Binary-hunk stripping** — Scale-Pro-specific (§1); classic doesn't do it.

**5.4 Empty / no-op patch = failure, never a pass.** SWE-Bench computes
`empty_patch_ids` (`prediction == "" or None`) and reports them unresolved
(`run_evaluation.py`); a patch that applies but changes nothing simply fails
`FAIL_TO_PASS`. Validation studies additionally reject whitespace/comment-only
diffs **[unverified as a stock-grader check]** (<https://arxiv.org/html/2503.15223v1>).
We should add an empty-patch guard.

**5.5 Patch size / DoS.** No size cap in stock SWE-Bench **[unverified]**; real
protection is container isolation + timeouts + worker caps. A Pro-style grader
wanting a DoS cap must add it.

**5.6 Path traversal / repo escape.** See §4E `--unsafe-paths`; rely on `git
apply` confinement + container sandbox.

**5.7 Hunk-header repair.** SWE-Bench inference recomputes `@@` stats from the
hunk body to salvage model diffs with wrong counts — a cheap pre-apply repair pass
worth stealing if we see count-mismatch failures.

---

## 6. How other harnesses extract & apply (comparison)

| System | Extract | Apply | Notable technique |
| --- | --- | --- | --- |
| **SWE-Bench Pro** (our target) | (solver-dependent) | single `git apply -v` + `strip_binary_hunks` | no ladder; binaries dropped (§1) |
| **SWE-Bench classic** | `git add -A && git diff --cached` | **ladder**: `git apply --verbose` → `git apply --verbose --reject` → `patch --batch --fuzz=5 -p1 -i` | forgiving fuzz fallback; test reset + `NON_TEST_EXTS` |
| **SWE-agent** | `git add -A && git diff --cached` | (upstream) | encoding guard on read |
| **mini-swe-agent** | same, + `:(exclude)` build files (#528) | (upstream) | pathspec exclude |
| **OpenHands** | pager off, strip nested `.git`, `git add -A`, `git diff --cached {base_commit}`, 5× retry | upstream ladder | diff vs base_commit; nested-repo cleanup |
| **Agentless** | `fake_git_repo` (synthetic init+commit+`git diff .`) | `git apply --whitespace=nowarn` + **trailing-newline retry** | normalize model edits to real git-diff; AST/lint gate |
| **R2E-Gym** | `git add -A && git diff --cached` | `git apply --whitespace=fix`; `git apply -R` to roll back | reverse-apply for clean retries |
| **Aider** | — (own search/replace) | **fuzzy Python apply**, ignores `@@` line numbers, progressive context-shrink, refuses ambiguous short context | line-number-free matching |
| **AutoCodeRover** *(non-OSS license)* | — | whitespace-stripped line match, two indentation reconstructions **disambiguated by which lints clean** | — |
| **Moatless** *(MIT)* | `difflib.unified_diff`, `:(exclude)` ignore paths, skip `is_test` | — | pathspec test exclusion |

The classic **`GIT_APPLY_CMDS` ladder** (`swebench/harness/run_evaluation.py`) is
the single most reused robustness pattern; note it does **not** use `--3way` (a
common misconception — `--3way` needs local blobs and is incompatible with
`--reject`). Reusable libs: `whatthepatch` (`.rej`-capturing `patch --forward`
fallback), `unidiff` (parse/classify hunks), `python-patch`.

---

## 7. Decision for this repo *(settled 2026-07-17; see `PLAN.md` → "Patch extraction — decisions")*

### Extraction (rollout side) — run in the container, at repo root, against `base_commit`

**Happy path = text only.** We deliberately do **not** extract or apply binary
(D3): intent-to-add new files with `git add -N` and diff **without `--binary`**,
so binary *bytes* are never serialized; the host then strips the residual
bytes-free `Binary files ... differ` marker. We diff against the instance's
original `base_commit` (D1), which guarantees the grading round-trip. No
`:(exclude)` build-noise denylist by default (D2). This is implemented in
[`core/patch.py`](../src/swe_lab/core/patch.py) `build_extraction_script`.

```bash
# 0. remove stray nested repos that would become gitlinks (§4A.8)
find "$REPO" -type d -name .git -not -path "$REPO/.git" -prune -exec rm -rf {} +

# 1. intent-to-add new files (no content staged, no binary bytes), isolated config (§3)
env GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null GIT_CONFIG_NOSYSTEM=1 \
    GIT_PAGER=cat GIT_EXTERNAL_DIFF= \
  git -C "$REPO" -c core.quotepath=false -c core.autocrlf=false \
    add -N -- ':/'          # exclude_globs is available but empty by default (D2, P1)

# 2. emit a text, applyable, format-stable diff of the worktree vs base_commit
#    (§2, §3, §4B). NO --cached (add -N shows new files in the worktree diff) and
#    NO --binary (binary → bytes-free marker, stripped host-side). NO
#    --default-prefix (git < 2.41 lacks it; the -c pins below are equivalent, D4).
env GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null GIT_CONFIG_NOSYSTEM=1 \
    GIT_PAGER=cat GIT_EXTERNAL_DIFF= \
  git -C "$REPO" -c core.quotepath=false -c core.autocrlf=false \
    -c color.ui=never -c diff.noprefix=false -c diff.mnemonicPrefix=false \
    -c diff.external= \
    diff --no-color --no-textconv --no-ext-diff \
    "$BASE_COMMIT" > patch.raw.diff   # write RAW BYTES; never a text round-trip (§4D)
```

Then, **host-side** (rollout runner): read `patch.raw.diff` back as bytes with an
encoding guard (`errors="backslashreplace"`, §2), `strip_binary_hunks` to drop any
`Binary files ... differ` section → write the clean, graded `patch.diff`; **guard
the empty patch** (treat empty/whitespace-only, or a patch that was entirely
binary, as a failed attempt — never graded as a pass, §5.4) and surface the reason
explicitly (`empty_patch` vs `unresolved_tests_failed`). Defer
`--ignore-submodules=all`, gitignored-new-file force-add, and the `:(exclude)`
denylist until a real instance needs them (D2/D7, **P1**) — but **log** when they'd
apply (no silent truncation).

Rationale maps 1:1 to the catalog: `add -N` + `{base_commit}` (worktree diff)
captures new/modified/deleted/committed text work (§2, §4A.1); omitting `--binary`
keeps bytes out (§4B.1) and the host strip removes the marker (§1); nested-`.git`
removal avoids gitlink loss (§4A.8); config isolation + `--no-textconv
--no-ext-diff --no-color -c diff.noprefix=false -c diff.mnemonicPrefix=false -c
core.quotepath=false -c core.autocrlf=false` neutralize every transform in §4B
(the `-c` prefix pins replace `--default-prefix` for git < 2.41, equivalent under
config isolation, D4); raw-byte I/O for transport (§4D). *Empirically verified
(2026-07-17): the stripped text patch applies cleanly against `base_commit`, and
`add -N` vs `add -A --cached` (both without `--binary`) are byte-identical — it is
omitting `--binary`, not `-N`, that drops binary.*

**Deferred (P1): faithful binary.** A two-artifact scheme — extract *with*
`--binary` for a faithful trace, strip for the graded patch — is deferred; today
binary is simply dropped, which matches Scale's grading effect (§1).

### Evaluation (grading side)

- **Match Scale exactly (D6):** single `git apply -v`, no fallback ladder. Grading
  does **not** call `strip_binary_hunks` because the patch reaching it is already
  binary-free (gold patches are binary-free; rollout patches are stripped upstream,
  §8). No change to the grading path.
- **Optional hardening (deferred, opt-in):** a fallback ladder
  `git apply -v` → `git apply --3way` (we have `base_commit`) → `git apply --reject`.
  Changes grading semantics vs Scale; must be opt-in + logged, never silent.
- **Test-file handling (D5):** our `build_eval_script` already restores the golden
  test files by path *after* applying the model patch (the last line of
  `before_repo_set_cmd`), so agent edits to the graded tests can't game them. A
  fuller all-test-file reset is P2 / monitor.

---

## 8. Status of our code *(2026-07-17 — was "gaps to close")*

- **Binary:** the extractor omits `--binary` (text-only, D3) and the rollout runner
  strips the residual marker, so the patch reaching
  [`core/datasets/swebench_pro/grading.py`](../src/swe_lab/core/datasets/swebench_pro/grading.py)
  is already binary-free; grading applies it verbatim (does **not** call
  `strip_binary_hunks`, matching Scale's effect of dropping binary).
- **Empty-patch guard:** wired rollout-side (`is_effectively_empty` → explicit
  `empty_patch` outcome, eval skipped). Eval-side defensive guard is P2 (§5.4).
- **The `rollout` extractor is written** — `build_extraction_script` +
  `rollout/entryscript.py` + `rollout/runner.py` implement this §7.
- **Test-file reset (§5.1):** golden test files are restored by grading (D5); no
  change needed for the load-bearing case.

---

## 9. References

**Primary sources (local clones — verified at cited file:line):**

- `3p/scaleapi/SWE-bench_Pro-os/swe_bench_pro_eval.py` — `strip_binary_hunks`
  (75-92), `git apply -v` (120), strip log (190). Our apply contract.
- `SWE-agent/sweagent/agent/agents.py:840`, `SWE-agent/tools/submit/bin/submit:10-11`
  — `git add -A && git diff --cached`; `errors="backslashreplace"`.
- `mini-swe-agent/src/minisweagent/config/extra/swebench.yaml:166` — submit idiom.

**Harness sources (fetched):**

- SWE-Bench `run_evaluation.py` (GIT_APPLY_CMDS ladder, `empty_patch_ids`),
  `test_spec/python.py` (test reset, `clean_diff_commands`), `utils.py`
  (`get_modified_files`), `constants/__init__.py` (`NON_TEST_EXTS`),
  `inference/make_datasets/utils.py` (`repair_patch`) —
  <https://github.com/SWE-bench/SWE-bench>.
- OpenHands `run_infer.py` (tag 0.30.0) —
  <https://github.com/All-Hands-AI/OpenHands/blob/0.30.0/evaluation/benchmarks/swe_bench/run_infer.py>.
- Agentless `postprocess_data.py`, R2E-Gym `docker.py`, Moatless `file_context.py`
  / `git.py`, Aider `udiff_coder.py` — links inline above.
- mini-swe-agent #528 (`:(exclude)` fix), SWE-agent #702 (CRLF), SWE-Bench #383
  (apply ladder / dirty base), #145 (corrupt patch), #465 (repo-state sanitize),
  go-git #936 (no-newline marker), git-for-windows #2733 (intent-to-add binary).

**Git docs (fetched):** git-diff, git-add, git-apply, diff-config, diff-options,
gitattributes, gitglossary, git-config, git-update-index, git-worktree —
<https://git-scm.com/docs/>.

**Lower-confidence / secondary:** labbott.name (binary patches), ajoz.github.io
(file modes), openillumi / scivision (patch-file EOL), codegenes, arXiv
2503.15223 (patch-quality audit). CVE-2023-23946 / -25652 (git apply traversal).

**Explicitly flagged uncertainties:** GNU `patch` non-handling of rename/mode
headers (§4C.5); submodule apply-failure mechanism (§4C.6); issue #145 CRLF
attribution; SWE-agent #717 resolution; LFS-specific SWE-Bench issue (§4B.2);
empty-file header bytes (§4C.3, partial); patch-size DoS cap and reverse-apply
round-trip in stock SWE-Bench (§5.5); `git diff --cached` subdir truncation (§4A.3).
