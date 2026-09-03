//! The actor as a child process: one owner for its stdin, stdout, stderr and
//! process group.
//!
//! This is the reason the runtime exists as a wrapper. Holding the actor's
//! stdout pipe is what makes blocking possible — stop reading, and the actor's
//! next write waits — and holding its process group is what makes shutdown
//! one act rather than a guess about what the actor spawned.
//!
//! The stdout reader runs on its own thread so that draining never waits on a
//! model call: it frames lines up to the configured ceiling, appends each to
//! the event log the moment it is complete, and hands it to the consumer.
//! Stderr is copied to its log on a second thread.
//!
//! What the wrapper holds is bounded on every side. One stdout line in memory,
//! up to the ceiling. At most [`EVENT_QUEUE_LINES`] lines queued ahead of the
//! consumer: the reader blocks on a full queue, the pipe fills, the actor
//! waits — the same back-pressure the gate applies on purpose. Each log is
//! capped at a configured size, exactly: a record that would cross the cap is
//! not written, the stream is not read further, and the run is over. And every
//! descendant carries a mark in its environment from launch, so that one which
//! leaves the process group (`setsid`) is still found and killed when the
//! actor ends.

use std::ffi::OsString;
use std::fs::{self, File};
use std::io::{self, BufWriter, Read, Write};
use std::os::unix::process::CommandExt;
use std::path::Path;
use std::process::{Child, ChildStderr, ChildStdin, ChildStdout, Command, ExitStatus, Stdio};
use std::sync::mpsc::{self, Receiver, SyncSender};
use std::sync::{Arc, Condvar, Mutex, PoisonError};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use nix::errno::Errno;
use nix::sys::signal::{Signal, kill, killpg};
use nix::sys::wait::{Id, WaitPidFlag, WaitStatus, waitid};
use nix::unistd::Pid;

use crate::framing::{Frame, Framer};

/// How much is taken off a pipe per read.
const READ_CHUNK_BYTES: usize = 64 * 1024;

/// How often the leader is checked for exit while the grace period runs.
const EXIT_POLL_INTERVAL: Duration = Duration::from_millis(20);

/// How many stdout lines may be queued ahead of the consumer. The reader
/// blocks on a full queue, so what the wrapper holds of an actor faster than
/// its consumer is at most this many lines of at most the line ceiling each,
/// plus one read chunk — never the stream.
pub const EVENT_QUEUE_LINES: usize = 16;

/// The environment variable that marks the actor and every descendant that
/// inherits its environment, so that one which leaves the process group is
/// still the wrapper's to end. Its value is not a secret: the wrapper's pid
/// and a timestamp.
pub const MARK_ENV: &str = "SWE_LAB_SUPERVISOR_MARK";

/// How many times the sweep for marked stragglers repeats: one may fork
/// while it is being killed.
const SWEEP_PASSES: usize = 5;

/// The reason a drain stops at its cap.
const CAP_REACHED: &str = "the log's byte cap was reached";

/// The queue between the reader threads and their consumer, bounded to
/// [`EVENT_QUEUE_LINES`] entries.
#[must_use]
pub fn event_queue<T>() -> (SyncSender<T>, Receiver<T>) {
    mpsc::sync_channel(EVENT_QUEUE_LINES)
}

/// What the reader threads report.
#[derive(Debug, Clone)]
pub enum Event {
    /// One complete stdout line within the ceiling, newline excluded, already
    /// appended to the event log.
    Line(Vec<u8>),
    /// One stdout line over the ceiling ended. It was appended to the event
    /// log verbatim; nothing here can decode it.
    Oversized,
    /// Nothing more of the actor's stdout will be read: end of file — every
    /// holder of the pipe's write end is gone — or the error the drain
    /// stopped with, a failed write to the event log or its cap reached.
    StdoutClosed(Result<(), String>),
    /// The same for stderr and its log.
    StderrClosed(Result<(), String>),
}

/// What bounds the actor's output, all in bytes.
#[derive(Debug, Clone, Copy)]
pub struct Limits {
    /// The ceiling on one stdout line held in memory. A longer line is
    /// logged verbatim and reported as [`Event::Oversized`].
    pub line: usize,
    /// The cap on the event log. A line that would cross it is not written,
    /// and stdout is not read further.
    pub stdout: u64,
    /// The cap on the stderr log, exact to the byte.
    pub stderr: u64,
}

/// How an actor ended.
#[derive(Debug)]
pub struct Ended {
    /// The leader's exit status.
    pub status: ExitStatus,
    /// How the stdout drain ended: `Ok` at end of file, or the error it
    /// stopped with. `None` if it had not finished within the grace — a
    /// writer the sweep could not find still holds the pipe.
    pub stdout: Option<Result<(), String>>,
    /// The same for the stderr drain.
    pub stderr: Option<Result<(), String>>,
    /// Marked descendants found outside the group once the group was
    /// killed, and killed.
    pub stragglers: usize,
}

