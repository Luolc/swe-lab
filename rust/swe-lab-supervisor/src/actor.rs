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
//! not written — an oversized one already partly written is rolled back — the
//! stream is not read further, and the run is over. The event log reproduces
//! the actor's stdout byte for byte, a last line left unterminated included.
//! And every descendant carries a mark in its environment from launch, so
//! that one which leaves the process group (`setsid`) is still found and
//! killed when the actor ends.
//!
//! Two limits of that last mechanism are accepted rather than closed, and
//! written here so the acceptance is a decision, not a forgetting. The mark
//! is inherited state: a descendant that clears its own environment before
//! `setsid` is invisible to the sweep. And a pid is not an identity: between
//! the sweep's identity check and its `kill`, the process could exit and the
//! pid be reused — closing that needs `pidfd`, which the pinned `nix` does
//! not wrap and which would otherwise cost a dependency or `unsafe`. Both are
//! accepted because the actor is Claude Code, not an adversary, and because
//! the wrapper runs inside a container that is discarded after the run: the
//! container is the boundary, the sweep is diligence within it. What the
//! sweep cannot prove — `/proc` unreadable, marked processes still alive
//! after its passes — it reports, and the run is not accounted for.

use std::collections::HashSet;
use std::ffi::OsString;
use std::fs::{self, File};
use std::io::{self, Read, Seek, SeekFrom, Write};
use std::os::fd::{AsFd, BorrowedFd, OwnedFd};
use std::os::unix::fs::MetadataExt;
use std::os::unix::process::CommandExt;
use std::process::{Child, ChildStderr, ChildStdin, ChildStdout, Command, ExitStatus, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::mpsc::{self, Receiver, SyncSender};
use std::sync::{Arc, Condvar, Mutex, PoisonError};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use nix::errno::Errno;
use nix::fcntl::{FcntlArg, OFlag, fcntl};
use nix::poll::{PollFd, PollFlags, PollTimeout, poll};
use nix::sys::signal::{Signal, kill, killpg};
use nix::sys::wait::{Id, WaitPidFlag, WaitStatus, waitid};
use nix::unistd::{Pid, pipe2};

use crate::framing::{Frame, Framer};
use crate::signals::{self, Stop};

/// How much is taken off a pipe per read.
const READ_CHUNK_BYTES: usize = 64 * 1024;

/// How often the leader is checked for exit while the grace period runs.
const EXIT_POLL_INTERVAL: Duration = Duration::from_millis(20);
/// How long [`Actor::freeze`] waits for the kernel to have stopped every
/// member of the group. A stop is delivered when a task next returns to
/// user space, which a task in the middle of disk I/O does when that I/O
/// completes; a group that has not settled within this is reported as not
/// confirmed, and the boundary goes unjudged rather than judged on evidence
/// that may still be moving.
const STOP_CONFIRMATION_BOUND: Duration = Duration::from_secs(2);
/// The longest pause between two looks at the group while confirming.
const STOP_POLL_MAX: Duration = Duration::from_millis(16);

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

/// How long a stdin write waits for the pipe to take more before it checks
/// for cancellation again, in milliseconds.
const STDIN_POLL_MS: u8 = 100;

/// The reason a drain stops at its cap.
const CAP_REACHED: &str = "the log's byte cap was reached";

/// The message a drain ends with when it was told to stop before end of
/// file: the grace was spent and something the sweep could not find still
/// held the pipe.
const STOPPED_BEFORE_EOF: &str = "stopped before end of file: something the wrapper could not find still held the pipe when the grace was spent";

/// How long a reader told to stop is given to come back before it is left
/// behind: it returns at once from a wait on its pipe, and the bound is for
/// the one wait no wake-up reaches — a write to its log that has stalled.
const READER_STOP_BOUND: Duration = Duration::from_secs(1);

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
    /// Everything the actor had written to stdout when [`Actor::barrier`]
    /// was asked for has been reported ahead of this — the pipe was found
    /// empty, or at its end. Carries the id the barrier was asked with.
    Barrier(u64),
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
    /// stopped with — [`STOPPED_BEFORE_EOF`] when the grace was spent with
    /// the pipe still held and the reader was told to stop, and came back.
    /// `None` when it did not come back either — stuck in a write to its
    /// log — and was left behind, still holding the log: the caller takes
    /// no digest of that log.
    pub stdout: Option<Result<(), String>>,
    /// The same for the stderr drain.
    pub stderr: Option<Result<(), String>>,
    /// Marked descendants found outside the group once the group was
    /// killed, and killed — or why the sweep could not prove there are none
    /// left, which makes the run not accounted for.
    pub stragglers: Result<usize, String>,
    /// What went wrong on the way out, in order — a continuation, an
    /// observation of the leader, a signal — each step taken all the same.
    /// Empty when nothing did; any entry makes the run not accounted for.
    pub teardown: Vec<String>,
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
    /// The wrapper's own start time, in clock ticks since boot: a floor
    /// under every descendant's — see [`marked_processes`].
    started: u64,
    stdin: Option<ChildStdin>,
    gate: Arc<Gate>,
    /// `SIGSTOP` was sent to the group and no `SIGCONT` has followed. A real
    /// state the wrapper guarantees it leaves before it exits.
    /// The write end of the pipe that wakes the stdout reader for a
    /// barrier, or to stop; the reader holds the read end.
    wake: OwnedFd,
    /// The same for the stderr reader, which is only ever woken to stop.
    wake_stderr: OwnedFd,
    /// Set by [`Actor::end`] once the grace is spent: a reader woken after
    /// this returns without waiting for end of file.
    stopping: Arc<AtomicBool>,
    /// The id of the barrier asked for and not yet reported, or zero.
    barrier: Arc<AtomicU64>,
    frozen: bool,
    /// Marked descendants outside the group that [`Actor::freeze`]
    /// stopped one by one, to be resumed one by one — each with the
    /// identity it was held under.
    held: Vec<Held>,
    reaped: bool,
    stdout_drain: Option<JoinHandle<Result<(), String>>>,
    stderr_drain: Option<JoinHandle<Result<(), String>>>,
}

