//! The post-match summary: how you did, for the player still in the game window.
//!
//! **This is an addition, not a gap being backfilled.** A finished match already
//! produces a debrief card in the dashboard — `channel._leave_any` reads
//! `MatchRoom.result_for` and files it under the account. What it does not do is
//! show it to somebody who is still looking at the game, which is exactly the
//! moment they want it.
//!
//! ## Why leaving is the moment
//!
//! There is no round end to hang this on: deathmatch is the only mode and it
//! runs until you go. That makes *leaving* the end of the match for this player,
//! and it is already the instant the server settles up — `result_for` is read in
//! `leave`, before `remove` drops the counters the card is made of. So the native
//! card and the dashboard card describe the same moment by construction rather
//! than by agreement.
//!
//! ## What is NOT on this card, and why
//!
//! **Damage dealt.** The dashboard card has it; this one deliberately does not,
//! and the reason is worth writing down because "just sum the hitmarkers" is the
//! obvious thing to do and it is wrong.
//!
//! `MatchRoom._apply_damage` accumulates `damage_dealt` from `landed`, which is
//! **capped at the victim's remaining health**. The `HitMarker` it puts on the
//! wire in the same breath carries `round(amount)` — post-armour, but *not*
//! capped. So a 90-damage sniper hit on a player with 30 health left contributes
//! 30 to the server's figure and 90 to anything the client adds up. Over a match
//! that is a systematic overstatement, and two cards for the same match showing
//! two different numbers under one word is worse than one card with one fewer
//! number on it.
//!
//! What the client *can* count exactly is **hits landed** and **headshot kills**:
//! one hitmarker per pellet that connected, and `killed` set exactly once per
//! kill because `_shoot` skips a victim who is already dead. So the card shows
//! those, under their own names, and does not compete with a figure it cannot
//! reproduce.

use crate::hud::{OverlayVertex, Painter};
use crate::menu::{ACCENT, SCRIM, TEXT, TEXT_DIM};
use crate::protocol::HitMarker;

/// The card's own background, and the one thing about it that matters: it is
/// **opaque**.
///
/// It used to be `menu::PANEL_BG`, which is `0.96`. Four percent of a bright
/// glyph sounds like nothing, and it is not: the killfeed, the multi-kill
/// banner, the floating damage numbers and a held scoreboard all sit behind this
/// card, and every one of them came through it at 7-12% Weber contrast against
/// the panel. Brightness is not what gives it away — *structure* is. The eye
/// picks out readable words at a contrast where it would never notice a shade.
///
/// Its own constant rather than a bump to `PANEL_BG`, because that one is shared
/// with the pause menu and every HUD panel, and none of those asked for this.
/// The buy menu already sets its own alpha for the same reason, so a panel
/// choosing its own is the established shape here rather than a deviation.
///
/// The rule it comes from: **a panel that is glanced at may be translucent; a
/// panel that is read may not.** The scoreboard is looked at during a lull with
/// the game carrying on behind it. This is a card somebody stops to read.
const CARD_BG: [f32; 4] = [0.04, 0.05, 0.07, 1.0];

/// The card's width and height at scale 1, in window pixels.
const CARD_W: f32 = 620.0;
const CARD_H: f32 = 360.0;

/// The leave button, at scale 1.
const BUTTON_W: f32 = 200.0;
const BUTTON_H: f32 = 40.0;

/// How much of the window one card is, and the height it was drawn against.
///
/// **Everything here scales, and it scales through one function.** The HUD
/// derives its whole layout from `u = height / 360` for the reason this needs
/// the same treatment: a card sized in fixed pixels is comfortable on the
/// monitor it was written on, cramped at 720p and a postage stamp at 4K.
///
/// The clamp is what stops it becoming silly in either direction — a card that
/// grew without limit would eventually be the window, and one that shrank
/// without limit would be unreadable before it was small.
///
/// The pause menu does *not* do this and is a fixed 460px wide. That is a real
/// inconsistency and it is deliberately not being fixed here: it is a settings
/// list somebody reads for a while, not a card glanced at once, and changing it
/// would move every row rect the input path hit-tests against.
fn scale(height: f32) -> f32 {
    (height / 800.0).clamp(0.75, 2.6)
}

