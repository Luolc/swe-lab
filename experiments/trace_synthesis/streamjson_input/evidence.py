"""Build the committed, redacted evidence artifact for one run.

**Raw captures do not go in the repo.** A Claude Code transcript, its
`stream-json` stdout and a proxy log all carry host state that has nothing to do
with this experiment — absolute operator-home paths, the operator's global
`CLAUDE.md` (which names them and carries their email), the machine's skill and
agent listings. `AGENTS.md` says to redact operator PII in any trace record and
never to commit raw trace records, and this repo's history was once force-pushed
to scrub exactly that class of leak.

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
RAW_NAMES = ("events.jsonl", "transcript.jsonl", "proxy.jsonl")

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
TEXT_KEEP_CHARS = 420
TOOL_RESULT_KEEP_CHARS = 200

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
  """Redact, and elide a startup-context reminder or an over-long block."""
  clean = redact(value)
  if "<system-reminder>" in clean and len(clean) > REMINDER_KEEP_BYTES:
    return f"<system-reminder elided: {len(clean)} chars of startup context>"
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


def _stdout_events(run_dir: pathlib.Path) -> list[dict[str, object]]:
  out = []
  for path in _event_files(run_dir):
    for line in path.read_text().splitlines():
      entry = json.loads(line)
      event = entry["event"]
      row: dict[str, object] = {
          "dt": entry["dt"],
          "dir": entry["dir"],
          "type": event.get("type") if isinstance(event, dict) else "raw",
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
  return out


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


def _wire(raw: str) -> dict[str, object]:
  records = [json.loads(l) for l in raw.splitlines() if l.strip()]
  if not records:
    return {}
  messages = records[-1].get("request", {}).get("body", {}).get("messages", [])
  rows = []
  total = 0
  for index, message in enumerate(messages):
    count = _reminder_count(message.get("content"))
    total += count
    labels, texts = _blocks(message.get("content"), tool_limit=TOOL_RESULT_KEEP_CHARS)
    rows.append({
        "i": index,
        "role": message.get("role"),
        "blocks": labels,
        "texts": texts,
        "system_reminder_blocks": count,
    })
  return {
      "api_calls": len(records),
      "messages": rows,
      "system_reminder_blocks": total,
  }


def build(run_dir: pathlib.Path) -> dict[str, object]:
  """Return the evidence artifact for `run_dir` (does not write it)."""
  meta = json.loads((run_dir / "meta.json").read_text())
  transcript_raw, transcript_source = transcripts.load(run_dir)
  stream_raw = "".join(p.read_text() for p in _event_files(run_dir))
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
              "stream": artifact in stream_raw,
              "wire": artifact in proxy_raw if proxy_raw else None,
          }
          for artifact in ARTIFACTS
      },
      "stdout_events": _stdout_events(run_dir),
      "transcript_records": _transcript_records(transcript_raw),
  }
  if proxy_raw:
    evidence["wire"] = _wire(proxy_raw)
  return evidence


def write(run_dir: pathlib.Path) -> list[str]:
  """Build and write `evidence.json`; return blockers (nothing written if any)."""
  payload = json.dumps(build(run_dir), indent=2, ensure_ascii=False)
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
