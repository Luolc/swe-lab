"""Tests for placing the native supervision wrapper (task 21 §3a).

The gate here is a **positive chain** — file, then runs, then names itself —
so the arms nobody enumerated fail at the first step they cannot answer. That
makes the accept arm load-bearing rather than a courtesy: a gate that rejects
everything is green on every rejection arm and indistinguishable from a
correct one, so "a real artifact is still accepted" is tested with the rest.
"""

from __future__ import annotations

import os
from pathlib import Path
import stat
import subprocess

import pytest

from swe_lab.harnesses.claude_code import ClaudeCodeHarness
from swe_lab.harnesses.claude_code.constants import PROXY_BINARY_AT
from swe_lab.harnesses.claude_code.proxy import PROXY_SOURCE_ENV
from swe_lab.rollout import CodingAgentTask
from swe_lab.trace_synthesis.native_supervision import (
    Blocking,
    NativeSupervision,
    SUPERVISOR_BINARY_AT,
)
from swe_lab.trace_synthesis.supervisor_binary import (
    BINARY_ENV,
    BINARY_NAME,
    ensure_supervisor_binary,
    local_build,
    supervisor_version,
)

_SUPERVISION = NativeSupervision(
    model="anthropic/claude-sonnet-5",
    budget=3,
    cooldown=4,
    window=8,
    judge_every_n_assistant_messages=1,
    block_actor_while_judging=Blocking.STDOUT,
)


def _script(path: Path, body: str, *, executable: bool = True) -> Path:
  """Write an executable stand-in for the wrapper.

  A script rather than a compiled binary on purpose: what this gate checks is
  that the file *answers as this program on this host*, which a script can do
  honestly. Whether the bytes run in the container is a different machine's
  question, and the invocation script's own probe is what settles it.

  Args:
    path: Where to write it.
    body: The shell body.
    executable: Whether to set the execute bit.

  Returns:
    The path written.
  """
  _ = path.write_text(f"#!/bin/sh\n{body}\n")
  if executable:
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
  return path


def test_a_wrapper_that_answers_for_itself_is_accepted(tmp_path: Path):
  """**The accept arm.** Without it, a gate that refuses everything passes.

  Every other case here is a rejection, and a `return False` implementation
  satisfies all of them at once. This is the only test that fails when the
  gate becomes uselessly strict.
  """
  binary = _script(tmp_path / "wrapper", f'echo "{BINARY_NAME} 0.4.2"')

  assert supervisor_version(binary) == "0.4.2"


def test_the_version_is_read_off_the_binary_not_matched_against_a_guess(
    tmp_path: Path,
):
  """No release exists to pin, so a pinned constant would refuse the artifact.

  What is wanted is *the version this is*. Asserting equality with a guess
  would make the gate reject a real, newer wrapper — a rejection arm would go
  green and the accept arm would go red.
  """
  binary = _script(tmp_path / "wrapper", f'echo "{BINARY_NAME} 99.0.0-rc1"')

  assert supervisor_version(binary) == "99.0.0-rc1"


def test_a_missing_wrapper_is_refused(tmp_path: Path):
  """Absence fails at the first link, before anything is executed."""
  with pytest.raises(FileNotFoundError):
    _ = supervisor_version(tmp_path / "not-there")


def test_a_file_that_cannot_be_executed_is_refused(tmp_path: Path):
  """Present and unrunnable is the case `[ -f ]` alone would admit."""
  binary = _script(
      tmp_path / "wrapper", f'echo "{BINARY_NAME} 1.0.0"', executable=False
  )

  with pytest.raises(RuntimeError, match="could not be executed"):
    _ = supervisor_version(binary)


def test_a_wrapper_that_fails_to_answer_is_refused(tmp_path: Path):
  """Runs, and cannot say what it is."""
  binary = _script(tmp_path / "wrapper", 'echo "boom" >&2; exit 3')

  with pytest.raises(RuntimeError, match="exited 3"):
    _ = supervisor_version(binary)


@pytest.mark.parametrize(
    "answer",
    ["some-other-tool 1.2.3", BINARY_NAME, "", "1.2.3"],
    ids=["another-program", "name-without-version", "silent", "bare-version"],
)
def test_a_program_that_is_not_this_one_is_refused(answer: str, tmp_path: Path):
  """**This is what `[ -x ]` lets through**: present, executable, not it.

  Each of these exits 0, so every check that stops at the exit status accepts
  them. Requiring the answer to name this program and a version is what
  separates them from the accept arm above.

  Args:
    answer: What the impostor prints.
    tmp_path: Where to write it.
  """
  binary = _script(tmp_path / "wrapper", f'echo "{answer}"')

  with pytest.raises(RuntimeError, match="does not name"):
    _ = supervisor_version(binary)


