//! The training range: a gun that works when there is no server to ask.
//!
//! A port of `packages/core/src/modules/hassault/training.ts`, and it exists for
//! the same reason that file does. Training is where the movement is meant to be
//! *learnable* — the chained jump, the shoot-jump — and the shoot-jump cannot be
//! practised without a trigger. Offline there is nobody to send a `SelfState`, so
//! the range plays that part locally: it owns ammo, reloads, a handful of static
//! dummies, and the hitscan against them.
//!
//! **Nothing here is authoritative and none of it goes on a wire.** In a match
//! the server owns every one of these decisions and this file is not consulted.
//! That is also what lets it be this simple: no rewind, no lag compensation, no
//! validation, no budget — there is exactly one client and it is the only thing
//! that exists.
//!
//! Two shapes are deliberate, both inherited from the TS original:
//!
//! - **Targets are `PlayerRow`s.** The body builder already draws those, so a
//!   dummy is a body like any other rather than a second rendering path that
//!   would drift from the one people actually shoot at in matches.
//! - **`self_state()` returns the same `SelfState` a snapshot would have.** The
//!   HUD, the view model and the trigger then have no offline branch at all;
//!   they cannot tell which half of the game produced the state they are drawing.
//!   That is the whole trick, and it is why native Train now shows ammo, counts
//!   a magazine down, reloads, and lights a hitmarker.

use crate::api::WeaponSpec;
use crate::physics::{spawn_at, Spawn};
use crate::protocol::{HitMarker, PlayerRow, SelfState};
use crate::trace::{
    aim_vector, damage_at, eye_position, falloff_start, ray_hits_body, raycast_world,
    spread_vector, Vec3, BODY_HEIGHT, HEAD_BAND,
};
use crate::world::World;

/// How many dummies the range puts out, at most.
pub const MAX_TARGETS: usize = 6;

/// Seconds a downed target stays down before it stands back up.
pub const TARGET_RESPAWN: f32 = 3.0;

/// A target's health. Two body shots from the rifle, one from the sniper.
pub const TARGET_HP: f32 = 100.0;

#[derive(Debug, Clone, PartialEq)]
pub struct TargetHit {
    pub id: String,
    pub damage: f32,
    pub head: bool,
    pub killed: bool,
}

/// One trigger pull, resolved. `ends` carries an endpoint per pellet — a wall, a
/// body, or the end of the shot's range — because the caller wants to draw a
/// tracer whether or not anything was hit.
#[derive(Debug, Clone)]
pub struct RangeShot {
    pub origin: Vec3,
    pub ends: Vec<Vec3>,
    pub hits: Vec<TargetHit>,
}

#[derive(Debug, Clone)]
struct Target {
    id: String,
    name: String,
    x: f32,
    y: f32,
    z: f32,
    hp: f32,
    alive: bool,
    /// Seconds until it stands back up; only meaningful while down.
    down_for: f32,
}

/// A tiny xorshift, so a shotgun's cone does not need `rand` as a dependency.
///
/// The spread pattern is cosmetic *here* — nothing is being adjudicated — so the
/// bar is "different every pull", not statistical quality. A real generator would
/// be a crate in the tree for one call site.
#[derive(Debug, Clone)]
pub struct Rng(u32);

impl Default for Rng {
    fn default() -> Rng {
        Rng(0x9E3779B9)
    }
}

impl Rng {
    pub fn seeded(seed: u32) -> Rng {
        Rng(if seed == 0 { 1 } else { seed })
    }

    pub fn next_f32(&mut self) -> f32 {
        let mut x = self.0;
        x ^= x << 13;
        x ^= x >> 17;
        x ^= x << 5;
        self.0 = x;
        // 24 bits is a float's mantissa; taking all 32 would round to 1.0 at the
        // top of the range, and `sqrt(1.0)` puts a pellet exactly on the cone.
        (x >> 8) as f32 / 16_777_216.0
    }
}

