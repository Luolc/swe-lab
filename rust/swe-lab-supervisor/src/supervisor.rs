//! The loop: consume the actor's stream, judge at boundaries, deliver what
//! is still fresh, end the run deliberately, and account for all of it.
//!
//! One thread owns everything here — the evidence, the policy state, the
//! actor's stdin, the log. Two things happen elsewhere and report back
//! through one bounded queue: the actor's readers (`actor.rs`), and at most
//! **one** judgment at a time, run on its own thread so that draining never
//! waits on a model call. While a judgment is in flight the loop keeps
//! consuming; when the judgment returns, its evidence revision is compared
//! with the current one and a correction newer admitted evidence overtook is
//! recorded as stale and never delivered. A boundary that falls while one is
//! in flight is recorded as unjudged and marks a pending latest boundary; one
//! judgment then starts on the current snapshot, not one per skipped
//! boundary — a latest-value channel, not a queue of prefixes.
//!
//! **Boundaries.** One falls at every `N`-th admitted assistant message since
//! the last boundary, and at every actor `result` event with admitted
//! evidence newer than the last judgment: the turn's end is the last moment
//! a correction can reach the actor before its stdin decides the run's fate.
//!
//! **Ending.** A quiet `result` — no judgment pending and nothing delivered —
//! closes the actor's stdin, and a cooperative actor exits on the EOF. A
//! correction delivered at a `result` keeps the actor open for another turn.
//! The loop ends when stdout closes and no judgment is in flight; when a log
//! stops taking output; when the wrapper is told to stop; or when the leader
//! has exited and, a descendant still holding its stdout, the grace has
//! passed. Then the actor's process group is ended, stragglers swept, the
//! drains finished, and the summary written.
//!
//! **Failure semantics.** A failed judge or writer call is one lapse; the
//! next boundary is judged normally. A failed stdin write is a gap: the loop
//! stops speaking, keeps accounting, and the run is not evidence about
//! supervision. So is an unclean ending — a drain that stopped with an error
//! or did not finish, a log that could not be written, a signal that failed.

use std::ffi::OsString;
use std::fs::File;
use std::io::{self, BufWriter, Write};
use std::path::PathBuf;
use std::process::ExitStatus;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{Receiver, RecvTimeoutError, SyncSender};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use serde_json::{Map, Value, json};

use crate::actor::{self, Actor, Event, Gate};
use crate::config::{self, Blocking, Config};
use crate::evidence::{self, Disposition, INTERVENTION_TAG, Message, Role};
use crate::model::{Call, Model};
use crate::policy::{self, Decision, Gates, Judged};
use crate::prompt::Observation;
use crate::stream::user_event_line;
use crate::summary::{self, Summary, SupervisorExit};

/// How long the loop sleeps between checks of the stop flag and the leader
/// when no event arrives.
const TICK: Duration = Duration::from_millis(100);

/// The word every row carries; the one policy this binary implements.
const POLICY_NAME: &str = "speak-when-off-track";

/// Where the run's files go.
#[derive(Debug, Clone)]
pub struct Paths {
    /// The actor's stdout, line by line.
    pub actor_event_log: PathBuf,
    /// The supervisor's account, one JSON object per line.
    pub supervisor_log: PathBuf,
    /// The actor's stderr.
    pub actor_stderr: PathBuf,
}

/// What the actor is launched with.
#[derive(Debug, Clone, Copy)]
pub struct Launch<'a> {
    /// The command, as opaque tokens.
    pub argv: &'a [OsString],
    /// The first bytes on the actor's stdin, verbatim — the actor's prompt,
    /// in whatever framing the actor takes. The wrapper does not read them.
    pub prompt: &'a [u8],
}

/// How a supervised run ended.
#[derive(Debug)]
pub struct Ended {
    /// The actor's exit status, when it was reaped.
    pub status: Option<ExitStatus>,
    /// The account of the run.
    pub summary: Summary,
}