impl Actor {
    /// Launch the actor from a prepared command (see [`command`]), in its
    /// own process group, all three standard streams held here, its
    /// environment marked ([`MARK_ENV`]). Every line of stdout goes to
    /// `event_log`, stderr to `stderr_log` — two files the caller opened
    /// (the wrapper opens every output through
    /// [`Outputs`](crate::outputs::Outputs)). `events` is called from the
    /// reader threads, in order per stream; it may block, and the reader
    /// with it.
    ///
    /// # Errors
    ///
    /// The two logs are one file, the actor cannot be spawned, or a reader
    /// thread cannot be started — in which case the actor that was already
    /// started is killed and reaped before this returns.
    pub fn spawn<F>(
        mut command: Command,
        event_log: File,
        stderr_log: File,
        limits: Limits,
        events: F,
    ) -> io::Result<Self>
    where
        F: Fn(Event) + Send + Sync + 'static,
    {
        // Two handles to one file would let the drains overwrite each
        // other while both report success. The wrapper's door for outputs
        // refuses that by construction; so does this, for any caller.
        if same_file(&event_log, &stderr_log)? {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "the event log and the stderr log are one file",
            ));
        }
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
        // Read before the actor exists, so that the floor is below it.
        let floor = match start_time(Pid::this()) {
            Probe::Present(started) => started,
            other => {
                return Err(io::Error::other(format!(
                    "the wrapper's own start time cannot be read: {other:?}"
                )));
            }
        };
        // The reader's wake-up: a byte on this pipe makes it look for a
        // barrier request. Close-on-exec, so the actor never holds it.
        let (wake_read, wake_write) = pipe2(OFlag::O_CLOEXEC | OFlag::O_NONBLOCK)?;
        let (wake_stderr_read, wake_stderr_write) = pipe2(OFlag::O_CLOEXEC | OFlag::O_NONBLOCK)?;
        let barrier = Arc::new(AtomicU64::new(0));
        let stopping = Arc::new(AtomicBool::new(false));
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
            wake: wake_write,
            wake_stderr: wake_stderr_write,
            stopping: Arc::clone(&stopping),
            barrier: Arc::clone(&barrier),
            frozen: false,
            held: Vec::new(),
            started: floor,
            reaped: false,
            stdout_drain: None,
            stderr_drain: None,
        };
        actor.stdin = actor.child.stdin.take();
        if let Some(stdin) = &actor.stdin {
            // The write end only: the actor's read end is another file
            // description, and blocks as before. This is what lets a write
            // be interrupted rather than wait for good on an actor that
            // never reads.
            fcntl(stdin.as_fd(), FcntlArg::F_SETFL(OFlag::O_NONBLOCK))?;
        }
        let (Some(stdout), Some(stderr)) = (actor.child.stdout.take(), actor.child.stderr.take())
        else {
            return Err(io::Error::other(
                "actor spawned without piped stdout and stderr",
            ));
        };
        let events = Arc::new(events);
        let gate = Arc::clone(&actor.gate);
        let report = Arc::clone(&events);
        let stop_stdout = Arc::clone(&stopping);
        actor.stdout_drain = Some(thread::Builder::new().name("actor-stdout".into()).spawn(
            move || {
                let wakeup = Wakeup {
                    pipe: &wake_read,
                    barrier: &barrier,
                    stopping: &stop_stdout,
                };
                let result = drain_stdout(
                    stdout,
                    &gate,
                    EventLog::new(event_log),
                    limits,
                    &*report,
                    wakeup,
                )
                .map_err(|e| e.to_string());
                report(Event::StdoutClosed(result.clone()));
                result
            },
        )?);
        actor.stderr_drain = Some(spawn_stderr_reader(
            stderr,
            stderr_log,
            limits.stderr,
            wake_stderr_read,
            stopping,
            events,
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

    /// Write on the actor's stdin, all of it, unless the wrapper is asked to
    /// stop meanwhile or the actor takes nothing off the pipe for `stall`.
    /// A pipe holds 64 KiB; a longer write waits for the actor to read, and
    /// this is where a run whose actor never reads would otherwise hang.
    ///
    /// # Errors
    ///
    /// `BrokenPipe`: stdin was already closed deliberately, or the actor is
    /// gone. `Interrupted`: cancelled. `TimedOut`: no progress for `stall`.
    /// Anything else is the write failing. Each is the supervisor's channel
    /// failing, which the caller classifies.
    pub fn write_stdin(&mut self, bytes: &[u8], stop: &Stop, stall: Duration) -> io::Result<()> {
        let Some(stdin) = self.stdin.as_mut() else {
            return Err(io::Error::new(
                io::ErrorKind::BrokenPipe,
                "actor stdin was already closed",
            ));
        };
        let mut offset = 0;
        let mut last_progress = Instant::now();
        while offset < bytes.len() {
            if signals::requested(stop).is_some() {
                return Err(io::Error::new(
                    io::ErrorKind::Interrupted,
                    "cancelled while writing on the actor's stdin",
                ));
            }
            match stdin.write(&bytes[offset..]) {
                Ok(0) => return Err(io::ErrorKind::WriteZero.into()),
                Ok(written) => {
                    offset += written;
                    last_progress = Instant::now();
                }
                Err(error) if error.kind() == io::ErrorKind::Interrupted => {}
                Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                    if last_progress.elapsed() >= stall {
                        return Err(io::Error::new(
                            io::ErrorKind::TimedOut,
                            "the actor took nothing off its stdin within the grace",
                        ));
                    }
                    let mut wait = [PollFd::new(stdin.as_fd(), PollFlags::POLLOUT)];
                    match poll(&mut wait, PollTimeout::from(STDIN_POLL_MS)) {
                        Ok(_) | Err(Errno::EINTR) => {}
                        Err(errno) => return Err(io::Error::from(errno)),
                    }
                }
                Err(error) => return Err(error),
            }
        }
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

    /// `SIGSTOP` the actor — the whole group, and every marked descendant
    /// that left it — and return once each is confirmed stopped. The stdout
    /// pipe keeps draining, so nothing the actor already wrote is lost, and
    /// it writes nothing more until [`Actor::thaw`].
    ///
    /// The signal alone is not the confirmation: `killpg` names the members
    /// it finds, and a member in the middle of a fork when it is sent has a
    /// child a moment later that the signal never reached; and a descendant
    /// that called `setsid` is in no group `killpg` is sent to, while it
    /// may still hold the actor's stdout. So the actor's processes are read
    /// from `/proc` — the group, and outside it whatever carries the mark
    /// and started after the wrapper — until two looks in a row find the
    /// same set, every thread of each stopped; a look that finds one running
    /// signals it, the group again or the escaped one by pid.
    ///
    /// # Errors
    ///
    /// The group could not be signalled, or the actor could not be
    /// confirmed stopped within [`STOP_CONFIRMATION_BOUND`] — or proved so:
    /// a process of this user's own that `/proc` refuses to show is a state
    /// unknown. Whatever was stopped stays stopped either way; the caller
    /// thaws it.
    pub fn freeze(&mut self) -> Result<(), String> {
        signal_group(self.group, Signal::SIGSTOP).map_err(|e| format!("SIGSTOP: {e}"))?;
        self.frozen = true;
        let needle = format!("{MARK_ENV}={}", self.mark);
        confirm_stopped(self.group, &needle, self.started, &mut self.held)
    }

    /// Ask the stdout reader for a barrier: once it has reported every line
    /// the actor had written by now, it reports [`Event::Barrier`] with
    /// `id`, in order behind them. Meaningful for a group that is frozen —
    /// then "by now" is "ever, until the thaw". `id` is not zero.
    ///
    /// # Errors
    ///
    /// The reader could not be woken.
    pub fn barrier(&self, id: u64) -> io::Result<()> {
        self.barrier.store(id, Ordering::SeqCst);
        match nix::unistd::write(&self.wake, &[1u8]) {
            // A byte already waiting wakes the reader just the same.
            Ok(_) | Err(Errno::EAGAIN) => Ok(()),
            Err(errno) => Err(io::Error::from(errno)),
        }
    }

    /// `SIGCONT` everything [`Actor::freeze`] stopped, if it did: each
    /// held escaped descendant by pid, once its identity is checked again
    /// — a pid reused since names another process, which is left alone —
    /// and then the group. Every continuation is attempted whatever the
    /// others did, and the group is not frozen after, whatever they did:
    /// the attempts were made, and a retry would make the same ones.
    /// Idempotent, and tolerant of processes that have since disappeared.
    ///
    /// # Errors
    ///
    /// A held descendant's identity could not be proved, or a process that
    /// exists could not be signalled; the error says how many, and the
    /// first reason.
    pub fn thaw(&mut self) -> io::Result<()> {
        if !self.frozen {
            return Ok(());
        }
        let held = std::mem::take(&mut self.held);
        let result = resume(&held, self.group);
        self.frozen = false;
        result
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
    /// up to `grace` more for the two drains to reach end of file. A drain
    /// still going then — something the sweep could not find holds its pipe
    /// — is told to stop and woken, comes back at once and is joined, on
    /// record as stopped short. The one reader no wake-up reaches is one
    /// stuck in a write to its log; it is left behind after
    /// [`READER_STOP_BOUND`] more, reported unfinished (`None`), and the
    /// caller takes no digest of that log. Every step is taken whatever the
    /// ones before it did; what went wrong on the way is carried in
    /// [`Ended::teardown`]. A consumer that stopped receiving must drop its
    /// receiver before calling this, or a reader blocked on the full queue
    /// never reaches it.
    ///
    /// # Errors
    ///
    /// The leader could not be reaped — its status is unknown. Returned
    /// only once the drains are settled all the same.
    pub fn end(mut self, grace: Duration) -> io::Result<Ended> {
        // Every step is taken whatever the ones before it did; what went
        // wrong is carried out, not returned early — nothing leaves here
        // while a drain may still write the logs the caller digests next.
        let mut teardown = Vec::new();
        note(&mut teardown, "SIGCONT", self.thaw());
        self.gate.open();
        let exited = match self.exited() {
            Ok(exited) => exited,
            Err(error) => {
                note(&mut teardown, "observing the actor", Err(error));
                false
            }
        };
        if !exited {
            match signal_group(self.group, Signal::SIGTERM) {
                Ok(()) => {
                    let deadline = Instant::now() + grace;
                    loop {
                        match self.exited() {
                            Ok(false) if Instant::now() < deadline => {
                                thread::sleep(EXIT_POLL_INTERVAL);
                            }
                            // Exited, or the grace is spent.
                            Ok(_) => break,
                            Err(error) => {
                                note(&mut teardown, "observing the actor", Err(error));
                                break;
                            }
                        }
                    }
                }
                Err(error) => note(&mut teardown, "SIGTERM", Err(error)),
            }
        }
        note(
            &mut teardown,
            "SIGKILL",
            signal_group(self.group, Signal::SIGKILL),
        );
        let status = self.child.wait();
        self.reaped = status.is_ok();
        let stragglers = sweep(&self.mark, self.group, self.started);
        // Bounded, in two stages. First one `grace` for both drains
        // together, for the reads to reach end of file. A drain still going
        // then is one whose pipe something the sweep could not find still
        // holds: it is told to stop and woken, and comes back at once, the
        // drain reported as stopped short — unless it is stuck in a write
        // to its log, the one wait no wake-up reaches; that one is left
        // behind after `READER_STOP_BOUND` more and reported unfinished,
        // and the caller takes no digest of a log a reader may still write.
        // Never an unbounded wait: a held pipe must not become a hung
        // wrapper.
        let deadline = Instant::now() + grace;
        let mut stdout = finish(&mut self.stdout_drain, deadline);
        let mut stderr = finish(&mut self.stderr_drain, deadline);
        if stdout.is_none() || stderr.is_none() {
            self.stopping.store(true, Ordering::SeqCst);
            let _ = nix::unistd::write(&self.wake, &[1u8]);
            let _ = nix::unistd::write(&self.wake_stderr, &[1u8]);
            let deadline = Instant::now() + READER_STOP_BOUND;
            if stdout.is_none() {
                stdout = finish(&mut self.stdout_drain, deadline);
            }
            if stderr.is_none() {
                stderr = finish(&mut self.stderr_drain, deadline);
            }
        }
        let status = status.map_err(|error| {
            let earlier = if teardown.is_empty() {
                String::new()
            } else {
                format!(" (after: {})", teardown.join("; "))
            };
            io::Error::other(format!("reaping the leader: {error}{earlier}"))
        })?;
        Ok(Ended {
            status,
            stdout,
            stderr,
            stragglers,
            teardown,
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
        let _ = sweep(&self.mark, self.group, self.started);
    }
}

/// The stderr reader's thread.
fn spawn_stderr_reader<F>(
    stderr: ChildStderr,
    log: File,
    cap: u64,
    wake: OwnedFd,
    stopping: Arc<AtomicBool>,
    events: Arc<F>,
) -> io::Result<JoinHandle<Result<(), String>>>
where
    F: Fn(Event) + Send + Sync + 'static,
{
    #[cfg(test)]
    if FAIL_STDERR_THREAD.with(std::cell::Cell::get) {
        return Err(io::Error::other(
            "injected: the stderr reader could not start",
        ));
    }
    thread::Builder::new()
        .name("actor-stderr".into())
        .spawn(move || {
            let result =
                drain_stderr(stderr, log, cap, &wake, &stopping).map_err(|e| e.to_string());
            events(Event::StderrClosed(result.clone()));
            result
        })
}

/// A teardown step's failure, noted and moved past.
fn note(teardown: &mut Vec<String>, step: &str, result: io::Result<()>) {
    if let Err(error) = result {
        teardown.push(format!("{step}: {error}"));
    }
}

/// An escaped descendant [`Actor::freeze`] stopped by pid: the pid, and the
/// start time it had then — with the pid, the nearest thing to an identity
/// `/proc` offers — checked again before the pid is signalled.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct Held {
    pid: Pid,
    started: u64,
}

#[cfg(test)]
thread_local! {
    /// Makes the next held continuation fail, for the test that the rest
    /// are still attempted.
    static FAIL_NEXT_CONTINUE: std::cell::Cell<bool> = const { std::cell::Cell::new(false) };
}

/// Continue every held descendant whose identity still holds, then the
/// group — every one attempted; the error, if any, counts the failures and
/// carries the first reason.
fn resume(held: &[Held], group: Pid) -> io::Result<()> {
    let mut failed = 0usize;
    let mut first: Option<String> = None;
    for descendant in held {
        if let Err(reason) = continue_held(*descendant) {
            failed += 1;
            first.get_or_insert(reason);
        }
    }
    let group_result = signal_group(group, Signal::SIGCONT);
    if failed == 0 {
        return group_result;
    }
    let reason = format!(
        "{failed} of {} held descendants could not be continued: {}",
        held.len(),
        first.unwrap_or_default()
    );
    match group_result {
        Ok(()) => Err(io::Error::other(reason)),
        Err(error) => Err(io::Error::other(format!("{reason}; the group: {error}"))),
    }
}

/// `SIGCONT` to one held descendant, if it is still the process that was
/// held: one that is gone, or whose pid another process has since, is
/// nothing to resume; an identity that cannot be proved is an error, not a
/// signal sent on a guess. The window between the check and the signal is
/// the residue the sweep accepts too (see [`sweep`]).
fn continue_held(descendant: Held) -> Result<(), String> {
    #[cfg(test)]
    if FAIL_NEXT_CONTINUE.with(std::cell::Cell::take) {
        return Err("injected".to_string());
    }
    match start_time(descendant.pid) {
        Probe::Present(started) if started == descendant.started => {}
        Probe::Present(_) | Probe::Gone => return Ok(()),
        Probe::Unprovable(reason) => return Err(reason),
    }
    match kill(descendant.pid, Signal::SIGCONT) {
        Ok(()) | Err(Errno::ESRCH) => Ok(()),
        Err(errno) => Err(format!("SIGCONT: {errno}")),
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
        // Still going: kept, so that a later bound can be waited on.
        *drain = Some(handle);
        return None;
    }
    Some(
        handle
            .join()
            .unwrap_or_else(|_| Err("the drain thread panicked".to_string())),
    )
}

/// A live process carrying the actor's mark.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct Marked {
    pid: Pid,
    group: Pid,
    /// The kernel's start time of the process (clock ticks since boot): with
    /// the pid, the nearest thing to an identity `/proc` offers.
    started: u64,
}

/// Kill every process whose environment carries the actor's mark and whose
/// process group is not the actor's — a descendant that called `setsid` —
/// until none is found or the passes run out. Returns how many distinct
/// processes were found (one still dying is seen by the next pass too), or
/// why there is no proof that none is left: `/proc` could not be read, or
/// marked processes were still alive after the last pass. A pid is checked
/// against the start time it was found with just before it is signalled;
/// the window between that check and the `kill` is the accepted residue
/// described in the module doc.
fn sweep(mark: &str, group: Pid, floor: u64) -> Result<usize, String> {
    let needle = format!("{MARK_ENV}={mark}");
    let escaped = |found: Vec<Marked>| -> Vec<Marked> {
        found.into_iter().filter(|m| m.group != group).collect()
    };
    let mut killed = HashSet::new();
    for _ in 0..SWEEP_PASSES {
        let stragglers = escaped(marked_processes(&needle, floor)?);
        if stragglers.is_empty() {
            return Ok(killed.len());
        }
        for straggler in stragglers {
            match start_time(straggler.pid) {
                Probe::Present(started) if started == straggler.started => {}
                // Not the process that was found: gone, or its pid reused.
                Probe::Present(_) | Probe::Gone => continue,
                Probe::Unprovable(reason) => return Err(reason),
            }
            if kill(straggler.pid, Signal::SIGKILL).is_ok() {
                killed.insert(straggler.pid);
            }
        }
        thread::sleep(EXIT_POLL_INTERVAL);
    }
    let left = escaped(marked_processes(&needle, floor)?).len();
    if left == 0 {
        Ok(killed.len())
    } else {
        Err(format!(
            "{left} marked process(es) outside the group still alive after {SWEEP_PASSES} sweep passes"
        ))
    }
}

/// Every live process of this user's own (see [`own`]) that started at or
/// after `floor` (clock ticks since boot) and whose initial environment
/// holds `needle` as one entry.
///
/// The floor is the wrapper's own start time: a process that started
/// strictly before it cannot be a descendant of the actor, whatever its
/// environment says — a fact read from `stat`, which is readable even when
/// `environ` is not (a hosted CI runner has live processes of the job's
/// own uid whose environment it may not read). Start times are whole
/// ticks, so a process on the same tick as the wrapper is kept, not
/// excluded: the boundary falls on the side where the cost is one more
/// read. A read of one of the remaining processes that fails is a state
/// unknown — the answer cannot be trusted, and says so.
fn marked_processes(needle: &str, floor: u64) -> Result<Vec<Marked>, String> {
    let entries = fs::read_dir("/proc").map_err(|e| format!("listing /proc: {e}"))?;
    let me = std::process::id();
    let mut found = Vec::new();
    for entry in entries {
        let entry = entry.map_err(|e| format!("listing /proc: {e}"))?;
        let Some(pid) = entry
            .file_name()
            .to_str()
            .and_then(|n| n.parse::<u32>().ok())
        else {
            continue;
        };
        if pid == me {
            continue;
        }
        let owned = match own(pid) {
            Probe::Present(Some(owned)) => owned,
            Probe::Present(None) | Probe::Gone => continue,
            Probe::Unprovable(reason) => return Err(reason),
        };
        let stat = match stat_fields(owned) {
            Probe::Present(stat) => stat,
            Probe::Gone => continue,
            Probe::Unprovable(reason) => return Err(reason),
        };
        if stat.exited() {
            // No environment left to read — this kernel refuses the read
            // of an exited process rather than returning none, so `stat`
            // is read first and is what says so.
            continue;
        }
        if stat.started < floor {
            // Older than the wrapper: not a descendant, whatever it holds.
            continue;
        }
        let environ = match proc_file(owned.0, "environ") {
            Probe::Present(environ) => environ,
            Probe::Gone => continue,
            // Refused. One that exited between the two reads is what
            // `stat` now says it is; a live process of this user's own
            // that cannot be read is the state unknown.
            Probe::Unprovable(reason) => match stat_fields(owned) {
                Probe::Present(stat) if stat.exited() => continue,
                Probe::Gone => continue,
                Probe::Present(_) => return Err(reason),
                Probe::Unprovable(reason) => return Err(reason),
            },
        };
        if !environ.split(|b| *b == 0).any(|e| e == needle.as_bytes()) {
            continue;
        }
        found.push(Marked {
            pid: Pid::from_raw(i32::try_from(pid).map_err(|_| "pid does not fit".to_string())?),
            group: Pid::from_raw(stat.group),
            started: stat.started,
        });
    }
    Ok(found)
}

/// What one read of a process's `/proc` entry established. A read that
/// fails is not a process that is gone: the two are different states, and
/// this type keeps them apart at every use — a caller has to say what it
/// does with an answer it cannot trust, and the compiler holds it to that.
/// (A two-state answer is how "cannot read" once became "gone", and a
/// marked descendant outlived the sweep.)
#[derive(Debug)]
enum Probe<T> {
    /// The process is there; this is what was read.
    Present(T),
    /// The process is gone, on the kernel's word: `ENOENT` or `ESRCH`.
    Gone,
    /// The read failed — permission denied included: a process this user
    /// may not read is not a process that is not there — or what it
    /// returned did not parse. The process's state is not known, and the
    /// reason says why.
    Unprovable(String),
}

/// The three states from a read's result. Only the kernel's word that
/// there is no such process is `Gone`; every other failure is a state
/// unknown, `what` naming the read for the reason.
fn probe<T>(result: io::Result<T>, what: impl FnOnce() -> String) -> Probe<T> {
    match result {
        Ok(value) => Probe::Present(value),
        Err(error)
            if error.kind() == io::ErrorKind::NotFound
                || error.raw_os_error() == Some(Errno::ESRCH as i32) =>
        {
            Probe::Gone
        }
        Err(error) => Probe::Unprovable(format!("{}: {error}", what())),
    }
}

/// The bytes of `/proc/<pid>/<name>`. Bytes, not a string: what a process
/// names itself is any bytes at all, and it appears in `stat`.
fn proc_file(pid: u32, name: &str) -> Probe<Vec<u8>> {
    probe(fs::read(format!("/proc/{pid}/{name}")), || {
        format!("reading /proc/{pid}/{name}")
    })
}

/// The uid that owns `/proc/<pid>`: the directory's, so readable whatever
/// is inside it.
fn owner(pid: u32) -> Probe<u32> {
    probe(
        fs::metadata(format!("/proc/{pid}")).map(|metadata| metadata.uid()),
        || format!("reading /proc/{pid}"),
    )
}

/// A pid whose `/proc/<pid>` this user owns, made only by [`own`]. What is
/// read of a process by way of it — its `stat`, and in particular the rule
/// that no address space means it has exited, which would take a kernel
/// thread for an exited process — cannot be reached for a process that is
/// not ours: the ownership check is that rule's premise, and this type is
/// what holds the two together, rather than the order of two lines.
#[derive(Debug, Clone, Copy)]
struct Owned(u32);

/// Whose the process is, from the owner of its `/proc/<pid>` directory.
/// `Present(Some)` is this user's own. `Present(None)` is another user's,
/// which cannot be a descendant of this process — a change of uid is not
/// in the model — and could not be killed from here either: a conclusion,
/// not a read that failed, and what keeps a box full of other users'
/// processes (or one mounted `hidepid`) from failing every sweep. One of
/// this user's own that made itself undumpable (a setuid exec) shows as
/// root's, and is outside the sweep's sight the same way.
fn own(pid: u32) -> Probe<Option<Owned>> {
    let mine = match owner(std::process::id()) {
        Probe::Present(uid) => uid,
        Probe::Gone => {
            return Probe::Unprovable("the wrapper's own /proc entry is not there".to_string());
        }
        Probe::Unprovable(reason) => return Probe::Unprovable(reason),
    };
    match owner(pid) {
        Probe::Present(uid) if uid == mine => Probe::Present(Some(Owned(pid))),
        Probe::Present(_) => Probe::Present(None),
        Probe::Gone => Probe::Gone,
        Probe::Unprovable(reason) => Probe::Unprovable(reason),
    }
}

/// What `/proc/<pid>/stat` says of a process, of the fields read here.
#[derive(Debug, Clone, Copy)]
struct Stat {
    /// The state letter: `R`, `S`, `D`, `T`, `Z`, `X`, ...
    state: u8,
    /// The parent.
    ppid: u32,
    /// The process group.
    group: i32,
    /// The start time, in clock ticks since boot.
    started: u64,
    /// The address space, in bytes: zero once the process has given it up.
    vsize: u64,
}

impl Stat {
    /// Exited, or on its way out: a zombie or dead by the state letter, or
    /// a process that has already given up its address space — it runs no
    /// more code, its environment went with the space, and it is a zombie
    /// moments from now (its state still reads `R` until then). Nothing to
    /// kill, either way; this is the kernel's own account, not a read that
    /// failed.
    fn exited(self) -> bool {
        matches!(self.state, b'Z' | b'X') || self.vsize == 0
    }
}

/// The state, parent, process group, start time and address space of a
/// process, from `/proc/<pid>/stat`.
fn stat_fields(pid: Owned) -> Probe<Stat> {
    let Owned(pid) = pid;
    match proc_file(pid, "stat") {
        Probe::Present(stat) => parse_stat(&stat).map_or_else(
            || Probe::Unprovable(format!("/proc/{pid}/stat did not parse")),
            Probe::Present,
        ),
        Probe::Gone => Probe::Gone,
        Probe::Unprovable(reason) => Probe::Unprovable(reason),
    }
}

/// The fields read here, from a `stat` line: those after the comm's
/// closing parenthesis — the last one, since the comm may hold parentheses
/// of its own — of which the first is the state, the second the parent,
/// the third the group, the twentieth the start time and the twenty-first
/// the address space. A thread's line (`task/<tid>/stat`) has the same
/// shape.
fn parse_stat(stat: &[u8]) -> Option<Stat> {
    let close = stat.iter().rposition(|b| *b == b')')?;
    let fields: Vec<&[u8]> = stat[close + 1..]
        .split(u8::is_ascii_whitespace)
        .filter(|field| !field.is_empty())
        .collect();
    let field = |index: usize| std::str::from_utf8(fields.get(index)?).ok();
    Some(Stat {
        state: *fields.first()?.first()?,
        ppid: field(1)?.parse().ok()?,
        group: field(2)?.parse().ok()?,
        started: field(19)?.parse().ok()?,
        vsize: field(20)?.parse().ok()?,
    })
}

/// How a process stands with respect to a group stop.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Halt {
    /// Every thread is stopped (or already gone).
    Stopped,
    /// Waiting in `kernel_clone` for a child it vforked to exec or exit: it
    /// runs no code of its own until then, and neither does the child if
    /// the child is stopped. Stopped, by any measure but the letter — once
    /// the child is found stopped too.
    VforkWait,
    /// A thread runs, sleeps interruptibly, or waits in the kernel for
    /// something other than a vforked child; it stops when it next returns
    /// to user space.
    Running,
}

/// One live process of the actor's: a member of its group, or a marked
/// descendant outside it.
#[derive(Debug, Clone, Copy)]
struct Member {
    pid: u32,
    ppid: u32,
    /// The start time, in clock ticks since boot: the identity a held pid
    /// is checked against before it is continued.
    started: u64,
    halt: Halt,
    in_group: bool,
}

/// The actor's live processes among this user's own: the members of
/// `group`, and outside it every process that started at or after `floor`
/// whose environment carries `needle` (the mark; see [`marked_processes`]
/// for why the floor and what a refused read means).
fn members(group: Pid, needle: &str, floor: u64) -> Result<Vec<Member>, String> {
    let entries = fs::read_dir("/proc").map_err(|e| format!("listing /proc: {e}"))?;
    let me = std::process::id();
    let mut members = Vec::new();
    for entry in entries {
        let entry = entry.map_err(|e| format!("listing /proc: {e}"))?;
        let Some(pid) = entry
            .file_name()
            .to_str()
            .and_then(|n| n.parse::<u32>().ok())
        else {
            continue;
        };
        if pid == me {
            continue;
        }
        let owned = match own(pid) {
            Probe::Present(Some(owned)) => owned,
            Probe::Present(None) | Probe::Gone => continue,
            Probe::Unprovable(reason) => return Err(reason),
        };
        let stat = match stat_fields(owned) {
            Probe::Present(stat) => stat,
            Probe::Gone => continue,
            Probe::Unprovable(reason) => return Err(reason),
        };
        if stat.exited() {
            continue;
        }
        let in_group = stat.group == group.as_raw();
        if !in_group {
            if stat.started < floor {
                continue;
            }
            let environ = match proc_file(owned.0, "environ") {
                Probe::Present(environ) => environ,
                Probe::Gone => continue,
                Probe::Unprovable(reason) => match stat_fields(owned) {
                    Probe::Present(stat) if stat.exited() => continue,
                    Probe::Gone => continue,
                    Probe::Present(_) => return Err(reason),
                    Probe::Unprovable(reason) => return Err(reason),
                },
            };
            if !environ.split(|b| *b == 0).any(|e| e == needle.as_bytes()) {
                continue;
            }
        }
        let halt = match threads_halt(owned) {
            Probe::Present(halt) => halt,
            Probe::Gone => continue,
            Probe::Unprovable(reason) => return Err(reason),
        };
        members.push(Member {
            pid,
            ppid: stat.ppid,
            started: stat.started,
            halt,
            in_group,
        });
    }
    Ok(members)
}

/// How the threads of one process stand, from `/proc/<pid>/task/*/stat`:
/// the process's own line shows its main thread only.
fn threads_halt(pid: Owned) -> Probe<Halt> {
    let Owned(pid) = pid;
    let tasks = match probe(
        fs::read_dir(format!("/proc/{pid}/task")).and_then(Iterator::collect::<io::Result<Vec<_>>>),
        || format!("listing /proc/{pid}/task"),
    ) {
        Probe::Present(tasks) => tasks,
        Probe::Gone => return Probe::Gone,
        Probe::Unprovable(reason) => return Probe::Unprovable(reason),
    };
    let mut halt = Halt::Stopped;
    for task in tasks {
        let Some(tid) = task.file_name().to_str().map(str::to_owned) else {
            continue;
        };
        let state = match proc_file(pid, &format!("task/{tid}/stat")) {
            Probe::Present(stat) => match parse_stat(&stat) {
                Some(stat) => stat.state,
                None => {
                    return Probe::Unprovable(format!("/proc/{pid}/task/{tid}/stat did not parse"));
                }
            },
            Probe::Gone => continue,
            Probe::Unprovable(reason) => return Probe::Unprovable(reason),
        };
        match state {
            b'T' | b't' | b'Z' | b'X' => {}
            b'D' => match proc_file(pid, &format!("task/{tid}/wchan")) {
                Probe::Present(wchan) if wchan.trim_ascii() == b"kernel_clone" => {
                    halt = Halt::VforkWait;
                }
                Probe::Present(_) | Probe::Gone => return Probe::Present(Halt::Running),
                Probe::Unprovable(reason) => return Probe::Unprovable(reason),
            },
            _ => return Probe::Present(Halt::Running),
        }
    }
    Probe::Present(halt)
}

/// Wait until every process of the actor's is confirmed stopped: two looks
/// in a row that find the same members, each stopped, with a signal after
/// any look that finds one that is not — the group again for a member of
/// it, the pid for an escaped one, which is then recorded in `held` for
/// the thaw (see [`Actor::freeze`]).
fn confirm_stopped(
    group: Pid,
    needle: &str,
    floor: u64,
    held: &mut Vec<Held>,
) -> Result<(), String> {
    let deadline = Instant::now() + STOP_CONFIRMATION_BOUND;
    let mut previous: Option<Vec<u32>> = None;
    let mut pause = Duration::from_millis(1);
    loop {
        let members = members(group, needle, floor)?;
        let stopped = |member: &Member| match member.halt {
            Halt::Stopped => true,
            Halt::Running => false,
            Halt::VforkWait => members
                .iter()
                .any(|child| child.ppid == member.pid && child.halt == Halt::Stopped),
        };
        let (halted, running): (Vec<&Member>, Vec<&Member>) =
            members.iter().partition(|member| stopped(member));
        if running.is_empty() {
            let pids: Vec<u32> = halted.iter().map(|member| member.pid).collect();
            if previous.as_ref() == Some(&pids) {
                return Ok(());
            }
            previous = Some(pids);
        } else {
            previous = None;
            if running.iter().any(|member| member.in_group) {
                signal_group(group, Signal::SIGSTOP).map_err(|e| format!("SIGSTOP: {e}"))?;
            }
            for member in running.iter().filter(|member| !member.in_group) {
                let pid = Pid::from_raw(
                    i32::try_from(member.pid).map_err(|_| "pid does not fit".to_string())?,
                );
                match kill(pid, Signal::SIGSTOP) {
                    Ok(()) => {
                        if !held.iter().any(|h| h.pid == pid) {
                            held.push(Held {
                                pid,
                                started: member.started,
                            });
                        }
                    }
                    Err(Errno::ESRCH) => {}
                    Err(errno) => return Err(format!("SIGSTOP to an escaped descendant: {errno}")),
                }
            }
        }
        if Instant::now() >= deadline {
            return Err(format!(
                "the actor was not confirmed stopped within {STOP_CONFIRMATION_BOUND:?}: {} of {} of its processes still running",
                running.len(),
                members.len()
            ));
        }
        thread::sleep(pause);
        pause = (pause * 2).min(STOP_POLL_MAX);
    }
}

/// The start time of the process at `pid` — one of this user's own; another
/// user's process there now is not the one that was found, and reads as
/// gone.
fn start_time(pid: Pid) -> Probe<u64> {
    let Ok(pid) = u32::try_from(pid.as_raw()) else {
        return Probe::Unprovable("a pid below zero".to_string());
    };
    let owned = match own(pid) {
        Probe::Present(Some(owned)) => owned,
        Probe::Present(None) | Probe::Gone => return Probe::Gone,
        Probe::Unprovable(reason) => return Probe::Unprovable(reason),
    };
    match stat_fields(owned) {
        Probe::Present(stat) => Probe::Present(stat.started),
        Probe::Gone => Probe::Gone,
        Probe::Unprovable(reason) => Probe::Unprovable(reason),
    }
}

/// Whether two open files are one file — by device and inode, so that a
/// hard link or a symlink to the other is caught, not only the same path.
fn same_file(a: &File, b: &File) -> io::Result<bool> {
    let (a, b) = (a.metadata()?, b.metadata()?);
    Ok((a.dev(), a.ino()) == (b.dev(), b.ino()))
}

/// The event log: every record whole or absent. A line is committed as one
/// write; an oversized record is written piece by piece from a remembered
/// offset and rolled back to it when a piece cannot be written — the cap
/// reached, or the filesystem refusing it after accepting a prefix. Nothing
/// is buffered: each write reaches the file, so the artifact is complete up
/// to the last whole record at any moment.
struct EventLog {
    file: File,
    written: u64,
    /// Where the record being written started, while one is open.
    record_start: Option<u64>,
}

impl EventLog {
    fn new(file: File) -> Self {
        Self {
            file,
            written: 0,
            record_start: None,
        }
    }

    /// Write one piece of the current record, opening the record if none is
    /// open; on failure the whole record is gone from the file.
    fn append(&mut self, bytes: &[u8]) -> io::Result<()> {
        let start = *self.record_start.get_or_insert(self.written);
        match self.file.write_all(bytes) {
            Ok(()) => {
                self.written += u64::try_from(bytes.len()).unwrap_or(u64::MAX);
                Ok(())
            }
            Err(error) => {
                self.roll_back(start);
                Err(error)
            }
        }
    }

    /// The current record is complete.
    fn close(&mut self) {
        self.record_start = None;
    }

    /// The current record cannot be completed: take it off the file.
    fn abandon(&mut self) {
        if let Some(start) = self.record_start {
            self.roll_back(start);
        }
    }

    fn roll_back(&mut self, start: u64) {
        // Best effort on a path that is already failing; the drain's error
        // is what the run is classified by.
        let _ = self.file.set_len(start);
        let _ = self.file.seek(SeekFrom::Start(start));
        self.written = start;
        self.record_start = None;
    }
}

fn drain_stdout(
    mut stdout: ChildStdout,
    gate: &Gate,
    mut log: EventLog,
    limits: Limits,
    events: &dyn Fn(Event),
    wakeup: Wakeup<'_>,
) -> io::Result<()> {
    let Wakeup {
        pipe: wake,
        barrier,
        stopping,
    } = wakeup;
    let mut framer = Framer::new(limits.line);
    let mut chunk = vec![0u8; READ_CHUNK_BYTES];
    let mut pending = Vec::new();
    let mut budget = limits.stdout;
    loop {
        gate.wait_open();
        // Wait for stdout or a wake-up, whichever comes first: a barrier
        // asked for while the actor is stopped would otherwise wait behind
        // a read that nothing will satisfy — and so would the stop.
        let (readable, woken) = readable_or_woken(stdout.as_fd(), wake)?;
        if woken {
            drain_wake(wake)?;
            if stopping.load(Ordering::SeqCst) {
                return Err(io::Error::other(STOPPED_BEFORE_EOF));
            }
        }
        if readable {
            let read = stdout.read(&mut chunk)?;
            if read == 0 {
                break;
            }
            framer.push(&chunk[..read], &mut pending);
            relay(&mut pending, &mut log, &mut budget, events)?;
        }
        // A barrier is reported once nothing is left to read right now;
        // whatever was read above went ahead of it.
        let asked = barrier.load(Ordering::SeqCst);
        if asked != 0 && !readable_now(stdout.as_fd())? {
            barrier.store(0, Ordering::SeqCst);
            events(Event::Barrier(asked));
        }
    }
    framer.finish(&mut pending);
    relay(&mut pending, &mut log, &mut budget, events)?;
    // At the end everything is drained by definition.
    let asked = barrier.swap(0, Ordering::SeqCst);
    if asked != 0 {
        events(Event::Barrier(asked));
    }
    Ok(())
}

/// What the stdout reader is woken for, and told.
#[derive(Clone, Copy)]
struct Wakeup<'a> {
    /// The read end of the wake pipe.
    pipe: &'a OwnedFd,
    /// The id of the barrier asked for, or zero.
    barrier: &'a AtomicU64,
    /// Whether to stop rather than wait for end of file.
    stopping: &'a AtomicBool,
}

