//! The binary as a subprocess around a fake actor, with a scripted model
//! endpoint on loopback: the whole path, launch to terminal summary.
//!
//! The actor is a shell script that behaves like a `stream-json` actor: it
//! takes its prompt on stdin, emits events, blocks on stdin until the
//! correction arrives, echoes the injected user event back as Claude Code
//! does, ends its turn with a `result`, and exits on EOF. The endpoint
//! answers the judge and the writer from a script and hands every request to
//! the test together with the actor's process state at the moment it
//! answered. No real provider is contacted. The same script runs under each
//! of the three blocking modes.

// An integration test's helpers are not inside a `#[test]` function, so the
// tests-only unwrap allowance in clippy.toml does not reach them; a panic is
// the right failure signal here as in any test. A test's fixtures are not
// outputs of the wrapper, so its one-door rule for those does not apply.
#![allow(clippy::unwrap_used, clippy::expect_used, clippy::disallowed_methods)]

use std::fs;
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::mpsc;
use std::thread;
use std::time::Duration;

use nix::sys::signal::{Signal, kill};
use nix::unistd::Pid;

use serde_json::{Value, json};
use sha2::{Digest, Sha256};

const KEY_SENTINEL: &str = "KEY_SENTINEL_MUST_NOT_BE_LOGGED";
const TASK: &str = "Fix the flaky test in the scheduler.";
/// The actor's prompt file: one stream-json user event whose text is not
/// the task, and which carries the escapes a shell `echo` would mangle.
const PROMPT_LINE: &str = "{\"type\":\"user\",\"message\":{\"role\":\"user\",\"content\":[{\"type\":\"text\",\"text\":\"PROMPT_SENTINEL: the scheduler test flakes under load\\nsee tests/test_scheduler.py \\\\ line 40\"}]}}\n";
const CORRECTION: &str = "Run the test suite before you declare the change done.";
const MARKER: &str = "declared done without running the tests";
const CRITERION_SHA256: &str = "ffb2dadfe2b36eb3f44f28c4282a8d51e84e1c943558500787cbb0518e2900a1";

/// The fake actor. `printf`, not `echo`: the echoed user event carries JSON
/// escapes that a shell `echo` would interpret. The probes on the first two
/// lines report presence only, never a value. The watchdog bounds a wrapper
/// that never speaks or never closes stdin; its descriptors are detached so
/// the actor's stdout closes when the actor exits, not when the watchdog does.
const ACTOR: &str = r#"
[ -n "$SWE_LAB_SUPERVISOR_API_KEY" ] && echo "LEAK: the api key is in the actor's environment" >&2
[ -n "$SWE_LAB_SUPERVISOR_BASE_URL" ] && echo "LEAK: the base url is in the actor's environment" >&2
( sleep 30; kill -TERM $$ ) >/dev/null 2>&1 </dev/null &
read -r prompt || exit 90
case "$prompt" in *PROMPT_SENTINEL*) ;; *) exit 92 ;; esac
printf '%s
' "$prompt"
printf '%s\n' '{"type":"system","subtype":"init","model":"fake"}'
printf '%s\n' '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"Done; I will not run the tests."}]}}'
printf '%s\n' '{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","name":"Bash","input":{"command":"git commit -am done"}}]}}'
read -r note || exit 91
printf '%s\n' "$note"
printf '%s\n' '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"Running the tests now."}]}}'
printf '%s\n' '{"type":"result","subtype":"success","is_error":false}'
cat >/dev/null
echo "actor: stdin closed, exiting" >&2
exit 0
"#;

/// One request the endpoint answered, and what the actor was doing then.
struct Answered {
    request: String,
    actor_states: Vec<char>,
}

/// Read one whole request — head and `Content-Length` body — off a socket.
fn read_request(socket: &mut TcpStream) -> Vec<u8> {
    let mut request = Vec::new();
    let mut buffer = [0u8; 4096];
    loop {
        let n = socket.read(&mut buffer).unwrap();
        assert!(n > 0, "the client closed before finishing its request");
        request.extend_from_slice(&buffer[..n]);
        if let Some(split) = request.windows(4).position(|w| w == b"\r\n\r\n") {
            let head = String::from_utf8_lossy(&request[..split]).to_string();
            let length: usize = head
                .lines()
                .find_map(|l| l.strip_prefix("Content-Length: "))
                .and_then(|v| v.parse().ok())
                .unwrap_or(0);
            if request.len() >= split + 4 + length {
                return request;
            }
        }
    }
}

/// The state letter of every process running `script` under `/bin/sh`: the
/// actor and its watchdog subshell, never the wrapper (whose argv also names
/// the script, after `--`).
fn actor_states(script: &Path) -> Vec<char> {
    let mut states = Vec::new();
    for entry in fs::read_dir("/proc").unwrap().flatten() {
        let name = entry.file_name();
        let Some(pid) = name
            .to_str()
            .filter(|p| p.bytes().all(|b| b.is_ascii_digit()))
        else {
            continue;
        };
        let Ok(cmdline) = fs::read(format!("/proc/{pid}/cmdline")) else {
            continue;
        };
        let mut argv = cmdline.split(|b| *b == 0);
        if argv.next() != Some(b"/bin/sh")
            || argv.next() != Some(script.as_os_str().as_encoded_bytes())
        {
            continue;
        }
        let Ok(stat) = fs::read_to_string(format!("/proc/{pid}/stat")) else {
            continue;
        };
        // `pid (comm) S ...` — the state is the first field after the last `)`.
        let Some(state) = stat
            .rsplit_once(')')
            .and_then(|(_, rest)| rest.trim_start().chars().next())
        else {
            continue;
        };
        // A shell that vforked a child which was stopped before it could
        // exec waits in `D` inside `kernel_clone`: stopped, by any measure
        // but the letter.
        let vfork_wait = state == 'D'
            && fs::read_to_string(format!("/proc/{pid}/wchan"))
                .is_ok_and(|wchan| wchan.trim() == "kernel_clone");
        states.push(if vfork_wait { 'T' } else { state });
    }
    states
}

