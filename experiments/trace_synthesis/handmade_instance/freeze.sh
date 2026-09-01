#!/usr/bin/env bash
# Step 3 of task 01: copy a run out of .cache/ before anything overwrites it.
#
# `swe-lab run` starts every non-`--resume` run with
# `output_dir.rmtree(missing_ok=True)` on .cache/runs/<workflow>/<instance_id>
# (src/swe_lab/cli/run.py), so the next rollout of the same instance destroys
# the one just harvested. Run this the moment a rollout exits 2, before issuing
# any further swe-lab command.
#
# Set FROZEN_ROOT to freeze somewhere other than this directory. Do set it when
# working in a git worktree: `git worktree remove` deletes gitignored content
# with no warning, and `frozen/` is gitignored (docs/conventions.md -> Hazards).
#
# Usage: freeze.sh <instance_id> <rollout_id> [workflow] [label]
set -euo pipefail

instance="${1:?usage: freeze.sh <instance_id> <rollout_id> [workflow] [label]}"
rollout_id="${2:?usage: freeze.sh <instance_id> <rollout_id> [workflow] [label]}"
workflow="${3:-rollout_and_unit_test}"
label="${4:-failure}"

here="$(cd "$(dirname "$0")" && pwd)"
repo_root="$(cd "$here/../../.." && pwd)"
src="$repo_root/.cache/runs/$workflow/$instance"
dst="${FROZEN_ROOT:-$here/frozen}/$label-rollout-$rollout_id"

[ -d "$src" ] || { echo "freeze: nothing at $src" >&2; exit 1; }
[ -e "$dst" ] && { echo "freeze: $dst already exists; not overwriting" >&2; exit 1; }

mkdir -p "$(dirname "$dst")"
cp -a "$src" "$dst"

# Provenance the copied tree does not carry: which instance, which sample, the
# exact command that produced it, and the commit we were on.
cat > "$dst/PROVENANCE.json" <<JSON
{
  "instance_id": "$instance",
  "dataset": "swebench_pro",
  "workflow": "$workflow",
  "rollout_id": $rollout_id,
  "command": "uv run swe-lab run $workflow $instance --dataset swebench_pro --rollout-id $rollout_id",
  "source_dir": "$src",
  "frozen_at": "$(date -Is)",
  "git_commit": "$(git -C "$repo_root" rev-parse HEAD)",
  "capture": "stream"
}
JSON

echo "froze $src -> $dst"
du -sh "$dst"
find "$dst" -maxdepth 3 -type f | sed "s|$dst/||" | sort
