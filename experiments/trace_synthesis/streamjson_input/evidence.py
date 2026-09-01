"""Build the committed, redacted evidence artifact for one run.

**Raw captures do not go in the repo.** A Claude Code transcript, its
`stream-json` stdout and a proxy log all carry host state that has nothing to do
with this experiment — absolute operator-home paths, the operator's global
`CLAUDE.md` (which names them and carries their email), the machine's skill and
agent listings. `AGENTS.md` says to redact operator PII in any trace record and
never to commit raw trace records.

So the raw files stay in the run directory and are **gitignored**; what is
committed is `evidence.json`, built here: the record shapes, roles, provenance
fields, marker counts and short quotations that `REPORT.md`'s tables actually
assert, and nothing else. Every emitted string goes through :func:`redact`, and
:func:`blockers` re-scans the finished artifact — a build that trips it writes
nothing.

Each source file is recorded by **sha256**, so a re-run can be checked against
the evidence that was published from it.

    uv run python evidence.py runs/*        # build (and verify) every run
    uv run python evidence.py --check runs/*  # verify committed artifacts only
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import transcripts

EVIDENCE_NAME = "evidence.json"

# Raw capture file names, in the order the evidence records them.
RAW_NAMES = ("events.jsonl", "transcript.jsonl", "proxy.jsonl", "tui.log")

# The three records the stop+resume path adds; grepped on the *raw* text so the
# published booleans mean what they say even though the raw text is not here.
ARTIFACTS = (
    "<system-reminder>",
    "Continue from where you left off.",
    "No response requested.",
)

# A `<system-reminder>` longer than this is session startup context (the
# operator's CLAUDE.md, the agent listing) and is elided to its size. The ones
# the report quotes — the mid-turn interjection wrapper — are far shorter.
REMINDER_KEEP_BYTES = 900
TEXT_KEEP_CHARS = 300
TOOL_RESULT_KEEP_CHARS = 200

# **Only text the report quotes is kept.** Everything else is reduced to its
# length: the shapes (which records, which roles, which block types, which
# provenance fields, how many `<system-reminder>` blocks) are what the tables
# are built from, and the message bodies are not evidence for anything asserted.
# This is what keeps the committed artifact the report's evidence rather than a
# derived trace corpus.
CITED = (
    "Correction from the operator",
    "One more thing: also append",
    "Now give me your final answer",
    "BANANA",
    "MANGO",
    "Continue from where you left off.",
    "No response requested.",
    "[Request interrupted by user for tool use]",
    "The user doesn't want to proceed",
    "[MESSAGE FROM NON-USER SOURCE",
    "The user sent a new message while you were working",
    "the secret color is teal",
    "slept",
)

_HOME = str(pathlib.Path.home())
# Claude Code names its per-project state directories after the cwd with the
# separators flattened, so the home path also appears as a *slug*
# (`-home-<user>-...`) that a plain path replacement does not see.
_HOME_SLUG = _HOME.replace("/", "-")


def _git_identity() -> list[str]:
  """Return the operator's git name and email, for the redaction deny-list.

  Read locally to *remove* these strings from what gets committed; they are
  never written into the artifact.
  """
  values = []
  for key in ("user.name", "user.email"):
    result = subprocess.run(
        ["git", "config", "--get", key],
        capture_output=True,
        text=True,
        check=False,
    )
    value = result.stdout.strip()
    if value:
      values.append(value)
  return values


_IDENTITY = _git_identity()

# Credential shapes. Not a scanner — gitleaks is — just the last line of defence
# for this artifact, so a build cannot quietly publish one.
_CREDENTIAL_PATTERNS = (
    re.compile(r"sk-ant-[A-Za-z0-9_-]{8,}"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._-]{16,}"),
    # A long opaque run that *mixes* case and digits — the shape of an API
    # token. Requiring the mix is what keeps this off the noise it would
    # otherwise drown in: sha256 digests, UUIDs and flattened path slugs are
    # all lower-case-and-hyphens and are not credentials.
    re.compile(
        r"\b(?=[A-Za-z0-9_-]*[a-z])(?=[A-Za-z0-9_-]*[A-Z])"
        r"(?=[A-Za-z0-9_-]*[0-9])[A-Za-z0-9_-]{40,}\b"
    ),
)


def redact(text: str) -> str:
  """Return `text` with operator identity, home paths and home slugs removed."""
  out = text.replace(_HOME, "<HOME>").replace(_HOME_SLUG, "<HOME-SLUG>")
  for value in _IDENTITY:
    out = out.replace(value, "<OPERATOR>")
  return out


def blockers(payload: str) -> list[str]:
  """Return every reason this artifact must not be committed, empty if none.

  Args:
    payload: The serialized evidence artifact.

  Returns:
    One finding per hit: an operator-home path, the operator's git identity, or
    a credential-shaped value. Empty means nothing this check knows about is
    present — which is **necessary, not sufficient**: it looks for the classes
    named here and cannot speak to anything else the bodies may contain.
  """
  found = []
  if _HOME in payload:
    found.append(f"operator home path {_HOME!r}")
  if _HOME_SLUG in payload:
    found.append(f"operator home slug {_HOME_SLUG!r}")
  for value in _IDENTITY:
    if value and value in payload:
      found.append("operator git identity")
  for pattern in _CREDENTIAL_PATTERNS:
    for hit in pattern.findall(payload):
      found.append(f"credential-shaped value matching {pattern.pattern}")
      del hit
      break
  return found


def _text(value: str, limit: int = TEXT_KEEP_CHARS) -> str:
  """Return the block's text if the report quotes it, else just its size.

  Redacts first, elides a startup-context reminder by size, and keeps a body
  only when it contains one of the :data:`CITED` phrases — so what lands in the
  repo is the evidence for a specific sentence in `REPORT.md`, not the run's
  message content at large.
  """
  clean = redact(value)
  cited = any(phrase in clean for phrase in CITED)
  if "<system-reminder>" in clean and len(clean) > REMINDER_KEEP_BYTES:
    return f"<system-reminder elided: {len(clean)} chars of startup context>"
  if not cited:
    return f"<uncited: {len(clean)} chars>"
  # A cited `<system-reminder>` is kept whole: §14's byte-identical claim is
  # about this string, and a truncated copy cannot witness it.
  if "<system-reminder>" in clean:
    return clean
  if len(clean) > limit:
    return clean[:limit] + f"… (+{len(clean) - limit} chars)"
  return clean


def _blocks(content: object, *, tool_limit: int = TOOL_RESULT_KEEP_CHARS):
  """Summarize a message `content` as (block labels, kept texts)."""
  labels: list[str] = []
  texts: list[str] = []
  if isinstance(content, str):
    return ["text"], [_text(content)]
  if not isinstance(content, list):
    return labels, texts
  for block in content:
    if not isinstance(block, dict):
      continue
    kind = block.get("type")
    if kind == "tool_use":
      labels.append(f"tool_use:{block.get('name')}")
      texts.append(_text(json.dumps(block.get("input")), limit=tool_limit))
    elif kind == "tool_result":
      labels.append("tool_result")
      texts.append(_text(json.dumps(block.get("content")), limit=tool_limit))
    elif kind == "text":
      labels.append("text")
      texts.append(_text(str(block.get("text"))))
    else:
      labels.append(str(kind))
  if all(t.startswith("<uncited:") for t in texts):
    texts = []
  return labels, texts


def _reminder_count(content: object) -> int:
  """Count `<system-reminder>` openings in a message's text, before redaction."""
  if isinstance(content, str):
    return content.count("<system-reminder>")
  if not isinstance(content, list):
    return 0
  return sum(
      str(b.get("text", "")).count("<system-reminder>")
      for b in content
      if isinstance(b, dict) and b.get("type") == "text"
  )


