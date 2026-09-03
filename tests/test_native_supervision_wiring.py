"""Tests for the harness side of the native supervision runtime (task 21).

What is asserted here is the *handover*: the script the harness renders, the
files it writes, and the artifacts it registers when a run is supervised by the
in-sandbox wrapper rather than by the host. The document the wrapper reads is
:mod:`tests.test_native_supervision`'s subject; this file is about how it and
the actor reach it.

Every assertion of an absence is paired with the presence that makes it
meaningful — an unsupervised script has no supervisor lines at all, so "the
supervised script does not redirect stdin" would pass on a blank string.
"""

from __future__ import annotations

import json
from pathlib import Path
import shlex

from etils import epath
import pytest

from swe_lab.harnesses.claude_code import ClaudeCodeHarness
from swe_lab.harnesses.claude_code.constants import (
    AGENT_STDERR_NAME,
    BINARY_AT,
    EVENT_STREAM_NAME,
    OPENROUTER_API,
    PROXY_PORT,
    STREAM_JSON_PROMPT_NAME,
    SUPERVISOR_INFO_NAME,
    SUPERVISOR_PROXY_BASE_URL,
    SUPERVISOR_PROXY_LOG_NAME,
    SUPERVISOR_PROXY_PORT,
    SUPERVISOR_PROXY_STDERR_NAME,
    SUPERVISOR_STDERR_NAME,
)
from swe_lab.sandbox import SandboxSpec
from swe_lab.sandbox.testing import FakeSandbox
from swe_lab.trace_synthesis.channel import SUPERVISOR_LOG_NAME
from swe_lab.trace_synthesis.native_supervision import (
    API_KEY_ENV,
    BASE_URL_ENV,
    Blocking,
    NativeSupervision,
    SUPERVISOR_BINARY_AT,
    SUPERVISOR_CONFIG_NAME,
    SUPERVISOR_PASS_ENV,
    SUPERVISOR_SUMMARY_NAME,
)

_SPEC = SandboxSpec("x", "img:tag", "/app", "abc")

_SUPERVISION = NativeSupervision(
    model="anthropic/claude-sonnet-5",
    budget=3,
    cooldown=4,
    window=8,
    judge_every_n_assistant_messages=3,
    block_actor_while_judging=Blocking.STDOUT,
)


#: Every artifact the wrapper contributes, and the name each is registered
#: under. Written once so the assertion and its control arm cannot drift into
#: covering different sets.
_SUPERVISOR_ARTIFACTS = {
    "supervisor_config.json": SUPERVISOR_CONFIG_NAME,
    "supervisor_log.jsonl": SUPERVISOR_LOG_NAME,
    "supervisor_summary.json": SUPERVISOR_SUMMARY_NAME,
    "supervisor_stderr.log": SUPERVISOR_STDERR_NAME,
    "supervisor.info": SUPERVISOR_INFO_NAME,
    "supervisor_proxy_log.jsonl": SUPERVISOR_PROXY_LOG_NAME,
    "supervisor_proxy_stderr.log": SUPERVISOR_PROXY_STDERR_NAME,
}


def _supervised() -> ClaudeCodeHarness:
  return ClaudeCodeHarness(capture="proxy", native_supervision=_SUPERVISION)


def _actor_command(script: str) -> str:
  """Return the one line that runs the actor, supervised or not.

  Args:
    script: A rendered invocation script.

  Returns:
    The line invoking either the wrapper or the agent binary directly.
  """
  candidates = [
      line
      for line in script.splitlines()
      if line.startswith((SUPERVISOR_BINARY_AT, BINARY_AT))
  ]
  assert len(candidates) == 1, f"expected one actor line, got {candidates}"
  return candidates[0]


