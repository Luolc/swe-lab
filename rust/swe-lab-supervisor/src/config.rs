//! The run's settings: one schema-versioned JSON file, non-secret by design.
//!
//! The file carries what a run *is* — the task, the pinned criterion, the
//! policy's numbers, the model's name — and nothing about where the model is
//! or how to authenticate to it. Those two are deployment facts and travel the
//! way the actor's own do (`ANTHROPIC_BASE_URL` plus a token): as environment
//! variables: [`BASE_URL_ENV`], [`API_KEY_NAME_ENV`], and the API-key variable
//! named by the latter. A credential never appears in the file, on the command
//! line, or in any artifact.

use std::net::{IpAddr, SocketAddr};
use std::num::{NonZeroU32, NonZeroU64};
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
    /// How the actor is held while a judgment is in flight. Not defaulted:
    /// blocking and the stale gate are two answers to the same lag, and a
    /// run says which it uses.
    pub block_actor_while_judging: Blocking,
}

/// How the actor is held while a judgment is in flight.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum Blocking {
    /// Not held: the actor runs ahead, and a verdict that newer admitted
    /// evidence overtook is discarded as stale.
    Off,
    /// The wrapper stops reading the actor's stdout; the pipe fills and the
    /// actor's next write waits for the verdict. The absence of a read,
    /// which self-releases if the wrapper dies.
    Stdout,
    /// `SIGSTOP` to the actor's process group, `SIGCONT` after the verdict.
    /// Exact, but a real state the wrapper must leave before it exits.
    Sigstop,
}

/// Which model answers. Where it answers from is the environment's to say.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Model {
    /// The model name sent on every request. No default, for the same reason
    /// `budget` has none.
    pub name: String,
}

/// The environment variable holding the base URL of an Anthropic Messages
/// endpoint — `http://host[:port][/base]`; the binary appends `/v1/messages`.
/// Required. Plain HTTP by design: TLS is terminated
/// outside the binary, which keeps the dependency tree pure Rust and the
/// artifact static.
pub const BASE_URL_ENV: &str = "SWE_LAB_SUPERVISOR_BASE_URL";

/// The default environment variable holding the supervisor's Anthropic API
/// key. A harness may select another name through [`API_KEY_NAME_ENV`].
pub const API_KEY_ENV: &str = "ANTHROPIC_API_KEY";

/// The non-secret environment variable naming the credential variable.
pub const API_KEY_NAME_ENV: &str = "SWE_LAB_SUPERVISOR_API_KEY_ENV";

/// The configured API-key variable name, defaulting to [`API_KEY_ENV`].
///
/// # Errors
///
/// The selector is present but is not a portable environment-variable name.
/// The error never includes its value because a caller may have put the API
/// key itself there by mistake.
pub fn api_key_env_name() -> Result<String, String> {
    let Some(value) = std::env::var_os(API_KEY_NAME_ENV) else {
        return Ok(API_KEY_ENV.to_string());
    };
    let name = value
        .into_string()
        .map_err(|_| "the supervisor API-key selector is not a variable name".to_string())?;
    if !is_environment_name(&name) {
        return Err("the supervisor API-key selector is not a variable name".to_string());
    }
    Ok(name)
}

fn is_environment_name(name: &str) -> bool {
    let mut bytes = name.bytes();
    bytes
        .next()
        .is_some_and(|first| first.is_ascii_alphabetic() || first == b'_')
        && bytes.all(|byte| byte.is_ascii_alphanumeric() || byte == b'_')
}

/// Read the configured API key.
///
/// # Errors
///
/// The named variable is unset or empty. The error does not repeat the name:
/// a caller may have put the API key itself in the selector by mistake.
pub fn api_key_from_env(name: &str) -> Result<String, String> {
    std::env::var(name)
        .ok()
        .filter(|key| !key.is_empty())
        .ok_or_else(|| "the configured supervisor API key is unset or empty".to_string())
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

/// How much the wrapper buffers and how much the actor may write.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(deny_unknown_fields)]
#[expect(
    clippy::struct_field_names,
    reason = "the fields are the config schema's own names"
)]
pub struct Limits {
    /// The ceiling on one line of actor stdout. A line past it is still
    /// written to the event log but reaches no judgment, and the summary
    /// counts it. Tool results can be large, which is why this is explicit.
    pub max_event_line_bytes: NonZeroU32,
    /// The cap on the event log, exact to the byte: a line that would cross
    /// it is not written, the actor's stdout is not read further, and the
    /// run is over and unhealthy. Without it an actor that never stops
    /// writing fills the sandbox before the summary can be written.
    pub max_actor_stdout_bytes: NonZeroU64,
    /// The same cap for the stderr log.
    pub max_actor_stderr_bytes: NonZeroU64,
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
/// apart, with the Anthropic Messages path appended.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Endpoint {
    /// Where to connect: a loopback address and a port, both given as
    /// numbers in the URL — nothing here is resolved, so nothing about it
    /// depends on the environment the binary runs in.
    pub address: SocketAddr,
    /// The request target: the base URL's path plus `/v1/messages`.
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