/// The actor's state while a request is being answered. `SIGSTOP` is sent
/// just before the judge is called, and the kernel stops each member of the
/// group a moment later, so a stopped actor is waited for. An unstopped one
/// is waited for too, in its own way: a sample that lands inside one of its
/// vfork windows reads as stopped (`D` in `kernel_clone`, see above) for the
/// microseconds until the child execs, and is sampled again.
fn settled_actor_states(script: &Path, expect_stopped: bool) -> Vec<char> {
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(2);
    loop {
        let states = actor_states(script);
        let settled = !states.is_empty() && states.iter().all(|s| (*s == 'T') == expect_stopped);
        if settled || std::time::Instant::now() > deadline {
            return states;
        }
        thread::sleep(std::time::Duration::from_millis(5));
    }
}

/// A chat-completions endpoint on loopback that answers requests, in order,
/// with the given assistant contents — each after `delay` — and every
/// further request with a 500.
fn endpoint(
    script: PathBuf,
    expect_stopped: bool,
    contents: Vec<String>,
    delay: Duration,
) -> (u16, mpsc::Receiver<Answered>) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    let (tx, rx) = mpsc::channel();
    thread::spawn(move || {
        let mut contents = contents.into_iter();
        loop {
            let (mut socket, _) = listener.accept().unwrap();
            let request = read_request(&mut socket);
            let states = settled_actor_states(&script, expect_stopped);
            thread::sleep(delay);
            let (http_status, content) = match contents.next() {
                Some(content) => ("200 OK", content),
                None => (
                    "500 Internal Server Error",
                    "unscripted request".to_string(),
                ),
            };
            let body = json!({
                "model": "fake-model",
                "choices": [{"finish_reason": "stop",
                             "message": {"role": "assistant", "content": content}}],
            })
            .to_string();
            // A client that gave up on the call before the answer has
            // closed the socket; the request it made is still on record.
            let _ = write!(
                socket,
                "HTTP/1.1 {http_status}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                body.len()
            );
            let answered = Answered {
                request: String::from_utf8_lossy(&request).into_owned(),
                actor_states: states,
            };
            if tx.send(answered).is_err() {
                return;
            }
        }
    });
    (port, rx)
}

struct Run {
    dir: PathBuf,
    status: Option<i32>,
    wrapper_stderr: String,
    actor_stderr: String,
    event_log: String,
    supervisor_log: String,
    summary: String,
    answered: Vec<Answered>,
}

/// One run of the wrapper around a scripted actor and a scripted endpoint.
struct Scenario<'a> {
    /// Names the scratch directory.
    name: &'a str,
    blocking: &'a str,
    actor: &'a str,
    judge_every_n_assistant_messages: u32,
    answers: Vec<String>,
    /// How long the endpoint takes over each answer.
    answer_delay: Duration,
    /// `limits.max_actor_stdout_bytes`, which also caps the supervisor log.
    stdout_cap: u64,
    /// Send the wrapper `SIGTERM` this long after launching it.
    cancel_after: Option<Duration>,
    /// Hold the wrapper this long between the actor's end and the summary
    /// (a debug-build hook).
    hold_before_summary: Option<Duration>,
}

fn supervise(blocking: &str) -> Run {
    supervise_scenario(&Scenario {
        name: blocking,
        blocking,
        actor: ACTOR,
        judge_every_n_assistant_messages: 2,
        answers: vec![
            json!({"off_track": true, "self_correcting": false, "reason": MARKER}).to_string(),
            CORRECTION.to_string(),
            json!({"off_track": false, "self_correcting": false, "reason": "the tests are running"})
                .to_string(),
        ],
        answer_delay: Duration::ZERO,
        stdout_cap: 1_048_576,
        cancel_after: None,
        hold_before_summary: None,
    })
}

/// A wrapper that hangs is a failed test, not a hung suite: unless the
/// returned sender is dropped within a minute, the wrapper is killed.
fn watchdog(wrapper: Pid) -> mpsc::Sender<()> {
    let (done, timer) = mpsc::channel::<()>();
    thread::spawn(move || {
        if timer.recv_timeout(Duration::from_mins(1)) == Err(mpsc::RecvTimeoutError::Timeout) {
            let _ = kill(wrapper, Signal::SIGKILL);
        }
    });
    done
}

