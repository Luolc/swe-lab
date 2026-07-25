"""Pluggable repository provisioning.

The annotation flow needs a working checkout of each instance's repo at its
``base_commit``. :class:`RepoProvider` is the abstraction;
:class:`GitCheckoutProvider` is the read-only implementation used now. A
future ``DockerProvider`` (using ``dockerhub_tag`` / ``before_repo_set_cmd``)
can slot in behind the same protocol without touching callers.

``GitCheckoutProvider`` keeps one bare *mirror* clone per repo and adds a
per-instance *worktree* checked out at ``base_commit``. Sharing a single object
store across instances of the same repo avoids re-cloning large repositories
once per task. Provisioning is idempotent: an existing checkout already at the
right commit is returned untouched, so it is cheap to call on every run.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
import shutil
import subprocess
import threading
from typing import override, Protocol

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
    """The ``owner/name`` identifier of the repository."""
    ...

  @property
  def base_commit(self) -> str:
    """The commit sha the task starts from."""
    ...

  @property
  def instance_id(self) -> str:
    """The unique identifier of the task instance."""
    ...


class RepoProvider(ABC):
  """Provisions a local working directory for a task instance.

  A behavior interface (ABC, per ADR-0002); providers implement it in-repo.
  """

  @abstractmethod
  def provision(self, instance: RepoInstance, *, variant: str = "") -> Path:
    """Return a path to a checkout of ``instance`` ready to read.

    Args:
      instance: The task instance to provision.
      variant: Optional label giving an isolated checkout so several runs
        of one instance can proceed concurrently without sharing a working
        directory.

    Returns:
      The path of the provisioned checkout.
    """
    ...


class GitError(RuntimeError):
  """A git subprocess exited non-zero."""


def _repo_slug(repo: str) -> str:
  """Turn ``owner/name`` into a filesystem-safe slug."""
  return repo.replace("/", "__")


@dataclass
class GitCheckoutProvider(RepoProvider):
  """Provider that clones and checks out ``base_commit`` into a local cache.

  Layout under ``cache_dir``::

      mirrors/<owner__name>.git   # one bare mirror per repo (shared objects)
      checkouts/<instance_id>/    # one worktree per instance @ base_commit

  Attributes:
    cache_dir: Gitignored root of the mirror/checkout cache.
    remote_base: Base URL repositories are cloned from.
  """

  cache_dir: Path = field(default_factory=repo_cache_dir)
  remote_base: str = "https://github.com"

  def __post_init__(self) -> None:
    self.cache_dir = Path(self.cache_dir)

  @property
  def mirrors_dir(self) -> Path:
    """The directory of bare mirror clones, one per repository."""
    return self.cache_dir / "mirrors"

  @property
  def checkouts_dir(self) -> Path:
    """The directory of per-instance worktree checkouts."""
    return self.cache_dir / "checkouts"

  def remote_url(self, repo: str) -> str:
    """Return the clone URL for ``repo``."""
    return f"{self.remote_base}/{repo}.git"

  def mirror_path(self, repo: str) -> Path:
    """Return the bare-mirror path for ``repo``."""
    return self.mirrors_dir / f"{_repo_slug(repo)}.git"

  def checkout_path(self, instance: RepoInstance, *, variant: str = "") -> Path:
    """Return the checkout path for ``instance`` (suffixed by ``variant``)."""
    name = instance.instance_id
    if variant:
      name = f"{name}__{variant}"
    return self.checkouts_dir / name

  @override
  def provision(self, instance: RepoInstance, *, variant: str = "") -> Path:
    """Return a worktree of ``instance.repo`` checked out at its base commit."""
    with _PROVISION_LOCK:
      mirror = self._ensure_mirror(instance.repo)
      return self._ensure_checkout(mirror, instance, variant=variant)

  # -- internals -------------------------------------------------------------

  def _git(
      self, *args: str, cwd: Path | None = None, timeout: float = 600.0
  ) -> str:
    # Always bound git ops: a stalled clone/fetch (network) must not hang the
    # whole pipeline forever.
    try:
      result = subprocess.run(
          ["git", *args],
          cwd=None if cwd is None else str(cwd),
          capture_output=True,
          text=True,
          check=False,
          timeout=timeout,
      )
    except subprocess.TimeoutExpired as exc:
      raise GitError(
          f"git {' '.join(args)} timed out after {timeout:.0f}s"
      ) from exc
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
      try:
        current = self._git("rev-parse", "HEAD", cwd=checkout)
      except GitError:
        # A stale worktree whose gitdir link is broken (e.g. the repo root was
        # moved/renamed): drop it and re-create from the mirror below.
        current = None
      if current == commit:
        return checkout
      if current is not None:
        self._ensure_commit(mirror, commit)
        _ = self._git("checkout", "--detach", commit, cwd=checkout)
        return checkout
      shutil.rmtree(checkout, ignore_errors=True)

    self._ensure_commit(mirror, commit)
    self.checkouts_dir.mkdir(parents=True, exist_ok=True)
    # Clear any dangling worktree registrations (a checkout dir deleted out
    # from under git, or stale entries after the repo was moved) so ``add``
    # self-heals instead of failing "missing but already registered".
    _ = self._git("worktree", "prune", cwd=mirror)
    _ = self._git(
        "worktree",
        "add",
        "--force",
        "--detach",
        str(checkout),
        commit,
        cwd=mirror,
    )
    return checkout