#[derive(Default)]
pub struct TrainingRange {
    targets: Vec<Target>,
    weapons: Vec<WeaponSpec>,
    slot: usize,
    ammo: Vec<i32>,
    reserve: Vec<i32>,
    reload_in: f32,
    pending_hits: Vec<TargetHit>,
    rng: Rng,
}

impl TrainingRange {
    pub fn set_weapons(&mut self, specs: &[WeaponSpec], slot: usize) {
        self.weapons = specs.to_vec();
        self.slot = slot.min(specs.len().saturating_sub(1));
        self.ammo = specs.iter().map(|w| w.mag).collect();
        self.reserve = specs.iter().map(|w| w.reserve).collect();
        self.reload_in = 0.0;
    }

    /// Put dummies out on the map's own spawn points.
    ///
    /// Spawn points rather than anywhere clever: a bundled map guarantees every
    /// one of them is standable (that is exactly what `test_hassault_bundled`
    /// checks), so this cannot put a target inside a wall on a map it has never
    /// seen. Nearest first, and never the one the player is standing on.
    pub fn place(&mut self, world: &World, from_x: f32, from_y: f32) {
        let mut points: Vec<(f32, f32, f32, f32)> = world
            .spawns(None)
            .iter()
            .map(|e| {
                let placed = spawn_at(
                    world,
                    &Spawn {
                        x: e.x,
                        y: e.y,
                        z: e.z,
                        yaw: e.yaw,
                    },
                );
                let d = ((placed.x - from_x).powi(2) + (placed.y - from_y).powi(2)).sqrt();
                (placed.x, placed.y, placed.z, d)
            })
            // Two cubes is a body's width; anything nearer is the point we
            // spawned on, and a dummy standing inside you is not a target.
            .filter(|p| p.3 > 2.5)
            .collect();
        points.sort_by(|a, b| a.3.total_cmp(&b.3));
        points.truncate(MAX_TARGETS);

        self.targets = points
            .iter()
            .enumerate()
            .map(|(i, p)| Target {
                id: format!("dummy{i}"),
                name: format!("Dummy {}", i + 1),
                x: p.0,
                y: p.1,
                z: p.2,
                hp: TARGET_HP,
                alive: true,
                down_for: 0.0,
            })
            .collect();
    }

    /// Whether the range has anything to shoot at.
    pub fn populated(&self) -> bool {
        !self.targets.is_empty()
    }

    pub fn slot(&self) -> usize {
        self.slot
    }

    pub fn select(&mut self, slot: usize) {
        if slot < self.weapons.len() && slot != self.slot {
            self.slot = slot;
            // A switch cancels a reload, exactly as it does server-side —
            // otherwise the timer keeps running on a weapon you are no longer
            // holding and fills it while you are somewhere else.
            self.reload_in = 0.0;
        }
    }

    /// Start a reload, reporting whether one actually began.
    ///
    /// The boolean is what the caller plays a sound off: a reload keypress on a
    /// full magazine must not make the noise, or the sound stops meaning "that
    /// player is briefly unable to shoot back", which is the whole reason a
    /// reload is audible.
    pub fn request_reload_started(&mut self) -> bool {
        let before = self.reload_in;
        self.request_reload();
        self.reload_in > 0.0 && before <= 0.0
    }

    pub fn request_reload(&mut self) {
        let Some(weapon) = self.weapons.get(self.slot) else {
            return;
        };
        if weapon.mag <= 0 || self.reload_in > 0.0 {
            return;
        }
        if self.ammo[self.slot] >= weapon.mag || self.reserve[self.slot] == 0 {
            return;
        }
        self.reload_in = weapon.reload_time;
    }