fn supervise_scenario(scenario: &Scenario<'_>) -> Run {
    let dir = std::env::temp_dir().join(format!(
        "swe-lab-supervisor-e2e-{}-{}",
        scenario.name,
        std::process::id()
    ));
    fs::create_dir_all(&dir).unwrap();
    let script = dir.join("actor.sh");
    fs::write(&script, scenario.actor).unwrap();
    let config = dir.join("config.json");
    fs::write(
        &config,
        json!({
            "schema_version": 1,
            "task": TASK,
            "criterion": {"name": "general-practice", "sha256": CRITERION_SHA256},
            "policy": {
                "kind": "speak-when-off-track",
                "budget": 1,
                "cooldown": 1,
                "window": 8,
                "judge_every_n_assistant_messages": scenario.judge_every_n_assistant_messages,
                "block_actor_while_judging": scenario.blocking,
            },
            "model": {"name": "fake-model"},
            "timeouts": {"model_call_ms": 10_000, "term_grace_ms": 2_000},
            "limits": {
                "max_event_line_bytes": 65_536,
                "max_actor_stdout_bytes": scenario.stdout_cap,
                "max_actor_stderr_bytes": 1_048_576,
            },
        })
        .to_string(),
    )
    .unwrap();
    let (port, answers) = endpoint(
        script.clone(),
        scenario.blocking == "sigstop",
        scenario.answers.clone(),
        scenario.answer_delay,
    );
    let prompt = dir.join("prompt.stream.json");
    fs::write(&prompt, PROMPT_LINE).unwrap();
    let child = Command::new(env!("CARGO_BIN_EXE_swe-lab-supervisor"))
        .arg("run")
        .arg("--config")
        .arg(&config)
        .arg("--actor-prompt")
        .arg(&prompt)
        .arg("--actor-event-log")
        .arg(dir.join("events.jsonl"))
        .arg("--supervisor-log")
        .arg(dir.join("supervisor.jsonl"))
        .arg("--summary")
        .arg(dir.join("summary.json"))
        .arg("--actor-stderr")
        .arg(dir.join("actor.stderr"))
        .arg("--")
        .arg("/bin/sh")
        .arg(&script)
        .env(
            "SWE_LAB_SUPERVISOR_BASE_URL",
            format!("http://127.0.0.1:{port}/v1"),
        )
        .env(
            "SWE_LAB_SUPERVISOR_API_KEY",
            format!("{KEY_SENTINEL},second-key-never-sent"),
        )
        .envs(scenario.hold_before_summary.map(|hold| {
            (
                "SWE_LAB_SUPERVISOR_DEBUG_HOLD_BEFORE_SUMMARY_MS",
                hold.as_millis().to_string(),
            )
        }))
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .spawn()
        .unwrap();
    let wrapper = Pid::from_raw(i32::try_from(child.id()).unwrap());
    let done = watchdog(wrapper);
    if let Some(after) = scenario.cancel_after {
        thread::sleep(after);
        kill(wrapper, Signal::SIGTERM).unwrap();
    }
    let output = child.wait_with_output().unwrap();
    drop(done);
    let read = |name: &str| fs::read_to_string(dir.join(name)).unwrap_or_default();
    Run {
        status: output.status.code(),
        wrapper_stderr: String::from_utf8_lossy(&output.stderr).into_owned(),
        actor_stderr: read("actor.stderr"),
        event_log: read("events.jsonl"),
        supervisor_log: read("supervisor.jsonl"),
        summary: read("summary.json"),
        answered: answers.try_iter().collect(),
        dir,
    }
}

fn sha256_of(path: &Path) -> String {
    format!("{:x}", Sha256::digest(fs::read(path).unwrap()))
}

/// The user prompt of one chat-completions request.
fn prompt_of(answered: &Answered) -> String {
    let (_, body) = answered.request.split_once("\r\n\r\n").unwrap();
    let body: Value = serde_json::from_str(body).unwrap();
    body["messages"][1]["content"].as_str().unwrap().to_string()
}

fn rows_of(text: &str) -> Vec<Value> {
    text.lines()
        .map(|l| serde_json::from_str(l).unwrap())
        .collect()
}

/// The actor's side: prompted, corrected, released, its environment clean.
fn check_actor(run: &Run, context: &str) {
    // The prompt file's bytes reached the actor as they were: it echoed its
    // first stdin line, and that line is the file minus its newline.
    assert_eq!(
        run.event_log.lines().next(),
        Some(PROMPT_LINE.trim_end()),
        "{context}"
    );
    let events = rows_of(&run.event_log);
    assert_eq!(events.len(), 7, "{context}");
    assert_eq!(events[0]["type"], "user", "{context}");
    assert_eq!(events[4]["type"], "user", "{context}");
    assert_eq!(
        events[4]["message"]["content"][0]["text"],
        format!("<supervisor_note>\n{CORRECTION}\n</supervisor_note>"),
        "{context}"
    );
    assert!(
        run.actor_stderr.contains("actor: stdin closed, exiting"),
        "{context}"
    );
    assert!(!run.actor_stderr.contains("LEAK"), "{context}");
}

/// The account: one boundary spoken — its `correction` row on disk before
/// the correction was sent, the `spoke` row behind it — one silent,
/// everything else observed.
fn check_account(run: &Run, context: &str) {
    let rows = rows_of(&run.supervisor_log);
    let kinds: Vec<&str> = rows.iter().map(|r| r["kind"].as_str().unwrap()).collect();
    assert_eq!(
        kinds,
        [
            "observed",
            "observed",
            "observed",
            "correction",
            "spoke",
            "observed",
            "observed",
            "silent"
        ],
        "{context}"
    );
    assert_eq!(rows[0]["evidence"], "excluded-external-text", "{context}");
    let correction = &rows[3];
    assert_eq!(correction["boundary"], 1, "{context}");
    assert_eq!(correction["cursor"], 4, "{context}");
    assert_eq!(correction["marker"], MARKER, "{context}");
    assert_eq!(correction["text"], CORRECTION, "{context}");
    assert_eq!(rows[4]["boundary"], 1, "{context}");
    assert_eq!(rows[4]["cursor"], 4, "{context}");
    let purposes: Vec<&str> = correction["calls"]
        .as_array()
        .unwrap()
        .iter()
        .map(|c| c["purpose"].as_str().unwrap())
        .collect();
    assert_eq!(purposes, ["judge", "writer"], "{context}");
    assert_eq!(
        rows[5]["evidence"], "excluded-own-intervention",
        "{context}"
    );
    let silent = &rows[7];
    assert_eq!(silent["boundary"], 2, "{context}");
    assert_eq!(silent["cursor"], 7, "{context}");
    assert!(silent.get("marker").is_none(), "{context}");
}

