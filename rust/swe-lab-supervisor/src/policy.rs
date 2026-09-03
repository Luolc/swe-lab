//! When to speak: the gates between a boundary and a correction.
//!
//! One boundary is judged as a pure function of a snapshot — the evidence
//! window, the task, what has been said, and the gate state the loop holds
//! (budget left, cooldown satisfied) — so that it can run on its own thread
//! while the loop keeps consuming events. What the loop then does with the
//! decision (deliver, or discard as stale) is the loop's; the budget is spent
//! only on delivery.
//!
//! **A boundary with no evidence is not judged at all.** Before any gate, an
//! empty window is [`Decision::Unjudged`]: there is nothing for the judge to
//! measure against the criterion, so asking it yields an answer about a
//! record it was never shown. Past that, the order is: the judge says off
//! track, else silent; the judge says it will not self-correct, else silent;
//! the would-have-spoken marker is recorded, before any budget is consulted;
//! budget left, else silent; cooldown satisfied, else silent; the writer
//! produces a usable line, else a lapse bounded to this boundary. A failed
//! judge call is a lapse too, and neither call is ever retried.

use crate::model::{Call, Model};
use crate::prompt::{self, MAX_INTERVENTION_CHARS, Observation};

/// What the loop tells the boundary about its own state.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Gates {
    /// Whether a correction may still be spent.
    pub budget_left: bool,
    /// Whether enough boundaries have passed since the last correction.
    pub cooldown_satisfied: bool,
}

/// What one boundary decided.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Decision {
    /// No decision was taken, for the reason given: the window was empty.
    Unjudged(String),
    /// Judged, and nothing to say.
    Silent,
    /// Judged off track and unlikely to recover, and a line was written.
    /// Whether it is delivered is the loop's call.
    Speak(String),
    /// A model call failed or produced an unusable answer. Bounded to this
    /// boundary: the next one is judged normally.
    Lapse(String),
}

/// The result of judging one boundary.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Judged {
    /// The decision.
    pub decision: Decision,
    /// The judge's reason when it found a deviation the actor was not
    /// correcting — the would-have-spoken marker, recorded whether or not
    /// speech followed (the zero-budget control arm produces only these).
    pub marker: Option<String>,
    /// Every model call made, in order, for the log row.
    pub calls: Vec<Call>,
}

/// Judge one boundary.
#[must_use]
pub fn judge_boundary(
    model: &Model,
    criterion: &str,
    observation: &Observation<'_>,
    gates: Gates,
) -> Judged {
    let mut calls = Vec::new();
    if observation.evidence.is_empty() {
        return Judged {
            decision: Decision::Unjudged("no actor evidence in the window".to_string()),
            marker: None,
            calls,
        };
    }
    let verdict = match model.judge(&prompt::judge_prompt(observation, criterion)) {
        Ok((verdict, call)) => {
            calls.push(call);
            verdict
        }
        Err(failed) => {
            calls.extend(failed.call.map(|call| *call));
            return Judged {
                decision: Decision::Lapse(format!("judge call failed: {}", failed.reason)),
                marker: None,
                calls,
            };
        }
    };
    if !verdict.off_track || verdict.self_correcting {
        return Judged {
            decision: Decision::Silent,
            marker: None,
            calls,
        };
    }
    let marker = Some(verdict.reason);
    if !gates.budget_left || !gates.cooldown_satisfied {
        return Judged {
            decision: Decision::Silent,
            marker,
            calls,
        };
    }
    let decision = match model.write(&prompt::writer_prompt(observation, criterion)) {
        Ok((text, call)) => {
            calls.push(call);
            match intervention(text) {
                Ok(text) => Decision::Speak(text),
                Err(reason) => Decision::Lapse(format!("writer produced no usable line: {reason}")),
            }
        }
        Err(failed) => {
            calls.extend(failed.call.map(|call| *call));
            Decision::Lapse(format!("writer produced no usable line: {}", failed.reason))
        }
    };
    Judged {
        decision,
        marker,
        calls,
    }
}

/// Apply the intervention's bounds: not blank, not over the cap. Rejected
/// rather than truncated, so a policy cannot ship half a sentence.
fn intervention(text: String) -> Result<String, String> {
    if text.trim().is_empty() {
        return Err("an intervention may not be empty".to_string());
    }
    let length = text.chars().count();
    if length > MAX_INTERVENTION_CHARS {
        return Err(format!("{length} chars > {MAX_INTERVENTION_CHARS}"));
    }
    Ok(text)
}

#[cfg(test)]
mod tests {
    use std::io::Write;
    use std::net::TcpListener;
    use std::sync::{Arc, Mutex};
    use std::thread;
    use std::time::Duration;

    use serde_json::json;

    use super::*;
    use crate::config::Endpoint;
    use crate::evidence::{Block, Message, Role};
    use crate::http::tests::read_request;