/// The stdout reader's gate. Closed, the reader takes nothing off the pipe;
/// the pipe fills and the actor's next write blocks until the gate opens.
/// Nothing to time out and no signal to send: it is the absence of a read,
/// and it releases by itself if the wrapper dies.
#[derive(Debug)]
pub struct Gate {
    open: Mutex<bool>,
    changed: Condvar,
}

impl Gate {
    fn new() -> Self {
        Self {
            open: Mutex::new(true),
            changed: Condvar::new(),
        }
    }

    /// Stop the reader before its next read.
    pub fn close(&self) {
        *self.open.lock().unwrap_or_else(PoisonError::into_inner) = false;
    }

    /// Let the reader continue.
    pub fn open(&self) {
        *self.open.lock().unwrap_or_else(PoisonError::into_inner) = true;
        self.changed.notify_all();
    }

    fn wait_open(&self) {
        let mut open = self.open.lock().unwrap_or_else(PoisonError::into_inner);
        while !*open {
            open = self
                .changed
                .wait(open)
                .unwrap_or_else(PoisonError::into_inner);
        }
    }
}

/// The actor's command: argv exactly as given after `--`, never joined into a
/// shell line and never augmented. `scrub_env` names the environment
/// variables the actor must not inherit — the supervisor's own endpoint and
/// credential, which the actor has no business reading.
///
/// # Errors
///
/// The argv is empty.
pub fn command(argv: &[OsString], scrub_env: &[&str]) -> io::Result<Command> {
    let Some((program, arguments)) = argv.split_first() else {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "empty actor argv",
        ));
    };
    let mut command = Command::new(program);
    command.args(arguments);
    for name in scrub_env {
        command.env_remove(name);
    }
    Ok(command)
}

#[cfg(test)]
thread_local! {
    /// Fault injection: the stderr reader's thread cannot be created.
    static FAIL_STDERR_THREAD: std::cell::Cell<bool> = const { std::cell::Cell::new(false) };
}

/// A running actor and everything the wrapper holds of it.
///
/// Dropping one without [`Actor::end`] kills its process group and sweeps
/// for marked stragglers: an actor the wrapper stops holding is not an actor
/// that may run on unsupervised.
#[derive(Debug)]
pub struct Actor {
    child: Child,
    group: Pid,
    mark: String,
    stdin: Option<ChildStdin>,
    gate: Arc<Gate>,
    /// `SIGSTOP` was sent to the group and no `SIGCONT` has followed. A real
    /// state the wrapper guarantees it leaves before it exits.
    frozen: bool,
    reaped: bool,
    stdout_drain: Option<JoinHandle<Result<(), String>>>,
    stderr_drain: Option<JoinHandle<Result<(), String>>>,
}

impl Actor {
    /// Launch the actor from a prepared command (see [`command`]), in its
    /// own process group, all three standard streams held here, its
    /// environment marked ([`MARK_ENV`]). Every line of stdout goes to
    /// `event_log`, stderr to `stderr_log`; both files are created before the
    /// actor starts. `events` is called from the reader threads, in order per
    /// stream; it may block, and the reader with it.
    ///
    /// # Errors
    ///
    /// A log file cannot be created, the actor cannot be spawned, or a reader
    /// thread cannot be started — in which case the actor that was already
    /// started is killed and reaped before this returns.
    pub fn spawn<F>(
        mut command: Command,
        event_log: &Path,
        stderr_log: &Path,
        limits: Limits,
        events: F,
    ) -> io::Result<Self>
    where
        F: Fn(Event) + Send + Sync + 'static,
    {
        let event_log = File::create(event_log)?;
        let stderr_log = File::create(stderr_log)?;
        let mark = format!(
            "{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap_or_default()
                .as_nanos()
        );
        command
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .process_group(0)
            .env(MARK_ENV, &mark);
        let child = command.spawn()?;
        let group = Pid::from_raw(
            i32::try_from(child.id()).map_err(|_| io::Error::other("actor pid does not fit"))?,
        );
        // From here the actor is live, and this handle owns it: whatever
        // fails below drops the handle, which kills and reaps the group.
        let mut actor = Self {
            child,
            group,
            mark,
            stdin: None,
            gate: Arc::new(Gate::new()),
            frozen: false,
            reaped: false,
            stdout_drain: None,
            stderr_drain: None,
        };
        actor.stdin = actor.child.stdin.take();
        let (Some(stdout), Some(stderr)) = (actor.child.stdout.take(), actor.child.stderr.take())
        else {
            return Err(io::Error::other(
                "actor spawned without piped stdout and stderr",
            ));
        };
        let events = Arc::new(events);
        let gate = Arc::clone(&actor.gate);
        let report = Arc::clone(&events);
        actor.stdout_drain = Some(thread::Builder::new().name("actor-stdout".into()).spawn(
            move || {
                let result =
                    drain_stdout(stdout, &gate, BufWriter::new(event_log), limits, &*report)
                        .map_err(|e| e.to_string());
                report(Event::StdoutClosed(result.clone()));
                result
            },
        )?);
        #[cfg(test)]
        if FAIL_STDERR_THREAD.with(std::cell::Cell::get) {
            return Err(io::Error::other(
                "injected: the stderr reader could not start",
            ));
        }
        actor.stderr_drain = Some(thread::Builder::new().name("actor-stderr".into()).spawn(
            move || {
                let result =
                    drain_stderr(stderr, stderr_log, limits.stderr).map_err(|e| e.to_string());
                events(Event::StderrClosed(result.clone()));
                result
            },
        )?);
        Ok(actor)
    }

