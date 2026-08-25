"""The harness seam: a brand-new harness composes with zero engine change.

Spec Success #3 — "a second harness (or a stub) registers **without touching the
engine**." This stub implements the ``Harness`` ABC and runs end-to-end through
the real ``SandboxManager`` + ``ConversationObserver`` + ``GitHubJobSandbox``
(local bash, no Docker), importing only the public engine surface — nothing in
``sandbox/`` or ``conversation/`` is modified to make it work.
"""

from collections.abc import Iterator, Mapping
import contextlib
from pathlib import Path
from typing import final, override

from etils import epath

from swe_lab.conversation import (
    Conversation,
    ConversationObserver,
    Message,
    Role,
    TextBlock,
)
from swe_lab.datasets.instance import TaskInstance
from swe_lab.evaluation.verdict import UnitTestSpec, Verdict
from swe_lab.harnesses import (
    AgentOutcome,
    COMPLETE_METRIC,
    Harness,
    HarnessOutcomeObserver,
)
from swe_lab.rollout import (
    CodingAgentTask,
    conversation_of,
    outcome_of,
)
from swe_lab.sandbox import (
    Contribution,
    ExecResult,
    GitHubJobSandbox,
    Inline,
    Mount,
    Mounts,
    RunStatus,
    SandboxFs,
    SandboxManager,
    SandboxObserver,
    SandboxSpec,
)
from swe_lab.sandbox.observers import PATCH_NAME

_SPEC = SandboxSpec("acme__widget-1", "acme/widget:tag", "/app", "abc123")
_TRACE_NAME = "stub.trace"


@final
class _Instance(TaskInstance[Verdict]):
  """The instance the task binds: this spec's run context and a statement."""

  instance_id = "acme__widget-1"

  @override
  def sandbox_spec(self) -> SandboxSpec:
    return _SPEC

  @override
  def prompt(self) -> str:
    return "SOLVE THIS"

  @override
  def gold_patch(self) -> str | None:
    return None

  @override
  def unit_test_spec(
      self,
      *,
      apply_patch: bool,
      patch_name: str = PATCH_NAME,
      checkout_golden_tests: bool = True,
      patch_baseline: bool = False,
  ) -> UnitTestSpec[Verdict]:
    raise NotImplementedError("this instance is only solved, never graded")


class StubHarness(Harness):
  """A minimal off-the-shelf-agent stand-in: stage a script, emit a trace."""

  @property
  @override
  def name(self) -> str:
    return "stub"

  @override
  def observers(self) -> tuple[SandboxObserver, ...]:
    # A foreign harness picks its own observers (ADR-0007 §3); the generic
    # pair are reusable building blocks, and choosing them is this stub's
    # decision, not an inherited default.
    return (
        ConversationObserver(producer=self),
        HarnessOutcomeObserver(harness=self),
    )

  @override
  def mounts(self, workdir: str) -> Mounts:
    del workdir
    # No pinned binary to fold in — the whole "agent" is this one script (an
    # asset would just be another read-only mount here).
    script = f'echo hello > "$SANDBOX_WORKSPACE/{_TRACE_NAME}"\n'
    return {"stub.sh": Mount(Inline(script.encode()), executable=True)}

  @override
  def run(
      self,
      sb: SandboxFs,
      *,
      prompt: str,
      timeout: float,
      env: Mapping[str, str] | None = None,
  ) -> ExecResult:
    # A foreign harness decides for itself where the prompt lands (ADR-0007
    # §8) and how injected env reaches its agent; this one writes the prompt
    # under its own name and hands env straight to the exec.
    sb.write("stub.prompt", prompt.encode())
    return sb.run_script("stub.sh", timeout=timeout, env=env)

  @override
  def native_outputs(self) -> dict[str, str]:
    return {"trace.txt": _TRACE_NAME}

  @override
  def to_conversation(self, sb: SandboxFs) -> Conversation:
    raw = sb.read(_TRACE_NAME) if sb.exists(_TRACE_NAME) else b""
    text = raw.decode().strip()
    return Conversation(
        messages=[Message(role=Role.ASSISTANT, content=[TextBlock(text=text)])]
    )

  @override
  def outcome(self, sb: SandboxFs) -> AgentOutcome:
    # This "agent" reads as finished once it wrote its own trace — the signal
    # is the harness's business, in its own format. It cannot tell a crash from
    # a budget ending, so an absent trace reads NO_OUTPUT rather than guessing.
    if sb.exists(_TRACE_NAME):
      return AgentOutcome.FINISHED
    return AgentOutcome.NO_OUTPUT


