"""Prepare a per-instance working directory for the annotation agent.

The agent runs with the checked-out repo (at ``base_commit``) as its working
directory. The "hint" materials it may consult — problem statement,
requirements, interface, gold patch, test patch, git log — are written to a
clearly-named ``.annotation_context/`` subdirectory so the agent can read them
without them being mistaken for repo source. The agent writes its result to a
fixed filename (``ANNOTATION_OUTPUT``) in the working directory.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess

from ..datasets.swebench_pro import SweBenchProInstance
from ..repo.provider import RepoProvider

# Subdirectory (inside the checkout) holding the hint materials.
CONTEXT_DIR = ".annotation_context"
# File the agent must write its snippet list to, relative to the checkout.
ANNOTATION_OUTPUT = ".annotation_output.json"

_GIT_LOG_LIMIT = 200


@dataclass(frozen=True, slots=True)
class Workspace:
  """Paths for one prepared annotation working directory."""

  instance_id: str
  checkout: Path

  @property
  def context_dir(self) -> Path:
    return self.checkout / CONTEXT_DIR

  @property
  def output_path(self) -> Path:
    return self.checkout / ANNOTATION_OUTPUT


def prepare_workspace(
    instance: SweBenchProInstance, provider: RepoProvider
) -> Workspace:
  """Provision the checkout and write the hint materials into it."""
  checkout = provider.provision(instance)
  workspace = Workspace(instance.instance_id, checkout)

  context = workspace.context_dir
  context.mkdir(parents=True, exist_ok=True)
  _write(context / "problem_statement.md", instance.problem_statement)
  _write(context / "requirements.md", instance.requirements)
  _write(context / "interface.md", instance.interface)
  _write(context / "gold_patch.diff", instance.patch)
  _write(context / "test_patch.diff", instance.test_patch)
  _write(context / "git_log.txt", _git_log(checkout))

  # Start each run from a clean slate: drop any output from a previous run.
  workspace.output_path.unlink(missing_ok=True)
  return workspace


def _write(path: Path, content: str) -> None:
  _ = path.write_text(content if content.endswith("\n") else content + "\n")


def _git_log(checkout: Path) -> str:
  result = subprocess.run(
      ["git", "log", f"-n{_GIT_LOG_LIMIT}", "--stat", "--date=short"],
      cwd=str(checkout),
      capture_output=True,
      text=True,
      check=False,
  )
  return result.stdout if result.returncode == 0 else result.stderr
