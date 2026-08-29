//! Damage numbers: what a hit was worth, floating off the body it landed on.
//!
//! **Nothing here invents a number.** Every figure drawn comes from a
//! `HitMarker` the server put in `you.hits` — the same per-recipient list the
//! hitmarker and the kill confirmation already read. This module decides *where*
//! to put it and how it fades, and that is all.
//!
//! ## The wire carries no position
//!
//! `HitMarker` has `victim`, `damage`, `head` and `killed`, and deliberately no
//! point of impact. So the anchor is the **victim's body**, looked up in the
//! interpolated roster the renderer is drawing this frame, and the number is
//! projected from there every frame rather than stamped once in screen space. A
//! number stamped once slides off the body the moment either of you moves, which
//! at these lifetimes is most of them.
//!
//! When the victim is *not* in the roster the number is drawn near the crosshair
//! instead. That is not a rare edge case: you kill somebody through smoke, the
//! kill removes them, and a shot at maximum range can outlive its target in the
//! snapshot buffer. Dropping the number there would make exactly the most
//! satisfying hits the silent ones.
//!
//! ## Why the projection lives here and not in `hud.rs`
//!
//! The HUD is a painter over a flat screen; it holds no matrices and should not
//! start. `Camera` is pure maths with no device in it, so this module can be
//! tested headless — which matters, because the two ways a projection goes
//! wrong (a point behind the eye, and a flipped vertical axis) both produce a
//! picture rather than an error.

use crate::camera::Camera;
use crate::protocol::HitMarker;
use crate::trace::BODY_HEIGHT;

/// How long a number stays up.
///
/// Short. It is a confirmation of something that already happened, and a screen
/// carrying two seconds of them during a firefight is a screen you cannot see
/// the firefight through.
const LIFE: f32 = 0.85;

/// How far a number rises over its life, as a fraction of the screen height.
///
/// Relative rather than in pixels, for the reason the whole HUD derives from
/// `u = height / 360`: a rise picked against 1080p is a twitch at 4K and a leap
/// at 720p.
const RISE: f32 = 0.055;

/// The most numbers alive at once.
///
/// A cap on what is *drawn*. Past this the oldest go, on the same reasoning
/// `EffectsPool` uses: the hit that just landed is the one worth seeing, and
/// dropping it to preserve one that is three frames from expiry would be
/// backwards.
const MAX_LIVE: usize = 16;

/// Where a number is pinned.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Anchor {
    /// The victim's body, in **cube** coordinates, at their feet. The rise is
    /// added on the way out; `BODY_HEIGHT` is added here so the number starts
    /// above the head rather than inside the chest.
    Body([f32; 3]),
    /// Near the crosshair, for a victim who is not in the roster this frame.
    Crosshair,
}

struct Live {
    anchor: Anchor,
    amount: f32,
    head: bool,
    killed: bool,
    age: f32,
    /// A small sideways offset so two numbers on the same body do not overprint.
    /// Deterministic per entry rather than random per frame, or the number would
    /// jitter instead of sitting still.
    drift: f32,
}

/// One number, placed on screen and ready to draw.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Placed {
    /// Screen pixels, top-left origin — the painter's own convention.
    pub x: f32,
    pub y: f32,
    /// Rounded, because a damage figure with a decimal in it is noise. The
    /// server rounds the same way before it sends it.
    pub amount: i32,
    pub head: bool,
    pub killed: bool,
    /// 1 at birth, 0 at the end. The caller multiplies its colour by this.
    pub fade: f32,
}

#[derive(Default)]
pub struct DamageNumbers {
    live: Vec<Live>,
    /// Advanced per entry so consecutive numbers drift opposite ways rather than
    /// stacking on one side.
    seq: u32,
}