# Per-token progress chatter. Counted, not listed: nothing in the report reads
# an individual one, and they are most of the event rows.
NOISE_EVENTS = (
    ("system", "thinking_tokens"),
    ("tool_progress", None),
    ("rate_limit_event", None),
)


def _stdout_events(run_dir: pathlib.Path) -> tuple[list[dict[str, object]], dict[str, int]]:
  """Return the meaningful stdout rows, plus a count of the elided noise."""
  out = []
  noise: dict[str, int] = {}
  for path in _event_files(run_dir):
    for line in path.read_text().splitlines():
      entry = json.loads(line)
      event = entry["event"]
      kind = event.get("type") if isinstance(event, dict) else "raw"
      subtype = event.get("subtype") if isinstance(event, dict) else None
      if (kind, subtype) in NOISE_EVENTS:
        noise[str(subtype)] = noise.get(str(subtype), 0) + 1
        continue
      row: dict[str, object] = {
          "dt": entry["dt"],
          "dir": entry["dir"],
          "type": kind,
      }
      if isinstance(event, dict):
        if event.get("subtype"):
          row["subtype"] = event["subtype"]
        if event.get("type") == "result":
          row["num_turns"] = event.get("num_turns")
          row["result"] = _text(str(event.get("result")))
        message = event.get("message")
        if isinstance(message, dict):
          labels, texts = _blocks(message.get("content"))
          row["role"] = message.get("role")
          row["blocks"] = labels
          row["texts"] = texts
          row["message_id"] = message.get("id")
        if event.get("type") == "control_response":
          row["control_response"] = event.get("response")
      out.append(row)
  return out, noise


def _event_files(run_dir: pathlib.Path) -> list[pathlib.Path]:
  own = run_dir / "events.jsonl"
  return [own] if own.is_file() else sorted(run_dir.glob("phase*/events.jsonl"))


