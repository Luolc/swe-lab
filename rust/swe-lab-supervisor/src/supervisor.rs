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
//! The loop ends when the actor's stdout has closed **and** its leader has
//! exited — either alone starts the grace, after which the other is not
//! waited for: an actor that closes stdout and then exits in its own time
//! gets that time, and a leader gone while a descendant holds its stdout
//! does not hold the wrapper — and no judgment is in flight; or when a log
//! stops taking output; or when the wrapper is told to stop. Then the
//! actor's process group is ended, stragglers swept, the drains finished,
//! and the summary written.
//!
//! **Failure semantics.** A failed judge or writer call is one lapse; the
//! next boundary is judged normally. A failed stdin write is a gap: the loop
//! stops speaking, keeps accounting, and the run is not evidence about
//! supervision. So is an unclean ending — a drain that stopped with an error
//! or did not finish, a log that could not be written, a signal that failed.

use std::collections::VecDeque;
use std::ffi::OsString;
use std::fs::File;
use std::io::{BufWriter, Write};
use std::process::ExitStatus;
use std::sync::Arc;
use std::sync::mpsc::{Receiver, RecvTimeoutError, SyncSender};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use serde_json::{Map, Value, json};

use crate::actor::{self, Actor, Event, Gate};
use crate::config::{self, Blocking, Config};
use crate::evidence::{self, Disposition, INTERVENTION_TAG, Message, Role};
use crate::model::{Call, Model};
use crate::outputs::Output;
use crate::policy::{self, Decision, Gates, Judged};
use crate::prompt::Observation;
use crate::signals::{self, Stop};
use crate::stream::user_event_line;
use crate::summary::{self, Summary, SupervisorExit};

/// How long the loop sleeps between checks of the stop flag and the leader
/// when no event arrives.
const TICK: Duration = Duration::from_millis(100);

/// The word every row carries; the one policy this binary implements.
const POLICY_NAME: &str = "speak-when-off-track";

/// The run's outputs, opened by the caller through the wrapper's one door
/// for them ([`Outputs`](crate::outputs::Outputs)), so that no two are one
/// file.
#[derive(Debug)]
pub struct Artifacts {
    /// The actor's stdout, line by line.
    pub actor_event_log: Output,
    /// The supervisor's account, one JSON object per line.
    pub supervisor_log: Output,
    /// The actor's stderr.
    pub actor_stderr: Output,
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
    /// A judgment's outcome: the policy's, or why the judge gave none —
    /// its thread panicked.
    Judged(u64, Result<Judged, String>),
}

/// One boundary underway.
struct InFlight {
    ordinal: u64,
    /// The snapshot's cursor and revision, as of when the judge started.
    cursor: u64,
    revision: u64,
    disposition: Disposition,
    started: Instant,
    /// The judge's thread, once started. Until then the boundary waits for
    /// the reader's barrier: the actor is stopped, and what it wrote before
    /// the stop is still on its way in.
    judge: Option<JoinHandle<()>>,
}

/// The judge thread's word to the loop, sent on the way out whatever
/// happens: a panic below it sends the word as well, saying so.
struct Report {
    outbox: SyncSender<Msg>,
    ordinal: u64,
    sent: bool,
}

impl Report {
    fn judged(mut self, judged: Judged) {
        self.sent = true;
        let _ = self.outbox.send(Msg::Judged(self.ordinal, Ok(judged)));
    }
}

impl Drop for Report {
    fn drop(&mut self) {
        if !self.sent {
            let _ = self.outbox.send(Msg::Judged(
                self.ordinal,
                Err("the judge thread panicked".to_string()),
            ));
        }
    }
}

/// A boundary that fell while a judgment was in flight: it keeps the
/// ordinal it was given, and starts once that judgment is in.
#[derive(Debug, Clone, Copy)]
struct PendingBoundary {
    ordinal: u64,
    disposition: Disposition,
}

