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
replaces that with a typed `Conversation` + a conversion seam (a
`Harness.to_conversation` method + the shared observer that runs it).

Naming (decided 2026-07-22): the canonical model is **`conversation`** — *not*
`trace` (collides with performance tracing), and *not* `trajectory` (that is a
Claude-Code-ism; Codex/Grok Build emit different formats). A harness's **native**
output keeps its own name — Claude Code's is `event_stream`.

This is pulled **out of task 06** deliberately: the model is shared by every
harness and every consumer, and the owner wants to grow it (more block kinds,
metadata) independently of the claude_code harness. Task 06's conversation observer is
the first *consumer*.

### In scope

- `swe_lab/conversation/model.py`: the Pydantic `Conversation` / `Message` /
  `Role` / `ContentBlock` model — **our own** implementation, shaped after
  `locode-protocol` + the Anthropic SDK.
- `swe_lab/conversation/observer.py`: the **shared, harness-agnostic**
  `ConversationObserver(SandboxObserver)` + the tiny **`ConversationProducer` ABC**
  it depends on (`to_conversation` + `native_outputs`). The observer takes **the
  harness** (as a `ConversationProducer`) and, in `before_destroy`, writes
  `conversation.json` and registers `conversation` + every native byproduct that
  landed (a harness produces more than one — `event_stream` *and* `agent.stderr`,
  …). **Not** a per-harness observer; it depends only on the `ConversationProducer`
  interface, so the conversation package never imports `harnesses` (no cycle).
- **No `ConversationConverter` ABC** (dropped 2026-07-22, §3): conversion is a
  `Harness.to_conversation` method (task 06; `Harness` *is a*
  `ConversationProducer`), backed by a module-level function per harness.
- Pydantic added as a runtime dependency (owner-approved 2026-07-22; AGENTS.md
  ask-first boundary satisfied).
- Round-trip + fixture-based unit tests (no Docker, no network).

### Out of scope

- **Renaming the W1 on-disk artifacts.** `.last_exchange.json` files are
  **already published to Hugging Face** by W1 (731 traces). New code speaks
  `conversation`, but renaming/re-hosting the published artifacts is an
  **ask-first HF change** → a separate backlog item (§6), not this task.
- Proxy capture (task 08): the harness's `to_conversation` grows to handle the
  proxy format alongside `event_stream`.
- Codex/Grok conversion — the seam (`Harness.to_conversation` + shared observer)
  is designed for them; impls come with their harnesses.
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

## 3. Conversion is a harness method, not a separate ABC

Conversion is a **harness responsibility** — the harness wrote the invocation
that produced the native output, so reading that output back sits next to
`mounts()`/`build_body()` as one of its own concerns. There is **no
`ConversationConverter` ABC** (dropped 2026-07-22): it was a one-method
indirection whose only job was to *name* "a thing with `to_conversation`", which
a plain `Callable[[Path], Conversation]` already does — an interceptor, not a
type worth an abstraction. So:

- the `Harness` ABC (task 06) exposes `to_conversation(self, workspace: Path) ->
  Conversation` (it reads its *own* primary output file from the workspace, by a
  name only it knows);
- the pure logic lives as a **module-level function** in the harness package,
  taking the specific file, so it is reusable offline (re-processing a stored
  native output, W1 later) without constructing a harness that needs a `prompt`.
  `Harness.to_conversation` just resolves its file and delegates:

```python
# ─── harnesses/claude_code/convert.py ───────────────────────────────────────
def event_stream_to_conversation(raw: Path) -> Conversation:
  """Claude Code `event_stream` (`--output-format stream-json`) → Conversation."""
  messages: list[Message] = []
  for line in raw.read_text().splitlines():           # fresh parse, stdlib json
    event = json.loads(line)
    ...                                                # event → Message/ContentBlock
  return Conversation(messages=messages)

# ClaudeCodeHarness.to_conversation(self, workspace):
#   return event_stream_to_conversation(workspace / EVENT_STREAM_NAME)
```

The claude_code impl is **written fresh** — it parses the stream-json lines
straight into the typed model with stdlib `json`, rather than wrapping
`core/agent/trace.py`'s `parse_stream_events` / `build_exchange_from_stream`
(which produce the legacy untyped dict and are **deprecation-bound**; their
cleanup rides the `core/` removal at 10b). Parsing straight into `Conversation`
is simpler and leaves nothing to unwind. The raw `event_stream.jsonl` is still
kept verbatim as an artifact; the `Conversation` is the canonical one.

### The shared observer + the `ConversationProducer` it runs

`ConversationObserver` is **shared and harness-agnostic**; it is kept (not folded
into the composition) because it must run *during* the run to (1) convert + write
`conversation.json` and (2) **register** `conversation` **plus every native
byproduct** as artifacts — and artifact registration can only happen through a
hook's `Contribution` (so the persist observer, task 12, also `before_destroy`,
uploads them to T1). A harness produces **more than one** native file (Claude
Code: the `event_stream` *and* the `agent.stderr` log, later maybe others).

Rather than pluck out a `convert` callable + an outputs dict (which reads as "one
file → one conversation"), the observer takes **the harness itself**, typed
against a tiny **`ConversationProducer` ABC** that the conversation package defines
— so the harness owns reading whatever files it needs, and the conversation
package never imports `harnesses`. `Harness` *is a* `ConversationProducer`, so the
dependency runs one way (`harnesses` → `conversation`) with no cycle:

```python
# ─── swe_lab/conversation/observer.py ───────────────────────────────────────
class ConversationProducer(ABC):            # what the observer needs from a harness
  @abstractmethod
  def to_conversation(self, workspace: Path) -> Conversation: ...  # read own outputs
  @abstractmethod
  def native_outputs(self) -> dict[str, str]: ...  # artifact name → workspace filename

@dataclass
class ConversationObserver(SandboxObserver):
  """Shared: convert a producer's primary output + register all its byproducts."""

  producer: ConversationProducer                # the harness (as a ConversationProducer)
  conversation: Conversation | None = None  # single-run state

  def before_destroy(self, sb: Sandbox) -> Contribution | None:
    self.conversation = self.producer.to_conversation(sb.workspace)
    dest = sb.workspace / CONVERSATION_NAME
    _ = dest.write_text(self.conversation.model_dump_json(indent=2))
    artifacts = {"conversation": dest}
    for name, filename in self.producer.native_outputs().items():
      path = sb.workspace / filename
      if path.is_file():                    # only register what actually landed
        artifacts[name] = path
    return Contribution(artifacts=artifacts)
```

The composition just passes the harness: `ConversationObserver(producer=harness)`.
A `complete` flag (did the agent finish cleanly?) is harness-specific to derive,
so it is *not* on this shared shape — task 06 surfaces it from the harness where
needed.

## 4. Consumers

- **Task 06** wires `ConversationObserver(producer=harness)` into the rollout
  composition (`Harness` *is a* `ConversationProducer`).
- **Task 08** adds proxy capture: the harness's `to_conversation` handles the
  proxy format too (or dispatches on its `capture`) — same shared observer.
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
4. ~~`ConversationConverter` ABC~~ — **dropped 2026-07-22** (§3): conversion is a
   `Harness.to_conversation` method; the observer takes **the harness** as a
   `ConversationProducer` (a small ABC in the conversation package), calling
   `to_conversation` + `native_outputs`. ✅ **Reconciled 2026-07-23**: the shipped
   code now drops the old ABC (`convert.py` removed), defines `ConversationProducer`
   in `observer.py`, and retypes the observer to `producer: ConversationProducer`
   (registering every byproduct that landed, not one `raw_output`); exports +
   tests updated.