    /// A loopback model that answers each request from a script and records
    /// how many requests it saw.
    fn scripted(answers: Vec<&'static str>) -> (Model, Arc<Mutex<usize>>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let seen = Arc::new(Mutex::new(0));
        let counter = Arc::clone(&seen);
        let _server = thread::spawn(move || {
            for content in answers {
                let (mut socket, _) = listener.accept().unwrap();
                let _request = read_request(&mut socket);
                *counter.lock().unwrap() += 1;
                let body = json!({"model": "m", "choices": [{"finish_reason": "stop",
                    "message": {"content": content}}]})
                .to_string();
                let reply = format!(
                    "HTTP/1.1 200 OK\r\nContent-Length: {}\r\n\r\n{body}",
                    body.len()
                );
                socket.write_all(reply.as_bytes()).unwrap();
            }
        });
        (
            Model {
                name: "m".to_string(),
                endpoint: Endpoint {
                    address: std::net::SocketAddr::from(([127, 0, 0, 1], port)),
                    path: "/v1/chat/completions".to_string(),
                },
                bearer: None,
                call_timeout: Duration::from_secs(5),
            },
            seen,
        )
    }

    const OFF: &str =
        "{\"off_track\": true, \"self_correcting\": false, \"reason\": \"blind edit\"}";
    const RECOVERING: &str = "{\"off_track\": true, \"self_correcting\": true}";
    const FINE: &str = "{\"off_track\": false, \"self_correcting\": false}";
    const OPEN: Gates = Gates {
        budget_left: true,
        cooldown_satisfied: true,
    };

    fn evidence() -> Vec<Message> {
        vec![Message {
            role: Role::Assistant,
            blocks: vec![Block::Text("editing".to_string())],
        }]
    }

    fn observe(evidence: &[Message]) -> Observation<'_> {
        Observation {
            task: "task",
            evidence,
            said: &[],
        }
    }

    #[test]
    fn an_empty_evidence_window_never_consults_the_judge() {
        // The named test of the invariant: no request reaches the model.
        let (model, seen) = scripted(vec![OFF]);
        let judged = judge_boundary(&model, "C", &observe(&[]), OPEN);
        assert!(
            matches!(judged.decision, Decision::Unjudged(ref why) if why.contains("no actor evidence"))
        );
        assert!(judged.calls.is_empty());
        thread::sleep(Duration::from_millis(100));
        assert_eq!(*seen.lock().unwrap(), 0);
    }

    #[test]
    fn off_track_and_not_recovering_with_open_gates_speaks() {
        let (model, seen) = scripted(vec![OFF, "Worth a look at the failure first?"]);
        let evidence = evidence();
        let judged = judge_boundary(&model, "C", &observe(&evidence), OPEN);
        assert_eq!(
            judged.decision,
            Decision::Speak("Worth a look at the failure first?".to_string())
        );
        assert_eq!(judged.marker.as_deref(), Some("blind edit"));
        assert_eq!(judged.calls.len(), 2);
        assert_eq!(*seen.lock().unwrap(), 2);
    }

    #[test]
    fn on_track_or_recovering_is_silent_without_a_marker_or_a_writer_call() {
        for answer in [FINE, RECOVERING] {
            let (model, seen) = scripted(vec![answer]);
            let evidence = evidence();
            let judged = judge_boundary(&model, "C", &observe(&evidence), OPEN);
            assert_eq!(judged.decision, Decision::Silent);
            assert_eq!(judged.marker, None);
            assert_eq!(judged.calls.len(), 1);
            assert_eq!(*seen.lock().unwrap(), 1);
        }
    }

    #[test]
    fn a_closed_budget_or_cooldown_records_the_marker_and_stays_silent() {
        for gates in [
            Gates {
                budget_left: false,
                cooldown_satisfied: true,
            },
            Gates {
                budget_left: true,
                cooldown_satisfied: false,
            },
        ] {
            let (model, seen) = scripted(vec![OFF]);
            let evidence = evidence();
            let judged = judge_boundary(&model, "C", &observe(&evidence), gates);
            assert_eq!(judged.decision, Decision::Silent);
            assert_eq!(judged.marker.as_deref(), Some("blind edit"));
            assert_eq!(*seen.lock().unwrap(), 1, "the writer must not be asked");
        }
    }

    #[test]
    fn a_bad_judge_answer_or_an_unusable_line_is_a_lapse_never_a_retry() {
        let (model, seen) = scripted(vec!["not json"]);
        let evidence = evidence();
        let judged = judge_boundary(&model, "C", &observe(&evidence), OPEN);
        assert!(
            matches!(judged.decision, Decision::Lapse(ref why) if why.starts_with("judge call failed"))
        );
        assert_eq!(judged.calls.len(), 1, "the failed call is still on record");
        assert_eq!(*seen.lock().unwrap(), 1);

        let long: &'static str = Box::leak("x".repeat(401).into_boxed_str());
        let (model, seen) = scripted(vec![OFF, long]);
        let judged = judge_boundary(&model, "C", &observe(&evidence), OPEN);
        assert!(matches!(judged.decision, Decision::Lapse(ref why) if why.contains("401 chars")));
        assert_eq!(judged.marker.as_deref(), Some("blind edit"));
        assert_eq!(*seen.lock().unwrap(), 2);

        let (model, _) = scripted(vec![OFF, "   \n"]);
        let judged = judge_boundary(&model, "C", &observe(&evidence), OPEN);
        assert!(matches!(judged.decision, Decision::Lapse(ref why) if why.contains("empty")));
    }
}
