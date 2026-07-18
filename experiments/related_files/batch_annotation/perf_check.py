"""Per-run performance profile: where did the time go, did anything stall?

For each instance it compares the **wall-clock** of every run
(``metadata.wall_clock_s``, recorded by ``run_agent``) against the agent's own
**active** time (the trace ``result.duration_ms``). A big gap = a stall: the run
sat idle (e.g. the box swap-thrashing under too much concurrency), not the agent
being slow. This is the profiling that would have caught the round-7 blowup
immediately (agent ~15s, wall-clock ~2h).

Usage:

    python .../batch_annotation/perf_check.py <round>
    python .../batch_annotation/perf_check.py <instance_id>

Flags any run whose idle overhead (wall_clock - active) exceeds STALL_S.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

from swe_lab.pipelines.related_files.storage import instance_dir

HERE = Path(__file__).parent
STALL_S = 120.0  # idle overhead above this is flagged as a stall


def _active_s(trace_path: Path) -> float | None:
  if not trace_path.is_file():
    return None
  rec = json.loads(trace_path.read_text())
  result = rec.get("extra_info", {}).get("result", {})
  ms = result.get("duration_ms") if isinstance(result, dict) else None
  return ms / 1000 if isinstance(ms, (int, float)) else None


def _runs(instance_id: str) -> list[tuple[str, float | None, float | None]]:
  base = instance_dir(instance_id)
  out: list[tuple[str, float | None, float | None]] = []
  for label in ("candidate_1", "candidate_2", "candidate_3", "aggregate"):
    ann = base / f"{label}.json"
    if not ann.is_file():
      continue
    meta = json.loads(ann.read_text()).get("metadata", {})
    active = _active_s(base / f"{label}.last_exchange.json")
    out.append((label, meta.get("wall_clock_s"), active))
  return out


def check(instance_id: str) -> bool:
  short = instance_id.replace("instance_", "")[:46]
  worst = 0.0
  parts: list[str] = []
  for label, wall, active in _runs(instance_id):
    overhead = (
        (wall - active) if (wall is not None and active is not None) else None
    )
    if overhead is not None:
      worst = max(worst, overhead)
    parts.append(
        f"{label}=wall {wall}s/active "
        f"{round(active, 1) if active else active}s"
        + (
            f" ⏸idle {round(overhead)}s"
            if overhead and overhead > STALL_S
            else ""
        )
    )
  flag = " 🚩STALL" if worst > STALL_S else ""
  print(f"{short:<48} {' | '.join(parts)}{flag}")
  return worst > STALL_S


def main() -> int:
  arg = sys.argv[1]
  if arg.startswith("instance_"):
    ids = [arg]
  else:
    ids = (HERE / f"{arg}_ids.txt").read_text().split()
  stalled = 0
  for i in ids:
    if (instance_dir(i) / "aggregate.json").is_file():
      stalled += check(i)
  print(f"\n-- stalled runs (idle > {STALL_S:.0f}s): {stalled} --")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