/// What the loop receives.
enum Msg {
    Actor(Event),
    Judged(u64, Judged),
}

/// One judgment in flight.
struct InFlight {
    ordinal: u64,
    cursor: u64,
    revision: u64,
    disposition: Disposition,
    started: Instant,
    blocking_note: Option<String>,
}

/// What arrived while a judgment was in flight and waits on its completion.
#[derive(Debug, Default)]
struct Pending {
    /// A boundary fell: one judgment starts on the current snapshot.
    boundary: bool,
    /// The actor emitted a `result`: the turn's fate is decided once the
    /// judgment (and any pending boundary) is in.
    result: bool,
}

#[derive(Debug, Default)]
struct Counts {
    events: u64,
    undecodable: u64,
    oversized: u64,
    boundaries: u64,
    corrections: u64,
    silent: u64,
    unjudged: u64,
    lapses: u64,
    gaps: u64,
    stale: u64,
    max_lag: Duration,
}

struct Loop {
    config: Config,
    criterion_text: &'static str,
    model: Arc<Model>,
    actor: Actor,
    gate: Arc<Gate>,
    outbox: SyncSender<Msg>,
    inbox: Receiver<Msg>,
    log: BufWriter<File>,
    cursor: u64,
    evidence: Vec<Message>,
    revision: u64,
    said: Vec<String>,
    assistant_since_boundary: u32,
    last_judged_revision: u64,
    boundary_ordinal: u64,
    spoken_at: Vec<u64>,
    last_disposition: Disposition,
    in_flight: Option<InFlight>,
    pending: Pending,
    stdout_closed: bool,
    /// The leader was seen exited, at this instant; its stdout may still be
    /// held by a descendant.
    leader_exited_at: Option<Instant>,
    /// A drain stopped with an error: the run ends as soon as no judgment
    /// is in flight.
    faulted: bool,
    mute: bool,
    unclean: Option<String>,
    counts: Counts,
}

/// Run one supervised actor to its end.
///
/// # Errors
///
/// The run could not start: the supervisor log could not be created, the
/// actor could not be launched, or it did not take its prompt. Nothing has
/// been supervised; the caller records a refusal.
pub fn run(
    config: Config,
    criterion_text: &'static str,
    criterion_sha256: &str,
    model: Model,
    launch: Launch<'_>,
    paths: &Paths,
    stop: &AtomicBool,
) -> Result<Ended, String> {
    let log = File::create(&paths.supervisor_log)
        .map_err(|e| format!("supervisor log {}: {e}", paths.supervisor_log.display()))?;
    let (outbox, inbox) = actor::event_queue();
    let command = actor::command(launch.argv, &[config::BASE_URL_ENV, config::API_KEY_ENV])
        .map_err(|e| format!("actor command: {e}"))?;
    let limits = actor::Limits {
        line: usize::try_from(config.limits.max_event_line_bytes.get())
            .map_err(|_| "limits.max_event_line_bytes does not fit".to_string())?,
        stdout: config.limits.max_actor_stdout_bytes.get(),
        stderr: config.limits.max_actor_stderr_bytes.get(),
    };
    let relay = outbox.clone();
    let actor = Actor::spawn(
        command,
        &paths.actor_event_log,
        &paths.actor_stderr,
        limits,
        move |event| {
            // The loop being gone means the wrapper is already on its way out.
            let _ = relay.send(Msg::Actor(event));
        },
    )
    .map_err(|e| format!("launching the actor: {e}"))?;
    let gate = actor.gate();
    let mut run = Loop {
        config,
        criterion_text,
        model: Arc::new(model),
        actor,
        gate,
        outbox,
        inbox,
        log: BufWriter::new(log),
        cursor: 0,
        evidence: Vec::new(),
        revision: 0,
        said: Vec::new(),
        assistant_since_boundary: 0,
        last_judged_revision: 0,
        boundary_ordinal: 0,
        spoken_at: Vec::new(),
        last_disposition: Disposition::ExcludedNothingToKeep,
        in_flight: None,
        pending: Pending::default(),
        stdout_closed: false,
        leader_exited_at: None,
        faulted: false,
        mute: false,
        unclean: None,
        counts: Counts::default(),
    };
    let grace = Duration::from_millis(run.config.timeouts.term_grace_ms);
    if let Err(error) = run.actor.write_stdin(launch.prompt) {
        drop(run.inbox);
        let ended = run
            .actor
            .end(grace)
            .map_err(|e| format!("ending the actor: {e}"))?;
        return Err(format!(
            "the actor did not take its prompt ({error}); it ended with {}",
            ended.status
        ));
    }
    let terminated = run.serve(stop);
    Ok(run.finish(terminated, criterion_sha256, paths))
}

