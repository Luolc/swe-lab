"""Get the bytes of the native supervision wrapper, and prove they are it.

Same division of labour as the agent's own binary
(:mod:`swe_lab.harnesses.claude_code.binary`): this module only *gets the
bytes*, and where they land is the sandbox's call, so it satisfies the
``Materializer`` contract the asset seam expects — ``dest=None`` caches on the
host, a ``dest`` puts the file exactly there.

**Two sources, and only one of them is meant to last.**

- the **release**: a published artifact with a checksum pinned here, verified
  before use, exactly as the agent binary is. This is the intended path and
  there is nothing to fetch yet — the wrapper has not been released — so asking
  for it fails with a message saying so rather than producing a path to
  nothing.
- the **local build**, named by :data:`BINARY_ENV`. Transitional, and it exists
  because the wrapper has to be runnable before it is releasable. It is a real
  code path with real checks, not a documented intention; when the release
  exists this becomes a developer convenience rather than the only way in.

**The probe runs a file somebody else named, so it is treated as hostile.**
Two separate measures, because neither is sufficient. It executes under a
minimal environment (:data:`_PROBE_ENV`), since ``--version`` needs no
credential — but a child running as this user can read the parent's
environment out of ``/proc`` whatever we pass it, so that alone would not be a
boundary. The one that is: **child output is never repeated in an exception.**
An error message travels into logs, tracebacks and CI transcripts, so a
process that printed a credential and exited nonzero would otherwise have
written it there. Diagnosis is left to whoever can run the file themselves.

**What the check here does and does not establish.** It asks the binary to
answer ``--version`` *on this host* and requires the answer to name itself.
That is a positive premise rather than a list of exclusions: ``[ -x ]`` admits
a file that is present, executable and not this program, and so does every
other absence we might think to rule out. What it cannot establish is that the
bytes run *in the container* — a different machine, a different libc. The
invocation script's own ``--version`` probe is what settles that, and it is
deliberately not removed because this one passed.
"""

from __future__ import annotations

import os
import subprocess

from etils import epath

from swe_lab.paths import cache_root, find_repo_root

#: What the wrapper calls itself. Its ``--version`` answer begins with this
#: word, which is what makes the answer evidence about *which* program replied
#: rather than merely that something did.
BINARY_NAME = "swe-lab-supervisor"

#: Names a locally built wrapper to use instead of a release. **Transitional**
#: — see the module docstring. Read here, and only here, so that "the override
#: works" is a property of code rather than of a sentence in a document.
BINARY_ENV = "SWE_LAB_SUPERVISOR_BINARY"

#: Seconds to wait for ``--version``. Generous for a process start; a wrapper
#: that needs longer than this to say its own name is not one we want holding
#: a rollout open.
VERSION_TIMEOUT_S = 30.0

#: The environment the probe runs under. ``--version`` needs nothing from us,
#: and this path executes **a file an operator named** — a wrong path, a stale
#: build, in principle anything. Handing that process our environment would
#: hand it :data:`~.native_supervision.API_KEY_ENV` and the actor's own token
#: for no reason at all.
#:
#: ``PATH`` alone, because a binary that re-execs something expects to find it
#: and a probe that fails for the want of a `PATH` teaches nothing about the
#: wrapper.
_PROBE_ENV = {"PATH": os.defpath}

_BIN_SUBDIR = "bin"
_CACHE_NAMESPACE = "swe-lab-supervisor"


def local_build() -> epath.Path | None:
  """Return the locally built wrapper named by the environment, if any.

  Returns:
    The path :data:`BINARY_ENV` names, or ``None`` when it is unset or empty.
    An empty value is treated as unset: an exported-but-blank variable is how
    a shell says "I did not set this".
  """
  named = os.environ.get(BINARY_ENV, "").strip()
  return epath.Path(named) if named else None


