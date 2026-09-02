"""Taking the actor's own session record out while there is still a container.

The record lives only in the container's writable layer, so ``before_destroy``
is the last moment it exists. What these tests pin is the pair of claims the
module makes: the *whole* subtree is taken rather than a pattern, and an
absence is written down rather than left to be inferred from a missing file.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import override

from etils import epath

from swe_lab.harnesses.claude_code.native_transcript import (
    CONFIG_DIR,
    NativeTranscriptObserver,
    PROJECTS_SUBDIR,
    REPORT_ARTIFACT,
    TRANSCRIPT_ARTIFACT,
    TRANSCRIPT_FILENAME,
)
from swe_lab.sandbox import Contribution, ExecResult, SandboxSpec
from swe_lab.sandbox.testing import FakeSandbox

SPEC = SandboxSpec("acme__widget-1", "acme/widget:tag", "/app", "abc123")


def sandbox(tmp_path: Path, *results: ExecResult) -> FakeSandbox:
  """Build a sandbox whose exec calls answer with ``results``.

  Args:
    tmp_path: Backs the workspace with a real directory.
    *results: What successive executions return.

  Returns:
    The fake sandbox.
  """
  return FakeSandbox(
      spec=SPEC, workspace=epath.Path(tmp_path), run_results=list(results)
  )


def report_of(contribution: Contribution | None) -> dict[str, object]:
  """Return the parsed report from a contribution.

  Args:
    contribution: What ``before_destroy`` returned.

  Returns:
    The report as a dict.
  """
  assert contribution is not None
  return json.loads(contribution.inline_artifacts[REPORT_ARTIFACT].decode())


def test_the_agents_own_record_is_archived_while_the_sandbox_is_still_live(
    tmp_path: Path,
):
  """The happy path, and the shape a reader gets."""
  observer = NativeTranscriptObserver()
  sb = sandbox(
      tmp_path, ExecResult(0, "projects/\nprojects/-app/s.jsonl\n", "")
  )
  # The command really does produce this; the fake does not run tar.
  sb.write(TRANSCRIPT_FILENAME, b"\x1f\x8b archive")

  contribution = observer.before_destroy(sb)

  assert contribution is not None
  assert contribution.artifacts == {TRANSCRIPT_ARTIFACT: TRANSCRIPT_FILENAME}
  report = report_of(contribution)
  assert report["archived"] is True
  assert report["members"] == 2
  assert report["config_dir"] == CONFIG_DIR


def test_the_whole_subtree_is_taken_rather_than_a_pattern(tmp_path: Path):
  """A glob would take a transcript whose tool-result references dangle.

  The command names the directory. Asserted on the command itself because the
  failure it prevents is invisible in the artifact: a `*.jsonl` archive opens
  fine and is silently incomplete.
  """
  observer = NativeTranscriptObserver()
  sb = sandbox(tmp_path, ExecResult(0, "", ""))
  sb.write(TRANSCRIPT_FILENAME, b"archive")

  _ = observer.before_destroy(sb)

  assert len(sb.commands) == 1
  assert f"-C {CONFIG_DIR} {PROJECTS_SUBDIR}" in sb.commands[0]
  assert "*" not in sb.commands[0]


def test_a_record_that_was_not_there_is_reported_not_silently_absent(
    tmp_path: Path,
):
  """An absent artifact beside an absent explanation is the failure family.

  A run whose actor wrote no session record is a normal run; what must not
  happen is that it reads the same as a run nobody tried to collect from.
  """
  observer = NativeTranscriptObserver()
  sb = sandbox(tmp_path, ExecResult(2, "", "tar: projects: Cannot stat"))

  contribution = observer.before_destroy(sb)

  assert contribution is not None
  assert contribution.artifacts == {}
  report = report_of(contribution)
  assert report["archived"] is False
  assert "Cannot stat" in str(report["stderr"])


def test_a_command_that_reports_success_without_a_file_is_not_believed(
    tmp_path: Path,
):
  """Attack: exit 0 and no archive.

  Claiming an artifact that is not there fails the collect step, which would
  turn a missing diagnostic into a failed run — the diagnostic taking down the
  thing it documents.
  """
  observer = NativeTranscriptObserver()
  sb = sandbox(tmp_path, ExecResult(0, "projects/\n", ""))

  contribution = observer.before_destroy(sb)

  assert contribution is not None
  assert contribution.artifacts == {}
  assert report_of(contribution)["archived"] is False


def test_a_sandbox_that_cannot_answer_exists_still_leaves_a_report(
    tmp_path: Path,
):
  """The validation is inside the `try`, with the command it validates.

  Attack: `tar` succeeds and the workspace check then raises. Outside the
  `try`, that propagates through `before_destroy`, and the run ends with
  neither the archive nor a report saying why — this module producing, by
  itself, the unreadable absence it exists to prevent. Found in review of this
  PR, by a mutant that made `exists` raise after a successful command.
  """

  class CannotAnswer(FakeSandbox):
    """A sandbox whose file check fails after the command succeeded."""

    @override
    def exists(self, name: str) -> bool:
      """Fail the check.

      Args:
        name: Ignored.

      Returns:
        Never returns.

      Raises:
        RuntimeError: Always.
      """
      del name
      raise RuntimeError("workspace is unreachable")

  sb = CannotAnswer(
      spec=SPEC,
      workspace=epath.Path(tmp_path),
      run_results=[ExecResult(0, "projects/\n", "")],
  )

  contribution = NativeTranscriptObserver().before_destroy(sb)

  assert contribution is not None
  assert contribution.artifacts == {}
  report = report_of(contribution)
  assert report["archived"] is False
  assert "workspace is unreachable" in str(report["error"])


def test_collecting_the_record_never_fails_the_run(tmp_path: Path):
  """A sandbox that raises is recorded, not propagated."""
  observer = NativeTranscriptObserver()
  sb = sandbox(tmp_path)
  sb.run_error = RuntimeError("exec is gone")

  contribution = observer.before_destroy(sb)

  assert contribution is not None
  assert contribution.artifacts == {}
  report = report_of(contribution)
  assert report["archived"] is False
  assert "exec is gone" in str(report["error"])


def test_the_report_is_contributed_on_every_path(tmp_path: Path):
  """Whatever happened, the run carries an account of the attempt."""
  cases = (
      sandbox(tmp_path / "ok", ExecResult(0, "", "")),
      sandbox(tmp_path / "missing", ExecResult(2, "", "no such directory")),
  )
  for sb in cases:
    sb.workspace.mkdir(parents=True, exist_ok=True)
    contribution = NativeTranscriptObserver().before_destroy(sb)
    assert contribution is not None
    assert REPORT_ARTIFACT in contribution.inline_artifacts
