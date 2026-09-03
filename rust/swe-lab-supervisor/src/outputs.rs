//! The one door every file the wrapper writes goes through.
//!
//! Two of the wrapper's outputs on one file — the same path twice, a hard
//! link, a symlink to another — would let two writers overwrite each other
//! while both report success, and the artifact would be a faithful record
//! of neither. So an output is opened here and nowhere else (`clippy.toml`
//! disallows `File::create` outside this module, and the gate treats the
//! lint as an error), and a path naming an inode already opened is refused
//! before anything is written. An output added later gets the check by
//! construction, not by someone remembering it.

use std::fs::File;
use std::io;
use std::os::unix::fs::MetadataExt;
use std::path::{Path, PathBuf};

/// One of the wrapper's outputs: the path, kept for the record, and the
/// file created at it.
#[derive(Debug)]
pub struct Output {
    pub path: PathBuf,
    pub file: File,
}

/// The outputs opened so far, by device and inode.
#[derive(Debug, Default)]
pub struct Outputs {
    opened: Vec<(u64, u64)>,
}

impl Outputs {
    /// Create (or truncate) the file at `path` as one of the wrapper's
    /// outputs.
    ///
    /// # Errors
    ///
    /// The file cannot be created, or it is one already opened here — by
    /// identity, not by name — which is refused as `InvalidInput`. Neither
    /// error repeats the path.
    #[expect(
        clippy::disallowed_methods,
        reason = "this is the door the lint points everything else to"
    )]
    pub fn create(&mut self, path: &Path) -> io::Result<Output> {
        let file = File::create(path)?;
        let metadata = file.metadata()?;
        let identity = (metadata.dev(), metadata.ino());
        if self.opened.contains(&identity) {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "two of the wrapper's outputs are one file",
            ));
        }
        self.opened.push(identity);
        Ok(Output {
            path: path.to_path_buf(),
            file,
        })
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
    fn two_files_are_two_outputs() {
        let dir = scratch("two");
        let mut outputs = Outputs::default();
        outputs.create(&dir.join("a")).unwrap();
        outputs.create(&dir.join("b")).unwrap();
    }

    #[test]
    fn one_path_twice_is_refused_the_second_time() {
        let dir = scratch("twice");
        let mut outputs = Outputs::default();
        outputs.create(&dir.join("a")).unwrap();
        let error = outputs.create(&dir.join("a")).expect_err("one file twice");
        assert_eq!(error.kind(), io::ErrorKind::InvalidInput, "{error}");
    }

    #[test]
    fn a_link_to_a_file_already_opened_is_refused_whatever_its_name() {
        let dir = scratch("link");
        fs::write(dir.join("a"), "").unwrap();
        fs::hard_link(dir.join("a"), dir.join("hard")).unwrap();
        std::os::unix::fs::symlink(dir.join("a"), dir.join("soft")).unwrap();
        let mut outputs = Outputs::default();
        outputs.create(&dir.join("a")).unwrap();
        for name in ["hard", "soft"] {
            let error = outputs.create(&dir.join(name)).expect_err(name);
            assert_eq!(error.kind(), io::ErrorKind::InvalidInput, "{name}: {error}");
            assert!(!error.to_string().contains(name), "{error}");
        }
    }
}
