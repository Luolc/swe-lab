//! The wrapper's own termination signals, as a flag the loop polls.
//!
//! `SIGTERM` is what the outside reaches for when a run's wall clock runs out.
//! The wrapper does not die of it: it forwards the ending to the actor's
//! process group through [`crate::actor::Actor::end`] and writes its account
//! first, so the ending is attributed rather than lost.

use std::io;
use std::sync::Arc;
use std::sync::atomic::AtomicBool;

use signal_hook::consts::{SIGINT, SIGTERM};

/// Register `SIGTERM` and `SIGINT` to raise the returned flag.
///
/// # Errors
///
/// The handler could not be installed.
pub fn termination_requested() -> io::Result<Arc<AtomicBool>> {
    let flag = Arc::new(AtomicBool::new(false));
    for signal in [SIGTERM, SIGINT] {
        let _id = signal_hook::flag::register(signal, Arc::clone(&flag))?;
    }
    Ok(flag)
}
