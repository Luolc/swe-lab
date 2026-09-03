//! The binary as a subprocess: what a refused run says, and does not say.
//!
//! A refusal happens before any actor exists, and its diagnostic is the one
//! thing a harness log keeps of it. Nothing the caller passed — the actor
//! argv, the config path, the environment — may be repeated there: a
//! misplaced token would otherwise be persisted by the very message that
//! refused it.

// An integration test's helpers are not inside a `#[test]` function, so the
// tests-only unwrap allowance in clippy.toml does not reach them; a panic is
// the right failure signal here as in any test.
#![allow(clippy::unwrap_used, clippy::expect_used)]

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
fn a_bad_base_url_is_refused_without_the_url() {
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
              "judge_every_n_assistant_messages": 1, "block_actor_while_judging": true},
            "model": {"name": "m"},
            "timeouts": {"model_call_ms": 1000, "term_grace_ms": 1000},
            "limits": {"max_event_line_bytes": 1024}}"#,
    )
    .unwrap();
    let mut command = wrapper();
    command
        .args(["run", "--config"])
        .arg(&config)
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
            "actor",
        ])
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
