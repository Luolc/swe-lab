//! The command line: what the wrapper is told, and nothing it is not.
//!
//! Hand-parsed: five path flags and a `--` separator do not need an argument
//! parser, and every dependency is one more thing to keep pure Rust.

use std::ffi::OsString;
use std::path::PathBuf;

/// Printed on a usage error and by `--help`.
pub const USAGE: &str = "\
usage:
  swe-lab-supervisor run \\
      --config <supervisor-config.json> \\
      --actor-event-log <actor.event_stream.jsonl> \\
      --supervisor-log <supervisor.jsonl> \\
      --summary <supervisor-summary.json> \\
      --actor-stderr <actor.stderr.log> \\
      -- <actor executable> [<actor argument>...]
  swe-lab-supervisor criteria     # the embedded criteria and their sha256
  swe-lab-supervisor --version

The actor argv after `--` is executed as given: it is never joined into a
shell command, and the wrapper adds no flags of its own.";

/// What the wrapper was asked to do.
#[derive(Debug, PartialEq, Eq)]
pub enum Command {
    /// Supervise one actor run.
    Run(RunArgs),
    /// List the criteria compiled into this binary, with their digests.
    Criteria,
    /// Print the binary's version.
    Version,
}

/// The inputs of one supervised run.
#[derive(Debug, PartialEq, Eq)]
pub struct RunArgs {
    /// The schema-versioned, non-secret run settings.
    pub config: PathBuf,
    /// Where the actor's stdout lines are written, verbatim.
    pub actor_event_log: PathBuf,
    /// Where the supervisor's own account of the run is written.
    pub supervisor_log: PathBuf,
    /// Where the terminal summary is written, atomically, at the end.
    pub summary: PathBuf,
    /// Where the actor's stderr is written.
    pub actor_stderr: PathBuf,
    /// The actor command, as opaque tokens.
    pub actor_argv: Vec<OsString>,
}

/// Parse the arguments after the program name.
pub fn parse<I>(args: I) -> Result<Command, String>
where
    I: IntoIterator<Item = OsString>,
{
    let mut args = args.into_iter();
    let Some(first) = args.next() else {
        return Err("no command given".to_string());
    };
    match first.to_str() {
        Some("run") => parse_run(args),
        Some("criteria") => reject_extra(args, Command::Criteria),
        Some("--version" | "-V") => reject_extra(args, Command::Version),
        Some("--help" | "-h") => Err("help requested".to_string()),
        _ => Err(format!("unknown command {}", first.display())),
    }
}

fn reject_extra<I>(mut args: I, command: Command) -> Result<Command, String>
where
    I: Iterator<Item = OsString>,
{
    match args.next() {
        None => Ok(command),
        Some(extra) => Err(format!("unexpected argument {}", extra.display())),
    }
}

fn parse_run<I>(args: I) -> Result<Command, String>
where
    I: Iterator<Item = OsString>,
{
    let mut config = None;
    let mut actor_event_log = None;
    let mut supervisor_log = None;
    let mut summary = None;
    let mut actor_stderr = None;
    let mut actor_argv = Vec::new();
    let mut args = args.peekable();
    while let Some(arg) = args.next() {
        if arg == "--" {
            actor_argv.extend(args);
            break;
        }
        let Some(flag) = arg.to_str() else {
            return Err(format!("unexpected argument {}", arg.display()));
        };
        let slot = match flag {
            "--config" => &mut config,
            "--actor-event-log" => &mut actor_event_log,
            "--supervisor-log" => &mut supervisor_log,
            "--summary" => &mut summary,
            "--actor-stderr" => &mut actor_stderr,
            _ => return Err(format!("unknown flag {flag}")),
        };
        let Some(value) = args.next() else {
            return Err(format!("{flag} needs a value"));
        };
        if value == "--" {
            return Err(format!("{flag} needs a value"));
        }
        if slot.replace(PathBuf::from(value)).is_some() {
            return Err(format!("{flag} given twice"));
        }
    }
    if actor_argv.is_empty() {
        return Err("no actor command after `--`".to_string());
    }
    Ok(Command::Run(RunArgs {
        config: required(config, "--config")?,
        actor_event_log: required(actor_event_log, "--actor-event-log")?,
        supervisor_log: required(supervisor_log, "--supervisor-log")?,
        summary: required(summary, "--summary")?,
        actor_stderr: required(actor_stderr, "--actor-stderr")?,
        actor_argv,
    }))
}

fn required(value: Option<PathBuf>, flag: &str) -> Result<PathBuf, String> {
    value.ok_or_else(|| format!("{flag} is required"))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn os(args: &[&str]) -> Vec<OsString> {
        args.iter().map(OsString::from).collect()
    }

    const FULL: &[&str] = &[
        "run",
        "--config",
        "c.json",
        "--actor-event-log",
        "events.jsonl",
        "--supervisor-log",
        "sup.jsonl",
        "--summary",
        "summary.json",
        "--actor-stderr",
        "stderr.log",
        "--",
        "claude",
        "-p",
        "--output-format",
        "stream-json",
    ];

    #[test]
    fn a_full_run_command_parses_and_keeps_the_actor_argv_opaque() {
        let Command::Run(args) = parse(os(FULL)).unwrap() else {
            panic!("not a run command");
        };
        assert_eq!(args.config, PathBuf::from("c.json"));
        assert_eq!(args.summary, PathBuf::from("summary.json"));
        assert_eq!(
            args.actor_argv,
            os(&["claude", "-p", "--output-format", "stream-json"])
        );
    }

    #[test]
    fn flags_after_the_separator_belong_to_the_actor() {
        let mut command_line = FULL.to_vec();
        command_line.extend(["--config", "theirs.json"]);
        let Command::Run(args) = parse(os(&command_line)).unwrap() else {
            panic!("not a run command");
        };
        assert_eq!(args.config, PathBuf::from("c.json"));
        assert!(args.actor_argv.ends_with(&os(&["--config", "theirs.json"])));
    }

    #[test]
    fn a_missing_flag_or_actor_is_a_usage_error() {
        let without_summary: Vec<&str> = FULL
            .iter()
            .copied()
            .filter(|a| *a != "--summary" && *a != "summary.json")
            .collect();
        assert!(
            parse(os(&without_summary))
                .unwrap_err()
                .contains("--summary")
        );

        let without_actor = &FULL[..FULL.iter().position(|a| *a == "--").unwrap()];
        assert!(parse(os(without_actor)).unwrap_err().contains("actor"));
    }

    #[test]
    fn the_other_commands_take_no_arguments() {
        assert_eq!(parse(os(&["criteria"])).unwrap(), Command::Criteria);
        assert_eq!(parse(os(&["--version"])).unwrap(), Command::Version);
        assert!(parse(os(&["criteria", "x"])).is_err());
        assert!(parse(os(&[])).is_err());
    }
}
