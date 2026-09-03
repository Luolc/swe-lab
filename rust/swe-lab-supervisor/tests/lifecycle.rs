//! The binary as a subprocess around actors that end badly: the wrapper
//! ends every one of them, and says when the record is not whole.
//!
//! No model is ever contacted here — the endpoint variable names a port
//! nothing listens on, and these actors emit nothing a boundary would fall
//! on, so no call is made.

// An integration test's helpers are not inside a `#[test]` function, so the
// tests-only unwrap allowance in clippy.toml does not reach them; a panic is
// the right failure signal here as in any test.
#![allow(clippy::unwrap_used, clippy::expect_used)]

use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Output, Stdio};
use std::time::{Duration, Instant};

use nix::sys::signal::{Signal, kill};
use nix::unistd::Pid;

const CRITERION_SHA256: &str = "ffb2dadfe2b36eb3f44f28c4282a8d51e84e1c943558500787cbb0518e2900a1";

fn scratch(name: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!(
        "swe-lab-supervisor-lifecycle-{name}-{}",
        std::process::id()
    ));
    fs::create_dir_all(&dir).unwrap();
    dir
}

fn config(dir: &Path, grace_ms: u64) -> PathBuf {
    config_with_task(dir, grace_ms, "t")
}

fn config_with_task(dir: &Path, grace_ms: u64, task: &str) -> PathBuf {
    let path = dir.join("config.json");
    fs::write(
        &path,
        format!(
            r#"{{"schema_version": 1, "task": "{task}",
            "criterion": {{"name": "general-practice", "sha256": "{CRITERION_SHA256}"}},
            "policy": {{"kind": "speak-when-off-track", "budget": 1, "cooldown": 1, "window": 1,
              "judge_every_n_assistant_messages": 1, "block_actor_while_judging": "off"}},
            "model": {{"name": "m"}},
            "timeouts": {{"model_call_ms": 1000, "term_grace_ms": {grace_ms}}},
            "limits": {{"max_event_line_bytes": 65536, "max_actor_stdout_bytes": 1048576,
              "max_actor_stderr_bytes": 1048576}}}}"#
        ),
    )
    .unwrap();
    path
}

/// Run the wrapper around `sh -c <script>`, with `probe` in the actor's
/// environment so its descendants can be found afterwards.
fn wrap(dir: &Path, script: &str, event_log: &Path, probe: &str) -> Output {
    wrapper(dir, config(dir, 500), script, event_log, probe)
        .output()
        .unwrap()
}

fn wrapper(dir: &Path, config: PathBuf, script: &str, event_log: &Path, probe: &str) -> Command {
    let prompt = dir.join("prompt.stream.json");
    fs::write(
        &prompt,
        "{\"type\":\"user\",\"message\":{\"role\":\"user\",\"content\":[{\"type\":\"text\",\"text\":\"t\"}]}}\n",
    )
    .unwrap();
    let mut command = Command::new(env!("CARGO_BIN_EXE_swe-lab-supervisor"));
    command
        .arg("run")
        .arg("--config")
        .arg(config)
        .arg("--actor-prompt")
        .arg(&prompt)
        .arg("--actor-event-log")
        .arg(event_log)
        .arg("--supervisor-log")
        .arg(dir.join("supervisor.jsonl"))
        .arg("--summary")
        .arg(dir.join("summary.json"))
        .arg("--actor-stderr")
        .arg(dir.join("actor.stderr"))
        .args(["--", "sh", "-c", script])
        .env("SWE_LAB_SUPERVISOR_BASE_URL", "http://127.0.0.1:9/v1")
        .env("PROBE", probe);
    command
}

/// Start the wrapper, wait until its actor is up (the probe appears), send
/// it `signal`, and collect how it ended.
fn cancel(mut wrapper: Command, probe: &str, signal: Signal) -> (Output, Duration) {
    let child: Child = wrapper
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    let deadline = Instant::now() + Duration::from_secs(10);
    while probes_alive(probe) == 0 {
        assert!(Instant::now() < deadline, "the actor never came up");
        std::thread::sleep(Duration::from_millis(20));
    }
    std::thread::sleep(Duration::from_millis(200));
    let started = Instant::now();
    kill(Pid::from_raw(i32::try_from(child.id()).unwrap()), signal).unwrap();
    let output = child.wait_with_output().unwrap();
    (output, started.elapsed())
}

/// How many live processes carry `PROBE=<value>` in their environment.
fn probes_alive(value: &str) -> usize {
    let needle = format!("PROBE={value}");
    fs::read_dir("/proc")
        .unwrap()
        .flatten()
        .filter(|entry| {
            entry
                .file_name()
                .to_str()
                .is_some_and(|name| name.bytes().all(|b| b.is_ascii_digit()))
                && fs::read(entry.path().join("environ"))
                    .is_ok_and(|environ| environ.split(|b| *b == 0).any(|e| e == needle.as_bytes()))
        })
        .count()
}

fn none_alive_within(probe: &str, timeout: Duration) -> bool {
    let deadline = Instant::now() + timeout;
    loop {
        if probes_alive(probe) == 0 {
            return true;
        }
        if Instant::now() > deadline {
            return false;
        }
        std::thread::sleep(Duration::from_millis(20));
    }
}

