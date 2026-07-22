# Task 06a — `Conversation` protocol + output converters

> **Status: PLANNED — pre-implementation.** Source of truth: the approved
> [spec](../spec.md) (§Agent output → one typed `Conversation`). References:
> the sibling `locode-core`'s `crates/locode-protocol/src/lib.rs` (its ADR-0013
> conversation protocol) and the Anthropic Python SDK's `anthropic.types`
> (`Message`, `ContentBlock` — Pydantic `BaseModel`s, `type`-discriminated
> unions). Grounded in the current Claude-Code trace code
> (`src/swe_lab/core/agent/trace.py` at `fae1738`). Open items in §7.

---

## 1. Purpose & scope

Give the project **one provider-neutral, well-typed conversation model** that
every harness converts its native output into, so nothing downstream (persisted
records, W3 behavioral analysis, future rubric judges) has to parse a
harness-specific shape. Today the record is an untyped `dict` (`last_stream_record`
→ `build_exchange_from_stream`) misnamed `exchange`/`last_exchange`; this task
replaces that with a typed `Conversation` + a `ConversationConverter` seam.

Naming (decided 2026-07-22): the canonical model is **`conversation`** — *not*
`trace` (collides with performance tracing), and *not* `trajectory` (that is a
Claude-Code-ism; Codex/Grok Build emit different formats). A harness's **native**
output keeps its own name — Claude Code's is `event_stream`.

This is pulled **out of task 06** deliberately: the model is shared by every
harness and every consumer, and the owner wants to grow it (more block kinds,
metadata) independently of the claude_code harness. Task 06's trace observer is
the first *consumer*.

### In scope

- `swe_lab/conversation/model.py`: the Pydantic `Conversation` / `Message` /
  `Role` / `ContentBlock` model — **our own** implementation, shaped after
  `locode-protocol` + the Anthropic SDK.
- `swe_lab/conversation/convert.py`: the `ConversationConverter` **ABC**
  (harness-native output → `Conversation`).
- `swe_lab/conversation/observer.py`: the **shared, harness-agnostic**
  `ConversationObserver(SandboxObserver)` — parameterized by a converter + the
  native output filename, it produces `conversation.json` in `before_destroy`
  and registers the `conversation` + raw artifacts. **Not** a per-harness
  observer (only the *converter* is harness-specific); no harness-specific
  concept lives on it.
- `harnesses/claude_code/convert.py`: the first converter impl — Claude Code
  `event_stream`
  (`--output-format stream-json`) → `Conversation` (wraps the existing
  `parse_stream_events` / `build_exchange_from_stream`, then maps to the typed
  model). Lands with task 06.
- Pydantic added as a runtime dependency (owner-approved 2026-07-22; AGENTS.md
  ask-first boundary satisfied).
- Round-trip + fixture-based unit tests (no Docker, no network).

### Out of scope

- **Renaming the W1 on-disk artifacts.** `.last_exchange.json` files are
  **already published to Hugging Face** by W1 (731 traces). New code speaks
  `conversation`, but renaming/re-hosting the published artifacts is an
  **ask-first HF change** → a separate backlog item (§6), not this task.
- The proxy converter (task 08 adds `proxy` → `Conversation` alongside the
  event-stream converter).
- Codex/Grok converters — the ABC is designed for them; impls come with their
  harnesses.
- Multimodal richness beyond what a coding agent emits (images kept in the model
  for parity with upstream, but not exercised in v0).

## 2. The model (ported, not imported)

Shape follows `locode-protocol` (clean, minimal) with Pydantic mechanics from
the Anthropic SDK (`BaseModel`, `type` discriminator). We implement our own so
we control the surface and are never boxed in where the upstream SDK can't reach
a case we need.

```python
# ─── swe_lab/conversation/model.py ──────────────────────────────────────────
class Role(StrEnum):
  SYSTEM = "system"        # immutable base identity / policy
  DEVELOPER = "developer"  # app-author instructions + injected context
  USER = "user"            # human turns; also carries ToolResult blocks
  ASSISTANT = "assistant"  # model turns: text, reasoning, tool-use

class TextBlock(BaseModel):
  type: Literal["text"] = "text"
  text: str

class ReasoningBlock(BaseModel):
  type: Literal["reasoning"] = "reasoning"
  text: str
  signature: str | None = None    # Anthropic thinking signature, when present

class ToolUseBlock(BaseModel):
  type: Literal["tool_use"] = "tool_use"
  id: str
  name: str
  input: dict[str, Any]           # arbitrary tool arguments (JSON)

class ToolResultBlock(BaseModel):
  type: Literal["tool_result"] = "tool_result"
  tool_use_id: str
  content: str                    # minimal v0: flattened text result
  is_error: bool = False

type ContentBlock = Annotated[
    TextBlock | ReasoningBlock | ToolUseBlock | ToolResultBlock,
    Field(discriminator="type"),
]

class Message(BaseModel):
  role: Role
  content: list[ContentBlock]

class Conversation(BaseModel):
  messages: list[Message]
```

Design notes:

