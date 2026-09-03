//! The two model calls: the judge, and the writer.
//!
//! Both go to the one configured endpoint in the OpenAI chat-completions
//! shape, both record what answered them — the model the **response**
//! reports (an alias re-pointed upstream leaves the request looking correct),
//! the sampling actually sent, the raw text, and the `finish_reason`, so a
//! token ceiling hit is distinguishable from an unparseable answer in the
//! account — and neither is ever retried: a second ask would make the verdict
//! a function of how many times we asked.

use std::time::{Duration, Instant};

use serde_json::{Value, json};

use crate::config::Endpoint;
use crate::http;
use crate::prompt::{JUDGE_INSTRUCTIONS, WRITER_INSTRUCTIONS};

/// The judge's completion ceiling. The judge answers with two booleans and a
/// sentence, but a reasoning model spends tokens before it answers: on the
/// replay experiment (#383) successful calls used a median of 89 and at most
/// 441 reasoning tokens, and every one of the 85 lapses was a 512-token
/// ceiling reached mid-reasoning — one call in ten. 4 096 clears the observed
/// maximum nine times over; the answer itself is under a hundred tokens.
pub const JUDGE_MAX_TOKENS: u32 = 4_096;

/// The writer's ceiling: one line of at most 400 characters, and no writer
/// lapse was measured at this value.
pub const WRITER_MAX_TOKENS: u32 = 256;

/// What answered one request, and how it was asked. Written to the log row
/// of the boundary it served.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Call {
    /// Which of the two calls this was.
    pub purpose: &'static str,
    /// The model name sent.
    pub requested_model: String,
    /// The model the response reports, when it reports one.
    pub response_model: Option<String>,
    /// The `max_tokens` sent — the one sampling parameter set; the provider's
    /// defaults apply to the rest.
    pub max_tokens_sent: u32,
    /// The `finish_reason` of the first choice, when reported.
    pub finish_reason: Option<String>,
    /// The answer's text before parsing, or `None` when the choice carried no
    /// string content.
    pub raw: Option<String>,
    /// How long the call took.
    pub took: Duration,
}

impl Call {
    /// The record as a JSON object, for a log row.
    #[must_use]
    pub fn to_json(&self) -> Value {
        json!({
            "purpose": self.purpose,
            "requested_model": self.requested_model,
            "response_model": self.response_model,
            "max_tokens_sent": self.max_tokens_sent,
            "finish_reason": self.finish_reason,
            "raw": self.raw,
            "took_ms": u64::try_from(self.took.as_millis()).unwrap_or(u64::MAX),
        })
    }
}

/// One judge call's answer: two questions, not one.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Verdict {
    /// Whether the actor has left the criterion's path.
    pub off_track: bool,
    /// Whether, left alone, it would come back by itself.
    pub self_correcting: bool,
    /// The judge's own words, recorded but never acted on.
    pub reason: String,
}

/// A call that produced no usable answer: the reason, and the record of the
/// call when a response was received at all.
#[derive(Debug)]
pub struct Failed {
    /// Why the answer could not be used.
    pub reason: String,
    /// The call's record, when a response came back. Boxed so the error
    /// variant stays small on the happy path.
    pub call: Option<Box<Call>>,
}

/// How to reach the model.
#[derive(Debug, Clone)]
pub struct Model {
    /// The model name sent on every request.
    pub name: String,
    /// Where requests go.
    pub endpoint: Endpoint,
    /// The bearer credential, or none.
    pub bearer: Option<String>,
    /// The bound on one call, connection included.
    pub call_timeout: Duration,
}

impl Model {
    /// Ask the judge.
    ///
    /// # Errors
    ///
    /// The call failed, or its answer was not one JSON object with the two
    /// booleans. A field that is not a JSON boolean is refused, never
    /// coerced: `"false"` read as truth would turn a verdict of *no* into a
    /// correction.
    pub fn judge(&self, prompt: &str) -> Result<(Verdict, Call), Failed> {
        let call = self.complete("judge", JUDGE_INSTRUCTIONS, prompt, JUDGE_MAX_TOKENS)?;
        let Some(raw) = call.raw.as_deref() else {
            return Err(Failed {
                reason: "the answer carried no text".to_string(),
                call: Some(Box::new(call)),
            });
        };
        match parse_verdict(raw) {
            Ok(verdict) => Ok((verdict, call)),
            Err(reason) => Err(Failed {
                reason,
                call: Some(Box::new(call)),
            }),
        }
    }