/// Wait until `fd` has something to say or a byte arrives on `wake`,
/// whichever comes first; which of the two it was.
fn readable_or_woken(fd: BorrowedFd<'_>, wake: &OwnedFd) -> io::Result<(bool, bool)> {
    let mut wait = [
        PollFd::new(fd, PollFlags::POLLIN),
        PollFd::new(wake.as_fd(), PollFlags::POLLIN),
    ];
    match poll(&mut wait, PollTimeout::NONE) {
        Ok(_) | Err(Errno::EINTR) => {}
        Err(errno) => return Err(io::Error::from(errno)),
    }
    Ok((ready(&wait[0]), ready(&wait[1])))
}

/// Whether a polled descriptor has anything to say: data, a hang-up, or an
/// error — each of which a read must go and see.
fn ready(polled: &PollFd<'_>) -> bool {
    polled.revents().is_some_and(|flags| !flags.is_empty())
}

/// Whether a read on `fd` would return right now.
fn readable_now(fd: BorrowedFd<'_>) -> io::Result<bool> {
    let mut probe = [PollFd::new(fd, PollFlags::POLLIN)];
    match poll(&mut probe, PollTimeout::ZERO) {
        Ok(_) | Err(Errno::EINTR) => Ok(ready(&probe[0])),
        Err(errno) => Err(io::Error::from(errno)),
    }
}

