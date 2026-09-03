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
//! the event log the moment it is complete, and hands it to the supervisor
//! loop through a channel. Stderr is copied to its log on a second thread.

use std::ffi::OsString;
use std::fs::File;
use std::io::{self, BufWriter, Read, Write};
use std::os::unix::process::CommandExt;
use std::path::Path;
use std::process::{Child, ChildStdin, ChildStdout, Command, ExitStatus, Stdio};
use std::sync::mpsc::Sender;
use std::sync::{Arc, Condvar, Mutex, PoisonError};
use std::thread;
use std::time::{Duration, Instant};

use nix::errno::Errno;
use nix::sys::signal::{Signal, killpg};
use nix::sys::wait::{Id, WaitPidFlag, WaitStatus, waitid};
use nix::unistd::Pid;

use crate::framing::{Frame, Framer};

/// How much is taken off the stdout pipe per read.
const READ_CHUNK_BYTES: usize = 64 * 1024;

/// How often the leader is checked for exit while the grace period runs.
const EXIT_POLL_INTERVAL: Duration = Duration::from_millis(20);

/// What the stdout reader reports to the supervisor loop.
#[derive(Debug)]
#[cfg_attr(
    not(test),
    expect(
        dead_code,
        reason = "the payloads are read by the supervision loop, the next slice"
    )
)]
pub enum Event {
    /// One complete stdout line within the ceiling, newline excluded, already
    /// appended to the event log.
    Line(Vec<u8>),
    /// One stdout line over the ceiling ended. It was appended to the event
    /// log verbatim; nothing here can decode it.
    Oversized,
    /// The actor's stdout reached end of file — every holder of the pipe's
    /// write end is gone — or reading it failed, with the error.
    StdoutClosed(Result<(), String>),
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

    #[cfg_attr(
        not(test),
        expect(dead_code, reason = "used by the supervision loop, the next slice")
    )]
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

/// A running actor and everything the wrapper holds of it.
///
/// Dropping one without [`Actor::end`] kills its process group: an actor the
/// wrapper stops holding is not an actor that may run on unsupervised.
#[derive(Debug)]
pub struct Actor {
    child: Child,
    group: Pid,
    stdin: Option<ChildStdin>,
    gate: Arc<Gate>,
    /// `SIGSTOP` was sent to the group and no `SIGCONT` has followed. A real
    /// state the wrapper guarantees it leaves before it exits.
    frozen: bool,
    reaped: bool,
}

impl Actor {
    /// Launch the actor from a prepared command (see [`command`]), in its
    /// own process group, all three standard streams held here. Every line
    /// of stdout goes to `event_log`, stderr to `stderr_log`; both files are
    /// created before the actor starts.
    ///
    /// # Errors
    ///
    /// A log file cannot be created, or the actor cannot be spawned.
    pub fn spawn(
        mut command: Command,
        event_log: &Path,
        stderr_log: &Path,
        max_line_bytes: usize,
        events: Sender<Event>,
    ) -> io::Result<Self> {
        let event_log = File::create(event_log)?;
        let mut stderr_log = File::create(stderr_log)?;
        command
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .process_group(0);
        let mut child = command.spawn()?;
        let group = Pid::from_raw(
            i32::try_from(child.id()).map_err(|_| io::Error::other("actor pid does not fit"))?,
        );
        let stdin = child.stdin.take();
        let (Some(stdout), Some(mut stderr)) = (child.stdout.take(), child.stderr.take()) else {
            return Err(io::Error::other(
                "actor spawned without piped stdout and stderr",
            ));
        };
        let gate = Arc::new(Gate::new());
        let reader_gate = Arc::clone(&gate);
        let _reader = thread::spawn(move || {
            let result = drain_stdout(
                stdout,
                &reader_gate,
                BufWriter::new(event_log),
                max_line_bytes,
                &events,
            );
            // The loop being gone means the wrapper is already on its way out.
            let _ = events.send(Event::StdoutClosed(result.map_err(|e| e.to_string())));
        });
        let _stderr_reader = thread::spawn(move || {
            // A stderr log that cannot be written changes nothing about the
            // run; the actor's own words on stderr are a courtesy record.
            let _ = io::copy(&mut stderr, &mut stderr_log);
        });
        Ok(Self {
            child,
            group,
            stdin,
            gate,
            frozen: false,
            reaped: false,
        })
    }

