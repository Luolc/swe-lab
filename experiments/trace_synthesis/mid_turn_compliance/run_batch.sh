#!/usr/bin/env bash
# Run a set of (arm, fixture) pairs with a bounded number in flight.
#
# Concurrency is not in §9's freeze — it is a run condition, not a design
# choice — but it can *manufacture* an infrastructure failure by provoking
# upstream rate limiting, and §9 allows only one re-run per run. So it is
# calibrated on the discarded pilot and then held fixed, and every run records
# the value it ran under.
#
# Usage: run_batch.sh <phase> <concurrency> <arm> [arm...]
#   run_batch.sh pilot 4 pos
#   run_batch.sh graded 6 mid neg pos
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../../.." && pwd)"
phase="$1"; concurrency="$2"; shift 2
scratch="${SCRATCH_DIR:-/tmp}/mid-turn-compliance"
mkdir -p "$scratch"

# Built once, here, before anything fans out.
binary="$repo/.cache/bin/cc-reverse-proxy"
[ -x "$binary" ] || { echo "build $binary first" >&2; exit 1; }

fixtures="$(uv run python -c "
import sys; sys.path.insert(0, '$here'); import tasks
print(' '.join(sorted(tasks.BY_SLUG)))")"

port=20400
for arm in "$@"; do
  for fixture in $fixtures; do
    out="$here/runs/$phase/$arm/$fixture"
    [ -f "$out/manifest.json" ] && continue
    while [ "$(jobs -rp | wc -l)" -ge "$concurrency" ]; do wait -n; done
    port=$((port + 1))
    (
      timeout 900 "$here/run_one.sh" "$arm" "$fixture" "$out" "$port" \
        --phase "$phase" --concurrency "$concurrency" \
        > "$scratch/$phase-$arm-$fixture.log" 2>&1 \
        || echo "FAILED $arm/$fixture" >> "$scratch/$phase-failures.txt"
    ) &
  done
done
wait
echo "BATCH DONE: $phase"
