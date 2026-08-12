"""What every agent harness needs, written once.

Three harnesses grew the same three pieces independently — a workspace text
reader, a caller-env renderer, and an observer that records which build
actually ran. The first two were **byte-identical** in all three; the third
differed only in which commands it probed and what it named the artifact.

Nothing here is agent-specific: a fourth harness gets all of it by importing,
and a bug fixed here is fixed everywhere. What stays per-harness is what
genuinely differs — the invocation script, the trace format, the outcome
mapping.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
import logging
import re
import shlex
from typing import override

from swe_lab.sandbox import (
    ArtifactSchema,
    Contribution,
    SandboxError,
    SandboxFs,
    SandboxObserver,
)

_logger = logging.getLogger(__name__)

# Generous: `--version` on a cold 100–300 MB binary is mostly process start-up.
_INFO_TIMEOUT_S = 60.0

# The probes an agent build is asked about by default. Version says *which*
# build ran — the first question anyone asks when a run behaves oddly, and
# unrecoverable once the sandbox is gone. Help says what that build actually
# accepted, which is the second question, because a harness's invocation is
# assembled from flags whose availability moves between releases.
DEFAULT_INFO_PROBES: tuple[str, ...] = ("--version", "--help")


def read_text(sb: SandboxFs, name: str) -> str:
  """Read a workspace file as text, tolerant of odd bytes and absence.

  Absence-tolerant on purpose: a crashed run leaves no trace file, and the
  callers (trace conversion, outcome classification) must report an outcome
  rather than raise.

  Args:
    sb: The live sandbox to read through.
    name: The workspace-relative filename.

  Returns:
    The file's text, or ``""`` when it is not there.
  """
  if not sb.exists(name):
    return ""
  return sb.read(name).decode("utf-8", "backslashreplace")


# A shell variable name; anything else would make the sourced file a syntax
# error, which `set -u` would turn into "the agent never ran" with no clue why.
_ENV_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def env_exports(env: Mapping[str, str]) -> str:
  """Render caller env as ``export K=V`` lines, values shell-quoted.

  Args:
    env: Variable name → value.

  Returns:
    The sourceable script text, in the given order.

  Raises:
    SandboxError: If a name is not a valid shell identifier — refused here
      rather than corrupting the sourced file and skipping the run.
  """
  bad = sorted(name for name in env if not _ENV_NAME_RE.match(name))
  if bad:
    raise SandboxError(f"invalid environment variable name(s): {bad}")
  lines = [f"export {name}={shlex.quote(value)}" for name, value in env.items()]
  return "\n".join(lines) + "\n"


@dataclass
class AgentInfoObserver(SandboxObserver):
  """Record the agent's own account of itself, for post-hoc debugging.

  Probes the provisioned binary once the sandbox is up, lands the output in
  the workspace, and registers it as an artifact. *Which build actually ran*
  is the first question anyone asks when a run behaves oddly, and once the
  sandbox is gone the answer is otherwise unrecoverable — the pin says what we
  asked for, not what the sandbox had.

  **Never fails a run.** Every step is caught: a diagnostic that can abort the
  thing it documents is worse than no diagnostic.

  Single-run, like every stateful observer: ``after_create`` captures (that
  hook cannot return a contribution) and ``before_destroy`` hands it over.

  Attributes:
    binary: The in-sandbox path to interrogate.
    artifact: The name the output is registered under, and the workspace file
      it lands in. Namespaced by the harness so two agents cannot collide.
    probes: Arguments to run the binary with, one section each. The default
      pair answers "which build" and "what did it accept"; a harness whose
      real surface is a subcommand overrides (Codex's is ``exec``).
  """

  binary: str
  artifact: str
  probes: Sequence[str] = DEFAULT_INFO_PROBES
  _captured: bool = field(default=False, init=False, repr=False)

  @override
  def output_schema(self) -> Sequence[ArtifactSchema]:
    """Declare the info file — advisory, since a run is valid without it."""
    return (
        ArtifactSchema(
            self.artifact,
            required=False,
            description="the agent's own --version and --help output",
        ),
    )

  @override
  def after_create(self, sb: SandboxFs) -> None:
    """Probe the binary and land the output in the workspace.

    Args:
      sb: The live sandbox, with the binary already provisioned (the asset
        observer runs first).
    """
    binary = shlex.quote(self.binary)
    sections: list[str] = []
    for probe in self.probes:
      command = f"{binary} {probe}"
      try:
        # 2>&1 because a binary that cannot run at all says so on stderr, and
        # that is exactly the case this file exists to explain.
        result = sb.run_command(f"{command} 2>&1", timeout=_INFO_TIMEOUT_S)
        body = (result.stdout + result.stderr).strip()
        sections.append(f"$ {command}\n[exit {result.exit_code}]\n{body}")
      except Exception:  # noqa: BLE001 — a diagnostic must never fail the run
        _logger.exception("%s failed; recording that instead", command)
        sections.append(f"$ {command}\n[did not run]")
    try:
      sb.write(self.artifact, ("\n\n".join(sections) + "\n").encode())
      self._captured = True
    except Exception:  # noqa: BLE001 — as above
      _logger.exception("could not write %s; skipping it", self.artifact)

  @override
  def before_destroy(self, sb: SandboxFs) -> Contribution | None:
    """Register the captured file, if there is one.

    Args:
      sb: Unused — the file is already in the workspace.

    Returns:
      The artifact registration, or ``None`` when nothing was captured.
    """
    del sb
    if not self._captured:
      return None
    return Contribution(artifacts={self.artifact: self.artifact})


def status_tail(exit_code_file: str) -> list[str]:
  """Return the script lines that record the agent's status and propagate it.

  Every harness ends its invocation script the same way, and it used to end it
  ``exit 0`` — throwing the agent's own status away and leaving the recorded
  ``<agent>.exit_code`` metric permanently ``0.0``. The stated reason was that
  a non-zero exit would disturb container teardown, and that is not so:
  teardown is a context-manager exit, no backend raises on a non-zero exec
  (all three ``check=False``), and nothing derives ``RunStatus`` from the code.
  So the zero bought nothing and cost a signal.

  The status is now propagated *and* written to the workspace, which are not
  redundant:

  - the **exec result** carries it to the metric, where a sweep can count
    non-zero runs from the manifest instead of fetching artifacts;
  - the **file** is written only if the agent process actually returned, so its
    *absence* distinguishes "we killed it at the deadline" from "it exited
    non-zero" — and it survives in the artifacts when the record does not.

  What has not changed, and must not: the code is **recorded, never gated on**.
  An agent's exit status is ambiguous — non-zero covers both "the task defeated
  it" and "the API broke" — so attribution stays with ``AgentOutcome``, read
  from the trace (ADR-0011). Propagating it makes the number honest without
  giving it authority.

  Args:
    exit_code_file: The already-quoted workspace path to write the status to.

  Returns:
    The tail lines, in order.
  """
  return [
      # `set -u` is on but `set -e` is not, so execution reaches here whatever
      # the agent did. Capture on the very next line, before anything can
      # overwrite `$?`.
      "status=$?",
      f"printf '%s\\n' \"$status\" > {exit_code_file}",
      'exit "$status"',
  ]
