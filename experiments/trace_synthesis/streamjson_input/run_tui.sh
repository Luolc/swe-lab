#!/usr/bin/env bash
# Run one TUI scenario through the *Go* cc-reverse-proxy (the only build with
# redaction). Same contract as run_proxy.sh, different front end.
#
# Usage: run_tui.sh <control|midturn> <out-dir> [port]
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../../.." && pwd)"
scenario="$1"
out_dir="$2"
port="${3:-20117}"

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

uv run python "$here/tui_driver.py" "$scenario" "$out_dir" \
  --base-url "http://127.0.0.1:$port"
