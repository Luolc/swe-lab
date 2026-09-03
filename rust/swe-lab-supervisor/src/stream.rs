//! The actor's wire: `stream-json` lines in both directions.
//!
//! What goes *to* the actor is one shape, the user event the CLI accepts
//! under `--input-format stream-json`. It is the shape the Python harness
//! writes (`user_event_line`), reproduced rather than re-derived.

use serde_json::json;

/// One stream-json user event carrying `text`, newline-terminated — the task
/// prompt at the start of a run, and every correction after it.
#[must_use]
pub fn user_event_line(text: &str) -> String {
    let event = json!({
        "type": "user",
        "message": {
            "role": "user",
            "content": [{"type": "text", "text": text}],
        },
    });
    format!("{event}\n")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_user_event_is_one_line_the_cli_reads() {
        let line = user_event_line("hello\nthere");
        assert!(line.ends_with('\n'));
        assert_eq!(
            line.matches('\n').count(),
            1,
            "the body's newline is escaped"
        );
        let event: serde_json::Value = serde_json::from_str(&line).unwrap();
        assert_eq!(event["type"], "user");
        assert_eq!(event["message"]["role"], "user");
        assert_eq!(event["message"]["content"][0]["type"], "text");
        assert_eq!(event["message"]["content"][0]["text"], "hello\nthere");
    }
}
