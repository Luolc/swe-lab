"""Take the actor's own session record out before the container is gone.

**What this artifact proves.** The actor's CLI wrote a session record of this
run, and here it is. It is written by the agent binary's own session
persistence — not by the pump, not by the supervisor's log, not by the
converter — so it survives every one of those being wrong, and a disagreement
between it and our account is a finding rather than a coincidence.

**What it does not prove.** It is *not* independent evidence about the run. It
shares a writer with the agent's ``stream-json`` output: both are
serializations by the same process, so a CLI that misreports gets it wrong in
both places at once. Nothing in a container is independent of the actor's
binary, and the only record that could be — the provider's own — is not ours to
read. That limit is permanent; it is stated once in the spec rather than
re-argued per run.

**What it is for, then.** One leg of a join. The recording proxy holds what the
actor actually sent upstream; this holds what the actor's own bookkeeping says
happened. Agreement between the two rules out the failure family this repo
keeps hitting — *our* wiring narrating its own success — because faking it
would take our proxy and the CLI's session writer failing in step. Ruling that
family out is the point: it is the one we have actually met.

**Why the whole tree and not a glob.** A session that persisted a large tool
output stores it beside the transcript under ``<session-id>/tool-results/``,
and the transcript *references* it rather than inlining it. A ``*.jsonl`` glob
therefore yields a record with dangling references. Measured on a real config
directory (283 project directories, names only): 253 hold transcripts alone,
26 hold only a session subdirectory, 4 hold both. The shape inside the pinned
container is **not** confirmed by that reading, which is why this module takes
the directory rather than a pattern — a copy that cannot be wrong about the
layout does not need the layout to be settled.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
import shlex
from typing import override

from swe_lab.sandbox.observer import ArtifactSchema, SandboxObserver
from swe_lab.sandbox.result import Contribution
from swe_lab.sandbox.sandbox import SandboxFs

from .constants import AGENT_HOME

#: The agent's config root, pinned by the invocation script as
#: ``CLAUDE_CONFIG_DIR``. Its ``projects/`` subtree is where the CLI keeps its
#: own record of each session.
CONFIG_DIR = f"{AGENT_HOME}/.claude"
PROJECTS_SUBDIR = "projects"

#: The payload, and the workspace file it is built in. Advisory: a run whose
#: actor wrote no session record is still a run.
TRANSCRIPT_ARTIFACT = "claude_code.native_transcript.tar.gz"
TRANSCRIPT_FILENAME = "native_transcript.tar.gz"

#: The account of the copy itself. **Always** contributed, including when there
#: was nothing to copy: an absent payload beside an absent explanation reads
#: the same as a run nobody tried to collect from.
REPORT_ARTIFACT = "claude_code.native_transcript.json"
REPORT_FILENAME = "native_transcript.json"

# Long enough for a large session with persisted tool outputs, short enough
# that a hung archive does not eat the teardown.
_COPY_TIMEOUT_S = 120.0

_logger = logging.getLogger(__name__)


@dataclass
class NativeTranscriptObserver(SandboxObserver):
  """Archive the agent's own session record into the run's artifacts.

  Runs in ``before_destroy``: the agent has finished, so the record is
  complete, and the sandbox is still live, so there is still something to copy
  from. After that hook the container is gone and the writable layer with it —
  the record has no host-side copy of its own.

  **Never fails a run.** Every step is caught and written into the report
  instead: evidence collection that can abort the thing it documents is worse
  than no collection.

  Attributes:
    config_dir: The agent's config root inside the container.
  """

  config_dir: str = CONFIG_DIR
  _report: dict[str, object] = field(default_factory=dict, init=False)

  @override
  def output_schema(self) -> tuple[ArtifactSchema, ...]:
    """Declare the payload and the account of collecting it.

    Returns:
      Two artifacts, both advisory — the run is valid without either, and the
      report is what makes their absence readable.
    """
    return (
        ArtifactSchema(
            TRANSCRIPT_ARTIFACT,
            required=False,
            description=(
                "the agent's own session record, as the CLI wrote it"
                " (gzipped tar of the config dir's projects/ subtree)"
            ),
        ),
        ArtifactSchema(
            REPORT_ARTIFACT,
            required=False,
            description="whether that record was found, and what was archived",
        ),
    )

  @override
  def before_destroy(self, sb: SandboxFs) -> Contribution | None:
    """Archive the session record, and account for the attempt either way.

    Args:
      sb: The live sandbox — the container is destroyed after this hook.

    Returns:
      The report, plus the archive when there was one to make.
    """
    archived = self._archive(sb)
    report = json.dumps(self._report, indent=2, sort_keys=True) + "\n"
    artifacts = {TRANSCRIPT_ARTIFACT: TRANSCRIPT_FILENAME} if archived else {}
    return Contribution(
        artifacts=artifacts,
        inline_artifacts={REPORT_ARTIFACT: report.encode()},
    )

  def _archive(self, sb: SandboxFs) -> bool:
    """Run the archive command and record what happened.

    Args:
      sb: The live sandbox.

    Returns:
      Whether an archive was produced.
    """
    source = shlex.quote(self.config_dir)
    target = f'"$SANDBOX_WORKSPACE"/{TRANSCRIPT_FILENAME}'
    # `-v` names each member on stdout, which is the member count without a
    # second traversal. The whole subtree, never a pattern: see the module.
    command = f"tar czvf {target} -C {source} {PROJECTS_SUBDIR}"
    self._report["config_dir"] = self.config_dir
    try:
      result = sb.run_command(command, timeout=_COPY_TIMEOUT_S)
    except Exception as error:  # noqa: BLE001 — collection never fails a run
      _logger.exception("could not archive the native transcript")
      self._report["archived"] = False
      self._report["error"] = repr(error)
      return False
    self._report["exit_code"] = result.exit_code
    self._report["members"] = len(result.stdout.split())
    # Both, not just the status: a command can report success and leave no
    # file, and claiming an artifact that is not there fails the collect step —
    # which would turn a missing diagnostic into a failed run.
    archived = result.exit_code == 0 and sb.exists(TRANSCRIPT_FILENAME)
    self._report["archived"] = archived
    if not archived:
      # The ordinary case is a run whose actor wrote nothing there, and `tar`
      # says so on stderr. Kept verbatim rather than classified: the reason a
      # record is missing is exactly what a reader needs.
      self._report["stderr"] = result.stderr.strip()
    return archived
