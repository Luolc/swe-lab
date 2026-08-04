"""The shipped GitHub workflows must invoke the CLI the way the CLI parses.

A `.yml` is never type-checked, imported, or exercised by the test suite, so a
CLI grammar change lands green and breaks the manual `workflow_dispatch` runs
silently — nobody finds out until someone dispatches one. That is exactly what
happened when the general CLI landed: the workflows still passed
`--rollout.timeout 3000` (space-separated), which the override grammar rejects.

So this reads the real workflow files and checks their real invocations.
"""

from __future__ import annotations

from pathlib import Path
import re

import pytest

from swe_lab.cli.overrides import OverrideError, parse_overrides

# Imported for its registrations: the built-in workflow definitions.
import swe_lab.workflow.definitions as _definitions
from swe_lab.workflow.registry import registered_workflows

assert _definitions.ROLLOUT_KEY  # the import above is for its side effect

_WORKFLOWS_DIR = Path(__file__).resolve().parents[1] / ".github" / "workflows"

# A `--foo.bar=...` / `--foo.bar ...` token in a workflow's run: block. The
# dotted name is what makes it an *override* rather than one of the command's
# own declared options (`--backend`, `--pull`).
_OVERRIDE_RE = re.compile(r"--[A-Za-z_][\w]*(?:\.[\w]+)+(?:=\S*)?")

# `${{ inputs.model }}` and friends are substituted by Actions, not by us —
# stand in a plausible literal so the grammar sees a real value.
_EXPANSION_RE = re.compile(r"\$\{\{[^}]*\}\}")


def _yaml_files() -> list[Path]:
  return sorted(_WORKFLOWS_DIR.glob("*.yml"))


def test_workflows_directory_is_found():
  # Guards the whole module: a wrong path would make every test below vacuous.
  assert _yaml_files(), f"no workflow files under {_WORKFLOWS_DIR}"


@pytest.mark.parametrize("path", _yaml_files(), ids=lambda p: p.name)
def test_cli_overrides_in_workflows_parse(path: Path):
  """Every dotted override a workflow passes must satisfy the grammar."""
  text = path.read_text()
  found = _OVERRIDE_RE.findall(text)
  if not found:
    pytest.skip(f"{path.name} passes no CLI overrides")
  args = [_EXPANSION_RE.sub("placeholder", token) for token in found]
  try:
    parsed = parse_overrides(args)
  except OverrideError as error:
    raise AssertionError(
        f"{path.name} invokes the CLI with an override the parser rejects:"
        f" {error}\n"
        "Overrides are spelled --<entry>.<field-path>=<value>, one token."
    ) from error
  assert len(parsed) == len(args)


@pytest.mark.parametrize("path", _yaml_files(), ids=lambda p: p.name)
def test_workflow_names_in_workflows_are_registered(path: Path):
  """A workflow name a `.yml` runs must actually be registered."""
  text = path.read_text()
  # `python -m swe_lab run <name>` — including the `a && 'x' || 'y'` ternary
  # the dispatch files use to pick between two registered names.
  names = set(re.findall(r"'([a-z_]+)'\s*\}\}", text)) | set(
      re.findall(r"swe_lab run\s*\\?\s*\n?\s*([a-z_]+)\s", text)
  )
  unknown = sorted(n for n in names if n not in set(registered_workflows()))
  assert not unknown, (
      f"{path.name} runs unregistered workflow name(s): {unknown};"
      f" registered: {registered_workflows()}"
  )
