//! The client's half of throwing: what is readied, and when it leaves the hand.
//!
//! The sibling of the trigger in `app.rs`, and the split is the same one
//! `nades.rs` makes about drawing: nothing here decides where a grenade lands,
//! what it blinds or how long a cloud stands — that is the server's, and only
//! the server's (`backend/modules/hassault/grenades.py`). What this owns is the
//! two things that must happen on the frame the throw key goes down: the count
//! on the HUD drops, and the next command carries `throw: true`.
//!
//! A direct port of the browser's `utility.ts`, deliberately so. The two clients
//! play the same game against the same server, and a rule that exists in only
//! one of them is a rule half the players are not subject to.
//!
//! **The throw is edge-triggered, and that is the whole reason this type
//! exists.** `throw` rides on a movement command, so a key simply read as *held*
//! sets the flag on every frame it is down — sixty throws a second, of which the
//! server's cooldown accepts one and silently discards the rest. The player sees
//! one grenade for a key they held, an empty pouch, and nothing anywhere
//! explaining the difference.
//!
//! Carry counts are **predicted and then corrected**, exactly as ammo is: the
//! count drops locally the instant you throw and is overwritten by `you.nades`
//! on the next snapshot. Usually right, always corrected — so a throw the server
//! refused (cooldown, empty, dead) puts the count back rather than leaving the
//! HUD one short until the next respawn.
//!
//! Free of `winit`, `wgpu` and the socket, so all of it is testable headless.

use std::collections::HashMap;

use crate::api::TacticalSpec;
use crate::protocol::SelfState;

/// Milliseconds between throws, mirroring `THROW_COOLDOWN` in `match.py` and
/// `THROW_COOLDOWN_MS` in `utility.ts`.
///
/// Not a second enforcement — the server owns it — but the same reason the fire
/// rate is mirrored: without it the client spends a command field every frame on
/// a throw the server has already decided to refuse.
pub const THROW_COOLDOWN_MS: f64 = 900.0;

/// What a frame decided about throwing.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct ThrowIntent {
    pub throwing: bool,
    /// Slot to throw, or `-1` when nothing is going out this frame.
    pub nade: i32,
    pub lob: bool,
}

impl ThrowIntent {
    pub const NONE: ThrowIntent = ThrowIntent {
        throwing: false,
        nade: -1,
        lob: false,
    };
}

#[derive(Debug, Default)]
pub struct GrenadeController {
    /// The served catalogue, in slot order. Empty until `/tacticals` answers,
    /// which is a throw key that does nothing rather than a client that guessed.
    specs: Vec<TacticalSpec>,
    /// Which slot is readied. Selecting only readies; throwing is its own key.
    slot: usize,
    /// Predicted carry counts, keyed by grenade id.
    counts: HashMap<String, i32>,
    want_throw: bool,
    want_lob: bool,
    last_throw_at: f64,
    /// The slot currently **in your hand**, or `None` when a weapon is.
    ///
    /// Selecting used to only *ready* a grenade — the gun stayed up and throwing
    /// was its own key. Equipping is what lets the two mouse buttons mean throw
    /// and toss without taking the right button away from the sniper's scope,
    /// whose whole identity is that scope.
    ///
    /// Entirely client-side. The server has no concept of an equipped grenade
    /// and needs none: `_throw` reads `command.nade` and nothing else.
    equipped: Option<usize>,
    /// True for exactly the frame after a throw left the hand.
    threw: bool,
}

impl GrenadeController {
    pub fn new(specs: Vec<TacticalSpec>) -> GrenadeController {
        let mut it = GrenadeController {
            last_throw_at: f64::NEG_INFINITY,
            ..Default::default()
        };
        it.set_specs(specs);
        it
    }

    pub fn set_specs(&mut self, specs: Vec<TacticalSpec>) {
        self.specs = specs;
        if self.counts.is_empty() {
            self.reset();
        }
    }

    pub fn catalogue(&self) -> &[TacticalSpec] {
        &self.specs
    }

    pub fn selected(&self) -> usize {
        self.slot
    }

    pub fn selected_spec(&self) -> Option<&TacticalSpec> {
        self.specs.get(self.slot)
    }

    /// How many of the grenade in `slot` we believe we are holding.
    pub fn count_of(&self, slot: usize) -> i32 {
        self.specs
            .get(slot)
            .and_then(|s| self.counts.get(&s.id))
            .copied()
            .unwrap_or(0)
    }

