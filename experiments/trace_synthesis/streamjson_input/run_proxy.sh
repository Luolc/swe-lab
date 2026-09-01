#!/usr/bin/env bash
# Run one driver scenario through the *Go* cc-reverse-proxy (the only build with
# redaction; the two python ports have none and leaked a live token once).
#
# Usage: run_proxy.sh <scenario> <out-dir> [port] [extra driver args...]
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../../.." && pwd)"
scenario="$1"
out_dir="$2"
port="${3:-20111}"
shift 3 || shift 2

binary="$repo/.cache/bin/cc-reverse-proxy"
mkdir -p "$out_dir"
log="$out_dir/proxy.jsonl"

"$binary" --port "$port" --target https://api.anthropic.com --output "$log" \
  > "$out_dir/proxy.stderr.log" 2>&1 &
proxy_pid=$!
trap 'kill "$proxy_pid" 2>/dev/null; wait "$proxy_pid" 2>/dev/null' EXIT

for _ in $(seq 1 100); do
  (exec 3<>/dev/tcp/127.0.0.1/"$port") 2>/dev/null && break
  sleep 0.1
done

uv run python "$here/driver.py" "$scenario" "$out_dir" \
  --base-url "http://127.0.0.1:$port" "$@"
