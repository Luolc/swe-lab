"""The lifecycle driver: bring a sandbox up, run one body, always clean up.

``SandboxManager`` yields a pure-handle ``Sandbox`` for the caller's body and
assembles a single ``RunResult`` at teardown. Failure semantics, exhaustively:

- Setup failures (hooks/mounts/up) **propagate** — a ``with`` body cannot run
  without a sandbox — but the ``RunResult`` is still assembled first.
- Body failures of ``Exception`` kind are **caught and recorded**; the
  ``with`` block exits cleanly and the caller reads ``result.status``.
  ``KeyboardInterrupt``/``SystemExit`` re-raise after teardown.
- ``before_destroy`` always runs once the sandbox was live; each hook is
  individually caught so one failing extractor cannot cost the others'
  contributions; ``backend.down`` always runs and never masks the primary
  error.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
import logging
from pathlib import Path

from .backend import ExecResult, SandboxBackend
from .errors import SandboxError
from .mounts import materialize, merge_mounts, Mounts
from .observer import SandboxObserver
from .result import Contribution, merge_contributions, RunResult, RunStatus
from .spec import SandboxSpec

_logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Sandbox:
  """Pure handle to one sandbox — nothing mutable accumulates on it.

  The shared, inspectable state between observers is the **workspace
  filesystem**, never this object. During ``before_create`` the sandbox is
  not yet live (``handle`` is empty) and ``run`` fails.

  Attributes:
    label: The run's label.
    spec: The run context this sandbox realizes.
    workspace: Host-visible directory shared into the sandbox.
    backend: The backend that realized this sandbox.
    handle: The backend's opaque handle (empty before the sandbox is live).
  """

  label: str
  spec: SandboxSpec
  workspace: Path
  backend: SandboxBackend
  handle: str

  def run(
      self,
      script_name: str,
      *,
      timeout: float,
      env: Mapping[str, str] | None = None,
      stream_to: Path | None = None,
  ) -> ExecResult:
    """Run a workspace script (by name) inside the live sandbox.

    The script must already be a file in the workspace — a mount, or one an
    observer wrote there. Running a persisted file (not stdin) keeps the exact
    script on disk for audit.

    Args:
      script_name: The script's workspace-relative filename.
      timeout: Seconds before the execution is killed.
      env: Extra variables for this execution only.
      stream_to: Stream stdout to this host file instead of capturing.

    Returns:
      The script's exit status and output.

    Raises:
      SandboxError: If the sandbox is not live yet.
    """
    if not self.handle:
      raise SandboxError("sandbox is not live yet (before_create phase)")
    return self.backend.run_script(
        self.handle, script_name, timeout=timeout, env=env, stream_to=stream_to
    )


@dataclass
class SandboxManager:
  """Drive one sandbox run: configuration fields + one private result slot.

  Single-run, like stateful observers: create a fresh manager per run.

  Attributes:
    spec: The run context to realize.
    backend: The backend that realizes it.
    workspace: Host-side workspace directory (created if missing; a
      non-empty one is refused unless ``reuse`` is set).
    observers: Lifecycle plugs, invoked in registration order.
    mounts: Composition-level extra mounts, merged with the observers'.
    label: Run label; defaults to ``spec.instance_id``.
    reuse: Allow running in a non-empty workspace.
  """

  spec: SandboxSpec
  backend: SandboxBackend
  workspace: Path
  observers: Sequence[SandboxObserver] = ()
  mounts: Mounts = field(default_factory=dict)
  label: str = ""
  reuse: bool = False
  _result: RunResult | None = field(default=None, init=False, repr=False)

  def __post_init__(self) -> None:
    if not self.label:
      self.label = self.spec.instance_id

  @property
  def result(self) -> RunResult:
    """The run's outcome, assembled exactly once at teardown.

    A guarded read, not a live view — it exists only because a ``with``
    block cannot return a value.

    Raises:
      SandboxError: If the run has not finished yet.
    """
    if self._result is None:
      raise SandboxError("result is not available until the run has finished")
    return self._result

  @contextmanager
  def sandbox(self) -> Iterator[Sandbox]:
    """Bring the sandbox up, yield it for the one main action, tear down.

    Yields:
      The live sandbox handle.

    Raises:
      SandboxError: On reuse of this manager, a refused non-empty
        workspace, or a mounts problem.
      BaseException: A setup failure from a hook or the backend, re-raised
        as-is after the result is assembled (the body cannot run without a
        sandbox); also ``KeyboardInterrupt``/``SystemExit`` from the body,
        re-raised after teardown.
    """
    if self._result is not None:
      raise SandboxError("this manager already ran; create a fresh one")

    status = RunStatus.SUCCESS
    primary: BaseException | None = None
    contributions: list[Contribution] = []
    handle = ""
    sb = Sandbox(
        label=self.label,
        spec=self.spec,
        workspace=self.workspace,
        backend=self.backend,
        handle="",
    )
    try:
      # --- setup: any failure here propagates (no body without a sandbox).
      try:
        merged = merge_mounts(
            dict(self.mounts), *(o.mounts() for o in self.observers)
        )
        for observer in self.observers:
          observer.before_create(sb)
        self._prepare_workspace()
        materialize(merged, self.workspace)
        handle = self.backend.up(self.spec, self.workspace)
        sb = replace(sb, handle=handle)
        for observer in self.observers:
          observer.after_create(sb)
      except BaseException as exc:
        status = RunStatus.SETUP_ERROR
        primary = exc
        if handle:  # the sandbox is live: let on_error hooks probe it
          contributions.extend(self._on_error(sb, exc))
        raise
      # --- the one main action.
      try:
        yield sb
      except BaseException as exc:
        status = RunStatus.RUN_ERROR
        primary = exc
        contributions.extend(self._on_error(sb, exc))
        if not isinstance(exc, Exception):
          raise  # KeyboardInterrupt/SystemExit: teardown, then propagate
        # A plain Exception is recorded, not re-raised: the with block
        # exits cleanly and the caller reads result.status.
    finally:
      destroy_error = self._teardown(sb, handle, contributions)
      if destroy_error is not None and primary is None:
        status = RunStatus.RUN_ERROR
        primary = destroy_error
      self._finish(status, primary, contributions)

  def _finish(
      self,
      status: RunStatus,
      primary: BaseException | None,
      contributions: list[Contribution],
  ) -> None:
    """Aggregate contributions and assemble the one ``RunResult``."""
    artifacts: dict[str, Path] = {}
    metrics: dict[str, float] = {}
    try:
      artifacts, metrics = merge_contributions(contributions)
    except SandboxError as exc:
      if primary is None:
        status = RunStatus.RUN_ERROR
        primary = exc
      else:
        _logger.warning("dropping colliding contributions: %s", exc)
    self._result = RunResult(
        label=self.label,
        status=status,
        artifacts=artifacts,
        metrics=metrics,
        error=primary,
    )

  def _prepare_workspace(self) -> None:
    """Create the workspace; refuse a non-empty one unless ``reuse``.

    Raises:
      SandboxError: If the workspace has files and ``reuse`` is False.
    """
    self.workspace.mkdir(parents=True, exist_ok=True)
    if not self.reuse and any(self.workspace.iterdir()):
      raise SandboxError(
          f"workspace {self.workspace} is not empty; pass reuse=True to run "
          "in it anyway"
      )

  def _on_error(self, sb: Sandbox, error: BaseException) -> list[Contribution]:
    """Run on_error hooks against the still-live sandbox, each caught."""
    collected: list[Contribution] = []
    for observer in self.observers:
      try:
        contribution = observer.on_error(sb, error)
      except Exception:
        _logger.exception("on_error hook failed (never masks the run error)")
        continue
      if contribution is not None:
        collected.append(contribution)
    return collected

  def _teardown(
      self,
      sb: Sandbox,
      handle: str,
      contributions: list[Contribution],
  ) -> Exception | None:
    """Run post-processing hooks and tear the backend down.

    ``before_destroy``/``after_destroy`` run only if the sandbox was live;
    each hook is individually caught so the rest still run. ``down`` always
    runs when there is a handle and never raises past this method.

    Args:
      sb: The sandbox handle the hooks receive.
      handle: The backend handle; empty when ``up`` never succeeded.
      contributions: Collector the hooks' contributions are appended to.

    Returns:
      The first hook failure, to become the primary error when the run had
      none.
    """
    first_error: Exception | None = None
    if handle:
      for observer in self.observers:
        try:
          contribution = observer.before_destroy(sb)
        except Exception as exc:
          _logger.exception("before_destroy hook failed; running the rest")
          first_error = first_error or exc
          continue
        if contribution is not None:
          contributions.append(contribution)
      try:
        self.backend.down(handle)
      except Exception:
        _logger.exception("backend.down failed (swallowed; never masks)")
      for observer in self.observers:
        try:
          observer.after_destroy(sb)
        except Exception as exc:
          _logger.exception("after_destroy hook failed; running the rest")
          first_error = first_error or exc
    return first_error