/// Take the wake-up bytes off the pipe, so that it reads as quiet again.
fn drain_wake(wake: &OwnedFd) -> io::Result<()> {
    let mut bytes = [0u8; 64];
    loop {
        match nix::unistd::read(wake, &mut bytes) {
            Ok(0) | Err(Errno::EAGAIN) => return Ok(()),
            Ok(_) | Err(Errno::EINTR) => {}
            Err(errno) => return Err(io::Error::from(errno)),
        }
    }
}

/// Write each frame to the event log as the actor emitted it — its newline
/// only if the actor wrote one — and report it. A record that would cross
/// the cap is not written (an oversized one already begun is rolled back),
/// and the drain stops.
fn relay(
    frames: &mut Vec<Frame>,
    log: &mut EventLog,
    budget: &mut u64,
    events: &dyn Fn(Event),
) -> io::Result<()> {
    for frame in frames.drain(..) {
        match frame {
            Frame::Line { mut bytes, newline } => {
                charge(budget, bytes.len(), newline)?;
                if newline {
                    bytes.push(b'\n');
                }
                log.append(&bytes)?;
                log.close();
                if newline {
                    bytes.pop();
                }
                events(Event::Line(bytes));
            }
            Frame::Oversized {
                part,
                last,
                newline,
            } => {
                if let Err(error) = charge(budget, part.len(), last && newline) {
                    log.abandon();
                    return Err(error);
                }
                log.append(&part)?;
                if last {
                    if newline {
                        log.append(b"\n")?;
                    }
                    log.close();
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
/// drain stops and the actor's next stderr write eventually blocks. Told
/// to stop (`stopping`, with a byte on `wake`), it returns without waiting
/// for end of file.
fn drain_stderr(
    mut stderr: ChildStderr,
    mut log: File,
    cap: u64,
    wake: &OwnedFd,
    stopping: &AtomicBool,
) -> io::Result<()> {
    let mut chunk = vec![0u8; READ_CHUNK_BYTES];
    let mut budget = cap;
    loop {
        let (readable, woken) = readable_or_woken(stderr.as_fd(), wake)?;
        if woken {
            drain_wake(wake)?;
            if stopping.load(Ordering::SeqCst) {
                return Err(io::Error::other(STOPPED_BEFORE_EOF));
            }
        }
        if !readable {
            continue;
        }
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
    use std::path::{Path, PathBuf};
    use std::sync::mpsc::Receiver;

    use nix::sys::stat::Mode;
    use nix::unistd::mkfifo;

    use super::*;

    /// A `sleep` in a process group of its own, stopped, with the identity
    /// it would be held under.
    fn stopped_alone() -> (Child, Held) {
        let child = Command::new("sleep")
            .arg("30")
            .process_group(0)
            .spawn()
            .unwrap();
        let pid = Pid::from_raw(i32::try_from(child.id()).unwrap());
        kill(pid, Signal::SIGSTOP).unwrap();
        assert!(wait_until(Duration::from_secs(5), || state_of(pid) == b'T'));
        let started = match start_time(pid) {
            Probe::Present(started) => started,
            other => panic!("the start time of a live child: {other:?}"),
        };
        (child, Held { pid, started })
    }

    fn state_of(pid: Pid) -> u8 {
        parse_stat(&fs::read(format!("/proc/{}/stat", pid.as_raw())).unwrap())
            .unwrap()
            .state
    }

    fn end(mut child: Child) {
        let _ = kill(
            Pid::from_raw(i32::try_from(child.id()).unwrap()),
            Signal::SIGKILL,
        );
        let _ = child.wait();
    }

    /// A held descendant whose pid now names another process — the start
    /// time is not the one it was held with — is not signalled: the process
    /// at that pid stays stopped, and the group is continued all the same.
    /// The control is the descendant held with its own start time, which
    /// is continued.
    #[test]
    fn a_held_pid_that_was_reused_is_not_continued() {
        for reused in [true, false] {
            let (descendant, held) = stopped_alone();
            let (leader, member) = stopped_alone();
            let held = if reused {
                Held {
                    started: held.started + 1,
                    ..held
                }
            } else {
                held
            };
            resume(&[held], member.pid).unwrap();
            assert!(
                wait_until(Duration::from_secs(2), || state_of(member.pid) == b'S'),
                "the group was not continued (reused: {reused})"
            );
            if reused {
                thread::sleep(Duration::from_millis(100));
                assert_eq!(state_of(held.pid), b'T', "a reused pid was signalled");
            } else {
                assert!(
                    wait_until(Duration::from_secs(2), || state_of(held.pid) == b'S'),
                    "the held descendant was not continued"
                );
            }
            end(descendant);
            end(leader);
        }
    }

    /// A pipe something the sweep cannot find still holds: an unmarked
    /// `setsid` holder keeps one of the actor's pipes — stdout in one arm,
    /// stderr in the other, each drain with a wake-up of its own — open
    /// past the group's death, so that drain cannot reach end of file.
    /// Told to stop once the grace is spent, it comes back and is joined:
    /// `end` returns within the stop bound with that drain stopped short,
    /// the other at end of file, and the held log does not change after
    /// that. Before, the drain was left running, the handle dropped.
    #[test]
    fn a_reader_whose_pipe_is_still_held_is_stopped_and_joined_when_the_grace_is_spent() {
        for (held, script, log) in [
            (
                "stdout",
                "setsid env -i sleep 5 2>/dev/null & echo held; exit 0",
                "events.jsonl",
            ),
            (
                "stderr",
                "setsid env -i sleep 5 >/dev/null & echo held; exit 0",
                "stderr.log",
            ),
        ] {
            let dir = scratch(&format!("end-held-{held}"));
            let (actor, _rx) = launch(&dir, script, 1024);
            thread::sleep(Duration::from_millis(300));
            let grace = Duration::from_millis(500);
            let started = Instant::now();
            let ended = actor.end(grace).unwrap();
            let took = started.elapsed();
            assert!(
                took >= grace && took < grace + READER_STOP_BOUND,
                "{held}: {took:?}"
            );
            assert!(ended.teardown.is_empty(), "{held}: {:?}", ended.teardown);
            let (stopped, other) = if held == "stdout" {
                (&ended.stdout, &ended.stderr)
            } else {
                (&ended.stderr, &ended.stdout)
            };
            assert_eq!(
                stopped,
                &Some(Err(STOPPED_BEFORE_EOF.to_string())),
                "{held}: the held drain"
            );
            assert_eq!(other, &Some(Ok(())), "{held}: the other drain");
            let log = dir.join(log);
            let size = fs::metadata(&log).unwrap().len();
            thread::sleep(Duration::from_millis(200));
            assert_eq!(
                fs::metadata(&log).unwrap().len(),
                size,
                "{held}: the log changed after `end`"
            );
            std::fs::remove_dir_all(dir).unwrap();
        }
    }

    /// The residue: a reader stuck in a write to its log — the event log is
    /// a pipe nobody reads, so the write never returns — is the one wait no
    /// wake-up reaches. `end` — its continuation failing, injected — takes
    /// every step, waits the grace and the stop bound, and returns with the
    /// failure carried and the drain reported unfinished, the leader
    /// reaped. Before, the failure returned at once.
    #[test]
    fn a_reader_stuck_in_a_write_is_left_behind_after_the_grace_and_the_stop_bound() {
        use std::os::unix::fs::OpenOptionsExt;
        let dir = scratch("end-stuck-write");
        let fifo = dir.join("events.fifo");
        mkfifo(&fifo, Mode::S_IRWXU).unwrap();
        // The read end is held open and never read: the writer fills the
        // pipe and then blocks in its write.
        let reader = File::options()
            .read(true)
            .custom_flags(OFlag::O_NONBLOCK.bits())
            .open(&fifo)
            .unwrap();
        let log = File::options().write(true).open(&fifo).unwrap();
        let (tx, _rx) = mpsc::channel();
        let mut actor = Actor::spawn(
            command(&sh("while :; do echo flood; done"), &[]).unwrap(),
            log,
            File::create(dir.join("stderr.log")).unwrap(),
            LIMITS,
            move |event| {
                let _ = tx.send(event);
            },
        )
        .unwrap();
        thread::sleep(Duration::from_millis(300));
        actor.frozen = true;
        actor.held.push(Held {
            pid: actor.group,
            started: 0,
        });
        FAIL_NEXT_CONTINUE.with(|fail| fail.set(true));
        let grace = Duration::from_millis(500);
        let started = Instant::now();
        let ended = actor.end(grace).unwrap();
        let took = started.elapsed();
        assert!(took >= grace + READER_STOP_BOUND, "{took:?}");
        assert!(took < (grace + READER_STOP_BOUND) * 3, "{took:?}");
        assert!(
            ended.teardown.iter().any(|step| step.contains("injected")),
            "{:?}",
            ended.teardown
        );
        assert_eq!(ended.stdout, None, "a drain stuck in a write came back?");
        assert!(ended.stderr.is_some());
        assert!(!ended.status.success());
        drop(reader);
        std::fs::remove_dir_all(dir).unwrap();
    }

    /// One continuation failing stops none of the others: the second held
    /// descendant and the group are continued, and the error counts the
    /// failure.
    #[test]
    fn every_continuation_is_attempted_when_one_fails() {
        let (first, held_first) = stopped_alone();
        let (second, held_second) = stopped_alone();
        let (leader, member) = stopped_alone();
        FAIL_NEXT_CONTINUE.with(|fail| fail.set(true));
        let error = resume(&[held_first, held_second], member.pid).unwrap_err();
        assert!(error.to_string().contains("1 of 2"), "{error}");
        assert!(error.to_string().contains("injected"), "{error}");
        assert!(wait_until(Duration::from_secs(2), || state_of(
            held_second.pid
        ) == b'S'));
        assert!(wait_until(Duration::from_secs(2), || state_of(member.pid) == b'S'));
        thread::sleep(Duration::from_millis(100));
        assert_eq!(
            state_of(held_first.pid),
            b'T',
            "the failed continuation sent a signal"
        );
        end(first);
        end(second);
        end(leader);
    }

    const LIMITS: Limits = Limits {
        line: 1024,
        stdout: u64::MAX,
        stderr: u64::MAX,
    };

    /// No cancellation, ever.
    static NO_STOP: Stop = Stop::new(0);
    const PATIENT: Duration = Duration::from_secs(5);

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
            File::create(dir.join("events.jsonl")).unwrap(),
            File::create(dir.join("stderr.log")).unwrap(),
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
    /// A child of this test is this user's own, or the test cannot go on.
    fn owned(pid: u32) -> Owned {
        match own(pid) {
            Probe::Present(Some(owned)) => owned,
            other => panic!("a child of the test is not this user's own: {other:?}"),
        }
    }

    /// The test process's own start time: the floor the wrapper would use.
    /// A hosted CI runner has live processes of the job's uid, older than
    /// the job, whose environment it may not read; the floor is what keeps
    /// a sweep from tripping over them, in the tests as in the wrapper.
    fn floor() -> u64 {
        match start_time(Pid::this()) {
            Probe::Present(started) => started,
            other => panic!("the test's own start time: {other:?}"),
        }
    }

    /// How many live processes carry `PROBE=<value>` in their environment —
    /// the test's own mark on an actor, set on its command.
    fn probes_alive(value: &str) -> usize {
        marked_processes(&format!("PROBE={value}"), floor())
            .unwrap()
            .len()
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
        assert_eq!(ended.stragglers, Ok(0));
        // Byte for byte: the last line the actor left unterminated stays so.
        assert_eq!(
            std::fs::read_to_string(dir.join("events.jsonl")).unwrap(),
            format!("a\nb\n{big}\nc")
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
        // Stalled: no progress across eight consecutive samples. One quiet
        // sample is not a stall on a loaded box, only a descheduled actor.
        let mut stalled_at = 0;
        let mut quiet = 0;
        for _ in 0..400 {
            thread::sleep(Duration::from_millis(25));
            let now = read_progress();
            quiet = if now > 0 && now == stalled_at {
                quiet + 1
            } else {
                0
            };
            if quiet >= 8 {
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
        // `exec`: dash otherwise vforks, and a SIGSTOP that lands on the
        // child between vfork and exec leaves the parent in an
        // uninterruptible vfork wait (`D` in `kernel_clone`) — stopped in
        // every sense but the letter this test reads.
        let (mut actor, rx) = launch(&dir, "exec sleep 30", 1024);
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
        // A process in an uninterruptible wait (`D`, e.g. paging its binary
        // in on a loaded box) stops only once that wait ends; give it time.
        let deadline = Instant::now() + Duration::from_secs(20);
        while state() != 'T' && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(20));
        }
        let diagnostic = || {
            let pid = actor.pid();
            let read = |name: &str| {
                std::fs::read_to_string(format!("/proc/{pid}/{name}")).unwrap_or_default()
            };
            format!(
                "stat: {}wchan: {}\nstatus: {}",
                read("stat"),
                read("wchan"),
                read("status")
            )
        };
        assert_eq!(
            state(),
            'T',
            "SIGSTOP did not stop the group: {}",
            diagnostic()
        );
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
        actor.write_stdin(b"echoed\n", &NO_STOP, PATIENT).unwrap();
        assert!(actor.stdin_open());
        actor.close_stdin();
        assert!(!actor.stdin_open());
        assert!(actor.write_stdin(b"late\n", &NO_STOP, PATIENT).is_err());
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
            File::create(dir.join("events.jsonl")).unwrap(),
            File::create(dir.join("stderr.log")).unwrap(),
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

    /// An actor with the given limits, its events on an unbounded queue.
    fn launch_with(dir: &Path, script: &str, limits: Limits) -> (Actor, Receiver<Event>) {
        let (tx, rx) = mpsc::channel();
        let actor = Actor::spawn(
            command(&sh(script), &[]).unwrap(),
            File::create(dir.join("events.jsonl")).unwrap(),
            File::create(dir.join("stderr.log")).unwrap(),
            limits,
            move |event| {
                let _ = tx.send(event);
            },
        )
        .unwrap();
        (actor, rx)
    }

    /// Receive until `wanted` accepts an event; that event.
    fn wait_for(rx: &Receiver<Event>, wanted: impl Fn(&Event) -> bool) -> Event {
        loop {
            let event = rx.recv_timeout(Duration::from_secs(10)).unwrap();
            if wanted(&event) {
                return event;
            }
        }
    }

    fn event_log(dir: &Path) -> Vec<u8> {
        std::fs::read(dir.join("events.jsonl")).unwrap()
    }

    #[test]
    fn the_event_log_stops_at_its_cap_exactly_and_the_drain_says_so() {
        let dir = scratch("stdout-cap");
        let (actor, rx) = launch_with(
            &dir,
            "printf 'abcd\\nabcd\\nabcd\\n'; sleep 30",
            Limits {
                stdout: 10,
                ..LIMITS
            },
        );
        // Causal, not timed: the drain's own verdict arrives before teardown.
        let closed = wait_for(&rx, |e| matches!(e, Event::StdoutClosed(_)));
        assert!(matches!(closed, Event::StdoutClosed(Err(e)) if e == CAP_REACHED));
        let ended = actor.end(Duration::from_secs(5)).unwrap();
        assert_eq!(ended.stdout, Some(Err(CAP_REACHED.to_string())));
        assert_eq!(event_log(&dir), b"abcd\nabcd\n");
    }

    #[test]
    fn the_stderr_log_stops_at_its_cap_exactly_and_the_drain_says_so() {
        let dir = scratch("stderr-cap");
        let (actor, rx) = launch_with(
            &dir,
            "printf 'oopsie' >&2; sleep 30",
            Limits {
                stderr: 4,
                ..LIMITS
            },
        );
        let closed = wait_for(&rx, |e| matches!(e, Event::StderrClosed(_)));
        assert!(matches!(closed, Event::StderrClosed(Err(e)) if e == CAP_REACHED));
        let ended = actor.end(Duration::from_secs(5)).unwrap();
        assert_eq!(ended.stderr, Some(Err(CAP_REACHED.to_string())));
        assert_eq!(
            std::fs::read_to_string(dir.join("stderr.log")).unwrap(),
            "oops"
        );
    }

    #[test]
    fn a_record_at_the_exact_cap_is_kept_as_the_actor_wrote_it_terminated_or_not() {
        // Four actors, one per cell: the byte the actor did or did not write
        // is the byte that does or does not fit.
        for (script, cap, expected_log, expected_drain) in [
            ("printf 'abcd'", 4, &b"abcd"[..], Ok(())),
            ("printf 'abcd'", 3, &b""[..], Err(CAP_REACHED.to_string())),
            ("printf 'abcd\\n'", 5, &b"abcd\n"[..], Ok(())),
            (
                "printf 'abcd\\n'",
                4,
                &b""[..],
                Err(CAP_REACHED.to_string()),
            ),
        ] {
            let dir = scratch("exact-cap");
            let (actor, rx) = launch_with(
                &dir,
                script,
                Limits {
                    stdout: cap,
                    ..LIMITS
                },
            );
            let _ = wait_for(&rx, |e| matches!(e, Event::StdoutClosed(_)));
            let ended = actor.end(Duration::from_secs(5)).unwrap();
            assert_eq!(ended.stdout, Some(expected_drain), "{script} at cap {cap}");
            assert_eq!(event_log(&dir), expected_log, "{script} at cap {cap}");
        }
    }

    #[test]
    fn an_oversized_record_that_crosses_the_cap_leaves_nothing_of_itself_behind() {
        // 100,001 bytes with a 1,000-byte line ceiling arrive in several
        // reads and are written piece by piece; the cap falls in the middle.
        // The record before it stays, the record itself is rolled back
        // whole: the log ends at the last complete record, never mid-line.
        let dir = scratch("oversized-rollback");
        let (actor, rx) = launch_with(
            &dir,
            "echo ok; head -c 100001 /dev/zero | tr '\\0' x; sleep 30",
            Limits {
                line: 1000,
                stdout: 70_000,
                ..LIMITS
            },
        );
        let closed = wait_for(&rx, |e| matches!(e, Event::StdoutClosed(_)));
        assert!(matches!(closed, Event::StdoutClosed(Err(e)) if e == CAP_REACHED));
        let ended = actor.end(Duration::from_secs(5)).unwrap();
        assert_eq!(ended.stdout, Some(Err(CAP_REACHED.to_string())));
        assert_eq!(event_log(&dir), b"ok\n");
    }

    #[test]
    fn a_stdin_write_the_actor_never_takes_is_interrupted_by_cancellation() {
        let dir = scratch("stdin-cancel");
        let (mut actor, _rx) = launch(&dir, "exec sleep 30", 1024);
        let stop = Arc::new(Stop::new(0));
        let trigger = Arc::clone(&stop);
        let _canceller = thread::spawn(move || {
            thread::sleep(Duration::from_millis(200));
            trigger.store(15, std::sync::atomic::Ordering::Relaxed);
        });
        let big = vec![b'x'; 1 << 20];
        let started = Instant::now();
        let error = actor
            .write_stdin(&big, &stop, Duration::from_secs(30))
            .unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::Interrupted, "{error}");
        assert!(
            started.elapsed() < Duration::from_secs(5),
            "{:?}",
            started.elapsed()
        );
        let _ = actor.end(Duration::from_millis(200));
    }

    #[test]
    fn a_stdin_write_the_actor_never_takes_gives_up_after_the_stall() {
        let dir = scratch("stdin-stall");
        let (mut actor, _rx) = launch(&dir, "exec sleep 30", 1024);
        let big = vec![b'x'; 1 << 20];
        let started = Instant::now();
        let error = actor
            .write_stdin(&big, &NO_STOP, Duration::from_millis(300))
            .unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::TimedOut, "{error}");
        assert!(started.elapsed() >= Duration::from_millis(300));
        assert!(
            started.elapsed() < Duration::from_secs(5),
            "{:?}",
            started.elapsed()
        );
        let _ = actor.end(Duration::from_millis(200));
    }

    #[test]
    fn continuous_output_is_cut_at_the_cap_and_the_actor_is_still_ended() {
        let dir = scratch("continuous");
        let (tx, rx) = mpsc::channel();
        let actor = Actor::spawn(
            command(&sh("yes"), &[]).unwrap(),
            File::create(dir.join("events.jsonl")).unwrap(),
            File::create(dir.join("stderr.log")).unwrap(),
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
        // Past the cap the read end is dropped, so the actor's next write is
        // an EPIPE / SIGPIPE rather than a stall; either way it did not
        // finish on its own terms.
        assert!(!ended.status.success(), "{ended:?}");
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
            File::create(dir.join("events.jsonl")).unwrap(),
            File::create(dir.join("stderr.log")).unwrap(),
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
            File::create(dir.join("events.jsonl")).unwrap(),
            File::create(dir.join("stderr.log")).unwrap(),
            LIMITS,
            |_| {},
        )
        .unwrap();
        let group = Pid::from_raw(i32::try_from(actor.pid()).unwrap());
        let escaped = || {
            marked_processes(&format!("PROBE={probe}"), floor())
                .unwrap()
                .iter()
                .any(|m| m.group != group)
        };
        assert!(
            wait_until(Duration::from_secs(5), escaped),
            "the escaped descendant never appeared"
        );
        let ended = actor.end(Duration::from_millis(500)).unwrap();
        assert_eq!(ended.stragglers, Ok(1), "{ended:?}");
        assert!(
            wait_until(Duration::from_secs(5), || probes_alive(&probe) == 0),
            "a descendant outside the group survived the end"
        );
    }

    /// `/proc/uptime`, in ticks: the unit `starttime` is in, from another
    /// file.
    fn uptime_ticks() -> u64 {
        let uptime = fs::read_to_string("/proc/uptime").unwrap();
        let (seconds, rest) = uptime.split_once('.').unwrap();
        seconds.parse::<u64>().unwrap() * 100 + rest[..2].parse::<u64>().unwrap()
    }

    /// The field read as a start time is the one that counts ticks since
    /// boot. Two premises, each failed by a neighbouring field: a process
    /// spawned now reads within two seconds of the uptime read around its
    /// spawn (the field before is an obsolete zero, the field after the
    /// address space in bytes); and two spawns of one binary half a second
    /// apart read at least forty ticks apart (every field the two share — a
    /// zero, a size, a count — reads zero apart).
    #[test]
    fn the_start_time_is_the_field_that_counts_ticks_since_boot() {
        let spawn = || {
            let before = uptime_ticks();
            let child = Command::new("sleep").arg("30").spawn().unwrap();
            let after = uptime_ticks();
            let pid = Pid::from_raw(i32::try_from(child.id()).unwrap());
            let started = match start_time(pid) {
                Probe::Present(started) => started,
                other => panic!("the start time of a live child: {other:?}"),
            };
            (child, before, started, after)
        };
        let (mut first, before, first_started, after) = spawn();
        assert!(
            before.saturating_sub(200) <= first_started && first_started <= after + 200,
            "the start time {first_started} is not within two seconds of the uptime {before}..{after}"
        );
        thread::sleep(Duration::from_millis(500));
        let (mut second, _, second_started, _) = spawn();
        assert!(
            second_started >= first_started + 40,
            "two spawns half a second apart read {first_started} and {second_started}"
        );
        for child in [&mut first, &mut second] {
            child.kill().unwrap();
            child.wait().unwrap();
        }
    }

    /// Only the kernel's word that there is no such process is "gone"; a
    /// read this user may not make, or any other failure, is a state
    /// unknown — the collapse of "cannot read" into "clean" has no cell in
    /// this table to hide in.
    #[test]
    fn only_the_kernels_word_that_a_process_is_gone_reads_as_gone() {
        let what = || "reading".to_string();
        let of = |error: io::Error| probe(Err::<(), _>(error), what);
        assert!(matches!(
            of(io::Error::from(io::ErrorKind::NotFound)),
            Probe::Gone
        ));
        assert!(matches!(
            of(io::Error::from_raw_os_error(Errno::ESRCH as i32)),
            Probe::Gone
        ));
        assert!(matches!(
            of(io::Error::from(io::ErrorKind::PermissionDenied)),
            Probe::Unprovable(reason) if reason.starts_with("reading: ")
        ));
        assert!(matches!(
            of(io::Error::other("injected")),
            Probe::Unprovable(_)
        ));
        assert!(matches!(probe(Ok(7), what), Probe::Present(7)));
    }

    /// Other users' processes are not this wrapper's to read, and a box has
    /// them (on CI the wrapper is not root, and PID 1 is): a conclusion
    /// drawn from their owner, not a fault. A hosted runner also has
    /// processes of the job's own uid that it may not read, older than the
    /// job: the floor is what keeps them out. Either way a sweep over such
    /// a box is an answer — the hosted-runner control case, red there with
    /// a floor of zero (CI at 9fcdc86) and green with the floor.
    #[test]
    fn a_box_with_other_users_processes_still_gets_a_sweep_answer() {
        assert!(matches!(own(1), Probe::Present(_)), "PID 1 is always there");
        marked_processes("PROBE=nobody-has-this", floor()).unwrap();
    }

    /// A child that exited and is not yet reaped is a zombie: exited by the
    /// kernel's own account — the state letter, and an address space of
    /// zero — with nothing to kill. On this kernel an exited process's
    /// `environ` is refused rather than empty, which must not read as a
    /// live process this user cannot see. A live child, for the contrast,
    /// has an address space.
    #[test]
    fn an_exited_child_of_this_users_own_is_exited_not_unreadable() {
        let probe = format!("zombie-{}", std::process::id());
        let mut live = Command::new("sleep").arg("30").spawn().unwrap();
        let mut child = Command::new("sh")
            .args(["-c", "exit 0"])
            .env("PROBE", &probe)
            .spawn()
            .unwrap();
        let pid = owned(child.id());
        assert!(
            wait_until(Duration::from_secs(5), || matches!(
                stat_fields(pid),
                Probe::Present(stat) if stat.state == b'Z'
            )),
            "the child never became a zombie"
        );
        let Probe::Present(zombie) = stat_fields(pid) else {
            panic!("the zombie's stat")
        };
        assert_eq!(zombie.vsize, 0, "{zombie:?}");
        let Probe::Present(running) = stat_fields(owned(live.id())) else {
            panic!("the live child's stat")
        };
        assert!(running.vsize > 0 && !running.exited(), "{running:?}");
        assert_eq!(probes_alive(&probe), 0, "a zombie counted as alive");
        child.wait().unwrap();
        live.kill().unwrap();
        live.wait().unwrap();
    }

    /// The floor excludes a process that started strictly before it and
    /// keeps one on the same tick: a real child, its own start tick as the
    /// floor (kept — the same-tick case, by construction) and one tick
    /// later (excluded), the mark present in both arms.
    #[test]
    fn the_start_time_floor_excludes_only_what_started_strictly_before_it() {
        let probe = format!("floor-{}", std::process::id());
        let needle = format!("PROBE={probe}");
        let mut child = Command::new("sleep")
            .arg("30")
            .env("PROBE", &probe)
            .spawn()
            .unwrap();
        let Probe::Present(stat) = stat_fields(owned(child.id())) else {
            panic!("the child's stat")
        };
        let found = |floor: u64| {
            marked_processes(&needle, floor)
                .unwrap()
                .iter()
                .any(|m| m.pid.as_raw() == i32::try_from(child.id()).unwrap())
        };
        assert!(found(floor()), "the test's own floor: the child is found");
        assert!(found(stat.started), "the same tick is kept");
        assert!(!found(stat.started + 1), "one tick later: excluded");
        child.kill().unwrap();
        child.wait().unwrap();
    }

    #[test]
    fn a_descendant_whose_comm_is_not_utf8_is_still_found_and_killed() {
        let dir = scratch("comm");
        let probe = format!("comm-{}", std::process::id());
        let pid_file = dir.join("escaped.pid");
        let fifo = dir.join("never-written");
        mkfifo(&fifo, Mode::S_IRWXU).unwrap();
        // A descendant that leaves the group and names itself in bytes that
        // are not UTF-8, so that its `/proc/<pid>/stat` is not a string;
        // then it blocks opening a fifo nobody writes — alive for good,
        // forking nothing, so only the sweep can end it. It writes its pid
        // after naming itself: the file appearing is the fixture in place.
        let script = format!(
            "setsid sh -c 'printf \"\\377\\376\" > /proc/self/comm; echo $$ > \"$0\"; \
             read _ < \"$1\"' {} {} >/dev/null 2>&1 </dev/null & sleep 30",
            pid_file.display(),
            fifo.display()
        );
        let mut command = command(&sh(&script), &[]).unwrap();
        command.env("PROBE", &probe);
        let actor = Actor::spawn(
            command,
            File::create(dir.join("events.jsonl")).unwrap(),
            File::create(dir.join("stderr.log")).unwrap(),
            LIMITS,
            |_| {},
        )
        .unwrap();
        let written = || {
            fs::read_to_string(&pid_file)
                .ok()
                .and_then(|s| s.trim().parse::<i32>().ok())
        };
        assert!(
            wait_until(Duration::from_secs(5), || written().is_some()),
            "the escaped descendant never wrote its pid"
        );
        let escaped = written().unwrap();
        let comm = fs::read(format!("/proc/{escaped}/comm")).unwrap();
        assert!(
            std::str::from_utf8(&comm).is_err(),
            "the fixture's comm is UTF-8: {comm:?}"
        );
        // Alive by an oracle of its own: a live process has an environment,
        // a zombie or a gone one has none.
        let alive = || fs::read(format!("/proc/{escaped}/environ")).is_ok_and(|e| !e.is_empty());
        assert!(alive(), "the fixture died on its own");
        let ended = actor.end(Duration::from_millis(500)).unwrap();
        let gone = wait_until(Duration::from_secs(5), || !alive());
        if !gone {
            let _ = kill(Pid::from_raw(escaped), Signal::SIGKILL);
        }
        assert!(
            gone,
            "the descendant with a non-UTF-8 comm survived the end: {ended:?}"
        );
        assert!(matches!(ended.stragglers, Ok(n) if n >= 1), "{ended:?}");
    }

    #[test]
    fn one_file_for_both_logs_is_refused_before_the_actor_starts() {
        let dir = scratch("alias");
        let probe = format!("alias-{}", std::process::id());
        let mut command = command(&sh("sleep 30"), &[]).unwrap();
        command.env("PROBE", &probe);
        let log = dir.join("one.log");
        let error = Actor::spawn(
            command,
            File::create(&log).unwrap(),
            File::create(&log).unwrap(),
            LIMITS,
            |_| {},
        )
        .expect_err("aliased logs");
        assert_eq!(error.kind(), io::ErrorKind::InvalidInput, "{error}");
        assert_eq!(probes_alive(&probe), 0, "an actor was started");
    }
}