/// What arrived while a judgment was in flight and waits on its completion.
#[derive(Debug, Default)]
struct Pending {
    /// A boundary fell: its judgment starts on the snapshot then current.
    boundary: Option<PendingBoundary>,
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
    /// The event log, for its digest once the reader is done with it.
    event_log: File,
    /// Bytes written to the supervisor log so far, against its cap.
    log_written: u64,
    cursor: u64,
    /// The admitted records, the policy's window of them and no more: a
    /// chatty actor's run is as long as its stdout cap allows, and only
    /// the tail is ever rendered.
    evidence: VecDeque<Message>,
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
    /// The first of stdout closing and the leader exiting was seen at this
    /// instant; the other gets the grace from here.
    over_since: Option<Instant>,
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
/// The run could not start: the actor could not be launched. Nothing has
/// been supervised; the caller records a refusal. Once the actor exists,
/// every ending — the prompt not taken, a cancellation, a fault — is an
/// `Ended` with its summary.
pub fn run(
    config: Config,
    criterion_text: &'static str,
    criterion_sha256: &str,
    model: Model,
    launch: Launch<'_>,
    artifacts: Artifacts,
    stop: &Stop,
) -> Result<Ended, String> {
    let Artifacts {
        actor_event_log,
        supervisor_log,
        actor_stderr,
    } = artifacts;
    // A second handle on the event log, for the digest at the end: the
    // reader thread takes the first, and this one reads back the same open
    // file, whatever is at its name by then.
    let event_log = actor_event_log
        .file
        .try_clone()
        .map_err(|e| format!("keeping a handle on the event log: {e}"))?;
    let log = supervisor_log.file;
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
        actor_event_log.file,
        actor_stderr.file,
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
        event_log,
        log_written: 0,
        cursor: 0,
        evidence: VecDeque::new(),
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
        over_since: None,
        faulted: false,
        mute: false,
        unclean: None,
        counts: Counts::default(),
    };
    // From here the actor exists, and the run ends in a summary whatever
    // happens: a prompt the actor did not take is a fault of the run —
    // or, when a stop arrived meanwhile, its cancellation — not a refusal.
    let grace = Duration::from_millis(run.config.timeouts.term_grace_ms);
    let terminated = match run.actor.write_stdin(launch.prompt, stop, grace) {
        Ok(()) => run.serve(stop),
        Err(error) => {
            run.fault(format!("the actor did not take its prompt: {error}"));
            signals::requested(stop).is_some()
        }
    };
    Ok(run.finish(terminated, criterion_sha256))
}

