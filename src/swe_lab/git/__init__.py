"""Everything that reasons about the task repo's **git state**.

Three concerns, one per module, all sharing a single subject — the `.git`
directory of the repo an instance is solved in:

- :mod:`~swe_lab.git.patch` — get the agent's work *out* as a clean, applyable
  diff against ``base_commit`` (ADR-0001).
- :mod:`~swe_lab.git.history` — keep the answer *out*: strip future commits
  before the agent starts, and prove they are gone (ADR-0010 §3b).
- :mod:`~swe_lab.git.audit` — the standalone task that runs the purge with no
  agent, so a whole dataset can be swept before an expensive run.

``patch`` and ``history`` are **pure**: they build in-container shell and parse
what it reports, holding no sandbox and running no process. The observers that
drive them live with the other shared observers, in
:mod:`swe_lab.sandbox.observers` — a script builder is git knowledge, while
*when* to run it is engine knowledge, and the split keeps this package free of
any dependency on the sandbox lifecycle.

**Only the pure halves are re-exported here.** ``audit`` is a ``Task``, so it
sits on the workflow layer, and hoisting it into this ``__init__`` would make
importing a diff helper drag the whole engine in — a genuine import cycle,
since ``sandbox.testing`` reads a report type from here. Import it by module,
as ``workflow.definitions`` does:
``from swe_lab.git.audit import GitIntegrityAuditTask``. Same rule
``workflow`` already states about not importing its own tasks.
"""

from .history import (
    build_purge_script,
    build_report_script,
    GitHistoryReport,
)
from .patch import (
    build_extraction_script,
    is_effectively_empty,
    strip_binary_hunks,
)

__all__ = [
    "GitHistoryReport",
    "build_extraction_script",
    "build_purge_script",
    "build_report_script",
    "is_effectively_empty",
    "strip_binary_hunks",
]