- **Minimal block set (v0)** — `Text` / `Reasoning` / `ToolUse` / `ToolResult`,
  the shapes a coding agent emits. `Image` and structured tool-result chunks are
  deferred (a `ToolResult` carries flattened text for now); adding a block class
  later is non-breaking for consumers that switch on `type`.
- **No separate `system` field** (as in `locode-protocol`): a `SYSTEM` message
  *is* the base prompt. Keeps one uniform stream.
- **`type`-discriminated union** exactly like the Anthropic SDK, so
  `Conversation.model_validate_json` / `.model_dump_json` round-trip losslessly
  and a reader maps block → shape by its `type` at a glance.

## 3. The converter seam

```python
# ─── swe_lab/conversation/convert.py ────────────────────────────────────────
class ConversationConverter(ABC):
  """Harness-native output → the canonical Conversation (ADR-0002 ABC)."""

  @abstractmethod
  def to_conversation(self, raw: Path) -> Conversation:
    """Read a harness-native output file → a typed Conversation."""
    ...

# ─── harnesses/claude_code/convert.py ───────────────────────────────────────
class EventStreamConverter(ConversationConverter):
  """Claude Code `event_stream` (`--output-format stream-json`) → Conversation."""

  @override
  def to_conversation(self, raw: Path) -> Conversation:
    events = parse_stream_events(read_lines(raw))       # reuse trace.py
    return _events_to_conversation(events)              # map to the typed model
```

The claude_code impl is thin: reuse the battle-tested `parse_stream_events`
(already handles partial lines / interleaving), then walk the events into
`Message`/`ContentBlock`s. The raw `event_stream.jsonl` is still kept verbatim as
an artifact; the `Conversation` is the canonical one.

```python
# ─── swe_lab/conversation/observer.py ───────────────────────────────────────
@dataclass
class ConversationObserver(SandboxObserver):
  """Shared: convert a harness's native output → conversation.json.

  Harness-agnostic — the harness injects its `converter` + `raw_name` (the
  native output file it writes). Nothing Claude-specific lives here.
  """

  converter: ConversationConverter
  raw_name: str                       # e.g. "event_stream.jsonl" (harness-owned)
  conversation: Conversation | None = None    # single-run state
  complete: bool = False

  def before_destroy(self, sb: Sandbox) -> Contribution | None:
    raw = sb.workspace / self.raw_name
    self.conversation = self.converter.to_conversation(raw)
    (sb.workspace / CONVERSATION_NAME).write_text(
        self.conversation.model_dump_json(indent=2)
    )
    artifacts = {"conversation": sb.workspace / CONVERSATION_NAME}
    if raw.is_file():
      artifacts["raw_output"] = raw     # harness-native, verbatim
    return Contribution(artifacts=artifacts)
```

The `complete` flag (did the agent finish cleanly?) is harness-specific to
derive, so it is set by the harness's converter/observer wiring in task 06, not
baked into this shared shape.

## 4. Consumers

- **Task 06** wires `ConversationObserver(converter=EventStreamConverter(),
  raw_name=EVENT_STREAM_NAME)` into the rollout composition.
- **Task 08** adds a proxy converter behind the same ABC (same observer).
- **W1 later** (post-cutover) can adopt `Conversation` in place of its
  `last_exchange` dicts — tracked in §6, not done here.

## 5. Tests (all Docker-free)

- **Round-trip:** a hand-built `Conversation` → `model_dump_json` →
  `model_validate_json` is identical; unknown/extra fields handled per policy.
- **Discriminator:** each block kind parses to its class from `{"type": …}`.
- **Converter:** the checked-in `event_stream` fixture (text + a tool_use paired
  to a tool_result + a terminal `result`) → a `Conversation` with the right
  roles, ordered blocks, and `tool_use_id` pairing; an empty/absent file →
  `Conversation(messages=[])`.

## 6. Backlog (recorded, not in this task)

- **Rename W1 artifacts `.last_exchange.json` → `conversation`** and re-host on
  Hugging Face. Ask-first (HF re-host boundary); the 731 published traces must
  migrate together or a compat alias kept. Do **not** touch W1's reader/writer
  here.
- **W1 adopts `Conversation`** in `pipelines/related_files/` in place of its
  `last_*_record` dicts, once W1 migrates onto the engine.

## 7. Open questions (need user confirmation)

1. ~~Concept naming~~ — **resolved 2026-07-22**: canonical model =
   **`conversation`** (`swe_lab/conversation/`, artifact `conversation.json`);
   Claude Code native raw = **`event_stream`** (`event_stream.jsonl`);
   `trajectory` retired as a shared name (Claude-specific).
2. ~~Pydantic vs. stdlib dataclass~~ — **resolved 2026-07-22**: **Pydantic**
   (runtime validation + JSON (de)serialize). Recorded as the ask-first
   runtime-dep decision.
3. ~~How much of `locode-protocol` to port in v0~~ — **resolved 2026-07-22**:
   port the **minimal block set** only — `Text` / `Reasoning` / `ToolUse` /
   `ToolResult` (a coding agent's shapes). `Image` and the finer
   `ReasoningFormat` cross-wire replay contract are deferred until a consumer
   needs them.
