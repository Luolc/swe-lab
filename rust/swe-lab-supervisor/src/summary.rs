//! The terminal summary: what the Python side classifies the run from,
//! without trusting the exit code alone. Written atomically — to a
//! temporary name, then renamed — so a reader finds it whole or not at all.

use std::fs::File;
use std::io::{self, Read, Write};
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
    /// The event log's digest, as written.
    pub actor_event_log_sha256: Option<String>,
    /// The supervisor log's digest, as written.
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

/// Where the summary is staged before its atomic rename onto `path`.
#[must_use]
pub fn staging_path(path: &Path) -> PathBuf {
    path.with_extension("json.partial")
}

/// The sha256 of a file's contents, or `None` when it cannot be read.
#[must_use]
pub fn file_sha256(path: &Path) -> Option<String> {
    // Only a regular file is an artifact with a digest: a device or a pipe
    // is neither finite nor the run's record. Streamed, because the event
    // log may be as large as its cap.
    if !std::fs::metadata(path).ok()?.is_file() {
        return None;
    }
    let mut file = File::open(path).ok()?;
    let mut hasher = Sha256::new();
    let mut chunk = vec![0u8; 64 * 1024];
    loop {
        let read = file.read(&mut chunk).ok()?;
        if read == 0 {
            break;
        }
        hasher.update(&chunk[..read]);
    }
    Some(format!("{:x}", hasher.finalize()))
}

#[cfg(test)]
mod tests {
    use super::*;

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
        assert_eq!(file_sha256(&path).unwrap().len(), 64);
        assert_eq!(file_sha256(&dir.join("missing")), None);
    }
}