def test_stub_harness_composes_over_the_engine(tmp_path: Path):
  harness = StubHarness()
  observer = ConversationObserver(producer=harness)
  outcome = HarnessOutcomeObserver(harness=harness)
  workspace = tmp_path / "run"
  manager = SandboxManager(
      sandbox=GitHubJobSandbox(spec=_SPEC, workspace=epath.Path(workspace)),
      output_dir=epath.Path(workspace),
      observers=[observer, outcome],
      mounts=harness.mounts(_SPEC.workdir),
  )
  with manager.session() as sb:
    harness.run(sb, prompt="ignored", timeout=10.0)

  # the engine ran the new harness end-to-end, with no engine change
  assert manager.result.status is RunStatus.SUCCESS
  assert (workspace / _TRACE_NAME).read_text() == "hello\n"
  # the shared conversation observer converted + registered the trace artifact
  assert observer.conversation == Conversation(
      messages=[Message(role=Role.ASSISTANT, content=[TextBlock(text="hello")])]
  )
  assert (workspace / "conversation.json").is_file()
  # the two observers split the names: the conversion is the conversation
  # observer's, the raw byproduct + completion the outcome observer's
  assert manager.result.artifacts["conversation.json"] == (
      workspace / "conversation.json"
  )
  # …and the byproduct lands under its artifact name, not the filename it had
  # in the sandbox (`stub.trace`), which is only where it was fetched from.
  landed = manager.result.artifacts["stub.trace.txt"]
  assert landed == workspace / "stub.trace.txt"
  assert landed.read_text() == "hello\n"
  assert outcome.complete is True
  assert manager.result.metrics[COMPLETE_METRIC] == 1.0


def test_the_task_takes_a_foreign_harness_and_proxy(tmp_path: Path):
  # The composition is harness-agnostic: a downstream user's own Harness and
  # their own recorder (any context manager) are injected, and the task
  # never reaches for a concrete agent.
  entered: list[str] = []

  @contextlib.contextmanager
  def stub_proxy() -> Iterator[None]:
    entered.append("open")
    yield
    entered.append("closed")

  workspace = tmp_path / "run"
  task = CodingAgentTask(
      harness=StubHarness(), proxy_factory=stub_proxy, purge_git_history=False
  )
  result = task.execute(
      GitHubJobSandbox(spec=_SPEC, workspace=epath.Path(workspace)),
      _Instance(),
      output_dir=workspace,
      timeout=10.0,
  )

  assert result.run.status is RunStatus.SUCCESS
  assert entered == ["open", "closed"]  # the recorder wrapped the whole run
  # …and the task stayed a declaration: executing it again opens a FRESH
  # recorder, which is what makes it registrable in a static definition.
  second = tmp_path / "run2"
  again = task.execute(
      GitHubJobSandbox(spec=_SPEC, workspace=epath.Path(second)),
      _Instance(),
      output_dir=second,
      timeout=10.0,
  )
  assert again.run.status is RunStatus.SUCCESS
  assert entered == ["open", "closed", "open", "closed"]
  # the prompt landed where the *harness* chose to put it — there is no
  # composition-level filename contract anymore (ADR-0007 §8)
  assert (workspace / "stub.prompt").read_text() == "SOLVE THIS"
  # the stub's own completion signal + trace conversion drove the outcome
  outcome = outcome_of(result)
  assert outcome is not None and outcome.complete is True
  trace = conversation_of(result)
  assert trace is not None and trace.conversation == Conversation(
      messages=[Message(role=Role.ASSISTANT, content=[TextBlock(text="hello")])]
  )


def test_the_task_takes_extra_observers_and_env(tmp_path: Path):
  # An extra observer is composed after the
  # composition's own, and env is forwarded to the harness.
  seen: list[str] = []

  class _Probe(SandboxObserver):

    @override
    def before_destroy(self, sb: SandboxFs) -> Contribution | None:
      seen.append("probe")
      # runs after diff-extract, so the extracted patch is already on the host
      return Contribution(metrics={"probe": 1.0})

  envs: list[Mapping[str, str] | None] = []

  class _Recording(GitHubJobSandbox):
    """Records the env each exec received, to prove `env` is forwarded."""

    @override
    def run_script(
        self,
        name: str,
        *,
        timeout: float,
        env: Mapping[str, str] | None = None,
    ) -> ExecResult:
      envs.append(env)
      return super().run_script(name, timeout=timeout, env=env)

  workspace = tmp_path / "run"
  result = CodingAgentTask(
      harness=StubHarness(), env={"MY_FLAG": "1"}, purge_git_history=False
  ).execute(
      _Recording(spec=_SPEC, workspace=epath.Path(workspace)),
      _Instance(),
      output_dir=workspace,
      timeout=10.0,
      extra_observers=[_Probe()],
  )
  assert seen == ["probe"]
  # its contribution reached the result
  assert result.run.metrics["probe"] == 1.0
  # the task → harness.run(env=...) → the exec; the stub hands it straight on
  assert {"MY_FLAG": "1"} in envs