    /// Advance reload timers and stand downed targets back up.
    pub fn update(&mut self, dt: f32) {
        if self.reload_in > 0.0 {
            self.reload_in -= dt;
            if self.reload_in <= 0.0 {
                self.reload_in = 0.0;
                self.finish_reload();
            }
        }
        for target in &mut self.targets {
            if target.alive {
                continue;
            }
            target.down_for -= dt;
            if target.down_for <= 0.0 {
                target.alive = true;
                target.hp = TARGET_HP;
            }
        }
    }

    fn finish_reload(&mut self) {
        let Some(weapon) = self.weapons.get(self.slot) else {
            return;
        };
        let want = weapon.mag - self.ammo[self.slot];
        let have = self.reserve[self.slot];
        // `-1` is unlimited and stays unlimited: decrementing it would turn the
        // sidearm's bottomless reserve into four billion rounds on the first
        // reload, which is the same bug in both clients if either forgets.
        let taken = if have < 0 { want } else { want.min(have) };
        self.ammo[self.slot] += taken;
        if have > 0 {
            self.reserve[self.slot] = have - taken;
        }
    }

    /// Whether the trigger would do anything: a weapon, and a round in it.
    pub fn can_fire(&self) -> bool {
        let Some(weapon) = self.weapons.get(self.slot) else {
            return false;
        };
        self.reload_in <= 0.0 && (weapon.mag <= 0 || self.ammo[self.slot] > 0)
    }

    /// Resolve one trigger pull against the world and the dummies.
    ///
    /// The same shape as the server's `resolve_shot`, and for the same reason:
    /// the caller wants endpoints whether or not anything was hit.
    #[allow(clippy::too_many_arguments)]
    pub fn fire(
        &mut self,
        world: &World,
        x: f32,
        y: f32,
        z: f32,
        eye: f32,
        yaw: f32,
        pitch: f32,
        scoped: i32,
    ) -> Option<RangeShot> {
        let weapon = self.weapons.get(self.slot)?.clone();
        if self.reload_in > 0.0 {
            return None;
        }
        if weapon.mag > 0 {
            if self.ammo[self.slot] <= 0 {
                return None;
            }
            self.ammo[self.slot] -= 1;
        }

        let origin = eye_position(x, y, z, eye);
        let direction = aim_vector(yaw, pitch);
        // The scope's whole mechanical effect, mirroring `effective_spread`
        // server-side: which cone this pull uses.
        let cone = if scoped > 0 {
            weapon.spread
        } else {
            weapon.hipfire_spread
        };
        let mut ends = Vec::new();
        let mut hits = Vec::new();

        for _ in 0..weapon.pellets.max(1) {
            let mut rng = std::mem::take(&mut self.rng);
            let dir = spread_vector(direction, cone, &mut || rng.next_f32());
            self.rng = rng;
            let wall = raycast_world(world, origin, dir, weapon.range);

            let mut best: Option<(f32, usize)> = None;
            for (i, target) in self.targets.iter().enumerate() {
                if !target.alive {
                    continue;
                }
                let Some(distance) = ray_hits_body(origin, dir, [target.x, target.y, target.z])
                else {
                    continue;
                };
                // A body behind a wall is not a target: the wall is nearer, and
                // this comparison is the whole of cover.
                if distance >= wall {
                    continue;
                }
                if best.is_none_or(|(d, _)| distance < d) {
                    best = Some((distance, i));
                }
            }

            let Some((distance, index)) = best else {
                ends.push([
                    origin[0] + dir[0] * wall,
                    origin[1] + dir[1] * wall,
                    origin[2] + dir[2] * wall,
                ]);
                continue;
            };

            let point = [
                origin[0] + dir[0] * distance,
                origin[1] + dir[1] * distance,
                origin[2] + dir[2] * distance,
            ];
            let target = &mut self.targets[index];
            // Relative to the top of the body, so the head is where the head is
            // on a crouched target too.
            let head = point[2] >= target.z + (BODY_HEIGHT - HEAD_BAND);
            let amount = damage_at(&weapon, distance, falloff_start(&weapon))
                * if head { weapon.head_multiplier } else { 1.0 };
            target.hp -= amount;
            let killed = target.hp <= 0.0;
            if killed {
                target.alive = false;
                target.down_for = TARGET_RESPAWN;
            }
            hits.push(TargetHit {
                id: target.id.clone(),
                damage: amount,
                head,
                killed,
            });
            ends.push(point);
        }

        self.pending_hits.extend(hits.iter().cloned());
        Some(RangeShot { origin, ends, hits })
    }

