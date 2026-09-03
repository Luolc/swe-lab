//! `swe-lab-supervisor`: the in-sandbox supervision runtime.
//!
//! Wraps a coding agent (the *actor*) as a child process, owns its stdin,
//! stdout, stderr and process group, drains its `stream-json` output while a
//! judge model is consulted at configured boundaries, and writes short
//! corrections on the actor's stdin. The design is swe-lab issue #375; the
//! policy semantics are those of `swe_lab.trace_synthesis` on the Python side,
//! which this binary replaces inside a sandbox.

mod cli;
mod config;
mod criterion;

use std::process::ExitCode;

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

/// Validate the run's inputs, then hand over to the runtime.
///
/// Everything that can refuse the run happens here, before any actor process
/// exists: a run that cannot be supervised as configured is not started.
fn run(args: &cli::RunArgs) -> Result<ExitCode, String> {
    let config = config::load(&args.config)?;
    let selected = criterion::select(&config.criterion.name, &config.criterion.sha256)?;
    Err(format!(
        "the runtime is not built yet; config {} and criterion {} ({}) are valid, and the actor would be {:?}",
        args.config.display(),
        selected.name,
        selected.digest,
        args.actor_argv
    ))
}
