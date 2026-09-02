"""Tests for :mod:`swe_lab.git.patch`.

The pure helpers (``strip_binary_hunks``, ``is_effectively_empty``) are unit
tested. ``build_extraction_script`` runs against a **real** temporary git repo
(git + bash are assumed present) so the corner cases from ADR-0001 are checked
end to end, including an extract -> apply round-trip against a clean base.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest

from swe_lab.git.patch import (
    build_baseline_script,
    build_extraction_script,
    is_effectively_empty,
    strip_binary_hunks,
)

# --- pure helpers ------------------------------------------------------------

_TEXT_DIFF = (
    "diff --git a/foo.py b/foo.py\n"
    "index e69de29..d95f3ad 100644\n"
    "--- a/foo.py\n"
    "+++ b/foo.py\n"
    "@@ -0,0 +1 @@\n"
    "+print('hi')\n"
)
_BINARY_DIFF = (
    "diff --git a/logo.png b/logo.png\n"
    "new file mode 100644\n"
    "index 0000000..a1b2c3d\n"
    "Binary files /dev/null and b/logo.png differ\n"
)
_GIT_BINARY_DIFF = (
    "diff --git a/blob.bin b/blob.bin\n"
    "index 0000000..a1b2c3d 100644\n"
    "GIT binary patch\n"
    "literal 4\n"
    "Lc$@aA00\n\n"
)


def test_strip_binary_hunks_keeps_text_drops_binary() -> None:
  patch = _TEXT_DIFF + _BINARY_DIFF + _GIT_BINARY_DIFF
  stripped = strip_binary_hunks(patch)
  assert "foo.py" in stripped
  assert "logo.png" not in stripped
  assert "GIT binary patch" not in stripped
  assert "blob.bin" not in stripped


def test_strip_binary_hunks_noop_on_text_and_empty() -> None:
  assert strip_binary_hunks(_TEXT_DIFF) == _TEXT_DIFF
  assert strip_binary_hunks("") == ""


def test_is_effectively_empty() -> None:
  assert is_effectively_empty("")
  assert is_effectively_empty("   \n\t\n")
  assert not is_effectively_empty(_TEXT_DIFF)
  # A binary-only patch is empty once its hunks are stripped.
  assert is_effectively_empty(strip_binary_hunks(_BINARY_DIFF))


# --- build_extraction_script (real git repo) ---------------------------------


def _git(repo: Path, *args: str) -> str:
  env = {
      "GIT_AUTHOR_NAME": "t",
      "GIT_AUTHOR_EMAIL": "t@t",
      "GIT_COMMITTER_NAME": "t",
      "GIT_COMMITTER_EMAIL": "t@t",
      "GIT_CONFIG_GLOBAL": "/dev/null",
      "GIT_CONFIG_SYSTEM": "/dev/null",
      "PATH": _PATH,
  }
  out = subprocess.run(
      ["git", "-C", str(repo), *args],
      capture_output=True,
      text=True,
      check=True,
      env=env,
  )
  return out.stdout.strip()


_PATH = os.environ.get("PATH", "/usr/bin:/bin")


def _init_repo(tmp_path: Path) -> tuple[Path, str]:
  """Init a repo with one committed file; return (repo, base_commit_sha)."""
  repo = tmp_path / "repo"
  repo.mkdir()
  _git(repo, "init", "-q", "-b", "main")
  _ = (repo / "app.py").write_text("def f():\n    return 1\n")
  _git(repo, "add", "-A")
  _git(repo, "commit", "-qm", "base")
  return repo, _git(repo, "rev-parse", "HEAD")


def _run_extraction(
    repo: Path, base_ref: str, *, exclude_globs: tuple[str, ...] = ()
) -> str:
  out_path = repo.parent / "patch.diff"
  script = build_extraction_script(
      workdir=str(repo),
      base_ref=base_ref,
      output_path=str(out_path),
      exclude_globs=exclude_globs,
  )
  subprocess.run(
      ["bash", "-c", script],
      check=True,
      capture_output=True,
      text=True,
      env={"PATH": _PATH},
  )
  return out_path.read_bytes().decode("utf-8", "replace")


def _run_baseline(repo: Path) -> str:
  """Commit the tree as the agent found it; return the baseline sha."""
  out_path = repo.parent / "base_ref.txt"
  script = build_baseline_script(workdir=str(repo), output_path=str(out_path))
  subprocess.run(
      ["bash", "-c", script],
      check=True,
      capture_output=True,
      text=True,
      env={"PATH": _PATH},
  )
  return out_path.read_text().strip()


def test_extraction_captures_new_modified_deleted(tmp_path: Path) -> None:
  repo, base = _init_repo(tmp_path)
  _ = (repo / "app.py").write_text("def f():\n    return 2\n")  # modified
  _ = (repo / "new.py").write_text("x = 1\n")  # untracked-new
  _ = (repo / "gone.py").write_text("old\n")
  _git(repo, "add", "gone.py")
  _git(repo, "commit", "-qm", "add gone")
  (repo / "gone.py").unlink()  # deleted (tracked at base? committed after base)

  patch = _run_extraction(repo, base)
  assert "diff --git a/app.py b/app.py" in patch
  assert "+    return 2" in patch
  assert "new file mode" in patch and "b/new.py" in patch  # new file present
  assert "x = 1" in patch


def test_extraction_captures_committed_work_since_base(tmp_path: Path) -> None:
  """Agent that *commits* its work is still captured (diff vs base_ref)."""
  repo, base = _init_repo(tmp_path)
  _ = (repo / "feature.py").write_text("y = 2\n")
  _git(repo, "add", "-A")
  _git(repo, "commit", "-qm", "agent commit")  # committed, clean worktree
  patch = _run_extraction(repo, base)
  assert "b/feature.py" in patch and "y = 2" in patch


def test_extraction_empty_when_no_changes(tmp_path: Path) -> None:
  repo, base = _init_repo(tmp_path)
  patch = _run_extraction(repo, base)
  assert is_effectively_empty(patch)


def test_a_stub_agent_produces_an_empty_patch_on_a_dirty_image(
    tmp_path: Path,
) -> None:
  """No agent ran, so the patch is empty — even on a dirty image (ADR-0014).

  The named enforcement of the baseline default. Measured 2026-09-01: a
  SWE-bench Pro image ships an untracked Redis append-only-file directory, so
  the ``git add -N`` of facet 3 stages the image's own state as if the agent
  had written it — 15,710 insertions, 166 KB, from a run whose agent executed
  nothing. ADR-0001's 2026-08-25 amendment described that failure and shipped
  the remedy **off**, which is why it kept happening; this test is what makes
  the default load-bearing rather than a sentence in a document.
  """
  repo, base_commit = _init_repo(tmp_path)
  # The image ships untracked state — as NodeBB's does — and no agent runs.
  (repo / "appendonlydir").mkdir()
  _ = (repo / "appendonlydir" / "appendonly.aof.1.incr.aof").write_text(
      "SELECT\n*2\n" * 500
  )

  # Guard against a vacuous pass: against `base_commit` the image's own files
  # *are* folded in, which is the defect this default exists to prevent.
  assert not is_effectively_empty(_run_extraction(repo, base_commit))

  # Against the tree the agent found, an agent that did nothing changed nothing.
  assert is_effectively_empty(_run_extraction(repo, _run_baseline(repo)))


def test_the_baseline_still_captures_the_work_of_an_agent_that_did_edit(
    tmp_path: Path,
) -> None:
  """The default must not buy an empty patch by suppressing real edits."""
  repo, _ = _init_repo(tmp_path)
  (repo / "appendonlydir").mkdir()
  _ = (repo / "appendonlydir" / "appendonly.aof").write_text("state\n")
  base_ref = _run_baseline(repo)  # the tree as the agent finds it

  _ = (repo / "app.py").write_text("def f():\n    return 2\n")  # the agent
  _ = (repo / "new.py").write_text("x = 1\n")

  patch = _run_extraction(repo, base_ref)
  assert "+    return 2" in patch
  assert "b/new.py" in patch and "x = 1" in patch
  assert "appendonlydir" not in patch  # the image's state stays out


def test_extraction_removes_nested_git(tmp_path: Path) -> None:
  """A nested repo must not leak as a gitlink; its files become normal adds."""
  repo, base = _init_repo(tmp_path)
  nested = repo / "vendored"
  nested.mkdir()
  _git(nested, "init", "-q", "-b", "main")
  _ = (nested / "inner.py").write_text("z = 3\n")
  _git(nested, "add", "-A")
  _git(nested, "commit", "-qm", "inner")

  patch = _run_extraction(repo, base)
  assert "160000" not in patch  # no gitlink
  assert "Subproject commit" not in patch
  assert "b/vendored/inner.py" in patch  # inner file captured as a normal add


def test_extraction_omits_binary_content(tmp_path: Path) -> None:
  """Happy path: binary content is never serialized (no --binary).

  A binary change appears only as a bytes-free ``Binary files ... differ``
  header (never an applyable ``GIT binary patch`` block); ``strip_binary_hunks``
  then removes that section, leaving a clean text-only patch.
  """
  repo, base = _init_repo(tmp_path)
  _ = (repo / "app.py").write_text("def f():\n    return 9\n")  # text change
  _ = (repo / "blob.bin").write_bytes(bytes(range(256)))  # new binary (NULs)
  patch = _run_extraction(repo, base)
  assert "GIT binary patch" not in patch  # no applyable binary block
  assert "Binary files" in patch  # only a bytes-free marker

  clean = strip_binary_hunks(patch)
  assert "blob.bin" not in clean  # binary section dropped
  assert "b/app.py" in clean and "+    return 9" in clean  # text kept


def test_extraction_exclude_globs(tmp_path: Path) -> None:
  repo, base = _init_repo(tmp_path)
  _ = (repo / "pyproject.toml").write_text("[x]\n")
  _ = (repo / "real.py").write_text("k = 1\n")
  patch = _run_extraction(repo, base, exclude_globs=("*.toml",))
  assert "pyproject.toml" not in patch
  assert "b/real.py" in patch


def test_extraction_roundtrips_through_git_apply(tmp_path: Path) -> None:
  """The extracted patch applies cleanly onto a fresh base checkout."""
  repo, base = _init_repo(tmp_path)
  _ = (repo / "app.py").write_text("def f():\n    return 42\n")
  _ = (repo / "new.py").write_text("added = True\n")
  patch = _run_extraction(repo, base)

  # Fresh clone at base, then apply — mirrors what evaluation does.
  clone = tmp_path / "clone"
  subprocess.run(
      ["git", "clone", "-q", str(repo), str(clone)],
      check=True,
      capture_output=True,
      env={"PATH": _PATH},
  )
  _git(clone, "checkout", "-q", base)
  patch_file = tmp_path / "p.diff"
  _ = patch_file.write_bytes(patch.encode("utf-8"))
  _git(clone, "apply", "-v", str(patch_file))
  assert (clone / "app.py").read_text() == "def f():\n    return 42\n"
  assert (clone / "new.py").read_text() == "added = True\n"


if __name__ == "__main__":
  raise SystemExit(pytest.main([__file__, "-v"]))


# ─── safe.directory travels with every engine git command (#244) ─────────────


def test_every_engine_git_command_carries_the_scoped_ownership_grant():
  """The grant must ride the invocation itself, or nothing can grant it.

  Isolation disables `git config --global` — the documented safe.directory
  channel — so without this, extraction, the purge and the baseline all fail
  on an image whose repo is owned by a different UID, none of them naming
  ownership as the cause.
  """
  from swe_lab.git.history import build_purge_script
  from swe_lab.git.patch import (
      build_baseline_script,
      build_baseline_verify_script,
      build_extraction_script,
      isolated_git_env,
  )

  env = isolated_git_env("/app")
  assert "GIT_CONFIG_KEY_0=safe.directory" in env
  assert "GIT_CONFIG_VALUE_0=/app" in env
  # Scoped to the instance's checkout, never the wildcard — `*` would also
  # disarm the check for any repo the agent happened to clone.
  assert not any("*" in a for a in env)
  # The isolation that made the grant necessary is still on.
  assert "GIT_CONFIG_GLOBAL=/dev/null" in env

  scripts = {
      "extract": build_extraction_script(
          workdir="/app", base_ref="base", output_path="out.diff"
      ),
      "baseline": build_baseline_script(workdir="/app", output_path="sha.txt"),
      "verify": build_baseline_verify_script(
          workdir="/app", base_ref_path="sha.txt"
      ),
      "purge": build_purge_script(workdir="/app"),
  }
  # The purge exports the env once in its preamble; every later git inherits
  # it from the shell. The other three prefix each invocation instead, so for
  # them the assertion is PER LINE — the first version of this test checked
  # mere presence and let a bare `git rev-parse` through, which then failed
  # live on an ownership-affected repo while the lines around it succeeded.
  assert "export GIT_CONFIG_KEY_0=safe.directory" in scripts.pop("purge")
  for name, script in scripts.items():
    git_lines = [
        line
        for line in script.splitlines()
        if " git " in f" {line} " and "echo" not in line
    ]
    assert git_lines, name
    for line in git_lines:
      assert "GIT_CONFIG_VALUE_0=/app" in line, (name, line)


def test_the_baseline_commands_are_isolated_like_every_other():
  # They previously ran bare: an image ~/.gitconfig could skew the baseline
  # (autocrlf on add) while extraction was isolated — the two would then
  # disagree about the same tree.
  from swe_lab.git.patch import baseline_commit_lines

  for line in baseline_commit_lines("/app"):
    assert "GIT_CONFIG_GLOBAL=/dev/null" in line
    assert "GIT_CONFIG_VALUE_0=/app" in line