    /// What a snapshot would have told us about ourselves.
    ///
    /// Hitmarkers drain on read, exactly as the server drains them when it builds
    /// a private view — so each is shown once whichever half of the game produced
    /// it, and the HUD needs no offline branch to avoid a marker that never
    /// clears.
    pub fn self_state(&mut self) -> SelfState {
        let weapon = self.weapons.get(self.slot);
        let hits = self
            .pending_hits
            .drain(..)
            .map(|h| HitMarker {
                victim: h.id,
                damage: h.damage,
                head: h.head,
                killed: h.killed,
            })
            .collect();
        SelfState {
            // Nothing shoots back on the range, so this is always true: the
            // dummies are targets, not opponents, and a training death would
            // only interrupt.
            hp: 100.0,
            alive: true,
            weapon: self.slot as i32,
            ammo: self.ammo.get(self.slot).copied().unwrap_or(0),
            reserve: self.reserve.get(self.slot).copied().unwrap_or(0),
            mag: weapon.map(|w| w.mag).unwrap_or(0),
            reloading: self.reload_in > 0.0,
            reload_in: self.reload_in.max(0.0),
            hits,
            ..Default::default()
        }
    }

    /// The dummies as bodies the body builder can draw.
    pub fn rows(&self) -> Vec<PlayerRow> {
        self.targets
            .iter()
            .map(|t| PlayerRow {
                id: t.id.clone(),
                name: t.name.clone(),
                // Team 1 throughout: they render in the opposing colour, which is
                // the colour a thing you are meant to shoot at should be.
                team: 1,
                x: t.x,
                y: t.y,
                z: t.z,
                hp: t.hp.max(0.0),
                alive: t.alive,
                bot: true,
                ..Default::default()
            })
            .collect()
    }