fn check_summary(run: &Run, context: &str) {
    let summary: Value = serde_json::from_str(&run.summary).unwrap();
    for (field, expected) in [
        ("accounted_for", json!(true)),
        ("supervisor_exit", json!("clean")),
        ("actor_exit_code", json!(0)),
        ("events", json!(7)),
        ("undecodable_lines", json!(0)),
        ("oversized_lines", json!(0)),
        ("boundaries", json!(2)),
        ("corrections", json!(1)),
        ("silent", json!(1)),
        ("unjudged", json!(0)),
        ("lapses", json!(0)),
        ("gaps", json!(0)),
        ("stale_verdicts_discarded", json!(0)),
        ("stragglers_killed", json!(0)),
        ("model", json!("fake-model")),
        ("criterion_sha256", json!(CRITERION_SHA256)),
        (
            "actor_event_log_sha256",
            json!(sha256_of(&run.dir.join("events.jsonl"))),
        ),
        (
            "supervisor_log_sha256",
            json!(sha256_of(&run.dir.join("supervisor.jsonl"))),
        ),
    ] {
        assert_eq!(summary[field], expected, "{field}: {context}");
    }
    assert!(!run.dir.join("summary.json.partial").exists(), "{context}");
}

/// The endpoint's side: the credential, the prompts, the actor's state.
fn check_endpoint(run: &Run, blocking: &str, context: &str) {
    assert_eq!(run.answered.len(), 3, "{context}");
    for answered in &run.answered {
        assert!(
            answered
                .request
                .contains(&format!("Authorization: Bearer {KEY_SENTINEL}\r\n")),
            "{context}"
        );
        assert!(!answered.request.contains("second-key"), "{context}");
    }
    let first_judge = prompt_of(&run.answered[0]);
    assert!(
        first_judge.contains("<tool_use name=\"Bash\">"),
        "{first_judge}"
    );
    assert!(first_judge.contains("git commit -am done"), "{first_judge}");
    assert!(!first_judge.contains("already said"), "{first_judge}");
    assert!(!first_judge.contains("PROMPT_SENTINEL"), "{first_judge}");
    assert!(first_judge.contains(TASK), "{first_judge}");
    let writer = prompt_of(&run.answered[1]);
    assert!(
        writer.contains("# What you have already said to them"),
        "{writer}"
    );
    let second_judge = prompt_of(&run.answered[2]);
    assert!(
        second_judge.contains("Running the tests now."),
        "{second_judge}"
    );
    assert!(!second_judge.contains(CORRECTION), "{second_judge}");
    assert!(!second_judge.contains("already said"), "{second_judge}");
    let states = &run.answered[0].actor_states;
    assert!(!states.is_empty(), "no actor process found while judging");
    if blocking == "sigstop" {
        assert!(states.iter().all(|s| *s == 'T'), "not stopped: {states:?}");
    } else {
        assert!(states.iter().all(|s| *s != 'T'), "stopped: {states:?}");
    }
}

fn check(run: &Run, blocking: &str) {
    let context = format!(
        "[{blocking}] wrapper stderr:\n{}\nactor stderr:\n{}\nsupervisor log:\n{}\nsummary:\n{}",
        run.wrapper_stderr, run.actor_stderr, run.supervisor_log, run.summary
    );
    assert_eq!(run.status, Some(0), "{context}");
    check_actor(run, &context);
    check_account(run, &context);
    check_summary(run, &context);
    check_endpoint(run, blocking, &context);
    // The credential reached no artifact and no stream.
    for text in [
        &run.wrapper_stderr,
        &run.actor_stderr,
        &run.event_log,
        &run.supervisor_log,
        &run.summary,
    ] {
        assert!(!text.contains(KEY_SENTINEL), "{context}");
    }
}

fn supervise_and_check(blocking: &str) {
    let run = supervise(blocking);
    check(&run, blocking);
    fs::remove_dir_all(&run.dir).unwrap();
}

#[test]
fn a_run_is_supervised_end_to_end() {
    supervise_and_check("off");
}

#[test]
fn a_run_is_supervised_end_to_end_while_gating_the_actor_stdout() {
    supervise_and_check("stdout");
}

#[test]
fn a_run_is_supervised_end_to_end_while_stopping_the_actor() {
    supervise_and_check("sigstop");
}

const SILENT: &str = r#"{"off_track": false, "self_correcting": false, "reason": "fine"}"#;

/// An actor that writes a boundary line and the line after it in one
/// `write`, then its result, and waits on stdin.
const TWO_LINES_AT_ONCE_ACTOR: &str = r#"
( sleep 30; kill -TERM $$ ) >/dev/null 2>&1 </dev/null &
read -r prompt || exit 90
printf '%s\n%s\n' '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"FIRST_OF_TWO"}]}}' '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"SECOND_OF_TWO"}]}}'
printf '%s\n' '{"type":"result","subtype":"success","is_error":false}'
cat >/dev/null
exit 0
"#;