impl DamageNumbers {
    /// File one tick's hitmarkers.
    ///
    /// **Summed per victim, not one number per marker.** A shotgun lands eight
    /// pellets in a single snapshot; eight numbers stacked on one body is
    /// unreadable, and worse, it reads as eight hits when it was one trigger
    /// pull. The same reasoning that gives `confirm_kill` one sound per tick.
    ///
    /// `locate` answers where a victim is, or `None` when they are not in the
    /// roster being drawn. It is a closure rather than a roster argument so this
    /// module never learns what a `PlayerRow` is — the caller already holds the
    /// interpolated one and the range holds a different shape entirely.
    pub fn push(&mut self, hits: &[HitMarker], locate: impl Fn(&str) -> Option<[f32; 3]>) {
        // Grouped by victim in arrival order. A `HashMap` would be tidier and
        // would also make the order of a multi-victim tick depend on a hash
        // seed, which is a difference nobody would ever see but which makes a
        // test's expected output a guess.
        let mut groups: Vec<(&str, f32, bool, bool)> = Vec::new();
        for hit in hits {
            match groups.iter_mut().find(|g| g.0 == hit.victim) {
                Some(group) => {
                    group.1 += hit.damage;
                    // A burst where one pellet found the head is a headshot, and
                    // one that killed is a kill: the louder of the two readings
                    // is the one worth showing, exactly as `on_hits` decides the
                    // hitmarker's colour.
                    group.2 |= hit.head;
                    group.3 |= hit.killed;
                }
                None => groups.push((&hit.victim, hit.damage, hit.head, hit.killed)),
            }
        }
        for (victim, amount, head, killed) in groups {
            let anchor = match locate(victim) {
                Some(at) => Anchor::Body(at),
                None => Anchor::Crosshair,
            };
            self.seq = self.seq.wrapping_add(1);
            // Alternating sides, widening slightly, so a run of hits on one
            // target fans out instead of piling up in a column.
            let step = (self.seq % 3) as f32;
            let side = if self.seq % 2 == 0 { 1.0 } else { -1.0 };
            self.push_one(Live {
                anchor,
                amount,
                head,
                killed,
                age: 0.0,
                drift: side * (0.012 + step * 0.010),
            });
        }
    }

    fn push_one(&mut self, entry: Live) {
        if self.live.len() >= MAX_LIVE {
            self.live.remove(0);
        }
        self.live.push(entry);
    }

    pub fn update(&mut self, dt: f32) {
        for n in &mut self.live {
            n.age += dt;
        }
        self.live.retain(|n| n.age < LIFE);
    }

    /// Everything gone at once — a respawn, or leaving a match.
    ///
    /// Numbers describe the fight that just ended. Carrying them across a death
    /// would float them over a body somewhere else entirely.
    pub fn clear(&mut self) {
        self.live.clear();
    }

    #[cfg(test)]
    pub fn count(&self) -> usize {
        self.live.len()
    }

