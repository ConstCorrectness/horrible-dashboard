//! Making silence loud.
//!
//! The two clients — this one and the browser pane — are independent
//! implementations of the same game, and every divergence between them so far
//! was found by *looking at a screenshot*. That is not an accident of
//! discipline; it is what the code makes easy. Three separate mechanisms turn a
//! missing feature into a non-event:
//!
//! - **An unknown event is not an error.** `protocol::classify` funnels anything
//!   it has no variant for into `Event::Other`, and the app loop drops it. The
//!   native client ignored `roster`, `invite`, `invites`, `invite_sent` and
//!   `console_res` for as long as they existed.
//! - **An undeclared field is not an error either.** serde ignores keys a struct
//!   does not name, so a new wire field reaches this client, is discarded, and
//!   nothing anywhere says so.
//! - **An unknown enum tag is absorbed** by `#[serde(other)]`, which is the same
//!   failure one level down: a new `fx` kind becomes `Fx::Other` and evaporates.
//!
//! None of those *should* be fatal — a client that refused to run against a
//! newer backend would be worse than one that renders a little less of it. What
//! they should be is **loud**. This module is the one place that decides how
//! loud: reported once per distinct thing, on stderr, and readable back out for
//! a test or a HUD line.
//!
//! Once per *distinct* thing rather than once per occurrence, because a snapshot
//! arrives 20 times a second: an unfiltered warning would be 20 lines a second
//! of the same sentence, which is a way of being silent that costs more.

use std::collections::BTreeSet;
use std::sync::{Mutex, OnceLock};

fn seen_set() -> &'static Mutex<BTreeSet<String>> {
    static SEEN: OnceLock<Mutex<BTreeSet<String>>> = OnceLock::new();
    SEEN.get_or_init(|| Mutex::new(BTreeSet::new()))
}

/// Record `key`, and say whether this is the first time.
///
/// A poisoned lock is treated as "already reported": the only thing on the far
/// side of this mutex is a set of strings, and panicking a game loop over a
/// diagnostic would be the tail wagging the dog.
fn first_time(key: String) -> bool {
    match seen_set().lock() {
        Ok(mut set) => set.insert(key),
        Err(_) => false,
    }
}

fn report(key: String, message: &str) {
    if first_time(key) {
        eprintln!("hassault: [divergence] {message}");
    }
}

/// A `hassault` event this build has no variant for.
///
/// Called from `classify`, so it fires once per event name for the whole
/// process regardless of which binary is consuming the socket.
pub fn note_event(name: &str) {
    report(
        format!("event:{name}"),
        &format!(
            "the server sent a '{name}' event this client does not handle \
             (the browser pane may act on it)"
        ),
    );
}

/// Keys present on the wire that no field of `context` declares.
///
/// `context` is a dotted path into the message — `snapshot.you`, not just
/// `SelfState` — because the same struct read in two places is two different
/// things to go looking for.
pub fn note_extra(context: &str, extra: &Extra) {
    for key in extra.keys() {
        report(
            format!("field:{context}.{key}"),
            &format!("'{context}.{key}' is on the wire and this client declares no field for it"),
        );
    }
}

/// An `fx` entry whose `kind` this build has no variant for.
///
/// Separate from `note_extra` because the failure is one level down and reads
/// differently: the field *is* declared, it is the tag inside it that is new.
pub fn note_fx_kind(kind: &str) {
    report(
        format!("fx:{kind}"),
        &format!("the server sent an fx of kind '{kind}' this client does not draw"),
    );
}

/// A client CVar the node serves that this client has no reader for.
///
/// The console calls this when someone sets one, which is the moment it matters:
/// the alternative is a console that accepts `net.prediction 0`, says nothing,
/// and changes nothing.
pub fn note_unhonored_cvar(name: &str) {
    report(
        format!("cvar:{name}"),
        &format!("'{name}' is a client CVar this client does not read yet; the value is stored but nothing acts on it"),
    );
}

/// A CVar the node reported changing that this client's registry has no entry for.
///
/// Dropped rather than stored, because a value with no definition has no type,
/// no scope and no default — there is nothing to render it as and no way to know
/// whether it belongs to the client or the server. That is the right handling
/// and it is completely silent, which is the shape this module exists for: the
/// only symptom is a console chip that never updates, on a client whose registry
/// fetch is older than the node it is talking to.
pub fn note_unknown_cvar(name: &str) {
    report(
        format!("unknown-cvar:{name}"),
        &format!(
            "the node reported '{name}' changing and this client's registry has no              definition for it; the value is dropped"
        ),
    );
}

/// A weapon prop that would not load, so the box model is being drawn instead.
///
/// The fallback is correct — the boxes are a complete weapon — which is exactly
/// what makes this worth saying out loud. A corrupt or missing GLB otherwise
/// looks identical to a weapon that simply has no prop yet, and the two want
/// very different reactions.
pub fn note_prop(weapon: &str, why: &str) {
    report(
        format!("prop:{weapon}"),
        &format!("the '{weapon}' prop did not load ({why}); drawing the box model instead"),
    );
}

/// A fixed-size GPU buffer that could not hold this frame.
///
/// Truncation is the right behaviour — growing a buffer mid-frame is a stutter —
/// but it is exactly the kind of failure this module exists for: the only
/// symptom of an overflowing body buffer is a player who is not drawn, which
/// nobody traces back to a constant.
pub fn note_overflow(what: &str, wanted: usize, cap: usize) {
    report(
        format!("overflow:{what}"),
        &format!("the {what} buffer holds {cap} vertices and this frame wanted {wanted}; the rest is not drawn"),
    );
}

/// Everything reported so far, sorted. For tests, and for the console's
/// `divergences` listing.
pub fn seen() -> Vec<String> {
    match seen_set().lock() {
        Ok(set) => set.iter().cloned().collect(),
        Err(_) => Vec::new(),
    }
}

/// Undeclared wire keys, captured by `#[serde(flatten)]`.
///
/// A flattened catch-all rather than a hand-written list of expected keys, and
/// that choice is the whole point: a list would have to be updated in step with
/// the struct, which is the same discipline that failed in the first place.
/// Declaring a field *removes* it from here automatically.
pub type Extra = serde_json::Map<String, serde_json::Value>;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_thing_is_reported_once_and_then_remembered() {
        // Names unique to this test: the set is process-global on purpose, so a
        // test that used a plausible field name would depend on test order.
        assert!(first_time("test:alpha".into()));
        assert!(!first_time("test:alpha".into()));
        assert!(first_time("test:beta".into()));
        let all = seen();
        assert!(all.contains(&"test:alpha".to_string()));
        assert!(all.contains(&"test:beta".to_string()));
    }

    #[test]
    fn an_empty_extra_reports_nothing() {
        let before = seen().len();
        note_extra("test.context", &Extra::new());
        assert_eq!(seen().len(), before, "no keys means nothing to say");
    }
}
