"""The rollout composition: one agent run → a graded-ready patch + trace.

``CodingAgentTask`` is the composition (ADR-0007): a harness solves the bound
instance, the shared observers — the harness's own pair plus diff-extract —
watch the run, and an optional proxy records the agent's API traffic around
the main action. Backend-, dataset- **and harness-agnostic**: nothing here
imports a concrete agent, so a downstream user's own ``Harness`` and internal
proxy compose unchanged.

The prompt is a **declared input**, like every other file a task consumes: the
standalone shape builds it from the instance (``instance_prompt``, the
default), and a chain can supply it by edge instead — a planning task writing
the prompt its solver reads.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import contextlib
from dataclasses import dataclass, field
from typing import Any, override

from swe_lab.conversation import ConversationObserver
from swe_lab.datasets.instance import TaskInstance
from swe_lab.harnesses import Harness, HarnessOutcomeObserver
from swe_lab.sandbox import (
    ArtifactSchema,
    ExecResult,
    merge_mounts,
    Mounts,
    SandboxFs,
    SandboxObserver,
)
from swe_lab.sandbox.observers import DiffExtractObserver
from swe_lab.workflow import AttemptResult, InputsBuilder, Task

# The store name the task prompt arrives as. Markdown because that is what the
# prompt is — and what an agent reading its own input file expects.
PROMPT_NAME = "prompt.md"


def instance_prompt(
    sb: SandboxFs, instance: TaskInstance[Any]
) -> Mapping[str, bytes]:
  """Build the coding task's prompt input from the dataset's own statement.

  The standalone default: solving an instance straight from a dataset needs no
  upstream task, so the task supplies its own input.

  Args:
    sb: Unused — the prompt is the instance's, not the workspace's.
    instance: The instance being solved.

  Returns:
    The prompt input, by store name.
  """
  del sb
  return {PROMPT_NAME: instance.prompt().encode("utf-8")}


@dataclass
class CodingAgentTask(Task):
  """An agent solves the bound instance; outputs a patch and a trace.

  The rollout composition as a task: the harness is a field, its mounts and
  observers folded into the task's total hooks, its ``run`` the main action.
  Every collaborator is **injected already built** — the caller owns their
  construction (the agent's own constructor carries its model / capture / auth
  mode; a recorder for the proxy) — so a new construction option never ripples
  through this class, and a downstream user's own ``Harness`` and internal
  proxy compose here unchanged.

  Attributes:
    harness: The agent to run. It supplies its own mounts, observers, the
      main action, the trace conversion, and the completion signal.
    inputs_builder: How the prompt gets built when nothing else supplies it;
      the default asks the instance. Set it to ``None`` in a chain whose
      earlier task *produces* ``prompt.md`` — otherwise the builder and the
      edge collide, loudly and on purpose.
    extra_inputs: Further inputs this task declares — files the harness or the
      prompt refers to, supplied by an edge or by the caller.
    exclude_globs: Build-noise denylist for the diff extraction.
    agent_env: Extra environment for the agent process, handed to the
      harness. For a secret, use the sandbox's ``pass_env`` instead — that
      passes it by reference, so the value never reaches a command line.
    proxy: A recorder held open around the main action (e.g. a host-side
      reverse proxy capturing the agent's API traffic). Any context manager
      will do; ``None`` means record nothing. Single-use, like one execution:
      a re-executed task needs a fresh one.
  """

  harness: Harness
  # Redeclared only to change the base's default. `kw_only` has to be restated:
  # redeclaring a field keeps the base's *position* but not its keyword-only
  # status, which would put a defaulted field ahead of `harness`. The plain
  # function as a default is safe — `__init__` sets it as an *instance*
  # attribute, so `self.inputs_builder` never binds as a method would.
  inputs_builder: InputsBuilder | None = field(
      default=instance_prompt, kw_only=True
  )
  extra_inputs: tuple[ArtifactSchema, ...] = ()
  exclude_globs: tuple[str, ...] = ()
  agent_env: Mapping[str, str] | None = None
  proxy: contextlib.AbstractContextManager[object] | None = None

  @override
  def mounts(self, instance: TaskInstance[Any]) -> Mounts:
    """Stage the instance's material and the harness's own files.

    Args:
      instance: The instance being solved.

    Returns:
      The merged staging set (duplicate targets refused).
    """
    return merge_mounts(
        super().mounts(instance),
        self.harness.mounts(instance.sandbox_spec().workdir),
    )

  @override
  def observers(self, instance: TaskInstance[Any]) -> Sequence[SandboxObserver]:
    """Return the harness's own observers plus the deliverable's extractor.

    Args:
      instance: Unused — what this task extracts is fixed by its own
        configuration.

    Returns:
      The harness's pair (or whatever it chooses) followed by a fresh
      ``DiffExtractObserver`` — the patch belongs to the *task* (ADR-0007
      §3), or the same harness could never run a task producing something
      other than a diff.
    """
    del instance
    return (
        *self.harness.observers(),
        DiffExtractObserver(exclude_globs=self.exclude_globs),
    )

  @override
  def input_schema(self) -> Sequence[ArtifactSchema]:
    """Declare the prompt, plus whatever else this task was configured with.

    Returns:
      The prompt input first, then ``extra_inputs``.
    """
    return (
        ArtifactSchema(PROMPT_NAME, description="the task prompt"),
        *self.extra_inputs,
    )

  @override
  def action(
      self, sb: SandboxFs, instance: TaskInstance[Any], *, timeout: float
  ) -> ExecResult:
    """Run the agent against the staged prompt, inside the recording proxy.

    The prompt is read back out of the workspace and handed to the harness as
    text: the harness contract is untouched (ADR-0007 §8), and where the
    harness lands it stays the harness's own business.

    The proxy's lifetime is the agent's — opened around the run and closed
    before ``before_destroy`` reads the log, so the recording is flushed by
    the time conversion happens.

    Args:
      sb: The live sandbox to run in.
      instance: Unused — the prompt reached the workspace before this ran.
      timeout: Seconds before the agent run is killed.

    Returns:
      The agent execution's outcome (a timeout comes back as a timed-out
      ``ExecResult``, not a raise).
    """
    del instance
    prompt = sb.read(PROMPT_NAME).decode("utf-8", "backslashreplace")
    with self.proxy or contextlib.nullcontext():
      return self.harness.run(
          sb, prompt=prompt, timeout=timeout, env=self.agent_env
      )


def patch_of(result: AttemptResult) -> DiffExtractObserver | None:
  """Return the diff-extract observer a rollout execution composed.

  The patch, its emptiness, and whether a binary hunk was stripped all live on
  it — read back off the execution's own observers rather than reshaped into
  another dataclass, so a caller reads exactly what the run produced.

  Args:
    result: The execution to read.

  Returns:
    The observer, or ``None`` if the result came from a task that composed no
    diff extraction.
  """
  return next(
      (o for o in result.observers if isinstance(o, DiffExtractObserver)),
      None,
  )


def conversation_of(result: AttemptResult) -> ConversationObserver | None:
  """Return the conversation observer a rollout execution composed.

  Args:
    result: The execution to read.

  Returns:
    The observer (it carries the typed trace), or ``None`` when the harness
    composed none.
  """
  return next(
      (o for o in result.observers if isinstance(o, ConversationObserver)),
      None,
  )


def outcome_of(result: AttemptResult) -> HarnessOutcomeObserver | None:
  """Return the harness-outcome observer a rollout execution composed.

  Args:
    result: The execution to read.

  Returns:
    The observer (it carries the completion signal), or ``None`` when the
    harness composed none.
  """
  return next(
      (o for o in result.observers if isinstance(o, HarnessOutcomeObserver)),
      None,
  )