def test_a_timed_out_agent_is_reported_as_timeout(tmp_path: Path):
  # Nothing raises on a timeout, so the engine assembles SUCCESS; the
  # composition knows better. A killed agent is a budget signal, and must not
  # look like a run that simply produced no trace.
  class _TimingOut(StubHarness):

    @override
    def run(
        self,
        sb: SandboxFs,
        *,
        prompt: str,
        timeout: float,
        env: Mapping[str, str] | None = None,
    ) -> ExecResult:
      _ = super().run(sb, prompt=prompt, timeout=timeout, env=env)
      return ExecResult(124, "", "killed after 10s", timed_out=True)

  workspace = tmp_path / "run"
  result = CodingAgentTask(
      harness=_TimingOut(), purge_git_history=False
  ).execute(
      GitHubJobSandbox(spec=_SPEC, workspace=epath.Path(workspace)),
      _Instance(),
      output_dir=workspace,
      timeout=10.0,
  )
  assert result.run.status is RunStatus.TIMEOUT
  assert result.run.metrics["stub.timed_out"] == 1.0
  assert result.run.metrics["stub.exit_code"] == 124.0
  assert result.run.metrics["stub.wall_seconds"] >= 0.0
  # what the exec itself said is kept — the only clue when the agent's own
  # redirected logs never got written
  assert result.run.artifacts["stub.exec_stderr.log"].read_text() == (
      "killed after 10s"
  )


def test_backend_observers_are_composed_first(tmp_path: Path):
  # ADR-0007 §3: the backend contributes its own observers, and the
  # composition prepends them. A sandbox subclass overriding observers() sees
  # its metrics in the run result with no composition change.
  class _MeteredSandbox(GitHubJobSandbox):

    @override
    def observers(self) -> tuple[SandboxObserver, ...]:
      class _Meter(SandboxObserver):

        @override
        def before_destroy(self, sb: SandboxFs) -> Contribution | None:
          del sb
          return Contribution(metrics={"sandbox.fake_metric": 42.0})

      return (_Meter(),)

  workspace = tmp_path / "run"
  result = CodingAgentTask(
      harness=StubHarness(), purge_git_history=False
  ).execute(
      _MeteredSandbox(spec=_SPEC, workspace=epath.Path(workspace)),
      _Instance(),
      output_dir=workspace,
      timeout=10.0,
  )
  assert result.run.status is RunStatus.SUCCESS
  assert result.run.metrics["sandbox.fake_metric"] == 42.0


def test_a_harness_without_the_generic_pair_still_runs(tmp_path: Path):
  # observers() is the harness's own decision — a harness returning none
  # composes fine; the outcome simply carries no completion or conversation.
  class _Unobserved(StubHarness):

    @override
    def observers(self) -> tuple[SandboxObserver, ...]:
      return ()

  workspace = tmp_path / "run"
  result = CodingAgentTask(
      harness=_Unobserved(), purge_git_history=False
  ).execute(
      GitHubJobSandbox(spec=_SPEC, workspace=epath.Path(workspace)),
      _Instance(),
      output_dir=workspace,
      timeout=10.0,
  )
  assert result.run.status is RunStatus.SUCCESS
  assert outcome_of(result) is None  # no completion signal was composed
  assert conversation_of(result) is None


def test_a_harness_composes_its_own_extra_observer(tmp_path: Path):
  # The factory is the point: an agent with a second signal channel adds its
  # collector itself, and no composition changes.
  class _Extra(StubHarness):

    @override
    def observers(self) -> tuple[SandboxObserver, ...]:
      class _Signal(SandboxObserver):

        @override
        def before_destroy(self, sb: SandboxFs) -> Contribution | None:
          del sb
          return Contribution(metrics={"stub.extra_signal": 7.0})

      return (*super().observers(), _Signal())

  workspace = tmp_path / "run"
  result = CodingAgentTask(harness=_Extra(), purge_git_history=False).execute(
      GitHubJobSandbox(spec=_SPEC, workspace=epath.Path(workspace)),
      _Instance(),
      output_dir=workspace,
      timeout=10.0,
  )
  assert result.run.status is RunStatus.SUCCESS
  assert result.run.metrics["stub.extra_signal"] == 7.0
  outcome = outcome_of(result)
  # the generic pair still composed alongside
  assert outcome is not None and outcome.complete is True
