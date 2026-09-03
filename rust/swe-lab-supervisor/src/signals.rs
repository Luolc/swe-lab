//! The wrapper's own termination signals, as a value the loop polls.
//!
//! `SIGTERM` is what the outside reaches for when a run's wall clock runs out.
//! The wrapper does not die of it: it forwards the ending to the actor's
//! process group through [`crate::actor::Actor::end`] and writes its account
//! first, so the ending is attributed rather than lost — and it is reported
//! as a cancellation, whatever the actor's own exit status turns out to be.

use std::io;
use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};

use signal_hook::consts::{SIGINT, SIGTERM};

/// The signal that asked the wrapper to stop, or zero while none has.
pub type Stop = AtomicUsize;

/// Register `SIGTERM` and `SIGINT` to store their number in the returned
/// value; zero means nothing has been asked.
///
/// # Errors
///
/// The handler could not be installed.
pub fn termination_requested() -> io::Result<Arc<Stop>> {
    let stop = Arc::new(AtomicUsize::new(0));
    for signal in [SIGTERM, SIGINT] {
        let value = usize::try_from(signal).map_err(|_| io::Error::other("signal number"))?;
        let _id = signal_hook::flag::register_usize(signal, Arc::clone(&stop), value)?;
    }
    Ok(stop)
}

/// The signal that asked for a stop, if one has.
#[must_use]
pub fn requested(stop: &Stop) -> Option<i32> {
    match stop.load(Ordering::Relaxed) {
        0 => None,
        signal => i32::try_from(signal).ok(),
    }
}
