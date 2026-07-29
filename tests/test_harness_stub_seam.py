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
from typing import override

from etils import epath

from swe_lab.conversation import (
    Conversation,
    ConversationObserver,
    Message,
    Role,
    TextBlock,
)
from swe_lab.harnesses import (
    COMPLETE_METRIC,
    Harness,
    HarnessOutcomeObserver,
    PROMPT_NAME,
)
from swe_lab.rollout import run_rollout
from swe_lab.sandbox import (
    GitHubJobSandbox,
    Inline,
    Mount,
    Mounts,
    RunStatus,
    SandboxFs,
    SandboxManager,
    SandboxSpec,
)

_SPEC = SandboxSpec("acme__widget-1", "acme/widget:tag", "/app", "abc123")
_TRACE_NAME = "stub.trace"


class StubHarness(Harness):
  """A minimal off-the-shelf-agent stand-in: stage a script, emit a trace."""

  @property
  @override
  def name(self) -> str:
    return "stub"

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
      timeout: float,
      env: Mapping[str, str] | None = None,
  ) -> None:
    # A foreign harness decides for itself how injected env reaches its agent;
    # this one hands it straight to the exec.
    _ = sb.run_script("stub.sh", timeout=timeout, env=env)

  @override
  def native_outputs(self) -> dict[str, str]:
    return {"trace": _TRACE_NAME}

  @override
  def to_conversation(self, sb: SandboxFs) -> Conversation:
    raw = sb.read(_TRACE_NAME) if sb.exists(_TRACE_NAME) else b""
    text = raw.decode().strip()
    return Conversation(
        messages=[Message(role=Role.ASSISTANT, content=[TextBlock(text=text)])]
    )

  @override
  def completed(self, sb: SandboxFs) -> bool:
    # This "agent" reads as finished once it wrote its own trace — the signal
    # is the harness's business, in its own format.
    return sb.exists(_TRACE_NAME)


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
    harness.run(sb, timeout=10.0)

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
  assert manager.result.artifacts["conversation"] == (
      workspace / "conversation.json"
  )
  assert manager.result.artifacts["stub.trace"] == workspace / _TRACE_NAME
  assert outcome.complete is True
  assert manager.result.metrics[COMPLETE_METRIC] == 1.0


def test_run_rollout_takes_a_foreign_harness_and_proxy(tmp_path: Path):
  # The composition is harness-agnostic: a downstream user's own Harness and
  # their own recorder (any context manager) are injected, and run_rollout
  # never reaches for a concrete agent.
  entered: list[str] = []

  @contextlib.contextmanager
  def stub_proxy() -> Iterator[None]:
    entered.append("open")
    yield
    entered.append("closed")

  workspace = tmp_path / "run"
  outcome = run_rollout(
      GitHubJobSandbox(spec=_SPEC, workspace=epath.Path(workspace)),
      StubHarness(),
      prompt="SOLVE THIS",
      output_dir=workspace,
      timeout=10.0,
      proxy=stub_proxy(),
  )

  assert outcome.status is RunStatus.SUCCESS
  assert entered == ["open", "closed"]  # the recorder wrapped the whole run
  # the prompt was staged for the harness under the shared contract name
  assert (workspace / PROMPT_NAME).read_text() == "SOLVE THIS"
  # the stub's own completion signal + trace conversion drove the outcome
  assert outcome.complete is True
  assert outcome.conversation == Conversation(
      messages=[Message(role=Role.ASSISTANT, content=[TextBlock(text="hello")])]
  )
