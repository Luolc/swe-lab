//! What the supervisor may see: the actor's records, converted and filtered.
//!
//! Two things happen here, and both are the Python side's semantics carried
//! over exactly, because they are what parity means:
//!
//! - **Conversion** (`swe_lab.harnesses.claude_code.convert.event_to_message`):
//!   one `stream-json` event becomes at most one message of typed blocks.
//! - **Filtering by origin** (`swe_lab.trace_synthesis.supervisor.EvidenceFilter`):
//!   what the *actor* produced is admitted — its assistant messages and the
//!   results of its own tool calls — and every user text is excluded, told
//!   apart only so the record says which kind it was. Stateless: a supervisor
//!   attached mid-run reaches the same verdict on a message as one that
//!   watched from the first event.
//!
//! The information barrier is the shape of [`Message`]: there is no field
//! that can carry a reference solution, a hidden test, a gold patch, an
//! oracle's output or a guidebook, and evidence is built only from records
//! the actor produced.

use serde_json::Value;

/// The provenance marker on every correction, so the actor can tell it apart
/// from its own output and from a tool's — and so this filter can tell the
/// supervisor's own words apart from an outside interjection.
pub const INTERVENTION_TAG: &str = "supervisor_note";

/// Who a message is from.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Role {
    /// The actor's own turn.
    Assistant,
    /// A user message: a tool result, the task, a correction, an interjection.
    User,
    /// A system message.
    System,
}

impl Role {
    /// The wire word, as the prompt renders it.
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Assistant => "assistant",
            Self::User => "user",
            Self::System => "system",
        }
    }
}

/// One content block. Only the kinds the Python model represents survive
/// conversion; the rest are dropped there too.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Block {
    /// Prose.
    Text(String),
    /// Reasoning ("thinking"). Its text is never rendered, so not kept.
    Reasoning,
    /// A tool call. Its name and input are never rendered, so not kept.
    ToolUse,
    /// The result of a tool call, flattened to text.
    ToolResult(String),
}

/// One converted record.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Message {
    /// Who produced it.
    pub role: Role,
    /// Its blocks, in order; never empty.
    pub blocks: Vec<Block>,
}

/// Convert one decoded `stream-json` event into a message, or nothing.
///
/// Nothing for a non-`user`/`assistant` event, a malformed `message`, an
/// unknown role, or content with no block we represent.
#[must_use]
pub fn event_to_message(event: &Value) -> Option<Message> {
    let kind = event.get("type").and_then(Value::as_str)?;
    if kind != "user" && kind != "assistant" {
        return None;
    }
    let message = event.get("message")?.as_object()?;
    let role = match message.get("role").and_then(Value::as_str)? {
        "user" => Role::User,
        "assistant" => Role::Assistant,
        "system" => Role::System,
        _ => return None,
    };
    let blocks = content_blocks(message.get("content"));
    if blocks.is_empty() {
        return None;
    }
    Some(Message { role, blocks })
}

fn content_blocks(content: Option<&Value>) -> Vec<Block> {
    match content {
        Some(Value::String(text)) => {
            if text.is_empty() {
                Vec::new()
            } else {
                vec![Block::Text(text.clone())]
            }
        }
        Some(Value::Array(items)) => items.iter().filter_map(one_block).collect(),
        _ => Vec::new(),
    }
}

fn one_block(item: &Value) -> Option<Block> {
    let item = item.as_object()?;
    match item.get("type").and_then(Value::as_str)? {
        "text" => Some(Block::Text(string_or_empty(item.get("text")))),
        "thinking" => Some(Block::Reasoning),
        "tool_use" => Some(Block::ToolUse),
        "tool_result" => Some(Block::ToolResult(flatten_result(item.get("content")))),
        _ => None,
    }
}

/// `str(item.get("text", ""))` on the Python side: a string as is, a
/// missing value as empty, anything else as its JSON text.
fn string_or_empty(value: Option<&Value>) -> String {
    match value {
        None | Some(Value::Null) => String::new(),
        Some(Value::String(text)) => text.clone(),
        Some(other) => other.to_string(),
    }
}

fn flatten_result(content: Option<&Value>) -> String {
    match content {
        Some(Value::String(text)) => text.clone(),
        Some(Value::Array(items)) => items
            .iter()
            .filter_map(|item| {
                let item = item.as_object()?;
                if item.get("type").and_then(Value::as_str) == Some("text") {
                    Some(string_or_empty(item.get("text")))
                } else {
                    None
                }
            })
            .collect::<Vec<_>>()
            .join("\n\n"),
        _ => String::new(),
    }
}

/// How a message was dispositioned. The words are the Python side's and
/// they appear in every log row, so the account of a run says why something
/// was not judged rather than leaving it missing.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Disposition {
    /// The actor's own turn.
    AdmittedAssistant,
    /// The result of the actor's own tool call.
    AdmittedToolResult,
    /// User text carrying the intervention tag: this supervisor's own words.
    ExcludedOwnIntervention,
    /// Any other user text: an outside interjection.
    ExcludedExternalText,
    /// No message, or nothing in it to keep.
    ExcludedNothingToKeep,
}

