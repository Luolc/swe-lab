"""The lifecycle driver: bring a sandbox up, run one body, always clean up.

``SandboxManager`` drives one live :class:`~swe_lab.sandbox.sandbox.Sandbox`
(up-first, ADR-0003) and assembles a single ``RunResult`` at teardown. Failure
semantics, exhaustively:

- Setup failures (hooks/up/mounts) **propagate** — a ``with`` body cannot run
  without a sandbox — but the ``RunResult`` is still assembled first.
- Body failures of ``Exception`` kind are **caught and recorded**; the ``with``
  block exits cleanly and the caller reads ``result.status``.
  ``KeyboardInterrupt``/``SystemExit`` re-raise after teardown.
- ``before_destroy`` always runs once the sandbox was live; each hook is
  individually caught. Then the manager **collects** registered artifacts out
  to the host (while the sandbox is still live), then ``sandbox.down`` — which
  never masks the primary error.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
import logging
from pathlib import Path

from .errors import SandboxError
from .mounts import merge_mounts, Mounts
from .observer import SandboxObserver
from .result import Contribution, merge_contributions, RunResult, RunStatus
from .sandbox import Sandbox

_logger = logging.getLogger(__name__)


@dataclass
class SandboxManager:
  """Drive one sandbox run: configuration fields + one private result slot.

  Single-run, like stateful observers: create a fresh manager per run.

  Attributes:
    sandbox: The live sandbox to drive (a backend-specific subclass, not yet
      up). Choosing a backend is choosing which subclass to construct.
    output_dir: Host directory that collected artifacts are fetched into.
    observers: Lifecycle plugs, invoked in registration order.
    mounts: Composition-level extra mounts, merged with the observers'.
    label: Run label; defaults to the sandbox spec's instance id.
  """

  sandbox: Sandbox
  output_dir: Path
  observers: Sequence[SandboxObserver] = ()
  mounts: Mounts = field(default_factory=dict)
  label: str = ""
  _result: RunResult | None = field(default=None, init=False, repr=False)

  def __post_init__(self) -> None:
    if not self.label:
      self.label = self.sandbox.spec.instance_id

  @property
  def result(self) -> RunResult:
    """The run's outcome, assembled exactly once at teardown.

    Raises:
      SandboxError: If the run has not finished yet.
    """
    if self._result is None:
      raise SandboxError("result is not available until the run has finished")
    return self._result

  @contextmanager
  def session(self) -> Iterator[Sandbox]:
    """Bring the sandbox up, yield it for the one main action, tear down.

    Yields:
      The live sandbox.

    Raises:
      SandboxError: On reuse of this manager or a mounts problem.
      BaseException: A setup failure from a hook or the sandbox, re-raised
        as-is after the result is assembled; also
        ``KeyboardInterrupt``/``SystemExit`` from the body, re-raised after
        teardown.
    """
    if self._result is not None:
      raise SandboxError("this manager already ran; create a fresh one")

    status = RunStatus.SUCCESS
    primary: BaseException | None = None
    contributions: list[Contribution] = []
    sb = self.sandbox
    live = False
    try:
      # --- setup: any failure here propagates (no body without a sandbox).
      try:
        merged = merge_mounts(
            dict(self.mounts), *(o.mounts() for o in self.observers)
        )
        for observer in self.observers:
          observer.before_create(sb)
        sb.up()
        live = True
        sb.mount(merged)
        for observer in self.observers:
          observer.after_create(sb)
      except BaseException as exc:
        status = RunStatus.SETUP_ERROR
        primary = exc
        if live:  # the sandbox is live: let on_error hooks probe it
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
      artifacts, metrics, teardown_error = self._teardown(
          sb, live, contributions
      )
      if teardown_error is not None and primary is None:
        status = RunStatus.RUN_ERROR
        primary = teardown_error
      self._result = RunResult(
          label=self.label,
          status=status,
          artifacts=artifacts,
          metrics=metrics,
          error=primary,
      )

  def _teardown(
      self,
      sb: Sandbox,
      live: bool,
      contributions: list[Contribution],
  ) -> tuple[dict[str, Path], dict[str, float], Exception | None]:
    """Run post-processing hooks, collect artifacts out, tear the sandbox down.

    ``before_destroy`` runs (each hook caught) only if the sandbox was live;
    then registered artifacts are **fetched to the host output dir while the
    sandbox is still live**; then ``down`` (never masks). ``after_destroy``
    runs last.

    Args:
      sb: The sandbox the hooks receive and artifacts are fetched from.
      live: Whether the sandbox came up (hooks/collect run only if so).
      contributions: Collector the ``before_destroy`` contributions append to.

    Returns:
      ``(artifacts, metrics, first_error)`` — artifacts as host paths.
    """
    first_error: Exception | None = None
    artifacts: dict[str, Path] = {}
    metrics: dict[str, float] = {}
    if not live:
      return artifacts, metrics, first_error

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
      names, metrics = merge_contributions(contributions)
      artifacts = self._collect(sb, names)  # fetch out WHILE LIVE
    except SandboxError as exc:
      first_error = first_error or exc

    try:
      sb.down()
    except Exception:
      _logger.exception("sandbox.down failed (swallowed; never masks)")
    for observer in self.observers:
      try:
        observer.after_destroy(sb)
      except Exception as exc:
        _logger.exception("after_destroy hook failed; running the rest")
        first_error = first_error or exc
    return artifacts, metrics, first_error

  def _collect(self, sb: Sandbox, names: dict[str, str]) -> dict[str, Path]:
    """Fetch each registered artifact out to the host output dir.

    Args:
      sb: The still-live sandbox to fetch from.
      names: Canonical artifact name → its in-sandbox filename.

    Returns:
      Canonical artifact name → the host path it was fetched to.
    """
    self.output_dir.mkdir(parents=True, exist_ok=True)
    collected: dict[str, Path] = {}
    for name, filename in names.items():
      dest = self.output_dir / filename
      sb.fetch(filename, dest)
      collected[name] = dest
    return collected

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
