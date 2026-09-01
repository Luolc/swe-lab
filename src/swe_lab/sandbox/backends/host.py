"""A sandbox that drives Docker from the host (A-host).

The host runs ``docker create`` → ``start`` → one or more ``exec`` → ``rm``
against one persistent container, with the workspace directory bind-mounted in.
A persistent container (rather than one throwaway ``docker run`` per action) is
what lets setup, the main action, and any on-error probe run as separate steps
against the *same* live container.

Per ADR-0003 the sandbox is up-first: ``up`` creates the container, then
``mount`` transfers the merged staging set into it. Workspace-relative mounts
are written to the bind-mounted host directory (visible in the container free);
an absolute-path read-only asset (e.g. the pinned agent binary, outside the
workspace) is ``docker cp``'d into the container and made read-only there.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import json
import logging
import os
import posixpath
import subprocess
import tempfile
import time
from typing import override

from etils import epath

from ..errors import SandboxError
from ..mounts import Mount
from ..observer import SandboxObserver
from ..result import Contribution, qualified_name
from ..sandbox import ExecResult, Sandbox, SandboxFs, WORKSPACE_ENV
from ..spec import SandboxSpec

_logger = logging.getLogger(__name__)

# The instance images are linux/amd64; on Apple Silicon they run emulated.
DEFAULT_PLATFORM = "linux/amd64"
# Pulls can be slow on a cold runner; create/start/rm are quick.
_PULL_TIMEOUT_S = 3600.0
_DOCKER_TIMEOUT_S = 120.0

# Every container this sandbox creates carries these labels, so a container
# that ever escapes teardown is findable and removable in one command:
#   docker rm -f $(docker ps -aq --filter label=swe-lab)
_OWNER_LABEL = "swe-lab"
_INSTANCE_LABEL = "swe-lab-instance"

# Mapped on every container via ``--add-host`` so an in-container process can
# always reach a host-side service (the optional proxy-capture recorder) at
# ``host.docker.internal``. Harmless when nothing on the host is listening.
_HOST_GATEWAY = "host.docker.internal:host-gateway"


@dataclass
class DockerHostSandbox(Sandbox):
  """One persistent Docker container, driven from the host.

  A single-run, stateful object (the container id accumulates on ``up``);
  construct a fresh one per run.

  The network/env settings are construction-time because they are properties
  of the composition using the sandbox: a solving run needs the network and a
  credential, a grading run needs neither.

  Attributes:
    spec: The run context (image and instance id) to realize.
    workspace: The host directory bind-mounted into the container.
    platform: The ``--platform`` value for pull and create.
    network: Whether the container gets network access (``--network none``
      when False).
    pull: Whether to pull the image before creating the container.
    mount_at: The path the workspace is bind-mounted to inside the container;
      also the value of the ``SANDBOX_WORKSPACE`` variable every exec sees.
    shell: The interpreter each ``run_script`` / ``run_command`` and the
      keep-alive entrypoint use (default ``/bin/bash``; set to ``/bin/sh`` for
      an image without bash).
    env: Variables set in the container as ``KEY=VALUE`` on each exec.
    pass_env: Variables inherited by reference from the host process: each key
      is the name the container sees, each value the host variable it is read
      from. Only the container-side **name** reaches the ``docker`` command
      line (``-e NAME``); the value travels in the Docker CLI's own
      environment, so it never appears in the argv, process list, or logs. A
      host variable that is not set is warned about and skipped — the
      container never falls back to inheriting the name ambiently.
    reuse: Allow ``up`` to run in a non-empty workspace.
  """

  spec: SandboxSpec
  workspace: epath.Path
  platform: str = DEFAULT_PLATFORM
  network: bool = True
  pull: bool = True
  mount_at: str = "/workspace"
  shell: str = "/bin/bash"
  env: Mapping[str, str] = field(default_factory=dict)
  pass_env: Mapping[str, str] = field(default_factory=dict)
  reuse: bool = False
  _container: str = field(default="", init=False, repr=False)
  # How long the image pull took, recorded by ``up`` for the metrics observer:
  # only the backend knows where the pull ends and the container setup begins.
  # ``None`` when no pull ran.
  _pull_seconds: float | None = field(default=None, init=False, repr=False)

  # --- lifecycle -----------------------------------------------------------

  @override
  def up(self) -> None:
    """Prepare the workspace, then pull, create, and start the container.

    Cleans up its own partial state: if ``start`` fails after ``create``
    succeeded, the created container is removed before raising.

    Raises:
      SandboxError: On a non-empty workspace (without ``reuse``), a missing
        Docker CLI, or a pull/create/start failure.
    """
    self.workspace.mkdir(parents=True, exist_ok=True)
    if not self.reuse and any(self.workspace.iterdir()):
      raise SandboxError(
          f"workspace {self.workspace} is not empty; pass reuse=True to run "
          "in it anyway"
      )
    if self.pull:
      pull_started = time.monotonic()
      self._pull(self.spec.image_ref)
      self._pull_seconds = time.monotonic() - pull_started
    create_args = ["create", "--platform", self.platform]
    if not self.network:
      create_args += ["--network", "none"]
    create_args += ["-v", f"{self.workspace}:{self.mount_at}"]
    create_args += ["--add-host", _HOST_GATEWAY]
    for key, value in self.env.items():
      create_args += ["-e", f"{key}={value}"]
    create_env, passed = self._pass_env()
    for key in passed:
      create_args += ["-e", key]
    create_args += [
        "--label",
        f"{_OWNER_LABEL}=1",
        "--label",
        f"{_INSTANCE_LABEL}={self.spec.instance_id}",
        "--entrypoint",
        self.shell,
        self.spec.image_ref,
        "-c",
        "sleep infinity",
    ]
    created = self._docker(
        create_args, timeout=_DOCKER_TIMEOUT_S, env=create_env
    )
    if created.returncode != 0:
      raise SandboxError(
          f"docker create for {self.spec.image_ref} failed:\n"
          f"{created.stderr[-2000:]}"
      )
    handle = created.stdout.strip()
    started = self._docker(["start", handle], timeout=_DOCKER_TIMEOUT_S)
    if started.returncode != 0:
      self._container = handle
      self.down()  # remove our own partial state before surfacing
      raise SandboxError(
          f"docker start for {self.spec.image_ref} failed:\n"
          f"{started.stderr[-2000:]}"
      )
    self._container = handle

  @override
  def down(self) -> None:
    """Remove the container, best-effort; never raises."""
    if not self._container:
      return
    try:
      removed = self._docker(
          ["rm", "-f", self._container], timeout=_DOCKER_TIMEOUT_S
      )
    except SandboxError as exc:
      _logger.warning("docker teardown failed (swallowed): %s", exc)
      self._container = ""
      return
    if removed.returncode != 0:
      _logger.warning(
          "docker teardown failed (swallowed): %s", removed.stderr.strip()
      )
    self._container = ""

  @override
  def observers(self) -> Sequence[SandboxObserver]:
    """Contribute this backend's own observers (ADR-0007 §3, backend source).

    Runtime metrics only. The agent binary used to be here too, hardcoded to
    one harness; it now arrives through the provisioning seam instead — the
    task declares what it needs and ``asset_observer`` (inherited: the mount
    answer, which is what a container requires) places it. So a grading
    container no longer carries an agent binary it never execs.
    """
    return (HostMetricsObserver(sandbox=self),)

  @override
  def fetch(self, name: str, dest: epath.PathLike) -> None:
    """Copy a produced workspace file out to a host path (near-zero on host).

    The workspace is bind-mounted, so the file already exists on the host; this
    is a host-to-host copy (a no-op when ``dest`` is that same file).

    Args:
      name: The artifact's workspace-relative filename.
      dest: The host path to write it to (parents created).
    """
    src = self.workspace / name
    dest = epath.Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.resolve() != dest.resolve():
      _ = epath.Path(src).copy(dest, overwrite=True)

  # --- files (SandboxFs) ---------------------------------------------------

  @override
  def read(self, name: str) -> bytes:
    """Read a workspace file's bytes from the bind-mounted host directory."""
    return (self.workspace / name).read_bytes()

  @override
  def exists(self, name: str) -> bool:
    """Whether a workspace file exists."""
    return (self.workspace / name).is_file()

  @override
  def write(self, name: str, data: bytes, *, executable: bool = False) -> None:
    """Write an ad-hoc workspace file (visible in the container at once)."""
    dest = self.workspace / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    _ = dest.write_bytes(data)
    if executable:
      os.chmod(dest, 0o755)

  # --- mounts (transfer) ---------------------------------------------------

  @override
  def _put_bytes(self, target: str, data: bytes, mount: Mount) -> None:
    if not target.startswith("/"):
      dest = self.workspace / target
      dest.parent.mkdir(parents=True, exist_ok=True)
      _ = dest.write_bytes(data)
      os.chmod(dest, mount.mode)
      return
    with tempfile.NamedTemporaryFile(dir=self.workspace, delete=False) as tmp:
      _ = tmp.write(data)
      staged = epath.Path(tmp.name)
    try:
      self._cp_into(staged, target, mount)
    finally:
      staged.unlink(missing_ok=True)

  @override
  def _put_file(self, target: str, src: epath.PathLike, mount: Mount) -> None:
    if not target.startswith("/"):
      dest = self.workspace / target
      dest.parent.mkdir(parents=True, exist_ok=True)
      _ = epath.Path(src).copy(dest, overwrite=True)
      os.chmod(dest, mount.mode)
      return
    self._cp_into(src, target, mount)

  def _cp_into(self, src: epath.PathLike, target: str, mount: Mount) -> None:
    """Copy a host file to an absolute container path and fix its mode."""
    # `docker cp` refuses a destination whose parent directory does not exist
    # in the container, and an instance image has no reason to carry e.g.
    # /opt/claude-code — the mounted asset's directory is ours to create.
    parent = posixpath.dirname(target.rstrip("/")) or "/"
    made = self._docker(
        ["exec", self._container, "mkdir", "-p", parent],
        timeout=_DOCKER_TIMEOUT_S,
    )
    if made.returncode != 0:
      raise SandboxError(
          f"mkdir -p of {parent!r} for mount {target!r} failed:\n"
          f"{made.stderr[-2000:]}"
      )
    copied = self._docker(
        ["cp", str(src), f"{self._container}:{target}"],
        timeout=_DOCKER_TIMEOUT_S,
    )
    if copied.returncode != 0:
      raise SandboxError(
          f"docker cp to {target!r} failed:\n{copied.stderr[-2000:]}"
      )
    chmodded = self._docker(
        ["exec", self._container, "chmod", format(mount.mode, "o"), target],
        timeout=_DOCKER_TIMEOUT_S,
    )
    if chmodded.returncode != 0:
      raise SandboxError(
          f"chmod of mounted {target!r} failed:\n{chmodded.stderr[-2000:]}"
      )

  # --- exec ----------------------------------------------------------------

  @override
  def run_script(
      self,
      name: str,
      *,
      timeout: float,
      env: Mapping[str, str] | None = None,
  ) -> ExecResult:
    """Run ``$SANDBOX_WORKSPACE/<name>`` under the shell in the live container.

    A persisted file on disk, not stdin, so the exact script survives for audit
    and references workspace files only through the variable.

    Args:
      name: The script's workspace-relative filename.
      timeout: Seconds before the exec process is killed (the container stays
        up so later runs remain possible).
      env: Extra ``KEY=VALUE`` variables for this run only.

    Returns:
      The script's exit status and output; exit code 124 on timeout.
    """
    return self._exec(
        [self.shell, f"{self.mount_at}/{name}"],
        timeout=timeout,
        env=env,
    )

  @override
  def run_command(
      self,
      command: str,
      *,
      timeout: float,
      env: Mapping[str, str] | None = None,
  ) -> ExecResult:
    """Run an inline command (``<shell> -c command``) in the live container."""
    return self._exec(
        [self.shell, "-c", command],
        timeout=timeout,
        env=env,
    )

  def _exec(
      self,
      inner: Sequence[str],
      *,
      timeout: float,
      env: Mapping[str, str] | None,
  ) -> ExecResult:
    """Run one ``docker exec`` with ``SANDBOX_WORKSPACE`` and extra env set."""
    if not self._container:
      raise SandboxError("sandbox is not live yet")
    args = ["exec", "-e", f"{WORKSPACE_ENV}={self.mount_at}"]
    for key, value in (env or {}).items():
      args += ["-e", f"{key}={value}"]
    args += [self._container, *inner]
    try:
      done = subprocess.run(
          ["docker", *args],
          capture_output=True,
          text=True,
          timeout=timeout,
          check=False,
      )
      return ExecResult(done.returncode, done.stdout, done.stderr)
    except subprocess.TimeoutExpired as exc:
      err = exc.stderr if isinstance(exc.stderr, str) else ""
      return ExecResult(124, "", err, timed_out=True)
    except FileNotFoundError as exc:
      raise SandboxError("docker CLI not found on PATH") from exc

  # --- helpers -------------------------------------------------------------

  def _pull(self, image_ref: str) -> None:
    pulled = self._docker(
        ["pull", "--platform", self.platform, image_ref],
        timeout=_PULL_TIMEOUT_S,
    )
    if pulled.returncode != 0:
      raise SandboxError(
          f"docker pull {image_ref} failed:\n{pulled.stderr[-2000:]}"
      )

  def _pass_env(self) -> tuple[dict[str, str], list[str]]:
    """Resolve ``pass_env`` into the Docker CLI's environment and its names.

    The rename happens here rather than on the argv: ``docker -e NAME`` copies
    ``NAME`` out of the CLI's *own* environment, so putting the host
    variable's value under the container-side name in that environment is what
    carries a renamed variable across without ever spelling the value.

    Returns:
      The environment to run ``docker create`` with, and the container-side
      names to pass as ``-e NAME``.
    """
    resolved = dict(os.environ)
    names: list[str] = []
    for name, source in self.pass_env.items():
      value = os.environ.get(source)
      if value is None:
        _logger.warning("pass_env source %s is not set on the host", source)
        continue
      resolved[name] = value
      names.append(name)
    return resolved, names

  def _docker(
      self,
      args: list[str],
      *,
      timeout: float,
      env: Mapping[str, str] | None = None,
  ) -> subprocess.CompletedProcess[str]:
    """Run one ``docker`` subcommand, capturing output.

    Args:
      args: The ``docker`` arguments (without the leading ``docker``).
      timeout: Seconds before the command is killed.
      env: The environment to run the CLI with; ours when omitted.

    Returns:
      The completed process.

    Raises:
      SandboxError: If the Docker CLI is missing or the command times out.
    """
    try:
      return subprocess.run(
          ["docker", *args],
          capture_output=True,
          text=True,
          timeout=timeout,
          env=None if env is None else dict(env),
          check=False,
      )
    except FileNotFoundError as exc:
      raise SandboxError("docker CLI not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
      raise SandboxError(
          f"docker {args[0]} timed out after {timeout}s"
      ) from exc


# The metric namespace for backend-contributed runtime metrics; distinct from
# the eval method's ``eval.*`` and any harness's by construction.
_METRIC_NAMESPACE = "sandbox"
# cgroup v2 / v1 locations of the container's cumulative memory peak and OOM
# counters, read from *inside* the container so the right cgroup is implied.
_CGROUP_PEAK_FILES = (
    "/sys/fs/cgroup/memory.peak",
    "/sys/fs/cgroup/memory/memory.max_usage_in_bytes",
)
_CGROUP_EVENTS_FILE = "/sys/fs/cgroup/memory.events"


@dataclass
class HostMetricsObserver(SandboxObserver):
  """Collect container runtime metrics while the sandbox is still live.

  Single-run, like every stateful observer. Reads through the backend handle,
  not through ``SandboxFs`` — these numbers live in Docker and the container's
  cgroup, not in the workspace, which is exactly why only the backend can
  contribute this observer (ADR-0007 §3).

  Metrics (all namespaced ``sandbox.``; an unreadable metric is **omitted**,
  never emitted as ``0.0`` — absent means unmeasured):

  - ``sandbox.setup_seconds`` — wall clock from just before ``up`` to just
    after the mounts landed, minus any image pull;
  - ``sandbox.pull_seconds`` — the image pull alone, when one ran;
  - ``sandbox.peak_memory_bytes`` — the cgroup's cumulative peak
    (v2 ``memory.peak``, v1 ``max_usage_in_bytes``);
  - ``sandbox.oom_kills`` — the cgroup's ``oom_kill`` counter, so an exec'd
    process OOM-killed mid-run counts even though the container survived it;
    ``docker inspect``'s ``State.OOMKilled`` is folded in as a floor.

  No hook here may fail the run: every read is caught and logged, and
  ``before_destroy`` degrades to fewer metrics rather than raising.

  Attributes:
    sandbox: The backend whose container is measured.
  """

  sandbox: DockerHostSandbox
  _started: float | None = field(default=None, init=False, repr=False)
  _setup_seconds: float | None = field(default=None, init=False, repr=False)

  @override
  def before_create(self, sb: SandboxFs) -> None:
    """Stamp the start of setup (fires before ``up``)."""
    del sb
    self._started = time.monotonic()

  @override
  def after_create(self, sb: SandboxFs) -> None:
    """Stamp the end of setup (fires after ``up`` + mount staging)."""
    del sb
    if self._started is not None:
      self._setup_seconds = time.monotonic() - self._started

  @override
  def before_destroy(self, sb: SandboxFs) -> Contribution | None:
    """Read the runtime metrics while the container still exists."""
    del sb
    metrics: dict[str, float] = {}
    try:
      metrics = self._metrics()
    except Exception:  # noqa: BLE001 — metrics must never fail a graded run
      _logger.exception("runtime metrics collection failed; omitting")
    return Contribution(metrics=metrics) if metrics else None

  def _metrics(self) -> dict[str, float]:
    """Assemble whatever is measurable; skip (and log) what is not."""
    metrics: dict[str, float] = {}

    def put(name: str, value: float | None) -> None:
      if value is not None:
        metrics[qualified_name(_METRIC_NAMESPACE, name)] = value

    pull = self.sandbox._pull_seconds  # noqa: SLF001 — same-module contract
    if self._setup_seconds is not None:
      # The setup window contains the pull, so this cannot go negative in the
      # real hook order; the clamp is for a caller that fired the hooks around
      # something narrower.
      put("setup_seconds", max(0.0, self._setup_seconds - (pull or 0.0)))
    put("pull_seconds", pull)
    put("peak_memory_bytes", self._peak_memory_bytes())
    put("oom_kills", self._oom_kills())
    return metrics

  def _peak_memory_bytes(self) -> float | None:
    """Read the cgroup's cumulative peak, v2 then v1; None when unreadable."""
    for path in _CGROUP_PEAK_FILES:
      raw = self._exec_read(path)
      if raw is not None:
        try:
          return float(int(raw.strip()))
        except ValueError:
          _logger.warning("unparseable cgroup peak %r from %s", raw, path)
    return None

  def _oom_kills(self) -> float | None:
    """Count OOM kills: the cgroup counter, floored by docker inspect."""
    count: float | None = None
    raw = self._exec_read(_CGROUP_EVENTS_FILE)
    if raw is not None:
      for line in raw.splitlines():
        if line.startswith("oom_kill "):
          try:
            count = float(int(line.split()[1]))
          except (IndexError, ValueError):
            _logger.warning("unparseable memory.events line %r", line)
    inspected = self._inspect_oom_killed()
    if inspected is None:
      return count
    return max(count or 0.0, 1.0) if inspected else (count or 0.0)

  def _inspect_oom_killed(self) -> bool | None:
    """Read ``State.OOMKilled`` from docker inspect; None when unreadable."""
    handle = self.sandbox._container  # noqa: SLF001 — same-module contract
    if not handle:
      return None
    try:
      result = self.sandbox._docker(  # noqa: SLF001 — same-module contract
          ["inspect", "-f", "{{json .State.OOMKilled}}", handle],
          timeout=_DOCKER_TIMEOUT_S,
      )
    except SandboxError as exc:
      _logger.warning("docker inspect for metrics failed: %s", exc)
      return None
    if result.returncode != 0:
      _logger.warning("docker inspect for metrics failed: %s", result.stderr)
      return None
    try:
      value = json.loads(result.stdout.strip())
    except json.JSONDecodeError:
      _logger.warning("unparseable inspect output %r", result.stdout)
      return None
    return bool(value)

  def _exec_read(self, path: str) -> str | None:
    """``cat`` one in-container file via docker exec; None when unreadable."""
    handle = self.sandbox._container  # noqa: SLF001 — same-module contract
    if not handle:
      return None
    try:
      result = self.sandbox._docker(  # noqa: SLF001 — same-module contract
          ["exec", handle, "cat", path], timeout=_DOCKER_TIMEOUT_S
      )
    except SandboxError as exc:
      _logger.warning("docker exec cat %s failed: %s", path, exc)
      return None
    return result.stdout if result.returncode == 0 else None
