//! `swe-lab-supervisor`: the in-sandbox supervision runtime.
//!
//! The design (swe-lab issue #375): wrap a coding agent (the *actor*) as a
//! child process, own its stdin, stdout, stderr and process group, drain its
//! `stream-json` output while a judge model is consulted at configured
//! boundaries, and write short corrections on the actor's stdin.
//!
//! **What this slice does.** `criteria`, `--version` and `--help` are
//! complete, and so is the process wrapper: `run` validates the config,
//! the criterion digest and the endpoint, launches the actor in its own
//! process group with the two endpoint variables scrubbed from its
//! environment and a mark added to it, writes the task on its stdin as one
//! `stream-json` user event, drains its stdout to the event log and its
//! stderr to the stderr log — each capped, each drain's fate reported — and
//! ends it deliberately (`SIGTERM`, the configured grace, `SIGKILL` to the
//! group, then every marked descendant that left the group) — on its own
//! exit, on `SIGTERM` / `SIGINT` to the wrapper, when a log stops taking
//! output, or when the leader has exited and a descendant still holds its
//! stdout — exiting as the actor did when every drain ended cleanly, and
//! with `1` when one did not. **No judgment is made and no correction is
//! written**, and there is no supervisor log or summary yet: the judgment
//! loop is the next slice, which rewrites this paragraph.

mod actor;
mod cli;
mod config;
mod criterion;
mod framing;
mod signals;
mod stream;

use std::os::unix::process::ExitStatusExt;
use std::process::{ExitCode, ExitStatus};
use std::sync::atomic::Ordering;
use std::sync::mpsc;
use std::time::{Duration, Instant};

use actor::{Actor, Event};

/// The command line could not be used as given.
const EXIT_USAGE: u8 = 2;
/// The actor ran, and the run is not accounted for: a drain stopped with an
/// error, or did not finish. The exit status is the wrapper's, not the
/// actor's, because the actor's success cannot be told from its record.
const EXIT_UNHEALTHY: u8 = 1;
/// The run was refused before the actor was launched: an unusable config, or a
/// criterion that is not the one the config pins.
const EXIT_REFUSED: u8 = 3;

#[allow(clippy::print_stdout, clippy::print_stderr)]
fn main() -> ExitCode {
    let command = match cli::parse(std::env::args_os().skip(1)) {
        Ok(command) => command,
        Err(message) => {
            eprintln!("swe-lab-supervisor: {message}\n\n{}", cli::USAGE);
            return ExitCode::from(EXIT_USAGE);
        }
    };
    match command {
        cli::Command::Version => {
            println!("swe-lab-supervisor {}", env!("CARGO_PKG_VERSION"));
            ExitCode::SUCCESS
        }
        cli::Command::Help => {
            println!("{}", cli::USAGE);
            ExitCode::SUCCESS
        }
        cli::Command::Criteria => {
            for embedded in criterion::EMBEDDED {
                println!("{}  {}", embedded.name, embedded.sha256());
            }
            ExitCode::SUCCESS
        }
        cli::Command::Run(args) => match run(&args) {
            Ok(code) => code,
            Err(Failed::Refused(reason)) => {
                eprintln!("swe-lab-supervisor: refusing to start: {reason}");
                ExitCode::from(EXIT_REFUSED)
            }
            Err(Failed::Unhealthy(reason)) => {
                eprintln!("swe-lab-supervisor: the run is not accounted for: {reason}");
                ExitCode::from(EXIT_UNHEALTHY)
            }
        },
    }
}

/// How a `run` did not end with the actor's exit status.
enum Failed {
    /// Refused before any actor process existed.
    Refused(String),
    /// The actor ran, and its record is not whole.
    Unhealthy(String),
}

impl From<String> for Failed {
    fn from(reason: String) -> Self {
        Self::Refused(reason)
    }
}