/// Running totals the wire does not carry, accumulated over a match.
///
/// Both are counted from **our own hitmarkers**, which is the one source that
/// exists in a match and on the range alike. See the module header for why
/// damage dealt is not among them.
#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub struct MatchTally {
    /// Every pellet that connected. A shotgun blast that lands six of eight is
    /// six, which is what makes this a measure of shooting rather than of
    /// trigger pulls.
    pub hits: u32,
    /// Kills that were headshots. `killed` is set exactly once per kill — the
    /// server skips a victim who is already dead — so this cannot double-count a
    /// shotgun that finished somebody with two pellets in the same tick.
    pub head_kills: u32,
}

impl MatchTally {
    pub fn note(&mut self, hits: &[HitMarker]) {
        for hit in hits {
            self.hits += 1;
            if hit.killed && hit.head {
                self.head_kills += 1;
            }
        }
    }

    /// A new match. Not called on respawn — a tally is for the whole match, and
    /// dying is part of one.
    pub fn reset(&mut self) {
        *self = MatchTally::default();
    }
}

/// Everything the card puts on screen, assembled by the caller.
///
/// A plain data struct rather than a borrow of the app: the layout below is then
/// testable without a socket, a roster or a window, which is the same split
/// `HudView` makes.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Summary {
    pub map: String,
    pub name: String,
    pub kills: i32,
    pub deaths: i32,
    pub tally: MatchTally,
    /// How many other players were in the room, bots included. Losing to a bot
    /// is losing, and a card that quietly excluded them would be flattering
    /// rather than true — the same call `result_for` makes.
    pub opponents: usize,
    /// Bombs planted or defused, flags captured. **Taken from
    /// `SelfState::objectives`, not counted here** — see that field for why this
    /// client has nothing to count it from.
    pub objectives: i32,
    /// Nobody outscored you.
    pub won: bool,
    /// Nobody equalled you either.
    pub mvp: bool,
    /// Whether this was a match at all — see [`Summary::is_recordable`].
    pub recordable: bool,
}

impl Summary {
    /// Whether this session was a match worth calling a result.
    ///
    /// The mirror of `results.is_recordable` on the node: somebody to play
    /// against, and something that happened. Without it, quitting a room you had
    /// just joined printed **TOP OF THE BOARD** — alone, `best` is `-1` and
    /// `0 >= -1` — which is the same bug the dashboard card had, arrived at
    /// independently by the same arithmetic.
    ///
    /// **The third term differs from the server's on purpose.** The node tests
    /// `damageDealt > 0`; this tests `tally.hits > 0`, because as the module
    /// header explains this client cannot reproduce `damage_dealt` (the wire's
    /// `HitMarker` is uncapped, the server's counter is capped at the victim's
    /// remaining health). They agree in every reachable case: armour absorbs a
    /// share and the remainder always goes through, so a hit that registered
    /// always cost somebody health. If that ever stops being true — a hit that
    /// lands for zero — this card and the dashboard's would begin to disagree
    /// about whether a match happened, which is why the claim is written down
    /// here rather than left to be rediscovered.
    ///
    /// **The fourth term is the one the first three could not cover.** All of
    /// them describe a *fight*, and in defuse the fight is not the game: a
    /// player who planted twice, was traded for both times by somebody else's
    /// shot and never landed one of their own has zero of each — and used to be
    /// told they had not played a match. Unlike `hits`, `objectives` is served
    /// rather than derived, so this term is identical on both sides by
    /// construction rather than by choosing the fixture's cases carefully.
    ///
    /// `opponents > 0` still gates it. Standing on a site alone until the bar
    /// fills is something you can do in an empty room, and an objective is not
    /// evidence of anybody to have played against.
    pub fn is_recordable(&self) -> bool {
        self.opponents > 0
            && (self.kills > 0 || self.deaths > 0 || self.tally.hits > 0 || self.objectives > 0)
    }