    pub fn reset(&mut self) {
        self.targets.clear();
        self.pending_hits.clear();
        self.reload_in = 0.0;
        self.ammo = self.weapons.iter().map(|w| w.mag).collect();
        self.reserve = self.weapons.iter().map(|w| w.reserve).collect();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::api::{Entity, MapInfo};

    fn world_with_spawns(spawns: &[(f32, f32)]) -> World {
        let ssize = 64;
        let n = (ssize * ssize) as usize;
        let mut bytes = Vec::with_capacity(n * 9);
        bytes.extend(std::iter::repeat_n(2u8, n)); // SPACE throughout
        bytes.extend(std::iter::repeat_n(0u8, n)); // floor
        bytes.extend(std::iter::repeat_n(16u8, n)); // ceil
        bytes.extend(std::iter::repeat_n(0u8, n * 6));
        let info = MapInfo {
            ssize,
            cubic_size: n,
            plane_order: [
                "type", "floor", "ceil", "wtex", "ftex", "ctex", "vdelta", "utex", "tag",
            ]
            .iter()
            .map(|s| s.to_string())
            .collect(),
            entities: spawns
                .iter()
                .map(|(x, y)| Entity {
                    name: "playerstart".into(),
                    x: *x,
                    y: *y,
                    z: 12.0,
                    yaw: 0.0,
                    attrs: vec![0, 0],
                })
                .collect(),
            ..Default::default()
        };
        World::new(info, &bytes).expect("world")
    }

    fn rifle() -> WeaponSpec {
        WeaponSpec {
            id: "assault".into(),
            name: "Rifle".into(),
            damage: 60.0,
            range: 90.0,
            mag: 20,
            reserve: 60,
            reload_time: 1.5,
            pellets: 1,
            head_multiplier: 2.0,
            ..Default::default()
        }
    }

    fn sidearm() -> WeaponSpec {
        WeaponSpec {
            id: "pistol".into(),
            name: "Pistol".into(),
            damage: 35.0,
            range: 60.0,
            mag: 10,
            // The bottomless one.
            reserve: -1,
            reload_time: 1.0,
            pellets: 1,
            head_multiplier: 2.0,
            ..Default::default()
        }
    }

    pub(super) fn ranged() -> (World, TrainingRange) {
        let world = world_with_spawns(&[(10.0, 10.0), (30.0, 10.0), (50.0, 10.0)]);
        let mut range = TrainingRange::default();
        range.set_weapons(&[rifle(), sidearm()], 0);
        range.place(&world, 10.0, 10.0);
        (world, range)
    }

    #[test]
    fn dummies_stand_on_spawns_and_never_on_top_of_you() {
        let (_, range) = ranged();
        let rows = range.rows();
        // Three spawns, one of which is the one we are standing on.
        assert_eq!(rows.len(), 2);
        assert!(rows.iter().all(|r| r.bot && r.alive && r.team == 1));
        // Nearest first: x = 30 before x = 50.
        assert!(rows[0].x < rows[1].x);
        // Standing on the floor, not at the entity's absurd z.
        assert!(rows[0].z.abs() < 0.001, "placed at {}", rows[0].z);
    }

    #[test]
    fn a_shot_down_the_line_hits_the_nearest_dummy_and_spends_a_round() {
        let (world, mut range) = ranged();
        let shot = range
            .fire(&world, 10.0, 10.0, 0.0, 4.5, 0.0, 0.0, 0)
            .expect("fired");
        assert_eq!(shot.hits.len(), 1);
        assert_eq!(shot.hits[0].id, "dummy0");
        let you = range.self_state();
        assert_eq!(you.ammo, 19, "a round was not spent");
        // And the marker arrived exactly once.
        assert_eq!(you.hits.len(), 1);
        assert!(range.self_state().hits.is_empty(), "a marker repeated");
    }

    #[test]
    fn an_empty_magazine_refuses_the_trigger() {
        let (world, mut range) = ranged();
        for _ in 0..20 {
            assert!(range
                .fire(&world, 10.0, 10.0, 0.0, 4.5, 0.0, 0.0, 0)
                .is_some());
        }
        assert!(!range.can_fire());
        assert!(range
            .fire(&world, 10.0, 10.0, 0.0, 4.5, 0.0, 0.0, 0)
            .is_none());
        assert_eq!(range.self_state().ammo, 0);
    }

    #[test]
    fn a_reload_takes_time_and_then_fills_from_the_reserve() {
        let (world, mut range) = ranged();
        for _ in 0..5 {
            range.fire(&world, 10.0, 10.0, 0.0, 4.5, 0.0, 0.0, 0);
        }
        range.request_reload();
        assert!(range.self_state().reloading);
        // Mid-reload the trigger does nothing — the same rule the server keeps.
        assert!(!range.can_fire());
        range.update(1.6);
        let you = range.self_state();
        assert!(!you.reloading);
        assert_eq!(you.ammo, 20);
        assert_eq!(you.reserve, 55);
    }

    #[test]
    fn a_bottomless_reserve_stays_bottomless() {
        // The classic bug: decrementing `-1` turns the sidearm's unlimited
        // reserve into four billion rounds on the first reload.
        let (world, mut range) = ranged();
        range.select(1);
        range.fire(&world, 10.0, 10.0, 0.0, 4.5, 0.0, 0.0, 0);
        range.request_reload();
        range.update(1.1);
        let you = range.self_state();
        assert_eq!(you.ammo, 10);
        assert_eq!(you.reserve, -1);
    }

    #[test]
    fn switching_weapons_cancels_a_reload() {
        let (world, mut range) = ranged();
        range.fire(&world, 10.0, 10.0, 0.0, 4.5, 0.0, 0.0, 0);
        range.request_reload();
        range.select(1);
        assert!(!range.self_state().reloading, "the timer survived a switch");
        // And it did not quietly fill the weapon we walked away from.
        range.update(2.0);
        range.select(0);
        assert_eq!(range.self_state().ammo, 19);
    }

    #[test]
    fn a_level_shot_at_a_target_on_your_own_floor_is_a_headshot() {
        // Surprising and correct, and worth pinning because it decides how many
        // rounds a dummy takes: the eye sits at 4.5, a body is 5.2 tall, and the
        // head band is its top cube — 4.2 to 5.2. So shooting level at somebody
        // standing on the same floor hits their head. The server's `weapons.py`
        // and the browser's range agree; a client that "fixed" this would take
        // two rifle shots where a match takes one.
        let (world, mut range) = ranged();
        let shot = range
            .fire(&world, 10.0, 10.0, 0.0, 4.5, 0.0, 0.0, 0)
            .expect("fired");
        assert!(shot.hits[0].head);
        assert!((shot.hits[0].damage - 120.0).abs() < 0.01);
        assert!(shot.hits[0].killed, "60 × 2 is past a dummy's hundred");
    }

    #[test]
    fn a_downed_dummy_stands_back_up() {
        let (world, mut range) = ranged();
        range.fire(&world, 10.0, 10.0, 0.0, 4.5, 0.0, 0.0, 0);
        assert!(!range.rows()[0].alive);
        // A downed target is not a target: the next shot carries past it to the
        // one behind rather than spending itself on a body lying down.
        let past = range
            .fire(&world, 10.0, 10.0, 0.0, 4.5, 0.0, 0.0, 0)
            .expect("fired");
        assert_eq!(past.hits.len(), 1);
        assert_eq!(past.hits[0].id, "dummy1");

        range.update(TARGET_RESPAWN + 0.1);
        assert!(range.rows()[0].alive);
        assert_eq!(range.rows()[0].hp, TARGET_HP);
    }

    #[test]
    fn a_shot_into_the_floor_hits_nothing() {
        let (world, mut range) = ranged();
        let shot = range
            .fire(
                &world,
                10.0,
                10.0,
                0.0,
                4.5,
                0.0,
                -std::f32::consts::FRAC_PI_2,
                0,
            )
            .expect("fired");
        assert!(shot.hits.is_empty());
        // And it still reports where it stopped, because a tracer is drawn to it.
        assert_eq!(shot.ends.len(), 1);
        assert!((shot.ends[0][2] - 0.0).abs() < 0.01, "stopped mid-air");
    }

    #[test]
    fn the_state_it_hands_back_is_the_one_a_snapshot_would_have() {
        // The whole point of the file: the HUD and the trigger take one code
        // path in both halves of the game. If this drifts, native Train silently
        // grows an offline branch in every consumer.
        let (_, mut range) = ranged();
        let you = range.self_state();
        assert!(you.alive && you.hp == 100.0);
        assert_eq!(you.weapon, 0);
        assert_eq!(you.mag, 20);
        assert_eq!(you.ammo, 20);
        assert_eq!(you.respawn_in, 0.0);
    }

    #[test]
    fn the_generator_stays_inside_the_unit_interval() {
        // `sqrt(1.0)` would put a pellet exactly on the cone edge, and anything
        // above it outside — a shotgun whose spread is occasionally wrong by a
        // hair, which is not a bug anyone would ever find by looking.
        let mut rng = Rng::seeded(7);
        for _ in 0..10_000 {
            let v = rng.next_f32();
            assert!((0.0..1.0).contains(&v), "{v}");
        }
    }
}
