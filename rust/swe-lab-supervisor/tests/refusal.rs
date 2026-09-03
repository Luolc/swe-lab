//! The binary as a subprocess: what a refused run says, and does not say.
//!
//! A refusal happens before any actor exists, and its diagnostic is the one
//! thing a harness log keeps of it. Nothing the caller passed — the actor
//! argv, the config path, the environment — may be repeated there: a
//! misplaced token would otherwise be persisted by the very message that
//! refused it.

// An integration test's helpers are not inside a `#[test]` function, so the
// tests-only unwrap allowance in clippy.toml does not reach them; a panic is
// the right failure signal here as in any test. A test's fixtures are not
// outputs of the wrapper, so its one-door rule for those does not apply.
#![allow(clippy::unwrap_used, clippy::expect_used, clippy::disallowed_methods)]

use std::ffi::OsString;
use std::path::PathBuf;
use std::process::Command;

const ARGV_SENTINEL: &str = "REVIEW_ACTOR_ARGV_SENTINEL_MUST_NOT_BE_LOGGED";
const PATH_SENTINEL: &str = "CONFIG_PATH_SENTINEL_MUST_NOT_BE_LOGGED";
const URL_SENTINEL: &str = "URL_SENTINEL_MUST_NOT_BE_LOGGED";

fn wrapper() -> Command {
    Command::new(env!("CARGO_BIN_EXE_swe-lab-supervisor"))
}

fn stderr_of(mut command: Command) -> (i32, String) {
    let output = command.output().unwrap();
    (
        output.status.code().unwrap(),
        String::from_utf8(output.stderr).unwrap(),
    )
}

#[test]
fn a_refused_run_names_the_fault_and_repeats_nothing_the_caller_passed() {
    let missing = format!("/nonexistent/{PATH_SENTINEL}.json");
    let mut command = wrapper();
    command
        .args(["run", "--config", &missing])
        .args(["--actor-prompt", "/dev/null"])
        .args([
            "--actor-event-log",
            "/dev/null",
            "--supervisor-log",
            "/dev/null",
        ])
        .args([
            "--summary",
            "/dev/null",
            "--actor-stderr",
            "/dev/null",
            "--",
        ])
        .args([ARGV_SENTINEL, "--token", ARGV_SENTINEL])
        .env(
            "SWE_LAB_SUPERVISOR_BASE_URL",
            format!("http://{URL_SENTINEL}:notaport/v1"),
        );
    let (code, stderr) = stderr_of(command);
    assert_eq!(code, 3, "{stderr}");
    assert!(stderr.contains("refusing to start"), "{stderr}");
    for sentinel in [ARGV_SENTINEL, PATH_SENTINEL, URL_SENTINEL] {
        assert!(
            !stderr.contains(sentinel),
            "{sentinel} was echoed: {stderr}"
        );
    }
}

#[test]
fn a_usage_error_names_the_fault_and_repeats_nothing_the_caller_passed() {
    let mut command = wrapper();
    command.args(["run", ARGV_SENTINEL, "--", "actor"]);
    let (code, stderr) = stderr_of(command);
    assert_eq!(code, 2, "{stderr}");
    assert!(stderr.contains("usage:"), "{stderr}");
    assert!(!stderr.contains(ARGV_SENTINEL), "{stderr}");

    let mut command = wrapper();
    command.args(["criteria", ARGV_SENTINEL]);
    let (code, stderr) = stderr_of(command);
    assert_eq!(code, 2, "{stderr}");
    assert!(!stderr.contains(ARGV_SENTINEL), "{stderr}");
}

#[test]
fn help_is_a_command_with_its_text_on_stdout_not_a_usage_error() {
    for flag in ["--help", "-h"] {
        let mut command = wrapper();
        command.arg(flag);
        let output = command.output().unwrap();
        let stdout = String::from_utf8(output.stdout).unwrap();
        assert_eq!(output.status.code(), Some(0), "{flag}: {stdout}");
        assert!(stdout.starts_with("usage:"), "{flag}: {stdout}");
        assert!(output.stderr.is_empty(), "{flag} wrote to stderr");
    }
}

