//! The terminal summary: what the Python side classifies the run from,
//! without trusting the exit code alone. Written atomically — to a
//! temporary name, then renamed — so a reader finds it whole or not at all.

use std::fs::File;
use std::io::{self, Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};

use crate::outputs::Outputs;

use serde::Serialize;
use sha2::{Digest, Sha256};

/// The one schema version this binary writes.
pub const SCHEMA_VERSION: u32 = 1;

/// How the wrapper ended.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "kebab-case")]
pub enum SupervisorExit {
    /// The actor ended — by itself or on the deliberate close — and every
    /// wrapper task finished.
    Clean,
    /// The wrapper was told to stop (`SIGTERM`) and ended the actor's group.
    Terminated,
    /// The run was refused before the actor was launched.
    Refused,
    /// Something in the wrapper's own machinery failed; the reason is in
    /// `unclean_reason`.
    Unclean,
}

/// The summary itself.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Summary {
    /// [`SCHEMA_VERSION`].
    pub schema_version: u32,
    /// Whether every boundary of the run is accounted for: no gap, a clean
    /// ending, and at least one usable actor event consumed. A run that is
    /// not accounted for is not evidence about supervision.
    pub accounted_for: bool,
    /// How the wrapper ended.
    pub supervisor_exit: SupervisorExit,
    /// Why, when not clean.
    pub unclean_reason: Option<String>,
    /// What the actor exited with: its code, or `128 + signal` when it died
    /// of one — the normalization the report's reader documents and
    /// requires. Absent only when no actor ran.
    pub actor_exit_code: Option<i32>,
    /// The signal the actor died of, when it was signalled.
    pub actor_exit_signal: Option<i32>,
    /// Stream events consumed (decoded JSON objects).
    pub events: u64,
    /// Stdout lines that were not a JSON object; written to the event log,
    /// counted here, consumed by nobody.
    pub undecodable_lines: u64,
    /// Stdout lines over the framing ceiling; likewise.
    pub oversized_lines: u64,
    /// Judgment boundaries.
    pub boundaries: u64,
    /// Corrections delivered.
    pub corrections: u64,
    /// Boundaries judged and left in silence.
    pub silent: u64,
    /// Boundaries at which no decision was sought, with a reason.
    pub unjudged: u64,
    /// Boundaries lost to a bounded model-call failure.
    pub lapses: u64,
    /// Holes of unknown reach; any makes the run unaccounted for.
    pub gaps: u64,
    /// Verdicts newer evidence overtook: recorded, never delivered.
    pub stale_verdicts_discarded: u64,
    /// The longest a boundary waited for its decision.
    pub max_decision_lag_ms: u64,
    /// Marked descendants found outside the actor's process group when it
    /// was ended, and killed.
    pub stragglers_killed: u64,
    /// The model name every request was sent with.
    pub model: String,
    /// The pinned criterion's digest.
    pub criterion_sha256: String,
    /// The digest of the event log the wrapper wrote, read back through
    /// its own descriptor. Absent when it could not be read, which makes
    /// the run not accounted for.
    pub actor_event_log_sha256: Option<String>,
    /// The same for the supervisor log.
    pub supervisor_log_sha256: Option<String>,
}

impl Summary {
    /// A summary for a run refused before the actor was launched.
    #[must_use]
    pub fn refused(reason: &str, model: &str, criterion_sha256: &str) -> Self {
        Self {
            schema_version: SCHEMA_VERSION,
            accounted_for: false,
            supervisor_exit: SupervisorExit::Refused,
            unclean_reason: Some(reason.to_string()),
            actor_exit_code: None,
            actor_exit_signal: None,
            events: 0,
            undecodable_lines: 0,
            oversized_lines: 0,
            boundaries: 0,
            corrections: 0,
            silent: 0,
            unjudged: 0,
            lapses: 0,
            gaps: 0,
            stale_verdicts_discarded: 0,
            max_decision_lag_ms: 0,
            stragglers_killed: 0,
            model: model.to_string(),
            criterion_sha256: criterion_sha256.to_string(),
            actor_event_log_sha256: None,
            supervisor_log_sha256: None,
        }
    }

    /// Write the summary atomically: whole at `path`, or absent. Both
    /// `path` and its staging name must have been reserved with `outputs`
    /// (see [`staging_path`]).
    ///
    /// # Errors
    ///
    /// The names were not reserved, or the staging file cannot be written
    /// or renamed into place.
    pub fn write(&self, outputs: &Outputs, path: &Path) -> io::Result<()> {
        let staging = outputs.create_reserved(&staging_path(path))?;
        let mut file = &staging.file;
        file.write_all(
            serde_json::to_string_pretty(self)
                .map_err(io::Error::other)?
                .as_bytes(),
        )?;
        file.write_all(b"\n")?;
        file.sync_all()?;
        Outputs::replace(staging, path)
    }
}