    /// The one word at the top of the card.
    ///
    /// `won` and `mvp` are relative to the room rather than to a team score,
    /// because deathmatch is the only mode — `MatchRoom.result_for` defines them
    /// the same way, and this client must not invent a second definition.
    ///
    /// A session that was never a match is called what it was. It is checked
    /// first because `won` and `mvp` are meaningless without an opponent, not
    /// merely unflattering.
    pub fn verdict(&self) -> &'static str {
        if !self.recordable {
            "NO CONTEST"
        } else if self.mvp {
            "MVP"
        } else if self.won {
            "TOP OF THE BOARD"
        } else {
            "MATCH OVER"
        }
    }

    /// Kills per death, as text.
    ///
    /// **Divided by deaths, not by `max(deaths, 1)` silently.** A player who
    /// never died has an undefined ratio, not a ratio equal to their kills, and
    /// printing the second is a number that is wrong rather than absent. It is
    /// shown as the kill count with no denominator instead.
    pub fn ratio(&self) -> String {
        if self.deaths <= 0 {
            format!("{}", self.kills.max(0))
        } else {
            format!("{:.2}", self.kills as f32 / self.deaths as f32)
        }
    }
}

/// The summary page, and whether it is up.
#[derive(Debug, Clone, Copy, Default)]
pub struct SummaryScreen {
    pub open: bool,
    /// Whether the pointer is over the leave button.
    hover: bool,
}

/// Where the leave button sits, in window pixels.
///
/// **The one place the button's geometry is defined**, used by the painter and
/// by the hit test alike. Two copies of this — one scaled, one not — is a button
/// that is drawn in one place and clicked in another, and it fails only at sizes
/// nobody develops at.
pub fn button_rect(width: f32, height: f32) -> (f32, f32, f32, f32) {
    let k = scale(height);
    let (cx, cy) = card_origin(width, height);
    (
        cx + (CARD_W - BUTTON_W - 24.0) * k,
        cy + (CARD_H - BUTTON_H - 22.0) * k,
        BUTTON_W * k,
        BUTTON_H * k,
    )
}

fn card_origin(width: f32, height: f32) -> (f32, f32) {
    let k = scale(height);
    ((width - CARD_W * k) / 2.0, (height - CARD_H * k) / 2.0)
}

impl SummaryScreen {
    pub fn open(&mut self) {
        self.open = true;
        self.hover = false;
    }

    pub fn close(&mut self) {
        self.open = false;
    }

    /// Track the pointer. Returns whether the hover state changed, so the caller
    /// can avoid a redraw that would change nothing.
    pub fn pointer(&mut self, x: f32, y: f32, width: f32, height: f32) -> bool {
        let was = self.hover;
        self.hover = Self::over_button(x, y, width, height);
        was != self.hover
    }

    /// Whether a click at this point leaves the match.
    pub fn hit(&self, x: f32, y: f32, width: f32, height: f32) -> bool {
        Self::over_button(x, y, width, height)
    }

    fn over_button(x: f32, y: f32, width: f32, height: f32) -> bool {
        let (bx, by, bw, bh) = button_rect(width, height);
        x >= bx && x <= bx + bw && y >= by && y <= by + bh
    }