/// Four output paths in a scratch directory of their own. The wrapper
/// refuses two outputs on one file, so `/dev/null` four times over would be
/// refused for that and not for the fault under test.
fn output_paths() -> [PathBuf; 4] {
    let dir = std::env::temp_dir().join(format!(
        "swe-lab-supervisor-refusal-{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    ));
    std::fs::create_dir_all(&dir).unwrap();
    [
        "events.jsonl",
        "supervisor.jsonl",
        "summary.json",
        "actor.stderr",
    ]
    .map(|name| dir.join(name))
}

fn output_args(paths: &[PathBuf; 4]) -> Vec<OsString> {
    let [events, supervisor, summary, stderr] = paths;
    [
        ("--actor-event-log", events),
        ("--supervisor-log", supervisor),
        ("--summary", summary),
        ("--actor-stderr", stderr),
    ]
    .into_iter()
    .flat_map(|(flag, path)| [OsString::from(flag), path.into()])
    .collect()
}

/// A valid config in a file of its own.
fn valid_config() -> std::path::PathBuf {
    let config = std::env::temp_dir().join(format!(
        "swe-lab-supervisor-refusal-{}-{}.json",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    ));
    std::fs::write(
        &config,
        r#"{"schema_version": 1, "task": "t",
            "criterion": {"name": "general-practice",
              "sha256": "ffb2dadfe2b36eb3f44f28c4282a8d51e84e1c943558500787cbb0518e2900a1"},
            "policy": {"kind": "speak-when-off-track", "budget": 1, "cooldown": 1, "window": 1,
              "judge_every_n_assistant_messages": 1, "block_actor_while_judging": "off"},
            "model": {"name": "m"},
            "timeouts": {"model_call_ms": 1000, "term_grace_ms": 1000},
            "limits": {"max_event_line_bytes": 1024, "max_actor_stdout_bytes": 1048576,
              "max_actor_stderr_bytes": 1048576}}"#,
    )
    .unwrap();
    config
}

#[test]
fn a_missing_actor_prompt_is_refused_without_the_path() {
    let config = valid_config();
    let missing = format!("/nonexistent/{PATH_SENTINEL}.stream.json");
    let mut command = wrapper();
    command
        .args(["run", "--config"])
        .arg(&config)
        .args(["--actor-prompt", &missing])
        .args(output_args(&output_paths()))
        .args(["--", "actor"])
        .env("SWE_LAB_SUPERVISOR_BASE_URL", "http://127.0.0.1:9/v1");
    let (code, stderr) = stderr_of(command);
    assert_eq!(code, 3, "{stderr}");
    assert!(stderr.contains("actor prompt"), "{stderr}");
    assert!(!stderr.contains(PATH_SENTINEL), "{stderr}");
    std::fs::remove_file(&config).unwrap();
}

#[test]
fn a_bad_base_url_is_refused_without_the_url() {
    let config = valid_config();
    let mut command = wrapper();
    command
        .args(["run", "--config"])
        .arg(&config)
        .args(["--actor-prompt", "/dev/null"])
        .args(output_args(&output_paths()))
        .args(["--", "actor"])
        .env(
            "SWE_LAB_SUPERVISOR_BASE_URL",
            format!("https://{URL_SENTINEL}/v1"),
        );
    let (code, stderr) = stderr_of(command);
    assert_eq!(code, 3, "{stderr}");
    assert!(stderr.contains("TLS"), "{stderr}");
    assert!(!stderr.contains(URL_SENTINEL), "{stderr}");
    std::fs::remove_file(&config).unwrap();
}

#[test]
fn a_missing_configured_api_key_is_refused_before_the_actor_starts() {
    let config = valid_config();
    let paths = output_paths();
    let actor_marker = paths[0].parent().unwrap().join("actor-started");
    let mut command = wrapper();
    command
        .args(["run", "--config"])
        .arg(&config)
        .args(["--actor-prompt", "/dev/null"])
        .args(output_args(&paths))
        .args(["--", "sh", "-c", "touch \"$ACTOR_MARKER\""])
        .env("ACTOR_MARKER", &actor_marker)
        .env("SWE_LAB_SUPERVISOR_BASE_URL", "http://127.0.0.1:9")
        .env(
            "SWE_LAB_SUPERVISOR_API_KEY_ENV",
            "MISSING_TEST_SUPERVISOR_KEY",
        )
        .env_remove("MISSING_TEST_SUPERVISOR_KEY");

    let (code, stderr) = stderr_of(command);

    assert_eq!(code, 3, "{stderr}");
    assert!(stderr.contains("API key is unset or empty"), "{stderr}");
    assert!(!stderr.contains("MISSING_TEST_SUPERVISOR_KEY"), "{stderr}");
    assert!(!actor_marker.exists());
    let summary = std::fs::read_to_string(&paths[2]).unwrap();
    assert!(
        !summary.contains("MISSING_TEST_SUPERVISOR_KEY"),
        "{summary}"
    );
    std::fs::remove_file(&config).unwrap();
}

#[test]
fn an_api_key_misplaced_in_the_selector_reaches_neither_diagnostic() {
    const MISPLACED_KEY: &str = "MISPLACED-CREDENTIAL-SENTINEL-MUST-NOT-LEAK";
    let config = valid_config();
    let paths = output_paths();
    let actor_marker = paths[0].parent().unwrap().join("actor-started");
    let mut command = wrapper();
    command
        .args(["run", "--config"])
        .arg(&config)
        .args(["--actor-prompt", "/dev/null"])
        .args(output_args(&paths))
        .args(["--", "sh", "-c", "touch \"$ACTOR_MARKER\""])
        .env("ACTOR_MARKER", &actor_marker)
        .env("SWE_LAB_SUPERVISOR_BASE_URL", "http://127.0.0.1:9")
        .env("SWE_LAB_SUPERVISOR_API_KEY_ENV", MISPLACED_KEY);

    let (code, stderr) = stderr_of(command);
    let summary = std::fs::read_to_string(&paths[2]).unwrap();

    assert_eq!(code, 3, "{stderr}");
    assert!(stderr.contains("not a variable name"), "{stderr}");
    assert!(!stderr.contains(MISPLACED_KEY), "{stderr}");
    assert!(!summary.contains(MISPLACED_KEY), "{summary}");
    assert!(!actor_marker.exists());
    std::fs::remove_file(&config).unwrap();
}

/// Two outputs on one file — here the summary and the supervisor log — are
/// refused before any actor exists, whichever two: every output goes through
/// the same door. The refusal names the fault, not the path.
#[test]
fn two_outputs_on_one_file_are_refused_before_any_actor_exists() {
    let config = valid_config();
    let mut paths = output_paths();
    paths[1] = paths[2].clone();
    let mut command = wrapper();
    command
        .args(["run", "--config"])
        .arg(&config)
        .args(["--actor-prompt", "/dev/null"])
        .args(output_args(&paths))
        .args(["--", "actor"])
        .env("SWE_LAB_SUPERVISOR_BASE_URL", "http://127.0.0.1:9/v1");
    let (code, stderr) = stderr_of(command);
    assert_eq!(code, 3, "{stderr}");
    assert!(stderr.contains("one file"), "{stderr}");
    assert!(!stderr.contains("launching"), "{stderr}");
    assert!(!stderr.contains("summary.json"), "{stderr}");
    std::fs::remove_file(&config).unwrap();
}

/// Every output's name is held before any refusal can write a summary: a
/// run refused on its inputs whose `--summary` names the event log is a
/// refusal that writes nothing, and the log keeps its bytes. The control
/// is the same refusal with distinct names, which writes its summary.
#[test]
fn an_early_refusal_writes_nothing_over_an_output_it_would_alias() {
    let mut paths = output_paths();
    std::fs::write(&paths[0], "a previous run's events\n").unwrap();
    paths[2] = paths[0].clone();
    let missing = format!("/nonexistent/{PATH_SENTINEL}.json");
    let mut command = wrapper();
    command
        .args(["run", "--config", &missing])
        .args(["--actor-prompt", "/dev/null"])
        .args(output_args(&paths))
        .args(["--", "actor"])
        .env("SWE_LAB_SUPERVISOR_BASE_URL", "http://127.0.0.1:9/v1");
    let (code, stderr) = stderr_of(command);
    assert_eq!(code, 3, "{stderr}");
    assert!(stderr.contains("one file"), "{stderr}");
    assert_eq!(
        std::fs::read_to_string(&paths[0]).unwrap(),
        "a previous run's events\n"
    );

    let paths = output_paths();
    let mut command = wrapper();
    command
        .args(["run", "--config", &missing])
        .args(["--actor-prompt", "/dev/null"])
        .args(output_args(&paths))
        .args(["--", "actor"])
        .env("SWE_LAB_SUPERVISOR_BASE_URL", "http://127.0.0.1:9/v1");
    let (code, _) = stderr_of(command);
    assert_eq!(code, 3);
    let summary: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(&paths[2]).unwrap()).unwrap();
    assert_eq!(summary["supervisor_exit"], "refused");
}
