"""Public test doubles for the sandbox engine.

Later tasks (the eval method, the harness) reuse these for their own
Docker-free tests, so they are shipped code, not test-local helpers.

``FakeSandbox`` is a real ``Sandbox``: file ops (read/write/mount/fetch) hit a
real local ``workspace`` directory so observers and graders exercise genuine
filesystem behavior, while ``run_script`` / ``run_command`` return **scripted**
``ExecResult``s (no process is spawned) and every call is recorded.
``FakeStore`` is an in-memory ``Store`` for exercising the persist flow.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import os
import shutil
from typing import override

from etils import epath

from .errors import SandboxError
from .mounts import Mount, Mounts
from .observer import SandboxObserver
from .persist import RunRecord
from .result import Contribution
from .sandbox import ExecResult, Sandbox, SandboxFs
from .spec import SandboxSpec
from .store import Store


def _maybe_raise(error: Exception | None) -> None:
  """Raise the scripted error when one is set."""
  if error is not None:
    raise error


@dataclass
class FakeSandbox(Sandbox):
  """In-memory ``Sandbox``: real file ops, scripted exec, recorded calls.

  Attributes:
    spec: The run context.
    workspace: A real local directory backing the file ops.
    run_results: Results returned by successive ``run_script`` / ``run_command``
      calls (repeating the last when exhausted); defaults to a single success.
    up_error: Raised by ``up`` when set.
    run_error: Raised by ``run_script`` / ``run_command`` when set.
    down_error: Raised by ``down`` when set (to test that the manager swallows
      a misbehaving sandbox).
    calls: Every call as ``(method, detail)``, in order.
    scripts: The script names passed to ``run_script``, in order.
    commands: The command strings passed to ``run_command``, in order.
  """

  spec: SandboxSpec
  workspace: epath.Path
  run_results: list[ExecResult] = field(default_factory=list)
  up_error: Exception | None = None
  run_error: Exception | None = None
  down_error: Exception | None = None
  calls: list[tuple[str, str]] = field(default_factory=list)
  scripts: list[str] = field(default_factory=list)
  commands: list[str] = field(default_factory=list)
  _execs: int = field(default=0, init=False, repr=False)

  # --- lifecycle -----------------------------------------------------------

  @override
  def up(self) -> None:
    """Create the workspace and record the call (or raise ``up_error``)."""
    self.workspace.mkdir(parents=True, exist_ok=True)
    self.calls.append(("up", self.spec.instance_id))
    _maybe_raise(self.up_error)

  @override
  def down(self) -> None:
    """Record the teardown (or raise the scripted ``down_error``)."""
    self.calls.append(("down", self.spec.instance_id))
    _maybe_raise(self.down_error)

  @override
  def fetch(self, name: str, dest: epath.PathLike) -> None:
    """Copy a produced workspace file out to a host path."""
    self.calls.append(("fetch", name))
    src = self.workspace / name
    dest = epath.Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.resolve() != dest.resolve():
      _ = shutil.copyfile(src, dest)

  # --- files ---------------------------------------------------------------

  @override
  def read(self, name: str) -> bytes:
    """Read a workspace file's bytes."""
    return (self.workspace / name).read_bytes()

  @override
  def exists(self, name: str) -> bool:
    """Whether a workspace file exists."""
    return (self.workspace / name).is_file()

  @override
  def write(self, name: str, data: bytes, *, executable: bool = False) -> None:
    """Write an ad-hoc workspace file."""
    dest = self.workspace / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    _ = dest.write_bytes(data)
    if executable:
      os.chmod(dest, 0o755)

  @override
  def _put_bytes(self, target: str, data: bytes, mount: Mount) -> None:
    dest = self._dest(target)
    dest.parent.mkdir(parents=True, exist_ok=True)
    _ = dest.write_bytes(data)
    os.chmod(dest, mount.mode)

  @override
  def _put_file(self, target: str, src: epath.PathLike, mount: Mount) -> None:
    dest = self._dest(target)
    dest.parent.mkdir(parents=True, exist_ok=True)
    _ = shutil.copyfile(src, dest)
    os.chmod(dest, mount.mode)

  def _dest(self, target: str) -> epath.Path:
    return (
        epath.Path(target)
        if target.startswith("/")
        else self.workspace / target
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
    """Record the script name and return the next scripted result."""
    del timeout, env
    self.calls.append(("run_script", name))
    self.scripts.append(name)
    return self._next_result()

  @override
  def run_command(
      self,
      command: str,
      *,
      timeout: float,
      env: Mapping[str, str] | None = None,
  ) -> ExecResult:
    """Record the command and return the next scripted result."""
    del timeout, env
    self.calls.append(("run_command", command))
    self.commands.append(command)
    return self._next_result()

  def _next_result(self) -> ExecResult:
    _maybe_raise(self.run_error)
    index = min(self._execs, len(self.run_results) - 1)
    self._execs += 1
    return (
        self.run_results[index] if self.run_results else ExecResult(0, "", "")
    )


@dataclass
class RecordingObserver(SandboxObserver):
  """Observer that records its hook invocations and can raise on demand.

  Attributes:
    name: Label prefixed to every recorded event.
    events: Shared event log — pass the same list to several observers to
      assert cross-observer ordering.
    raise_in: Hook name that raises ``RuntimeError`` when reached.
    contribution: Returned from ``before_destroy`` when set.
    error_contribution: Returned from ``on_error`` when set.
    extra_mounts: Returned from ``mounts``.
  """

  name: str = "obs"
  events: list[str] = field(default_factory=list)
  raise_in: str = ""
  contribution: Contribution | None = None
  error_contribution: Contribution | None = None
  extra_mounts: Mounts = field(default_factory=dict)

  def _hit(self, hook: str) -> None:
    self.events.append(f"{self.name}.{hook}")
    if self.raise_in == hook:
      raise RuntimeError(f"{self.name} scripted failure in {hook}")

  @override
  def mounts(self) -> Mounts:
    """Record and return the scripted mounts."""
    self._hit("mounts")
    return self.extra_mounts

  @override
  def before_create(self, sb: SandboxFs) -> None:
    """Record the hook."""
    self._hit("before_create")

  @override
  def after_create(self, sb: SandboxFs) -> None:
    """Record the hook."""
    self._hit("after_create")

  @override
  def before_destroy(self, sb: SandboxFs) -> Contribution | None:
    """Record the hook and return the scripted contribution."""
    self._hit("before_destroy")
    return self.contribution

  @override
  def after_destroy(self, sb: SandboxFs) -> None:
    """Record the hook."""
    self._hit("after_destroy")

  @override
  def on_error(
      self, sb: SandboxFs, error: BaseException
  ) -> Contribution | None:
    """Record the hook and return the scripted error contribution."""
    self._hit("on_error")
    return self.error_contribution


@dataclass
class FakeStore(Store):
  """In-memory ``Store``: real semantics, no filesystem or network.

  Attributes:
    objects: Key → uploaded bytes.
    manifests: Appended run shards, in order.
    puts: Keys passed to ``put``, in order (for assertions).
  """

  objects: dict[str, bytes] = field(default_factory=dict)
  manifests: list[RunRecord] = field(default_factory=list)
  puts: list[str] = field(default_factory=list)

  @override
  def put(self, key: str, src: epath.PathLike) -> None:
    self.objects[key] = epath.Path(src).read_bytes()
    self.puts.append(key)

  @override
  def get(self, key: str, dest: epath.PathLike) -> None:
    if key not in self.objects:
      raise SandboxError(f"store key not found: {key}")
    dest = epath.Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    _ = dest.write_bytes(self.objects[key])

  @override
  def append_manifest(self, record: RunRecord) -> None:
    self.manifests.append(record)

  @override
  def read_manifest(self, sweep_id: str) -> list[RunRecord]:
    return [m for m in self.manifests if m.sweep_id == sweep_id]
