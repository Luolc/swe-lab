"""The contrast arm: stop a session mid-turn, then `--resume` it with a message.

This is the alternative the stream-json channel is being compared against. It is
run here only to see, in *this* harness and on *this* build, which records the
stop+resume path leaves behind — so the artifact diff has both sides.

Usage: python resume_control.py <out-dir> [workdir]
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import time
import uuid

import driver
import transcripts


def launch(
    session_args: list[str],
    out_dir: pathlib.Path,
    tag: str,
    workdir: pathlib.Path,
):
  argv = [
      "claude",
      "-p",
      "--input-format",
      "stream-json",
      "--output-format",
      "stream-json",
      "--verbose",
      "--model",
      "sonnet",
      "--dangerously-skip-permissions",
      *session_args,
  ]
  return driver.Run(out_dir / tag, argv, workdir), argv


def main() -> int:
  out_dir = pathlib.Path(sys.argv[1])
  (out_dir / "phase1").mkdir(parents=True, exist_ok=True)
  (out_dir / "phase2").mkdir(parents=True, exist_ok=True)
  workdir = driver.resolve_workdir(
      sys.argv[2] if len(sys.argv) > 2 else os.environ.get("STREAMJSON_WORKDIR")
  )
  session_id = str(uuid.uuid4())

  # Phase 1: start the task, then end the process *and its 30 s sleep child*
  # while that sleep is still running. `end()` signals the whole process group;
  # killing the parent alone would leave the sleep running into the next run.
  run, argv1 = launch(["--session-id", session_id], out_dir, "phase1", workdir)
  run.send(driver.TASK)
  hit = run.wait_for(driver.is_bash_sleep, timeout=180)
  time.sleep(3)
  code1 = run.end()

  time.sleep(2)

  # Phase 2: resume that session and deliver the correction.
  run2, argv2 = launch(["--resume", session_id], out_dir, "phase2", workdir)
  run2.send(driver.CORRECTION)
  _ = run2.wait_for(driver.is_result, timeout=240)
  run2.close_stdin()
  code2 = run2.finish(timeout=120)

  meta = {
      "scenario": "resume-control",
      "session_id": session_id,
      "argv_phase1": argv1,
      "argv_phase2": argv2,
      "killed_during_bash_sleep": hit is not None,
      "workdir": str(workdir),
      "exit_code_phase1": code1,
      "exit_code_phase2": code2,
      "timeline": ["phase1 killed mid tool call", "phase2 resumed with CORRECTION"],
  }
  _ = (out_dir / "meta.json").write_text(json.dumps(meta, indent=2))
  _ = transcripts.snapshot(out_dir)
  print(json.dumps(meta, indent=2))

  body, source = transcripts.load(out_dir)
  print(f"\nTRANSCRIPT (source: {source})")
  for artifact in driver_artifacts():
    print(f"  {artifact!r}: {artifact in body}")
  return 0


def driver_artifacts() -> tuple[str, ...]:
  return (
      "<system-reminder>",
      "Continue from where you left off.",
      "No response requested.",
  )


if __name__ == "__main__":
  raise SystemExit(main())
