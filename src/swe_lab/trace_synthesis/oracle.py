"""Phase B of trace synthesis: the Oracle writes a guidebook for a failure.

``OracleAnalysisTask`` runs a harness against an instance whose failure is
already in hand — an ``oracle_failures`` record, which stages the failed
conversation, verdict and patch through its own ``mounts`` — with everything
the actor never had: the reference patch (when the dataset records one), the
exact grading procedure, and the repository's **unpurged** git history. Its one
output is ``guidebook.md``: a staged tutorial for a future blind actor alongside
a compact supervisor-facing rubric, checked against the schema in
:mod:`swe_lab.trace_synthesis.guidebook`.

The task is deliberately contaminated, and says so by construction rather than
by flag: it composes no git-history purge, no diff extraction and no result
verifier, because none of the three describes what this run is. A guidebook is
not a patch, and a run that is handed the answer has nothing to be verified
against. The integrity consequence — that such a run's records are never
pooled with benchmark numbers — is the policy stamp's job (ADR-0010 §5), which
lands with the full workflow.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import logging
from typing import Any, override

from swe_lab.datasets.instance import TaskInstance
from swe_lab.evaluation.unit_test import (
    BaselineVerifyObserver,
    ENTRYSCRIPT_NAME,
)
from swe_lab.harnesses import Harness
from swe_lab.rollout import outcome_of, PROMPT_NAME
from swe_lab.sandbox import (
    AgentAsset,
    ArtifactSchema,
    Contribution,
    ExecResult,
    Inline,
    merge_mounts,
    Mount,
    Mounts,
    SandboxFs,
    SandboxObserver,
)
from swe_lab.sandbox.observers import BASE_REF_NAME
from swe_lab.workflow import AttemptResult, InputsBuilder, Task

from .guidebook import (
    GUIDEBOOK_NAME,
    RUBRIC_FIELDS,
    STAGE_FIELDS,
    validate_guidebook,
)
from .sample import (
    FAILED_CONVERSATION_NAME,
    FAILED_PATCH_NAME,
    FAILED_VERDICT_NAME,
    FAILURE_NAMES,
)

_logger = logging.getLogger(__name__)

# The reference solution, staged for the Oracle alone — when the dataset
# records one; a dataset without it is a supported input, briefed as such.
GOLD_PATCH_NAME = "gold_patch.diff"

# Metric names, unqualified by any harness: one run has one guidebook.
PRESENT_METRIC = "guidebook.present"
VALID_METRIC = "guidebook.valid"
STAGES_METRIC = "guidebook.stages"


def privileged_mounts(instance: TaskInstance[Any]) -> Mounts:
  """Stage what the Oracle may see and the actor never did.

  The grading procedure is compiled to apply the **failed** patch, so the
  Oracle can reproduce the verdict it is explaining by running the same
  script the grader ran; the dataset's own grading files come with it.

  Args:
    instance: The instance under analysis.

  Returns:
    The compiled grading procedure and its files, plus the reference patch
    when the dataset has one.
  """
  spec = instance.unit_test_spec(apply_patch=True, patch_name=FAILED_PATCH_NAME)
  mounts = merge_mounts(
      dict(spec.mounts),
      {
          ENTRYSCRIPT_NAME: Mount(
              Inline(spec.eval_script.encode()), executable=True
          )
      },
  )
  gold = instance.gold_patch()
  if gold is not None:
    mounts = merge_mounts(
        mounts, {GOLD_PATCH_NAME: Mount(Inline(gold.encode()), read_only=True)}
    )
  return mounts


def build_oracle_prompt(instance: TaskInstance[Any]) -> str:
  """Write the Oracle's brief for one failed instance.

  The brief carries the failed actor's task statement **verbatim and whole**,
  names every file the Oracle has, and states the guidebook's shape and its
  rules. The two rules that earned their place the hard way: quote the task
  statement whole rather than in excerpt — an absence claim ("the interface
  says nothing about X") can only be checked against the full text, and a
  guidebook once got one wrong — and a verification stage has to say what a
  green suite cannot show, because the failed actor's own suite was green.

  Args:
    instance: The instance under analysis.

  Returns:
    The brief, as Markdown.
  """
  spec = instance.sandbox_spec()
  fix = instance.solution_sha()
  history = (
      f"Its git history is intact: the upstream fix commit is `{fix}`"
      f" (`git show {fix}` shows it, `git diff {spec.base_commit} {fix}`"
      " the whole change)."
      if fix
      else "Its git history is intact, but the dataset records no upstream"
      " fix commit for this task."
  )
  files = [
      (
          FAILED_CONVERSATION_NAME,
          "the failed agent's full conversation — every tool call and"
          " result, as typed JSON",
      ),
      (
          FAILED_VERDICT_NAME,
          "the grader's verdict on its patch; `summary` names the tests it"
          " failed",
      ),
      (FAILED_PATCH_NAME, "the patch it submitted"),
  ]
  # A dataset without a reference patch gets a brief that says so — every
  # sentence below that mentions the reference is conditioned on this, so the
  # Oracle is never told to read a file it does not have.
  has_reference = instance.gold_patch() is not None
  if has_reference:
    files.append((GOLD_PATCH_NAME, "the reference solution"))
  grading = instance.unit_test_spec(
      apply_patch=True, patch_name=FAILED_PATCH_NAME
  )
  files.append(
      (
          ENTRYSCRIPT_NAME,
          "the exact grading procedure, as the grader runs it. It resets the"
          f" repository, applies `{FAILED_PATCH_NAME}` and runs the graded"
          f' tests — run `bash "$SANDBOX_WORKSPACE/{ENTRYSCRIPT_NAME}"` to'
          " reproduce the failure (it discards any edits you made first)",
      )
  )
  files.extend(
      (name, "a file the grading procedure reads")
      for name in sorted(grading.mounts)
  )
  table = "\n".join(f"| `{name}` | {what} |" for name, what in files)
  fields = "\n".join(f"**{name}.** …" for name in STAGE_FIELDS)
  rubric_fields = "\n".join(f"**{name}.** …" for name in RUBRIC_FIELDS)
  statement = instance.prompt().rstrip("\n")
  privileges = (
      "its full conversation, the grader's verdict, the reference solution"
      " and the grading procedure"
      if has_reference
      else "its full conversation, the grader's verdict and the grading"
      " procedure"
  )
  diagnose = (
      "Read the verdict, then the failed patch\n   against the reference,"
      " then the conversation."
      if has_reference
      else "Read the verdict, then the failed patch,\n   then the"
      " conversation."
  )
  title = f"Oracle brief: a guidebook for `{instance.instance_id}`"
  return f"""# {title}

You are the **Oracle** in a training-data pipeline. A coding agent already
attempted the task below and failed its graded tests. You have what it never
had — {privileges} — and one job: write **`guidebook.md`**, a staged
tutorial that lets a *future, blind* agent (same task statement, no privileged
information, no memory of this attempt) solve the task correctly.

## What you have

Every file below is in the run's workspace directory, `$SANDBOX_WORKSPACE`
(an environment variable in your shell; `echo "$SANDBOX_WORKSPACE"` prints
it). The repository is at `{spec.workdir}`, checked out at the commit the
failed agent started from, `{spec.base_commit}`. {history}

| File | What it is |
|---|---|
{table}

## The task statement the failed agent received, verbatim

<<<TASK_STATEMENT
{statement}
TASK_STATEMENT>>>

## Method

1. **Diagnose before you write.** {diagnose} Find the exact decision at
   which the attempt went wrong and the evidence in the conversation for why
   the agent made it. Reproduce the failure with the grading procedure when
   that is what it takes to be sure. A guidebook written from a vague sense
   that the agent "should have been more careful" teaches nothing.
2. **Check every claim about the task statement against the task
   statement.** Before you write that it "says", "does not say", "is silent
   about" or "implies" something, re-read the whole field and quote it whole.
   A guidebook once asserted that an interface block was silent about a
   placement it in fact stated; a blind agent can refute that in one command,
   and a hint that loses that argument is worse than no hint.
3. **Write the guidebook**, then re-read it as the blind agent would: every
   claim it can check, it will.

## The guidebook

Write it to `$SANDBOX_WORKSPACE/{GUIDEBOOK_NAME}`, as Markdown, in exactly this
shape — the file is machine-checked for the compact rubric, stage headings,
and their bold fields, and a guidebook missing any of them is rejected:

```markdown
# Guidebook — <one line naming the change>

Instance: `{instance.instance_id}`
Repo: <owner/name> @ `{spec.base_commit}`
Unit under change: <the function / class / module>

<How many stages, and which stage holds the decision this guidebook exists to
get right.>

---

## Supervisor rubric

{rubric_fields}

---

## Stage 1 — <title>

{fields}

---

## Stage 2 — <title>
…
```

Four to six stages is typical. A stage that changes code may add `**Edits.**`
and `**Tests.**` after the five required fields.

The rules:

- **The rubric is compact and the tutorial stays complete.** Summarize the
  tutorial's checkpoints and observable signals; do not replace stages with
  the rubric or introduce a claim the tutorial does not support.
- **Self-correction signals are diagnostic only.** They explain recorded
  telemetry for later analysis. Only off-track signals bear on whether the
  Supervisor speaks; self-correction signals never veto that decision.
- **Never say or imply that you saw the answer.** No "the reference does X",
  no diff summary, no test names the blind agent could not have found. The
  guidebook reads as a tutorial written by someone who understands the
  codebase and the task, not by someone holding the solution.
- **Every stage's `Justification` is derivable by a blind agent** from the
  task statement, the repository at `{spec.base_commit}`, and the stages
  before it — nothing else. If the honest justification would have to cite
  the reference or the graded tests, the stage is too specific: back it up to
  the observation that would have led there.
- **Quote the task statement whole, never in excerpt.** When a stage rests on
  what the statement says, quote the entire field or paragraph it draws on,
  verbatim. An excerpt cannot support a claim about what the text does *not*
  say, and those are exactly the claims that decide placement, naming and
  interface questions.
- **Make the failing stage a decision, not a formality.** Name the fork the
  failed agent got wrong, the observation that shows it *is* a fork, and how
  to resolve it without guessing. If the statement genuinely underdetermines
  it, say what satisfies every reading rather than picking one.
- **The verification stage says what a green suite cannot tell you.** A
  passing suite says only that you broke nothing it covers. The graded tests
  are usually not in the working tree the agent works in, so look at what the
  failed agent ran and what its green result could and could not show; then
  name a check that *does* discriminate — usually exercising the new behavior
  directly, through every access path the statement names.
- **Direction, not specifics.** Each stage points at what to look at and what
  you would see if you are on track; the derivation stays the agent's. A stage
  the agent merely executes teaches nothing.
"""


def oracle_prompt(
    sb: SandboxFs, instance: TaskInstance[Any]
) -> Mapping[str, bytes]:
  """Build the Oracle's brief as the task's prompt input.

  Args:
    sb: Unused — the brief is the instance's, not the workspace's.
    instance: The instance under analysis.

  Returns:
    The prompt input, by store name.
  """
  del sb
  return {PROMPT_NAME: build_oracle_prompt(instance).encode("utf-8")}


@dataclass
class GuidebookObserver(SandboxObserver):
  """Collect the guidebook the Oracle wrote, and check its shape.

  Single-run, like every stateful observer: construct a fresh one per run.

  Attributes:
    guidebook: The guidebook text, once ``before_destroy`` found it; ``None``
      while it has not run, or when the Oracle wrote nothing.
    problems: What the schema check found wrong with it; empty when valid or
      absent.
  """

  guidebook: str | None = None
  problems: tuple[str, ...] = ()

  @property
  def valid(self) -> bool:
    """Whether a guidebook was written and passed the schema check."""
    return self.guidebook is not None and not self.problems

  @override
  def output_schema(self) -> tuple[ArtifactSchema, ...]:
    """Declare the guidebook — required: a run without one produced nothing."""
    return (
        ArtifactSchema(
            GUIDEBOOK_NAME,
            description="the Oracle's staged guidebook for a blind actor",
        ),
    )

  @override
  def before_destroy(self, sb: SandboxFs) -> Contribution | None:
    """Read the guidebook back, validate it, and register it.

    Args:
      sb: The still-live sandbox.

    Returns:
      The guidebook as an inline artifact plus the presence / validity /
      stage-count metrics; presence alone when nothing was written.
    """
    if not sb.exists(GUIDEBOOK_NAME):
      return Contribution(metrics={PRESENT_METRIC: 0.0})
    text = sb.read(GUIDEBOOK_NAME).decode("utf-8", "backslashreplace")
    self.guidebook = text
    self.problems = tuple(validate_guidebook(text, require_rubric=True))
    if self.problems:
      _logger.warning("guidebook rejected: %s", "; ".join(self.problems))
    return Contribution(
        inline_artifacts={GUIDEBOOK_NAME: text.encode("utf-8")},
        metrics={
            PRESENT_METRIC: 1.0,
            VALID_METRIC: float(self.valid),
            STAGES_METRIC: float(
                text.count("\n## Stage ") + text.startswith("## Stage ")
            ),
        },
    )


@dataclass
class OracleAnalysisTask(Task):
  """The Oracle writes a guidebook for an instance's cached failure.

  Composes the harness's own mounts, observers and assets around one main
  action, exactly as the rollout does, but with a different set of extras:
  the instance's failure material (its own mounts), the grading procedure and
  — when the dataset records one — the reference patch (``privileged_mounts``;
  a dataset without one is supported and briefed as such), and a
  ``GuidebookObserver``
  in place of the diff extractor. **No git-history purge and no result
  verifier** — see the module docstring, and the named test that pins it.

  Attributes:
    harness: The agent to run as the Oracle. It supplies its own mounts,
      observers, the main action, the trace conversion and the completion
      signal.
    inputs_builder: How the brief gets built when nothing else supplies it;
      the default writes it from the instance. ``None`` in a chain whose
      earlier task produces ``prompt.md``.
    env: Extra environment for the agent process, handed to the harness. Not
      the place for a secret — use the sandbox's ``pass_env``.
    instructions: Optional model instructions for Oracle prompt variants.
      ``None`` preserves the instance-built default.
  """

  harness: Harness
  # Redeclared only to change the base's default; `kw_only` restated so the
  # defaulted field stays keyword-only behind the positional `harness`.
  inputs_builder: InputsBuilder | None = field(
      default=oracle_prompt, kw_only=True
  )
  env: Mapping[str, str] | None = None
  instructions: str | None = None

  @override
  def mounts(self, instance: TaskInstance[Any]) -> Mounts:
    """Stage the failure, the harness's files, and the privileged material.

    The failure has to come from the instance: an ordinary instance would
    assemble just as well, with a brief that says three files exist that do
    not, and the agent budget spent finding out. So the check happens here —
    at assembly, before anything is staged or started — against the neutral
    names of the failure-sample contract, not a concrete dataset.

    Args:
      instance: The instance under analysis; its own mounts carry the
        failure.

    Returns:
      The merged staging set (duplicate targets refused).

    Raises:
      ValueError: If the instance stages no failure to analyze.
    """
    own = super().mounts(instance)
    missing = [name for name in FAILURE_NAMES if name not in own]
    if missing:
      raise ValueError(
          f"instance {instance.instance_id!r} stages no failure to analyze"
          f" (missing {missing}); oracle_analysis runs over a record that"
          " carries a cached failure, such as an oracle_failures row"
          " (--dataset oracle_failures)"
      )
    return merge_mounts(
        own,
        self.harness.mounts(instance.sandbox_spec().workdir),
        privileged_mounts(instance),
    )

  @override
  def assets(self) -> Sequence[AgentAsset]:
    """Declare whatever the composed agent says it needs."""
    return self.harness.assets()

  @override
  def observers(self, instance: TaskInstance[Any]) -> Sequence[SandboxObserver]:
    """Return baseline verification, harness observers and the collector.

    A baseline-patched failure carries its recorded base ref as an instance
    mount. Verify and restore that tree before the Oracle can run the exposed
    grading procedure. Nothing else is added: in particular no history purge,
    which would strip the material the Oracle is given, and no result verifier,
    which would flag a run that is contaminated by design.

    Args:
      instance: The failure record whose mounts identify baseline mode.

    Returns:
      Optional baseline verification, the harness's observers, then a fresh
      ``GuidebookObserver``.
    """
    baseline = (
        (BaselineVerifyObserver(workdir=instance.sandbox_spec().workdir),)
        if BASE_REF_NAME in instance.mounts()
        else ()
    )
    return (*baseline, *self.harness.observers(), GuidebookObserver())

  @override
  def input_schema(self) -> Sequence[ArtifactSchema]:
    """Declare the brief as the one input."""
    return (ArtifactSchema(PROMPT_NAME, description="the Oracle's brief"),)

  @override
  def action(
      self, sb: SandboxFs, instance: TaskInstance[Any], *, timeout: float
  ) -> ExecResult:
    """Run the agent against the staged brief.

    Args:
      sb: The live sandbox to run in.
      instance: Unused — the default brief reached the workspace before this
        ran.
      timeout: Seconds before the agent run is killed.

    Returns:
      The agent execution's outcome.
    """
    del instance
    prompt = self.instructions
    if prompt is None:
      prompt = sb.read(PROMPT_NAME).decode("utf-8", "backslashreplace")
    return self.harness.run(sb, prompt=prompt, timeout=timeout, env=self.env)

  @override
  def outputs_valid(self, result: AttemptResult) -> bool:
    """Require a guidebook that passes the schema, on top of the baseline.

    Args:
      result: The execution to judge.

    Returns:
      Whether the attempt produced a valid guidebook.
    """
    observer = guidebook_of(result)
    return (
        super().outputs_valid(result)
        and observer is not None
        and observer.valid
    )

  @override
  def should_retry(self, result: AttemptResult) -> bool:
    """Retry an invalid attempt, and an ending that happened *to* the agent.

    The rollout's fairness argument (ADR-0011) does not bind here — no
    benchmark number rides on an Oracle run — so an attempt that produced no
    valid guidebook is simply retried within the budget, plus the harness's
    own retryable endings.

    Args:
      result: The attempt to judge.

    Returns:
      Whether another attempt is owed.
    """
    if super().should_retry(result):
      return True
    observer = outcome_of(result)
    return observer is not None and observer.outcome.retryable

  @override
  def record_extra(self, result: AttemptResult) -> Mapping[str, object]:
    """Record the agent's ending and what the schema check found.

    Args:
      result: The attempt being recorded.

    Returns:
      ``agent_outcome`` when a harness observer ran, and
      ``guidebook_problems`` when the check rejected the guidebook.
    """
    extra: dict[str, object] = {}
    observer = outcome_of(result)
    if observer is not None:
      extra["agent_outcome"] = observer.outcome.value
    guidebook = guidebook_of(result)
    if guidebook is not None and guidebook.problems:
      extra["guidebook_problems"] = list(guidebook.problems)
    return extra


def guidebook_of(result: AttemptResult) -> GuidebookObserver | None:
  """Return the guidebook observer an Oracle execution composed.

  Args:
    result: The execution to read.

  Returns:
    The observer (it carries the guidebook and the check's findings), or
    ``None`` if the result came from a task that composed none.
  """
  return next(
      (o for o in result.observers if isinstance(o, GuidebookObserver)), None
  )