/// Under `sigstop`, the judge is started only once the actor is confirmed
/// stopped and its stdout is drained to a barrier, so a boundary line and
/// the line the actor wrote in the same breath are both in the snapshot.
/// The control: the snapshot taken on consuming the first line, as before,
/// never holds the second — that ordering fails this test every time.
/// (`dash` writes one `printf` in one `write`, checked with `strace`.)
#[test]
fn under_sigstop_the_snapshot_holds_everything_written_before_the_stop() {
    let run = supervise_scenario(&Scenario {
        name: "barrier",
        blocking: "sigstop",
        actor: TWO_LINES_AT_ONCE_ACTOR,
        judge_every_n_assistant_messages: 1,
        answers: vec![SILENT.to_string()],
        answer_delay: Duration::ZERO,
        stdout_cap: 1_048_576,
        cancel_after: None,
        hold_before_summary: None,
    });
    let context = format!(
        "wrapper stderr:\n{}\nsupervisor log:\n{}\nsummary:\n{}",
        run.wrapper_stderr, run.supervisor_log, run.summary
    );
    assert_eq!(run.status, Some(0), "{context}");
    assert_eq!(run.answered.len(), 1, "{context}");
    let judge = prompt_of(&run.answered[0]);
    assert!(judge.contains("FIRST_OF_TWO"), "{judge}");
    assert!(judge.contains("SECOND_OF_TWO"), "{judge}");
    let states = &run.answered[0].actor_states;
    assert!(
        !states.is_empty() && states.iter().all(|s| *s == 'T'),
        "not stopped while judged: {states:?}"
    );
    let summary: Value = serde_json::from_str(&run.summary).unwrap();
    assert_eq!(summary["boundaries"], 1, "{context}");
    assert_eq!(summary["silent"], 1, "{context}");
    assert_eq!(summary["accounted_for"], true, "{context}");
    let rows = rows_of(&run.supervisor_log);
    let judged: Vec<&Value> = rows
        .iter()
        .filter(|r| r.get("boundary").is_some())
        .collect();
    assert_eq!(judged.len(), 1, "{context}");
    // The boundary's row is about the event it fell at; its snapshot holds
    // both lines — and the result too, when the actor got it out before
    // the stop landed.
    assert_eq!(judged[0]["cursor"], 1, "{context}");
    assert!(
        judged[0]["snapshot_cursor"].as_u64().unwrap() >= 2,
        "{context}"
    );
    // Every event is on record, the folded second line as observed and
    // as folded into the boundary rather than one of its own.
    let events = summary["events"].as_u64().unwrap();
    let mut cursors: Vec<u64> = rows.iter().filter_map(|r| r["cursor"].as_u64()).collect();
    cursors.sort_unstable();
    assert_eq!(cursors, (1..=events).collect::<Vec<u64>>(), "{context}");
    let folded = rows.iter().find(|r| r["cursor"] == 2).unwrap();
    assert_eq!(folded["kind"], "observed", "{context}");
    assert_eq!(folded["folded_into"], 1, "{context}");
    fs::remove_dir_all(&run.dir).unwrap();
}

/// An actor whose three boundary lines come one every 300 ms while the
/// judge takes a second over each answer.
const THREE_BOUNDARIES_ACTOR: &str = r#"
( sleep 30; kill -TERM $$ ) >/dev/null 2>&1 </dev/null &
read -r prompt || exit 90
printf '%s\n' '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"ONE"}]}}'
sleep 0.3
printf '%s\n' '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"TWO"}]}}'
sleep 0.3
printf '%s\n' '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"THREE"}]}}'
printf '%s\n' '{"type":"result","subtype":"success","is_error":false}'
cat >/dev/null
exit 0
"#;

/// A boundary that falls during a judgment keeps the ordinal it is given
/// and is judged, on the snapshot then current, once that judgment is in;
/// a third boundary before then supersedes it, on record. Three boundaries
/// fell, so the summary counts three — not the four that allocating anew
/// on completion used to count.
#[test]
fn a_boundary_during_a_judgment_keeps_its_ordinal_and_is_judged_after_it() {
    let run = supervise_scenario(&Scenario {
        name: "pending",
        blocking: "off",
        actor: THREE_BOUNDARIES_ACTOR,
        judge_every_n_assistant_messages: 1,
        answers: vec![SILENT.to_string(), SILENT.to_string()],
        answer_delay: Duration::from_secs(1),
        stdout_cap: 1_048_576,
        cancel_after: None,
        hold_before_summary: None,
    });
    let context = format!(
        "wrapper stderr:\n{}\nsupervisor log:\n{}\nsummary:\n{}",
        run.wrapper_stderr, run.supervisor_log, run.summary
    );
    assert_eq!(run.status, Some(0), "{context}");
    assert_eq!(run.answered.len(), 2, "{context}");
    let first = prompt_of(&run.answered[0]);
    assert!(first.contains("ONE") && !first.contains("TWO"), "{first}");
    let second = prompt_of(&run.answered[1]);
    assert!(
        second.contains("ONE") && second.contains("TWO") && second.contains("THREE"),
        "{second}"
    );
    let summary: Value = serde_json::from_str(&run.summary).unwrap();
    assert_eq!(summary["boundaries"], 3, "{context}");
    assert_eq!(summary["silent"], 2, "{context}");
    assert_eq!(summary["unjudged"], 1, "{context}");
    assert_eq!(summary["accounted_for"], true, "{context}");
    let rows = rows_of(&run.supervisor_log);
    // Rows are in the order they were written: the superseded boundary's
    // before the first judgment's, which was still in flight then.
    let mut boundaries: Vec<(u64, &str)> = rows
        .iter()
        .filter_map(|r| Some((r.get("boundary")?.as_u64()?, r["kind"].as_str()?)))
        .collect();
    boundaries.sort_unstable();
    assert_eq!(
        boundaries,
        vec![(1, "silent"), (2, "unjudged"), (3, "silent")],
        "{context}"
    );
    let superseded = rows
        .iter()
        .find(|r| r.get("boundary") == Some(&json!(2)))
        .unwrap();
    assert!(
        superseded["reason"]
            .as_str()
            .unwrap()
            .contains("superseded"),
        "{context}"
    );
    // Each boundary's row is about the event it fell at; the third's
    // snapshot was taken after the result, once the first judgment was in.
    assert_eq!(superseded["cursor"], 2, "{context}");
    let third = rows
        .iter()
        .find(|r| r.get("boundary") == Some(&json!(3)))
        .unwrap();
    assert_eq!(third["cursor"], 3, "{context}");
    assert_eq!(third["snapshot_cursor"], 4, "{context}");
    fs::remove_dir_all(&run.dir).unwrap();
}

/// An actor whose descendant leaves the process group (`setsid`) and keeps
/// the actor's stdout: the descendant writes a line after a delay, then
/// the result; the leader gives it time to leave the group, then writes
/// the boundary line and waits on stdin. Run as `<script> child`, the
/// script is the descendant.
const ESCAPED_WRITER_ACTOR: &str = r#"
case "$1" in
  child)
    sleep 2.5
    printf '%s\n' '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"ESCAPED_LINE"}]}}'
    printf '%s\n' '{"type":"result","subtype":"success","is_error":false}'
    exit 0
    ;;