/// Where the summary is staged before its atomic rename onto `path`:
/// `.partial` appended to the name as given, so that no final name — one
/// already ending in `.partial` included — is its own staging name.
#[must_use]
pub fn staging_path(path: &Path) -> PathBuf {
    let mut staged = path.as_os_str().to_os_string();
    staged.push(".partial");
    PathBuf::from(staged)
}

/// The sha256 of what is in `file`, read back from its start through the
/// descriptor: the file the wrapper wrote, whatever is at its name by now.
/// Streamed, because the event log may be as large as its cap.
///
/// # Errors
///
/// The descriptor is not a regular file — a device or a pipe is neither
/// finite nor the run's record — or cannot be read.
pub fn digest(file: &mut File) -> io::Result<String> {
    if !file.metadata()?.is_file() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "not a regular file",
        ));
    }
    file.seek(SeekFrom::Start(0))?;
    let mut hasher = Sha256::new();
    let mut chunk = vec![0u8; 64 * 1024];
    loop {
        let read = file.read(&mut chunk)?;
        if read == 0 {
            break;
        }
        hasher.update(&chunk[..read]);
    }
    Ok(format!("{:x}", hasher.finalize()))
}

#[cfg(test)]
mod tests {
    use std::collections::BTreeSet;

    use super::*;

    /// The summaries committed for the Python reader's tests
    /// (`tests/fixtures/native_supervision/`, written by this binary via
    /// `scripts/summary-fixtures.sh`) carry exactly this struct's keys: a
    /// field added or renamed here fails this test until the fixtures are
    /// regenerated, and the Python test on the other side of them fails
    /// until its reader follows.
    #[test]
    fn the_committed_fixtures_carry_exactly_the_summary_s_keys() {
        let ours: BTreeSet<String> = serde_json::to_value(Summary::refused("r", "m", "c"))
            .unwrap()
            .as_object()
            .unwrap()
            .keys()
            .cloned()
            .collect();
        let fixtures =
            Path::new(env!("CARGO_MANIFEST_DIR")).join("../../tests/fixtures/native_supervision");
        for (arm, exit) in [
            ("clean-exit", "clean"),
            ("actor-signalled", "clean"),
            ("cancelled", "terminated"),
            ("unclean", "unclean"),
            ("refused", "refused"),
        ] {
            let text = std::fs::read_to_string(fixtures.join(format!("{arm}.json"))).unwrap();
            let fixture: serde_json::Value = serde_json::from_str(&text).unwrap();
            let keys: BTreeSet<String> = fixture.as_object().unwrap().keys().cloned().collect();
            assert_eq!(keys, ours, "{arm}");
            assert_eq!(fixture["schema_version"], SCHEMA_VERSION, "{arm}");
            assert_eq!(fixture["supervisor_exit"], exit, "{arm}");
        }
    }

    /// An `Outputs` with the summary's two names reserved, as the wrapper
    /// does at the start.
    fn reserved(path: &Path) -> Outputs {
        let mut outputs = Outputs::default();
        outputs.reserve(path).unwrap();
        outputs.reserve(&staging_path(path)).unwrap();
        outputs
    }

    #[test]
    fn a_summary_is_written_whole_under_its_final_name_only() {
        let dir = std::env::temp_dir().join(format!(
            "swe-lab-supervisor-summary-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("summary.json");
        Summary::refused("bad config", "m", "abc")
            .write(&reserved(&path), &path)
            .unwrap();
        let text = std::fs::read_to_string(&path).unwrap();
        let parsed: serde_json::Value = serde_json::from_str(&text).unwrap();
        assert_eq!(parsed["schema_version"], SCHEMA_VERSION);
        assert_eq!(parsed["accounted_for"], false);
        assert_eq!(parsed["supervisor_exit"], "refused");
        assert_eq!(parsed["unclean_reason"], "bad config");
        assert!(!dir.join("summary.json.partial").exists());
        let mut written = File::open(&path).unwrap();
        // Read back once already: the digest starts over from the top.
        written.read_to_end(&mut Vec::new()).unwrap();
        assert_eq!(
            digest(&mut written).unwrap(),
            format!("{:x}", Sha256::digest(text.as_bytes()))
        );
        assert!(digest(&mut File::open("/dev/null").unwrap()).is_err());
    }
}
