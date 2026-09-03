//! The one door every file the wrapper writes goes through.
//!
//! Two of the wrapper's outputs on one file — the same path twice, a hard
//! link, a symlink to another — would let two writers overwrite each other
//! while both report success, and the artifact would be a faithful record
//! of neither. So an output is opened here and nowhere else (`clippy.toml`
//! disallows every way of creating or moving a file outside this module,
//! and the gate treats the lint as an error — the list is an enumeration,
//! and says so), and a path naming an inode already opened is refused
//! before anything is written or truncated. An output added later gets the
//! check by construction, not by someone remembering it.
//!
//! Two kinds of output. A **log** is opened at the start, without
//! truncating what is there, and truncated only once every output has
//! passed — a refusal changes nothing on disk. The **summary** is written
//! once, at the end, through a staging file and an atomic rename; its name
//! and the staging name are *reserved* at the start, checked against the
//! logs the same way, but nothing is created at them until then: an absent
//! summary is the artifact's own word that the run is unfinished.

use std::fs::{File, OpenOptions};
use std::io;
use std::os::unix::fs::MetadataExt;
use std::path::{Path, PathBuf};

/// One of the wrapper's outputs: the path, kept for the record, and the
/// file open at it.
#[derive(Debug)]
pub struct Output {
    pub path: PathBuf,
    pub file: File,
}

/// A path as this module tells one from another: where it resolves to,
/// and — once something exists there — what.
#[derive(Debug, Clone, PartialEq, Eq)]
struct Identity {
    /// The resolved path: symlinks followed as far as they exist.
    resolved: PathBuf,
    /// Device and inode, when something exists at the path.
    inode: Option<(u64, u64)>,
}

impl Identity {
    fn of(path: &Path) -> io::Result<Self> {
        let metadata = match std::fs::metadata(path) {
            Ok(metadata) => Some(metadata),
            Err(error) if error.kind() == io::ErrorKind::NotFound => None,
            Err(error) => return Err(error),
        };
        let resolved = if metadata.is_some() {
            path.canonicalize()?
        } else {
            let name = path
                .file_name()
                .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidInput, "no file name"))?;
            path.parent()
                .filter(|parent| !parent.as_os_str().is_empty())
                .unwrap_or_else(|| Path::new("."))
                .canonicalize()?
                .join(name)
        };
        Ok(Self {
            resolved,
            inode: metadata.map(|m| (m.dev(), m.ino())),
        })
    }

    fn collides_with(&self, other: &Self) -> bool {
        self.resolved == other.resolved
            || matches!((self.inode, other.inode), (Some(a), Some(b)) if a == b)
    }
}

fn one_file() -> io::Error {
    io::Error::new(
        io::ErrorKind::InvalidInput,
        "two of the wrapper's outputs are one file",
    )
}

/// The outputs opened or reserved so far.
#[derive(Debug, Default)]
pub struct Outputs {
    opened: Vec<Identity>,
    reserved: Vec<Identity>,
}

impl Outputs {
    /// Open the log at `path` for writing, creating it if absent, without
    /// truncating it: what is there stays until [`Outputs::truncate`], so a
    /// refusal that comes later changes nothing.
    ///
    /// # Errors
    ///
    /// The file cannot be opened; it is not a regular file (a device or a
    /// pipe is not a record with a digest); or it is one already opened or
    /// reserved here — by identity, not by name — refused as `InvalidInput`.
    /// No error repeats the path.
    #[expect(
        clippy::disallowed_methods,
        reason = "this is the door the lint points everything else to"
    )]
    pub fn open(&mut self, path: &Path) -> io::Result<Output> {
        let file = OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(false)
            .open(path)?;
        let metadata = file.metadata()?;
        if !metadata.is_file() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "an output must be a regular file",
            ));
        }
        let identity = Identity::of(path)?;
        if self
            .opened
            .iter()
            .chain(&self.reserved)
            .any(|known| known.collides_with(&identity))
        {
            return Err(one_file());
        }
        self.opened.push(identity);
        Ok(Output {
            path: path.to_path_buf(),
            file,
        })
    }

    /// Reserve `path` for a file written at the end: nothing is created at
    /// it now, but the name is held against every log and reservation, so
    /// that writing it later replaces nothing the wrapper is writing.
    /// Reserving the same path twice is one reservation.
    ///
    /// # Errors
    ///
    /// The path cannot be resolved, or it is one already opened here.
    pub fn reserve(&mut self, path: &Path) -> io::Result<()> {
        let identity = Identity::of(path)?;
        if self.reserved.contains(&identity) {
            return Ok(());
        }
        if self
            .opened
            .iter()
            .chain(&self.reserved)
            .any(|known| known.collides_with(&identity))
        {
            return Err(one_file());
        }
        self.reserved.push(identity);
        Ok(())
    }

    /// Truncate the logs opened so far: every output has passed, and the
    /// run's record starts empty.
    ///
    /// # Errors
    ///
    /// A file could not be truncated.
    pub fn truncate(outputs: &[&Output]) -> io::Result<()> {
        for output in outputs {
            output.file.set_len(0)?;
        }
        Ok(())
    }

    /// Create the file at a reserved path — the one place a reserved name
    /// becomes a file — truncating whatever a previous run left there.
    ///
    /// # Errors
    ///
    /// The path was not reserved, or the file cannot be created.
    #[expect(
        clippy::disallowed_methods,
        reason = "the door's own create, for a name it reserved"
    )]
    pub fn create_reserved(&self, path: &Path) -> io::Result<Output> {
        let identity = Identity::of(path)?;
        if !self
            .reserved
            .iter()
            .any(|r| r.resolved == identity.resolved)
        {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "a file written at the end must have been reserved at the start",
            ));
        }
        Ok(Output {
            path: path.to_path_buf(),
            file: File::create(path)?,
        })
    }

    /// Move `staging` — created here, written and synced — onto `path`,
    /// atomically: the one way a file of the wrapper's is replaced. Both
    /// names were reserved, so nothing the wrapper writes is under either.
    ///
    /// # Errors
    ///
    /// The rename failed; the staging file is left where it was.
    #[expect(clippy::disallowed_methods, reason = "the door's own rename")]
    pub fn replace(staging: Output, path: &Path) -> io::Result<()> {
        drop(staging.file);
        std::fs::rename(&staging.path, path)
    }
}

