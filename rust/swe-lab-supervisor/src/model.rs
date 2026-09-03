//! The two model calls: the judge, and the writer.
//!
//! Both go to the one configured endpoint in the Anthropic Messages shape,
//! and both record what answered them — the model the **response**
//! reports (an alias re-pointed upstream leaves the request looking correct),
//! the sampling actually sent, the raw text, and the `stop_reason` (under the
//! account's existing `finish_reason` key), so a
//! token ceiling hit is distinguishable from an unparseable answer in the
//! account — and neither is ever retried: a second ask would make the verdict
//! a function of how many times we asked.

use std::sync::Arc;
use std::time::{Duration, Instant};

use serde_json::{Value, json};

use crate::config::Endpoint;
use crate::http;
use crate::prompt::{JUDGE_INSTRUCTIONS, WRITER_INSTRUCTIONS};
use crate::signals::Stop;

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
    /// The response's `stop_reason`, when reported.
    pub finish_reason: Option<String>,
    /// The answer's text before parsing, or `None` when the choice carried no
    /// string content — or when no answer came.
    pub raw: Option<String>,
    /// How long the call took.
    pub took: Duration,
    /// Why the call produced no answer to parse, when it did not: transport,
    /// a non-2xx status, a body that is not JSON. Every attempted call is
    /// on record; this is the record of one that failed.
    pub error: Option<String>,
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
            "error": self.error,
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
/// attempt — there is always one, since the record is made before the
/// request is sent.
#[derive(Debug)]
pub struct Failed {
    /// Why the answer could not be used.
    pub reason: String,
    /// The call's record. Boxed so the error variant stays small on the
    /// happy path.
    pub call: Box<Call>,
}