impl Disposition {
    /// The word written to the log.
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            Self::AdmittedAssistant => "assistant",
            Self::AdmittedToolResult => "tool-result",
            Self::ExcludedOwnIntervention => "excluded-own-intervention",
            Self::ExcludedExternalText => "excluded-external-text",
            Self::ExcludedNothingToKeep => "excluded-nothing-to-keep",
        }
    }
}

/// Decide whether one converted message becomes evidence, and say why.
#[must_use]
pub fn admit(message: Option<Message>) -> (Option<Message>, Disposition) {
    let Some(message) = message else {
        return (None, Disposition::ExcludedNothingToKeep);
    };
    if message.role == Role::Assistant {
        return (Some(message), Disposition::AdmittedAssistant);
    }
    let results: Vec<Block> = message
        .blocks
        .iter()
        .filter(|block| matches!(block, Block::ToolResult(_)))
        .cloned()
        .collect();
    if !results.is_empty() {
        return (
            Some(Message {
                role: message.role,
                blocks: results,
            }),
            Disposition::AdmittedToolResult,
        );
    }
    let text: String = message
        .blocks
        .iter()
        .filter_map(|block| match block {
            Block::Text(text) => Some(text.as_str()),
            _ => None,
        })
        .collect();
    if text.is_empty() {
        return (None, Disposition::ExcludedNothingToKeep);
    }
    if text.contains(&format!("<{INTERVENTION_TAG}>")) {
        return (None, Disposition::ExcludedOwnIntervention);
    }
    (None, Disposition::ExcludedExternalText)
}

#[cfg(test)]
mod tests {
    use serde_json::json;

    use super::*;

    fn admit_event(event: Value) -> (Option<Message>, Disposition) {
        admit(event_to_message(&event))
    }

    #[test]
    fn an_assistant_turn_is_admitted_whatever_its_blocks() {
        let (kept, disposition) = admit_event(json!({
            "type": "assistant",
            "message": {"role": "assistant", "content": [
                {"type": "thinking", "thinking": "hmm"},
                {"type": "tool_use", "id": "t1", "name": "Read", "input": {}}
            ]}
        }));
        assert_eq!(disposition, Disposition::AdmittedAssistant);
        assert_eq!(kept.unwrap().blocks, vec![Block::Reasoning, Block::ToolUse]);
    }

    #[test]
    fn a_tool_result_is_admitted_as_only_its_result_blocks() {
        let (kept, disposition) = admit_event(json!({
            "type": "user",
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": [
                    {"type": "text", "text": "line one"}, {"type": "text", "text": "line two"}
                ]},
                {"type": "text", "text": "and some user prose"}
            ]}
        }));
        assert_eq!(disposition, Disposition::AdmittedToolResult);
        assert_eq!(
            kept.unwrap().blocks,
            vec![Block::ToolResult("line one\n\nline two".to_string())]
        );
    }

    #[test]
    fn user_text_is_excluded_and_told_apart_by_origin() {
        let own = json!({"type": "user", "message": {"role": "user", "content":
            "<supervisor_note>\nlook again\n</supervisor_note>"}});
        assert_eq!(admit_event(own).1, Disposition::ExcludedOwnIntervention);
        let external = json!({"type": "user", "message": {"role": "user", "content":
            [{"type": "text", "text": "please hurry"}]}});
        assert_eq!(admit_event(external).1, Disposition::ExcludedExternalText);
    }

    #[test]
    fn events_carrying_no_message_are_nothing_to_keep() {
        for event in [
            json!({"type": "system", "subtype": "init"}),
            json!({"type": "result", "subtype": "success"}),
            json!({"type": "user", "message": "not an object"}),
            json!({"type": "user", "message": {"role": "tool", "content": "x"}}),
            json!({"type": "user", "message": {"role": "user", "content": ""}}),
            json!({"type": "user", "message": {"role": "user", "content": [{"type": "image"}]}}),
        ] {
            assert_eq!(admit_event(event).1, Disposition::ExcludedNothingToKeep);
        }
    }

    #[test]
    fn a_string_content_is_one_text_block_and_a_text_block_keeps_its_text() {
        let message = event_to_message(&json!({
            "type": "assistant",
            "message": {"role": "assistant", "content": "just prose"}
        }))
        .unwrap();
        assert_eq!(message.blocks, vec![Block::Text("just prose".to_string())]);
        let message = event_to_message(&json!({
            "type": "assistant",
            "message": {"role": "assistant", "content": [{"type": "text"}]}
        }))
        .unwrap();
        assert_eq!(message.blocks, vec![Block::Text(String::new())]);
    }
}