#[cfg(test)]
mod tests {
    use std::fs;

    use super::*;

    fn scratch(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "swe-lab-supervisor-outputs-{name}-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn two_files_are_two_outputs_and_keep_their_bytes_until_truncated() {
        let dir = scratch("two");
        fs::write(dir.join("a"), "old a").unwrap();
        let mut outputs = Outputs::default();
        let a = outputs.open(&dir.join("a")).unwrap();
        let b = outputs.open(&dir.join("b")).unwrap();
        assert_eq!(fs::read_to_string(dir.join("a")).unwrap(), "old a");
        Outputs::truncate(&[&a, &b]).unwrap();
        assert_eq!(fs::read_to_string(dir.join("a")).unwrap(), "");
    }

    #[test]
    fn one_path_twice_is_refused_the_second_time_with_the_first_untouched() {
        let dir = scratch("twice");
        fs::write(dir.join("a"), "keep").unwrap();
        let mut outputs = Outputs::default();
        outputs.open(&dir.join("a")).unwrap();
        let error = outputs.open(&dir.join("a")).expect_err("one file twice");
        assert_eq!(error.kind(), io::ErrorKind::InvalidInput, "{error}");
        assert_eq!(fs::read_to_string(dir.join("a")).unwrap(), "keep");
    }

    #[test]
    fn a_link_to_a_file_already_opened_is_refused_whatever_its_name() {
        let dir = scratch("link");
        fs::write(dir.join("a"), "").unwrap();
        fs::hard_link(dir.join("a"), dir.join("hard")).unwrap();
        std::os::unix::fs::symlink(dir.join("a"), dir.join("soft")).unwrap();
        let mut outputs = Outputs::default();
        outputs.open(&dir.join("a")).unwrap();
        for name in ["hard", "soft"] {
            let error = outputs.open(&dir.join(name)).expect_err(name);
            assert_eq!(error.kind(), io::ErrorKind::InvalidInput, "{name}: {error}");
            assert!(!error.to_string().contains(name), "{error}");
        }
    }

    #[test]
    fn a_device_is_not_an_output() {
        let mut outputs = Outputs::default();
        let error = outputs.open(Path::new("/dev/null")).expect_err("a device");
        assert_eq!(error.kind(), io::ErrorKind::InvalidInput, "{error}");
        assert!(error.to_string().contains("regular file"), "{error}");
    }

    /// A reservation creates nothing, holds the name against the logs in
    /// both directions, and is the one way the name becomes a file later.
    #[test]
    fn a_reserved_name_exists_only_when_it_is_written_and_collides_with_no_log() {
        let dir = scratch("reserve");
        let mut outputs = Outputs::default();
        outputs.reserve(&dir.join("summary.json")).unwrap();
        outputs.reserve(&dir.join("summary.json")).unwrap();
        assert!(
            !dir.join("summary.json").exists(),
            "reserving created a file"
        );
        let error = outputs
            .open(&dir.join("summary.json"))
            .expect_err("a log at a reserved name");
        assert_eq!(error.kind(), io::ErrorKind::InvalidInput, "{error}");
        outputs.open(&dir.join("events.jsonl")).unwrap();
        let error = outputs
            .reserve(&dir.join("events.jsonl"))
            .expect_err("reserving a log's name");
        assert_eq!(error.kind(), io::ErrorKind::InvalidInput, "{error}");
        let error = outputs
            .create_reserved(&dir.join("never-reserved"))
            .expect_err("creating an unreserved name");
        assert_eq!(error.kind(), io::ErrorKind::InvalidInput, "{error}");
        let written = outputs.create_reserved(&dir.join("summary.json")).unwrap();
        assert!(dir.join("summary.json").exists());
        drop(written);
    }
}
