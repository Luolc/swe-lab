//! The run's settings: one schema-versioned JSON file, non-secret by design.
//!
//! The file carries what a run *is* — the task, the pinned criterion, the
//! policy's numbers, the model's name — and nothing about where the model is
//! or how to authenticate to it. Those two are deployment facts and travel the
//! way the actor's own do (`ANTHROPIC_BASE_URL` plus a token): as environment
//! variables, [`BASE_URL_ENV`] and [`API_KEY_ENV`], passed into the sandbox by
//! reference and read in-process. A credential never appears in the file, on
//! the command line, or in any artifact.

use std::num::NonZeroU32;
use std::path::Path;

use serde::Deserialize;

/// The one schema version this binary reads.
pub const SCHEMA_VERSION: u32 = 1;

/// The one policy this binary implements. Present in the file so that a
/// config written for another policy is refused rather than reinterpreted.
pub const POLICY_KIND: &str = "speak-when-off-track";

/// Everything a run is configured with.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Config {
    /// Must equal [`SCHEMA_VERSION`].
    pub schema_version: u32,
    /// What the actor was asked to do — the supervisor's view of the goal.
    pub task: String,
    /// Which embedded criterion the judge measures against, and its digest.
    pub criterion: CriterionPin,
    /// When the supervisor speaks.
    pub policy: Policy,
    /// Which model answers, and where.
    pub model: Model,
    /// The bounds on waiting.
    pub timeouts: Timeouts,
    /// The bounds on buffering.
    pub limits: Limits,
}

/// The criterion by name, pinned to a digest the binary must reproduce.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CriterionPin {
    /// The name of one of the criteria compiled into the binary.
    pub name: String,
    /// The lowercase hex sha256 the embedded text must have.
    pub sha256: String,
}

/// The speaking policy's parameters. None has a default: a policy that may
/// speak states how often, how far apart, and on how much evidence.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Policy {
    /// Must equal [`POLICY_KIND`].
    pub kind: String,
    /// How many corrections a whole run may carry. Zero is the control arm:
    /// the judge still runs at every boundary and nothing is ever said.
    pub budget: u32,
    /// How many judgment boundaries must pass between two corrections. It
    /// never delays the first one.
    pub cooldown: u32,
    /// How many of the actor's most recent admitted records the judge sees.
    pub window: NonZeroU32,
    /// A judgment boundary falls every this many admitted assistant messages
    /// (and at every actor `result` that has new evidence behind it). Not
    /// defaulted: the value is a run's choice, and #375 records why no value
    /// has been measured yet.
    pub judge_every_n_assistant_messages: NonZeroU32,
    /// Whether the wrapper stops reading the actor's stdout while a judgment
    /// is in flight, so the actor blocks on its next write until the verdict
    /// is in. Off, the actor runs ahead and a verdict overtaken by newer
    /// evidence is discarded as stale.
    pub block_actor_while_judging: bool,
}

/// Which model answers. Where it answers from is the environment's to say.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Model {
    /// The model name sent on every request. No default, for the same reason
    /// `budget` has none.
    pub name: String,
}

/// The environment variable holding the base URL of the OpenAI-shaped
/// chat-completions API — `http://host[:port]/v1`; the binary appends
/// `/chat/completions`. Required. Plain HTTP by design: TLS is terminated
/// outside the binary, which keeps the dependency tree pure Rust and the
/// artifact static.
pub const BASE_URL_ENV: &str = "SWE_LAB_SUPERVISOR_BASE_URL";

/// The environment variable holding the bearer credential, if the endpoint
/// needs one. Its value may hold several keys comma-separated; the first is
/// used, split in-process so no key ever reaches a command line. Unset or
/// empty, no `Authorization` header is sent. Both variables are removed from
/// the actor's environment before it is launched.
pub const API_KEY_ENV: &str = "SWE_LAB_SUPERVISOR_API_KEY";

/// The bearer credential from [`API_KEY_ENV`], or `None` when the variable
/// is unset or holds no key. Split here, never in a shell.
#[must_use]
pub fn api_key_from_env() -> Option<String> {
    std::env::var(API_KEY_ENV)
        .ok()?
        .split(',')
        .map(str::trim)
        .find(|key| !key.is_empty())
        .map(str::to_string)
}

/// How long the wrapper waits, in milliseconds.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Timeouts {
    /// The bound on one judge or writer call, connection included. A call
    /// past it is one lapse; the next boundary is judged normally.
    pub model_call_ms: u64,
    /// How long the actor's process group gets to honour `SIGTERM` before
    /// `SIGKILL` — and how long it gets to exit on its own after its stdin is
    /// closed deliberately, before that same teardown starts.
    pub term_grace_ms: u64,
}