    #[cfg_attr(
        not(test),
        expect(dead_code, reason = "used by the supervision loop, the next slice")
    )]
    /// The reader's gate, to close while a judgment is in flight.
    #[must_use]
    pub fn gate(&self) -> Arc<Gate> {
        Arc::clone(&self.gate)
    }

    #[cfg_attr(
        not(test),
        expect(dead_code, reason = "used by the supervision loop, the next slice")
    )]
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

    #[cfg_attr(
        not(test),
        expect(dead_code, reason = "used by the supervision loop, the next slice")
    )]
    /// Whether stdin is still open for corrections.
    #[must_use]
    pub fn stdin_open(&self) -> bool {
        self.stdin.is_some()
    }

    #[cfg_attr(
        not(test),
        expect(dead_code, reason = "used by the supervision loop, the next slice")
    )]
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

    #[cfg_attr(
        not(test),
        expect(dead_code, reason = "used by the supervision loop, the next slice")
    )]
    /// Whether the group is currently stopped by [`Actor::freeze`].
    #[must_use]
    pub fn frozen(&self) -> bool {
        self.frozen
    }

    /// Whether the leader has exited — observed **without reaping it**, so
    /// its pid keeps naming the group until the last signal has been sent.
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

    /// End the actor's whole process group and reap the leader.
    ///
    /// Any freeze is lifted and the gate opened first, so an actor that is
    /// stopped or blocked can act on the signal. Then `SIGTERM` to the group,
    /// up to `grace` for the leader to exit, `SIGKILL` to whatever is left —
    /// grandchildren included — and the reap last, because an unreaped leader
    /// is what keeps its pid naming this group and no other.
    ///
    /// # Errors
    ///
    /// A signal or the final wait failed for a reason other than the group
    /// being already gone.
    pub fn end(mut self, grace: Duration) -> io::Result<ExitStatus> {
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
        Ok(status)
    }
}

impl Drop for Actor {
    fn drop(&mut self) {
        if self.reaped {
            return;
        }
        // Best effort on a path that is already abnormal: never leave the
        // group frozen, never leave it running, and reap the leader so its
        // pid stops naming the group — a killed leader dies at once, so the
        // wait does not block.
        let _ = self.thaw();
        let _ = signal_group(self.group, Signal::SIGKILL);
        let _ = self.child.wait();
    }
}

/// Signal the group, treating a group that no longer exists as done.
///
/// A group id at or below one is refused before any call: `killpg(0, …)`
/// signals the caller's own group — this wrapper and whatever shares its
/// group in the sandbox — and `1` is init's. Neither can be the actor's, so
/// reaching here with one is a wrapper bug, and the safe answer to a bug on
/// a signalling path is to send nothing.
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

fn drain_stdout(
    mut stdout: ChildStdout,
    gate: &Gate,
    mut log: BufWriter<File>,
    ceiling: usize,
    events: &Sender<Event>,
) -> io::Result<()> {
    let mut framer = Framer::new(ceiling);
    let mut chunk = vec![0u8; READ_CHUNK_BYTES];
    let mut pending = Vec::new();
    loop {
        gate.wait_open();
        let read = stdout.read(&mut chunk)?;
        if read == 0 {
            break;
        }
        framer.push(&chunk[..read], &mut pending);
        relay(&mut pending, &mut log, events)?;
    }
    framer.finish(&mut pending);
    relay(&mut pending, &mut log, events)?;
    log.flush()
}

/// Append each frame to the event log — flushed per line, so the artifact is
/// complete up to the last whole line at any moment — and report it.
fn relay(
    frames: &mut Vec<Frame>,
    log: &mut BufWriter<File>,
    events: &Sender<Event>,
) -> io::Result<()> {
    for frame in frames.drain(..) {
        match frame {
            Frame::Line(line) => {
                log.write_all(&line)?;
                log.write_all(b"\n")?;
                log.flush()?;
                let _ = events.send(Event::Line(line));
            }
            Frame::Oversized { part, last } => {
                log.write_all(&part)?;
                if last {
                    log.write_all(b"\n")?;
                    log.flush()?;
                    let _ = events.send(Event::Oversized);
                }
            }
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use std::os::unix::process::ExitStatusExt;
    use std::path::PathBuf;
    use std::sync::mpsc::{self, Receiver};

    use super::*;

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

    fn launch(dir: &Path, script: &str, ceiling: usize) -> (Actor, Receiver<Event>) {
        let (tx, rx) = mpsc::channel();
        let actor = Actor::spawn(
            command(&sh(script), &[]).unwrap(),
            &dir.join("events.jsonl"),
            &dir.join("stderr.log"),
            ceiling,
            tx,
        )
        .unwrap();
        (actor, rx)
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
        let status = actor.end(Duration::from_secs(5)).unwrap();
        assert_eq!(status.code(), Some(0));
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
        assert_eq!(actor.end(Duration::from_secs(5)).unwrap().code(), Some(0));
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
        let status = actor.end(Duration::from_secs(5)).unwrap();
        assert_eq!(status.signal(), Some(15));
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
        let status = actor.end(Duration::from_millis(500)).unwrap();
        let took = started.elapsed();
        assert_eq!(status.signal(), Some(9));
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
        let status = actor.end(Duration::from_secs(5)).unwrap();
        assert_eq!(status.signal(), Some(15));
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
        assert_eq!(actor.end(Duration::from_secs(5)).unwrap().code(), Some(7));
    }
}
