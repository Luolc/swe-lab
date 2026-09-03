"""What this repo asks of a *developer's own shell*, and how a run recovers it.

Two credentials reach a run under a name that is not the name the developer's
shell exports, and this module is the one place that gap is closed. Both
adoptions are the same mechanism — copy a whole value to another name, in this
process only, never overwriting a name that is already set — and they **point
in opposite directions**, which is the thing to know before reading either:

- **Narrowing** (the OAuth token). The shell exports the repo-scoped
  ``SWE_LAB_CLAUDE_CODE_OAUTH_TOKEN``; the run reads the canonical, unprefixed
  ``CLAUDE_CODE_OAUTH_TOKEN``. The prefix exists so that anyone's ``claude``
  started in this directory does **not** pick the token up — ours is an
  inference-only subscription token, and Remote Control then refuses to start
  (``docs/conventions.md`` → Hazards).
- **Adopting** (the supervisor's provider key). The shell exports the
  machine-wide ``OPENROUTER_API_KEYS``, shared with every other consumer on the
  box; the run reads the repo-scoped ``SWE_LAB_SUPERVISOR_API_KEY``, which the
  sandbox passes by reference to the wrapper.

So the repo-scoped name is the *source* in one and the *target* in the other. A
reader who assumes they run the same way infers a rule that does not exist
("this repo's names are always the wider ones"), and an unmarked difference is
indistinguishable from no difference at all.

This is a **local convenience, not a contract**: the sandbox layer and the
harness still know exactly one name for each variable, and CI sets those names
directly from the repository secrets.
"""

from __future__ import annotations

import os

from swe_lab.credential_sources import forget_adoptions, record_adoption
from swe_lab.harnesses.claude_code.constants import OAUTH_TOKEN_ENV
from swe_lab.trace_synthesis.judge import OPENROUTER_KEYS_ENV
from swe_lab.trace_synthesis.native_supervision import (
    API_KEY_ENV as SUPERVISOR_API_KEY_ENV,
)

# The repo-scoped name `.envrc.local` exports (see `.envrc.local.example`).
HOST_OAUTH_TOKEN_ENV = "SWE_LAB_CLAUDE_CODE_OAUTH_TOKEN"

# The machine-wide name `.envrc.local` exports, which the supervisor's key is
# adopted *from*. Its value may hold several comma-separated keys; nothing here
# looks at that, because splitting a credential is the consuming program's job
# and a shell or a shim that splits it puts a key somewhere it can be seen —
# the wrapper's `config::api_key_from_env` splits it in-process instead.
HOST_SUPERVISOR_API_KEY_ENV = OPENROUTER_KEYS_ENV


def _adopt(canonical: str, host_scoped: str) -> None:
  """Copy one whole value to the name a run reads, if that name is free.

  Args:
    canonical: The name the run, the harness and the sandbox know.
    host_scoped: The name the developer's shell exports.
  """
  if canonical in os.environ:
    return
  value = os.environ.get(host_scoped)
  if value:
    os.environ[canonical] = value
    record_adoption(canonical, host_scoped)


def adopt_host_scoped_credentials() -> None:
  """Hand each shell-scoped credential to the name a run reads, in-process.

  A no-op per credential unless it is needed: an environment that already
  *has* the canonical name is left alone, because that is what CI does — it
  sets it straight from the repository secret, and a shim that overwrote it
  would be deciding a question its caller had already answered.

  "Has" is presence, not a value: ``CLAUDE_CODE_OAUTH_TOKEN= swe-lab run …``
  is someone blanking the credential for one command, and quietly restoring it
  from ``.envrc.local`` would override an explicit instruction with an ambient
  one. The *source* side is the other way round — an empty shell-scoped
  variable is nothing to copy, and copying it would only manufacture the empty
  canonical variable this branch exists to respect. The bundle's
  ``smoke-test.sh`` runs the same two rules in shell.
  """
  forget_adoptions()
  _adopt(OAUTH_TOKEN_ENV, HOST_OAUTH_TOKEN_ENV)
  _adopt(SUPERVISOR_API_KEY_ENV, HOST_SUPERVISOR_API_KEY_ENV)