    /// Draw the card.
    ///
    /// Appended to the overlay rather than clearing it, exactly as `Menu::build`
    /// is: the HUD is drawn first and this goes *over* it, so the scrim covers
    /// the crosshair instead of the crosshair showing through the card.
    pub fn build(&self, summary: &Summary, width: f32, height: f32, out: &mut Vec<OverlayVertex>) {
        if !self.open {
            return;
        }
        let mut p = Painter::new(out, width, height);
        p.rect(0.0, 0.0, width, height, SCRIM);

        let k = scale(height);
        let (cx, cy) = card_origin(width, height);
        p.rect(cx, cy, CARD_W * k, CARD_H * k, CARD_BG);
        // An accent along the top, the same rule the pause menu and every HUD
        // panel follow: the house style is a top edge, never a full perimeter.
        p.rect(cx, cy, CARD_W * k, 2.0 * k, ACCENT);

        p.text(
            cx + 24.0 * k,
            cy + 26.0 * k,
            3.0 * k,
            TEXT,
            summary.verdict(),
        );
        let subtitle = if summary.map.is_empty() {
            summary.name.to_uppercase()
        } else {
            format!(
                "{} - {}",
                summary.name.to_uppercase(),
                summary.map.to_uppercase()
            )
        };
        p.text(cx + 24.0 * k, cy + 60.0 * k, 1.4 * k, TEXT_DIM, &subtitle);

        // Two rows of three. A grid rather than a list because these are figures
        // to be compared at a glance, and a column of labelled lines is read one
        // line at a time.
        //
        // The last cell is the only one that changes: in a mode with objectives
        // it is what you did with them, and otherwise it is who was there. That
        // substitution rather than a seventh cell, because the grid is two rows
        // of three and a third row of one would sit where the leave button is.
        // OPPONENTS is the one it replaces because that figure is on the card to
        // explain **NO CONTEST** — a session with nobody in it — and a card
        // showing objectives is by construction not that card.
        let last = if summary.objectives > 0 {
            ("OBJECTIVES", summary.objectives.to_string())
        } else {
            ("OPPONENTS", summary.opponents.to_string())
        };
        let cells: [(&str, String); 6] = [
            ("KILLS", summary.kills.max(0).to_string()),
            ("DEATHS", summary.deaths.max(0).to_string()),
            ("K/D", summary.ratio()),
            ("HEADSHOTS", summary.tally.head_kills.to_string()),
            ("HITS", summary.tally.hits.to_string()),
            (last.0, last.1),
        ];
        let col_w = (CARD_W - 48.0) / 3.0 * k;
        for (i, (label, value)) in cells.iter().enumerate() {
            let col = (i % 3) as f32;
            let row = (i / 3) as f32;
            let x = cx + 24.0 * k + col * col_w;
            let y = cy + (108.0 + row * 92.0) * k;
            p.text(x, y, 1.3 * k, TEXT_DIM, label);
            p.text(x, y + 20.0 * k, 3.4 * k, TEXT, value);
        }

        let (bx, by, bw, bh) = button_rect(width, height);
        // The button is filled only on hover; at rest it is an outline, so the
        // card does not read as having already been dismissed.
        p.rect(
            bx,
            by,
            bw,
            bh,
            if self.hover {
                [ACCENT[0], ACCENT[1], ACCENT[2], 0.22]
            } else {
                [1.0, 1.0, 1.0, 0.05]
            },
        );
        p.rect(bx, by, bw, 2.0 * k, ACCENT);
        p.text(
            bx + 16.0 * k,
            by + bh / 2.0 - 6.0 * k,
            1.7 * k,
            TEXT,
            "LEAVE MATCH",
        );

        // Centred against the button rather than placed by its own constant, so
        // the two read as one row. Derived from `button_rect` for the same
        // reason the hit test is: a second copy of the button's geometry drifts
        // from the first the moment either is touched.
        p.text(
            cx + 24.0 * k,
            by + bh / 2.0 - 4.5 * k,
            1.3 * k,
            TEXT_DIM,
            "ENTER LEAVES - ESC GOES BACK",
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn hit(head: bool, killed: bool) -> HitMarker {
        HitMarker {
            victim: "bob".into(),
            damage: 20.0,
            head,
            killed,
        }
    }

    #[test]
    fn nothing_behind_the_card_can_be_read_through_it() {
        // The bug this pins is invisible in every other kind of test and nearly
        // invisible on screen: at `menu::PANEL_BG`'s 0.96 the killfeed, the
        // multi-kill banner, the floating damage numbers and a held scoreboard
        // all came through the card at 7-12% Weber contrast. Faint — but
        // *structured*, and the eye picks out readable words long before it
        // notices a shade.
        //
        // Asserted geometrically rather than against the constant, because
        // `assert_eq!(CARD_BG[3], 1.0)` would only restate the definition. This
        // asks the real question: is every point inside the card covered by
        // something opaque? It therefore also fails if the fill is ever moved,
        // shrunk, or drawn before the scrim instead of after it.
        let screen = SummaryScreen {
            open: true,
            hover: false,
        };
        let mut out = Vec::new();
        let (w, h) = (1280.0_f32, 800.0_f32);
        screen.build(&Summary::default(), w, h, &mut out);

        let ndc = |x: f32, y: f32| [x / w * 2.0 - 1.0, 1.0 - y / h * 2.0];
        let inside = |t: &[OverlayVertex], p: [f32; 2]| {
            let sign = |a: [f32; 2], b: [f32; 2]| {
                (p[0] - b[0]) * (a[1] - b[1]) - (a[0] - b[0]) * (p[1] - b[1])
            };
            let (d1, d2, d3) = (
                sign(t[0].position, t[1].position),
                sign(t[1].position, t[2].position),
                sign(t[2].position, t[0].position),
            );
            !((d1 < 0.0 || d2 < 0.0 || d3 < 0.0) && (d1 > 0.0 || d2 > 0.0 || d3 > 0.0))
        };

        let k = scale(h);
        let (cx, cy) = card_origin(w, h);
        // Sampled across the body rather than at one point: a fill that covered
        // the middle and not the corners would pass a single-point check and
        // still leak a killfeed into the card's bottom-left.
        for (fx, fy) in [
            (0.02, 0.04),
            (0.5, 0.04),
            (0.98, 0.04),
            (0.5, 0.5),
            (0.02, 0.98),
            (0.5, 0.98),
            (0.98, 0.98),
        ] {
            let point = ndc(cx + CARD_W * k * fx, cy + CARD_H * k * fy);
            let covered = out
                .chunks_exact(3)
                .any(|t| t[0].color[3] >= 1.0 && inside(t, point));
            assert!(
                covered,
                "the card is see-through at ({fx}, {fy}) of its body — anything                  the HUD draws there reads straight through it"
            );
        }

        // And the scrim is still translucent, because that is what dims the game
        // behind the card. An overlay opaque all the way out to the window edge
        // would not be a fix, it would be a different bug.
        assert!(
            out.chunks_exact(3)
                .any(|t| t[0].color[3] > 0.0 && t[0].color[3] < 1.0),
            "nothing translucent is drawn at all — the scrim is gone"
        );
    }

    #[test]
    fn an_objective_is_a_match_even_with_nothing_else_on_the_card() {
        // The case the first three terms could not describe: planted twice,
        // traded for both times by somebody else's shot, nothing landed. This
        // card used to read NO CONTEST.
        let summary = Summary {
            opponents: 4,
            objectives: 2,
            ..Summary::default()
        };
        assert!(summary.is_recordable());
    }

    #[test]
    fn an_objective_alone_in_a_room_is_still_not_a_match() {
        // `opponents` gates it and has to: standing on a site until the bar
        // fills is something you can do with nobody else there at all.
        let summary = Summary {
            opponents: 0,
            objectives: 3,
            ..Summary::default()
        };
        assert!(!summary.is_recordable());
    }

    #[test]
    fn the_last_cell_says_objectives_only_when_there_were_any() {
        // The substitution, asserted through the painted text rather than
        // through the branch — the grid is two rows of three and a seventh cell
        // would sit exactly where the leave button is, so this is the layout
        // constraint and not a preference.
        let screen = SummaryScreen {
            open: true,
            hover: false,
        };
        let painted = |objectives: i32| {
            let mut out = Vec::new();
            screen.build(
                &Summary {
                    opponents: 4,
                    objectives,
                    ..Summary::default()
                },
                1280.0,
                800.0,
                &mut out,
            );
            out.len()
        };
        // A different label is a different number of glyph quads, which is the
        // cheapest observable difference that does not require reading pixels.
        assert_ne!(painted(0), painted(3));
    }

    #[test]
    fn every_pellet_that_connected_is_a_hit() {
        // A measure of shooting, not of trigger pulls: a shotgun that lands six
        // of eight is six.
        let mut t = MatchTally::default();
        t.note(&vec![hit(false, false); 6]);
        assert_eq!(t.hits, 6);
        assert_eq!(t.head_kills, 0);
    }

    #[test]
    fn only_a_headshot_that_killed_counts_as_one() {
        // The server increments `head_kills` inside its `if killed` block. A
        // head *hit* that did not kill is not one, and counting it would make
        // this the one figure on the card that disagreed with the dashboard's.
        let mut t = MatchTally::default();
        t.note(&[hit(true, false), hit(false, true), hit(true, true)]);
        assert_eq!(t.head_kills, 1);
        assert_eq!(t.hits, 3);
    }

    #[test]
    fn a_tally_accumulates_across_ticks_and_resets_with_the_match() {
        let mut t = MatchTally::default();
        t.note(&[hit(true, true)]);
        t.note(&[hit(false, false)]);
        assert_eq!(t.hits, 2);
        assert_eq!(t.head_kills, 1);
        t.reset();
        assert_eq!(t, MatchTally::default());
    }

    #[test]
    fn a_player_who_never_died_has_no_ratio_rather_than_a_wrong_one() {
        // Dividing by `max(deaths, 1)` prints the kill count as though it were a
        // ratio — a number that is wrong, where absent would have been right.
        let s = Summary {
            kills: 7,
            deaths: 0,
            ..Default::default()
        };
        assert_eq!(s.ratio(), "7");
        let s = Summary {
            kills: 7,
            deaths: 2,
            ..Default::default()
        };
        assert_eq!(s.ratio(), "3.50");
    }

    #[test]
    fn the_verdict_follows_the_servers_own_definitions() {
        // `won` is "nobody outscored you" and `mvp` is "nobody equalled you" —
        // `MatchRoom.result_for`'s words. A second definition here would make
        // the two cards disagree about the same match.
        // Recordable, because `won`/`mvp` only mean anything once there was
        // somebody to beat — an unrecordable card says so instead, and that is
        // its own test below.
        let base = Summary {
            recordable: true,
            ..Summary::default()
        };
        assert_eq!(base.verdict(), "MATCH OVER");
        assert_eq!(
            Summary {
                won: true,
                ..base.clone()
            }
            .verdict(),
            "TOP OF THE BOARD"
        );
        assert_eq!(
            Summary {
                won: true,
                mvp: true,
                ..base
            }
            .verdict(),
            "MVP"
        );
    }

    #[test]
    fn an_empty_session_is_no_contest() {
        // The bug this exists for: alone in a room `best` is `-1`, so `0 >= -1`
        // congratulated a player for quitting. Both cards had it, arrived at
        // independently by the same arithmetic.
        let quit = Summary {
            opponents: 0,
            ..Summary::default()
        };
        assert!(!quit.is_recordable());
        assert_eq!(quit.verdict(), "NO CONTEST");
    }

    #[test]
    fn a_hit_is_enough_to_have_been_a_match() {
        // The native half of the predicate counts hits where the node counts
        // damage — see `is_recordable` for why the two agree.
        let brushed = Summary {
            opponents: 1,
            tally: MatchTally {
                hits: 1,
                ..MatchTally::default()
            },
            ..Summary::default()
        };
        assert!(brushed.is_recordable());
    }

    #[test]
    fn a_closed_screen_draws_nothing_at_all() {
        let mut out = Vec::new();
        SummaryScreen::default().build(&Summary::default(), 1280.0, 800.0, &mut out);
        assert!(out.is_empty());
    }

    #[test]
    fn an_open_screen_draws_the_card_over_the_game() {
        let mut screen = SummaryScreen::default();
        screen.open();
        let mut out = Vec::new();
        screen.build(&Summary::default(), 1280.0, 800.0, &mut out);
        assert!(!out.is_empty());
        // The scrim is the first thing in it, or the HUD underneath shows
        // through the card.
        assert_eq!(out[0].color, SCRIM);
    }

    #[test]
    fn the_leave_button_is_inside_the_card_at_every_size() {
        // The card is centred, so a small window is where a button placed by a
        // constant ends up off screen — and a button you cannot click is a page
        // with one way out instead of two.
        for (w, h) in [
            (1280.0, 720.0),
            (1280.0, 800.0),
            (1920.0, 1080.0),
            (3840.0, 2160.0),
        ] {
            let k = scale(h);
            let (bx, by, bw, bh) = button_rect(w, h);
            let (cx, cy) = card_origin(w, h);
            assert!(bx >= cx && bx + bw <= cx + CARD_W * k, "{w}x{h}: x {bx}");
            assert!(by >= cy && by + bh <= cy + CARD_H * k, "{w}x{h}: y {by}");
            // And the whole card is on the window, which the clamp is what
            // guarantees: unclamped, a tall enough screen makes it wider than
            // the screen is.
            assert!(
                cx >= 0.0 && cx + CARD_W * k <= w,
                "{w}x{h}: card off screen"
            );
            assert!(
                cy >= 0.0 && cy + CARD_H * k <= h,
                "{w}x{h}: card off screen"
            );
        }
    }

    #[test]
    fn the_button_only_answers_to_clicks_inside_it() {
        let mut screen = SummaryScreen::default();
        screen.open();
        let (w, h) = (1280.0, 800.0);
        let (bx, by, bw, bh) = button_rect(w, h);
        assert!(screen.hit(bx + bw / 2.0, by + bh / 2.0, w, h));
        assert!(!screen.hit(bx - 10.0, by + bh / 2.0, w, h));
        assert!(!screen.hit(w / 2.0, h - 4.0, w, h));
    }

    #[test]
    fn hovering_reports_only_a_real_change() {
        // The caller redraws on a change; reporting one every frame the pointer
        // moves inside the button would redraw for nothing.
        let mut screen = SummaryScreen::default();
        screen.open();
        let (w, h) = (1280.0, 800.0);
        let (bx, by, bw, bh) = button_rect(w, h);
        assert!(screen.pointer(bx + bw / 2.0, by + bh / 2.0, w, h));
        assert!(!screen.pointer(bx + bw / 2.0 + 1.0, by + bh / 2.0, w, h));
        assert!(screen.pointer(0.0, 0.0, w, h));
    }

    #[test]
    fn the_hovered_button_is_drawn_differently() {
        let summary = Summary::default();
        let draw = |hover: bool| {
            let mut screen = SummaryScreen::default();
            screen.open();
            if hover {
                let (bx, by, bw, bh) = button_rect(1280.0, 800.0);
                screen.pointer(bx + bw / 2.0, by + bh / 2.0, 1280.0, 800.0);
            }
            let mut out = Vec::new();
            screen.build(&summary, 1280.0, 800.0, &mut out);
            // Compared as colours: `OverlayVertex` is not `PartialEq`, and the
            // fill is the only thing hover changes anyway.
            out.iter().map(|v| v.color).collect::<Vec<_>>()
        };
        assert_ne!(draw(false), draw(true));
    }
}
