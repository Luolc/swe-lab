//! The judge's criterion: compiled in, selected by name, verified by digest.
//!
//! The text is the same artifact the Python side loads
//! (`src/swe_lab/trace_synthesis/criteria/`), included at build time from the
//! same file, so the two runtimes cannot judge against different standards
//! without the digest saying so. A config names a criterion and pins its
//! sha256; the run is refused before the actor starts if this binary's copy
//! does not reproduce that digest.

use std::fmt::Write as _;

use sha2::{Digest, Sha256};

/// One criterion compiled into the binary.
#[derive(Debug)]
pub struct Embedded {
    /// The name a config selects it by.
    pub name: &'static str,
    /// The text, byte-identical to the committed artifact.
    pub text: &'static str,
}

impl Embedded {
    /// The lowercase hex sha256 of the text.
    #[must_use]
    pub fn sha256(&self) -> String {
        sha256_hex(self.text.as_bytes())
    }
}

/// Every criterion this binary can judge against.
pub const EMBEDDED: &[Embedded] = &[Embedded {
    name: "general-practice",
    text: include_str!("../../../src/swe_lab/trace_synthesis/criteria/general-practice.md"),
}];

/// A criterion that passed the pin: the standard the run judges against.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Selected {
    /// The name it was selected by.
    pub name: &'static str,
    /// The text the judge is handed.
    pub text: &'static str,
    /// The digest that was pinned and reproduced.
    pub digest: String,
}

/// Select an embedded criterion by name and require its digest.
///
/// # Errors
///
/// No embedded criterion has that name, or the embedded text's sha256 is not
/// the pinned one. Either refuses the run: a criterion that is not the
/// reviewed one leaves nothing to judge against.
pub fn select(name: &str, expected_sha256: &str) -> Result<Selected, String> {
    let Some(embedded) = EMBEDDED.iter().find(|e| e.name == name) else {
        let known: Vec<&str> = EMBEDDED.iter().map(|e| e.name).collect();
        return Err(format!(
            "no embedded criterion named {name:?}; this binary has {known:?}"
        ));
    };
    let digest = embedded.sha256();
    if digest != expected_sha256 {
        return Err(format!(
            "criterion {name:?} has sha256 {digest} in this binary, not the pinned {expected_sha256}"
        ));
    }
    Ok(Selected {
        name: embedded.name,
        text: embedded.text,
        digest,
    })
}

/// The lowercase hex sha256 of some bytes.
#[must_use]
pub fn sha256_hex(bytes: &[u8]) -> String {
    let mut hex = String::with_capacity(64);
    for byte in Sha256::digest(bytes) {
        // Writing into a `String` cannot fail.
        let _ = write!(hex, "{byte:02x}");
    }
    hex
}

#[cfg(test)]
mod tests {
    use super::*;

    /// `CRITERION_SHA256` in `src/swe_lab/trace_synthesis/criterion.py`. Both
    /// runtimes pin the same artifact; a change to the file moves both pins
    /// together, or one of them refuses.
    const PYTHON_PIN: &str = "ffb2dadfe2b36eb3f44f28c4282a8d51e84e1c943558500787cbb0518e2900a1";

    #[test]
    fn the_embedded_criterion_is_the_one_the_python_side_pins() {
        let selected = select("general-practice", PYTHON_PIN).unwrap();
        assert_eq!(selected.digest, PYTHON_PIN);
        assert!(selected.text.starts_with("# The supervisor's criterion"));
    }

    #[test]
    fn a_wrong_digest_or_an_unknown_name_is_refused() {
        let forged = format!("{}0", &PYTHON_PIN[..63]);
        assert!(
            select("general-practice", &forged)
                .unwrap_err()
                .contains("not the pinned")
        );
        assert!(
            select("instance-specific", PYTHON_PIN)
                .unwrap_err()
                .contains("no embedded")
        );
    }

    #[test]
    fn sha256_hex_matches_a_known_vector() {
        assert_eq!(
            sha256_hex(b"abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
    }
}