impl Loop {
    /// Consume until the run is over and no judgment is in flight, or until
    /// the wrapper is told to stop. Returns whether it was stopped.
    fn serve(&mut self, stop: &AtomicBool) -> bool {
        let grace = Duration::from_millis(self.config.timeouts.term_grace_ms);
        loop {
            if stop.load(Ordering::Relaxed) {
                return true;
            }
            match self.inbox.recv_timeout(TICK) {
                Ok(Msg::Actor(Event::Line(line))) => self.consume_line(&line),
                Ok(Msg::Actor(Event::Oversized)) => self.counts.oversized += 1,
                Ok(Msg::Actor(Event::StdoutClosed(result))) => {
                    self.stdout_closed = true;
                    if let Err(error) = result {
                        self.fault(format!("the actor's stdout: {error}"));
                    }
                }
                Ok(Msg::Actor(Event::StderrClosed(result))) => {
                    if let Err(error) = result {
                        self.fault(format!("the actor's stderr: {error}"));
                    }
                }
                Ok(Msg::Judged(ordinal, judged)) => self.complete(ordinal, judged),
                Err(RecvTimeoutError::Timeout) => {}
                Err(RecvTimeoutError::Disconnected) => {
                    self.fault("the event queue closed".to_string());
                }
            }
            if self.leader_exited_at.is_none() {
                match self.actor.exited() {
                    Ok(true) => self.leader_exited_at = Some(Instant::now()),
                    Ok(false) => {}
                    Err(error) => self.fault(format!("observing the actor: {error}")),
                }
            }
            let over = self.stdout_closed
                || self.faulted
                || self
                    .leader_exited_at
                    .is_some_and(|at| at.elapsed() >= grace);
            if over && self.in_flight.is_none() {
                return false;
            }
        }
    }

    /// The run's record is not whole: end it as soon as the judgment in
    /// flight, if any, is in.
    fn fault(&mut self, reason: String) {
        self.faulted = true;
        self.unclean.get_or_insert(reason);
    }

    fn consume_line(&mut self, line: &[u8]) {
        let Ok(event @ Value::Object(_)) = serde_json::from_slice::<Value>(line) else {
            self.counts.undecodable += 1;
            return;
        };
        self.cursor += 1;
        self.counts.events += 1;
        let (record, disposition) = evidence::admit(evidence::event_to_message(&event));
        self.last_disposition = disposition;
        if let Some(record) = record {
            if record.role == Role::Assistant {
                self.assistant_since_boundary += 1;
            }
            self.evidence.push(record);
            self.revision += 1;
        }
        let is_result = event.get("type").and_then(Value::as_str) == Some("result");
        let due = self.assistant_since_boundary
            >= self.config.policy.judge_every_n_assistant_messages.get()
            || (is_result && self.revision > self.last_judged_revision);
        if due {
            self.assistant_since_boundary = 0;
            if self.in_flight.is_some() {
                self.boundary_ordinal += 1;
                self.counts.boundaries += 1;
                self.counts.unjudged += 1;
                self.pending.boundary = true;
                let mut row = self.row("unjudged", disposition);
                row.insert("boundary".into(), json!(self.boundary_ordinal));
                row.insert(
                    "reason".into(),
                    json!("a judgment was in flight; superseded by the next boundary"),
                );
                self.write_row(row);
            } else {
                self.start_boundary(disposition);
            }
        } else {
            let row = self.row("observed", disposition);
            self.write_row(row);
        }
        if is_result {
            if self.in_flight.is_some() {
                self.pending.result = true;
            } else {
                // A quiet result: the deliberate end of the run.
                self.actor.close_stdin();
            }
        }
    }