esac
( sleep 30; kill -TERM $$ ) >/dev/null 2>&1 </dev/null &
read -r prompt || exit 90
setsid /bin/sh "$0" child &
sleep 0.2
printf '%s\n' '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"BOUNDARY_LINE"}]}}'
cat >/dev/null
exit 0
"#;

/// Under `sigstop`, a marked descendant that left the process group is
/// stopped and confirmed with the group: it cannot write behind the
/// barrier while the judge runs. The endpoint samples every process
/// running the script — the leader, its watchdog subshell, the escaped
/// child — and finds all of them in `T` while it answers. The control:
/// with the group alone stopped, the child sleeps on in `S` for longer
/// than the sampler waits for a settled picture, and the sample carries
/// its `S`.
#[test]
fn under_sigstop_a_descendant_that_left_the_group_is_stopped_too() {
    let run = supervise_scenario(&Scenario {
        name: "escaped",
        blocking: "sigstop",
        actor: ESCAPED_WRITER_ACTOR,
        judge_every_n_assistant_messages: 1,
        answers: vec![SILENT.to_string(), SILENT.to_string()],
        answer_delay: Duration::from_millis(500),
        stdout_cap: 1_048_576,
        cancel_after: None,
        hold_before_summary: None,
    });
    let context = format!(
        "wrapper stderr:\n{}\nsupervisor log:\n{}\nsummary:\n{}",
        run.wrapper_stderr, run.supervisor_log, run.summary
    );
    assert_eq!(run.status, Some(0), "{context}");
    assert!(!run.answered.is_empty(), "{context}");
    let first = prompt_of(&run.answered[0]);
    assert!(
        first.contains("BOUNDARY_LINE") && !first.contains("ESCAPED_LINE"),
        "{first}"
    );
    let states = &run.answered[0].actor_states;
    assert!(
        states.len() >= 3 && states.iter().all(|s| *s == 'T'),
        "not every process of the actor's was stopped: {states:?}"
    );
    let summary: Value = serde_json::from_str(&run.summary).unwrap();
    assert_eq!(summary["accounted_for"], true, "{context}");
    fs::remove_dir_all(&run.dir).unwrap();
}

/// One assistant line, the result, and a wait on stdin.
const ONE_LINE_ACTOR: &str = r#"
( sleep 30; kill -TERM $$ ) >/dev/null 2>&1 </dev/null &
read -r prompt || exit 90
printf '%s\n' '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"ONE"}]}}'
printf '%s\n' '{"type":"result","subtype":"success","is_error":false}'
cat >/dev/null
exit 0
"#;

/// A judge is not asked when the supervisor log could not keep the
/// record of the call: the endpoint sees no request, and the run ends as
/// a fault that says so. The control is the same run under a cap with
/// room, which asks.
#[test]
fn a_judge_is_not_asked_when_its_record_could_not_be_kept() {
    let run = supervise_scenario(&Scenario {
        name: "no-room",
        blocking: "off",
        actor: ONE_LINE_ACTOR,
        judge_every_n_assistant_messages: 1,
        answers: vec![SILENT.to_string()],
        answer_delay: Duration::ZERO,
        stdout_cap: 1024,
        cancel_after: None,
        hold_before_summary: None,
    });
    let context = format!(
        "wrapper stderr:\n{}\nsupervisor log:\n{}\nsummary:\n{}",
        run.wrapper_stderr, run.supervisor_log, run.summary
    );
    assert_eq!(run.status, Some(1), "{context}");
    assert!(run.answered.is_empty(), "{context}");
    let summary: Value = serde_json::from_str(&run.summary).unwrap();
    assert_eq!(summary["accounted_for"], false, "{context}");
    assert!(
        summary["unclean_reason"]
            .as_str()
            .unwrap()
            .contains("no room left in the supervisor log"),
        "{context}"
    );
    assert_eq!(summary["boundaries"], 1, "{context}");
    assert_eq!(summary["unjudged"], 1, "{context}");
    fs::remove_dir_all(&run.dir).unwrap();

    let run = supervise_scenario(&Scenario {
        name: "room",
        blocking: "off",
        actor: ONE_LINE_ACTOR,
        judge_every_n_assistant_messages: 1,
        answers: vec![SILENT.to_string()],
        answer_delay: Duration::ZERO,
        stdout_cap: 65_536,
        cancel_after: None,
        hold_before_summary: None,
    });
    assert_eq!(run.status, Some(0), "{}", run.wrapper_stderr);
    assert_eq!(run.answered.len(), 1);
    fs::remove_dir_all(&run.dir).unwrap();
}

/// A boundary's row that would cross the log's cap is written without the
/// raw answers, each call saying so, rather than dropped whole: the call
/// stays on record, and the run stays accounted for.
#[test]
fn a_boundary_row_that_would_cross_the_cap_keeps_the_call_and_drops_the_raw_answer() {
    let long_reason =
        json!({"off_track": false, "self_correcting": false, "reason": "x".repeat(20_000)});
    let run = supervise_scenario(&Scenario {
        name: "reduced",
        blocking: "off",
        actor: ONE_LINE_ACTOR,
        judge_every_n_assistant_messages: 1,
        answers: vec![long_reason.to_string()],
        answer_delay: Duration::ZERO,
        stdout_cap: 17_408,
        cancel_after: None,
        hold_before_summary: None,
    });
    let context = format!(
        "wrapper stderr:\n{}\nsupervisor log:\n{}\nsummary:\n{}",
        run.wrapper_stderr, run.supervisor_log, run.summary
    );
    assert_eq!(run.status, Some(0), "{context}");
    assert_eq!(run.answered.len(), 1, "{context}");
    let rows = rows_of(&run.supervisor_log);
    let judged = rows.iter().find(|r| r.get("boundary").is_some()).unwrap();
    assert_eq!(judged["kind"], "silent", "{context}");
    let call = &judged["calls"][0];
    assert_eq!(call["purpose"], "judge", "{context}");
    assert_eq!(call["finish_reason"], "stop", "{context}");
    assert!(call["raw"].is_null(), "{context}");
    assert!(
        call["raw_omitted"].as_str().unwrap().contains("not kept"),
        "{context}"
    );
    let summary: Value = serde_json::from_str(&run.summary).unwrap();
    assert_eq!(summary["accounted_for"], true, "{context}");
    fs::remove_dir_all(&run.dir).unwrap();
}

