"""The harness contract: the agent plug a solving run composes over the engine.

A ``Harness`` is a ``ConversationProducer`` (it yields a ``Conversation`` and
names its native output files) that also supplies the run's **mounts** (its own
staged files, including any fixed-path read-only asset like the pinned binary)
and the **main action** (``run``). The engine never imports a concrete harness —
the rollout composition calls these and wires them into a manager + sandbox.
Nothing dataset-specific lives here: a harness is agnostic to the task (the
prompt is the dataset's).

How a run *ended* is :class:`AgentOutcome`, not a boolean: an agent that spent
its own turn budget and one that died on an API error are different facts, and
only the first is the agent's own doing. That distinction is what makes a fair
retry decidable (ADR-0011), so the contract asks a harness for the outcome and
derives ``completed`` from it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from enum import StrEnum
from typing import final

from swe_lab.conversation import ConversationProducer
from swe_lab.sandbox import (
    AgentAsset,
    ExecResult,
    Mounts,
    SandboxFs,
    SandboxObserver,
)


class AgentOutcome(StrEnum):
  """How an agent run ended, as its own trace reports it (ADR-0011).

  Orthogonal to every other axis a run has: the engine's ``RunStatus`` (did
  the sandbox come up, did we kill the action), the patch axis, and the grade.
  This one answers "what did the *agent* do", and each member is either the
  agent spending a budget it was given or something that happened *to* it —
  which is exactly the retry question (:attr:`retryable`).

  Two states deliberately have no member here, because they already have a
  home and one fact gets one home: **not started** is
  ``RunStatus.SETUP_ERROR``, and **timed out** is ``RunStatus.TIMEOUT`` (the
  task promotes a killed action to it). Both are the engine's axis — a
  harness reading its own trace cannot see either, and duplicating them would
  create a second, disagreeing source.

  Attributes:
    NO_OUTPUT: The trace is absent or empty — nothing was ever written.
    TRUNCATED: A partial trace with no terminal result: the process died
      mid-flight (SIGKILL, OOM) before it could report.
    FINISHED: The agent loop ended cleanly.
    FINISHED_WITH_API_ERROR: The loop ended, but its final turn was an API
      error.
    MAX_TURNS: It ran out of the turn budget it was given.
    MAX_BUDGET: It ran out of the spend budget it was given.
    MAX_OUTPUT_RETRIES: It could not produce schema-valid structured output
      within the allowed retries.
    EXECUTION_ERROR: An exception escaped the turn loop — the catch-all,
      covering an exhausted API retry, an auth or billing failure, and an
      internal agent bug alike.
  """

  NO_OUTPUT = "no_output"
  TRUNCATED = "truncated"
  FINISHED = "finished"
  FINISHED_WITH_API_ERROR = "finished_with_api_error"
  MAX_TURNS = "max_turns"
  MAX_BUDGET = "max_budget"
  MAX_OUTPUT_RETRIES = "max_output_retries"
  EXECUTION_ERROR = "execution_error"

  @property
  def retryable(self) -> bool:
    """Whether re-running this ending is fair — it was ours, not the agent's.

    The one principle behind the whole table (ADR-0011): **retry only a
    failure that is not attributable to the agent**. Re-running an agent that
    spent its own budget hands it attempts a better-behaved agent would not
    need, and the score inflates; not re-running our own crash penalizes the
    agent for our problem, and the score deflates. The axis is causal
    attribution, never severity — a crash is retryable because it is *ours*,
    not because it is bad.

    ``FINISHED`` is not retryable for the same reason a solved task is not
    re-rolled: there is nothing to absorb. Note what this does **not** read —
    the patch and the verdict. A predicate that retried an empty patch or a
    failing test would re-roll bad luck and inflate pass@1 directly.

    Returns:
      Whether a retry is owed rather than earned.
    """
    return self in _RETRYABLE_OUTCOMES


# Everything the agent did *not* choose, given the budget it was handed. Kept
# as a frozenset beside the enum rather than inline in the property so the
# policy reads as one table.
_RETRYABLE_OUTCOMES: frozenset[AgentOutcome] = frozenset(
    {
        AgentOutcome.NO_OUTPUT,
        AgentOutcome.TRUNCATED,
        AgentOutcome.FINISHED_WITH_API_ERROR,
        AgentOutcome.EXECUTION_ERROR,
    }
)


class Harness(ConversationProducer, ABC):
  """An off-the-shelf agent CLI plugged into the sandbox engine as a run body.

  A behavior interface (ABC, per ADR-0002). It also inherits
  ``to_conversation`` from ``ConversationProducer`` — the conversion contract —
  while the run's own side effects (``native_outputs``, ``outcome``) are the
  harness's, collected by ``HarnessOutcomeObserver``.
  """

  def assets(self) -> Sequence[AgentAsset]:
    """Declare the files this agent needs at fixed absolute paths.

    The provisioning seam (task-28 §7). A harness says **what** it needs and
    **where**, never how the bytes travel — that is the backend's call, since
    a container has to be handed a copy while a CI job should fetch straight
    to the final path (ADR-0003 §3, and ``Sandbox.asset_observer``).

    Declaring rather than mounting is what keeps the two sides from
    enumerating each other: adding an agent touches no backend, and a
    downstream backend can provision an agent swe-lab has never heard of.

    Returns:
      The assets, empty by default — a harness whose agent is already present
      in every image needs none.
    """
    return ()

  @abstractmethod
  def mounts(self, workdir: str) -> Mounts:
    """Return the harness's own files to stage (including any read-only asset).

    An asset (e.g. the pinned binary at a fixed absolute path) is just a
    read-only mount — there is no separate assets seam (ADR-0003).
    """
    ...

  @abstractmethod
  def observers(self) -> Sequence[SandboxObserver]:
    """Return the observers that watch this harness's run (ADR-0007 §3).

    The runner owns how *it* is observed, and which observers that takes is
    the concrete harness's decision — no default, because a base-class default
    would bake one agent's shape into the contract. Most agents want the
    generic pair (``ConversationObserver`` + ``HarnessOutcomeObserver``),
    which stay reusable building blocks; returning them is the subclass's
    choice. Fresh instances per call: stateful observers are single-run.

    NOT the place for task outputs — the patch extractor belongs to the task
    (ADR-0007 §3), or the same harness could never run a task that produces
    something other than a diff.
    """
    ...

  @abstractmethod
  def run(
      self,
      sb: SandboxFs,
      *,
      prompt: str,
      timeout: float,
      env: Mapping[str, str] | None = None,
  ) -> ExecResult:
    """Run the main action (the agent) against ``prompt`` in the live sandbox.

    Returning the execution's result — rather than discarding it, as this
    contract used to — is what lets the composition tell a *killed* run from
    one that merely produced nothing: a timeout does not raise, it comes back
    here as a timed-out ``ExecResult``. A harness that execs more than once
    returns the result of the main action.

    Args:
      sb: The live sandbox to run in.
      prompt: The task prompt, as text. The dataset owns its *content*; where
        it lands — a file the harness writes here (``sb.write``), argv, stdin
        — is the harness's own business (ADR-0007 §8), which is why this is a
        string and not a filename convention.
      timeout: Seconds before the run is killed.
      env: Extra environment for the agent process, injected by the caller (an
        internal endpoint, a feature flag, …). Layered **over** the harness's
        own defaults, so a caller can override them; a harness may still pin
        what it must own (e.g. the URL of a capture proxy it was wired to). Not
        the place for a secret — pass those to the *sandbox* by reference
        (``pass_env``) so the value never reaches a command line or a staged
        file.

    Returns:
      The main action's exit status, output, and whether it timed out.
    """
    ...

  @property
  def accepts_corrections(self) -> bool:
    """Whether the actor can be told something while it is still running.

    A capability, asked of the contract rather than of a concrete class, so a
    composition can refuse a supervised run on a harness that would silently
    drop what the supervisor says. Most agents are handed one prompt and are
    unreachable until they stop, which is why the default is ``False``.

    Returns:
      Whether a correction delivered mid-run reaches the actor.
    """
    return False

  @property
  @abstractmethod
  def name(self) -> str:
    """Short snake_case identifier for this harness (e.g. ``claude_code``).

    Namespaces the harness's own artifacts, so the generic role names in
    ``native_outputs`` (``stderr``, ``event_stream``) cannot collide between two
    harnesses and say *whose* they are once persisted.
    """
    ...

  @abstractmethod
  def native_outputs(self) -> dict[str, str]:
    """Name every native byproduct this harness writes during a run.

    Declared, not discovered: the harness knows which files it produces, and
    ``HarnessOutcomeObserver`` registers the ones that actually landed (a run
    that died early simply produces fewer).

    Keys are the byproduct's **role** for this harness, unqualified — the
    observer prefixes them with :attr:`name`, so a harness neither has to
    remember to namespace nor can forget to.

    Returns:
      Role → workspace-relative filename, for the trace and any log.
    """
    ...

  def usage(self, sb: SandboxFs) -> dict[str, float | int | None]:
    """Report what the run spent, when the harness's trace says so.

    Not abstract and not required: a harness whose trace carries no such
    figures inherits the empty mapping, which reads as "this harness does not
    report it" — distinct from a zero. Read through the sandbox and return a
    value rather than raising, for the same reason :meth:`outcome` does.

    Args:
      sb: The still-live sandbox, read through rather than a host path.

    Returns:
      Whatever the harness can evidence, by convention ``cost_usd`` and
      ``num_turns``; empty when it can evidence nothing.
    """
    del sb
    return {}

  @abstractmethod
  def outcome(self, sb: SandboxFs) -> AgentOutcome:
    """How the run ended, read from the harness's own captured trace.

    Only the harness knows which file carries the signal and how to read it, so
    the composition asks rather than parsing an agent-specific format itself.
    Read through the sandbox (like ``to_conversation``), never a host path, and
    **return a value — don't raise — when the trace is absent or unreadable**:
    that is ``NO_OUTPUT`` or ``TRUNCATED``, a legitimate outcome to report.

    A harness whose agent cannot distinguish the budget endings should say so
    conservatively: report ``FINISHED`` for a clean end and, for an unexplained
    one, prefer a non-retryable member over guessing ``EXECUTION_ERROR`` —
    retrying an ending the agent may have chosen is what inflates a score.

    Args:
      sb: The live sandbox, for reading the harness's own output files.

    Returns:
      How the agent's own loop ended.
    """
    ...

  @final
  def completed(self, sb: SandboxFs) -> bool:
    """Whether the agent finished cleanly — ``outcome`` is ``FINISHED``.

    Derived, not asked for separately: two sources for one fact could
    disagree, and the bit is the coarse view of :meth:`outcome`.

    Args:
      sb: The live sandbox, for reading the harness's own output files.

    Returns:
      Whether the run reached a clean finish.
    """
    return self.outcome(sb) is AgentOutcome.FINISHED