    fn start_boundary(&mut self, disposition: Disposition) {
        self.boundary_ordinal += 1;
        self.counts.boundaries += 1;
        self.last_judged_revision = self.revision;
        let window = usize::try_from(self.config.policy.window.get()).unwrap_or(usize::MAX);
        let start = self.evidence.len().saturating_sub(window);
        let evidence = self.evidence[start..].to_vec();
        let said = self.said.clone();
        let task = self.config.task.clone();
        let ordinal = self.boundary_ordinal;
        let cooldown = u64::from(self.config.policy.cooldown);
        let gates = Gates {
            budget_left: self.spoken_at.len() < self.config.policy.budget as usize,
            cooldown_satisfied: self
                .spoken_at
                .last()
                .is_none_or(|&last| ordinal - last >= cooldown),
        };
        let blocking_note = match self.config.policy.block_actor_while_judging {
            Blocking::Off => None,
            Blocking::Stdout => {
                self.gate.close();
                None
            }
            Blocking::Sigstop => self
                .actor
                .freeze()
                .err()
                .map(|e| format!("SIGSTOP failed, judged unblocked: {e}")),
        };
        self.in_flight = Some(InFlight {
            ordinal,
            cursor: self.cursor,
            revision: self.revision,
            disposition,
            started: Instant::now(),
            blocking_note,
        });
        let model = Arc::clone(&self.model);
        let criterion = self.criterion_text;
        let outbox = self.outbox.clone();
        let _judge = thread::spawn(move || {
            let observation = Observation {
                task: &task,
                evidence: &evidence,
                said: &said,
            };
            let judged = policy::judge_boundary(&model, criterion, &observation, gates);
            let _ = outbox.send(Msg::Judged(ordinal, judged));
        });
    }

    fn complete(&mut self, ordinal: u64, judged: Judged) {
        let Some(boundary) = self.in_flight.take() else {
            self.unclean = Some(format!("judgment {ordinal} completed with none in flight"));
            return;
        };
        if boundary.ordinal != ordinal {
            self.unclean = Some(format!(
                "judgment {ordinal} completed while {} was in flight",
                boundary.ordinal
            ));
            return;
        }
        match self.config.policy.block_actor_while_judging {
            Blocking::Off => {}
            Blocking::Stdout => self.gate.open(),
            Blocking::Sigstop => {
                if let Err(error) = self.actor.thaw() {
                    self.unclean = Some(format!("SIGCONT failed: {error}"));
                }
            }
        }
        let lag = boundary.started.elapsed();
        self.counts.max_lag = self.counts.max_lag.max(lag);
        let mut row = self.row("", boundary.disposition);
        row.insert("cursor".into(), json!(boundary.cursor));
        row.insert("boundary".into(), json!(boundary.ordinal));
        row.insert("decision_lag_ms".into(), json!(millis(lag)));
        if let Some(marker) = &judged.marker {
            row.insert("marker".into(), json!(marker));
        }
        if let Some(note) = &boundary.blocking_note {
            row.insert("blocking".into(), json!(note));
        }
        row.insert(
            "calls".into(),
            Value::Array(judged.calls.iter().map(Call::to_json).collect()),
        );
        let mut delivered = false;
        let kind = match judged.decision {
            Decision::Unjudged(reason) => {
                self.counts.unjudged += 1;
                row.insert("reason".into(), json!(reason));
                "unjudged"
            }
            Decision::Silent => {
                self.counts.silent += 1;
                "silent"
            }
            Decision::Lapse(reason) => {
                self.counts.lapses += 1;
                row.insert("reason".into(), json!(reason));
                "lapse"
            }
            Decision::Speak(text) => {
                row.insert("text".into(), json!(text));
                let (kind, reason) = self.deliver(&boundary, text);
                delivered = kind == "spoke";
                if let Some(reason) = reason {
                    row.insert("reason".into(), json!(reason));
                }
                kind
            }
        };
        row.insert("kind".into(), json!(kind));
        self.write_row(row);

        if self.pending.boundary {
            self.pending.boundary = false;
            if !self.stdout_closed && !self.faulted && self.leader_exited_at.is_none() {
                self.start_boundary(self.last_disposition);
                return;
            }
        }
        if self.pending.result {
            self.pending.result = false;
            if !delivered {
                self.actor.close_stdin();
            }
        }
    }

