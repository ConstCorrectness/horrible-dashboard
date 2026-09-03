//! The mode wire, against payloads the **real server actually produced**.
//!
//! `tests/mode-wire.json` is a welcome and a snapshot captured out of a live
//! defuse room mid-round, bomb planted — not written by hand. That is the whole
//! point: every other test of these types builds the struct it is about to
//! assert on, so all of them would pass just as happily against a wire shape the
//! server has never sent.
//!
//! This is the one seam where the two sides can disagree in silence.
//! `#[serde(default)]` is on every field here — which it has to be, so an older
//! server does not break a newer client — and the cost of that is exactly this:
//! a renamed key, a `camelCase` slip, a struct nested one level differently, all
//! deserialize to a default without an error. The symptom is not a crash, it is
//! a HUD that draws zeros over a round that is really happening.
//!
//! Regenerate with the snippet in `docs/modules/hassault.mdx` when the mode wire
//! changes on purpose.

use hassault_native::protocol::{classify, Event, SUPPORTED_MODE_V};

fn fixture() -> serde_json::Value {
    serde_json::from_str(include_str!("mode-wire.json")).expect("the fixture parses")
}

fn line(which: &str) -> String {
    fixture()[which].to_string()
}

#[test]
fn a_real_welcome_carries_the_mode_and_its_static_configuration() {
    let Some(Event::Welcome(w)) = classify(&line("welcome")) else {
        panic!("the welcome did not classify as one");
    };
    let mode = w.mode.expect("the welcome carried no mode at all");
    assert_eq!(mode.id, "defuse");
    assert_eq!(mode.score_label, "Rounds", "scoreLabel did not survive");
    assert!(mode.teams);
    assert_eq!(mode.v, SUPPORTED_MODE_V);

    // The timings, which the HUD must never re-derive: a plant bar that finishes
    // early is a bar drawn against a number this client made up.
    assert!(mode.config.plant_time > 0.0, "plantTime did not survive");
    assert!(mode.config.defuse_time > 0.0, "defuseTime did not survive");
    assert!(mode.config.fuse_time > 0.0, "fuseTime did not survive");
    assert!(mode.config.rounds_to_win > 0, "roundsToWin did not survive");

    // Sites arrive resolved onto the floor, so the client places nothing itself.
    assert!(!mode.sites.is_empty(), "no sites in the welcome");
    assert!(mode.sites.iter().all(|s| !s.id.is_empty()));
}

#[test]
fn the_welcome_flattens_the_current_state_in_beside_the_static_half() {
    // Which is what gives the HUD a real phase on the *first* frame instead of a
    // blank one until the next snapshot — joining mid-round otherwise shows a
    // round clock reading zero, and that looks like the round just ended.
    let Some(Event::Welcome(w)) = classify(&line("welcome")) else {
        panic!("not a welcome");
    };
    let mode = w.mode.expect("no mode");
    assert!(!mode.state.phase.is_empty(), "the welcome carried no phase");
    assert!(mode.state.round > 0);
}

#[test]
fn a_real_snapshot_carries_the_public_mode_state() {
    let Some(Event::Snapshot(s)) = classify(&line("snapshot")) else {
        panic!("the snapshot did not classify as one");
    };
    let mode = s.mode.expect("the snapshot carried no mode blob");
    assert_eq!(mode.phase, "live");
    assert!(mode.round > 0);

    // The bomb, which is the whole reason this fixture was captured mid-plant.
    assert_eq!(mode.bomb.state, "planted");
    assert_eq!(mode.bomb.site, "A");
    assert!(mode.bomb.fuse_in > 0.0, "fuseIn did not survive the rename");
    assert!(
        mode.bomb.x != 0.0 || mode.bomb.y != 0.0,
        "the bomb has no position"
    );
}

#[test]
fn the_private_half_rides_inside_you_and_not_in_the_shared_blob() {
    // The rule the server side documents as its most dangerous mistake: anything
    // per-recipient placed in the shared state is sent to everybody, and nothing
    // raises, warns, or breaks the snapshot template. This is the client-side
    // half of that guard — if these fields ever start arriving in `mode` instead
    // of `you.mode`, this fails rather than quietly reading them from the wrong
    // place.
    let Some(Event::Snapshot(s)) = classify(&line("snapshot")) else {
        panic!("not a snapshot");
    };
    let mine = s.you.mode.expect("`you` carried no mode blob");
    // Captured from the defender's envelope, on the round the attackers planted.
    assert!(!mine.attacking, "the defender's envelope said attacking");
    assert!(!mine.carrying);
    assert_eq!(mine.progress, 0.0);
}

#[test]
fn a_client_that_is_too_old_for_the_mode_wire_is_told_so() {
    // The gap nothing else covers. `divergence` reports unknown *events* and
    // unknown *fx kinds*; an unknown key inside a mode blob is swallowed by
    // `#[serde(default)]` without a word, so an old build would join a mode it
    // cannot draw and say nothing at all. The version stamp is the only thing
    // there is to compare, so it must actually be on the wire.
    let Some(Event::Welcome(w)) = classify(&line("welcome")) else {
        panic!("not a welcome");
    };
    let v = w.mode.expect("no mode").v;
    assert!(v > 0, "the server sent no mode version to compare against");
    assert!(
        v <= SUPPORTED_MODE_V,
        "this fixture is newer than the build reading it"
    );
}