    /// Ready a slot.
    ///
    /// Readying an *empty* slot is allowed and deliberate: the tray then shows it
    /// greyed with a zero, which is a better answer to "why did nothing happen"
    /// than silently readying a different grenade than the key names.
    pub fn select(&mut self, slot: usize) {
        if slot < self.specs.len() {
            self.slot = slot;
        }
    }

    /// Step to the next slot that still has something in it.
    pub fn cycle(&mut self) {
        if self.specs.is_empty() {
            return;
        }
        for i in 1..=self.specs.len() {
            let next = (self.slot + i) % self.specs.len();
            if self.count_of(next) > 0 {
                self.slot = next;
                return;
            }
        }
    }

    /// Whether a grenade is in your hand rather than a weapon.
    pub fn equipped(&self) -> bool {
        self.equipped.is_some()
    }

    /// Take a grenade in hand: ready it *and* put the weapon away.
    ///
    /// What the number keys now do. Empty slots still equip — `select`'s
    /// reasoning, and the auto-holster below means nobody is ever left holding
    /// nothing.
    pub fn equip(&mut self, slot: usize) {
        if slot < self.specs.len() {
            self.slot = slot;
            self.equipped = Some(slot);
        }
    }

    /// Put the grenade away. A weapon key, Escape, dying, or the last one gone.
    pub fn holster(&mut self) {
        self.equipped = None;
        // A throw queued on the frame the pouch was put away must not come out
        // on the next one.
        self.want_throw = false;
        self.want_lob = false;
    }

    /// Whether a throw left the hand on the frame just resolved.
    ///
    /// Read once by the app to bring the previous weapon back up.
    pub fn just_threw(&self) -> bool {
        self.threw
    }

    /// A throw key went down this frame. Edge, not level — see the module docs.
    pub fn press(&mut self, lob: bool) {
        self.want_throw = true;
        self.want_lob = lob;
    }

    /// What this frame sends, and the point at which the local count drops.
    ///
    /// Takes `you` for the reason the ammo prediction does: the server's answer
    /// is the truth, and adopting it here is what makes a refused throw give the
    /// grenade back.
    pub fn frame(&mut self, now_ms: f64, you: Option<&SelfState>) -> ThrowIntent {
        if let Some(you) = you {
            // Guarded on non-empty rather than adopted blindly: Train produces a
            // `SelfState` with no inventory in it at all, and adopting that would
            // empty a pouch the server has never been asked about.
            if !you.nades.is_empty() {
                self.counts = you.nades.clone();
            }
        }

        self.threw = false;
        // Dying puts the grenade away. Coming back holding one you readied in a
        // previous life, with the weapon stowed, is a spawn you cannot shoot
        // from.
        if you.is_some_and(|y| !y.alive) {
            self.equipped = None;
        }

        let wanted = std::mem::take(&mut self.want_throw);
        let lob = std::mem::take(&mut self.want_lob);
        if !wanted {
            return ThrowIntent::NONE;
        }
        // Dead men throw nothing. Checked here rather than at the key, because
        // the key press is real input and swallowing it silently at the edge
        // would make a throw queued a frame before dying come out on respawn.
        if you.is_some_and(|y| !y.alive) {
            return ThrowIntent::NONE;
        }
        if now_ms - self.last_throw_at < THROW_COOLDOWN_MS {
            return ThrowIntent::NONE;
        }
        let Some(spec) = self.specs.get(self.slot) else {
            return ThrowIntent::NONE;
        };
        let id = spec.id.clone();
        if self.counts.get(&id).copied().unwrap_or(0) <= 0 {
            return ThrowIntent::NONE;
        }

        self.last_throw_at = now_ms;
        let left = self.counts.entry(id).or_insert(0);
        *left -= 1;
        let emptied = *left <= 0;
        let intent = ThrowIntent {
            throwing: true,
            nade: self.slot as i32,
            lob,
        };
        self.threw = true;
        // Ready the next one you actually have. Standing there holding an empty
        // hand after your last smoke is a state with nothing to do in it.
        if emptied {
            self.cycle();
            // Nothing left of *anything*: put the pouch away rather than leaving
            // the weapon stowed and both mouse buttons doing nothing.
            if self
                .specs
                .iter()
                .all(|s| self.counts.get(&s.id).copied().unwrap_or(0) <= 0)
            {
                self.equipped = None;
            }
        }
        // The grenade has left the hand. The weapon comes back up on the same
        // frame — a throw is one action, not a mode you have to leave.
        self.equipped = None;
        intent
    }

