"""tutanota de49d486: a build client that discards its error, then succeeds."""

from __future__ import annotations

from .._seam import (
    RegisteredFix,
    render,
    SweBenchProUnitTestSpec,
    with_setup,
)

_BUILD_SERVER_INSTANCE = (
    "instance_tutao__tutanota-de49d486feef842101506adf040a0f00ded59519"
    "-v10a26bfb45a064b93f4fc044a0254925037b88f1"
)
# The *built* client is what runs; patching the TypeScript source beside it
# would do nothing.
_CLIENT = "packages/tutanota-build-server/dist/BuildServerClient.js"
# 20 x the client's own 600 ms wait, so a detached server has ~12 s to bind
# instead of 1.2 s.
_ATTEMPTS = "20"
_CONNECT_ERROR = "could not connect to the build server"

_BUILD_SERVER_SETUP = """
c=@CLIENT@
# `dist/` is gitignored, so a patched client survives the reset between retry
# attempts (ADR-0005) and this may already be done. That has to be a no-op
# rather than an error, or the fix would abort every attempt after the first.
if ! grep -q 'connectionAttempts < @ATTEMPTS@ ' "$c"; then
  if ! grep -q 'connectionAttempts < 2 ' "$c"; then
    echo "the 2-attempt connect budget is not in @CLIENT@; already fixed?" >&2
    exit 1
  fi
  if [ "$(grep -c 'reject();' "$c")" != "2" ]; then
    echo "expected exactly two bare reject() in @CLIENT@" >&2
    exit 1
  fi
  sed -i 's/connectionAttempts < 2 /connectionAttempts < @ATTEMPTS@ /' "$c"
  sed -i 's/reject();/reject(new Error("@CONNECT_ERROR@"));/g' "$c"
fi
# Assert the *shape* of the result, and let the JavaScript parser be the judge
# of the rewrite rather than a grep for a keyword.
if ! grep -q 'connectionAttempts < @ATTEMPTS@ ' "$c"; then
  echo "the connect budget was not widened in @CLIENT@" >&2
  exit 1
fi
if [ "$(grep -c 'reject();' "$c")" != "0" ]; then
  echo "a bare reject() survived in @CLIENT@" >&2
  exit 1
fi
if ! node --check "$c"; then
  echo "@CLIENT@ is not valid JavaScript after patching" >&2
  exit 1
fi
"""


def _fix_instance_tutanota_de49d486(
    spec: SweBenchProUnitTestSpec,
) -> SweBenchProUnitTestSpec:
  """Stop the build client from turning a failed connection into a success.

  ``npm test`` builds through a **detached** build server. The client spawns it,
  waits for it to bind a unix socket, then sends a build request. In
  ``BuildServerClient.buildWithServer`` the wait is::

      const waitTimeinMs = 600;
      let connectionAttempts = 0;
      let lastError = null;
      while (connectionAttempts < 2 && this.state !== STATE_CONNECTED) {
          await new Promise(r => setTimeout(r, waitTimeinMs));
          try   { await this.connectAndBuild(); ...; lastError = null; }
          catch (e) { lastError = e; connectionAttempts++; }
      }
      if (lastError) { throw lastError; }

  and ``connectAndBuild``'s error handler rejects like this, in both branches::

      onError: async () => { ... reject(); }

  ``reject()`` with no argument rejects with **``undefined``**, so ``lastError``
  becomes ``undefined`` and ``if (lastError)`` — a truthiness test — reads that
  as *no error*. Two failed connections therefore leave the loop and resolve
  **successfully**: ``test.js`` prints ``build finished!`` and forks
  ``build/bootstrapTests-api.js``, which was never built. The run dies with
  ``MODULE_NOT_FOUND`` and it looks like a test failure.

  The other half is the budget: two attempts 600 ms apart give a freshly spawned
  detached Node process **1.2 seconds** to boot and bind.

  Both are repaired here — the budget so a slow start is survivable, and the
  rejection so a genuine failure is loud. Verified in this instance's own image,
  against a build server made to bind after 5 s:

  ===============  =========  =================================================
  server binds     client     outcome
  ===============  =========  =================================================
  after 5 s        unpatched  silent ``build finished!``, ``MODULE_NOT_FOUND``
  never            patched    ``Build failed: could not connect ...``
  after 5 s        patched    connected, built, bundle present
  ===============  =========  =================================================

  **Why retry could not save it.** Each attempt does restart the server and
  rebuild — ``-c`` maps to ``forceRestart`` and that was measured working — so
  the failure is not inherited state. It is the same race lost three times in a
  container slow enough to lose it once, and the swallowed error is why nothing
  in the logs said so.

  **Idempotent on purpose.** ``dist/`` is gitignored, so the patched client
  survives ``git reset --hard`` + ``git clean -fd`` between attempts. A fix that
  insisted on finding the defect would abort every attempt after the first,
  breaking the retry it exists to support.

  This satisfies the package's principle. The gold patch is correct and no test
  or assertion is touched — the broken thing is a vendored build client, and the
  pass/fail boundary does not move: a build that genuinely cannot start still
  fails the instance, just with the reason attached instead of as a phantom
  missing module.

  Scoped to this instance because the defect is version-bound: later tutanota
  commits (``f373ac38``) build in-process via ``runTestBuild`` and never touch
  the build server. Other instances old enough to use ``BuildServerClient`` can
  be added to ``INSTANCES`` once checked.

  Args:
    spec: The compiled spec for this instance.

  Returns:
    The spec with the build client repaired before the test run.
  """
  return with_setup(
      spec,
      mounts={},
      setup=render(
          _BUILD_SERVER_SETUP,
          CLIENT=_CLIENT,
          ATTEMPTS=_ATTEMPTS,
          CONNECT_ERROR=_CONNECT_ERROR,
      ),
  )


TUTANOTA_BUILD_SERVER = RegisteredFix(
    instances=(_BUILD_SERVER_INSTANCE,),
    fix=_fix_instance_tutanota_de49d486,
)