/// How much the wrapper buffers.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Limits {
    /// The ceiling on one line of actor stdout. A line past it is still
    /// written to the event log but reaches no judgment, and the summary
    /// counts it. Tool results can be large, which is why this is explicit.
    pub max_event_line_bytes: NonZeroU32,
}

/// Read and validate the config file.
///
/// # Errors
///
/// The file cannot be read, is not the schema this binary reads, or carries a
/// value the runtime cannot honour. Every error names the field.
pub fn load(path: &Path) -> Result<Config, String> {
    // The path is the caller's and is not repeated: it is on the command
    // line they wrote, and a diagnostic that formats caller input is how a
    // misplaced value reaches a log.
    let raw = std::fs::read(path).map_err(|e| format!("reading the config file: {e}"))?;
    let config: Config =
        serde_json::from_slice(&raw).map_err(|e| format!("parsing the config file: {e}"))?;
    config.validate()?;
    Ok(config)
}

impl Config {
    /// Refuse what the schema alone cannot: version and kind pins, the digest's
    /// shape, and an endpoint the client can reach.
    ///
    /// # Errors
    ///
    /// One field is unusable; the message names it.
    pub fn validate(&self) -> Result<(), String> {
        if self.schema_version != SCHEMA_VERSION {
            return Err(format!(
                "schema_version {} is not the {SCHEMA_VERSION} this binary reads",
                self.schema_version
            ));
        }
        if self.policy.kind != POLICY_KIND {
            return Err(format!(
                "policy.kind {:?} is not {POLICY_KIND:?}, the one policy this binary implements",
                self.policy.kind
            ));
        }
        if !is_sha256_hex(&self.criterion.sha256) {
            return Err("criterion.sha256 must be 64 lowercase hex digits".to_string());
        }
        if self.model.name.is_empty() {
            return Err("model.name must name a model".to_string());
        }
        if self.timeouts.model_call_ms == 0 {
            return Err("timeouts.model_call_ms must be positive".to_string());
        }
        Ok(())
    }
}

fn is_sha256_hex(digest: &str) -> bool {
    digest.len() == 64
        && digest
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

/// Where a model request goes: the base URL from [`BASE_URL_ENV`], taken
/// apart, with the chat-completions path appended.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Endpoint {
    /// The host to connect to, as written.
    pub host: String,
    /// The port, `80` when the URL gives none.
    pub port: u16,
    /// The request target: the base URL's path plus `/chat/completions`.
    pub path: String,
}

impl Endpoint {
    /// Read the base URL from the environment and take it apart.
    ///
    /// # Errors
    ///
    /// The variable is unset or empty, or its value is not usable — see
    /// [`Endpoint::parse`].
    pub fn from_env() -> Result<Self, String> {
        match std::env::var(BASE_URL_ENV) {
            Ok(url) if !url.is_empty() => {
                Self::parse(&url).map_err(|e| format!("{BASE_URL_ENV}: {e}"))
            }
            _ => Err(format!(
                "{BASE_URL_ENV} is unset or empty, so the supervisor cannot reach a model"
            )),
        }
    }

    /// Take an `http://host[:port][/base]` URL apart and append the
    /// chat-completions path.
    ///
    /// # Errors
    ///
    /// Any other scheme — `https://` is refused with the reason, since the
    /// binary carries no TLS — an empty host, or an unparseable port. The
    /// message names the fault, never the value: a URL can carry a signed
    /// token or a private host.
    pub fn parse(url: &str) -> Result<Self, String> {
        if url.starts_with("https://") {
            return Err(
                "https is not supported: the binary speaks plain HTTP and TLS is terminated outside it"
                    .to_string(),
            );
        }
        let Some(rest) = url.strip_prefix("http://") else {
            return Err("not an http:// URL".to_string());
        };
        let (authority, base) = match rest.find('/') {
            Some(at) => (&rest[..at], rest[at..].trim_end_matches('/')),
            None => (rest, ""),
        };
        let (host, port) = match authority.rsplit_once(':') {
            Some((host, port)) => (
                host,
                port.parse::<u16>()
                    .map_err(|_| "the port is not a port number".to_string())?,
            ),
            None => (authority, 80),
        };
        if host.is_empty() {
            return Err("the URL has no host".to_string());
        }
        Ok(Self {
            host: host.to_string(),
            port,
            path: format!("{base}/chat/completions"),
        })
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;

    /// A config every field of which is usable; tests perturb one field.
    pub(crate) const VALID: &str = r#"{
      "schema_version": 1,
      "task": "Fix the failing test.",
      "criterion": {
        "name": "general-practice",
        "sha256": "ffb2dadfe2b36eb3f44f28c4282a8d51e84e1c943558500787cbb0518e2900a1"
      },
      "policy": {
        "kind": "speak-when-off-track",
        "budget": 3,
        "cooldown": 4,
        "window": 8,
        "judge_every_n_assistant_messages": 3,
        "block_actor_while_judging": true
      },
      "model": { "name": "anthropic/claude-sonnet-5" },
      "timeouts": { "model_call_ms": 180000, "term_grace_ms": 10000 },
      "limits": { "max_event_line_bytes": 16777216 }
    }"#;