    /// Ask the writer for the line. Unvalidated here: the policy applies the
    /// intervention's bounds and records a refusal as a lapse.
    ///
    /// # Errors
    ///
    /// The call failed, or its answer carried no text.
    pub fn write(&self, prompt: &str) -> Result<(String, Call), Failed> {
        let call = self.complete("writer", WRITER_INSTRUCTIONS, prompt, WRITER_MAX_TOKENS)?;
        match call.raw.clone() {
            Some(text) => Ok((text, call)),
            None => Err(Failed {
                reason: "the answer carried no text".to_string(),
                call: Some(Box::new(call)),
            }),
        }
    }

    fn complete(
        &self,
        purpose: &'static str,
        instructions: &str,
        prompt: &str,
        max_tokens: u32,
    ) -> Result<Call, Failed> {
        let payload = json!({
            "model": self.name,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": instructions},
                {"role": "user", "content": prompt},
            ],
        });
        let started = Instant::now();
        let response = http::post_json(
            &self.endpoint,
            self.bearer.as_deref(),
            payload.to_string().as_bytes(),
            started + self.call_timeout,
        )
        .map_err(|reason| Failed {
            reason: format!("call failed: {reason}"),
            call: None,
        })?;
        let took = started.elapsed();
        if response.status / 100 != 2 {
            return Err(Failed {
                reason: format!(
                    "answered HTTP {}: {}",
                    response.status,
                    String::from_utf8_lossy(&response.body)
                        .chars()
                        .take(300)
                        .collect::<String>()
                ),
                call: None,
            });
        }
        let body: Value = serde_json::from_slice(&response.body).map_err(|e| Failed {
            reason: format!("answered non-JSON: {e}"),
            call: None,
        })?;
        let choice = body.get("choices").and_then(|c| c.get(0));
        Ok(Call {
            purpose,
            requested_model: self.name.clone(),
            response_model: body
                .get("model")
                .and_then(Value::as_str)
                .map(str::to_string),
            max_tokens_sent: max_tokens,
            finish_reason: choice
                .and_then(|c| c.get("finish_reason"))
                .and_then(Value::as_str)
                .map(str::to_string),
            raw: choice
                .and_then(|c| c.get("message"))
                .and_then(|m| m.get("content"))
                .and_then(Value::as_str)
                .map(str::to_string),
            took,
        })
    }
}

/// Parse the judge's answer: one JSON object with the two booleans.
fn parse_verdict(raw: &str) -> Result<Verdict, String> {
    let answer: Value =
        serde_json::from_str(raw.trim()).map_err(|e| format!("unusable judge answer: {e}"))?;
    let object = answer
        .as_object()
        .ok_or_else(|| "unusable judge answer: not a JSON object".to_string())?;
    let boolean = |name: &str| -> Result<bool, String> {
        match object.get(name) {
            Some(Value::Bool(value)) => Ok(*value),
            Some(other) => Err(format!(
                "{name} must be a JSON boolean, got {}",
                kind_of(other)
            )),
            None => Err(format!("unusable judge answer: no {name}")),
        }
    };
    Ok(Verdict {
        off_track: boolean("off_track")?,
        self_correcting: boolean("self_correcting")?,
        reason: match object.get("reason") {
            None | Some(Value::Null) => String::new(),
            Some(Value::String(text)) => text.clone(),
            Some(other) => other.to_string(),
        },
    })
}

