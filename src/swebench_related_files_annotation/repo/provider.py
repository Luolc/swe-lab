"""Pluggable repository provisioning.

The annotation flow needs a working checkout of each instance's repo at its
``base_commit``. :class:`RepoProvider` is the abstraction; :class:`GitCheckout`
``Provider`` is the read-only implementation used now. A future
``DockerProvider`` (using ``dockerhub_tag`` / ``before_repo_set_cmd``) can slot
in behind the same protocol without touching callers.

``GitCheckoutProvider`` keeps one bare *mirror* clone per repo and adds a
per-instance *worktree* checked out at ``base_commit``. Sharing a single object
store across instances of the same repo avoids re-cloning large repositories
once per task. Provisioning is idempotent: an existing checkout already at the
right commit is returned untouched, so it is cheap to call on every run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import subprocess
import threading
from typing import Protocol

from ..paths import repo_cache_dir

# Serializes the mutating git operations (mirror clone, worktree add) so several
# threads provisioning concurrently — e.g. parallel repeats of one instance —
# don't race on a mirror's worktree registry. The mutations are fast relative to
# the agent call that follows, so a single lock is fine.
_PROVISION_LOCK = threading.Lock()


class RepoInstance(Protocol):
  """The instance fields a provider needs — kept dataset-agnostic.

  Declared read-only so frozen-dataclass records (e.g. ``SweBenchProInstance``)
  satisfy the protocol.
  """

  @property
  def repo(self) -> str:
    ...

  @property
  def base_commit(self) -> str:
    ...

  @property
  def instance_id(self) -> str:
    ...


class RepoProvider(Protocol):
  """Provisions a local working directory for a task instance."""

  def provision(self, instance: RepoInstance, *, variant: str = "") -> Path:
    """Return a path to a checkout of ``instance`` ready to read.

    ``variant`` gives an isolated checkout so several runs of one instance can
    proceed concurrently without sharing a working directory.
    """
    ...


class GitError(RuntimeError):
  """A git subprocess exited non-zero."""


def _repo_slug(repo: str) -> str:
  """Turn ``owner/name`` into a filesystem-safe slug."""
  return repo.replace("/", "__")


@dataclass
class GitCheckoutProvider:
  """Clone + checkout ``base_commit`` into a gitignored local cache.

  Layout under ``cache_dir``::

      mirrors/<owner__name>.git   # one bare mirror per repo (shared objects)
      checkouts/<instance_id>/    # one worktree per instance @ base_commit
  """

  cache_dir: Path = field(default_factory=repo_cache_dir)
  remote_base: str = "https://github.com"

  def __post_init__(self) -> None:
    self.cache_dir = Path(self.cache_dir)

  @property
  def mirrors_dir(self) -> Path:
    return self.cache_dir / "mirrors"

  @property
  def checkouts_dir(self) -> Path:
    return self.cache_dir / "checkouts"

  def remote_url(self, repo: str) -> str:
    return f"{self.remote_base}/{repo}.git"

  def mirror_path(self, repo: str) -> Path:
    return self.mirrors_dir / f"{_repo_slug(repo)}.git"

  def checkout_path(self, instance: RepoInstance, *, variant: str = "") -> Path:
    name = instance.instance_id
    if variant:
      name = f"{name}__{variant}"
    return self.checkouts_dir / name

  def provision(self, instance: RepoInstance, *, variant: str = "") -> Path:
    """Return a worktree of ``instance.repo`` checked out at its base commit."""
    with _PROVISION_LOCK:
      mirror = self._ensure_mirror(instance.repo)
      return self._ensure_checkout(mirror, instance, variant=variant)

  # -- internals -------------------------------------------------------------

  def _git(self, *args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=None if cwd is None else str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
      raise GitError(
          f"git {' '.join(args)} failed ({result.returncode}):\n"
          f"{result.stderr.strip()}"
      )
    return result.stdout.strip()

  def _ensure_mirror(self, repo: str) -> Path:
    mirror = self.mirror_path(repo)
    if (mirror / "HEAD").is_file():
      return mirror
    self.mirrors_dir.mkdir(parents=True, exist_ok=True)
    _ = self._git("clone", "--mirror", self.remote_url(repo), str(mirror))
    return mirror

  def _mirror_has_commit(self, mirror: Path, commit: str) -> bool:
    try:
      _ = self._git("cat-file", "-e", f"{commit}^{{commit}}", cwd=mirror)
    except GitError:
      return False
    return True

  def _ensure_commit(self, mirror: Path, commit: str) -> None:
    if self._mirror_has_commit(mirror, commit):
      return
    # The mirror may predate this commit; refresh refs, then try the sha
    # directly (works when the server allows reachable-SHA fetches).
    _ = self._git("remote", "update", "--prune", cwd=mirror)
    if self._mirror_has_commit(mirror, commit):
      return
    _ = self._git("fetch", "origin", commit, cwd=mirror)
    if not self._mirror_has_commit(mirror, commit):
      raise GitError(f"Commit {commit} not available in mirror {mirror}.")

  def _ensure_checkout(
      self, mirror: Path, instance: RepoInstance, *, variant: str = ""
  ) -> Path:
    checkout = self.checkout_path(instance, variant=variant)
    commit = instance.base_commit

    if (checkout / ".git").exists():
      current = self._git("rev-parse", "HEAD", cwd=checkout)
      if current == commit:
        return checkout
      self._ensure_commit(mirror, commit)
      _ = self._git("checkout", "--detach", commit, cwd=checkout)
      return checkout

    self._ensure_commit(mirror, commit)
    self.checkouts_dir.mkdir(parents=True, exist_ok=True)
    _ = self._git(
        "worktree", "add", "--detach", str(checkout), commit, cwd=mirror
    )
    return checkout