    fn parsed(raw: &str) -> Result<Config, String> {
        let config: Config = serde_json::from_str(raw).map_err(|e| e.to_string())?;
        config.validate()?;
        Ok(config)
    }

    #[test]
    fn the_reference_config_is_valid_and_reads_back_what_was_written() {
        let config = parsed(VALID).unwrap();
        assert_eq!(config.policy.budget, 3);
        assert_eq!(config.policy.window.get(), 8);
        assert_eq!(config.policy.judge_every_n_assistant_messages.get(), 3);
        assert!(config.policy.block_actor_while_judging);
        assert_eq!(config.limits.max_event_line_bytes.get(), 16_777_216);
    }

    #[test]
    fn an_unknown_field_is_refused_not_ignored() {
        // A field this binary does not read is most likely a setting the
        // writer believed was in force; silently dropping it would run a
        // different configuration from the one written down.
        let raw = VALID.replace("\"budget\": 3,", "\"budget\": 3, \"gold_patch\": \"...\",");
        assert!(parsed(&raw).unwrap_err().contains("gold_patch"));
    }

    #[test]
    fn a_zero_n_or_window_is_refused_by_the_type() {
        assert!(parsed(&VALID.replace("\"window\": 8", "\"window\": 0")).is_err());
        assert!(
            parsed(&VALID.replace(
                "\"judge_every_n_assistant_messages\": 3",
                "\"judge_every_n_assistant_messages\": 0"
            ))
            .is_err()
        );
    }

    #[test]
    fn the_pins_are_checked() {
        assert!(
            parsed(&VALID.replace("\"schema_version\": 1", "\"schema_version\": 2"))
                .unwrap_err()
                .contains("schema_version")
        );
        assert!(
            parsed(&VALID.replace("speak-when-off-track", "never-speak"))
                .unwrap_err()
                .contains("policy.kind")
        );
        assert!(
            parsed(&VALID.replace("ffb2dadf", "FFB2DADF"))
                .unwrap_err()
                .contains("criterion.sha256")
        );
    }

    #[test]
    fn the_file_cannot_carry_an_endpoint_or_a_credential() {
        // Both are the environment's: a file that names them is refused as
        // unknown fields rather than read.
        for field in ["\"endpoint\": \"http://x/v1\"", "\"api_key\": \"sk-...\""] {
            let raw = VALID.replace(
                "\"model\": { \"name\": \"anthropic/claude-sonnet-5\" }",
                &format!("\"model\": {{ \"name\": \"m\", {field} }}"),
            );
            assert!(parsed(&raw).is_err(), "{field} was accepted");
        }
    }

    #[test]
    fn a_base_url_is_taken_apart_and_https_is_refused_with_the_reason() {
        assert_eq!(
            Endpoint::parse("http://127.0.0.1:8080/v1").unwrap(),
            Endpoint {
                host: "127.0.0.1".to_string(),
                port: 8080,
                path: "/v1/chat/completions".to_string(),
            }
        );
        assert_eq!(
            Endpoint::parse("http://judge/api/v1/").unwrap().path,
            "/api/v1/chat/completions"
        );
        assert_eq!(
            Endpoint::parse("http://judge").unwrap(),
            Endpoint {
                host: "judge".to_string(),
                port: 80,
                path: "/chat/completions".to_string(),
            }
        );
        assert!(
            Endpoint::parse("https://openrouter.ai/api/v1")
                .unwrap_err()
                .contains("TLS")
        );
        // The fault is named; the value never is, whatever it carries.
        for (url, fault) in [
            ("http://:8080/", "no host"),
            ("http://host:notaport/", "port"),
            ("judge:8080", "not an http://"),
            ("http://sk-SECRET-TOKEN@host:x/", "port"),
        ] {
            let error = Endpoint::parse(url).unwrap_err();
            assert!(error.contains(fault), "{url}: {error}");
            assert!(!error.contains("SECRET"), "{url}: {error}");
            assert!(!error.contains("notaport"), "{url}: {error}");
        }
    }
}
