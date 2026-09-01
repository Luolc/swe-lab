"""Copy a run's session transcript into the run directory, so it is an artifact.

Claude Code writes the session transcript to `~/.claude/projects/<slug>/<session
-id>.jsonl` — host-private, mutable, and cleaned up on its own schedule. Every
transcript-level claim in `REPORT.md` is read off that file, so the file has to
live **beside the run**, not on the machine that happened to produce it.

`snapshot(run_dir)` copies it to `<run_dir>/transcript.jsonl` and records where
it came from in `<run_dir>/transcript.provenance.json`. `load(run_dir)` is what
readers use: the committed copy when there is one, the live host file otherwise
(and it says which it returned).

Run as a script to backfill every run directory that has a `meta.json`:

    python transcripts.py runs/*
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

PROJECTS = pathlib.Path.home() / ".claude" / "projects"
SNAPSHOT_NAME = "transcript.jsonl"
PROVENANCE_NAME = "transcript.provenance.json"


def session_ids(run_dir: pathlib.Path) -> list[str]:
  """Return the session ids a run's `meta.json` records (one, or two phases)."""
  meta = json.loads((run_dir / "meta.json").read_text())
  ids = [meta[key] for key in ("session_id",) if key in meta]
  return [str(i) for i in ids]


def host_transcript(session_id: str) -> pathlib.Path | None:
  """Return the host's transcript path for `session_id`, if it still exists."""
  hits = sorted(PROJECTS.glob(f"*/{session_id}.jsonl"))
  return hits[0] if hits else None


def snapshot(run_dir: pathlib.Path) -> pathlib.Path | None:
  """Copy the run's transcript into `run_dir`; return the snapshot path.

  Args:
    run_dir: A run directory containing `meta.json`.

  Returns:
    The path of the written snapshot, or ``None`` when the host no longer has
    the transcript (already-snapshotted runs are left alone and returned).
  """
  target = run_dir / SNAPSHOT_NAME
  parts: list[str] = []
  sources: list[str] = []
  for session_id in session_ids(run_dir):
    source = host_transcript(session_id)
    if source is None:
      continue
    parts.append(source.read_text())
    sources.append(str(source))
  if not parts:
    return target if target.is_file() else None
  _ = target.write_text("".join(parts))
  _ = (run_dir / PROVENANCE_NAME).write_text(
      json.dumps(
          {
              "copied_from": sources,
              "copied_at": dt.datetime.now(dt.timezone.utc).isoformat(),
              "note": (
                  "Verbatim copy of the Claude Code session transcript(s) this"
                  " run produced. The live file is host-private and mutable;"
                  " this is the artifact every transcript-level claim in"
                  " REPORT.md is read from."
              ),
          },
          indent=2,
      )
  )
  return target


def load(run_dir: pathlib.Path) -> tuple[str, str]:
  """Return `(text, provenance)` for a run's transcript.

  Args:
    run_dir: A run directory.

  Returns:
    The transcript text and a one-word source: ``"snapshot"`` for the committed
    copy, ``"host"`` for the live session file, ``"missing"`` for neither.
  """
  snap = run_dir / SNAPSHOT_NAME
  if snap.is_file():
    return snap.read_text(), "snapshot"
  texts = [
      path.read_text()
      for path in (host_transcript(i) for i in session_ids(run_dir))
      if path is not None
  ]
  if texts:
    return "".join(texts), "host"
  return "", "missing"


def main() -> int:
  for arg in sys.argv[1:]:
    run_dir = pathlib.Path(arg)
    if not (run_dir / "meta.json").is_file():
      continue
    written = snapshot(run_dir)
    print(f"{run_dir}: {'snapshot ' + str(written) if written else 'NO TRANSCRIPT'}")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