def test_the_supervisor_gets_its_own_proxy_instance_to_its_own_upstream():
  """The wrapper's model calls do not go to the actor's upstream.

  Two instances of the same proxy, differing only in `--target` and port: the
  actor's traffic is Anthropic's and the supervisor's is OpenRouter's, and a
  reader of a finished run has to be able to tell whose call was whose.
  """
  script = _supervised()._invocation_script("/app")

  assert OPENROUTER_API in script
  assert f"--port {SUPERVISOR_PROXY_PORT}" in script
  assert SUPERVISOR_PROXY_LOG_NAME in script
  assert SUPERVISOR_PROXY_STDERR_NAME in script
  # The ports have to differ, and both have to be there: one proxy serving
  # both would send the supervisor's calls to the actor's upstream.
  assert SUPERVISOR_PROXY_PORT != PROXY_PORT
  assert f"--port {PROXY_PORT}" in script

  # The control arm: none of it appears without the wrapper, so the presences
  # above are the wrapper's doing and not the script's baseline.
  plain = ClaudeCodeHarness(capture="proxy")._invocation_script("/app")
  assert OPENROUTER_API not in plain
  assert str(SUPERVISOR_PROXY_PORT) not in plain


def test_the_actor_argv_reaches_the_wrapper_after_a_double_dash_unchanged():
  """The wrapper is handed tokens, not a command it has to re-parse.

  The harness does not rebuild the agent's flags for the supervised path and
  the wrapper does not join them into a shell command; what crosses is the
  same argv the unsupervised path executes.
  """
  harness = _supervised()
  command = _actor_command(harness._invocation_script("/app"))

  # The line ends in the wrapper's own output redirect, which the shell eats
  # and the wrapper never sees. Cutting there is safe precisely because
  # `test_the_actor_argv_needs_no_shell_to_mean_what_it_says` holds: no token
  # of the argv is a redirect operator.
  tokens = shlex.split(command.split(" > ")[0])
  assert "--" in tokens
  assert tokens[tokens.index("--") + 1 :] == list(harness.actor_argv())


def test_the_wrappers_own_flags_still_name_workspace_files():
  """Quoting the actor's argv must not swallow the wrapper's own paths.

  The two halves of the line get opposite treatments — the wrapper's flags
  expand `$SANDBOX_WORKSPACE`, the actor's tokens are quoted as a unit — and
  the failure mode of getting it wrong is silent: the wrapper receives the
  path literally and writes a file named `$SANDBOX_WORKSPACE`.
  """
  command = _actor_command(_supervised()._invocation_script("/app"))
  before_actor = command.split(" -- ")[0]

  for flag, name in (
      ("--config", SUPERVISOR_CONFIG_NAME),
      ("--actor-event-log", EVENT_STREAM_NAME),
      ("--supervisor-log", SUPERVISOR_LOG_NAME),
      ("--summary", SUPERVISOR_SUMMARY_NAME),
      ("--actor-stderr", AGENT_STDERR_NAME),
      ("--actor-prompt", STREAM_JSON_PROMPT_NAME),
  ):
    assert f'{flag} "$SANDBOX_WORKSPACE"/{name}' in before_actor


def test_the_supervised_command_hands_over_stdin_instead_of_redirecting_it():
  """When the actor's stdin closes is the wrapper's decision, not a file's.

  The prompt travels by path so that the wrapper can write it and then *hold*
  that stdin open. A `<` on this line would hand the actor a plain file whose
  EOF ends the run at a moment nobody chose.
  """
  supervised = _actor_command(_supervised()._invocation_script("/app"))
  assert "--actor-prompt" in supervised
  assert "<" not in supervised

  # Non-vacuous: the unsupervised path does redirect stdin, so the absence
  # above is a difference between the two and not a property of every line.
  plain = _actor_command(
      ClaudeCodeHarness(capture="proxy")._invocation_script("/app")
  )
  assert "<" in plain


def test_a_supervised_run_stops_when_the_credential_is_not_there():
  """No credential means no supervisor, and an unsupervised run is kept data.

  Fail-closed on purpose: a run that continues here reaches the outside as an
  ordinary result, which is worse than a failure because a failure is
  discarded and a result is believed.
  """
  script = _supervised()._invocation_script("/app")

  assert f'if [ -z "${{{API_KEY_ENV}:-}}" ]; then' in script
  # The name is written; the value has no rendered form anywhere.
  assert API_KEY_ENV in script
  assert "exit 78" in script