    /// The reader's gate, to close while a judgment is in flight.
    #[must_use]
    pub fn gate(&self) -> Arc<Gate> {
        Arc::clone(&self.gate)
    }

    #[cfg_attr(not(test), expect(dead_code, reason = "exercised by the tests"))]
    /// The actor's pid, which is also its process group id.
    #[must_use]
    pub fn pid(&self) -> u32 {
        self.child.id()
    }

    /// Write on the actor's stdin and flush.
    ///
    /// # Errors
    ///
    /// The stdin was already closed deliberately, or the write failed — the
    /// actor is gone, or stopped reading. Either is the supervisor's channel
    /// failing, which the caller classifies.
    pub fn write_stdin(&mut self, bytes: &[u8]) -> io::Result<()> {
        let Some(stdin) = self.stdin.as_mut() else {
            return Err(io::Error::new(
                io::ErrorKind::BrokenPipe,
                "actor stdin was already closed",
            ));
        };
        stdin.write_all(bytes)?;
        stdin.flush()
    }

    /// Close the actor's stdin: the deliberate end of the run. A cooperative
    /// actor finishes its turn and exits on the EOF.
    pub fn close_stdin(&mut self) {
        self.stdin = None;
    }

    #[cfg_attr(not(test), expect(dead_code, reason = "exercised by the tests"))]
    /// Whether stdin is still open for corrections.
    #[must_use]
    pub fn stdin_open(&self) -> bool {
        self.stdin.is_some()
    }

    /// `SIGSTOP` the whole group. The stdout pipe keeps draining, so nothing
    /// the actor already wrote is lost, and it writes nothing more until
    /// [`Actor::thaw`].
    ///
    /// # Errors
    ///
    /// The group could not be signalled.
    pub fn freeze(&mut self) -> io::Result<()> {
        killpg(self.group, Signal::SIGSTOP).map_err(io::Error::from)?;
        self.frozen = true;
        Ok(())
    }

    /// `SIGCONT` the group if it was frozen. Idempotent, and tolerant of a
    /// group that has since disappeared.
    ///
    /// # Errors
    ///
    /// The group exists and could not be signalled.
    pub fn thaw(&mut self) -> io::Result<()> {
        if !self.frozen {
            return Ok(());
        }
        self.frozen = false;
        signal_group(self.group, Signal::SIGCONT)
    }

    #[cfg_attr(not(test), expect(dead_code, reason = "exercised by the tests"))]
    /// Whether the group is currently stopped by [`Actor::freeze`].
    #[must_use]
    pub fn frozen(&self) -> bool {
        self.frozen
    }

    /// Whether the leader has exited — observed **without reaping it**, so
    /// its pid keeps naming the group until the last signal has been sent.
    /// A leader that exited while a descendant still holds its stdout is the
    /// case this exists for: the drain alone would never end.
    ///
    /// # Errors
    ///
    /// The kernel refused the query.
    pub fn exited(&self) -> io::Result<bool> {
        if self.reaped {
            return Ok(true);
        }
        match waitid(
            Id::Pid(self.group),
            WaitPidFlag::WEXITED | WaitPidFlag::WNOWAIT | WaitPidFlag::WNOHANG,
        ) {
            Ok(WaitStatus::StillAlive) => Ok(false),
            // Exited — or reaped elsewhere, and then not this wrapper's to
            // name any more.
            Ok(_) | Err(Errno::ECHILD) => Ok(true),
            Err(errno) => Err(io::Error::from(errno)),
        }
    }