fn kind_of(value: &Value) -> &'static str {
    match value {
        Value::Null => "null",
        Value::Bool(_) => "boolean",
        Value::Number(_) => "number",
        Value::String(_) => "string",
        Value::Array(_) => "array",
        Value::Object(_) => "object",
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::http::tests::serve_once;

    fn model(endpoint: Endpoint) -> Model {
        Model {
            name: "test-model".to_string(),
            endpoint,
            bearer: None,
            call_timeout: Duration::from_secs(5),
        }
    }

    fn canned(content: &str, finish: &str) -> String {
        let body = json!({
            "model": "test-model-2026",
            "choices": [{"finish_reason": finish, "message": {"role": "assistant", "content": content}}]
        })
        .to_string();
        format!(
            "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{body}",
            body.len()
        )
    }

    #[test]
    fn the_judge_sends_its_instructions_and_the_prompt_and_reads_the_verdict() {
        let reply = canned(
            "{\"off_track\": true, \"self_correcting\": false, \"reason\": \"editing blind\"}",
            "stop",
        );
        let (endpoint, requests) = serve_once(Box::leak(reply.into_boxed_str()));
        let (verdict, call) = model(endpoint).judge("PROMPT").unwrap();
        assert_eq!(
            verdict,
            Verdict {
                off_track: true,
                self_correcting: false,
                reason: "editing blind".to_string()
            }
        );
        assert_eq!(call.response_model.as_deref(), Some("test-model-2026"));
        assert_eq!(call.finish_reason.as_deref(), Some("stop"));
        assert_eq!(call.max_tokens_sent, JUDGE_MAX_TOKENS);
        let request = String::from_utf8(requests.recv().unwrap()).unwrap();
        let body: Value = serde_json::from_str(request.split("\r\n\r\n").nth(1).unwrap()).unwrap();
        assert_eq!(body["model"], "test-model");
        assert_eq!(body["max_tokens"], JUDGE_MAX_TOKENS);
        assert_eq!(body["messages"][0]["role"], "system");
        assert_eq!(body["messages"][0]["content"], JUDGE_INSTRUCTIONS);
        assert_eq!(body["messages"][1]["content"], "PROMPT");
    }

    #[test]
    fn a_verdict_field_that_is_not_a_json_boolean_is_unusable_not_coerced() {
        for raw in [
            "{\"off_track\": \"false\", \"self_correcting\": false}",
            "{\"off_track\": 1, \"self_correcting\": false}",
            "{\"self_correcting\": false}",
            "not json",
            "[true, false]",
        ] {
            assert!(parse_verdict(raw).is_err(), "{raw} was accepted");
        }
        assert_eq!(
            parse_verdict(" {\"off_track\": false, \"self_correcting\": true} \n").unwrap(),
            Verdict {
                off_track: false,
                self_correcting: true,
                reason: String::new()
            }
        );
    }

    #[test]
    fn a_ceiling_hit_with_no_content_is_a_failure_that_keeps_the_call_record() {
        let body = json!({
            "model": "m",
            "choices": [{"finish_reason": "length", "message": {"role": "assistant", "content": null}}]
        })
        .to_string();
        let reply = format!(
            "HTTP/1.1 200 OK\r\nContent-Length: {}\r\n\r\n{body}",
            body.len()
        );
        let (endpoint, _requests) = serve_once(Box::leak(reply.into_boxed_str()));
        let failed = model(endpoint).judge("PROMPT").unwrap_err();
        assert!(failed.reason.contains("no text"));
        let call = failed.call.unwrap();
        assert_eq!(call.finish_reason.as_deref(), Some("length"));
        assert_eq!(call.raw, None);
    }

    #[test]
    fn a_non_2xx_answer_is_a_failure_without_a_call_record() {
        let (endpoint, _requests) =
            serve_once("HTTP/1.1 401 Unauthorized\r\nContent-Length: 9\r\n\r\nno access");
        let failed = model(endpoint).write("PROMPT").unwrap_err();
        assert!(failed.reason.contains("HTTP 401"));
        assert!(failed.call.is_none());
    }

    #[test]
    fn the_writer_returns_the_text_as_is() {
        let reply = canned("Maybe look at the error before editing?", "stop");
        let (endpoint, _requests) = serve_once(Box::leak(reply.into_boxed_str()));
        let (text, call) = model(endpoint).write("PROMPT").unwrap();
        assert_eq!(text, "Maybe look at the error before editing?");
        assert_eq!(call.purpose, "writer");
        assert_eq!(call.max_tokens_sent, WRITER_MAX_TOKENS);
    }
}
