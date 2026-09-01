#!/usr/bin/env bash
# One run, captured through the *Go* cc-reverse-proxy — the only build with
# redaction; the two python ports have none and leaked a live token once.
#
# Usage: run_one.sh <arm> <fixture> <out-dir> [port] [extra driver args...]
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../../.." && pwd)"
arm="$1"; fixture="$2"; out_dir="$3"; port="${4:-20301}"
shift 4 || shift 3

mkdir -p "$out_dir"
"$repo/.cache/bin/cc-reverse-proxy" --port "$port" \
  --target https://api.anthropic.com --output "$out_dir/proxy.jsonl" \
  > "$out_dir/proxy.stderr.log" 2>&1 &
proxy_pid=$!
trap 'kill "$proxy_pid" 2>/dev/null; wait "$proxy_pid" 2>/dev/null' EXIT

for _ in $(seq 1 100); do
  (exec 3<>/dev/tcp/127.0.0.1/"$port") 2>/dev/null && break
  sleep 0.1
done

uv run python "$here/driver.py" "$arm" "$fixture" "$out_dir" \
  --base-url "http://127.0.0.1:$port" "$@"
