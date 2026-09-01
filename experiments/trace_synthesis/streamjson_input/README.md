# Experiment: `claude --input-format stream-json` as a correction channel

**Question.** Can a user-role correction be delivered into a *live* headless
Claude Code session without adding anything to the conversation? The premise
this experiment was commissioned under is that the alternative — stop the
session, inject, `--resume` — adds three records (`<system-reminder>`,
`"Continue from where you left off."`, `"No response requested."`), which is
disqualifying for SFT. **That premise is taken as given, not measured here**:
the `resume-control` arm is a *positive control* whose job is to show the
detector fires, and it observed two of the three, once, with a SIGKILL trigger
and no wire capture (`REPORT.md` §3.3).

**Findings:** [`REPORT.md`](REPORT.md). Read the verdict block first.

## Design

One `claude -p --input-format stream-json --output-format stream-json --verbose`
process per run, stdin held open, one NDJSON `user` object per line. Each arm
differs only in **when** the correction is written and **what provenance fields**
it carries; task text, correction text, model and cwd are held fixed.

Injection moments are **event-triggered**: the driver waits for the actual
`tool_use` / `result` event on stdout rather than sleeping, so "mid-turn" means
the tool call was demonstrably in flight.

Three observation surfaces, all captured for every run:

- **stdout** — the agent's `stream-json` events (`runs/<name>/events.jsonl`,
  each line wrapped with a wall-clock stamp and a monotonic offset).
- **the session transcript** — copied out of the host-private, mutable file
  under `~/.claude/projects/` at the end of each run (`transcripts.py`).

**What is committed is not the raw capture.** A transcript and a proxy log carry
operator-home paths and the operator's global `CLAUDE.md`, which names them and
carries their email; `AGENTS.md` says to redact operator PII in any trace record
and never to commit raw trace records. So the three raw files are **gitignored**
and `evidence.py` builds `runs/<name>/evidence.json` from them: record shapes,
roles, provenance fields, wire `<system-reminder>` counts, the artifact greps
(computed on the raw text at build time) and short quotations — every string
redacted, the whole artifact re-scanned for home paths, home *slugs*, the
operator's git identity and credential shapes, and **nothing written when that
scan finds anything**. Each file records the sha256 of the raw inputs it was
built from, so a re-run can be matched to the published numbers.
- **the wire** — for `proxy-*` arms, the Go `cc-reverse-proxy` request/response
  log (`runs/<name>/proxy.jsonl`).

## Files

| file | what it is |
| --- | --- |
| `driver.py` | runs one scenario, logs stdin and stdout with timestamps |
| `run_proxy.sh` | same, with the **Go** `cc-reverse-proxy` recording the wire |
| `resume_control.py` | the contrast arm: kill mid-turn, then `--resume` and inject |
| `analyze.py` | events → the report's tables; runs both converters; greps the artifacts |
| `transcripts.py` | copies a run's session transcript into the run directory (gitignored; input to `evidence.py`) |
| `evidence.py` | builds and re-scans the committed `runs/*/evidence.json`; `--check` verifies without rebuilding |
| `check_process_group.py` | asserts the runner ends the agent's tool children, not just the agent; no API cost |
| `workdir/` | the agent's cwd fixture — created on demand, holds the `notes.txt` the task reads |
| `runs/<variant>/` | raw artifacts, one directory per run, never overwritten |

## How to run

Everything runs through `uv run`, which is how python is run in this repo — the
driver imports `swe_lab.process_group` and the analyzer imports the converters.
The agent's working directory defaults to the `workdir/` fixture beside these
files and is created on first use; `--workdir` / `STREAMJSON_WORKDIR` override
it, and an override that does not exist is a clear error.

```sh
uv run python driver.py control      runs/control
uv run python driver.py boundary     runs/boundary           # inject at a turn boundary
uv run python driver.py boundary     runs/boundary-replay --replay-user-messages
uv run python driver.py midturn      runs/midturn            # inject during a tool call
uv run python driver.py interrupt    runs/interrupt          # control_request, then inject
uv run python driver.py maxturns     runs/maxturns1 --max-turns 1
uv run python driver.py accept       runs/accept-human-r1 --provenance human
uv run python driver.py shouldquery  runs/shouldquery-r1
uv run python resume_control.py      runs/resume-control
./run_proxy.sh boundary              runs/proxy-boundary 20112

uv run python evidence.py runs/*                 # rebuild the committed evidence
uv run python evidence.py --check runs/*         # verify it, without raw captures
uv run python analyze.py --from-evidence runs/proxy-midturn   # tables, fresh checkout
uv run python analyze.py runs/boundary runs/control runs/resume-control  # needs raw
uv run python check_process_group.py
```

`run_proxy.sh` calls `driver.py` the same way. Every scenario snapshots its
transcript on exit; `uv run python transcripts.py runs/*` backfills a run
recorded before that existed.

## Safety

Proxy capture uses **only** the Go `cc-reverse-proxy` (12 redaction sites); the
two python ports in that project have none and leaked a live OAuth token once.

Two checks, over different domains, and the second exists because the first
cannot speak to it:

1. `redaction.unredacted_fields` / `unclassified_fields` /
   `publication_blockers` on every `proxy.jsonl` (→ `[]`). These classify the
   **envelope** — headers and `metadata.user_id`. `publication_blockers`' own
   docstring says an empty list is not evidence about the **bodies**, and here
   the bodies did carry operator PII.
2. `evidence.py`'s own scan over the artifact that is actually committed, for
   operator-home paths and slugs, the operator's git identity, and
   credential-shaped values. A build that trips it writes nothing.

Re-run both after any new capture:

```sh
uv run python -c "
from swe_lab.harnesses.claude_code.redaction import unredacted_fields
import pathlib; print(unredacted_fields(pathlib.Path('runs/<name>/proxy.jsonl').read_text()))"
uv run python evidence.py --check runs/*
```

Neither check is a proof of absence — both look for named classes. The reason
the raw captures are gitignored rather than scrubbed is that a filter is a
promise about what you thought of.