    /// This frame's numbers, projected onto the screen.
    ///
    /// `camera` must be the camera the world is **drawn** with — the shaken copy,
    /// not the one the game runs on. A number projected through the true camera
    /// stays still while the world it is pinned to shakes underneath it, which
    /// reads as the number belonging to the HUD rather than to the body.
    pub fn placed(&self, camera: &Camera, width: u32, height: u32, out: &mut Vec<Placed>) {
        out.clear();
        let (w, h) = (width as f32, height as f32);
        let vp = camera.view_projection(width, height);
        for n in &self.live {
            let t = (n.age / LIFE).clamp(0.0, 1.0);
            // Held at full for the first half and then faded, so a number is
            // legible for long enough to read before it starts going.
            let fade = (1.0 - (t - 0.5) / 0.5).clamp(0.0, 1.0);
            let base = match n.anchor {
                Anchor::Crosshair => {
                    // Just off centre, so it does not sit on the crosshair
                    // itself — the one part of the screen that has to stay
                    // readable while it is being aimed.
                    (w * (0.5 + n.drift * 2.0), h * 0.46)
                }
                Anchor::Body(at) => {
                    // Cube (x, y, z-up) → render (x, height, y), the convention
                    // `Camera::eye` establishes and every mesher here follows.
                    let p = glam::Vec4::new(at[0], at[2] + BODY_HEIGHT, at[1], 1.0);
                    let clip = vp * p;
                    // **Behind the eye is not a small w, it is a negative one.**
                    // Dividing by it mirrors the point through the centre of the
                    // screen, so a hit on somebody behind you would draw a
                    // number in front of you — a picture, not an error. This is
                    // the whole reason the projection is testable.
                    if clip.w <= 1e-4 {
                        continue;
                    }
                    let ndc = clip.truncate() / clip.w;
                    if !ndc.x.is_finite() || !ndc.y.is_finite() {
                        continue;
                    }
                    (
                        (ndc.x * 0.5 + 0.5) * w + n.drift * w,
                        // Clip space is Y-**up** and the painter is Y-down.
                        // Without the flip every number appears mirrored about
                        // the horizon, which looks like a placement bug rather
                        // than an axis one.
                        (1.0 - (ndc.y * 0.5 + 0.5)) * h,
                    )
                }
            };
            out.push(Placed {
                x: base.0,
                y: base.1 - h * RISE * t,
                amount: n.amount.round() as i32,
                head: n.head,
                killed: n.killed,
                fade,
            });
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn hit(victim: &str, damage: f32, head: bool, killed: bool) -> HitMarker {
        HitMarker {
            victim: victim.into(),
            damage,
            head,
            killed,
        }
    }

    /// A camera at the origin looking down +x, which is the default's heading.
    fn cam() -> Camera {
        Camera {
            x: 0.0,
            y: 0.0,
            z: 2.0,
            ..Default::default()
        }
    }

    fn placed(n: &DamageNumbers, camera: &Camera) -> Vec<Placed> {
        let mut out = Vec::new();
        n.placed(camera, 1920, 1080, &mut out);
        out
    }

    #[test]
    fn a_shotgun_is_one_number_not_eight() {
        // Eight numbers stacked on one body is unreadable, and reads as eight
        // hits when it was one trigger pull.
        let mut n = DamageNumbers::default();
        let hits: Vec<HitMarker> = (0..8).map(|_| hit("bob", 9.0, false, false)).collect();
        n.push(&hits, |_| Some([10.0, 0.0, 0.0]));
        assert_eq!(n.count(), 1);
        assert_eq!(placed(&n, &cam())[0].amount, 72);
    }

    #[test]
    fn two_victims_in_one_tick_are_two_numbers() {
        let mut n = DamageNumbers::default();
        n.push(
            &[
                hit("bob", 20.0, false, false),
                hit("ann", 35.0, false, false),
            ],
            |_| Some([10.0, 0.0, 0.0]),
        );
        assert_eq!(n.count(), 2);
        let amounts: Vec<i32> = placed(&n, &cam()).iter().map(|p| p.amount).collect();
        assert_eq!(amounts, vec![20, 35]);
    }

    #[test]
    fn a_burst_that_finds_the_head_or_kills_is_marked_as_one() {
        // The louder reading wins, exactly as the hitmarker's colour is decided.
        let mut n = DamageNumbers::default();
        n.push(
            &[hit("bob", 9.0, false, false), hit("bob", 40.0, true, true)],
            |_| Some([10.0, 0.0, 0.0]),
        );
        let p = placed(&n, &cam());
        assert_eq!(p.len(), 1);
        assert!(p[0].head);
        assert!(p[0].killed);
        assert_eq!(p[0].amount, 49);
    }

    #[test]
    fn a_victim_absent_from_the_roster_is_drawn_near_the_crosshair() {
        // The kill through smoke: the kill removes them from the roster, so the
        // most satisfying hit in the game is the one with nowhere to anchor.
        // Dropping it would make exactly those silent.
        let mut n = DamageNumbers::default();
        n.push(&[hit("ghost", 55.0, true, true)], |_| None);
        let p = placed(&n, &cam());
        assert_eq!(p.len(), 1, "the number was dropped");
        assert!(
            (p[0].x - 960.0).abs() < 120.0,
            "not near the crosshair: {}",
            p[0].x
        );
        assert!(
            (p[0].y - 540.0).abs() < 120.0,
            "not near the crosshair: {}",
            p[0].y
        );
    }

    #[test]
    fn a_body_in_front_projects_near_the_centre_of_the_screen() {
        // Straight down the barrel. The tolerance is wide because `drift`
        // deliberately pushes a number off the column its body is on — up to
        // ~3% of the width — so this asserts "roughly the middle", not "exactly".
        // The axes themselves are pinned by the test below, which is the one
        // that would actually catch a transposed mapping.
        let mut n = DamageNumbers::default();
        n.push(&[hit("bob", 20.0, false, false)], |_| Some([25.0, 0.0, 0.0]));
        let p = placed(&n, &cam());
        assert_eq!(p.len(), 1);
        assert!((p[0].x - 960.0).abs() < 100.0, "x {}", p[0].x);
        // Above the middle, because the anchor is the top of the head and the
        // camera's eye is below it.
        assert!(p[0].y < 540.0, "y {} was not above the centre", p[0].y);
    }

    #[test]
    fn the_screen_axes_are_not_transposed_or_flipped() {
        // The real guard on the projection, and the reason this module is
        // testable at all. Cube `y` becomes render `z`, and clip space is Y-up
        // while the painter is Y-down; get either wrong and every number is
        // still drawn, just in the wrong place — a picture, never an error.
        //
        // The camera sits at the origin looking down +x. Cube +y is to the
        // **right** of that view, not the left: `apply_look` increases yaw when
        // the mouse goes right, and yaw 90 degrees faces cube +y
        // (`ninety_degrees_of_yaw_looks_along_cube_y`), so turning right walks
        // from +x toward +y. Cube +z is up.
        let at = |p: [f32; 3]| {
            let mut n = DamageNumbers::default();
            n.push(&[hit("bob", 20.0, false, false)], |_| Some(p));
            let out = placed(&n, &cam());
            assert_eq!(out.len(), 1, "{p:?} did not project at all");
            (out[0].x, out[0].y)
        };
        // Far enough apart that the drift (at most ~62px here) cannot flip the
        // comparison.
        let (right_x, _) = at([25.0, 8.0, 0.0]);
        let (left_x, _) = at([25.0, -8.0, 0.0]);
        assert!(
            left_x < right_x,
            "cube +y should be screen-right: {right_x} vs {left_x}"
        );

        let (_, high_y) = at([25.0, 0.0, 6.0]);
        let (_, low_y) = at([25.0, 0.0, -6.0]);
        assert!(
            high_y < low_y,
            "a body higher up should draw nearer the top: {high_y} vs {low_y}"
        );
    }

    #[test]
    fn a_body_behind_the_eye_is_not_drawn_mirrored_in_front_of_it() {
        // Negative `w`. Divide by it and the point flips through the centre of
        // the screen, so a hit on somebody behind you draws a number in front —
        // a picture, not an error, which is why it needs a test.
        let mut n = DamageNumbers::default();
        n.push(&[hit("bob", 20.0, false, false)], |_| {
            Some([-25.0, 0.0, 0.0])
        });
        assert_eq!(placed(&n, &cam()).len(), 0);
    }

    #[test]
    fn a_number_moves_up_the_screen_and_fades_out() {
        let mut n = DamageNumbers::default();
        n.push(&[hit("bob", 20.0, false, false)], |_| {
            Some([25.0, 0.0, 0.0])
        });
        let start = placed(&n, &cam())[0];
        assert_eq!(start.fade, 1.0, "faded before it was read");
        n.update(LIFE * 0.75);
        let later = placed(&n, &cam())[0];
        assert!(later.y < start.y, "it sank instead of rising");
        assert!(later.fade < 1.0 && later.fade > 0.0, "{}", later.fade);
    }

    #[test]
    fn numbers_expire_rather_than_accumulating() {
        let mut n = DamageNumbers::default();
        n.push(&[hit("bob", 20.0, false, false)], |_| {
            Some([25.0, 0.0, 0.0])
        });
        n.update(LIFE + 0.01);
        assert_eq!(n.count(), 0);
        assert!(placed(&n, &cam()).is_empty());
    }

    #[test]
    fn the_pool_drops_the_oldest_rather_than_refusing_the_newest() {
        let mut n = DamageNumbers::default();
        for i in 0..MAX_LIVE + 5 {
            n.push(
                &[hit(&format!("v{i}"), (i + 1) as f32, false, false)],
                |_| Some([25.0, 0.0, 0.0]),
            );
        }
        assert_eq!(n.count(), MAX_LIVE);
        let amounts: Vec<i32> = placed(&n, &cam()).iter().map(|p| p.amount).collect();
        let newest = (MAX_LIVE + 5) as i32;
        assert!(amounts.contains(&newest), "the newest was dropped");
    }

    #[test]
    fn hits_on_one_body_do_not_all_land_on_the_same_column() {
        // Two ticks against one target. Without the drift they overprint, and
        // 20 over 20 reads as one 20.
        let mut n = DamageNumbers::default();
        n.push(&[hit("bob", 20.0, false, false)], |_| {
            Some([25.0, 0.0, 0.0])
        });
        n.push(&[hit("bob", 20.0, false, false)], |_| {
            Some([25.0, 0.0, 0.0])
        });
        let p = placed(&n, &cam());
        assert_eq!(p.len(), 2);
        assert!((p[0].x - p[1].x).abs() > 1.0, "{} vs {}", p[0].x, p[1].x);
    }

    #[test]
    fn clearing_takes_everything_with_it() {
        let mut n = DamageNumbers::default();
        n.push(&[hit("bob", 20.0, false, false)], |_| {
            Some([25.0, 0.0, 0.0])
        });
        n.clear();
        assert_eq!(n.count(), 0);
    }

    #[test]
    fn an_empty_tick_files_nothing() {
        let mut n = DamageNumbers::default();
        n.push(&[], |_| Some([25.0, 0.0, 0.0]));
        assert_eq!(n.count(), 0);
    }
}
