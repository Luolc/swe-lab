"""Patch helpers shared by ``rollout`` (extract) and ``evaluation`` (apply).

Producing a git diff that re-applies cleanly with ``git apply`` is deceptively
error-prone; this module is the small, testable core of it. See ADR-0001
(``docs/decisions``) for the full rationale and the alternatives rejected.

- :func:`build_extraction_script` — the in-container bash that turns an agent's
  edits into a canonical, applyable **text** diff vs ``base_ref``. It stages new
  files with ``git add -N`` (intent-to-add) and diffs **without** ``--binary``,
  so binary content is never serialized — the happy path is text-only. We pass
  the instance's ``base_commit`` as ``base_ref`` so the patch applies against
  the exact base the grader resets to.
- :func:`strip_binary_hunks` — drop binary ``diff --git`` sections from a patch.
  Omitting ``--binary`` (not ``git add -N`` — that part is incidental) is what
  keeps binary *bytes* out; a binary change then still shows as a bytes-free
  ``Binary files ... differ`` header, which would break ``git apply``. The
  rollout runner calls this on the extracted patch to remove those sections so
  the graded patch is cleanly text-only (matching what Scale's Pro harness
  strips before apply).
- :func:`is_effectively_empty` — an empty/no-op patch is a failed attempt, never
  a pass. True once a binary-only patch has its hunks stripped.
"""

from __future__ import annotations

import re
import shlex

# --- Applying / grading side -------------------------------------------------

_BINARY_MARKERS = (
    re.compile(r"^Binary files .* differ$", re.MULTILINE),
    re.compile(r"^GIT binary patch$", re.MULTILINE),
)
_DIFF_SECTION_SPLIT = re.compile(r"(?=^diff --git )", re.MULTILINE)
_DIFF_HEADER = re.compile(r"^diff --git ", re.MULTILINE)


def strip_binary_hunks(patch: str) -> str:
  """Remove binary diff sections from a git patch.

  Mirrors ``strip_binary_hunks`` in Scale's ``swe_bench_pro_eval.py``: drops any
  ``diff --git`` section that contains a ``Binary files ... differ`` line or a
  ``GIT binary patch`` block, so binary changes are never applied. The rollout
  runner calls this on every extracted patch: our extraction uses ``git add -N``
  + a diff **without** ``--binary``, which still emits a bytes-free
  ``Binary files ... differ`` header for any binary change; that header would
  break ``git apply``, so we strip it, leaving a cleanly-applyable text patch
  (the binary change is simply dropped — the same effect Scale gets by stripping
  at apply time).
  """
  if not patch:
    return patch
  kept: list[str] = []
  for section in _DIFF_SECTION_SPLIT.split(patch):
    if not section.strip():
      continue
    if any(marker.search(section) for marker in _BINARY_MARKERS):
      continue
    kept.append(section)
  return "".join(kept)


def is_effectively_empty(patch: str) -> bool:
  """Return whether a patch has no applyable content.

  True for the empty string, whitespace-only text, and a patch that carries no
  ``diff --git`` section at all (e.g. one that was entirely binary and got
  stripped by :func:`strip_binary_hunks`). Callers treat this as a failed agent
  attempt rather than a resolved task.
  """
  return not patch.strip() or not _DIFF_HEADER.search(patch)


# --- Extraction side (runs inside the instance container) --------------------

