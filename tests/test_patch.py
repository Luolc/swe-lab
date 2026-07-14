"""Tests for :mod:`swebench_eval_lab.core.patch`.

The pure helpers (``strip_binary_hunks``, ``is_effectively_empty``) are unit
tested. ``build_extraction_script`` runs against a **real** temporary git repo
(git + bash are assumed present) so the corner cases from
docs/patch-extraction.md are checked end to end, including an extract -> apply
round-trip against a clean base.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess

import pytest

from swebench_eval_lab.core.patch import (
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


def test_extraction_binary_included_as_git_binary_patch(tmp_path: Path) -> None:
  repo, base = _init_repo(tmp_path)
  _ = (repo / "blob.bin").write_bytes(bytes(range(256)))  # has NUL bytes
  patch = _run_extraction(repo, base)
  # --binary emits an applyable binary block, not just "Binary files differ".
  assert "GIT binary patch" in patch


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