/// One assistant line, then a wait on stdin that ignores `SIGTERM`: the
/// wrapper's teardown spends its whole grace on this actor.
const TERM_IGNORING_ACTOR: &str = r#"
trap '' TERM
( sleep 30; kill -KILL $$ ) >/dev/null 2>&1 </dev/null &
read -r prompt || exit 90
printf '%s\n' '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"ONE"}]}}'
cat >/dev/null
exit 0
"#;

/// A cancellation during a judgment: the judge's call in progress returns
/// as cancelled, the writer is never asked, and the boundary's row carries
/// the judge's call before the summary is written. The control is the old
/// ordering, in which the judge's answer a second later started the
/// writer while the actor's teardown spent its grace — the endpoint saw a
/// second request, and the boundary's row had no call at all.
#[test]
fn a_cancellation_during_a_judgment_asks_the_writer_nothing_and_keeps_the_call_on_record() {
    let run = supervise_scenario(&Scenario {
        name: "cancelled",
        blocking: "off",
        actor: TERM_IGNORING_ACTOR,
        judge_every_n_assistant_messages: 1,
        answers: vec![
            json!({"off_track": true, "self_correcting": false, "reason": MARKER}).to_string(),
            CORRECTION.to_string(),
        ],
        answer_delay: Duration::from_secs(1),
        stdout_cap: 1_048_576,
        cancel_after: Some(Duration::from_millis(400)),
        hold_before_summary: None,
    });
    let context = format!(
        "wrapper stderr:\n{}\nsupervisor log:\n{}\nsummary:\n{}",
        run.wrapper_stderr, run.supervisor_log, run.summary
    );
    assert_eq!(run.status, Some(143), "{context}");
    let summary: Value = serde_json::from_str(&run.summary).unwrap();
    assert_eq!(summary["supervisor_exit"], "terminated", "{context}");
    assert_eq!(run.answered.len(), 1, "{context}");
    assert!(
        !run.answered[0]
            .request
            .contains("What you have already said"),
        "{context}"
    );
    let rows = rows_of(&run.supervisor_log);
    let judged = rows.iter().find(|r| r.get("boundary").is_some()).unwrap();
    assert_eq!(judged["kind"], "unjudged", "{context}");
    assert!(
        judged["reason"].as_str().unwrap().contains("cancelled"),
        "{context}"
    );
    assert_eq!(judged["calls"][0]["purpose"], "judge", "{context}");
    assert!(
        judged["calls"][0]["error"]
            .as_str()
            .unwrap()
            .contains("cancelled"),
        "{context}"
    );
    fs::remove_dir_all(&run.dir).unwrap();
}

/// One assistant line, then lines without end: enough to fill the event
/// queue many times over once nobody reads it.
const FLOODING_ACTOR: &str = r#"
( sleep 30; kill -KILL $$ ) >/dev/null 2>&1 </dev/null &
read -r prompt || exit 90
printf '%s\n' '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"ONE"}]}}'
while :; do printf '%s\n' '{"type":"system","subtype":"tick"}'; done
"#;

/// A cancellation while the actor floods its stdout and a judge is in
/// flight: the loop has stopped taking events, the queue is full, and the
/// judge's word must not wait behind it — the run ends, the actor's tree
/// with it, the judge's call on record. Against the shared queue this was
/// the deadlock: the judge blocked on its send, the join on the judge, and
/// the wrapper never reached the actor's teardown (the harness's watchdog
/// kills it after a minute, exit 137).
#[test]
fn a_cancellation_under_a_full_event_queue_still_ends_the_run() {
    let run = supervise_scenario(&Scenario {
        name: "cancelled-flood",
        blocking: "off",
        actor: FLOODING_ACTOR,
        judge_every_n_assistant_messages: 1,
        answers: vec![SILENT.to_string()],
        answer_delay: Duration::from_secs(1),
        stdout_cap: 64 << 20,
        cancel_after: Some(Duration::from_millis(400)),
        hold_before_summary: None,
    });
    let context = format!(
        "wrapper stderr:\n{}\nsummary:\n{}",
        run.wrapper_stderr, run.summary
    );
    assert_eq!(run.status, Some(143), "{context}");
    let summary: Value = serde_json::from_str(&run.summary).unwrap();
    assert_eq!(summary["supervisor_exit"], "terminated", "{context}");
    let rows = rows_of(&run.supervisor_log);
    assert!(
        rows.iter().filter(|r| r["kind"] == "observed").count() > 16,
        "{context}"
    );
    let judged = rows.iter().find(|r| r.get("boundary").is_some()).unwrap();
    assert_eq!(judged["kind"], "unjudged", "{context}");
    assert_eq!(judged["calls"][0]["purpose"], "judge", "{context}");
    assert!(
        judged["calls"][0]["error"]
            .as_str()
            .unwrap()
            .contains("cancelled"),
        "{context}"
    );
    assert!(
        actor_states(&run.dir.join("actor.sh")).is_empty(),
        "{context}"
    );
    fs::remove_dir_all(&run.dir).unwrap();
}