# git plumbing config pinned for extraction. `git diff` output format is
# config-driven (prefixes, color, textconv, quoting, EOL); a leaked user/system
# gitconfig can silently produce a non-applyable diff. We neutralize all of it.
# NB: we deliberately do NOT use `--default-prefix` (git >= 2.41 only) — pinning
# `diff.noprefix` / `diff.mnemonicPrefix` to false gives the same `a/ b/`
# prefixes on any git.
_ISOLATED_ENV = (
    "GIT_CONFIG_GLOBAL=/dev/null",  # ignore the user's ~/.gitconfig
    "GIT_CONFIG_SYSTEM=/dev/null",  # ignore /etc/gitconfig (system-wide)
    "GIT_CONFIG_NOSYSTEM=1",  # belt-and-suspenders: no system config at all
    "GIT_PAGER=cat",  # never open a pager (would hang a headless run)
    "GIT_EXTERNAL_DIFF=",  # no external diff program — use git's own
)
_ADD_CONFIG = (
    "-c",
    "core.quotepath=false",  # non-ASCII paths literal (UTF-8), not octal \NNN
    "-c",
    "core.autocrlf=false",  # never rewrite CRLF<->LF; stage bytes verbatim
)
_DIFF_CONFIG = (
    "-c",
    "core.quotepath=false",  # non-ASCII paths literal (UTF-8), not octal \NNN
    "-c",
    "core.autocrlf=false",  # never rewrite CRLF<->LF; diff bytes verbatim
    "-c",
    "color.ui=never",  # no ANSI color codes (would corrupt the patch)
    "-c",
    "diff.noprefix=false",  # keep the a/ b/ path prefixes (apply needs them)
    "-c",
    "diff.mnemonicPrefix=false",  # plain a/ b/, not mnemonic i/ w/ c/ o/
    "-c",
    "diff.external=",  # force git's built-in diff, no external tool
)
# No ``--cached`` (we diff the worktree vs ``base_ref`` so ``git add -N``'s
# intent-to-add new files show as full additions) and no ``--binary`` (binary
# content is never serialized — the happy path is text-only; the runner strips
# any residual ``Binary files ... differ`` header).
_DIFF_FLAGS = (
    "--no-color",  # no ANSI color (also enforced by color.ui=never)
    "--no-textconv",  # diff the real bytes, not a textconv'd view
    "--no-ext-diff",  # ignore any configured external diff helper
)


# The pre-agent baseline commit (ADR-0001, 2026-08-25 amendment). Everything
# here is pinned because the baseline sha must be a pure function of the tree:
# identity and dates enter the commit hash, so an unpinned value would make the
# rollout container and the grading container compute different shas for the
# same image — and sha equality is exactly how the grading side proves it is
# about to grade the tree the patch was taken against.
_BASELINE_IDENTITY = (
    "-c user.email=baseline@swe-lab.invalid -c user.name=swe-lab"
)
_BASELINE_DATE = "1970-01-01T00:00:00+00:00"
_BASELINE_MESSAGE = "swe-lab: pre-agent baseline"


def baseline_commit_lines(workdir: str) -> list[str]:
  """Return the commands that commit the tree exactly as it stands.

  **The one source of these lines.** Both sides run them — the rollout side to
  create the diff base, the grading side to *recompute* it and compare shas —
  and the sha is a hash over everything pinned here, so two copies that
  drifted by a character would make every baseline run fail its verify.

  ``add -A`` because the point is a base matching the tree the agent starts
  from — a file left out would later read as if the agent created it.
  ``--allow-empty`` so a clean worktree still yields a baseline rather than
  one whose existence depends on whether the image happened to be dirty.

  Args:
    workdir: In-container path of the instance's repo.

  Returns:
    The command lines, in order.
  """
  wd = shlex.quote(workdir)
  dates = (
      f"GIT_AUTHOR_DATE={_BASELINE_DATE} GIT_COMMITTER_DATE={_BASELINE_DATE}"
  )
  return [
      f"git -C {wd} {_BASELINE_IDENTITY} add -A -- :/",
      f"{dates} git -C {wd} {_BASELINE_IDENTITY} commit --allow-empty -q"
      f" -m {shlex.quote(_BASELINE_MESSAGE)}",
  ]


def build_baseline_script(*, workdir: str, output_path: str) -> str:
  """Build the bash that commits the pre-agent baseline and reports its sha.

  Args:
    workdir: In-container path of the instance's repo.
    output_path: Where the resolved sha is written.

  Returns:
    The bash script text, newline-terminated.
  """
  out = shlex.quote(output_path)
  wd = shlex.quote(workdir)
  return (
      "\n".join(
          [
              "set -eu",
              *baseline_commit_lines(workdir),
              f"git -C {wd} rev-parse HEAD > {out}",
          ]
      )
      + "\n"
  )