    /// End the actor: its whole process group, then every marked descendant
    /// that left the group, then the drains.
    ///
    /// Any freeze is lifted and the gate opened first, so an actor that is
    /// stopped or blocked can act on the signal. Then `SIGTERM` to the group,
    /// up to `grace` for the leader to exit, `SIGKILL` to whatever is left —
    /// grandchildren included — the reap (an unreaped leader is what keeps
    /// its pid naming this group and no other), the sweep for stragglers, and
    /// up to `grace` more for the two drains to reach end of file. A consumer
    /// that stopped receiving must drop its receiver before calling this, or
    /// a reader blocked on the full queue never reaches it.
    ///
    /// # Errors
    ///
    /// A signal or the final wait failed for a reason other than the group
    /// being already gone.
    pub fn end(mut self, grace: Duration) -> io::Result<Ended> {
        self.thaw()?;
        self.gate.open();
        if !self.exited()? {
            signal_group(self.group, Signal::SIGTERM)?;
            let deadline = Instant::now() + grace;
            while !self.exited()? && Instant::now() < deadline {
                thread::sleep(EXIT_POLL_INTERVAL);
            }
        }
        signal_group(self.group, Signal::SIGKILL)?;
        let status = self.child.wait()?;
        self.reaped = true;
        let stragglers = sweep(&self.mark, self.group);
        let deadline = Instant::now() + grace;
        let stdout = finish(&mut self.stdout_drain, deadline);
        let stderr = finish(&mut self.stderr_drain, deadline);
        Ok(Ended {
            status,
            stdout,
            stderr,
            stragglers,
        })
    }
}

impl Drop for Actor {
    fn drop(&mut self) {
        if self.reaped {
            return;
        }
        // Best effort on a path that is already abnormal: never leave the
        // group frozen, never leave it running, reap the leader so its pid
        // stops naming the group — a killed leader dies at once, so the wait
        // does not block — and sweep for what left the group.
        let _ = self.thaw();
        let _ = signal_group(self.group, Signal::SIGKILL);
        let _ = self.child.wait();
        self.reaped = true;
        sweep(&self.mark, self.group);
    }
}

fn signal_group(group: Pid, signal: Signal) -> io::Result<()> {
    if group.as_raw() <= 1 {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "refusing to signal a process group at or below 1",
        ));
    }
    match killpg(group, signal) {
        Ok(()) | Err(Errno::ESRCH) => Ok(()),
        Err(errno) => Err(io::Error::from(errno)),
    }
}

/// Wait for a drain to finish, bounded: a writer the sweep could not find
/// may hold a pipe open for good, and the wrapper does not hang on it.
fn finish(
    drain: &mut Option<JoinHandle<Result<(), String>>>,
    deadline: Instant,
) -> Option<Result<(), String>> {
    let handle = drain.take()?;
    while !handle.is_finished() && Instant::now() < deadline {
        thread::sleep(EXIT_POLL_INTERVAL);
    }
    if !handle.is_finished() {
        return None;
    }
    Some(
        handle
            .join()
            .unwrap_or_else(|_| Err("the drain thread panicked".to_string())),
    )
}

/// Kill every process whose environment carries the actor's mark and whose
/// process group is not the actor's — a descendant that called `setsid` —
/// until none is found or the passes run out. Returns how many were found.
fn sweep(mark: &str, group: Pid) -> usize {
    let needle = format!("{MARK_ENV}={mark}");
    let mut found = 0;
    for _ in 0..SWEEP_PASSES {
        let escaped: Vec<Pid> = marked_processes(&needle)
            .into_iter()
            .filter(|(_, pgrp)| *pgrp != group)
            .map(|(pid, _)| pid)
            .collect();
        if escaped.is_empty() {
            break;
        }
        for pid in escaped {
            if kill(pid, Signal::SIGKILL).is_ok() {
                found += 1;
            }
        }
        thread::sleep(EXIT_POLL_INTERVAL);
    }
    found
}

/// Every live process whose initial environment holds `needle` as one entry,
/// with its process group. Only this user's processes are readable, and
/// those are the only ones the actor could have made.
fn marked_processes(needle: &str) -> Vec<(Pid, Pid)> {
    let Ok(entries) = fs::read_dir("/proc") else {
        return Vec::new();
    };
    let me = std::process::id();
    entries
        .flatten()
        .filter_map(|entry| {
            let pid: u32 = entry.file_name().to_str()?.parse().ok()?;
            if pid == me {
                return None;
            }
            let environ = fs::read(entry.path().join("environ")).ok()?;
            if !environ.split(|b| *b == 0).any(|e| e == needle.as_bytes()) {
                return None;
            }
            let stat = fs::read_to_string(entry.path().join("stat")).ok()?;
            // `pid (comm) state ppid pgrp ...` — after the last `)`.
            let pgrp: i32 = stat
                .rsplit_once(')')?
                .1
                .split_whitespace()
                .nth(3)?
                .parse()
                .ok()?;
            Some((Pid::from_raw(i32::try_from(pid).ok()?), Pid::from_raw(pgrp)))
        })
        .collect()
}