/// A stop raised after the run's fate was decided — the actor ended on
/// its own, and the signal lands while the wrapper is between the actor's
/// teardown and the summary — changes neither: the summary says `clean`
/// and the exit status is 0. Before the decision was latched the summary
/// said `clean` and the wrapper exited 143, and a reader trusting the
/// summary took a cancelled invocation for a clean one.
#[test]
fn a_stop_after_the_run_ended_changes_neither_the_summary_nor_the_exit_status() {
    let run = supervise_scenario(&Scenario {
        name: "late-stop",
        blocking: "off",
        actor: ONE_LINE_ACTOR,
        judge_every_n_assistant_messages: 1,
        answers: vec![SILENT.to_string()],
        answer_delay: Duration::ZERO,
        stdout_cap: 1_048_576,
        cancel_after: Some(Duration::from_millis(1500)),
        hold_before_summary: Some(Duration::from_secs(3)),
    });
    let context = format!(
        "wrapper stderr:\n{}\nsummary:\n{}",
        run.wrapper_stderr, run.summary
    );
    let summary: Value = serde_json::from_str(&run.summary).unwrap();
    assert_eq!(summary["supervisor_exit"], "clean", "{context}");
    assert_eq!(summary["accounted_for"], true, "{context}");
    assert_eq!(run.status, Some(0), "{context}");
    fs::remove_dir_all(&run.dir).unwrap();
}

/// One assistant line, the correction read back and echoed, a result.
const CORRECTED_ACTOR: &str = r#"
( sleep 30; kill -TERM $$ ) >/dev/null 2>&1 </dev/null &
read -r prompt || exit 90
printf '%s\n' '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"Done; I will not run the tests."}]}}'
read -r note || exit 91
printf '%s\n' "$note"
printf '%s\n' '{"type":"result","subtype":"success","is_error":false}'
cat >/dev/null
exit 0
"#;

/// A judge's reason too long for the log's room, with a correction to
/// deliver: the row is cut to fit — the reason bounded, the raw answers
/// gone — before the correction is written, and the run is accounted for.
/// Before the row was committed to first, the correction was written, the
/// cut row still carried the whole reason, and it was dropped: an
/// intervention with no record, and a fault.
#[test]
fn a_correction_is_delivered_only_once_its_row_is_committed_to() {
    let run = supervise_scenario(&Scenario {
        name: "long-marker",
        blocking: "off",
        actor: CORRECTED_ACTOR,
        judge_every_n_assistant_messages: 1,
        answers: vec![
            json!({"off_track": true, "self_correcting": false, "reason": "m".repeat(20_000)})
                .to_string(),
            CORRECTION.to_string(),
        ],
        answer_delay: Duration::ZERO,
        stdout_cap: 17_408,
        cancel_after: None,
        hold_before_summary: None,
    });
    let context = format!(
        "wrapper stderr:\n{}\nsupervisor log:\n{}\nsummary:\n{}",
        run.wrapper_stderr, run.supervisor_log, run.summary
    );
    assert_eq!(run.status, Some(0), "{context}");
    let rows = rows_of(&run.supervisor_log);
    let correction = rows
        .iter()
        .find(|r| r["kind"] == "correction")
        .expect(&context);
    assert_eq!(correction["text"], CORRECTION, "{context}");
    let marker = correction["marker"].as_str().unwrap();
    assert!(
        marker.chars().count() < 300 && marker.ends_with('…'),
        "{context}"
    );
    assert_eq!(
        correction["calls"].as_array().unwrap().len(),
        2,
        "{context}"
    );
    assert!(correction["calls"][0]["raw"].is_null(), "{context}");
    assert!(
        correction["calls"][0]["raw_omitted"].is_string(),
        "{context}"
    );
    let spoke = rows.iter().find(|r| r["kind"] == "spoke").expect(&context);
    assert_eq!(spoke["boundary"], correction["boundary"], "{context}");
    assert!(run.event_log.contains(CORRECTION), "{context}");
    let summary: Value = serde_json::from_str(&run.summary).unwrap();
    assert_eq!(summary["corrections"], 1, "{context}");
    assert_eq!(summary["accounted_for"], true, "{context}");
    fs::remove_dir_all(&run.dir).unwrap();
}

/// One assistant line, then sixty system lines while the judge is out.
const CHATTY_DURING_JUDGMENT_ACTOR: &str = r#"
( sleep 30; kill -TERM $$ ) >/dev/null 2>&1 </dev/null &
read -r prompt || exit 90
printf '%s\n' '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"ONE"}]}}'
i=0; while [ $i -lt 60 ]; do printf '%s\n' '{"type":"system","subtype":"tick"}'; i=$((i+1)); done
cat >/dev/null
exit 0
"#;

/// Events that arrive while a judge is out cannot take the room held for
/// its row: at a cap with room for little else, the run faults on the
/// events, and the judgment's row — its call with it — is still written.
/// Before the room was held, the events took it, and the row was dropped:
/// a call with no record.
#[test]
fn events_during_a_judgment_cannot_take_the_room_held_for_its_record() {
    let run = supervise_scenario(&Scenario {
        name: "reserved-room",
        blocking: "off",
        actor: CHATTY_DURING_JUDGMENT_ACTOR,
        judge_every_n_assistant_messages: 1,
        answers: vec![
            json!({"off_track": true, "self_correcting": false, "reason": "m".repeat(13_000)})
                .to_string(),
            CORRECTION.to_string(),
        ],
        answer_delay: Duration::from_secs(1),
        stdout_cap: 17_408,
        cancel_after: None,
        hold_before_summary: None,
    });
    let context = format!(
        "wrapper stderr:\n{}\nsupervisor log:\n{}\nsummary:\n{}",
        run.wrapper_stderr, run.supervisor_log, run.summary
    );
    assert_eq!(run.status, Some(1), "{context}");
    let summary: Value = serde_json::from_str(&run.summary).unwrap();
    assert!(
        summary["unclean_reason"]
            .as_str()
            .unwrap()
            .contains("held for the judgment"),
        "{context}"
    );
    let rows = rows_of(&run.supervisor_log);
    let judged = rows
        .iter()
        .find(|r| r.get("boundary").is_some())
        .expect(&context);
    assert_eq!(judged["calls"][0]["purpose"], "judge", "{context}");
    fs::remove_dir_all(&run.dir).unwrap();
}