def _transcript_records(raw: str) -> list[dict[str, object]]:
  out = []
  for line in raw.splitlines():
    record = json.loads(line)
    kind = record.get("type")
    row: dict[str, object] = {"type": kind}
    if kind == "attachment":
      attachment = record.get("attachment", {})
      row["attachment"] = attachment.get("type")
      # Never the payload: skill/agent listings are host configuration. The two
      # scalars below are what §13.3 quotes.
      for scalar in ("maxTurns", "turnCount"):
        if scalar in attachment:
          row[scalar] = attachment[scalar]
      if attachment.get("type") == "queued_command":
        labels, texts = _blocks(attachment.get("prompt"))
        row["blocks"], row["texts"] = labels, texts
    elif kind in ("user", "assistant"):
      message = record.get("message", {})
      labels, texts = _blocks(message.get("content"))
      row["role"] = message.get("role")
      row["blocks"], row["texts"] = labels, texts
      for field in ("promptSource", "entrypoint", "isMeta", "origin"):
        if record.get(field) is not None:
          row[field] = record[field]
    elif kind == "queue-operation":
      row["operation"] = record.get("operation")
      if record.get("reason"):
        row["reason"] = record["reason"]
    out.append(row)
  return out


# The TUI issues requests that are not the agent's task loop and must not be the
# one the report compares: a startup `quota` probe (no `tools`) and, after the
# turn, a prompt-suggestion request whose body is the whole conversation plus a
# synthetic trailing user message — a message nobody sent, which would enter the
# comparison as a real turn.
SUGGESTION_MARKER = "SUGGESTION MODE"


def _is_agent_loop(record: dict[str, object]) -> bool:
  """True when a record is the agent's own loop rather than a side call."""
  body = record.get("request", {}).get("body", {})
  if not body.get("tools"):
    return False
  messages = body.get("messages") or []
  if not messages:
    return False
  return SUGGESTION_MARKER not in json.dumps(messages[-1])


def select_wire_record(
    records: list[dict[str, object]],
) -> tuple[int | None, dict[str, int]]:
  """Return the index of the request §14 compares, plus what was skipped.

  The comparison is over the **last agent-loop request**: the last exchange
  whose body carries `tools` and whose trailing message is not the TUI's
  prompt-suggestion prompt.

  Args:
    records: Every record in the proxy log, in order.

  Returns:
    `(index, counts)` — the selected record's index (`None` when there is no
    agent-loop request at all), and a breakdown of `api_calls`,
    `agent_loop_calls` and `excluded_side_calls`.
  """
  loop = [i for i, record in enumerate(records) if _is_agent_loop(record)]
  counts = {
      "api_calls": len(records),
      "agent_loop_calls": len(loop),
      "excluded_side_calls": len(records) - len(loop),
  }
  return (loop[-1] if loop else None), counts


def _wire(raw: str) -> dict[str, object]:
  records = [json.loads(l) for l in raw.splitlines() if l.strip()]
  if not records:
    return {}
  index, counts = select_wire_record(records)
  if index is None:
    return dict(counts) | {"selected_record_index": None, "messages": []}
  messages = (
      records[index].get("request", {}).get("body", {}).get("messages", [])
  )
  rows = []
  total = 0
  for position, message in enumerate(messages):
    count = _reminder_count(message.get("content"))
    total += count
    labels, texts = _blocks(
        message.get("content"), tool_limit=TOOL_RESULT_KEEP_CHARS
    )
    rows.append({
        "i": position,
        "role": message.get("role"),
        "blocks": labels,
        "texts": texts,
        # Equality witnesses, over the **raw** text: two runs' messages can be
        # compared byte-for-byte from the committed artifacts, without the
        # committed artifacts carrying the bytes.
        "text_digests": _digests(message.get("content")),
        "system_reminder_blocks": count,
    })
  return dict(counts) | {
      "selected_record_index": index,
      "selection": (
          "last agent-loop request: has `tools`, trailing message is not the"
          " TUI prompt-suggestion prompt"
      ),
      "messages": rows,
      "system_reminder_blocks": total,
  }


def _digests(content: object) -> list[dict[str, object]]:
  """Return `{len, sha256}` per text block, computed on the raw text."""
  blocks = [content] if isinstance(content, str) else content
  if not isinstance(blocks, list):
    return []
  out = []
  for block in blocks:
    if isinstance(block, str):
      text = block
    elif isinstance(block, dict) and block.get("type") == "text":
      text = str(block.get("text", ""))
    else:
      continue
    out.append({
        "len": len(text),
        "sha256": hashlib.sha256(text.encode()).hexdigest(),
    })
  return out


