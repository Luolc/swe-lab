//! `swe-lab-supervisor`: the in-sandbox supervision runtime.
//!
//! The design (swe-lab issue #375): wrap a coding agent (the *actor*) as a
//! child process, own its stdin, stdout, stderr and process group, drain its
//! `stream-json` output while a judge model is consulted at configured
//! boundaries, and write short corrections on the actor's stdin.
//!
//! Every part of that is implemented: the process wrapper (`actor`), the
//! framing of its output (`framing`), the evidence filter (`evidence`), the
//! prompts (`prompt`), the model calls over plain HTTP (`http`, `model`),
//! the policy (`policy`), the loop that ties them together and keeps the
//! account (`supervisor`), and the terminal summary (`summary`). The crate
//! README is the operational contract; the design record is task 20 in
//! `docs/trace-synthesis/plans/`.

mod actor;
mod cli;
mod config;
mod criterion;
mod evidence;
mod framing;
mod http;
mod model;
mod policy;
mod prompt;
mod signals;
mod stream;
mod summary;
mod supervisor;

use std::os::unix::process::ExitStatusExt;
use std::process::{ExitCode, ExitStatus};
use std::time::Duration;

use summary::Summary;

/// The command line could not be used as given.
const EXIT_USAGE: u8 = 2;
/// The actor was supervised to its end, but the summary — the artifact a run
/// is classified from — could not be written; the exit status is the
/// wrapper's, not the actor's.
const EXIT_UNWRITTEN: u8 = 1;
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
            Err(Failed::SummaryUnwritten(reason)) => {
                eprintln!("swe-lab-supervisor: {reason}");
                ExitCode::from(EXIT_UNWRITTEN)
            }
        },
    }
}

/// How a `run` did not end with the actor's exit status.
enum Failed {
    /// Refused before any actor process existed.
    Refused(String),
    /// The actor ran, and the summary could not be written.
    SummaryUnwritten(String),
}

/// Validate the run's inputs, run the actor under supervision, write the
/// summary, and exit as the actor did.
///
/// Everything that can refuse the run happens before any actor process
/// exists: a run that cannot be supervised as configured is not started, and
/// the summary says so — so that a reader classifies a refusal from the
/// artifact rather than from a missing file. Nothing the caller passed is
/// repeated in a diagnostic: a misplaced token in the actor argv, the config
/// path or the environment would otherwise land in a log.
fn run(args: &cli::RunArgs) -> Result<ExitCode, Failed> {
    let refused = |reason: String, model: &str, digest: &str| -> Failed {
        // Best effort: the refusal is already the answer, and a summary that
        // cannot be written leaves the reader with a missing file, which the
        // consumer treats the same way.
        let _ = Summary::refused(&reason, model, digest).write(&args.summary);
        Failed::Refused(reason)
    };
    let config = config::load(&args.config).map_err(|e| refused(e, "", ""))?;
    let selected = criterion::select(&config.criterion.name, &config.criterion.sha256)
        .map_err(|e| refused(e, &config.model.name, &config.criterion.sha256))?;
    let endpoint = config::Endpoint::from_env()
        .map_err(|e| refused(e, &config.model.name, &selected.digest))?;
    let model = model::Model {
        name: config.model.name.clone(),
        endpoint,
        bearer: config::api_key_from_env(),
        call_timeout: Duration::from_millis(config.timeouts.model_call_ms),
    };
    let stop = signals::termination_requested().map_err(|e| {
        refused(
            format!("signal handler: {e}"),
            &config.model.name,
            &selected.digest,
        )
    })?;
    let paths = supervisor::Paths {
        actor_event_log: args.actor_event_log.clone(),
        supervisor_log: args.supervisor_log.clone(),
        actor_stderr: args.actor_stderr.clone(),
    };
    let digest = selected.digest.clone();
    let model_name = config.model.name.clone();
    let ended = supervisor::run(
        config,
        selected.text,
        &digest,
        model,
        &args.actor_argv,
        &paths,
        &stop,
    )
    .map_err(|e| refused(e, &model_name, &digest))?;
    ended
        .summary
        .write(&args.summary)
        .map_err(|e| Failed::SummaryUnwritten(format!("writing the summary: {e}")))?;
    Ok(ended.status.map_or(ExitCode::FAILURE, exit_code_of))
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