# ─── the probe runs a file somebody else named (P0, PR #400) ────────────────

#: Not a credential, and shaped so a grep for it cannot match anything else.
#: It proves the *mechanism* — that what the child prints reaches the
#: exception — without putting a real secret near a test.
_SENTINEL = "swe-lab-sentinel-b3f1a7"


def test_the_probe_never_repeats_what_the_child_printed(tmp_path: Path):
  """An exception travels into logs, tracebacks and CI transcripts.

  The file being run is one an operator named — a wrong path, a stale build,
  in principle anything — so a process that printed a credential and exited
  nonzero would have written it wherever this error was reported.

  **The sentinel is baked into the script rather than read from the
  environment, and that is the whole design of this test.** Sourcing it from
  the environment would make this pass as soon as *either* fix is in place:
  with the probe's environment scrubbed the child prints nothing, so an
  exception that echoed stderr verbatim would still show no sentinel. The two
  measures would mask each other and the test would name one property while
  measuring another. (Measured: with the sentinel taken from the environment,
  restoring the verbatim echo left this green.)
  """
  loud = _script(tmp_path / "loud", f'echo "{_SENTINEL}" >&2; exit 9')

  # The input is live: this script does print it, to a caller who looks.
  direct = subprocess.run(
      [str(loud)], capture_output=True, text=True, check=False
  )
  assert _SENTINEL in direct.stderr

  with pytest.raises(RuntimeError) as raised:
    _ = supervisor_version(loud)

  assert _SENTINEL not in str(raised.value)


def test_the_probe_repeats_nothing_from_a_program_that_exits_zero_either(
    tmp_path: Path,
):
  """The other error path prints too, and it is the likelier one.

  A binary that answers 0 with the wrong text reaches the "does not name"
  branch, and a credential printed on stdout would have been quoted into it.
  """
  loud = _script(tmp_path / "loud", f'echo "{_SENTINEL}"')

  with pytest.raises(RuntimeError) as raised:
    _ = supervisor_version(loud)

  assert _SENTINEL not in str(raised.value)


def test_the_probe_does_not_hand_the_child_our_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
  """`--version` needs no credential, so the probe passes none.

  **Not something to rely on**, which is why the errors above say nothing
  either: whether a same-uid child can read the parent's `/proc/<pid>/environ`
  is decided by `ptrace_may_access` and varies with `ptrace_scope`, the
  target's `dumpable` flag and capabilities. This narrows what a careless
  binary picks up; not repeating child output is what stops a deliberate leak
  from being written down.
  """
  reporter = _script(
      tmp_path / "reporter",
      f'echo "{BINARY_NAME} 1.0.0"; echo "$SENTINEL_VAR" > "{tmp_path}/seen"',
  )
  monkeypatch.setenv("SENTINEL_VAR", _SENTINEL)

  assert supervisor_version(reporter) == "1.0.0"

  # The child ran, and saw nothing where the variable would have been.
  assert (tmp_path / "seen").read_text().strip() == ""