/// How to reach the model.
#[derive(Debug, Clone)]
pub struct Model {
    /// The model name sent on every request.
    pub name: String,
    /// Where requests go.
    pub endpoint: Endpoint,
    /// The API key, or none.
    pub api_key: Option<String>,
    /// The environment variable that supplied the key, scrubbed from actor.
    pub api_key_env: String,
    /// The bound on one call, connection included.
    pub call_timeout: Duration,
    /// The wrapper's stop flag: a call in progress returns as cancelled
    /// once it is raised, and nothing is asked after that.
    pub stop: Arc<Stop>,
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
                call: Box::new(call),
            });
        };
        match parse_verdict(raw) {
            Ok(verdict) => Ok((verdict, call)),
            Err(reason) => Err(Failed {
                reason,
                call: Box::new(call),
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
                call: Box::new(call),
            }),
        }
    }

    /// `text` with every exact occurrence of the API key replaced. Nothing
    /// derived from a response — the answer's text, a model name, an error
    /// body — enters a record, a log row or the actor's stdin before it has
    /// been through here: an endpoint that reflects `x-api-key` would
    /// otherwise write the credential into the artifacts.
    fn redact(&self, text: &str) -> String {
        match self.api_key.as_deref() {
            Some(api_key) if !api_key.is_empty() => text.replace(api_key, "[REDACTED]"),
            _ => text.to_string(),
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
            "system": instructions,
            "messages": [{"role": "user", "content": prompt}],
        });
        // The record exists before the request: an attempt that fails is
        // still an attempt, and the row shows it.
        let started = Instant::now();
        let mut call = Call {
            purpose,
            requested_model: self.name.clone(),
            response_model: None,
            max_tokens_sent: max_tokens,
            finish_reason: None,
            raw: None,
            took: Duration::ZERO,
            error: None,
        };
        let failed = |mut call: Call, reason: String| {
            call.took = started.elapsed();
            call.error = Some(reason.clone());
            Failed {
                reason,
                call: Box::new(call),
            }
        };
        // Every string that comes back from the exchange — an error, a
        // body — is redacted whole before anything is cut from it: a
        // credential straddling a cut would leave a fragment that the
        // exact-match redaction of the excerpt could not see.
        let response = match http::post_json(
            &self.endpoint,
            self.api_key.as_deref(),
            payload.to_string().as_bytes(),
            started + self.call_timeout,
            &self.stop,
        ) {
            Ok(response) => response,
            Err(reason) => {
                return Err(failed(call, self.redact(&format!("call failed: {reason}"))));
            }
        };
        call.took = started.elapsed();
        if response.status / 100 != 2 {
            let excerpt: String = self
                .redact(&String::from_utf8_lossy(&response.body))
                .chars()
                .take(300)
                .collect();
            return Err(failed(
                call,
                format!("answered HTTP {}: {excerpt}", response.status),
            ));
        }
        let body: Value = match serde_json::from_slice(&response.body) {
            Ok(body) => body,
            Err(e) => {
                return Err(failed(
                    call,
                    self.redact(&format!("answered non-JSON: {e}")),
                ));
            }
        };
        let text = |value: Option<&Value>| value.and_then(Value::as_str).map(|t| self.redact(t));
        call.response_model = text(body.get("model"));
        call.finish_reason = text(body.get("stop_reason"));
        call.raw = text(
            body.get("content")
                .and_then(|c| c.get(0))
                .and_then(|b| b.get("text")),
        );
        Ok(call)
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
            api_key: None,
            api_key_env: "TEST_API_KEY".to_string(),
            call_timeout: Duration::from_secs(5),
            stop: Arc::new(std::sync::atomic::AtomicUsize::new(0)),
        }
    }

    fn canned(content: &str, stop_reason: &str) -> String {
        let body = json!({
            "model": "test-model-2026",
            "content": [{"type": "text", "text": content}],
            "stop_reason": stop_reason,
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
        assert_eq!(body["system"], JUDGE_INSTRUCTIONS);
        assert_eq!(
            body["messages"],
            json!([{"role": "user", "content": "PROMPT"}])
        );
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
            "content": [],
            "stop_reason": "max_tokens"
        })
        .to_string();
        let reply = format!(
            "HTTP/1.1 200 OK\r\nContent-Length: {}\r\n\r\n{body}",
            body.len()
        );
        let (endpoint, _requests) = serve_once(Box::leak(reply.into_boxed_str()));
        let failed = model(endpoint).judge("PROMPT").unwrap_err();
        assert!(failed.reason.contains("no text"));
        let call = *failed.call;
        assert_eq!(call.finish_reason.as_deref(), Some("max_tokens"));
        assert_eq!(call.raw, None);
    }

    const API_KEY: &str = "sk-REFLECTED-SECRET-MUST-NOT-BE-RECORDED";

    fn with_api_key(endpoint: Endpoint) -> Model {
        Model {
            api_key: Some(API_KEY.to_string()),
            ..model(endpoint)
        }
    }

    /// An endpoint that reflects the request's `x-api-key` header in an
    /// error body would write the credential into the lapse's reason and
    /// the call's record; both carry the redaction instead. And the attempt
    /// is on record — the row shows a request was made, and why it gave no
    /// answer.
    #[test]
    fn a_non_2xx_answer_is_a_failure_with_the_attempt_on_record_and_the_api_key_redacted() {
        let body = format!("denied: {API_KEY} is not valid here");
        let reply = format!(
            "HTTP/1.1 401 Unauthorized\r\nContent-Length: {}\r\n\r\n{body}",
            body.len()
        );
        let (endpoint, _requests) = serve_once(Box::leak(reply.into_boxed_str()));
        let failed = with_api_key(endpoint).write("PROMPT").unwrap_err();
        assert!(failed.reason.contains("HTTP 401"), "{}", failed.reason);
        assert!(failed.reason.contains("[REDACTED]"), "{}", failed.reason);
        assert!(!failed.reason.contains(API_KEY), "{}", failed.reason);
        let call = *failed.call;
        assert_eq!(call.purpose, "writer");
        assert_eq!(call.raw, None);
        let error = call.error.as_deref().expect("the attempt's error");
        assert!(
            error.contains("HTTP 401") && !error.contains(API_KEY),
            "{error}"
        );
        assert!(!call.to_json().to_string().contains(API_KEY));
    }

    /// A fragment of the credential is as much a leak as the whole of it,
    /// and an exact-match redaction applied after a cut cannot see one. So
    /// the redaction comes before any cut: an API key straddling the
    /// excerpt's 300-character boundary, or placed where the response
    /// parser fails, leaves neither the token nor a fragment of it in the
    /// reason, the call's record, or its serialized row.
    #[test]
    fn no_fragment_of_the_api_key_survives_a_cut_or_a_malformed_response() {
        let fragment = &API_KEY[..10];
        let straddling = format!("{}{API_KEY} more", "x".repeat(295));
        let replies = [
            format!(
                "HTTP/1.1 401 Unauthorized\r\nContent-Length: {}\r\n\r\n{straddling}",
                straddling.len()
            ),
            format!("HTTP/1.1 {API_KEY} Unauthorized\r\n\r\n"),
            format!(
                "HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n{API_KEY}\r\nbody\r\n0\r\n\r\n"
            ),
        ];
        for reply in replies {
            let (endpoint, _requests) = serve_once(Box::leak(reply.into_boxed_str()));
            let failed = with_api_key(endpoint).write("PROMPT").unwrap_err();
            let call = *failed.call;
            for text in [
                failed.reason.clone(),
                call.error.clone().unwrap_or_default(),
                call.to_json().to_string(),
            ] {
                assert!(!text.contains(fragment), "{text}");
            }
        }
        // The body is redacted whole, then cut: an API key inside the excerpt
        // shows as the marker (one straddling the cut shows as what is left
        // of the marker, which is no fragment of the token either).
        let inside = format!("{}{API_KEY} more", "x".repeat(250));
        let reply = format!(
            "HTTP/1.1 401 Unauthorized\r\nContent-Length: {}\r\n\r\n{inside}",
            inside.len()
        );
        let (endpoint, _requests) = serve_once(Box::leak(reply.into_boxed_str()));
        let failed = with_api_key(endpoint).write("PROMPT").unwrap_err();
        assert!(failed.reason.contains("[REDACTED]"), "{}", failed.reason);
    }

    /// A successful answer that reflects the credential is redacted before
    /// it becomes text anything downstream sees — the log row, the actor's
    /// stdin, the next prompt.
    #[test]
    fn a_reflected_api_key_in_an_answer_never_reaches_the_text() {
        let reply = canned(&format!("Use {API_KEY} to log in."), "stop");
        let (endpoint, _requests) = serve_once(Box::leak(reply.into_boxed_str()));
        let (text, call) = with_api_key(endpoint).write("PROMPT").unwrap();
        assert_eq!(text, "Use [REDACTED] to log in.");
        assert_eq!(call.raw.as_deref(), Some("Use [REDACTED] to log in."));
    }

    /// A request that never reached a server is an attempt too, with its
    /// reason on the record.
    #[test]
    fn a_call_that_could_not_connect_is_on_record() {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        drop(listener);
        let endpoint = Endpoint {
            address,
            path: "/v1/messages".to_string(),
        };
        let failed = model(endpoint).judge("PROMPT").unwrap_err();
        assert!(
            failed.reason.starts_with("call failed"),
            "{}",
            failed.reason
        );
        let call = *failed.call;
        assert_eq!(call.purpose, "judge");
        assert!(call.error.is_some());
        assert_eq!(call.raw, None);
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