impl Loop {
    /// Consume until the run is over and no judgment is in flight, or until
    /// the wrapper is told to stop. Returns whether it was stopped.
    fn serve(&mut self, stop: &Stop) -> bool {
        let grace = Duration::from_millis(self.config.timeouts.term_grace_ms);
        loop {
            if signals::requested(stop).is_some() {
                return true;
            }
            match self.inbox.recv_timeout(TICK) {
                Ok(Msg::Actor(Event::Line(line))) => self.consume_line(&line),
                Ok(Msg::Actor(Event::Oversized)) => self.counts.oversized += 1,
                Ok(Msg::Actor(Event::Barrier(id))) => self.barrier_reached(id),
                Ok(Msg::Actor(Event::StdoutClosed(result))) => {
                    self.stdout_closed = true;
                    if let Err(error) = result {
                        self.fault(format!("the actor's stdout: {error}"));
                    }
                    // The reader reports a barrier before its end; one still
                    // awaited here means the reader stopped short of it.
                    if let Some(boundary) = self.in_flight.take_if(|b| b.judge.is_none()) {
                        self.release();
                        self.unjudged(
                            boundary.ordinal,
                            boundary.disposition,
                            "the actor's stdout closed before the judgment could start",
                        );
                    }
                }
                Ok(Msg::Actor(Event::StderrClosed(result))) => {
                    if let Err(error) = result {
                        self.fault(format!("the actor's stderr: {error}"));
                    }
                }
                Ok(Msg::Judged(ordinal, outcome)) => self.complete(ordinal, outcome, stop),
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
            if self.stdout_closed || self.leader_exited_at.is_some() {
                self.over_since.get_or_insert_with(Instant::now);
            }
            let over = self.faulted
                || (self.stdout_closed && self.leader_exited_at.is_some())
                || self
                    .over_since
                    .is_some_and(|since| since.elapsed() >= grace);
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
            self.evidence.push_back(record);
            let window = usize::try_from(self.config.policy.window.get()).unwrap_or(usize::MAX);
            while self.evidence.len() > window {
                self.evidence.pop_front();
            }
            self.revision += 1;
        }
        let is_result = event.get("type").and_then(Value::as_str) == Some("result");
        // A result asks that the turn's last evidence be judged; a boundary
        // that has yet to take its snapshot will judge it, so it is not a
        // boundary of its own then.
        let covered = self.pending.boundary.is_some()
            || self.in_flight.as_ref().is_some_and(|b| b.judge.is_none());
        let due = self.assistant_since_boundary
            >= self.config.policy.judge_every_n_assistant_messages.get()
            || (is_result && self.revision > self.last_judged_revision && !covered);
        if due {
            self.assistant_since_boundary = 0;
            match &self.in_flight {
                None => self.start_boundary(None, disposition),
                // The boundary underway has not taken its snapshot yet:
                // this line is in it.
                Some(boundary) if boundary.judge.is_none() => {}
                Some(_) => {
                    // The boundary keeps the ordinal it gets now and starts
                    // when the judgment in flight is in; a third boundary
                    // before then supersedes it, on record.
                    if let Some(previous) = self.pending.boundary.take() {
                        self.unjudged(
                            previous.ordinal,
                            previous.disposition,
                            "superseded by the next boundary before its judgment could start",
                        );
                    }
                    let ordinal = self.allocate_boundary();
                    self.pending.boundary = Some(PendingBoundary {
                        ordinal,
                        disposition,
                    });
                }
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

    /// Start a boundary: with the ordinal it was given when it fell during
    /// a judgment, or with a new one.
    fn start_boundary(&mut self, allocated: Option<u64>, disposition: Disposition) {
        let ordinal = allocated.unwrap_or_else(|| self.allocate_boundary());
        let boundary = InFlight {
            ordinal,
            cursor: self.cursor,
            revision: self.revision,
            disposition,
            started: Instant::now(),
            judge: None,
        };
        match self.config.policy.block_actor_while_judging {
            Blocking::Off => self.spawn_judge(boundary),
            Blocking::Stdout => {
                self.gate.close();
                self.spawn_judge(boundary);
            }
            Blocking::Sigstop => {
                // Stopped and confirmed so, then drained to a barrier: the
                // snapshot is taken when the barrier arrives, with
                // everything the actor wrote before it stopped admitted.
                if let Err(reason) = self.actor.freeze() {
                    self.release();
                    self.unjudged(
                        ordinal,
                        disposition,
                        format!("the actor could not be held still: {reason}"),
                    );
                    return;
                }
                if let Err(error) = self.actor.barrier(ordinal) {
                    self.fault(format!("asking the stdout reader for a barrier: {error}"));
                    self.release();
                    self.unjudged(
                        ordinal,
                        disposition,
                        "the stdout reader could not be asked for a barrier",
                    );
                    return;
                }
                self.in_flight = Some(boundary);
            }
        }
    }

    fn allocate_boundary(&mut self) -> u64 {
        self.boundary_ordinal += 1;
        self.counts.boundaries += 1;
        self.boundary_ordinal
    }

    /// The reader's barrier: everything the actor wrote before it stopped
    /// is admitted. This is the snapshot the boundary is judged on.
    fn barrier_reached(&mut self, id: u64) {
        let Some(mut boundary) = self
            .in_flight
            .take_if(|b| b.ordinal == id && b.judge.is_none())
        else {
            self.fault(format!(
                "barrier {id} reached with no boundary waiting for it"
            ));
            return;
        };
        if self.faulted {
            self.release();
            self.unjudged(
                boundary.ordinal,
                boundary.disposition,
                "the run faulted before the judgment could start",
            );
            return;
        }
        boundary.cursor = self.cursor;
        boundary.revision = self.revision;
        self.spawn_judge(boundary);
    }

    /// Start the judge on the evidence as it is now.
    fn spawn_judge(&mut self, mut boundary: InFlight) {
        self.last_judged_revision = self.revision;
        let evidence: Vec<Message> = self.evidence.iter().cloned().collect();
        let said = self.said.clone();
        let task = self.config.task.clone();
        let ordinal = boundary.ordinal;
        let cooldown = u64::from(self.config.policy.cooldown);
        let gates = Gates {
            budget_left: self.spoken_at.len() < self.config.policy.budget as usize,
            cooldown_satisfied: self
                .spoken_at
                .last()
                .is_none_or(|&last| ordinal - last >= cooldown),
        };
        let model = Arc::clone(&self.model);
        let criterion = self.criterion_text;
        let outbox = self.outbox.clone();
        let spawned = thread::Builder::new()
            .name(format!("judge-{ordinal}"))
            .spawn(move || {
                let report = Report {
                    outbox,
                    ordinal,
                    sent: false,
                };
                let observation = Observation {
                    task: &task,
                    evidence: &evidence,
                    said: &said,
                };
                report.judged(policy::judge_boundary(
                    &model,
                    criterion,
                    &observation,
                    gates,
                ));
            });
        match spawned {
            Ok(handle) => {
                boundary.judge = Some(handle);
                self.in_flight = Some(boundary);
            }
            Err(error) => {
                self.unclean.get_or_insert_with(|| {
                    format!("the judge for boundary {ordinal} could not be started: {error}")
                });
                self.release();
                self.unjudged(
                    ordinal,
                    boundary.disposition,
                    format!("the judge could not be started: {error}"),
                );
            }
        }
    }

    fn complete(&mut self, ordinal: u64, outcome: Result<Judged, String>, stop: &Stop) {
        let Some(mut boundary) = self
            .in_flight
            .take_if(|b| b.ordinal == ordinal && b.judge.is_some())
        else {
            self.unclean.get_or_insert_with(|| {
                format!("judgment {ordinal} completed with no such boundary in flight")
            });
            return;
        };
        // Reaped: the thread has reported, so this returns at once. A panic
        // arrived as the outcome and is the reason below.
        if let Some(handle) = boundary.judge.take() {
            let _ = handle.join();
        }
        let lag = boundary.started.elapsed();
        self.counts.max_lag = self.counts.max_lag.max(lag);
        let mut row = self.row("", boundary.disposition);
        row.insert("cursor".into(), json!(boundary.cursor));
        row.insert("boundary".into(), json!(boundary.ordinal));
        row.insert("decision_lag_ms".into(), json!(millis(lag)));
        let mut delivered = false;
        let kind = match outcome {
            Err(reason) => {
                self.unclean
                    .get_or_insert_with(|| format!("boundary {ordinal}: {reason}"));
                self.counts.unjudged += 1;
                row.insert("reason".into(), json!(reason));
                "unjudged"
            }
            Ok(judged) => {
                if let Some(marker) = &judged.marker {
                    row.insert("marker".into(), json!(marker));
                }
                row.insert(
                    "calls".into(),
                    Value::Array(judged.calls.iter().map(Call::to_json).collect()),
                );
                match judged.decision {
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
                        let (kind, reason) = self.deliver(&boundary, text, stop);
                        delivered = kind == "spoke";
                        if let Some(reason) = reason {
                            row.insert("reason".into(), json!(reason));
                        }
                        kind
                    }
                }
            }
        };
        row.insert("kind".into(), json!(kind));
        self.write_row(row);
        // The actor was held — stopped, or gated — through the freshness
        // check and the stdin write above; only now is it let go.
        self.release();

        if let Some(pending) = self.pending.boundary.take() {
            if self.continuing() {
                self.start_boundary(Some(pending.ordinal), pending.disposition);
                return;
            }
            self.unjudged(
                pending.ordinal,
                pending.disposition,
                "the run ended before its judgment could start",
            );
        }
        if self.pending.result {
            self.pending.result = false;
            if !delivered {
                self.actor.close_stdin();
            }
        }
    }

    /// Let the actor go on: the gate opens, or the group is thawed. Every
    /// way out of a boundary passes here.
    fn release(&mut self) {
        match self.config.policy.block_actor_while_judging {
            Blocking::Off => {}
            Blocking::Stdout => self.gate.open(),
            Blocking::Sigstop => {
                if let Err(error) = self.actor.thaw() {
                    // An actor that cannot be resumed cannot be supervised
                    // further: the run ends now, and is not accounted for.
                    self.fault(format!("SIGCONT failed: {error}"));
                }
            }
        }
    }

    /// Account for a boundary at which no decision was sought.
    fn unjudged(&mut self, ordinal: u64, disposition: Disposition, reason: impl Into<String>) {
        self.counts.unjudged += 1;
        let mut row = self.row("unjudged", disposition);
        row.insert("boundary".into(), json!(ordinal));
        row.insert("reason".into(), json!(reason.into()));
        self.write_row(row);
    }

    /// Whether there is a run left to supervise.
    fn continuing(&self) -> bool {
        !self.stdout_closed && !self.faulted && self.leader_exited_at.is_none()
    }

    /// Deliver a line the policy wrote, unless it is stale or the channel is
    /// gone. Returns the row kind and, when not delivered, the reason.
    fn deliver(
        &mut self,
        boundary: &InFlight,
        text: String,
        stop: &Stop,
    ) -> (&'static str, Option<String>) {
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
        let grace = Duration::from_millis(self.config.timeouts.term_grace_ms);
        match self
            .actor
            .write_stdin(user_event_line(&rendered).as_bytes(), stop, grace)
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

    /// Append one row to the supervisor log, whole, and flush it. A row that
    /// would cross the log's cap is not written, and a write that fails is
    /// a fault: a supervisor that has lost its own account is producing
    /// supervision without evidence, and the run ends rather than go on.
    ///
    /// The cap is `limits.max_actor_stdout_bytes`, on purpose and not by
    /// accident: a row per event and per boundary, with the model's raw
    /// answers, is in the same order as the stdout it accounts for, and the
    /// cap's job is to bound pathological growth, not to be tuned — so it
    /// borrows the stdout cap rather than add a config key that the Python
    /// side would have to mirror by hand. If a use for tuning it apart ever
    /// appears, it gets a key of its own then.
    fn write_row(&mut self, row: Map<String, Value>) {
        let mut line = match serde_json::to_vec(&Value::Object(row)) {
            Ok(line) => line,
            Err(error) => {
                self.fault(format!("rendering a supervisor log row: {error}"));
                return;
            }
        };
        line.push(b'\n');
        let cap = self.config.limits.max_actor_stdout_bytes.get();
        let Some(after) = u64::try_from(line.len())
            .ok()
            .and_then(|bytes| self.log_written.checked_add(bytes))
            .filter(|&after| after <= cap)
        else {
            self.fault(format!("the supervisor log reached its cap of {cap} bytes"));
            return;
        };
        let written = self.log.write_all(&line).and_then(|()| self.log.flush());
        match written {
            Ok(()) => self.log_written = after,
            Err(error) => self.fault(format!("writing the supervisor log: {error}")),
        }
    }

    /// Put a boundary still underway on record as such. A judge still
    /// running is not waited for: only a cancellation gets here with one,
    /// its call has a deadline of its own, and the process ends and takes
    /// the thread with it.
    fn close_boundaries(&mut self) {
        if let Some(boundary) = self.in_flight.take() {
            let reason = if boundary.judge.is_some() {
                "the run ended while the judgment was in flight"
            } else {
                "the run ended before the judgment could start"
            };
            self.unjudged(boundary.ordinal, boundary.disposition, reason);
        }
        if let Some(pending) = self.pending.boundary.take() {
            self.unjudged(
                pending.ordinal,
                pending.disposition,
                "the run ended before its judgment could start",
            );
        }
    }

    fn finish(mut self, terminated: bool, criterion_sha256: &str) -> Ended {
        self.close_boundaries();
        let Loop {
            config,
            actor,
            inbox,
            mut log,
            mut event_log,
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
                let stragglers = match ended.stragglers {
                    Ok(count) => count,
                    Err(reason) => {
                        unclean.get_or_insert_with(|| {
                            format!("no proof that no descendant of the actor survived: {reason}")
                        });
                        0
                    }
                };
                (Some(ended.status), stragglers)
            }
            Err(error) => {
                unclean.get_or_insert_with(|| format!("ending the actor: {error}"));
                (None, 0)
            }
        };
        if let Err(error) = log.flush() {
            unclean.get_or_insert_with(|| format!("flushing the supervisor log: {error}"));
        }
        // Both logs are complete: the reader is joined and the account is
        // flushed. Digested through the descriptors they were written by.
        let mut digest = |name: &str, file: &mut File| match summary::digest(file) {
            Ok(digest) => Some(digest),
            Err(error) => {
                unclean.get_or_insert_with(|| format!("digesting the {name}: {error}"));
                None
            }
        };
        let actor_event_log_sha256 = digest("event log", &mut event_log);
        let supervisor_log_sha256 = digest("supervisor log", log.get_mut());
        let supervisor_exit = if terminated {
            SupervisorExit::Terminated
        } else if unclean.is_some() {
            SupervisorExit::Unclean
        } else {
            SupervisorExit::Clean
        };
        let summary = Summary {
            schema_version: summary::SCHEMA_VERSION,
            // An identifiable record is part of being accounted for: a
            // run whose logs have no digest is not one.
            accounted_for: counts.gaps == 0
                && supervisor_exit == SupervisorExit::Clean
                && counts.events > 0
                && actor_event_log_sha256.is_some()
                && supervisor_log_sha256.is_some(),
            supervisor_exit,
            unclean_reason: unclean,
            actor_exit_code: status.and_then(|s| {
                use std::os::unix::process::ExitStatusExt;
                s.code().or_else(|| s.signal().map(|signal| 128 + signal))
            }),
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
            actor_event_log_sha256,
            supervisor_log_sha256,
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