#[test]
fn a_leader_that_exits_while_a_descendant_holds_its_stdout_is_ended_within_the_grace() {
    let dir = scratch("inherited-stdout");
    let probe = format!("inherited-{}", std::process::id());
    let started = Instant::now();
    let output = wrap(
        &dir,
        "read -r prompt; echo '{\"type\":\"x\"}'; ( sleep 30 ) & exit 7",
        &dir.join("events.jsonl"),
        &probe,
    );
    let took = started.elapsed();
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert_eq!(output.status.code(), Some(7), "{stderr}");
    assert!(
        took < Duration::from_secs(10),
        "hung on the inherited pipe: {took:?}"
    );
    assert!(none_alive_within(&probe, Duration::from_secs(5)));
    assert_eq!(
        fs::read_to_string(dir.join("events.jsonl")).unwrap(),
        "{\"type\":\"x\"}\n"
    );
    fs::remove_dir_all(&dir).unwrap();
}

#[test]
fn an_event_log_that_cannot_be_written_is_not_a_success() {
    let dir = scratch("dev-full");
    let probe = format!("full-{}", std::process::id());
    let output = wrap(
        &dir,
        "read -r prompt; echo '{\"type\":\"x\"}'; exit 0",
        Path::new("/dev/full"),
        &probe,
    );
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert_eq!(output.status.code(), Some(1), "{stderr}");
    assert!(stderr.contains("not accounted for"), "{stderr}");
    assert!(stderr.contains("stdout"), "{stderr}");
    assert!(none_alive_within(&probe, Duration::from_secs(5)));
    fs::remove_dir_all(&dir).unwrap();
}

#[test]
fn a_descendant_that_left_the_process_group_does_not_survive_the_wrapper() {
    let dir = scratch("setsid");
    let probe = format!("setsid-{}", std::process::id());
    let output = wrap(
        &dir,
        "read -r prompt; setsid sleep 30 >/dev/null 2>&1 </dev/null & sleep 0.2; exit 0",
        &dir.join("events.jsonl"),
        &probe,
    );
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert_eq!(output.status.code(), Some(0), "{stderr}");
    assert!(
        none_alive_within(&probe, Duration::from_secs(2)),
        "a process outside the group outlived the wrapper"
    );
    fs::remove_dir_all(&dir).unwrap();
}

#[test]
fn an_actor_that_closes_stdout_early_gets_its_grace_to_exit_on_its_own() {
    let dir = scratch("early-eof");
    let probe = format!("early-{}", std::process::id());
    let started = Instant::now();
    let output = wrapper(
        &dir,
        config(&dir, 3_000),
        "read -r prompt; exec >/dev/null; sleep 1; exit 7",
        &dir.join("events.jsonl"),
        &probe,
    )
    .output()
    .unwrap();
    let took = started.elapsed();
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert_eq!(output.status.code(), Some(7), "{stderr}");
    assert!(took >= Duration::from_secs(1), "did not wait: {took:?}");
    assert!(
        took < Duration::from_secs(3),
        "forced instead of waited: {took:?}"
    );
    fs::remove_dir_all(&dir).unwrap();
}

#[test]
fn a_cancelled_run_is_reported_cancelled_even_when_the_actor_exits_zero() {
    let dir = scratch("cancel-trap");
    let probe = format!("cancel-{}", std::process::id());
    let (output, took) = cancel(
        wrapper(
            &dir,
            config(&dir, 2_000),
            "trap 'exit 0' TERM; read -r prompt; sleep 30 & wait",
            &dir.join("events.jsonl"),
            &probe,
        ),
        &probe,
        Signal::SIGTERM,
    );
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert_eq!(output.status.code(), Some(143), "{stderr}");
    assert!(stderr.contains("cancelled by signal 15"), "{stderr}");
    assert!(took < Duration::from_secs(10), "{took:?}");
    assert!(none_alive_within(&probe, Duration::from_secs(5)));
    fs::remove_dir_all(&dir).unwrap();
}

#[test]
fn a_prompt_the_actor_never_reads_does_not_hold_the_wrapper_against_cancellation() {
    let dir = scratch("cancel-prompt");
    let probe = format!("prompt-{}", std::process::id());
    // A prompt far past what one pipe holds, and an actor that never reads.
    let mut command = wrapper(
        &dir,
        config(&dir, 30_000),
        "exec sleep 30",
        &dir.join("events.jsonl"),
        &probe,
    );
    let prompt = dir.join("prompt.stream.json");
    fs::write(&prompt, "x".repeat(1 << 20)).unwrap();
    command.arg("--actor-prompt").arg(&prompt);
    let (output, took) = cancel(command, &probe, Signal::SIGTERM);
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert_eq!(output.status.code(), Some(143), "{stderr}");
    assert!(
        took < Duration::from_secs(5),
        "blocked in the prompt write: {took:?}"
    );
    assert!(none_alive_within(&probe, Duration::from_secs(5)));
    fs::remove_dir_all(&dir).unwrap();
}