def test_without_a_release_or_a_local_build_there_is_nothing_to_verify(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
  """Fail closed, and say the one thing the reader can act on.

  No release is published, so this is today's only outcome for anyone who has
  not built the wrapper. It must not return a path to nothing: an unsupervised
  run that looked ordinary is worse than one that failed.
  """
  monkeypatch.delenv(BINARY_ENV, raising=False)

  with pytest.raises(RuntimeError, match=BINARY_ENV):
    _ = ensure_supervisor_binary(dest=tmp_path / "out")


def test_the_local_build_override_is_read_by_code_not_only_described(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
  """The transitional path is a real implementation, with a real check.

  A name that appears only in documentation is indistinguishable, from inside
  the documentation, from one the code reads — which is exactly how this
  override spent a PR being a sentence. Setting it must change what happens.
  """
  built = _script(tmp_path / "built", f'echo "{BINARY_NAME} 0.1.0"')
  monkeypatch.setenv(BINARY_ENV, str(built))

  assert local_build() == built
  placed = ensure_supervisor_binary(dest=tmp_path / "placed")

  assert Path(placed).is_file()
  assert os.access(placed, os.X_OK)
  assert Path(placed).read_text() == built.read_text()


def test_an_override_naming_an_impostor_places_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
  """The check runs before the copy, so a refusal leaves no file behind.

  A rejected wrapper that had already been copied would sit at the asset's
  path looking placed, and the next reader would have no way to tell.
  """
  monkeypatch.setenv(
      BINARY_ENV, str(_script(tmp_path / "built", 'echo "not-it 1.0"'))
  )
  dest = tmp_path / "placed"

  with pytest.raises(RuntimeError):
    _ = ensure_supervisor_binary(dest=dest)

  assert not dest.exists()


def test_only_a_supervised_run_declares_the_wrapper_as_an_asset():
  """An asset declared is an asset transferred.

  The unsupervised path must not ask for a binary it will never exec — and
  today asking for it would fail the run outright, since there is no release.

  **Stream capture, deliberately.** What is asserted is conditionality on
  `native_supervision`, and nothing here is about the proxy; asking for proxy
  capture would drag in `proxy_source_version()`, which reads a sibling
  checkout this repo does not vendor. That is what turned CI red while the
  same test passed locally — the check gave two verdicts in two environments,
  and the one that counts is the one without the sibling.
  """
  supervised = ClaudeCodeHarness(native_supervision=_SUPERVISION)
  plain = ClaudeCodeHarness()

  assert SUPERVISOR_BINARY_AT in [a.path for a in supervised.assets()]
  assert SUPERVISOR_BINARY_AT not in [a.path for a in plain.assets()]


def test_the_wrapper_is_declared_whatever_the_capture_mode_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
  """The wrapper runs the actor, so every capture mode needs it.

  It was nested under the proxy branch, which left a supervised *stream* run
  declaring no wrapper: the script would exec a path nothing had placed. The
  shipped definition uses proxy capture, so that configuration is covered too
  — with a synthetic proxy source, the convention this repo already uses for
  the un-vendored sibling checkout.

  Args:
    tmp_path: Somewhere to put the synthetic proxy source.
    monkeypatch: Used to point the proxy at it.
  """
  source = tmp_path / "reverse_proxy.go"
  _ = source.write_text("package main\n")
  monkeypatch.setenv(PROXY_SOURCE_ENV, str(source))

  for capture in ("stream", "proxy"):
    harness = ClaudeCodeHarness(
        capture=capture, native_supervision=_SUPERVISION
    )
    paths = [a.path for a in harness.assets()]

    assert SUPERVISOR_BINARY_AT in paths, capture
    # …and the proxy is still conditional on capture, so this did not simply
    # make every asset unconditional.
    assert (PROXY_BINARY_AT in paths) == (capture == "proxy"), capture


# ─── the definition that actually opens the path (task 21 §3a, step 2) ──────


def test_a_shipped_definition_takes_the_native_path():
  """Without this, every other test here describes an unreachable capability.

  The harness could be configured for it and no workflow ever was, which is a
  switch nobody wired to anything.
  """
  from swe_lab.workflow.definitions import NATIVE_SUPERVISED_ROLLOUT

  task = NATIVE_SUPERVISED_ROLLOUT[0].task
  assert isinstance(task, CodingAgentTask)
  harness = task.harness
  assert isinstance(harness, ClaudeCodeHarness)

  assert harness.native_supervision is not None


def test_the_native_definition_carries_the_supervisors_credential_by_name():
  """Both credentials cross by reference; the endpoint deliberately does not.

  A host-settable endpoint could aim a credential-bearing request at any host,
  so its absence here is the security property, not an omission.
  """
  from swe_lab.trace_synthesis.native_supervision import (
      API_KEY_ENV,
      BASE_URL_ENV,
  )
  from swe_lab.workflow.definitions import NATIVE_SUPERVISED_ROLLOUT

  pass_env = NATIVE_SUPERVISED_ROLLOUT[0].sandbox.pass_env

  assert API_KEY_ENV in pass_env
  assert BASE_URL_ENV not in pass_env


def test_the_native_definition_does_not_also_run_the_host_supervisor():
  """The two runtimes are alternatives, and the harness refuses them together.

  Asserted at the definition too: a definition carrying both would fail only
  when someone ran it, which is later than a reader would notice.
  """
  from swe_lab.workflow.definitions import NATIVE_SUPERVISED_ROLLOUT

  task = NATIVE_SUPERVISED_ROLLOUT[0].task
  assert isinstance(task, CodingAgentTask)
  harness = task.harness
  assert isinstance(harness, ClaudeCodeHarness)

  assert task.supervision_factory is None
  assert harness.correction_channel is False


def test_the_native_workflow_is_registered_under_its_own_name():
  """Registered separately from the host arms: it is not one of them retuned."""
  from swe_lab.workflow.registry import (
      registered_workflows,
      workflow_definition,
  )

  name = "native_supervised_rollout_and_unit_test"

  assert name in registered_workflows()
  assert workflow_definition(name)
  # The control arm: the host arms are still registered under their own names,
  # so this one was added rather than substituted for them.
  assert "supervised_rollout_and_unit_test" in registered_workflows()
