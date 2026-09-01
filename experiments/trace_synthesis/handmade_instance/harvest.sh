#!/usr/bin/env bash
# Step 2 of task 01: take one rollout sample and, if it is a genuine failure,
# freeze it immediately.
#
# The freeze is inside this script on purpose. `swe-lab run` rmtree's
# .cache/runs/<workflow>/<instance_id> at the start of every non-`--resume` run
# (src/swe_lab/cli/run.py), so a harvested failure only survives until the next
# command against the same instance. Doing the copy by hand invites losing it.
#
# Usage: harvest.sh <instance_id> <rollout_id>
set -uo pipefail

instance="${1:?usage: harvest.sh <instance_id> <rollout_id>}"
rollout_id="${2:?usage: harvest.sh <instance_id> <rollout_id>}"
workflow="rollout_and_unit_test"

here="$(cd "$(dirname "$0")" && pwd)"
out="$here/runs"
mkdir -p "$out"
short="$(printf '%s' "$instance" | cut -c1-60)"
log="$out/$short-r$rollout_id.log"

started="$(date -Is)"
start_s=$SECONDS
uv run swe-lab run "$workflow" "$instance" \
  --dataset swebench_pro --rollout-id "$rollout_id" >"$log" 2>&1
code=$?
elapsed=$(( SECONDS - start_s ))

case $code in
  0) verdict="resolved" ;;
  2) verdict="unresolved" ;;   # the failure we want
  *) verdict="infrastructure" ;;  # 1 = task/edge failed or refused: not a sample
esac

printf '{"instance": "%s", "rollout_id": %d, "started": "%s", "wall_s": %d, "exit_code": %d, "verdict": "%s"}\n' \
  "$instance" "$rollout_id" "$started" "$elapsed" "$code" "$verdict" >>"$out/rollouts.jsonl"
echo "[$(date -Is)] $short r$rollout_id -> exit $code ($verdict, ${elapsed}s)"

if [ "$code" -eq 2 ]; then
  echo "harvested a genuine failure; freezing before anything else runs"
  "$here/freeze.sh" "$instance" "$rollout_id"
fi
exit $code