    /// Spawning refills, matching `reset_loadout` on the server.
    pub fn reset(&mut self) {
        self.equipped = None;
        self.threw = false;
        self.counts.clear();
        for spec in &self.specs {
            self.counts.insert(spec.id.clone(), spec.carried);
        }
        self.last_throw_at = f64::NEG_INFINITY;
        self.want_throw = false;
        self.want_lob = false;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn spec(id: &str, carried: i32) -> TacticalSpec {
        TacticalSpec {
            id: id.to_string(),
            name: id.to_uppercase(),
            kind: id.to_string(),
            carried,
            ..Default::default()
        }
    }

    fn loadout() -> GrenadeController {
        GrenadeController::new(vec![
            spec("he", 1),
            spec("flash", 2),
            spec("smoke", 1),
            spec("molotov", 1),
        ])
    }

    #[test]
    fn a_held_key_throws_once_and_not_once_a_frame() {
        // The bug this is the whole shape of the type for: sixty throws a
        // second, one accepted, an empty pouch and no explanation.
        let mut g = loadout();
        g.press(false);
        assert!(g.frame(0.0, None).throwing);
        for frame in 1..60 {
            assert_eq!(g.frame(f64::from(frame) * 16.0, None), ThrowIntent::NONE);
        }
        assert_eq!(g.count_of(0), 0);
    }

    #[test]
    fn the_cooldown_refuses_a_second_press_and_keeps_the_grenade() {
        let mut g = loadout();
        g.select(1);
        g.press(false);
        assert!(g.frame(0.0, None).throwing);
        g.press(false);
        assert_eq!(g.frame(100.0, None), ThrowIntent::NONE);
        // Refused, so it is still in the pouch — a refusal that spent one would
        // be worse than the double throw it is refusing.
        assert_eq!(g.count_of(1), 1);
        g.press(false);
        assert!(g.frame(THROW_COOLDOWN_MS + 1.0, None).throwing);
        assert_eq!(g.count_of(1), 0);
    }

    #[test]
    fn an_empty_slot_throws_nothing_and_readies_the_next_one_you_have() {
        let mut g = loadout();
        g.press(false);
        g.frame(0.0, None);
        // The HE is gone, so the readied slot moves rather than leaving the
        // player holding an empty hand.
        assert_eq!(g.selected(), 1);
        g.select(0);
        g.press(false);
        assert_eq!(g.frame(5000.0, None), ThrowIntent::NONE);
    }

    #[test]
    fn the_underhand_is_the_same_throw_with_a_flag() {
        let mut g = loadout();
        g.select(2);
        g.press(true);
        assert_eq!(
            g.frame(0.0, None),
            ThrowIntent {
                throwing: true,
                nade: 2,
                lob: true
            }
        );
    }

    #[test]
    fn the_servers_count_wins_over_the_predicted_one() {
        // A throw the server refused has to give the grenade back. The
        // prediction is only ever a guess about a round trip.
        let mut g = loadout();
        g.select(1);
        g.press(false);
        g.frame(0.0, None);
        assert_eq!(g.count_of(1), 1);

        let mut you = SelfState {
            alive: true,
            ..Default::default()
        };
        you.nades.insert("flash".to_string(), 2);
        g.frame(16.0, Some(&you));
        assert_eq!(g.count_of(1), 2);
    }

    #[test]
    fn a_selfstate_with_no_inventory_does_not_empty_the_pouch() {
        // Train's `SelfState` carries no grenades at all, and reading that as
        // "you have none" would silently disarm the one mode you would practise
        // a smoke line in.
        let mut g = loadout();
        let you = SelfState {
            alive: true,
            ..Default::default()
        };
        g.frame(0.0, Some(&you));
        assert_eq!(g.count_of(1), 2);
    }

    #[test]
    fn the_dead_throw_nothing() {
        let mut g = loadout();
        let you = SelfState {
            alive: false,
            ..Default::default()
        };
        g.press(false);
        assert_eq!(g.frame(0.0, Some(&you)), ThrowIntent::NONE);
        assert_eq!(g.count_of(0), 1);
    }

    #[test]
    fn nothing_served_means_a_throw_key_that_does_nothing_not_a_guess() {
        let mut g = GrenadeController::default();
        g.press(false);
        assert_eq!(g.frame(0.0, None), ThrowIntent::NONE);
        assert!(g.selected_spec().is_none());
    }

    #[test]
    fn respawning_refills() {
        let mut g = loadout();
        g.press(false);
        g.frame(0.0, None);
        g.reset();
        assert_eq!(g.count_of(0), 1);
        // And the cooldown goes with it: a respawn is a new life, not the same
        // one holding a stopwatch.
        g.press(false);
        assert!(g.frame(1.0, None).throwing);
    }
}
