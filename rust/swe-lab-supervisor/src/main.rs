//! `swe-lab-supervisor`: the in-sandbox supervision runtime.
//!
//! The design (swe-lab issue #375): wrap a coding agent (the *actor*) as a
//! child process, own its stdin, stdout, stderr and process group, drain its
//! `stream-json` output while a judge model is consulted at configured
//! boundaries, and write short corrections on the actor's stdin.
//!
//! **What this slice does.** `criteria`, `--version` and `--help` are
//! complete. `run` loads and validates the config, verifies the criterion
//! digest and reads the endpoint from the environment, then **refuses with
//! exit 3**: no actor is launched and no artifact is written. The process
//! wrapper and the judgment loop are the following slices; each moves this
//! paragraph.

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
use std::time::Duration;

use actor::{Actor, Event};

/// The command line could not be used as given.
const EXIT_USAGE: u8 = 2;
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
            Err(refusal) => {
                eprintln!("swe-lab-supervisor: refusing to start: {refusal}");
                ExitCode::from(EXIT_REFUSED)
            }
        },
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
fn run(args: &cli::RunArgs) -> Result<ExitCode, String> {
    let config = config::load(&args.config)?;
    let _criterion = criterion::select(&config.criterion.name, &config.criterion.sha256)?;
    let _endpoint = config::Endpoint::from_env()?;
    let _credential = config::api_key_from_env();
    let stop = signals::termination_requested().map_err(|e| format!("signal handler: {e}"))?;
    let (events, inbox) = mpsc::channel();
    let command = actor::command(
        &args.actor_argv,
        &[config::BASE_URL_ENV, config::API_KEY_ENV],
    )
    .map_err(|e| format!("actor command: {e}"))?;
    let max_line_bytes = usize::try_from(config.limits.max_event_line_bytes.get())
        .map_err(|_| "limits.max_event_line_bytes does not fit".to_string())?;
    let mut actor = Actor::spawn(
        command,
        &args.actor_event_log,
        &args.actor_stderr,
        max_line_bytes,
        events,
    )
    .map_err(|e| format!("launching the actor: {e}"))?;
    let grace = Duration::from_millis(config.timeouts.term_grace_ms);

    if let Err(error) = actor.write_stdin(stream::user_event_line(&config.task).as_bytes()) {
        let status = actor
            .end(grace)
            .map_err(|e| format!("ending the actor: {e}"))?;
        return Err(format!(
            "the actor did not take its prompt ({error}); it ended with {status}"
        ));
    }
    actor.close_stdin();

    loop {
        if stop.load(Ordering::Relaxed) {
            break;
        }
        match inbox.recv_timeout(Duration::from_millis(100)) {
            Ok(Event::StdoutClosed(_)) | Err(mpsc::RecvTimeoutError::Disconnected) => break,
            Ok(Event::Line(_) | Event::Oversized) | Err(mpsc::RecvTimeoutError::Timeout) => {}
        }
    }
    let status = actor
        .end(grace)
        .map_err(|e| format!("ending the actor: {e}"))?;
    Ok(exit_code_of(status))
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