    /// Take an `http://<loopback ip>:<port>[/base]` URL apart and append
    /// the Anthropic Messages path.
    ///
    /// Loopback only, by number: the supervisor sends an API key in
    /// clear, and the design has a forwarder on this host terminate TLS.
    /// A hostname — `localhost` included — would be resolved, and what it
    /// resolves to depends on the box; a numeric loopback address is a
    /// fact the binary can check on the spot. So no stray environment
    /// variable can point a request carrying `x-api-key` off the box:
    /// that is a property of the type, not of the deployment.
    ///
    /// # Errors
    ///
    /// Any other scheme — `https://` is refused with the reason, since the
    /// binary carries no TLS — a hostname or a non-loopback address, or a
    /// missing or unparseable port. The message names the fault, never
    /// the value: a URL can carry a signed token or a private host.
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
        let Some((host, port)) = authority.rsplit_once(':') else {
            return Err("the URL gives no port".to_string());
        };
        let port = port
            .parse::<u16>()
            .map_err(|_| "the port is not a port number".to_string())?;
        // `[::1]` as the URL writes it; `127.0.0.1` as is.
        let ip = host
            .strip_prefix('[')
            .and_then(|h| h.strip_suffix(']'))
            .unwrap_or(host)
            .parse::<IpAddr>()
            .map_err(|_| "the host is not a numeric IP address".to_string())?;
        if !ip.is_loopback() {
            return Err("the address is not loopback".to_string());
        }
        Ok(Self {
            address: SocketAddr::new(ip, port),
            path: format!("{base}/v1/messages"),
        })
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;

    #[test]
    fn an_api_key_selector_has_the_portable_environment_name_shape() {
        for accepted in ["A", "_", "ANTHROPIC_API_KEY", "key2"] {
            assert!(is_environment_name(accepted), "{accepted}");
        }
        for refused in ["", "2KEY", "API-KEY", "API KEY", "clé"] {
            assert!(!is_environment_name(refused), "{refused}");
        }
    }

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
        "block_actor_while_judging": "stdout"
      },
      "model": { "name": "anthropic/claude-sonnet-5" },
      "timeouts": { "model_call_ms": 180000, "term_grace_ms": 10000 },
      "limits": {
        "max_event_line_bytes": 16777216,
        "max_actor_stdout_bytes": 1073741824,
        "max_actor_stderr_bytes": 268435456
      }
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
        assert_eq!(config.policy.block_actor_while_judging, Blocking::Stdout);
        assert_eq!(config.limits.max_event_line_bytes.get(), 16_777_216);
        assert_eq!(config.limits.max_actor_stdout_bytes.get(), 1 << 30);
        assert_eq!(config.limits.max_actor_stderr_bytes.get(), 1 << 28);
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
    fn blocking_is_one_of_three_named_modes() {
        for (name, mode) in [("off", Blocking::Off), ("sigstop", Blocking::Sigstop)] {
            let raw = VALID.replace(
                "\"block_actor_while_judging\": \"stdout\"",
                &format!("\"block_actor_while_judging\": \"{name}\""),
            );
            assert_eq!(parsed(&raw).unwrap().policy.block_actor_while_judging, mode);
        }
        let raw = VALID.replace(
            "\"block_actor_while_judging\": \"stdout\"",
            "\"block_actor_while_judging\": true",
        );
        assert!(parsed(&raw).is_err());
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
            Endpoint::parse("http://127.0.0.1:8080").unwrap(),
            Endpoint {
                address: "127.0.0.1:8080".parse().unwrap(),
                path: "/v1/messages".to_string(),
            }
        );
        assert_eq!(
            Endpoint::parse("http://127.0.0.1:8080/api/").unwrap().path,
            "/api/v1/messages"
        );
        assert_eq!(
            Endpoint::parse("http://[::1]:8080").unwrap(),
            Endpoint {
                address: "[::1]:8080".parse().unwrap(),
                path: "/v1/messages".to_string(),
            }
        );
        assert!(
            Endpoint::parse("https://model.example/api")
                .unwrap_err()
                .contains("TLS")
        );
        // The fault is named; the value never is, whatever it carries.
        for (url, fault) in [
            ("http://:8080/", "numeric IP"),
            ("http://host:notaport/", "port"),
            ("judge:8080", "not an http://"),
            ("http://sk-SECRET-TOKEN@host:x/", "port"),
            ("http://127.0.0.1/v1", "no port"),
        ] {
            let error = Endpoint::parse(url).unwrap_err();
            assert!(error.contains(fault), "{url}: {error}");
            assert!(!error.contains("SECRET"), "{url}: {error}");
            assert!(!error.contains("notaport"), "{url}: {error}");
        }
    }

    /// Anything that is not a numeric loopback address is refused before
    /// a connection could be attempted — a hostname, `localhost` among
    /// them, since what it resolves to is the box's business; and a
    /// numeric address off the box. The reason names the fault, not the
    /// host.
    #[test]
    fn only_a_numeric_loopback_address_is_an_endpoint() {
        for (url, fault) in [
            ("http://judge:8080/v1", "numeric IP"),
            ("http://localhost:8080/v1", "numeric IP"),
            ("http://attacker.example:80/v1", "numeric IP"),
            ("http://10.0.0.7:8080/v1", "not loopback"),
            ("http://[fe80::1]:8080/v1", "not loopback"),
            ("http://0.0.0.0:8080/v1", "not loopback"),
        ] {
            let error = Endpoint::parse(url).unwrap_err();
            assert!(error.contains(fault), "{url}: {error}");
            assert!(!error.contains("attacker"), "{url}: {error}");
            assert!(!error.contains("10.0.0.7"), "{url}: {error}");
        }
        for url in [
            "http://127.0.0.1:1/v1",
            "http://127.255.255.254:65535",
            "http://[::1]:1",
        ] {
            assert!(
                Endpoint::parse(url).unwrap().address.ip().is_loopback(),
                "{url}"
            );
        }
    }
}