def build(run_dir: pathlib.Path) -> dict[str, object]:
  """Return the evidence artifact for `run_dir` (does not write it)."""
  meta = json.loads((run_dir / "meta.json").read_text())
  transcript_raw, transcript_source = transcripts.load(run_dir)
  # A TUI arm has no `stream-json` stdout at all — its terminal bytes are not a
  # trace and are never read for findings — so the stream column is null there
  # rather than a misleading False.
  stream_raw = "".join(p.read_text() for p in _event_files(run_dir))
  has_stream = bool(_event_files(run_dir))
  proxy_path = run_dir / "proxy.jsonl"
  proxy_raw = proxy_path.read_text() if proxy_path.is_file() else ""

  sources = {}
  for name in RAW_NAMES:
    path = run_dir / name
    if path.is_file():
      body = path.read_bytes()
      sources[name] = {
          "sha256": hashlib.sha256(body).hexdigest(),
          "bytes": len(body),
      }

  stdout_rows, stdout_noise = _stdout_events(run_dir)
  evidence: dict[str, object] = {
      "run": run_dir.name,
      "note": (
          "Redacted evidence for REPORT.md. The raw captures this is built"
          " from are gitignored: they carry operator-home paths and the"
          " operator's global CLAUDE.md. Rebuild with"
          " `uv run python evidence.py <run-dir>` after re-running the"
          " scenario; the sha256s below identify the exact inputs used here."
      ),
      "meta": json.loads(redact(json.dumps(meta))),
      "sources": sources,
      "transcript_source": transcript_source,
      "artifact_greps": {
          artifact: {
              "transcript": artifact in transcript_raw,
              "stream": (artifact in stream_raw) if has_stream else None,
              "wire": artifact in proxy_raw if proxy_raw else None,
          }
          for artifact in ARTIFACTS
      },
      "stdout_events": stdout_rows,
      "stdout_events_elided": stdout_noise,
      "transcript_records": _transcript_records(transcript_raw),
  }
  if proxy_raw:
    evidence["wire"] = _wire(proxy_raw)
  return evidence


def write(run_dir: pathlib.Path) -> list[str]:
  """Build and write `evidence.json`; return blockers (nothing written if any)."""
  payload = json.dumps(build(run_dir), indent=1, ensure_ascii=False)
  found = blockers(payload)
  if found:
    return found
  _ = (run_dir / EVIDENCE_NAME).write_text(payload + "\n")
  return []


def check(run_dir: pathlib.Path) -> list[str]:
  """Re-scan a committed `evidence.json`; return blockers, empty if clean."""
  path = run_dir / EVIDENCE_NAME
  if not path.is_file():
    return [f"{path} is missing"]
  return blockers(path.read_text())


def scan_tracked() -> list[tuple[str, list[str]]]:
  """Run :func:`blockers` over every git-tracked file in this directory.

  The build-time scan only sees what `evidence.py` emits. This one is the
  regression check over what is actually about to be committed — including
  files nobody thought of as trace records.

  Returns:
    One `(path, findings)` pair per file with findings.
  """
  here = pathlib.Path(__file__).resolve().parent
  listed = subprocess.run(
      ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
      cwd=here,
      capture_output=True,
      text=True,
      check=False,
  ).stdout.split("\0")
  out = []
  for name in listed:
    if not name:
      continue
    path = here / name
    if not path.is_file():
      continue
    try:
      body = path.read_text()
    except UnicodeDecodeError:
      continue
    found = blockers(body)
    if found:
      out.append((name, sorted(set(found))))
  return out


def main() -> int:
  flags = {a for a in sys.argv[1:] if a.startswith("--")}
  args = [a for a in sys.argv[1:] if not a.startswith("--")]
  if "--scan-tracked" in flags:
    hits = scan_tracked()
    for name, found in hits:
      print(f"BLOCKED {name}: {'; '.join(found)}")
    print(f"{len(hits)} tracked file(s) blocked" if hits else "ok: no findings")
    return 1 if hits else 0
  check_only = "--check" in flags
  # Selected by what each mode actually needs: `--check` reads the committed
  # artifact (the only file present in a fresh checkout), a build reads the raw
  # capture and its meta.json, which are local-only.
  marker = EVIDENCE_NAME if check_only else "meta.json"
  failed = 0
  seen = 0
  for arg in args:
    run_dir = pathlib.Path(arg)
    if not (run_dir / marker).is_file():
      continue
    seen += 1
    found = check(run_dir) if check_only else write(run_dir)
    if found:
      failed += 1
      print(f"BLOCKED {run_dir}: {'; '.join(sorted(set(found)))}")
    else:
      print(f"ok {run_dir}")
  if not seen:
    print(f"no run directory matched (looked for {marker})")
    return 1
  if failed:
    print(f"\n{failed} run(s) blocked — nothing was written for those.")
  return 1 if failed else 0


if __name__ == "__main__":
  raise SystemExit(main())