def test_a_supervised_run_stops_when_the_wrapper_cannot_answer_for_itself():
  """A positive premise: the binary answers, rather than merely existing.

  `[ -x ]` would pass a file that is present, executable and not a working
  binary for this image — the arm nobody enumerated, exactly as green as the
  ones they did.
  """
  script = _supervised()._invocation_script("/app")

  assert f"if ! {SUPERVISOR_BINARY_AT} --version" in script
  assert SUPERVISOR_INFO_NAME in script


def test_the_endpoint_is_exported_by_the_harness_and_not_passed_by_reference():
  """The two variables cross the boundary differently, and must keep doing so.

  The credential has no rendered form and travels by name (`pass_env`). The
  endpoint is the loopback address of a forwarder this harness just started,
  so the harness is the only party that knows it — a host variable of that
  name would name something else and would take precedence.
  """
  script = _supervised()._invocation_script("/app")

  assert f"export {BASE_URL_ENV}={SUPERVISOR_PROXY_BASE_URL}" in script
  assert SUPERVISOR_PASS_ENV == (API_KEY_ENV,)
  assert BASE_URL_ENV not in SUPERVISOR_PASS_ENV
  # …and the credential is never exported with a value beside it.
  assert f"export {API_KEY_ENV}=" not in script


def test_the_config_document_is_written_where_the_wrapper_reads_it(
    tmp_path: Path,
):
  """The wrapper is given a file, and the run is the only writer of it."""
  sb = FakeSandbox(spec=_SPEC, workspace=epath.Path(tmp_path))
  _ = _supervised().run(sb, prompt="solve it", timeout=1.0)

  document = json.loads(sb.read(SUPERVISOR_CONFIG_NAME).decode())
  assert document == _SUPERVISION.config_document(task="solve it")
  assert document["task"] == "solve it"


def test_an_unsupervised_run_writes_no_config(tmp_path: Path):
  """The wrapper is added beside the host runtime, not in place of it.

  The default path is untouched, which is the whole condition under which this
  can land before the binary is a released artifact.
  """
  sb = FakeSandbox(spec=_SPEC, workspace=epath.Path(tmp_path))
  _ = ClaudeCodeHarness(capture="proxy").run(sb, prompt="solve it", timeout=1.0)

  with pytest.raises(FileNotFoundError):
    _ = sb.read(SUPERVISOR_CONFIG_NAME)


def test_the_wrappers_artifacts_are_registered_so_the_run_outlives_the_box():
  """A summary nobody persists cannot classify anything afterwards.

  Asserted as **set equality against the unsupervised run**, in both
  directions, rather than as a sample of names. A `>=` over some of the keys
  passes while a name is missing, and a control arm naming two of them passes
  while a third leaks into the plain run — the arm nobody listed is exactly as
  green as the ones they did.
  """
  supervised = _supervised().native_outputs()
  plain = ClaudeCodeHarness(capture="proxy").native_outputs()

  added = {name: supervised[name] for name in set(supervised) - set(plain)}
  assert added == _SUPERVISOR_ARTIFACTS | {
      # Not the wrapper's own file, but only a supervised run persists it: it
      # is the stream the supervisor read while the actor was still running.
      "event_stream.jsonl": EVENT_STREAM_NAME,
  }
  # …and nothing the wrapper contributes is in the plain run under any name.
  assert not set(plain) & set(_SUPERVISOR_ARTIFACTS)


def test_the_wrapper_and_the_correction_channel_cannot_both_own_stdin():
  """Two writers to one stdin, and two answers to when the run ends.

  Refused where the two are named, rather than discovered as a run that
  stopped at a moment neither of them chose.
  """
  with pytest.raises(ValueError, match="stdin"):
    _ = ClaudeCodeHarness(
        capture="proxy",
        correction_channel=True,
        native_supervision=_SUPERVISION,
    )
