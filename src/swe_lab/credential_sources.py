"""Which shell name each credential was adopted from — **names, never values**.

A leaf on purpose: it imports nothing from this package, so both ends can use
it. :mod:`swe_lab.host_env` performs the adoptions and records them here, and
:mod:`swe_lab.rollout` reads them into the run record. Were this state to live
in either of those, the other would have to import it and the import graph
closes a loop (``host_env`` → the harness constants → ``native_supervision`` →
``rollout``), which fails at import time in a way that names neither end.

Nothing here ever holds a credential. The point of recording the *source name*
is that adoption is otherwise invisible: a run reads a canonical variable and
cannot say where that value came from, so a reader of a finished run could not
tell a supervisor key taken from the machine-wide pool from one exported
deliberately for that run.
"""

from __future__ import annotations

from collections.abc import Mapping

_ADOPTED_FROM: dict[str, str] = {}


def record_adoption(canonical: str, source: str) -> None:
  """Record that ``canonical`` was filled from ``source``.

  Args:
    canonical: The environment-variable name a run reads.
    source: The environment-variable name its value was copied from.
  """
  _ADOPTED_FROM[canonical] = source


def forget_adoptions() -> None:
  """Drop the record, so what follows describes one adoption pass."""
  _ADOPTED_FROM.clear()


def adopted_credential_sources() -> Mapping[str, str]:
  """Return canonical name → source name for the adoptions performed.

  Empty when a run supplied every canonical name itself, which is what CI does.

  Returns:
    A mapping of environment-variable names. Names only — no value passes
    through here.
  """
  return dict(_ADOPTED_FROM)
