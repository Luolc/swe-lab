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
            write!(
                socket,
                "HTTP/1.1 {http_status}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                body.len()
            )
            .unwrap();
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
    })
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
                "max_actor_stdout_bytes": 1_048_576,
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
    let output = Command::new(env!("CARGO_BIN_EXE_swe-lab-supervisor"))
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
        .output()
        .unwrap();
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

/// The account: one boundary spoken, one silent, everything else observed.
fn check_account(run: &Run, context: &str) {
    let rows = rows_of(&run.supervisor_log);
    let kinds: Vec<&str> = rows.iter().map(|r| r["kind"].as_str().unwrap()).collect();
    assert_eq!(
        kinds,
        [
            "observed", "observed", "observed", "spoke", "observed", "observed", "silent"
        ],
        "{context}"
    );
    assert_eq!(rows[0]["evidence"], "excluded-external-text", "{context}");
    let spoke = &rows[3];
    assert_eq!(spoke["boundary"], 1, "{context}");
    assert_eq!(spoke["cursor"], 4, "{context}");
    assert_eq!(spoke["marker"], MARKER, "{context}");
    assert_eq!(spoke["text"], CORRECTION, "{context}");
    let purposes: Vec<&str> = spoke["calls"]
        .as_array()
        .unwrap()
        .iter()
        .map(|c| c["purpose"].as_str().unwrap())
        .collect();
    assert_eq!(purposes, ["judge", "writer"], "{context}");
    assert!(
        spoke.get("blocking").is_none(),
        "blocking failed: {context}"
    );
    assert_eq!(
        rows[4]["evidence"], "excluded-own-intervention",
        "{context}"
    );
    let silent = &rows[6];
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
    // Both lines were admitted before the snapshot — and the result too,
    // when the actor got it out before the stop landed.
    assert!(judged[0]["cursor"].as_u64().unwrap() >= 2, "{context}");
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
    fs::remove_dir_all(&run.dir).unwrap();
}