def supervisor_version(binary: epath.PathLike) -> str:
  """Return the version this binary reports, or refuse it.

  The positive chain, in order: the path is a file, running it with
  ``--version`` exits zero, and what it prints names :data:`BINARY_NAME` and a
  version after it. Only a file that passes every step is accepted, so the
  arms nobody enumerated — a text file, a script for another program, a
  truncated download — fail at whichever step they first cannot answer, rather
  than at a check written specifically for them.

  Args:
    binary: The wrapper to interrogate.

  Returns:
    The version token, e.g. ``0.1.0``. Read off the binary rather than
    asserted against a constant: what is wanted here is *the version this is*,
    and a mismatch against a guess would fail a real artifact.

  Raises:
    FileNotFoundError: The path is not a file.
    RuntimeError: It is a file, but it did not answer as this program.
  """
  path = epath.Path(binary)
  if not path.is_file():
    raise FileNotFoundError(f"no supervisor wrapper at {path}")
  try:
    answer = subprocess.run(
        [str(path), "--version"],
        capture_output=True,
        text=True,
        timeout=VERSION_TIMEOUT_S,
        check=False,
        env=_PROBE_ENV,
    )
  except OSError as error:
    raise RuntimeError(
        f"the file at {path} could not be executed: {error}"
    ) from error
  if answer.returncode != 0:
    raise RuntimeError(
        f"{path} --version exited {answer.returncode}. Run it yourself to see"
        " what it printed; its output is deliberately not repeated here."
    )
  fields = answer.stdout.split()
  if len(fields) < 2 or fields[0] != BINARY_NAME:
    raise RuntimeError(
        f"{path} --version exited 0 but does not name {BINARY_NAME} and a"
        " version; it is some other program. Run it yourself to see what it"
        " printed; its output is deliberately not repeated here."
    )
  return fields[1]


def binary_cache_path(
    version: str, *, repo_root: epath.PathLike | None = None
) -> epath.Path:
  """Return the host cache path for ``version`` of the wrapper.

  Args:
    version: The wrapper release the copy is of.
    repo_root: Repo root used to locate the cache; discovered when omitted.

  Returns:
    Where a host-cached copy of that version lives.
  """
  return (
      cache_root(repo_root or find_repo_root())
      / _BIN_SUBDIR
      / _CACHE_NAMESPACE
      / version
      / BINARY_NAME
  )


def ensure_supervisor_binary(
    *,
    dest: epath.PathLike | None = None,
    repo_root: epath.PathLike | None = None,
) -> epath.Path:
  """Ensure a verified wrapper exists, and return where it landed.

  Satisfies the ``Materializer`` contract (see :mod:`swe_lab.sandbox.assets`):
  ``dest=None`` caches on the host and returns the cache path; a ``dest`` puts
  it exactly there.

  Every path through here either returns a binary that answered for itself or
  raises. There is deliberately no branch that returns a path to something
  unverified: a run whose supervisor is not really there must fail, because it
  would otherwise be recorded as an ordinary result and kept as data.

  Args:
    dest: Where the binary must end up; ``None`` for the host cache.
    repo_root: Repo root used to locate the cache; discovered when omitted.

  Returns:
    The path of the verified, executable wrapper.

  Raises:
    RuntimeError: No release is published yet and :data:`BINARY_ENV` names no
      local build, so there is nothing to verify.
  """
  source = local_build()
  if source is None:
    raise RuntimeError(
        f"no {BINARY_NAME} release is published yet, so there is nothing to"
        f" fetch. Build it (cargo build --release --target"
        f" x86_64-unknown-linux-musl, in rust/{BINARY_NAME}) and point"
        f" {BINARY_ENV} at the binary. This override is transitional and goes"
        " away when the release exists."
    )
  version = supervisor_version(source)
  target = (
      epath.Path(dest)
      if dest is not None
      else binary_cache_path(version, repo_root=repo_root)
  )
  if epath.Path(source).resolve() == target.resolve():
    return target
  target.parent.mkdir(parents=True, exist_ok=True)
  _ = epath.Path(source).copy(target, overwrite=True)
  os.chmod(target, 0o755)
  return target