/// Validate the run's inputs, then run the actor under the wrapper.
///
/// Everything that can refuse the run happens before any actor process
/// exists: a run that cannot be supervised as configured is not started.
///
/// Until the policy lands, the wrapper is transparent: the task prompt is
/// the first message on the actor's stdin, stdin is then closed so the actor
/// runs one turn, and every line of its output is drained to the event log.
/// The run ends when stdout reaches end of file, when a drain stops with an
/// error, when the wrapper is told to stop, or when the leader has exited
/// and — a descendant still holding its stdout — the grace has passed.
fn run(args: &cli::RunArgs) -> Result<ExitCode, Failed> {
    let config = config::load(&args.config)?;
    let _criterion = criterion::select(&config.criterion.name, &config.criterion.sha256)?;
    let _endpoint = config::Endpoint::from_env()?;
    let _credential = config::api_key_from_env();
    let stop = signals::termination_requested().map_err(|e| format!("signal handler: {e}"))?;
    let (events, inbox) = actor::event_queue();
    let command = actor::command(
        &args.actor_argv,
        &[config::BASE_URL_ENV, config::API_KEY_ENV],
    )
    .map_err(|e| format!("actor command: {e}"))?;
    let limits = actor::Limits {
        line: usize::try_from(config.limits.max_event_line_bytes.get())
            .map_err(|_| "limits.max_event_line_bytes does not fit".to_string())?,
        stdout: config.limits.max_actor_stdout_bytes.get(),
        stderr: config.limits.max_actor_stderr_bytes.get(),
    };
    let mut actor = Actor::spawn(
        command,
        &args.actor_event_log,
        &args.actor_stderr,
        limits,
        move |event| {
            // The consumer being gone means the wrapper is on its way out.
            let _ = events.send(event);
        },
    )
    .map_err(|e| format!("launching the actor: {e}"))?;
    let grace = Duration::from_millis(config.timeouts.term_grace_ms);

    if let Err(error) = actor.write_stdin(stream::user_event_line(&config.task).as_bytes()) {
        let ended = actor
            .end(grace)
            .map_err(|e| format!("ending the actor: {e}"))?;
        return Err(Failed::Refused(format!(
            "the actor did not take its prompt ({error}); it ended with {}",
            ended.status
        )));
    }
    actor.close_stdin();

    let mut fault: Option<String> = None;
    let mut leader_exited_at: Option<Instant> = None;
    loop {
        if stop.load(Ordering::Relaxed) {
            break;
        }
        match inbox.recv_timeout(Duration::from_millis(100)) {
            Ok(Event::StdoutClosed(Ok(()))) | Err(mpsc::RecvTimeoutError::Disconnected) => break,
            Ok(Event::StdoutClosed(Err(error))) => {
                fault = Some(format!("actor stdout: {error}"));
                break;
            }
            Ok(Event::StderrClosed(Err(error))) => {
                fault = Some(format!("actor stderr: {error}"));
                break;
            }
            Ok(Event::Line(_) | Event::Oversized | Event::StderrClosed(Ok(())))
            | Err(mpsc::RecvTimeoutError::Timeout) => {}
        }
        if leader_exited_at.is_none()
            && actor
                .exited()
                .map_err(|e| Failed::Unhealthy(format!("observing the actor: {e}")))?
        {
            leader_exited_at = Some(Instant::now());
        }
        if leader_exited_at.is_some_and(|at| at.elapsed() >= grace) {
            break;
        }
    }
    // A reader blocked on the full queue can only finish once nobody holds
    // the receiver.
    drop(inbox);
    let ended = actor
        .end(grace)
        .map_err(|e| Failed::Unhealthy(format!("ending the actor: {e}")))?;
    if fault.is_none() {
        fault = drain_fault("stdout", ended.stdout).or(drain_fault("stderr", ended.stderr));
    }
    match fault {
        Some(reason) => Err(Failed::Unhealthy(reason)),
        None => Ok(exit_code_of(ended.status)),
    }
}

/// What a drain's ending says against the run, if anything.
fn drain_fault(stream: &str, drain: Option<Result<(), String>>) -> Option<String> {
    match drain {
        Some(Ok(())) => None,
        Some(Err(error)) => Some(format!("actor {stream}: {error}")),
        None => Some(format!(
            "actor {stream}: the drain did not finish within the grace; a process the wrapper could not find still holds the pipe"
        )),
    }
}

/// The wrapper exits as the actor did, so a script recording `$?` sees what
/// it would have seen without the wrapper: the code, or `128 + signal`.
fn exit_code_of(status: ExitStatus) -> ExitCode {
    let code = match (status.code(), status.signal()) {
        (Some(code), _) => code,
        (None, Some(signal)) => 128 + signal,
        (None, None) => 1,
    };
    ExitCode::from(u8::try_from(code & 0xff).unwrap_or(1))
}
