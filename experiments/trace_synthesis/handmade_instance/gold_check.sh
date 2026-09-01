#!/usr/bin/env bash
# Step 1 of task 01: run each candidate's gold patch through its unit tests,
# before spending a rollout on it. Sequential on purpose — these build/pull
# Docker images and running four at once only makes the box slower.
#
# Usage: experiments/trace_synthesis/handmade_instance/gold_check.sh
# Writes:
#   runs/gold/<short>.log       full CLI output per instance
#   runs/gold/summary.jsonl     one append-only line per instance
set -uo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
out="$here/runs/gold"
mkdir -p "$out"

while read -r instance; do
  [ -n "$instance" ] || continue
  short="$(printf '%s' "$instance" | cut -c1-60)"
  started="$(date -Is)"
  start_s=$SECONDS
  uv run swe-lab run gold_unit_test "$instance" --dataset swebench_pro \
    >"$out/$short.log" 2>&1
  code=$?
  elapsed=$(( SECONDS - start_s ))
  printf '{"instance": "%s", "started": "%s", "wall_s": %d, "exit_code": %d}\n' \
    "$instance" "$started" "$elapsed" "$code" >>"$out/summary.jsonl"
  echo "[$(date -Is)] $short -> exit $code (${elapsed}s)"
done < "$here/instances.txt"
