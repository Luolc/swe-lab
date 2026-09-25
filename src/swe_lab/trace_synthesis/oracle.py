"""Phase B of trace synthesis: the Oracle writes a guidebook for an attempt.

``OracleAnalysisTask`` runs a harness against an instance whose attempt is
already in hand, with everything the actor never had: the reference patch
(when the dataset records one), the exact grading procedure, and the
repository's **unpurged** git history. Its one output is ``guidebook.md``: a
staged tutorial for a future blind actor alongside a compact supervisor-facing
rubric, measured against the schema in
:mod:`swe_lab.trace_synthesis.guidebook`.

**The attempt is not always a failed one, and the brief says which it was.**
The from-scratch chain runs this task whatever the blind verdict was
(ADR-0023 §2), so the Oracle is briefed from the staged verdict: a failed
attempt gets the brief that diagnoses why, a passed one the brief that finds
which of its steps were guessed rather than derived and makes them
reproducible. Either way it writes a guidebook — there is no refusal path
(ADR-0027).

The attempt arrives as declared inputs, exactly as the solving pipeline
produced it — the rollout's ``conversation.json``, ``patch.diff`` and
``patch.base_ref.txt``, the grading entry's ``unit_test.verdict.json`` — fed by
a workflow edge from phase A, or by a caller's own bytes. An edge matches by
store name, so the Oracle's input names *are* the producers' names; nothing is
renamed on the way.

The task is deliberately contaminated, and says so by construction rather than
by flag: it composes no git-history purge, no diff extraction and no result
verifier, because none of the three describes what this run is. A guidebook is
not a patch, and a run that may be handed the answer — the reference patch
and the unpurged history, whenever the dataset records them — has nothing to
be verified against. The integrity consequence — that such a run's records are
never pooled with benchmark numbers — is the policy stamp's job (ADR-0010
§5), and no record carries that stamp today: what separates this run's
records is only their entry key.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import json
import logging
from typing import Any, override

from swe_lab.conversation.observer import CONVERSATION_NAME
from swe_lab.datasets.instance import TaskInstance
from swe_lab.evaluation.unit_test import (
    ARTIFACT_NAMESPACE,
    BaselineVerifyObserver,
    ENTRYSCRIPT_NAME,
    VERDICT_NAME,
)
from swe_lab.evaluation.verdict import UnitTestSpec
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
    qualified_name,
    SandboxError,
    SandboxFs,
    SandboxObserver,
)
from swe_lab.sandbox.observers import BASE_REF_NAME, PATCH_NAME
from swe_lab.workflow import AttemptResult, InputsBuilder, Task

from .guidebook import (
    GUIDEBOOK_NAME,
    RUBRIC_FIELDS,
    STAGE_FIELDS,
    validate_guidebook,
)

_logger = logging.getLogger(__name__)

# The reference solution, staged for the Oracle alone — when the dataset
# records one; a dataset without it is a supported input, briefed as such.
GOLD_PATCH_NAME = "gold_patch.diff"


# The attempt as the solving pipeline produces it: the rollout's three
# artifacts and the grading entry's verdict, under the store names their
# producers declare. The base ref is required because the shipped rollout diffs
# against the pre-agent baseline (ADR-0014), and a grading procedure that reset
# to ``base_commit`` instead would grade a different tree than the one the
# patch was taken from.
ATTEMPT_VERDICT_NAME = qualified_name(ARTIFACT_NAMESPACE, VERDICT_NAME)

# Metric names, unqualified by any harness: one run has one guidebook.
PRESENT_METRIC = "guidebook.present"
VALID_METRIC = "guidebook.valid"
STAGES_METRIC = "guidebook.stages"


def _grading_spec(instance: TaskInstance[Any]) -> UnitTestSpec[Any]:
  """Compile the grading procedure to apply the attempt's patch.

  Args:
    instance: The instance under analysis.

  Returns:
    The compiled spec — the script the grader ran, aimed at the attempt and
    reset to its recorded baseline first.
  """
  return instance.unit_test_spec(
      apply_patch=True, patch_name=PATCH_NAME, patch_baseline=True
  )


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
  spec = _grading_spec(instance)
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


def build_oracle_prompt(instance: TaskInstance[Any], *, resolved: bool) -> str:
  """Write the Oracle's brief for one attempted instance.

  The brief carries the prior actor's task statement **verbatim and whole**,
  names every file the Oracle has, and states the guidebook's shape and its
  rules. The two rules that earned their place the hard way: quote the task
  statement whole rather than in excerpt — an absence claim ("the interface
  says nothing about X") can only be checked against the full text, and a
  guidebook once got one wrong — and a verification stage has to say what a
  green suite cannot show, because the prior actor's own suite was green.

  **Two briefs, one per verdict** (ADR-0027). A failed attempt is diagnosed:
  find the decision that went wrong, and teach the fork that resolves it. A
  passed attempt is *not* — it is read for the steps that were **guessed
  rather than derived**, which the guidebook then makes deliberate and
  reproducible. Telling an Oracle that a run which passed had failed is not a
  harmless framing: one live Oracle refused the premise and asked an operator
  who was not there, in a headless run, and wrote nothing.

  Args:
    instance: The instance under analysis.
    resolved: Whether the attempt passed its graded tests, from its own
      verdict. Required rather than defaulted: a default here is a guess
      about what happened, and the brief's first sentence states it as fact.

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
  # The agent whose attempt is being explained — named for what its verdict
  # says it did, so no sentence of the brief contradicts the verdict beside it.
  actor = "successful agent" if resolved else "failed agent"
  files = [
      (
          CONVERSATION_NAME,
          f"the {actor}'s full conversation — every tool call and"
          " result, as typed JSON",
      ),
      (
          ATTEMPT_VERDICT_NAME,
          "the grader's verdict on its patch; `summary` names the graded"
          " tests it passed"
          if resolved
          else "the grader's verdict on its patch; `summary` names the tests"
          " it failed",
      ),
      (PATCH_NAME, "the patch it submitted"),
      (
          BASE_REF_NAME,
          "the commit the submitted patch was diffed against — the grading"
          " procedure verifies the tree and resets to it before applying",
      ),
  ]
  # A dataset without a reference patch gets a brief that says so — every
  # sentence below that mentions the reference is conditioned on this, so the
  # Oracle is never told to read a file it does not have.
  has_reference = instance.gold_patch() is not None
  if has_reference:
    files.append((GOLD_PATCH_NAME, "the reference solution"))
  grading = _grading_spec(instance)
  files.append(
      (
          ENTRYSCRIPT_NAME,
          "the exact grading procedure, as the grader runs it. It resets the"
          f" repository, applies `{PATCH_NAME}` and runs the graded"
          f' tests — run `bash "$SANDBOX_WORKSPACE/{ENTRYSCRIPT_NAME}"` to'
          " reproduce the verdict (it discards any edits you made first)",
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
      "Read the verdict, then the submitted patch\n   against the reference,"
      " then the conversation."
      if has_reference
      else "Read the verdict, then the submitted patch,\n   then the"
      " conversation."
  )
  # The one job, and the reading that leads to it — the whole difference
  # between the two briefs. A passed attempt is not narrated as a failure: it
  # is read for the steps nothing in the evidence forced, which are exactly
  # the ones a blind agent can get wrong.
  job = (
      "solve the task correctly, and reach that solution by evidence"
      " rather than by luck."
      if resolved
      else "solve the task correctly."
  )
  premise = (
      """
**This attempt passed.** Do not write it up as a failure and do not invent
one: a guidebook that narrates a failure which did not happen is wrong about
the only run it has evidence for. An attempt that passed still contains steps
that were **guessed rather than derived** — a name, a placement, an interface
choice, an edge case the agent picked with nothing in the task statement or
the repository forcing it, and it happened to be right. A blind agent
repeating this task can guess differently. Those steps are what this guidebook
exists to make deliberate.
"""
      if resolved
      else ""
  )
  method_one = (
      f"""1. **Find what was guessed, not what went wrong.** {diagnose} For
   every decision that shaped the patch, ask what in the task statement, the
   repository or an earlier stage *forced* it — and mark the ones nothing
   did. Those are the guesses. Reproduce the result with the grading
   procedure when that is what it takes to be sure of what actually passed. A
   guidebook written from a vague sense that the agent "did it right"
   teaches nothing."""
      if resolved
      else f"""1. **Diagnose before you write.** {diagnose} Find the exact
   decision at which the attempt went wrong and the evidence in the
   conversation for why the agent made it. Reproduce the failure with the
   grading procedure when that is what it takes to be sure. A guidebook
   written from a vague sense that the agent "should have been more careful"
   teaches nothing."""
  )
  decision_rule = (
      """- **Make the guessed step a decision, not a formality.** Name the
  fork the successful agent resolved without evidence, the observation that
  shows it *is* a fork, and how to resolve it without guessing. If the
  statement genuinely underdetermines it, say what satisfies every reading
  rather than picking one."""
      if resolved
      else """- **Make the failing stage a decision, not a formality.** Name
  the fork the failed agent got wrong, the observation that shows it *is* a
  fork, and how to resolve it without guessing. If the statement genuinely
  underdetermines it, say what satisfies every reading rather than picking
  one."""
  )
  title = f"Oracle brief: a guidebook for `{instance.instance_id}`"
  return f"""# {title}

You are the **Oracle** in a training-data pipeline. A coding agent already
attempted the task below and {"passed" if resolved else "failed"} its graded
tests. You have what it never had — {privileges} — and one job: write
**`guidebook.md`**, a staged tutorial that lets a *future, blind* agent (same
task statement, no privileged information, no memory of this attempt)
{job}
{premise}
## What you have

Every file below is in the run's workspace directory, `$SANDBOX_WORKSPACE`
(an environment variable in your shell; `echo "$SANDBOX_WORKSPACE"` prints
it). The repository is at `{spec.workdir}`, checked out at the commit the
{actor} started from, `{spec.base_commit}`. {history}

| File | What it is |
|---|---|
{table}

## The task statement the {actor} received, verbatim

<<<TASK_STATEMENT
{statement}
TASK_STATEMENT>>>

## Method

{method_one}
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
shape — the compact rubric, the stage headings and their bold field labels are
read mechanically, and what that read finds is recorded with the run:

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

Four to six stages is typical. Every stage carries exactly the five labels
above and no others, whatever it does: a stage that changes code states the
edits it makes under `**Actions.**` and the tests it runs under
`**Expected observations.**` — do not give either a label of its own.

The rules:

- **The rubric is compact and the tutorial stays complete.** Summarize the
  tutorial's checkpoints and observable signals; do not replace stages with
  the rubric or introduce a claim the tutorial does not support.
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
{decision_rule}
- **The verification stage says what a green suite cannot tell you.** A
  passing suite says only that you broke nothing it covers. The graded tests
  are usually not in the working tree the agent works in, so look at what the
  {actor} ran and what its green result could and could not show; then
  name a check that *does* discriminate — usually exercising the new behavior
  directly, through every access path the statement names.
- **Direction, not specifics.** Each stage points at what to look at and what
  you would see if you are on track; the derivation stays the agent's. A stage
  the agent merely executes teaches nothing.
"""


def attempt_resolved(sb: SandboxFs) -> bool:
  """Read from the staged verdict whether the attempt passed its graded tests.

  Which brief the Oracle gets is a claim about what happened, and phase B runs
  whatever the verdict was (ADR-0023 §2), so the claim is **read, not
  assumed**. The verdict is staged before any input is built, so this runs in
  the session, off the same bytes the Oracle itself will read.

  Args:
    sb: The live sandbox, with the attempt already staged.

  Returns:
    The verdict's ``resolved`` flag.

  Raises:
    SandboxError: The verdict is absent, is not JSON, or carries no boolean
      ``resolved`` — the same class of refusal as an input nobody staged, and
      raised in the same place, before the agent is launched. Defaulting
      instead would put a guess in the brief's first sentence and state it as
      fact, which is the failure this branch exists to end, in a form nobody
      would see.
  """
  if not sb.exists(ATTEMPT_VERDICT_NAME):
    raise SandboxError(
        f"required input(s) missing: [{ATTEMPT_VERDICT_NAME!r}] — the"
        " Oracle's brief is written from the verdict, so supply it (a"
        " workflow edge or the caller's bytes)"
    )
  raw = sb.read(ATTEMPT_VERDICT_NAME).decode("utf-8", "backslashreplace")
  try:
    facts = json.loads(raw)
  except json.JSONDecodeError as error:
    raise SandboxError(
        f"the verdict at {ATTEMPT_VERDICT_NAME!r} is not JSON: {error}"
    ) from error
  resolved = facts.get("resolved") if isinstance(facts, dict) else None
  if not isinstance(resolved, bool):
    raise SandboxError(
        f"the verdict at {ATTEMPT_VERDICT_NAME!r} carries no boolean 'resolved'"
        f" (got {resolved!r}); Verdict.facts() always does"
    )
  return resolved


def oracle_prompt(
    sb: SandboxFs, instance: TaskInstance[Any]
) -> Mapping[str, bytes]:
  """Build the Oracle's brief for the attempt declared as inputs.

  Args:
    sb: The live sandbox — read for the staged verdict, which picks the brief.
    instance: The instance under analysis.

  Returns:
    The prompt input, by store name.
  """
  return {
      PROMPT_NAME: build_oracle_prompt(
          instance, resolved=attempt_resolved(sb)
      ).encode("utf-8")
  }


@dataclass
class GuidebookObserver(SandboxObserver):
  """Collect the guidebook the Oracle wrote, and measure its shape.

  **The schema check is a metric, not a gate** (ADR-0027): what it finds is
  recorded — ``guidebook.valid`` and, on the record, ``guidebook_problems`` —
  and an imperfect guidebook goes downstream and gets used. Nothing here
  fails an attempt or asks for a retry over a label.

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
    """Read the guidebook back, measure it, and register it.

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
    self.problems = tuple(validate_guidebook(text))
    if self.problems:
      # Recorded, never enforced: the guidebook is used either way.
      _logger.warning("guidebook problems: %s", "; ".join(self.problems))
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
  """The Oracle writes a guidebook for an attempt at an instance.

  Composes the harness's own mounts, observers and assets around one main
  action, exactly as the rollout does, but with a different set of extras:
  the attempt (this task's declared inputs), the grading procedure and — when
  the dataset records one — the reference patch (``privileged_mounts``; a
  dataset without one is supported and briefed as such), and a
  ``GuidebookObserver`` in place of the diff extractor. **No git-history purge
  and no result verifier** — see the module docstring, and the named test that
  pins it.

  Attributes:
    harness: The agent to run as the Oracle. It supplies its own mounts,
      observers, the main action, the trace conversion and the completion
      signal.
    inputs_builder: How the brief gets built when nothing else supplies it;
      the default writes it from the instance and the staged verdict.
      ``None`` in a chain whose earlier task produces ``prompt.md``.
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
    """Stage the harness's files and the privileged material.

    The attempt itself is declared as inputs, so the workflow supplies it: a
    missing one is its distinct edge failure (ADR-0007 §5) or, standalone, the
    assembly error ``execute`` raises for any input nobody staged.

    Args:
      instance: The instance under analysis.

    Returns:
      The merged staging set (duplicate targets refused).
    """
    return merge_mounts(
        super().mounts(instance),
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

    The attempt carries its recorded base ref as a declared input. Verify and
    restore that tree before the Oracle can run the exposed grading procedure.
    Nothing else is added: in particular no history purge, which would strip
    the material the Oracle is given, and no result verifier, which would flag
    a run that is contaminated by design.

    Args:
      instance: The instance under analysis.

    Returns:
      Baseline verification, the harness's observers, then a fresh
      ``GuidebookObserver``.
    """
    return (
        BaselineVerifyObserver(workdir=instance.sandbox_spec().workdir),
        *self.harness.observers(),
        GuidebookObserver(),
    )

  @override
  def input_schema(self) -> Sequence[ArtifactSchema]:
    """Declare the brief and the attempt it is written about.

    Returns:
      The brief plus the four files the solving pipeline produced.
    """
    return (
        ArtifactSchema(PROMPT_NAME, description="the Oracle's brief"),
        ArtifactSchema(
            CONVERSATION_NAME,
            description="the blind rollout's typed conversation",
        ),
        ArtifactSchema(
            PATCH_NAME,
            description="the patch the rollout submitted",
        ),
        ArtifactSchema(
            BASE_REF_NAME,
            description="the sha the submitted patch was diffed against",
        ),
        ArtifactSchema(
            ATTEMPT_VERDICT_NAME,
            description="the grader's verdict on the submitted patch",
        ),
    )

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
  def should_retry(self, result: AttemptResult) -> bool:
    """Retry an ending that happened *to* the agent — never a schema result.

    There is deliberately **no schema clause here** (ADR-0027). A guidebook
    that fails the label check is not bad luck, it is what the model wrote:
    re-running buys another sample of the same writer at the price of a paid
    agent run. Validity is a metric; nothing retries on it.

    What is left is the baseline — a run that ended anything but ``SUCCESS``,
    or produced no guidebook **at all**, which is a missing declared output
    rather than a judgement about one — plus the harness's own retryable
    endings: a container that would not start, a network failure, a crash.

    **This is the policy, not what the shipped entries do.** Every entry
    carrying this task runs at the default ``retries=0``
    (``test_the_shipped_oracle_entries_carry_no_retry_budget``), so
    ``run_task`` runs one attempt and this answer only decides whether to
    break out of a loop that has already ended: **by default, an Oracle run
    is never retried, whatever happened to it.** That is a decision about
    spending someone else's quota rather than a claim that those endings are
    unworthy of another attempt — they are exactly the ones that are. A
    caller who wants the behaviour above asks for it per run
    (``--oracle_analysis.retries=N``, which reaches the entry field), and
    then gets this policy instead of the base class's.

    Args:
      result: The attempt to judge.

    Returns:
      Whether another attempt is owed, if anyone is spending.
    """
    if super().should_retry(result):
      return True
    observer = outcome_of(result)
    return observer is not None and observer.outcome.retryable

  @override
  def record_extra(self, result: AttemptResult) -> Mapping[str, object]:
    """Record the agent's ending and what the schema check found.

    ``guidebook_problems`` is evidence, not a verdict: the attempt it
    describes succeeded, and the guidebook went downstream (ADR-0027).

    Args:
      result: The attempt being recorded.

    Returns:
      ``agent_outcome`` when a harness observer ran, and
      ``guidebook_problems`` when the check found anything wrong.
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