def build_baseline_verify_script(*, workdir: str, base_ref_path: str) -> str:
  """Build the bash that proves the tree is the one the patch was taken from.

  Recomputes the pre-agent baseline with :func:`baseline_commit_lines` — the
  sha is a pure function of the tree, so equality with the recorded base ref
  is the proof — then resets to it, giving the reset discipline its correct
  target (everything is tracked in the baseline, so ``clean -fd`` cannot eat a
  shipped-untracked file).

  On mismatch the script prints both shas and exits non-zero. The caller
  (``BaselineVerifyObserver``) turns that into a **failed run, not a graded
  one**: a tree that is not the patch's base is an environment fault, and
  grading it would score the agent zero for the operator's error.

  Args:
    workdir: In-container path of the instance's repo.
    base_ref_path: Where the recorded base ref was staged (read via ``cat``).

  Returns:
    The bash script text, newline-terminated.
  """
  wd = shlex.quote(workdir)
  ref = shlex.quote(base_ref_path)
  return (
      "\n".join(
          [
              "set -eu",
              *baseline_commit_lines(workdir),
              f'baseline="$(git -C {wd} rev-parse HEAD)"',
              f'expected="$(cat {ref})"',
              'if [ "$baseline" != "$expected" ]; then'
              ' echo "grading tree differs from the patch base:'
              ' recomputed $baseline, patch taken against $expected" >&2;'
              " exit 1; fi",
              # -c autocrlf: the reset is a checkout, and the line-ending
              # discipline (symmetric with extraction) must hold for it too.
              f"git -C {wd} -c core.autocrlf=false reset --hard HEAD",
              f"git -C {wd} clean -fd",
          ]
      )
      + "\n"
  )


def build_extraction_script(
    *,
    workdir: str,
    base_ref: str,
    output_path: str,
    exclude_globs: tuple[str, ...] = (),
    remove_nested_git: bool = True,
) -> str:
  """Build the in-container bash that extracts the agent's patch.

  The script produces a canonical, ``git apply``-able **text** diff of
  everything the repo at ``workdir`` gained since ``base_ref``, written as
  **raw bytes** to ``output_path``. New files are staged with ``git add -N``
  (intent-to-add) and the diff omits ``--binary``, so binary content is never
  serialized — the happy path is text-only. The output may still carry a
  bytes-free ``Binary files ... differ`` header for a binary change; the
  rollout runner strips those with :func:`strip_binary_hunks` before grading.

  The script itself is side-effecting only inside the container (it stages the
  worktree and removes stray nested ``.git`` dirs); it does not commit.

  Args:
    workdir: In-container path of the instance's repo.
    base_ref: The diff base — pass the instance's ``base_commit`` so the patch
      applies against the exact base the grader resets to (a post-setup commit
      is a deferred alternative).
    output_path: In-container path the diff is written to as raw bytes.
    exclude_globs: Build-noise denylist; each entry is a git pathspec suffix
      (e.g. ``pyproject.toml`` or ``*.toml``). Defaults to empty — the
      denylist is deferred (see ADR-0001).
    remove_nested_git: Remove stray nested ``.git`` dirs first so they are not
      staged as gitlinks that swallow the files inside them.

  Returns:
    The bash script text, newline-terminated.
  """
  wd = shlex.quote(workdir)
  out = shlex.quote(output_path)
  ref = shlex.quote(base_ref)
  env = " ".join(_ISOLATED_ENV)
  add_cfg = " ".join(_ADD_CONFIG)
  diff_cfg = " ".join(_DIFF_CONFIG)
  diff_flags = " ".join(_DIFF_FLAGS)
  excludes = "".join(
      f" {shlex.quote(f':(exclude){glob}')}" for glob in exclude_globs
  )

  own_git = shlex.quote(workdir + "/.git")
  lines = ["set -u"]  # bash: treat any unset variable as an error
  if remove_nested_git:
    # A stray nested .git (a dep the agent cloned, a fixture that ran git init)
    # would be staged as a single gitlink, silently swallowing the files inside
    # it and breaking apply. Remove them first.
    lines.append(
        f"find {wd} -type d -name .git -not -path {own_git}"
        " -prune -exec rm -rf {} + 2>/dev/null || true"
    )
  # Intent-to-add new files from the repo root (:/ ) so untracked files show in
  # the worktree diff as full additions, without staging binary content. Tracked
  # modifications/deletions need no staging — the worktree diff vs base_ref
  # captures them.
  lines.append(f"{env} git -C {wd} {add_cfg} add -N -- :/{excludes}")
  # Emit the text diff of the worktree vs base_ref as raw bytes. Redirection
  # writes bytes verbatim — no text round-trip. No --cached: with add -N, the
  # worktree diff carries the new files; --cached would show them empty.
  lines.append(f"{env} git -C {wd} {diff_cfg} diff {diff_flags} {ref} > {out}")
  return "\n".join(lines) + "\n"
