"""Run the in-sandbox fold check. See PRE-REGISTRATION.md for the reading.

Reproduce (needs OPENROUTER_API_KEYS in the environment, and no other container
running on this machine):

    uv run python experiments/trace_synthesis/sandbox_fold_check/run_check.py
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import threading
import time
import uuid

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
BASELINE = (
    ROOT
    / "experiments/trace_synthesis/streamjson_input/runs/proxy-midturn/evidence.json"
)
EVIDENCE_PY = ROOT / "experiments/trace_synthesis/streamjson_input/evidence.py"
DRIVER_PY = ROOT / "experiments/trace_synthesis/streamjson_input/driver.py"

# The binaries come from the shipped helpers rather than a hardcoded cache
# path: the proxy's cache key is a digest of its Go source, so asking for it is
# what guarantees the check runs the binary the harness would have staged.

IMAGE = (
    "jefzda/sweap-images:qutebrowser.qutebrowser-qutebrowser__qutebrowser-"
    "9ed748effa8f3bcd804612d9291da017b514e12f-v363c8a7e5ccdf6968fc7ab84a2053ac780366"
)
CONTAINER = "swe-lab-sandbox-fold-check"
PORT = 20111
TARGET = "https://openrouter.ai/api"
WORKDIR = "/tmp/fold-check"
# Mirrors the shipped harness: the config dir is pinned so the image cannot
# inject agent instructions, and IS_SANDBOX signals the throwaway container to
# builds that otherwise refuse --dangerously-skip-permissions as root.
AGENT_HOME = "/agent-home"
OUT = pathlib.Path(
    os.environ.get("FOLD_CHECK_OUT", "~/dev/swe-lab-artifacts/sandbox_fold_check")
).expanduser()


def load(path: pathlib.Path, name: str):
  """Import a module by path so its own code is reused, not re-derived.

  Args:
    path: The module file.
    name: The name to register it under.

  Returns:
    The imported module.

  Raises:
    RuntimeError: If the module cannot be loaded.
  """
  spec = importlib.util.spec_from_file_location(name, path)
  if spec is None or spec.loader is None:
    raise RuntimeError(f"cannot load {path}")
  module = importlib.util.module_from_spec(spec)
  sys.modules[name] = module
  spec.loader.exec_module(module)
  return module


def run(*argv: str, check: bool = True, stdin: bytes | None = None) -> str:
  """Run a command and return its stdout.

  Args:
    *argv: The command.
    check: Raise on a non-zero exit.
    stdin: Bytes to feed on stdin.

  Returns:
    Captured stdout.
  """
  done = subprocess.run(argv, capture_output=True, input=stdin, check=check)
  return done.stdout.decode()


def log(message: str) -> None:
  """Print a timestamped line.

  Args:
    message: What happened.
  """
  print(f"{time.strftime('%FT%TZ', time.gmtime())} {message}", flush=True)


def main() -> int:
  """Run the check and write its artifacts.

  Returns:
    0 when the run completed (whatever the verdict), 1 when it did not.

  Raises:
    RuntimeError: If another container is already running.
  """
  evidence = load(EVIDENCE_PY, "streamjson_evidence")
  driver = load(DRIVER_PY, "streamjson_driver")

  from swe_lab.harnesses.claude_code.binary import ensure_claude_binary
  from swe_lab.harnesses.claude_code.proxy import ensure_proxy_binary

  claude_bin = pathlib.Path(str(ensure_claude_binary()))
  proxy_bin = pathlib.Path(str(ensure_proxy_binary()))
  log(f"claude: {claude_bin.name} proxy: {proxy_bin.parent.parent.name[:12]}…")

  baseline = json.loads(BASELINE.read_text())
  want = baseline["wire"]["messages"][-1]["text_digests"][0]
  log(f"baseline: len={want['len']} sha256={want['sha256'][:12]}…")

  running = run("docker", "ps", "-q").split()
  if running:
    raise RuntimeError(
        f"{len(running)} container(s) already running; this machine allows one"
    )

  OUT.mkdir(parents=True, exist_ok=True)
  # The pool is read and selected from *here*, by the code that already knows
  # how: no shell splitting, and the value never reaches a command line. It is
  # handed to the container by name (`docker exec -e ANTHROPIC_API_KEY`).
  supervisor = load(
      ROOT / "experiments/trace_synthesis/steered_rerun/supervisor.py",
      "steered_supervisor",
  )
  index, key = supervisor.openrouter_key(0)
  os.environ["ANTHROPIC_API_KEY"] = key
  log(f"actor credential: pool index {index}, {supervisor.key_fingerprint(key)}")

  _ = run("docker", "rm", "-f", CONTAINER, check=False)
  _ = run(
      "docker", "run", "-d", "--name", CONTAINER,
      "--label", "swe-lab-instance=sandbox-fold-check",
      # The instance images declare ENTRYPOINT ["/bin/bash"], so a bare
      # `sleep infinity` is read as the name of a script to run and the
      # container exits immediately.
      "--entrypoint", "/bin/sh", IMAGE, "-c", "sleep infinity",
  )
  log(f"container up: {CONTAINER}")
  try:
    _ = run("docker", "exec", CONTAINER, "mkdir", "-p", "/opt/claude-code",
            "/opt/cc-reverse-proxy", WORKDIR, f"{AGENT_HOME}/.claude")
    _ = run("docker", "cp", str(claude_bin), f"{CONTAINER}:/opt/claude-code/claude")
    _ = run("docker", "cp", str(proxy_bin), f"{CONTAINER}:/opt/cc-reverse-proxy/cc-reverse-proxy")
    _ = run("docker", "exec", CONTAINER, "chmod", "+x",
            "/opt/claude-code/claude", "/opt/cc-reverse-proxy/cc-reverse-proxy")
    _ = run("docker", "exec", "-i", CONTAINER, "sh", "-c",
            f"cat > {WORKDIR}/notes.txt", stdin=driver.NOTES_TEXT.encode())
    log("binaries and fixture staged")

    _ = run(
        "docker", "exec", "-d", CONTAINER, "sh", "-c",
        f"/opt/cc-reverse-proxy/cc-reverse-proxy --target {TARGET}"
        f" --output {WORKDIR}/proxy.jsonl --port {PORT}"
        f" > {WORKDIR}/proxy.log 2>&1",
    )
    for _ in range(60):
      probe = run("docker", "exec", CONTAINER, "sh", "-c",
                  f"grep -c 'Reverse proxy:' {WORKDIR}/proxy.log 2>/dev/null || true",
                  check=False).strip()
      if probe not in ("", "0"):
        break
      time.sleep(0.5)
    log(f"proxy listening probe: {probe!r}")

    session = str(uuid.uuid4())
    argv = [
        "docker", "exec", "-i",
        "-e", "ANTHROPIC_API_KEY",
        "-e", f"ANTHROPIC_BASE_URL=http://127.0.0.1:{PORT}",
        "-e", f"CLAUDE_CONFIG_DIR={AGENT_HOME}/.claude",
        "-e", "IS_SANDBOX=1",
        "-w", WORKDIR, CONTAINER,
        "/opt/claude-code/claude", "-p",
        "--input-format", "stream-json", "--output-format", "stream-json",
        "--verbose", "--model", "sonnet", "--session-id", session,
        "--dangerously-skip-permissions",
    ]
    proc = subprocess.Popen(
        argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, env=os.environ.copy(),
    )
    events: list[dict] = []

    def pump() -> None:
      """Collect stdout events."""
      assert proc.stdout is not None
      for line in proc.stdout:
        try:
          events.append(json.loads(line))
        except json.JSONDecodeError:
          continue

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()

    def send(text: str) -> None:
      """Write one stream-json user message.

      Args:
        text: The message.
      """
      assert proc.stdin is not None
      message = {
          "type": "user",
          "message": {"role": "user", "content": [{"type": "text", "text": text}]},
      }
      proc.stdin.write((json.dumps(message) + "\n").encode())
      proc.stdin.flush()

    def saw(predicate, timeout: float) -> bool:
      """Wait until an event satisfies a predicate.

      Args:
        predicate: Tested against each event.
        timeout: Seconds.

      Returns:
        Whether it happened.
      """
      deadline = time.time() + timeout
      while time.time() < deadline:
        if any(predicate(e) for e in list(events)):
          return True
        time.sleep(0.5)
      return False

    def is_sleep_tool_use(event: dict) -> bool:
      """Whether the event is the Bash sleep call.

      Args:
        event: A stream-json event.

      Returns:
        Whether it matches.
      """
      return "time.sleep(30)" in json.dumps(event) and "tool_use" in json.dumps(event)

    send(driver.TASK)
    log("sent task")
    hit = saw(is_sleep_tool_use, timeout=180)
    log(f"saw sleep tool_use: {hit}")
    time.sleep(2)
    send(driver.CORRECTION)
    log("sent correction mid tool call")
    done = saw(lambda e: e.get("type") == "result", timeout=300)
    log(f"saw result: {done}")
    if proc.stdin is not None:
      proc.stdin.close()
    try:
      _ = proc.wait(timeout=120)
    except subprocess.TimeoutExpired:
      proc.kill()
    reader.join(timeout=10)

    raw = run("docker", "exec", CONTAINER, "sh", "-c",
              f"cat {WORKDIR}/proxy.jsonl 2>/dev/null || true", check=False)
    _ = (OUT / "events.json").write_text(json.dumps(events, indent=2))
    stderr_text = proc.stderr.read().decode() if proc.stderr else ""
    _ = (OUT / "claude.stderr.log").write_text(stderr_text)
    if stderr_text.strip():
      log(f"agent stderr: {stderr_text.strip().splitlines()[0][:120]}")
    proxy_log = run("docker", "exec", CONTAINER, "sh", "-c",
                    f"cat {WORKDIR}/proxy.log 2>/dev/null || true", check=False)
    _ = (OUT / "proxy.log").write_text(proxy_log)
    wire = evidence._wire(raw) if raw.strip() else {}  # noqa: SLF001
    got = None
    if wire.get("messages"):
      last = wire["messages"][-1]
      if last.get("text_digests"):
        got = last["text_digests"][0]

    if got is None:
      verdict = "INCOMPLETE"
    elif got["len"] == want["len"] and got["sha256"] == want["sha256"]:
      verdict = "MATCH"
    else:
      verdict = "MISMATCH"

    summary = {
        "verdict": verdict,
        "expected": want,
        "observed": got,
        "wire_counts": {
            k: wire.get(k) for k in
            ("api_calls", "agent_loop_calls", "excluded_side_calls",
             "selected_record_index", "system_reminder_blocks")
        },
        "last_message_role": (wire.get("messages") or [{}])[-1].get("role"),
        "claude_version": run("docker", "exec", CONTAINER,
                              "/opt/claude-code/claude", "--version",
                              check=False).strip(),
        "image": IMAGE,
        "upstream": TARGET,
        "delivered": bool(done),
        "saw_sleep_tool_use": bool(hit),
        "events": len(events),
    }
    _ = (OUT / "summary.json").write_text(json.dumps(summary, indent=2))
    _ = (OUT / "proxy.jsonl").write_text(raw)
    log(json.dumps(summary["wire_counts"]))
    log(f"VERDICT: {verdict}")
    print(json.dumps(summary, indent=2))
    return 0
  finally:
    _ = run("docker", "rm", "-f", CONTAINER, check=False)
    log("container removed")


if __name__ == "__main__":
  raise SystemExit(main())
