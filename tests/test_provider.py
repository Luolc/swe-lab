"""Tests for GitCheckoutProvider against a local (network-free) remote."""

from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from swe_lab.core.datasets.swebench_pro import (
    COLUMNS,
    SweBenchProInstance,
)
from swe_lab.core.repo.provider import GitCheckoutProvider


def _git(*args: str, cwd: Path) -> str:
  env_flags = [
      "-c",
      "user.email=test@example.com",
      "-c",
      "user.name=Test",
  ]
  result = subprocess.run(
      ["git", *env_flags, *args],
      cwd=str(cwd),
      capture_output=True,
      text=True,
      check=True,
  )
  return result.stdout.strip()


def _instance(base_commit: str) -> SweBenchProInstance:
  raw = dict.fromkeys(COLUMNS, "")
  raw.update(
      repo="acme/widget",
      instance_id="instance_acme__widget-1",
      base_commit=base_commit,
      repo_language="python",
      fail_to_pass="[]",
      pass_to_pass="[]",
      issue_specificity="[]",
      issue_categories="[]",
      selected_test_files_to_run="[]",
  )
  return SweBenchProInstance.from_raw(raw)


@pytest.fixture
def remote(tmp_path: Path) -> tuple[Path, str, str]:
  """Bare remote repo with two commits; returns (remote_base, c1, c2)."""
  work = tmp_path / "work"
  work.mkdir()
  _git("init", "-b", "main", cwd=work)
  (work / "file.txt").write_text("first\n")
  _git("add", ".", cwd=work)
  _git("commit", "-m", "first", cwd=work)
  c1 = _git("rev-parse", "HEAD", cwd=work)
  (work / "file.txt").write_text("second\n")
  _git("commit", "-am", "second", cwd=work)
  c2 = _git("rev-parse", "HEAD", cwd=work)

  remote_base = tmp_path / "remotes"
  bare = remote_base / "acme" / "widget.git"
  bare.parent.mkdir(parents=True)
  _git("clone", "--bare", str(work), str(bare), cwd=tmp_path)
  return remote_base, c1, c2


def test_provision_checks_out_base_commit(
    tmp_path: Path, remote: tuple[Path, str, str]
) -> None:
  remote_base, c1, _ = remote
  provider = GitCheckoutProvider(
      cache_dir=tmp_path / "cache", remote_base=str(remote_base)
  )

  checkout = provider.provision(_instance(c1))

  assert (checkout / "file.txt").read_text() == "first\n"
  assert _git("rev-parse", "HEAD", cwd=checkout) == c1
  # Mirror is shared, not a per-instance full clone.
  assert provider.mirror_path("acme/widget").is_dir()


def test_provision_is_idempotent(
    tmp_path: Path, remote: tuple[Path, str, str]
) -> None:
  remote_base, c1, _ = remote
  provider = GitCheckoutProvider(
      cache_dir=tmp_path / "cache", remote_base=str(remote_base)
  )

  first = provider.provision(_instance(c1))
  second = provider.provision(_instance(c1))
  assert first == second
  assert _git("rev-parse", "HEAD", cwd=second) == c1


def test_provision_reuses_dir_when_commit_changes(
    tmp_path: Path, remote: tuple[Path, str, str]
) -> None:
  remote_base, c1, c2 = remote
  provider = GitCheckoutProvider(
      cache_dir=tmp_path / "cache", remote_base=str(remote_base)
  )

  provider.provision(_instance(c1))
  checkout = provider.provision(_instance(c2))  # same instance_id, new commit

  assert _git("rev-parse", "HEAD", cwd=checkout) == c2
  assert (checkout / "file.txt").read_text() == "second\n"


def test_variant_gives_isolated_checkouts(
    tmp_path: Path, remote: tuple[Path, str, str]
) -> None:
  # Two variants of the same instance must be separate worktrees (so concurrent
  # runs don't share a working directory), both at the commit.
  remote_base, c1, _ = remote
  provider = GitCheckoutProvider(
      cache_dir=tmp_path / "cache", remote_base=str(remote_base)
  )

  default = provider.provision(_instance(c1))
  variant_a = provider.provision(_instance(c1), variant="run1")
  variant_b = provider.provision(_instance(c1), variant="run2")

  assert len({default, variant_a, variant_b}) == 3
  for checkout in (variant_a, variant_b):
    assert _git("rev-parse", "HEAD", cwd=checkout) == c1
    assert (checkout / "file.txt").read_text() == "first\n"
