"""Patch helpers shared by ``rollout`` (extract) and ``evaluation`` (apply).

Producing a git diff that re-applies cleanly with ``git apply`` is deceptively
error-prone; the grounded corner-case survey is in
[`docs/patch-extraction.md`](../../../docs/patch-extraction.md). This module is
the small, testable core of that survey:

- :func:`build_extraction_script` — the in-container bash that turns an agent's
  edits into a canonical, applyable diff (survey §7). It relies on committing
  the post-setup repo state as ``base_ref`` (survey §2.10) so environment/build
  mutations never enter the patch — which is why it needs **no** fragile
  ``:(exclude)`` denylist by default.
- :func:`strip_binary_hunks` — mirror Scale's Pro harness, which drops binary
  diff sections before ``git apply`` (survey §1); our eval must match it.
- :func:`is_effectively_empty` — an empty/no-op patch is a failed attempt, never
  a pass (survey §5.4).
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

  Mirrors ``strip_binary_hunks`` in Scale's ``swe_bench_pro_eval.py``: the Pro
  grader drops any ``diff --git`` section that contains a ``Binary files ...
  differ`` line or a ``GIT binary patch`` block before applying, so binary
  changes are never applied (and thus never grade). Our eval must strip the same
  way or a binary-containing patch that Scale would strip-then-apply could fail
  our strict single ``git apply`` (see docs/patch-extraction.md §1, §8).
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
  """Whether a patch has no applyable content (empty, whitespace, or no hunks).

  True for the empty string, whitespace-only text, and a patch that carries no
  ``diff --git`` section at all (e.g. one that was entirely binary and got
  stripped by :func:`strip_binary_hunks`). Callers treat this as a failed agent
  attempt rather than a resolved task (docs/patch-extraction.md §5.4).
  """
  return not patch.strip() or not _DIFF_HEADER.search(patch)


# --- Extraction side (runs inside the instance container) --------------------

# git plumbing config pinned for extraction. `git diff` output format is
# config-driven (prefixes, color, textconv, quoting, EOL); a leaked user/system
# gitconfig can silently produce a non-applyable diff. We neutralize all of it
# (docs/patch-extraction.md §3, §4B). NB: we deliberately do NOT use
# `--default-prefix` (git >= 2.41 only) — pinning `diff.noprefix` /
# `diff.mnemonicPrefix` to false gives the same `a/ b/` prefixes on any git.
_ISOLATED_ENV = (
    "GIT_CONFIG_GLOBAL=/dev/null",
    "GIT_CONFIG_SYSTEM=/dev/null",
    "GIT_CONFIG_NOSYSTEM=1",
    "GIT_PAGER=cat",
    "GIT_EXTERNAL_DIFF=",
)
_ADD_CONFIG = ("-c", "core.quotepath=false", "-c", "core.autocrlf=false")
_DIFF_CONFIG = (
    "-c",
    "core.quotepath=false",
    "-c",
    "core.autocrlf=false",
    "-c",
    "color.ui=never",
    "-c",
    "diff.noprefix=false",
    "-c",
    "diff.mnemonicPrefix=false",
    "-c",
    "diff.external=",
)
_DIFF_FLAGS = (
    "--cached",
    "--binary",
    "--no-color",
    "--no-textconv",
    "--no-ext-diff",
)


def build_extraction_script(
    *,
    workdir: str,
    base_ref: str,
    output_path: str,
    exclude_globs: tuple[str, ...] = (),
    remove_nested_git: bool = True,
) -> str:
  """Bash that extracts the agent's patch, for the instance container.

  Produces a canonical, ``git apply``-able diff of everything the repo at
  ``workdir`` gained since ``base_ref`` (survey §7), written as **raw bytes** to
  ``output_path``. ``base_ref`` should be the *post-setup* commit the rollout
  entryscript makes right before the agent runs, so environment/build mutations
  are already in the base and never enter the patch (survey §2.10) — hence no
  ``:(exclude)`` denylist is needed by default. ``exclude_globs`` is available
  for the rare instance that still needs it (e.g. mini-swe-agent #528); each is
  a git pathspec suffix, e.g. ``pyproject.toml`` or ``*.toml``.

  The script itself is side-effecting only inside the container (it stages the
  worktree and removes stray nested ``.git`` dirs); it does not commit.
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
  lines = ["set -u"]
  if remove_nested_git:
    # A stray nested .git (a dep the agent cloned, a fixture that ran git init)
    # would be staged as a single gitlink, silently swallowing the files inside
    # it and breaking apply. Remove them first (docs/patch-extraction.md §4A.8).
    lines.append(
        f"find {wd} -type d -name .git -not -path {own_git}"
        " -prune -exec rm -rf {} + 2>/dev/null || true"
    )
  # Stage new/modified/deleted from the repo root (:/ ) so untracked files are
  # captured (docs/patch-extraction.md §2, §4A.1/§4A.3).
  lines.append(f"{env} git -C {wd} {add_cfg} add -A -- :/{excludes}")
  # Emit the diff vs the post-setup base as raw bytes (docs/patch-extraction.md
  # §4B, §4D). Redirection writes bytes verbatim — no text round-trip.
  lines.append(f"{env} git -C {wd} {diff_cfg} diff {diff_flags} {ref} > {out}")
  return "\n".join(lines) + "\n"