    /// Deliver a line the policy wrote, unless it is stale or the channel is
    /// gone. Returns the row kind and, when not delivered, the reason.
    fn deliver(&mut self, boundary: &InFlight, text: String) -> (&'static str, Option<String>) {
        if boundary.revision != self.revision {
            self.counts.stale += 1;
            return (
                "stale",
                Some(format!(
                    "admitted evidence moved from revision {} to {}",
                    boundary.revision, self.revision
                )),
            );
        }
        if self.stdout_closed || self.leader_exited_at.is_some() {
            self.counts.stale += 1;
            return (
                "stale",
                Some("the actor was gone before delivery".to_string()),
            );
        }
        if self.mute {
            self.counts.gaps += 1;
            return (
                "gap",
                Some("actor stdin unusable; not attempted".to_string()),
            );
        }
        let rendered = format!("<{INTERVENTION_TAG}>\n{text}\n</{INTERVENTION_TAG}>");
        match self
            .actor
            .write_stdin(user_event_line(&rendered).as_bytes())
        {
            Ok(()) => {
                self.counts.corrections += 1;
                self.said.push(text);
                self.spoken_at.push(boundary.ordinal);
                ("spoke", None)
            }
            Err(error) => {
                self.counts.gaps += 1;
                self.mute = true;
                ("gap", Some(format!("actor stdin write failed: {error}")))
            }
        }
    }

    fn row(&self, kind: &str, disposition: Disposition) -> Map<String, Value> {
        let mut row = Map::new();
        row.insert("cursor".into(), json!(self.cursor));
        row.insert("at".into(), json!(utc_now_iso8601()));
        row.insert("policy".into(), json!(POLICY_NAME));
        row.insert("kind".into(), json!(kind));
        row.insert("evidence".into(), json!(disposition.as_str()));
        row
    }

    fn write_row(&mut self, row: Map<String, Value>) {
        let written = serde_json::to_writer(&mut self.log, &Value::Object(row))
            .map_err(io::Error::other)
            .and_then(|()| self.log.write_all(b"\n"))
            .and_then(|()| self.log.flush());
        if let Err(error) = written {
            // Without the account there is no evidence about supervision.
            self.unclean
                .get_or_insert_with(|| format!("writing the supervisor log: {error}"));
        }
    }

