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

// The wrapper's outputs go through `outputs::Outputs`, the one door
// `clippy.toml` points every `File::create` to; a test writing its fixtures
// is not an output of the wrapper.
#![cfg_attr(test, allow(clippy::disallowed_methods))]

mod actor;
mod cli;
mod config;
mod criterion;
mod evidence;
mod framing;
mod http;
mod model;
mod outputs;
mod policy;
mod prompt;
mod signals;
mod stream;
mod summary;
mod supervisor;

use std::io;
use std::os::unix::process::ExitStatusExt;
use std::process::{ExitCode, ExitStatus};
use std::time::Duration;

use outputs::Outputs;
use summary::Summary;

/// The command line could not be used as given.
const EXIT_USAGE: u8 = 2;
/// The actor ran, and the run is not accounted for: the wrapper's ending was
/// unclean, or the summary — the artifact a run is classified from — could
/// not be written. The exit status is the wrapper's, not the actor's, because
/// the actor's success cannot be told from its record.
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
            Err(Failed::Cancelled { signal, actor }) => {
                eprintln!(
                    "swe-lab-supervisor: cancelled by signal {signal}; the actor ended with {actor}"
                );
                ExitCode::from(exit_code_for_signal(signal))
            }
        },
    }
}

/// How a `run` did not end with the actor's exit status.
enum Failed {
    /// Refused before any actor process existed.
    Refused(String),
    /// The actor ran, and its record is not whole: an unclean ending, or a
    /// summary that could not be written.
    Unhealthy(String),
    /// The wrapper was asked to stop by a signal, and did, whatever the actor
    /// then made of its own ending: a cancelled run is not a result. The
    /// summary was written and says `terminated`; `actor` is how the actor
    /// ended, for the diagnostic.
    Cancelled { signal: i32, actor: String },
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
    let mut outputs = Outputs::default();
    // Every output's name is held before anything can be written at any
    // of them — a refusal's summary included: two names on one file are a
    // refusal that writes nothing, not a summary written over a log.
    preflight(&mut outputs, args).map_err(|e| Failed::Refused(format!("outputs: {e}")))?;
    let config = config::load(&args.config).map_err(|e| refused(&mut outputs, args, e, "", ""))?;
    let selected =
        criterion::select(&config.criterion.name, &config.criterion.sha256).map_err(|e| {
            refused(
                &mut outputs,
                args,
                e,
                &config.model.name,
                &config.criterion.sha256,
            )
        })?;
    let endpoint = config::Endpoint::from_env()
        .map_err(|e| refused(&mut outputs, args, e, &config.model.name, &selected.digest))?;
    // Unread here: whatever the file holds goes to the actor as it is.
    let actor_prompt = std::fs::read(&args.actor_prompt).map_err(|e| {
        refused(
            &mut outputs,
            args,
            format!("reading the actor prompt: {e}"),
            &config.model.name,
            &selected.digest,
        )
    })?;
    let stop = signals::termination_requested().map_err(|e| {
        refused(
            &mut outputs,
            args,
            format!("signal handler: {e}"),
            &config.model.name,
            &selected.digest,
        )
    })?;
    let api_key_env = config::api_key_env_name()
        .map_err(|e| refused(&mut outputs, args, e, &config.model.name, &selected.digest))?;
    let api_key = config::api_key_from_env(&api_key_env)
        .map_err(|e| refused(&mut outputs, args, e, &config.model.name, &selected.digest))?;
    let model = model::Model {
        name: config.model.name.clone(),
        endpoint,
        api_key: Some(api_key),
        api_key_env,
        call_timeout: Duration::from_millis(config.timeouts.model_call_ms),
        stop: std::sync::Arc::clone(&stop),
    };
    let digest = selected.digest.clone();
    let model_name = config.model.name.clone();
    let artifacts = open_outputs(&mut outputs, args).map_err(|e| {
        refused(
            &mut outputs,
            args,
            format!("opening the outputs: {e}"),
            &model_name,
            &digest,
        )
    })?;
    let launch = supervisor::Launch {
        argv: &args.actor_argv,
        prompt: &actor_prompt,
    };
    let ended = match supervisor::run(config, selected.text, &digest, model, launch, artifacts) {
        Ok(ended) => ended,
        Err(reason) => {
            // A stop that arrived while the prompt was still being written
            // ended the run before it started; that is a cancellation, not
            // a refusal.
            if let Some(signal) = signals::requested(&stop) {
                return Err(Failed::Cancelled {
                    signal,
                    actor: reason,
                });
            }
            return Err(refused(&mut outputs, args, reason, &model_name, &digest));
        }
    };
    ended
        .summary
        .write(&outputs, &args.summary)
        .map_err(|e| Failed::Unhealthy(format!("writing the summary: {e}")))?;
    // The run's one decision, taken when its loop ended: not the flag,
    // which a signal after that may have raised.
    if let Some(signal) = ended.cancelled {
        return Err(Failed::Cancelled {
            signal,
            actor: ended
                .status
                .map_or_else(|| "no status".to_string(), |s| s.to_string()),
        });
    }
    if ended.summary.supervisor_exit == summary::SupervisorExit::Unclean {
        return Err(Failed::Unhealthy(
            ended
                .summary
                .unclean_reason
                .unwrap_or_else(|| "unclean ending".to_string()),
        ));
    }
    Ok(ended.status.map_or(ExitCode::FAILURE, exit_code_of))
}

/// Every output's name held through the one door, read-only, before
/// anything is written anywhere: the three logs, the summary and its
/// staging name, each checked against the others by identity — the same
/// path twice, a hard link, a symlink to another. Nothing on disk changes.
fn preflight(outputs: &mut Outputs, args: &cli::RunArgs) -> io::Result<()> {
    for path in [
        &args.actor_event_log,
        &args.actor_stderr,
        &args.supervisor_log,
        &args.summary,
        &summary::staging_path(&args.summary),
    ] {
        outputs.reserve(path)?;
    }
    Ok(())
}

/// The three logs opened at their reserved names, and only then — every
/// name having passed — truncated. A refusal before this point leaves
/// every file as it was; nothing is created at the summary's name until
/// the end.
fn open_outputs(outputs: &mut Outputs, args: &cli::RunArgs) -> io::Result<supervisor::Artifacts> {
    let artifacts = supervisor::Artifacts {
        actor_event_log: outputs.open(&args.actor_event_log)?,
        actor_stderr: outputs.open(&args.actor_stderr)?,
        supervisor_log: outputs.open(&args.supervisor_log)?,
    };
    Outputs::truncate(&[
        &artifacts.actor_event_log,
        &artifacts.actor_stderr,
        &artifacts.supervisor_log,
    ])?;
    Ok(artifacts)
}

/// A refusal, with its summary written first — best effort: the refusal is
/// already the answer, and a summary that cannot be written leaves the
/// reader with a missing file, which the consumer treats the same way. The
/// summary's names were reserved by the preflight, against every log.
fn refused(
    outputs: &mut Outputs,
    args: &cli::RunArgs,
    reason: String,
    model: &str,
    digest: &str,
) -> Failed {
    let _ = Summary::refused(&reason, model, digest).write(outputs, &args.summary);
    Failed::Refused(reason)
}

/// A cancelled wrapper exits as a process killed by that signal would, so
/// a harness reads the cancellation and not the actor's own status.
fn exit_code_for_signal(signal: i32) -> u8 {
    u8::try_from(128 + signal).unwrap_or(1)
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