fn drain_stdout(
    mut stdout: ChildStdout,
    gate: &Gate,
    mut log: BufWriter<File>,
    limits: Limits,
    events: &dyn Fn(Event),
) -> io::Result<()> {
    let mut framer = Framer::new(limits.line);
    let mut chunk = vec![0u8; READ_CHUNK_BYTES];
    let mut pending = Vec::new();
    let mut budget = limits.stdout;
    loop {
        gate.wait_open();
        let read = stdout.read(&mut chunk)?;
        if read == 0 {
            break;
        }
        framer.push(&chunk[..read], &mut pending);
        relay(&mut pending, &mut log, &mut budget, events)?;
    }
    framer.finish(&mut pending);
    relay(&mut pending, &mut log, &mut budget, events)?;
    log.flush()
}

/// Append each frame to the event log — flushed per line, so the artifact is
/// complete up to the last whole line at any moment — and report it. A frame
/// that would cross the cap is not written, and the drain stops.
fn relay(
    frames: &mut Vec<Frame>,
    log: &mut BufWriter<File>,
    budget: &mut u64,
    events: &dyn Fn(Event),
) -> io::Result<()> {
    for frame in frames.drain(..) {
        match frame {
            Frame::Line(line) => {
                charge(budget, line.len(), true)?;
                log.write_all(&line)?;
                log.write_all(b"\n")?;
                log.flush()?;
                events(Event::Line(line));
            }
            Frame::Oversized { part, last } => {
                charge(budget, part.len(), last)?;
                log.write_all(&part)?;
                if last {
                    log.write_all(b"\n")?;
                    log.flush()?;
                    events(Event::Oversized);
                }
            }
        }
    }
    Ok(())
}

/// Take a record — `bytes`, plus its newline when it ends a line — off the
/// remaining budget, or stop the stream at the cap.
fn charge(budget: &mut u64, bytes: usize, newline: bool) -> io::Result<()> {
    let bytes = u64::try_from(bytes)
        .unwrap_or(u64::MAX)
        .saturating_add(u64::from(newline));
    match budget.checked_sub(bytes) {
        Some(left) => {
            *budget = left;
            Ok(())
        }
        None => Err(io::Error::other(CAP_REACHED)),
    }
}

/// Copy stderr to its log up to the cap, exact to the byte; past it, the
/// drain stops and the actor's next stderr write eventually blocks.
fn drain_stderr(mut stderr: ChildStderr, mut log: File, cap: u64) -> io::Result<()> {
    let mut chunk = vec![0u8; READ_CHUNK_BYTES];
    let mut budget = cap;
    loop {
        let read = stderr.read(&mut chunk)?;
        if read == 0 {
            return Ok(());
        }
        let allowed = usize::try_from(budget).map_or(read, |left| left.min(read));
        log.write_all(&chunk[..allowed])?;
        budget -= u64::try_from(allowed).unwrap_or(u64::MAX);
        if allowed < read {
            return Err(io::Error::other(CAP_REACHED));
        }
    }
}

#[cfg(test)]
mod tests {
    use std::os::unix::process::ExitStatusExt;
    use std::path::PathBuf;
    use std::sync::mpsc::Receiver;

    use super::*;

    const LIMITS: Limits = Limits {
        line: 1024,
        stdout: u64::MAX,
        stderr: u64::MAX,
    };

