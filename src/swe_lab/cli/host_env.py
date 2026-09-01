"""What this repo asks of a *developer's own shell*, and how a run recovers it.

The ask is a single negative: the agent's OAuth token must not sit in an
interactive shell under the name Claude Code itself reads. Anyone's `claude`
started in this directory would log in with it, and ours is an inference-only
subscription token — Remote Control then refuses to start
(``docs/conventions.md`` → Hazards). So `.envrc.local` exports the repo-scoped
name instead, and the CLI hands the value back to the canonical one for its own
process only.

This is a **local convenience, not a contract**: the sandbox layer and the
harness still know exactly one name for this variable, and CI sets that name
directly from the repository secret.
"""

from __future__ import annotations

import os

from swe_lab.harnesses.claude_code.constants import OAUTH_TOKEN_ENV

# The repo-scoped name `.envrc.local` exports (see `.envrc.local.example`).
HOST_OAUTH_TOKEN_ENV = "SWE_LAB_CLAUDE_CODE_OAUTH_TOKEN"


def adopt_host_scoped_token() -> None:
  """Copy the repo-scoped OAuth token to the canonical name, in this process.

  A no-op unless it is needed: an environment that already *has* the canonical
  name is left alone, because that is what CI does — it sets it straight from
  the repository secret, and a shim that overwrote it would be deciding a
  question its caller had already answered.

  "Has" is presence, not a value: ``CLAUDE_CODE_OAUTH_TOKEN= swe-lab run …``
  is someone blanking the credential for one command, and quietly restoring it
  from ``.envrc.local`` would override an explicit instruction with an ambient
  one. The *source* side is the other way round — an empty repo-scoped variable
  is nothing to copy, and copying it would only manufacture the empty canonical
  variable this branch exists to respect. The bundle's ``smoke-test.sh`` runs
  the same two rules in shell.
  """
  if OAUTH_TOKEN_ENV in os.environ:
    return
  host_scoped = os.environ.get(HOST_OAUTH_TOKEN_ENV)
  if host_scoped:
    os.environ[OAUTH_TOKEN_ENV] = host_scoped