    fn finish(self, terminated: bool, criterion_sha256: &str, paths: &Paths) -> Ended {
        let Loop {
            config,
            actor,
            inbox,
            mut log,
            counts,
            mut unclean,
            ..
        } = self;
        // A reader blocked on the full queue can only finish once nobody
        // holds the receiver.
        drop(inbox);
        let grace = Duration::from_millis(config.timeouts.term_grace_ms);
        let (status, stragglers) = match actor.end(grace) {
            Ok(ended) => {
                for (stream, drain) in [("stdout", ended.stdout), ("stderr", ended.stderr)] {
                    if let Some(reason) = drain_fault(stream, drain) {
                        unclean.get_or_insert(reason);
                    }
                }
                (Some(ended.status), ended.stragglers)
            }
            Err(error) => {
                unclean.get_or_insert_with(|| format!("ending the actor: {error}"));
                (None, 0)
            }
        };
        if let Err(error) = log.flush() {
            unclean.get_or_insert_with(|| format!("flushing the supervisor log: {error}"));
        }
        let supervisor_exit = if terminated {
            SupervisorExit::Terminated
        } else if unclean.is_some() {
            SupervisorExit::Unclean
        } else {
            SupervisorExit::Clean
        };
        let summary = Summary {
            schema_version: summary::SCHEMA_VERSION,
            accounted_for: counts.gaps == 0
                && supervisor_exit == SupervisorExit::Clean
                && counts.events > 0,
            supervisor_exit,
            unclean_reason: unclean,
            actor_exit_code: status.and_then(|s| s.code()),
            actor_exit_signal: status.and_then(|s| {
                use std::os::unix::process::ExitStatusExt;
                s.signal()
            }),
            events: counts.events,
            undecodable_lines: counts.undecodable,
            oversized_lines: counts.oversized,
            boundaries: counts.boundaries,
            corrections: counts.corrections,
            silent: counts.silent,
            unjudged: counts.unjudged,
            lapses: counts.lapses,
            gaps: counts.gaps,
            stale_verdicts_discarded: counts.stale,
            max_decision_lag_ms: millis(counts.max_lag),
            stragglers_killed: u64::try_from(stragglers).unwrap_or(u64::MAX),
            model: config.model.name,
            criterion_sha256: criterion_sha256.to_string(),
            actor_event_log_sha256: summary::file_sha256(&paths.actor_event_log),
            supervisor_log_sha256: summary::file_sha256(&paths.supervisor_log),
        };
        Ended { status, summary }
    }
}

/// What a drain's ending says against the run, if anything.
fn drain_fault(stream: &str, drain: Option<Result<(), String>>) -> Option<String> {
    match drain {
        Some(Ok(())) => None,
        Some(Err(error)) => Some(format!("the actor's {stream}: {error}")),
        None => Some(format!(
            "the actor's {stream}: the drain did not finish within the grace; a process the wrapper could not find still holds the pipe"
        )),
    }
}

fn millis(duration: Duration) -> u64 {
    u64::try_from(duration.as_millis()).unwrap_or(u64::MAX)
}

/// The current time as `YYYY-MM-DDTHH:MM:SS.mmmZ`.
#[must_use]
pub fn utc_now_iso8601() -> String {
    let since_epoch = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default();
    let seconds = i64::try_from(since_epoch.as_secs()).unwrap_or(i64::MAX);
    let (date, time) = (seconds.div_euclid(86_400), seconds.rem_euclid(86_400));
    // Civil date from days since 1970-01-01 (Howard Hinnant's algorithm).
    let z = date + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365;
    let year = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = if month <= 2 { year + 1 } else { year };
    format!(
        "{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}.{:03}Z",
        time / 3_600,
        (time % 3_600) / 60,
        time % 60,
        since_epoch.subsec_millis()
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_clock_renders_a_known_instant() {
        // Not the live clock: the algorithm, on a fixed second.
        let seconds: i64 = 1_756_857_600; // 2025-09-03T00:00:00Z
        let (date, time) = (seconds.div_euclid(86_400), seconds.rem_euclid(86_400));
        assert_eq!((date, time), (20_334, 0));
        let now = utc_now_iso8601();
        assert_eq!(now.len(), 24);
        assert!(now.ends_with('Z'));
        assert_eq!(&now[4..5], "-");
        assert_eq!(&now[10..11], "T");
    }
}