    /// A fresh directory under the system temp dir, unique to this test.
    fn scratch(name: &str) -> PathBuf {
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::temp_dir().join(format!(
            "swe-lab-supervisor-{name}-{}-{nanos}",
            std::process::id()
        ));
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn sh(script: &str) -> Vec<OsString> {
        ["sh", "-c", script].map(OsString::from).to_vec()
    }

    /// An actor whose events go to an unbounded queue: only the gate can
    /// hold it, never a slow test.
    fn launch(dir: &Path, script: &str, ceiling: usize) -> (Actor, Receiver<Event>) {
        let (tx, rx) = mpsc::channel();
        let actor = Actor::spawn(
            command(&sh(script), &[]).unwrap(),
            &dir.join("events.jsonl"),
            &dir.join("stderr.log"),
            Limits {
                line: ceiling,
                ..LIMITS
            },
            move |event| {
                let _ = tx.send(event);
            },
        )
        .unwrap();
        (actor, rx)
    }

    /// How many live processes carry `PROBE=<value>` in their environment —
    /// the test's own mark on an actor, set on its command.
    fn probes_alive(value: &str) -> usize {
        marked_processes(&format!("PROBE={value}")).len()
    }

    fn wait_until(deadline: Duration, condition: impl Fn() -> bool) -> bool {
        let deadline = Instant::now() + deadline;
        while !condition() {
            if Instant::now() > deadline {
                return false;
            }
            thread::sleep(EXIT_POLL_INTERVAL);
        }
        true
    }

    /// Collect events until stdout closes.
    fn drain(rx: &Receiver<Event>) -> Vec<Event> {
        let mut events = Vec::new();
        loop {
            let event = rx.recv_timeout(Duration::from_secs(10)).unwrap();
            let closed = matches!(event, Event::StdoutClosed(_));
            events.push(event);
            if closed {
                return events;
            }
        }
    }

    fn lines(events: &[Event]) -> Vec<String> {
        events
            .iter()
            .filter_map(|e| match e {
                Event::Line(line) => Some(String::from_utf8(line.clone()).unwrap()),
                _ => None,
            })
            .collect()
    }

    fn group_exists(pid: u32) -> bool {
        killpg(Pid::from_raw(i32::try_from(pid).unwrap()), None).is_ok()
    }

    #[test]
    fn stdout_is_framed_relayed_and_logged_verbatim_and_stderr_goes_to_its_log() {
        let dir = scratch("drain");
        let big = "x".repeat(100);
        let (actor, rx) = launch(
            &dir,
            &format!("printf 'a\\nb\\n'; echo oops >&2; printf '{big}\\n'; printf 'c'"),
            16,
        );
        let events = drain(&rx);
        assert_eq!(lines(&events), vec!["a", "b", "c"]);
        assert_eq!(
            events
                .iter()
                .filter(|e| matches!(e, Event::Oversized))
                .count(),
            1
        );
        assert!(matches!(events.last(), Some(Event::StdoutClosed(Ok(())))));
        let ended = actor.end(Duration::from_secs(5)).unwrap();
        assert_eq!(ended.status.code(), Some(0));
        assert_eq!(ended.stdout, Some(Ok(())));
        assert_eq!(ended.stderr, Some(Ok(())));
        assert_eq!(ended.stragglers, 0);
        assert_eq!(
            std::fs::read_to_string(dir.join("events.jsonl")).unwrap(),
            format!("a\nb\n{big}\nc\n")
        );
        assert_eq!(
            std::fs::read_to_string(dir.join("stderr.log")).unwrap(),
            "oops\n"
        );
    }

    #[test]
    fn a_closed_gate_holds_the_actor_on_its_next_write_and_opening_it_releases_it() {
        // The actor appends one line to a progress file after every line it
        // writes, so the test can watch it stall without trusting anything
        // about pipe sizes. Appended, not rewritten: a shell buffers a
        // redirected builtin's output, and a truncate-then-write progress
        // file reads back empty or stale while the shell is blocked.
        let dir = scratch("gate");
        let progress = dir.join("progress");
        let script = format!(
            "i=0; while [ $i -lt 40000 ]; do echo \"line $i\"; i=$((i+1)); echo $i >> {}; done",
            progress.display()
        );
        let (actor, rx) = launch(&dir, &script, 1024);
        let gate = actor.gate();
        gate.close();
        let read_progress =
            || -> usize { std::fs::read_to_string(&progress).map_or(0, |s| s.lines().count()) };
        // Wait for the pipe to fill and the actor to stall on it.
        let mut stalled_at = 0;
        for _ in 0..200 {
            thread::sleep(Duration::from_millis(25));
            let now = read_progress();
            if now > 0 && now == stalled_at {
                break;
            }
            stalled_at = now;
        }
        assert!(stalled_at > 0, "the actor never wrote anything");
        assert!(
            stalled_at < 40000,
            "the actor finished with the gate closed"
        );
        thread::sleep(Duration::from_millis(300));
        assert_eq!(
            read_progress(),
            stalled_at,
            "the actor kept going with the gate closed"
        );
        let relayed_before_open = {
            let mut count = 0;
            while rx.try_recv().is_ok() {
                count += 1;
            }
            count
        };
        assert!(
            relayed_before_open < stalled_at,
            "the reader relayed everything the actor wrote despite the closed gate"
        );

        gate.open();
        let events = drain(&rx);
        assert_eq!(lines(&events).len() + relayed_before_open, 40000);
        assert_eq!(read_progress(), 40000);
        assert_eq!(
            actor.end(Duration::from_secs(5)).unwrap().status.code(),
            Some(0)
        );
    }

    #[test]
    fn a_frozen_group_is_stopped_and_end_always_continues_it_before_signalling() {
        let dir = scratch("freeze");
        let (mut actor, rx) = launch(&dir, "sleep 30", 1024);
        actor.freeze().unwrap();
        assert!(actor.frozen());
        let state = || {
            let stat = std::fs::read_to_string(format!("/proc/{}/stat", actor.pid())).unwrap();
            // "<pid> (<comm>) <state> ..." — the state is the field after the
            // closing parenthesis.
            stat.rsplit(") ").next().unwrap().chars().next().unwrap()
        };
        // The stop takes effect when the process next returns to user mode,
        // which can be after the read that follows it — so poll.
        let deadline = Instant::now() + Duration::from_secs(5);
        while state() != 'T' && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(20));
        }
        assert_eq!(state(), 'T', "SIGSTOP did not stop the group");
        // TERM alone would queue behind the stop; `end` lifts it first, so
        // the actor dies of the TERM within the grace rather than of the KILL.
        let ended = actor.end(Duration::from_secs(5)).unwrap();
        assert_eq!(ended.status.signal(), Some(15));
        drop(rx);
    }

    #[test]
    fn dropping_a_frozen_actor_continues_it_and_kills_the_group() {
        let dir = scratch("drop");
        let (mut actor, _rx) = launch(&dir, "sleep 30", 1024);
        let pid = actor.pid();
        actor.freeze().unwrap();
        drop(actor);
        let deadline = Instant::now() + Duration::from_secs(5);
        while group_exists(pid) && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(20));
        }
        assert!(
            !group_exists(pid),
            "the group outlived the wrapper's handle"
        );
    }

    #[test]
    fn teardown_terms_the_group_waits_the_grace_then_kills_survivors() {
        // The leader ignores TERM and so does its child (an ignored signal
        // survives exec), so only the KILL after the grace ends either.
        let dir = scratch("teardown");
        let (actor, rx) = launch(&dir, "trap '' TERM; sleep 30 & sleep 30", 1024);
        let pid = actor.pid();
        thread::sleep(Duration::from_millis(100));
        let started = Instant::now();
        let ended = actor.end(Duration::from_millis(500)).unwrap();
        let took = started.elapsed();
        assert_eq!(ended.status.signal(), Some(9));
        assert!(
            took >= Duration::from_millis(500),
            "did not wait the grace: {took:?}"
        );
        assert!(
            took < Duration::from_secs(5),
            "waited far past the grace: {took:?}"
        );
        // The backgrounded grandchild is a member of the group and dies with
        // it: once nothing references the group id, signalling it fails.
        let deadline = Instant::now() + Duration::from_secs(5);
        while group_exists(pid) && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(20));
        }
        assert!(!group_exists(pid), "a survivor kept the group alive");
        assert!(matches!(
            drain(&rx).last(),
            Some(Event::StdoutClosed(Ok(())))
        ));
    }

    #[test]
    fn a_degenerate_group_id_is_never_signalled() {
        // Sending to group 0 would signal this test process's own group.
        for raw in [0, 1, -1] {
            let error = signal_group(Pid::from_raw(raw), Signal::SIGTERM).unwrap_err();
            assert_eq!(error.kind(), io::ErrorKind::InvalidInput);
        }
        // A group that once existed and is gone is not an error.
        signal_group(Pid::from_raw(i32::MAX), Signal::SIGTERM).unwrap();
    }

    #[test]
    fn a_cooperative_actor_honours_term_within_the_grace() {
        let dir = scratch("term");
        let (actor, _rx) = launch(&dir, "sleep 30", 1024);
        thread::sleep(Duration::from_millis(100));
        let ended = actor.end(Duration::from_secs(5)).unwrap();
        assert_eq!(ended.status.signal(), Some(15));
    }

    #[test]
    fn closing_stdin_ends_a_cooperative_actor_and_its_exit_status_is_kept() {
        let dir = scratch("stdin");
        let (mut actor, rx) = launch(&dir, "cat; exit 7", 1024);
        actor.write_stdin(b"echoed\n").unwrap();
        assert!(actor.stdin_open());
        actor.close_stdin();
        assert!(!actor.stdin_open());
        assert!(actor.write_stdin(b"late\n").is_err());
        let events = drain(&rx);
        assert_eq!(lines(&events), vec!["echoed"]);
        assert!(
            actor.exited().unwrap() || {
                thread::sleep(Duration::from_millis(200));
                actor.exited().unwrap()
            }
        );
        assert_eq!(
            actor.end(Duration::from_secs(5)).unwrap().status.code(),
            Some(7)
        );
    }

    #[test]
    fn a_reader_queues_at_most_the_bound_ahead_of_its_consumer() {
        let dir = scratch("queue");
        let (tx, rx) = event_queue::<Event>();
        let actor = Actor::spawn(
            command(
                &sh("i=0; while [ $i -lt 1000 ]; do echo \"line $i\"; i=$((i+1)); done"),
                &[],
            )
            .unwrap(),
            &dir.join("events.jsonl"),
            &dir.join("stderr.log"),
            LIMITS,
            move |event| {
                let _ = tx.send(event);
            },
        )
        .unwrap();
        let logged =
            || std::fs::read_to_string(dir.join("events.jsonl")).map_or(0, |s| s.lines().count());
        // The queue fills, the reader blocks in its send with one more line
        // already logged, and the log stops there — the bound plus one.
        assert!(wait_until(Duration::from_secs(5), || logged() == EVENT_QUEUE_LINES + 1));
        thread::sleep(Duration::from_millis(200));
        assert_eq!(logged(), EVENT_QUEUE_LINES + 1);
        drop(rx);
        let ended = actor.end(Duration::from_secs(5)).unwrap();
        assert_eq!(ended.status.code(), Some(0));
        assert_eq!(logged(), 1000, "the reader did not finish once released");
    }

    #[test]
    fn the_logs_stop_at_their_caps_exactly_and_the_drains_say_so() {
        let dir = scratch("caps");
        let (tx, rx) = mpsc::channel();
        let actor = Actor::spawn(
            command(
                &sh("printf 'abcd\\nabcd\\nabcd\\n'; printf 'oopsie' >&2; sleep 30"),
                &[],
            )
            .unwrap(),
            &dir.join("events.jsonl"),
            &dir.join("stderr.log"),
            Limits {
                line: 1024,
                stdout: 10,
                stderr: 4,
            },
            move |event| {
                let _ = tx.send(event);
            },
        )
        .unwrap();
        let events = drain(&rx);
        assert_eq!(lines(&events), vec!["abcd", "abcd"]);
        assert!(matches!(events.last(), Some(Event::StdoutClosed(Err(e))) if e == CAP_REACHED));
        let ended = actor.end(Duration::from_secs(5)).unwrap();
        assert_eq!(ended.status.signal(), Some(15));
        assert_eq!(ended.stdout, Some(Err(CAP_REACHED.to_string())));
        assert_eq!(ended.stderr, Some(Err(CAP_REACHED.to_string())));
        assert_eq!(
            std::fs::read_to_string(dir.join("events.jsonl")).unwrap(),
            "abcd\nabcd\n"
        );
        assert_eq!(
            std::fs::read_to_string(dir.join("stderr.log")).unwrap(),
            "oops"
        );
    }

    #[test]
    fn continuous_output_is_cut_at_the_cap_and_the_actor_is_still_ended() {
        let dir = scratch("continuous");
        let (tx, rx) = mpsc::channel();
        let actor = Actor::spawn(
            command(&sh("yes"), &[]).unwrap(),
            &dir.join("events.jsonl"),
            &dir.join("stderr.log"),
            Limits {
                line: 1024,
                stdout: 1000,
                stderr: 1000,
            },
            move |event| {
                let _ = tx.send(event);
            },
        )
        .unwrap();
        let events = drain(&rx);
        assert_eq!(lines(&events).len(), 500);
        let ended = actor.end(Duration::from_millis(500)).unwrap();
        assert!(ended.status.signal().is_some(), "{ended:?}");
        assert_eq!(ended.stdout, Some(Err(CAP_REACHED.to_string())));
        assert_eq!(
            std::fs::metadata(dir.join("events.jsonl")).unwrap().len(),
            1000
        );
    }

    #[test]
    fn a_reader_that_cannot_start_leaves_no_actor_behind() {
        let dir = scratch("partial");
        let probe = format!("partial-{}", std::process::id());
        let mut command = command(&sh("sleep 30"), &[]).unwrap();
        command.env("PROBE", &probe);
        FAIL_STDERR_THREAD.with(|f| f.set(true));
        let result = Actor::spawn(
            command,
            &dir.join("events.jsonl"),
            &dir.join("stderr.log"),
            LIMITS,
            |_| {},
        );
        FAIL_STDERR_THREAD.with(|f| f.set(false));
        let error = result.expect_err("the injected failure was not returned");
        assert!(error.to_string().contains("injected"), "{error}");
        assert!(
            wait_until(Duration::from_secs(5), || probes_alive(&probe) == 0),
            "the actor started before the failure outlived it"
        );
    }

    #[test]
    fn a_descendant_that_left_the_group_is_found_and_killed_at_the_end() {
        let dir = scratch("setsid");
        let probe = format!("setsid-{}", std::process::id());
        let mut command = command(
            &sh("setsid sleep 30 >/dev/null 2>&1 </dev/null & sleep 30"),
            &[],
        )
        .unwrap();
        command.env("PROBE", &probe);
        let actor = Actor::spawn(
            command,
            &dir.join("events.jsonl"),
            &dir.join("stderr.log"),
            LIMITS,
            |_| {},
        )
        .unwrap();
        assert!(
            wait_until(Duration::from_secs(5), || probes_alive(&probe) >= 2),
            "the escaped descendant never appeared"
        );
        let ended = actor.end(Duration::from_millis(500)).unwrap();
        assert_eq!(ended.stragglers, 1, "{ended:?}");
        assert!(
            wait_until(Duration::from_secs(5), || probes_alive(&probe) == 0),
            "a descendant outside the group survived the end"
        );
    }
}
