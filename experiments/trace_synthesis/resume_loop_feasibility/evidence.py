"""Builds the committed, field-reduced witnesses this report's claims rest on.

Raw captures and session transcripts never enter the repository: they carry
operator-home paths and the operator's global `CLAUDE.md`. But a claim whose
only support is a file in `/tmp` on one machine is unauditable self-report, so
every number the report drives off is reduced here into `runs/*/evidence.json`
— shapes, roles, block lists, counters and text *digests* (length and sha256,
never content) — with the sha256 of each raw input recorded so a re-run can be
matched to what was published.

Two disciplines are load-bearing:

- **`has_tools` classifies a request.** A capture contains auxiliary calls
  (titling, suggestions) that carry no `tools` array. Counting them as
  main-loop requests misstates the request-to-segment account, so the
  classification is recorded rather than inferred later from message counts.
  This follows `streamjson_input`'s selection rule.
- **Nothing is written when the redaction re-scan finds anything.**

Usage:

    python evidence.py <capture-dir> <out-json> [--label NAME]
    python evidence.py --sessions <session-file>... --out <out-json>
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re

SEAM_USER_TEXT = "Continue from where you left off."
SEAM_SYNTHETIC_ASSISTANT = "No response requested."

# Shapes that must never appear in a committed artifact. The home path is
# derived at run time rather than written here, so this file contains none.
# Keys are descriptive names, not the patterns: an artifact that reported the
# patterns verbatim would trip its own scan on every run, and a checker that
# always fires is indistinguishable from one that never does.
_CREDENTIAL_SHAPES = {
    "anthropic_key_prefix": re.compile("sk" + "-ant-"),
    "bearer_jwt": re.compile(r"(?i)bearer\s+ey"),
    "jwt_shaped": re.compile(r"eyJ[A-Za-z0-9_-]{20,}"),
}


class RedactionFailure(RuntimeError):
  """Raised instead of writing when the re-scan finds something."""


def digest(text: str) -> dict[str, object]:
  """Reduces a text block to a length and a hash.

  Args:
    text: the block's content.

  Returns:
    Its length and sha256; the content itself is not returned.
  """
  return {
      "len": len(text),
      "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
  }


def message_shape(message: dict[str, object]) -> dict[str, object]:
  """Reduces one wire message to roles, block types and digests.

  Block composition is recorded, not just the role: the difference between
  `[tool_result]` and `[tool_result, text]` is invisible to a role sequence and
  is exactly what this experiment got wrong once.

  Args:
    message: one entry of a request's `messages` array.

  Returns:
    The reduced shape.
  """
  content = message.get("content")
  blocks: list[str] = []
  digests: list[dict[str, object]] = []
  if isinstance(content, str):
    blocks.append("str")
    digests.append(digest(content))
  else:
    for block in content or []:
      if not isinstance(block, dict):
        continue
      kind = str(block.get("type"))
      if kind == "tool_use":
        blocks.append(f"tool_use:{block.get('name')}")
      else:
        blocks.append(kind)
      if kind == "text":
        digests.append(digest(str(block.get("text", ""))))
  return {
      "role": message.get("role"),
      "blocks": blocks,
      "text_digests": digests,
  }


def reduce_capture(log: pathlib.Path) -> dict[str, object]:
  """Reduces a proxy capture to the structure the report cites.

  Args:
    log: the proxy's JSONL output.

  Returns:
    The credential gate's two arms, and one record per captured request.
  """
  raw = log.read_bytes()
  text = raw.decode("utf-8", errors="replace")
  requests: list[dict[str, object]] = []
  for index, line in enumerate(text.splitlines()):
    if not line.strip():
      continue
    body = json.loads(line)["request"]["body"]
    messages = body.get("messages") or []
    blob = json.dumps(messages)
    shapes = [message_shape(m) for m in messages]
    last = shapes[-1]["blocks"] if shapes else []
    requests.append({
        "index": index,
        # An auxiliary call (titling, suggestions) carries no tools array.
        "has_tools": bool(body.get("tools")),
        "tool_count": len(body.get("tools") or []),
        "kind": "agent-loop" if body.get("tools") else "auxiliary",
        "messages": len(messages),
        "role_sequence": [s["role"] for s in shapes],
        "message_shapes": shapes,
        "last_message_blocks": last,
        "mixed_tool_result_and_text_indices": [
            i
            for i, s in enumerate(shapes)
            if "tool_result" in s["blocks"] and "text" in s["blocks"]
        ],
        # The correction text accumulates one block per seam; counting it is
        # how the report's 1/2/3 progression stays checkable offline.
        "correction_text_blocks": sum(
            1
            for shape in shapes
            for i, kind in enumerate(shape["blocks"])
            if kind == "text"
            and "tool_result" in shape["blocks"]
        ),
        "seam_user_text_blocks": blob.count(SEAM_USER_TEXT),
        "seam_synthetic_assistant": blob.count(SEAM_SYNTHETIC_ASSISTANT),
        "system_reminder_blocks": blob.count("<system-reminder>"),
    })
  return {
      "source_sha256": hashlib.sha256(raw).hexdigest(),
      "source_bytes": len(raw),
      "credential_gate": {
          "redaction_marker_occurrences": text.lower().count("[redacted]"),
          "credential_shapes": {
              name: len(pattern.findall(text))
              for name, pattern in _CREDENTIAL_SHAPES.items()
          },
      },
      "requests": requests,
  }


def scan(payload: object) -> None:
  """Refuses a payload that carries a home path or a credential shape.

  Args:
    payload: the artifact about to be written.

  Raises:
    RedactionFailure: when the scan finds anything.
  """
  blob = json.dumps(payload)
  home = str(pathlib.Path.home())
  offenders: list[str] = []
  if home in blob or home.replace("/", "-") in blob:
    offenders.append("operator home path")
  for name, pattern in _CREDENTIAL_SHAPES.items():
    if pattern.search(blob):
      offenders.append(f"credential shape {name}")
  if offenders:
    raise RedactionFailure(f"refusing to write: {', '.join(offenders)}")


def write(payload: object, out: pathlib.Path) -> None:
  """Scans and then writes a witness.

  Args:
    payload: the artifact.
    out: where to write it.
  """
  scan(payload)
  out.parent.mkdir(parents=True, exist_ok=True)
  _ = out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def main() -> int:
  """Builds a witness from a capture or from session files.

  Returns:
    A process exit code.
  """
  parser = argparse.ArgumentParser()
  _ = parser.add_argument("capture", nargs="?")
  _ = parser.add_argument("out", nargs="?")
  _ = parser.add_argument("--label", default=None)
  _ = parser.add_argument("--sessions", nargs="*", default=None)
  _ = parser.add_argument("--out", dest="out_flag", default=None)
  args = parser.parse_args()

  if args.sessions is not None:
    rows: list[dict[str, object]] = []
    for name in args.sessions:
      path = pathlib.Path(name)
      body = path.read_bytes()
      rows.append({
          "session_stem": path.stem,
          "bytes": len(body),
          "sha256": hashlib.sha256(body).hexdigest(),
          "records": len(
              [l for l in body.decode("utf-8", "replace").splitlines() if l.strip()]
          ),
      })
    payload = {"kind": "session-files", "sessions": rows}
    write(payload, pathlib.Path(args.out_flag))
    print(json.dumps(payload, indent=2))
    return 0

  payload = reduce_capture(pathlib.Path(args.capture))
  if args.label:
    payload["label"] = args.label
  write(payload, pathlib.Path(args.out))
  gate = payload["credential_gate"]
  print(json.dumps({"label": args.label, "gate": gate,
                    "requests": len(payload["requests"])}, indent=2))
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
