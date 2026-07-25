"""The harness seam: a brand-new harness composes with zero engine change.

Spec Success #3 — "a second harness (or a stub) registers **without touching the
engine**." This stub implements the ``Harness`` ABC and runs end-to-end through
the real ``SandboxManager`` + ``ConversationObserver`` + ``GitHubJobBackend``
(local bash, no Docker), importing only the public engine surface — nothing in
``sandbox/`` or ``conversation/`` is modified to make it work.
"""

from pathlib import Path
from typing import override

from swe_lab.conversation import (
    Conversation,
    ConversationObserver,
    Message,
    Role,
    TextBlock,
)
from swe_lab.harnesses.base import Harness
from swe_lab.sandbox import (
    Assets,
    GitHubJobBackend,
    Inline,
    Mount,
    Mounts,
    RunStatus,
    Sandbox,
    SandboxManager,
    SandboxSpec,
)

_SPEC = SandboxSpec("acme__widget-1", "acme/widget:tag", "/app", "abc123")
_TRACE_NAME = "stub.trace"


class StubHarness(Harness):
  """A minimal off-the-shelf-agent stand-in: stage a script, emit a trace."""

  @override
  def mounts(self, workdir: str) -> Mounts:
    del workdir
    script = f'echo hello > "$SANDBOX_WORKSPACE/{_TRACE_NAME}"\n'
    return {"stub.sh": Mount(Inline(script.encode()), executable=True)}

  @override
  def assets(self) -> Assets:
    return {}

  @override
  def run(self, sb: Sandbox, *, timeout: float) -> None:
    _ = sb.run("stub.sh", timeout=timeout)

  @override
  def native_outputs(self) -> dict[str, str]:
    return {"trace": _TRACE_NAME}

  @override
  def to_conversation(self, workspace: Path) -> Conversation:
    text = (workspace / _TRACE_NAME).read_text().strip()
    return Conversation(
        messages=[Message(role=Role.ASSISTANT, content=[TextBlock(text=text)])]
    )


def test_stub_harness_composes_over_the_engine(tmp_path: Path):
  harness = StubHarness()
  observer = ConversationObserver(producer=harness)
  workspace = tmp_path / "run"
  manager = SandboxManager(
      spec=_SPEC,
      backend=GitHubJobBackend(),
      workspace=workspace,
      observers=[observer],
      mounts=harness.mounts(_SPEC.workdir),
  )
  with manager.sandbox() as sb:
    harness.run(sb, timeout=10.0)

  # the engine ran the new harness end-to-end, with no engine change
  assert manager.result.status is RunStatus.SUCCESS
  assert (workspace / _TRACE_NAME).read_text() == "hello\n"
  # the shared conversation observer converted + registered the trace artifact
  assert observer.conversation == Conversation(
      messages=[Message(role=Role.ASSISTANT, content=[TextBlock(text="hello")])]
  )
  assert (workspace / "conversation.json").is_file()
  assert manager.result.artifacts["trace"] == workspace / _TRACE_NAME
