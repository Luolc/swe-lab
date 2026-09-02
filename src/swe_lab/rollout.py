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

from collections.abc import Callable, Mapping, Sequence
import contextlib
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, override

from swe_lab.conversation import ConversationObserver
from swe_lab.datasets.instance import TaskInstance
from swe_lab.harnesses import Harness, HarnessOutcomeObserver
from swe_lab.sandbox import (
    AgentAsset,
    ArtifactSchema,
    ExecResult,
    merge_mounts,
    Mounts,
    RunStatus,
    SandboxFs,
    SandboxObserver,
)
from swe_lab.sandbox.observers import (
    DiffExtractObserver,
    GitHistoryLeakError,
    GitHistoryPurgeObserver,
    ResultVerifyObserver,
)
from swe_lab.workflow import AttemptResult, InputsBuilder, Task

# The store name the task prompt arrives as. Markdown because that is what the
# prompt is — and what an agent reading its own input file expects.
PROMPT_NAME = "prompt.md"

type ProxyFactory = Callable[[], contextlib.AbstractContextManager[object]]
"""Opens one recorder for one execution.

See ``CodingAgentTask.proxy_factory``: a factory rather than a recorder,
because a task is executed as many times as it is invoked.
"""

type SupervisionFactory = Callable[[str], SandboxObserver]
"""Builds one run's supervision, given the task text.

See ``CodingAgentTask.supervision_factory``. An observer, because the sandbox
lifecycle already brackets the action — which keeps this module free of any
knowledge of what a supervisor is.
"""


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
    patch_baseline: Take the patch against the tree **as the agent found it**
      rather than against ``base_commit``, by committing that tree before the
      agent starts (ADR-0001, 2026-08-25 amendment). **On by default**
      (ADR-0014): an image whose worktree ships already different from
      ``base_commit`` folds those build-time edits into every agent's patch,
      and the images this repo actually runs do it — a SWE-bench Pro image was
      measured shipping an untracked Redis AOF directory, which alone yields a
      166 KB patch for an agent that ran no steps at all. Diffing against the
      tree the agent found is right whether or not the image is dirty (the
      baseline sha is a pure function of the tree, so a clean worktree simply
      yields a baseline identical in content to ``base_commit``), which is what
      lets it be the default rather than a per-image opt-in nobody remembers.
      Set it ``False`` only to reproduce a pre-ADR-0014 run.

      **Half of a pair**: the base ref is a contract with the grader, which
      has to grade a tree matching it, so set this together with
      ``UnitTestTask.patch_baseline`` — the grading side then recomputes the
      baseline, verifies its sha against this run's recorded base, and resets
      to *it* instead of ``base_commit``. Either flag alone moves the patch
      and the tree apart (a default-graded baseline patch fails to apply
      exactly when the agent touched a path the image had mutated — closed,
      not wrong, but confusingly).
    purge_git_history: Strip future git history before the agent starts, and
      refuse to run if it is still reachable (ADR-0010 §3b). **On by default**:
      the images ship the whole upstream history, so without it the reference
      fix is one ``git show`` away. Set it ``False`` only to characterize an
      unpurged image deliberately.
    verify_result: Run the integrity rules over the finished run and record
      what they saw (ADR-0010 §3c). **Detection, never a gate** — findings are
      reported, the run's status is untouched, and the verifier cannot fail a
      run even on its own bug.
    env: Extra environment for this task's own action — the agent process,
      handed to the harness. Distinct from the sandbox's ``env``, which every
      exec of the run gets; this is the agent's alone. For a secret, use the
      sandbox's ``pass_env`` instead — that passes it by reference, so the
      value never reaches a command line.
    proxy_factory: Opens a recorder held open around the main action (e.g. a
      host-side reverse proxy capturing the agent's API traffic). Anything
      returning a context manager will do; ``None`` records nothing. A
      *factory*, not a recorder, because a task is a declaration that may be
      executed any number of times — a registered definition is executed once
      per instance — while a recorder is single-use. One execution, one
      recorder, and the declaration stays reusable.
    supervision_factory: Builds the observer that watches the actor's live
      stream and may speak to it, given the task text. ``None`` runs the actor
      unsupervised, which is the default — and is *not* the paired control,
      which is supervised with a zero speaking budget
      (:data:`swe_lab.workflow.definitions.CONTROL_BUDGET`). A factory for the
      same reason ``proxy_factory`` is one, and it hands back an *observer*
      because the sandbox lifecycle already brackets the action — nothing here
      needs to know what supervision is made of.
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
  patch_baseline: bool = True
  purge_git_history: bool = True
  verify_result: bool = True
  env: Mapping[str, str] | None = None
  proxy_factory: ProxyFactory | None = None
  supervision_factory: SupervisionFactory | None = None

  def __post_init__(self) -> None:
    """Refuse a supervised run the actor could not hear.

    Supervision on a harness with no live channel is not a degraded run, it is
    a silent one: the corrections are written, nothing reads them, and the
    result is indistinguishable from an unsupervised rollout — including in
    the record. Refused where the two are composed, which is the first place
    both are known.

    Raises:
      ValueError: A supervisor is configured on a harness that cannot receive
        a correction mid-run.
    """
    if self.supervision_factory is not None and not (
        self.harness.accepts_corrections
    ):
      raise ValueError(
          f"supervision needs a harness that accepts corrections;"
          f" {type(self.harness).__name__} does not (for claude_code, that is"
          " correction_channel=True)"
      )

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
  def assets(self) -> Sequence[AgentAsset]:
    """Declare whatever the composed agent says it needs.

    The task knows the harness; the backend knows how to place bytes. This is
    the one line that joins them, and it is why neither has to enumerate the
    other (task-28 §7).

    Returns:
      The harness's declared assets.
    """
    return self.harness.assets()

  @override
  def observers(self, instance: TaskInstance[Any]) -> Sequence[SandboxObserver]:
    """Return the history purge, the harness's own observers, and the extractor.

    The purge comes **first**: it is an environment precondition, and every
    later hook — and the agent — must see an already-clean repo. It is
    contributed here rather than by the caller so it cannot be forgotten on one
    code path, and it attaches to this task alone, leaving the evaluation
    sandbox (which needs its refs) untouched. ADR-0010 §3b.

    Args:
      instance: Supplies the fix commit the purge asserts the absence of;
        ``None`` for a dataset that records none, which weakens the assertion
        but never disables it.

    Returns:
      The purge, then the harness's pair (or whatever it chooses), then a fresh
      ``DiffExtractObserver`` — the patch belongs to the *task* (ADR-0007 §3),
      or the same harness could never run a task producing something other
      than a diff — and the verifier **last**, because it reads what all of
      them just produced.
    """
    purge = (
        GitHistoryPurgeObserver(solution_sha=instance.solution_sha())
        if self.purge_git_history
        else None
    )
    # Called once: the verifier must point at the *same* observer objects that
    # run, since it reads what they leave on themselves.
    from_harness = tuple(self.harness.observers())
    diff = DiffExtractObserver(
        exclude_globs=self.exclude_globs, baseline=self.patch_baseline
    )
    verify = (
        ResultVerifyObserver(
            patch_source=diff,
            conversation_source=next(
                (o for o in from_harness if hasattr(o, "conversation")), None
            ),
            integrity_source=purge,
            required_tests=tuple(instance.required_tests()),
            workdir=instance.sandbox_spec().workdir,
        )
        if self.verify_result
        else None
    )
    supervision = (
        self.supervision_factory(instance.prompt())
        if self.supervision_factory is not None
        else None
    )
    return tuple(
        o
        for o in (purge, *from_harness, supervision, diff, verify)
        if o is not None
    )

  @override
  def outputs_valid(self, result: AttemptResult) -> bool:
    """Refuse an attempt whose ending was ours, so nothing downstream grades it.

    This is what makes :class:`RolloutOutcome` load-bearing rather than
    recorded: a workflow blocks every later entry once an entry fails, so an
    out-of-memory kill or a crashed harness stops here instead of paying for a
    grading container that can only report the damage as a zero. Measured: a
    run whose agent died in 1.4 s still spent the grading budget in full.

    An ending the *actor* owns — a spent wall-clock budget, or a clean run that
    produced no patch — is **not** refused here. It is a real result, and the
    empty patch it yields is already stopped one step later by the edge, which
    costs no container (ADR-0007 §5).

    Args:
      result: The attempt to judge.

    Returns:
      Whether the attempt produced outputs worth carrying forward.
    """
    if rollout_outcome(result).ours:
      return False
    return super().outputs_valid(result)

  @override
  def should_retry(self, result: AttemptResult) -> bool:
    """Retry an infrastructure failure, never one the agent earned (ADR-0011).

    A rollout retry is the one that can inflate a published number, so the
    predicate is causal, not severity-based: **re-run only what happened *to*
    the agent.** Three sources, in order:

    - an **integrity failure** is never retried — a contaminated repo is
      deterministic, so the same image purges the same way every time and a
      retry buys the same verdict one container later, while reading like
      flakiness in the record;
    - the **engine's** verdict (the base hook): a sandbox that never came up,
      a run error, a missing declared output. All ours, all retried. The
      fourth engine failure, a timeout, never reaches here —
      :meth:`~swe_lab.workflow.Task.should_retry` vetoes it, because
      wall-clock is a budget the agent spent;
    - the **agent's own** ending, when the engine is happy: a crash, a
      truncated trace or an API error is ours and is retried;
      ``max_turns`` / ``max_budget`` and a clean finish are the agent's and
      are not (:attr:`~swe_lab.harnesses.AgentOutcome.retryable`).

    What this deliberately does not read is the **patch** and the **grade**.
    Retrying an empty patch or a failing test would re-roll bad luck until it
    landed, which inflates pass@1 directly — and is the reason the predicate
    is a function of the two outcome axes alone.

    Args:
      result: The attempt to judge.

    Returns:
      Whether another attempt is owed.
    """
    if isinstance(result.run.error, GitHistoryLeakError):
      return False
    if super().should_retry(result):
      return True
    observer = outcome_of(result)
    return observer is not None and observer.outcome.retryable

  @override
  def record_extra(self, result: AttemptResult) -> Mapping[str, object]:
    """Record how the agent's loop ended, so the retry is auditable later.

    The retry decision above is a function of this value, and a run that
    exhausted its budget on infrastructure failures still marks *succeeded*
    (that is ``outputs_valid``'s call, not this one). Without the outcome on
    the shard, telling those attempts apart from a solved-nothing agent would
    mean re-parsing the trace artifact of every attempt.

    Args:
      result: The attempt being recorded.

    Returns:
      The agent outcome, or nothing when the task composed no harness
      observer.
    """
    extra: dict[str, object] = {}
    patch = patch_of(result)
    if patch is not None and patch.base_ref:
      # Which base the patch was taken against. Constant in the default mode
      # and therefore mildly redundant there — but in baseline mode it is a
      # per-attempt sha that exists nowhere else, and a reader should not have
      # to know which mode a run used to know how to read its patch.
      extra["patch_base_ref"] = patch.base_ref
    # The stage's own word, so a reader can tell our breakage from the
    # actor's result without re-deriving it from three other fields.
    extra["rollout_outcome"] = rollout_outcome(result).value
    # Which actor produced this. Without it the model is recoverable only by
    # grepping the trace, so a run whose trace is gone cannot be compared with
    # a later batch at all — and comparability is the whole point of a
    # measured rate. `getattr` because a harness need not have a model (a
    # scripted stub does not), and one that has none records none.
    model = getattr(self.harness, "model", "")
    if model:
      extra["agent_model"] = model
    observer = outcome_of(result)
    if observer is not None:
      extra["agent_outcome"] = observer.outcome.value
      # What the attempt cost, recorded because it is already parsed and
      # answering it later means re-reading every trace artifact — or, when the
      # trace is gone, not answering it at all.
      for name, value in observer.usage.items():
        if value is not None:
          extra[f"agent_{name}"] = value
    return extra

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

    The recorder's lifetime is the agent's — opened around the run and closed
    before ``before_destroy`` reads the log, so the recording is flushed by
    the time conversion happens, and a fresh one is opened per execution.

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
    recorder = (
        self.proxy_factory() if self.proxy_factory else contextlib.nullcontext()
    )
    with recorder:
      return self.harness.run(sb, prompt=prompt, timeout=timeout, env=self.env)


# The cgroup/docker OOM counter the host backend records on every run
# (`sandbox.oom_kills`). Named here because this module is what turns it from a
# recorded number into a branch.
OOM_METRIC = "sandbox.oom_kills"

#: Set to 1.0 when a supervised run lost its supervisor part-way *and the reach
#: of that loss cannot be named* — the pump died, the correction channel closed
#: without being told to, or the policy broke in a way it could not bound. A
#: metric rather than an observer field so that `rollout_outcome` stays readable
#: from the run alone, exactly as the out-of-memory signal is.
SUPERVISION_METRIC = "supervision.unhealthy"

#: How many boundaries went unsupervised for a reason the policy *could* bound
#: to them — a failed model call, a line the writer could not make usable. Each
#: one is named in the supervisor's own log; this is the count a reader needs to
#: weigh a run without opening it, and the reason it is not folded into
#: `SUPERVISION_METRIC`: a run with named holes is still evidence, carrying
#: them, while a run of unknown reach is not evidence at all. An event, so a run
#: that had none leaves no key rather than a zero.
SUPERVISION_LAPSE_METRIC = "supervision.lapses"


class RolloutOutcome(StrEnum):
  """What the rollout *stage* produced — the word that decides what follows.

  Distinct from :class:`~swe_lab.harnesses.AgentOutcome`, which is what the
  agent's own trace says about its loop. This one is about the stage as a
  whole (engine, resources, agent, patch), and it exists because four endings
  that need different treatment were previously rendered the same way: a
  crashed harness, an out-of-memory kill, a spent wall-clock budget, and an
  agent that ran fine and produced nothing all arrived downstream as "no
  usable patch".

  **Classification is by cause, never by exit code.** An actor that exhausts
  its own turn budget may well exit non-zero, and that is still the actor's
  result. The question each member answers is: *did the actor terminate on its
  own terms?* Killed from outside, or a precondition that was never met, is
  ours; running to its own boundary and stopping is the actor's.

  Attributes:
    OOM_KILLED: Killed for memory. Ours — the box was too small, which says
      nothing about the task.
    SYSTEM_FAILED: The engine failed, or the agent's loop ended in a way it
      did not choose (:attr:`AgentOutcome.retryable` — a crash, a truncated
      trace, an API error). Deliberately **not** named ``AGENT_FAILED``:
      reading "the agent failed" is exactly the mistake this member exists to
      prevent, because it invites counting our breakage as the actor's.
    TIMED_OUT: The action was killed at its wall-clock budget. The actor's,
      per ADR-0011 — wall-clock is a budget it was handed and spent.
    NO_PATCH: It terminated on its own terms and produced no usable patch. A
      genuine failure to solve, not a failure of the system.
    PATCH_PRODUCED: There is something to grade.
    UNCLASSIFIED: The harness supplied no outcome, so how the loop ended cannot
      be read. Neither positively ours nor positively the actor's: it stays in
      the denominator like any unclassified ending, but it is **counted
      separately**, so an ending nobody could attribute is a number rather than
      silence inside :attr:`NO_PATCH` (ADR-0016).
    SUPERVISION_FAILED: A supervised run lost its supervisor part-way, with no
      bound on where. Ours, and its own word: a run that was meant to be
      supervised and was not for an unknown part of its length is **not
      evidence about supervision**, and pooling it with "supervised, and the
      actor did not comply" would put our own breakage inside the very
      comparison the supervision is being judged by. A run whose unsupervised
      boundaries are each named — `SUPERVISION_LAPSE_METRIC` — does not land
      here: it is evidence, and it carries the count.
  """

  OOM_KILLED = "oom_killed"
  SYSTEM_FAILED = "system_failed"
  TIMED_OUT = "timed_out"
  NO_PATCH = "no_patch"
  PATCH_PRODUCED = "patch_produced"
  UNCLASSIFIED = "unclassified"
  SUPERVISION_FAILED = "supervision_failed"

  @property
  def ours(self) -> bool:
    """Whether this ending was the system's doing rather than the actor's.

    The one causal bit, read by both consumers: it decides whether the attempt
    produced trustworthy outputs (so whether grading runs) and whether the run
    belongs in a solve rate. One bit rather than two, so the gate and the
    accounting cannot disagree about the same run.

    Returns:
      Whether the ending is attributable to us.
    """
    return self in _OURS

  @property
  def counts_in_denominator(self) -> bool:
    """Whether a run that ended this way belongs in a solve rate.

    **Default in.** Only an ending positively identified as ours leaves, so an
    ending nobody classified stays — which can only *understate* a success
    rate. The opposite default lets the excluded set grow unwatched, and it
    grows in the direction that makes results look better.

    Returns:
      Whether to count this run in the denominator.
    """
    return not self.ours

  @property
  def unclassified(self) -> bool:
    """Whether the ending was attributed to nobody, for want of evidence.

    This is the separately reportable not-attributed set: reported alongside
    every rate, next to the excluded count (ADR-0016). The excluded set is
    watched by construction — an ending must be positively identified as ours
    to leave the denominator. This set is not, so it is counted on its own.

    Returns:
      Whether this ending was positively identified as neither ours nor the
      actor's.
    """
    return self is RolloutOutcome.UNCLASSIFIED


# The only two endings that are ours rather than the actor's. Kept beside the
# enum, like `_RETRYABLE_OUTCOMES`, so the policy reads as one table.
_OURS: frozenset[RolloutOutcome] = frozenset(
    {
        RolloutOutcome.OOM_KILLED,
        RolloutOutcome.SYSTEM_FAILED,
        RolloutOutcome.SUPERVISION_FAILED,
    }
)


def rollout_outcome(result: AttemptResult) -> RolloutOutcome:
  """Classify how a rollout attempt ended, by cause.

  Order matters where causes co-occur: a run killed for memory reports both an
  OOM and a broken agent loop, and the OOM is the one that explains the other.
  Wall-clock is checked before the agent's own ending for the same reason —
  a killed action leaves a truncated trace, and calling that a crash would
  move a budget the actor spent onto our side of the ledger.

  Args:
    result: The attempt to classify.

  Returns:
    The stage's outcome.
  """
  if result.run.metrics.get(OOM_METRIC, 0.0) > 0.0:
    return RolloutOutcome.OOM_KILLED
  if result.run.metrics.get(SUPERVISION_METRIC, 0.0) > 0.0:
    # Before the wall clock on purpose: a stalled channel is one of the ways a
    # supervised run reaches its timeout, and reporting that as `TIMED_OUT`
    # would hand a budget the actor never got to spend back to the actor.
    return RolloutOutcome.SUPERVISION_FAILED
  if result.run.status is RunStatus.TIMEOUT:
    return RolloutOutcome.TIMED_OUT
  if result.run.status is not RunStatus.SUCCESS:
    return RolloutOutcome.SYSTEM_FAILED  # setup / run error: never the actor's
  observer = outcome_of(result)
  if observer is not None and observer.outcome.retryable:
    return RolloutOutcome.SYSTEM_FAILED
  if observer is None:
    # No outcome to read: a crash and a clean stop are indistinguishable from
    # here, so neither attribution is earned. Calling it `NO_PATCH` would book
    # an absence of evidence as the actor's failure to solve.
    return RolloutOutcome.UNCLASSIFIED
  patch = patch_of(result)
  if patch is None:
    # This task always composes a `DiffExtractObserver` and declares the patch
    # as a required output, so a missing one is a broken composition, not a
    # task that chose not to look — ours, and it must stop here.
    return RolloutOutcome.SYSTEM_FAILED
  if patch.is_empty:
    return RolloutOutcome.NO_PATCH
  return RolloutOutcome.PATCH_PRODUCED


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
