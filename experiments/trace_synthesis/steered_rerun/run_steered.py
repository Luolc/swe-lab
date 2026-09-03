#!/usr/bin/env python3
"""Drive one steered (or unsteered) rollout of a real instance, and freeze it.

Task 01 step 5. A blind actor runs the same instance as the frozen phase-A
failure; a ``PostToolUse`` hook fires at every tool boundary, asks the
host-side Supervisor, and appends a tagged hint when the actor is off track.
Then the shipped ``unit_test`` entry grades what the actor produced, so the
verdict is the same verdict phase A was measured with.

**Scratch, not production.** Nothing here is wired into a shipped workflow
definition. The rollout entry is the shipped ``ROLLOUT`` one with its harness
swapped for a subclass that mounts two extra files and adds ``--settings`` to
the agent's argv; the grading entry is the shipped ``UNIT_TEST`` unchanged.
Tasks 03 / 05 / 09 are what turn any of this into something the repo keeps.

**``capture="proxy"`` is required, not an arm.** The actor is served through
OpenRouter, and two things ``cc-reverse-proxy`` injects on that path are things
Claude Code does not send for itself: it mirrors ``Anthropic-Beta`` to
``X-Anthropic-Beta`` (the header OpenRouter actually reads, without which
interleaved thinking simply does not happen), and it pins a provider preference
with ``require_parameters: true`` (without which OpenRouter may route to a
provider that quietly drops ``thinking``). Both failures are **silent**: the run
completes, the trace looks whole, and the model's reasoning is not what the
spec says it is — and that reasoning is the entire value of the trace
([spec §10](../../../docs/trace-synthesis/spec.md#10-what-is-measured-about-hooks)).

So a ``stream``-captured direct-to-OpenRouter run is **not another capture of
the same configuration**; it is a different model configuration, and its numbers
are not comparable to a proxied run's. ``--capture stream`` stays available for
a non-OpenRouter path and warns otherwise.

Usage::

  direnv exec . uv run python experiments/trace_synthesis/steered_rerun/run_steered.py \\
      --label steered-stream --instance <id> --rollout-id 10 --capture stream
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
import hashlib
import importlib.util
import json
import os
import pathlib
import shlex
import shutil
import subprocess
import sys
import time
from typing import Any, override

from etils import epath

from swe_lab.cli.persist_wiring import run_store, run_ts
from swe_lab.datasets.loader import load_dataset
from swe_lab.harnesses.claude_code import ClaudeCodeHarness
from swe_lab.harnesses.claude_code.constants import (
    AGENT_SCRIPT_NAME,
    BINARY_AT,
    UNATTENDED_DENIED_TOOLS,
)
from swe_lab.paths import cache_root, find_repo_root
from swe_lab.rollout import CodingAgentTask
from swe_lab.sandbox import Inline, Mount, Mounts
from swe_lab.workflow import Workflow, WorkflowEntry
from swe_lab.workflow.definitions import ROLLOUT, ROLLOUT_KEY, UNIT_TEST

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import supervisor as supervisor_module  # noqa: E402 — needs the path above

_HERE = pathlib.Path(__file__).resolve().parent
# The actor is authenticated through OpenRouter, not the subscription OAuth
# token: one credential for the actor and the Supervisor both, and a model id
# that names the provider it is served from. `https://openrouter.ai/api` is the
# base URL because Claude Code appends `/v1/messages` itself and OpenRouter's
# messages endpoint carries the extra `/api` segment.
ACTOR_BASE_URL = "https://openrouter.ai/api"
ACTOR_MODEL = "anthropic/claude-sonnet-5"
API_KEY_ENV = "ANTHROPIC_API_KEY"

# Where the container sees the bind-mounted workspace (`DockerHostSandbox`'s
# `mount_at` default). The hook is spawned by the agent, not by a `docker
# exec`, so it never sees `$SANDBOX_WORKSPACE` and its settings entry has to
# name an absolute path.
MOUNT_AT = "/workspace"
HOOK_NAME = "steer_hook.py"
SETTINGS_NAME = "steer_settings.json"
HOOK_LOG_NAME = "steer_hook.local.jsonl"

# The hook and the Supervisor exchange files here. Staged as a mount — a file
# inside it, since a mount names a file — so the *host* user owns the
# directory: the container is root and can write anywhere, the host cannot
# write into a root-created directory, and only one of those two is fixable.
IO_KEEP_NAME = f"{supervisor_module.IO_DIR}/.keep"

# The hook waits on a model call; the agent's own turn waits on the hook. 150 s
# is comfortably above the Supervisor's 75 s model deadline, and a hook that
# blows through it fails open (documented) — which the host-side log then shows
# as a judgement with no matching applied line.
HOOK_TIMEOUT_S = 150


def _redact_proxy_log() -> Any:
  """Return task 02's proxy-log redactor, loaded from its driver by path.

  Reused rather than reimplemented: that function is what
  ``tests/test_injection_shape_redaction.py`` pins, in both directions, and a
  second copy of a redaction rule is a second place for it to go stale. The
  driver calls ``main()`` at import (it is a script, not a package), so the
  load fakes an inert argv exactly as that test does.

  Returns:
    The ``redact_proxy_log`` function.
  """
  path = (
      pathlib.Path(__file__).resolve().parents[1]
      / "injection_shape"
      / "run_experiment.py"
  )
  spec = importlib.util.spec_from_file_location("injection_shape_driver", path)
  assert spec is not None and spec.loader is not None
  module = importlib.util.module_from_spec(spec)
  argv = sys.argv
  sys.argv = ["run_experiment.py", "--list"]
  try:
    spec.loader.exec_module(module)
  finally:
    sys.argv = argv
  return module.redact_proxy_log


@dataclass(frozen=True)
class SteeredClaudeCodeHarness(ClaudeCodeHarness):
  """The shipped harness plus a per-run hook, mounted into the workspace.

  Two additions and nothing else: the hook program and its settings file are
  staged beside the invocation script, and the script's ``claude`` argv gains
  ``--settings``. Everything the shipped harness decides — the pinned config
  dir, the denied tools, the capture wiring, the exit-code file — is inherited
  untouched, so the steered run differs from the baseline in exactly the hook.

  Attributes:
    hook_source: The hook program's text, staged executable.
    settings_json: The ``--settings`` payload wiring that hook to the tool
      events. Built by the driver, which is what knows the session id.
  """

  hook_source: str = ""
  settings_json: str = ""

  @override
  def mounts(self, workdir: str) -> Mounts:
    """Stage the shipped mounts, plus the hook and its settings.

    Args:
      workdir: The repo path the invocation script ``cd``s into.

    Returns:
      The shipped staging set with the two hook files added and the invocation
      script rewritten to point at them.

    Raises:
      RuntimeError: If the shipped script no longer has the argv shape this
        patches — better a refusal than a silently unhooked run.
    """
    staged = dict(super().mounts(workdir))
    script = self._invocation_script(workdir)
    settings = f'"$SANDBOX_WORKSPACE"/{SETTINGS_NAME}'
    marker = f"{shlex.quote(BINARY_AT)} -p "
    denied = ",".join(UNATTENDED_DENIED_TOOLS)
    if marker not in script or f"--disallowedTools {denied}" not in script:
      raise RuntimeError(
          "invocation script no longer has the argv shape this patches; the"
          " --settings and subagent patches would be silently dropped"
      )
    script = script.replace(marker, f"{marker}--settings {settings} ", 1)
    # `Task` on top of the shipped denylist: a subagent's conversation is a
    # second thread, and the proxy converter keeps only the last one (task 02).
    # Nothing here needs one, so the whole class of confound goes away for the
    # price of one argument.
    script = script.replace(
        f"--disallowedTools {denied}", f"--disallowedTools {denied},Task", 1
    )
    staged[AGENT_SCRIPT_NAME] = Mount(Inline(script.encode()), executable=True)
    staged[HOOK_NAME] = Mount(Inline(self.hook_source.encode()), executable=True)
    staged[SETTINGS_NAME] = Mount(Inline(self.settings_json.encode()))
    staged[IO_KEEP_NAME] = Mount(Inline(b""))
    return staged


def hook_settings(*, session: str) -> str:
  """Build the ``--settings`` payload for one steered run.

  Both tool-completion events are hooked. Only ``PostToolUse`` can carry
  ``updatedToolOutput`` (task 02, off the shipped binary's own schema), so
  ``PostToolUseFailure`` is there to keep the Supervisor's belief state
  complete across a failing call — the moment the actor is most likely to be
  spinning and least reachable.

  Args:
    session: This run's session id.

  Returns:
    The settings JSON.
  """
  command = " ".join(
      shlex.quote(part)
      for part in (
          "python3",
          f"{MOUNT_AT}/{HOOK_NAME}",
          f"{MOUNT_AT}/{supervisor_module.IO_DIR}",
          session,
          f"{MOUNT_AT}/{HOOK_LOG_NAME}",
      )
  )
  entry = [{
      "matcher": "",
      "hooks": [{"type": "command", "command": command, "timeout": HOOK_TIMEOUT_S}],
  }]
  return json.dumps(
      {"hooks": {"PostToolUse": entry, "PostToolUseFailure": entry}}, indent=2
  )


def freeze(
    source: epath.Path, destination: pathlib.Path, provenance: dict[str, object]
) -> None:
  """Copy a finished run out of ``.cache/`` and record what produced it.

  ``run.py`` rmtree's ``.cache/runs/<workflow>/<instance>`` at the start of
  every non-resumed run, so the next rollout of this instance destroys this
  one. The destination is deliberately outside any git worktree: ``git
  worktree remove`` deletes gitignored content without warning, and that is
  how the previous round's frozen tree was lost.

  Args:
    source: The run's cache directory.
    destination: Where to freeze it.
    provenance: Facts the copied tree does not carry.

  Raises:
    FileExistsError: If the destination already exists.
  """
  if destination.exists():
    raise FileExistsError(f"{destination} already exists; not overwriting")
  destination.parent.mkdir(parents=True, exist_ok=True)
  shutil.copytree(source, destination)
  _ = (destination / "PROVENANCE.json").write_text(
      json.dumps(provenance, indent=2, default=str) + "\n"
  )


def main() -> None:
  """Run one arm end to end: supervisor up, rollout, grade, freeze."""
  parser = argparse.ArgumentParser(description=__doc__)
  _ = parser.add_argument("--label", required=True, help="names the frozen tree")
  _ = parser.add_argument("--instance", required=True, help="the instance id")
  _ = parser.add_argument("--rollout-id", type=int, required=True)
  _ = parser.add_argument("--capture", choices=("stream", "proxy"), default="proxy")
  _ = parser.add_argument(
      "--steer",
      action=argparse.BooleanOptionalAction,
      default=True,
      help="wire the hook at all; --no-steer is the control arm",
  )
  _ = parser.add_argument("--supervisor-model", default="anthropic/claude-opus-5")
  _ = parser.add_argument("--max-hints", type=int, default=8)
  _ = parser.add_argument(
      "--concurrency",
      type=int,
      default=1,
      help=(
          "how many rollouts share the box right now. Recorded, not enforced:"
          " a wall clock is meaningless without it"
      ),
  )
  _ = parser.add_argument(
      "--guidebook",
      help=(
          "the Oracle's guidebook for *this* instance. Required with --steer:"
          " a guidebook written for another instance is not a weaker"
          " supervisor, it is a supervisor judging against the wrong task"
      ),
  )
  _ = parser.add_argument(
      "--key-index",
      type=int,
      default=0,
      help=(
          "which key of the pool to prefer. Every key is its own account with"
          " its own balance and its own rate limit, so parallel runs take"
          " different ones"
      ),
  )
  _ = parser.add_argument(
      "--frozen-root",
      default="~/dev/swe-lab-artifacts/trace_synthesis",
      help="outside every worktree, on purpose",
  )
  args = parser.parse_args()

  # The config guard, paired with the result check at the end of the run: one
  # refuses a configuration that cannot produce interleaved thinking, the other
  # verifies the trace that came back actually carries it. Refusing rather than
  # warning, because the failure it prevents is silent — a warning scrolls past
  # and the degraded trace looks exactly like a good one.
  if "openrouter" in ACTOR_BASE_URL and args.capture != "proxy":
    raise SystemExit(
        f"refusing to run: the actor is served through {ACTOR_BASE_URL} and"
        f" --capture is {args.capture!r}. On that path only cc-reverse-proxy"
        " mirrors Anthropic-Beta to X-Anthropic-Beta and pins"
        " require_parameters, and without them interleaved thinking silently"
        " does not happen. Use --capture proxy."
    )
  if args.steer and not args.guidebook:
    raise SystemExit(
        "refusing to run: --steer needs --guidebook. The Supervisor judges"
        " every boundary against it, so the wrong one silently produces"
        " confident hints about a different task."
    )

  # The actor and the Supervisor share one credential and one provider. Set in
  # this process only; `pass_env` hands it to the container by name, so the
  # value never appears in a command line.
  key_index, key = supervisor_module.openrouter_key(args.key_index)
  os.environ[API_KEY_ENV] = key
  key_name = supervisor_module.key_fingerprint(key)
  credits_before = supervisor_module.key_credits(key)
  root = find_repo_root()
  workflow_name = "rollout_and_unit_test"
  output_dir = cache_root(root) / "runs" / workflow_name / args.instance
  output_dir.rmtree(missing_ok=True)

  frozen = (
      pathlib.Path(args.frozen_root).expanduser()
      / f"{args.label}-rollout-{args.rollout_id}"
  )
  if frozen.exists():
    raise SystemExit(f"{frozen} already exists; pick another --label")
  runs = _HERE / "runs" / args.label
  runs.mkdir(parents=True, exist_ok=True)

  session = f"{args.label}-r{args.rollout_id}"
  hint_log = runs / "hint_log.jsonl"
  watcher = None
  if args.steer:
    watcher = supervisor_module.Watcher(
        supervisor_module.Supervisor(
            guidebook=pathlib.Path(args.guidebook).read_text(),
            log_path=hint_log,
            api_key=os.environ[API_KEY_ENV],
            model=args.supervisor_model,
            max_hints=args.max_hints,
        ),
        pathlib.Path(output_dir),
    )
    watcher.start()

  # `bare=False` is not an oversight. `--bare` is minimal mode and it disables
  # hooks outright — measured 2026-09-01, the same probe with and without it:
  # with `--bare` the `--settings` hook never fires and no hint reaches the
  # actor. Hooks *are* the mechanism, so bare mode cannot be used here; the
  # subagent suppression it was wanted for is bought with `--disallowedTools`
  # instead (see the harness above).
  #
  # `proxy_target` is where the upstream is chosen now. The proxy runs in the
  # sandbox, so there is no port to hand out and nothing host-side to order
  # against the container; what remains is the one thing that was never
  # cosmetic — `cc-reverse-proxy` gates its OpenRouter behaviour on the target
  # string (`isOpenRouter = strings.Contains(targetURL, "openrouter.ai")`), so
  # the Anthropic default would forward this run's OpenRouter key to the wrong
  # API *and* silently drop the `X-Anthropic-Beta` mirroring and the provider
  # injection this capture exists for.
  harness_kwargs: dict[str, Any] = {
      "model": ACTOR_MODEL,
      "bare": False,
      "capture": args.capture,
      "proxy_target": ACTOR_BASE_URL,
  }
  # Both arms run the *same* harness subclass and therefore the same argv: the
  # control's settings file simply declares no hooks. A control that also
  # differed in `--settings` and `--disallowedTools` would confound the one
  # variable this round is trying to isolate.
  harness = SteeredClaudeCodeHarness(
      hook_source=(_HERE / HOOK_NAME).read_text(),
      settings_json=hook_settings(session=session) if args.steer else "{}\n",
      **harness_kwargs,
  )

  rollout = ROLLOUT[0]
  entries = (
      WorkflowEntry(
          ROLLOUT_KEY,
          # No `env` here: the invocation script sources the caller's env
          # *before* it exports the in-sandbox proxy's URL, deliberately, so an
          # `ANTHROPIC_BASE_URL` passed this way would be overwritten anyway —
          # and pointing the agent past the proxy is the one thing this run
          # must not do. The key is a secret and still travels by *name*
          # through `pass_env`, so its value never reaches a command line, a
          # process list, or a log.
          CodingAgentTask(harness=harness),
          timeout=rollout.timeout,
          sandbox=replace(rollout.sandbox, pass_env=(API_KEY_ENV,)),
      ),
      *UNIT_TEST,
  )

  instance = load_dataset("swebench_pro").require(args.instance)
  started = time.monotonic()
  started_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
  try:
    outcome = Workflow(
        store=run_store(root, persist_to_t1=False, scratch=output_dir),
        sweep_id="adhoc",
        rollout_id=args.rollout_id,
        entries=entries,
    ).execute(
        instance,
        output_dir=output_dir,
        run_ts=run_ts(),
        resume=False,
        extra_record=instance.run_provenance(),
    )
  finally:
    if watcher is not None:
      watcher.shutdown()
  wall = round(time.monotonic() - started, 1)

  # `cc-reverse-proxy` now masks sensitive headers *as it writes* each
  # exchange (ADR-0012 §4), so this pass is no longer what makes the capture
  # safe — it is the second belt, kept because the proxy is an external,
  # separately versioned binary and "the build we ran redacts" is exactly the
  # assumption that stops holding without anyone noticing. `redact_record`
  # masks in place and is idempotent, so re-running it over an
  # already-redacted log is a no-op.
  redacted = sorted(pathlib.Path(output_dir).rglob("*proxy*.jsonl"))
  if redacted:
    redact = _redact_proxy_log()
    for path in redacted:
      redact(pathlib.Path(path))

  resolved = _resolved(outcome)
  summary = {
      "label": args.label,
      "session": session,
      "instance_id": args.instance,
      "rollout_id": args.rollout_id,
      "capture": args.capture,
      "steered": args.steer,
      "actor_model": ACTOR_MODEL,
      "actor_base_url": ACTOR_BASE_URL,
      "auth": "openrouter api key",
      # Per key, because there is nothing else to be per: the pool is 25
      # separate accounts, not 25 doors onto one balance (measured 2026-09-01).
      "key_index": key_index,
      "key": key_name,
      "credits_before": credits_before,
      "credits_after": supervisor_module.key_credits(key),
      "bare": False,
      "supervisor_model": args.supervisor_model if args.steer else None,
      "guidebook": args.guidebook if args.steer else None,
      "concurrency": args.concurrency,
      "started": started_at,
      "wall_s": wall,
      "entries": {
          entry.key: {
              "status": entry.status.value,
              "attempts": entry.run.attempts if entry.run else 0,
              "metrics": dict(entry.run.record.metrics) if entry.run else {},
          }
          for entry in outcome.entries
      },
      "resolved": resolved,
      "hints_emitted": _hints_emitted(hint_log, session) if args.steer else 0,
      "reasoning": _reasoning_blocks(pathlib.Path(output_dir)),
      "proxy_logs_redacted": [
          str(path.relative_to(pathlib.Path(output_dir))) for path in redacted
      ],
  }
  # One file per rollout, not one per label: a harvest samples the same label
  # several times, and a single `summary.json` means sample N erases the
  # evidence for sample N-1 — including which key paid for it.
  _ = (runs / f"summary-r{args.rollout_id}.json").write_text(
      json.dumps(summary, indent=2) + "\n"
  )

  freeze(
      output_dir,
      frozen,
      {
          **summary,
          "workflow": workflow_name,
          "source_dir": str(output_dir),
          "frozen_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
          "git_commit": subprocess.run(
              ["git", "-C", str(root), "rev-parse", "HEAD"],
              capture_output=True,
              text=True,
              check=False,
          ).stdout.strip(),
          "hook_settings_sha256": hashlib.sha256(
              harness.settings_json.encode()
          ).hexdigest(),
      },
  )
  print(json.dumps(summary, indent=2))
  if not summary["reasoning"]["signed"]:
    print(
        "WARNING: the trace carries no signed reasoning blocks — interleaved"
        " thinking did not survive. Treat this run as degraded.",
        file=sys.stderr,
    )
  raise SystemExit(0 if resolved else 2)


def _reasoning_blocks(output_dir: pathlib.Path) -> dict[str, int]:
  """Count the actor's reasoning blocks, and how many carry a signature.

  The cheap mechanical guard against the silent degradation above: interleaved
  thinking either reached the trace or it did not, and a run that lost it looks
  entirely healthy otherwise. Anyone who later changes the base URL, the
  provider preference or the capture mode makes this number go to zero, and a
  zero is visible in the summary rather than discovered in the training data.

  Args:
    output_dir: The run directory.

  Returns:
    ``total`` reasoning blocks and how many are ``signed``.
  """
  total = signed = 0
  for path in sorted(output_dir.rglob("conversation.json")):
    conversation = json.loads(path.read_text())
    for message in conversation.get("messages", []):
      for block in message.get("content", []):
        if block.get("type") == "reasoning":
          total += 1
          signed += bool(block.get("signature"))
    break
  return {"total": total, "signed": signed}


def _resolved(outcome: Any) -> bool:
  """Read the graded verdict off the run's own metrics.

  Args:
    outcome: The workflow outcome.

  Returns:
    Whether the evaluation method reported the patch as resolving.
  """
  for entry in outcome.entries:
    if entry.run is None:
      continue
    for name, value in entry.run.record.metrics.items():
      if name.endswith(".resolved"):
        return bool(value)
  return False


def _hints_emitted(log_path: pathlib.Path, session: str) -> int:
  """Count the hints the Supervisor emitted for one session.

  Args:
    log_path: The host-side hint log.
    session: The session id to count.

  Returns:
    How many judgements emitted a hint.
  """
  if not log_path.is_file():
    return 0
  return sum(
      1
      for line in log_path.read_text().splitlines()
      if line.strip()
      and (record := json.loads(line)).get("session") == session
      and record.get("hint_emitted")
  )


if __name__ == "__main__":
  main()
