#!/bin/sh
# Regenerate the cross-language summary fixtures (tests/fixtures/native_supervision/)
# with the real binary, inside the pinned container:
#
#   scripts/check-in-container.sh sh scripts/summary-fixtures.sh
#
# One fixture per way a run ends. The crate's summary tests hold them to the
# binary's field list; the Python reader's tests read them.
set -eu
cd /repo/rust/swe-lab-supervisor
cargo build -q
bin=target/debug/swe-lab-supervisor
out=/repo/tests/fixtures/native_supervision
mkdir -p "$out"
work=$(mktemp -d)
sha=ffb2dadfe2b36eb3f44f28c4282a8d51e84e1c943558500787cbb0518e2900a1
config() {
cat > "$1" <<CFG
{"schema_version": 1, "task": "t", "criterion": {"name": "general-practice", "sha256": "$3"},
 "policy": {"kind": "speak-when-off-track", "budget": 1, "cooldown": 1, "window": 1, "judge_every_n_assistant_messages": 1, "block_actor_while_judging": "off"},
 "model": {"name": "m"}, "timeouts": {"model_call_ms": 1000, "term_grace_ms": 500},
 "limits": {"max_event_line_bytes": 65536, "max_actor_stdout_bytes": $2, "max_actor_stderr_bytes": 1048576}}
CFG
}
printf '%s\n' '{"type":"user","message":{"role":"user","content":[{"type":"text","text":"t"}]}}' > "$work/prompt.json"
launch() {
  d="$work/$1"; mkdir -p "$d"
  SWE_LAB_SUPERVISOR_BASE_URL=http://127.0.0.1:9/v1 "$bin" run --config "$2" --actor-prompt "$work/prompt.json" \
    --actor-event-log "$d/events.jsonl" --supervisor-log "$d/supervisor.jsonl" --summary "$d/summary.json" \
    --actor-stderr "$d/actor.stderr" -- sh -c "$3" >"$d/wrapper.out" 2>"$d/wrapper.err"
}
run() { launch "$@" || echo "$1: exit $?"; cp "$work/$1/summary.json" "$out/$1.json"; }
config "$work/ok.json" 1048576 "$sha"
config "$work/cap.json" 64 "$sha"
config "$work/bad.json" 1048576 0000000000000000000000000000000000000000000000000000000000000000
run clean-exit "$work/ok.json" 'printf "%s\n" "{\"type\":\"system\"}"; exit 3'
run actor-signalled "$work/ok.json" 'printf "%s\n" "{\"type\":\"system\"}"; kill -TERM $$'
run unclean "$work/cap.json" 'printf "%s\n" "{\"type\":\"system\"}"; sleep 1'
run refused "$work/bad.json" 'true'
d="$work/cancelled"; mkdir -p "$d"
SWE_LAB_SUPERVISOR_BASE_URL=http://127.0.0.1:9/v1 "$bin" run --config "$work/ok.json" --actor-prompt "$work/prompt.json" \
  --actor-event-log "$d/events.jsonl" --supervisor-log "$d/supervisor.jsonl" --summary "$d/summary.json" \
  --actor-stderr "$d/actor.stderr" -- sh -c 'printf "%s\n" "{\"type\":\"system\"}"; sleep 30' >"$d/wrapper.out" 2>"$d/wrapper.err" &
pid=$!
sleep 1
kill -TERM $pid
wait $pid || echo "cancelled: exit $?"
cp "$work/cancelled/summary.json" "$out/cancelled.json"
for f in "$out"/*.json; do echo "== $f"; cat "$f"; done
