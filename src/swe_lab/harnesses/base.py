"""The harness contract: the agent plug a solving run composes over the engine.

A ``Harness`` is a ``ConversationProducer`` (it yields a ``Conversation`` and
names its native output files) that also supplies the run's **mounts** (its own
staged files, including any fixed-path read-only asset like the pinned binary)
and the **main action** (``run``). The engine never imports a concrete harness —
the rollout composition calls these and wires them into a manager + sandbox.
Nothing dataset-specific lives here: a harness is agnostic to the task (the
prompt is the dataset's).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping

from swe_lab.conversation import ConversationProducer
from swe_lab.sandbox import Mounts, SandboxFs

# Where the rollout composition stages the task prompt, as a workspace-relative
# name. This is the composition↔harness contract — the composition writes it
# (the text is the dataset's), every harness reads it — so it belongs here
# rather than to any one agent's constants.
PROMPT_NAME = "prompt.txt"


class Harness(ConversationProducer, ABC):
  """An off-the-shelf agent CLI plugged into the sandbox engine as a run body.

  A behavior interface (ABC, per ADR-0002). It also inherits
  ``to_conversation`` from ``ConversationProducer`` — the conversion contract —
  while the run's own side effects (``native_outputs``, ``completed``) are the
  harness's, collected by ``HarnessOutcomeObserver``.
  """

  @abstractmethod
  def mounts(self, workdir: str) -> Mounts:
    """Return the harness's own files to stage (including any read-only asset).

    An asset (e.g. the pinned binary at a fixed absolute path) is just a
    read-only mount — there is no separate assets seam (ADR-0003).
    """
    ...

  @abstractmethod
  def run(
      self,
      sb: SandboxFs,
      *,
      timeout: float,
      env: Mapping[str, str] | None = None,
  ) -> None:
    """Run the main action (the agent) in the live sandbox.

    Args:
      sb: The live sandbox to run in.
      timeout: Seconds before the run is killed.
      env: Extra environment for the agent process, injected by the caller (an
        internal endpoint, a feature flag, …). Layered **over** the harness's
        own defaults, so a caller can override them; a harness may still pin
        what it must own (e.g. the URL of a capture proxy it was wired to). Not
        the place for a secret — pass those to the *sandbox* by reference
        (``pass_env``) so the value never reaches a command line or a staged
        file.
    """
    ...

  @abstractmethod
  def native_outputs(self) -> dict[str, str]:
    """Name every native byproduct this harness writes during a run.

    Declared, not discovered: the harness knows which files it produces, and
    ``HarnessOutcomeObserver`` registers the ones that actually landed (a run
    that died early simply produces fewer).

    Returns:
      Artifact name → workspace-relative filename, for the trace and any log.
    """
    ...

  @abstractmethod
  def completed(self, sb: SandboxFs) -> bool:
    """Whether the agent finished cleanly, read from its own captured trace.

    Only the harness knows which file carries the signal and how to read it, so
    the composition asks rather than parsing an agent-specific format itself.
    Read through the sandbox (like ``to_conversation``), never a host path, and
    return ``False`` — don't raise — when the trace is absent or unreadable: a
    crashed run is a legitimate outcome to report.

    Args:
      sb: The live sandbox, for reading the harness's own output files.

    Returns:
      Whether the run reached a clean finish.
    """
    ...
