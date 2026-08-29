//! Items lying on the map.
//!
//! **A renderer for something the server already decided**, the same contract
//! `nades.rs` has and the same one the browser's `items.ts` has. Placements come
//! from `MapInfo` (the server resolved each item onto the floor beneath it — an
//! entity's `z` is the mapper's eye, not the ground), and *availability* comes
//! from the snapshot's `itemsOut`. Nothing here decides whether a pickup
//! happened: two players reaching the armour in the same tick is exactly the
//! case the server exists to settle.
//!
//! Shapes are built from primitives, like every other surface in this game:
//! AssaultCube's item models are its copyright and are never bundled, so a health
//! pack is two boxes in a cross and an armour plate is a bevelled slab. The
//! **colours** are the browser's, to the digit, and `tests/browser_parity.rs`
//! pins them — a health pack that is red in one client and orange in the other is
//! precisely the divergence that file exists to catch, and it is invisible from
//! either client alone.
//!
//! Two behaviours are load-bearing rather than decoration, and both are the
//! browser's:
//!
//! - **A taken item is drawn as absent, not deleted.** It sinks and fades, and
//!   its floor ring stays, dimmed. Players learn spawn timings off that ring, and
//!   an item that simply vanished would make the map's rhythm invisible — which
//!   is most of what makes an item worth fighting over.
//! - **One clock drives every item's bob**, so a whole map's items rise and fall
//!   together. Per-item phase looks livelier and reads as noise; in motion, a
//!   synchronised field is much easier to pick a *missing* item out of.
//!
//! ## Coordinates
//!
//! The wire and `MapInfo` are cube space — `x`, `y` horizontal, `z` up. The
//! renderer is `[x, z, y]`. The mapping happens **once**, where a vertex is
//! emitted, exactly as `nades.rs` and `bodies.rs` do it. Carrying two conventions
//! around in one module is how an item ends up drawn inside the floor.

use std::collections::HashSet;

use crate::api::ItemRow;
use crate::nades::{push_sphere, rgb};
use crate::renderer::Vertex;

/// Body colour per kind, matching `TINT` in the browser's `items.ts` exactly.
/// Warm for what heals, cool for what protects, brass for ammunition.
pub const TINT_HEALTH: u32 = 0xe4534a;
pub const TINT_HELMET: u32 = 0x6f97c4;
pub const TINT_ARMOUR: u32 = 0x4c7fd4;
pub const TINT_AMMO: u32 = 0xc9a227;
pub const TINT_CLIPS: u32 = 0xb08d2a;
pub const TINT_GRENADE: u32 = 0x5d6b45;

/// How high above the floor an item floats, in cubes. The browser's `HOVER`.
const HOVER: f32 = 0.9;
/// Half-amplitude of the bob, and its rate. The browser's `BOB` / `BOB_SPEED`.
const BOB: f32 = 0.18;
const BOB_SPEED: f32 = 1.6;
const SPIN_SPEED: f32 = 0.9;

/// Seconds an item takes to sink away when taken, and to pop back on return.
const FADE: f32 = 0.22;

/// The floor ring: inner and outer radius, and how many segments it is drawn in.
const RING_INNER: f32 = 0.55;
const RING_OUTER: f32 = 0.78;
const RING_SEGMENTS: usize = 20;
/// The ring sits just off the floor, or it z-fights with it.
const RING_LIFT: f32 = 0.03;

/// One item, and how present it currently is.
struct LiveItem {
    id: i32,
    kind: String,
    /// Cube-space position of the item's resting point, on the floor.
    at: [f32; 3],
    /// 1 = fully there, 0 = taken. Eased toward `wanted`, never snapped: the snap
    /// is what makes a respawn look like a rendering glitch.
    presence: f32,
    wanted: f32,
}

/// Every item on one map.
#[derive(Default)]
pub struct ItemField {
    items: Vec<LiveItem>,
    elapsed: f32,
}

impl ItemField {
    /// Adopt a map's placements. Called once, when the map is loaded.
    ///
    /// An unknown kind is **kept and drawn in a fallback colour** rather than
    /// dropped: a node that grows a seventh item type should leave this client
    /// showing something on the floor where one is, not a bare patch that reads
    /// as an empty spawn. It is reported once through `divergence`.
    pub fn place(rows: &[ItemRow]) -> ItemField {
        let mut items = Vec::with_capacity(rows.len());
        for row in rows {
            if tint(&row.kind).is_none() {
                crate::divergence::note_item_kind(&row.kind);
            }
            items.push(LiveItem {
                id: row.id,
                kind: row.kind.clone(),
                at: [row.x, row.y, row.z],
                presence: 1.0,
                wanted: 1.0,
            });
        }
        ItemField {
            items,
            elapsed: 0.0,
        }
    }

    /// Tell the field which items are currently gone.
    ///
    /// Takes an `Option` because the wire distinguishes **absent** from **empty**:
    /// a server that never sends `itemsOut` does not do items, and reading its
    /// silence as "none are gone" would pop every taken item back into existence
    /// once a tick.
    pub fn sync(&mut self, taken: Option<&Vec<i32>>) {
        let Some(taken) = taken else { return };
        let gone: HashSet<i32> = taken.iter().copied().collect();
        for item in &mut self.items {
            item.wanted = if gone.contains(&item.id) { 0.0 } else { 1.0 };
        }
    }

    /// Mark one item taken right now, from a `pickup` effect.
    ///
    /// The effect and the next snapshot say the same thing, and this exists only
    /// so the sink starts on the frame the pickup was announced rather than up to
    /// a tick later — at 20 Hz that difference is visible on an item somebody
    /// took in front of you.
    pub fn take(&mut self, id: i32) {
        if let Some(item) = self.items.iter_mut().find(|i| i.id == id) {
            item.wanted = 0.0;
        }
    }

    pub fn update(&mut self, dt: f32) {
        self.elapsed += dt;
        let step = (dt / FADE).min(1.0);
        for item in &mut self.items {
            item.presence += (item.wanted - item.presence) * step;
        }
    }

    pub fn is_empty(&self) -> bool {
        self.items.is_empty()
    }

    /// The items, as opaque triangles for the body pass.
    ///
    /// Opaque and in the same buffer as the players and grenades: an item is a
    /// small solid object and wants no pass of its own.
    pub fn vertices(&self, out: &mut Vec<Vertex>) {
        let bob = (self.elapsed * BOB_SPEED).sin() * BOB;
        let spin = self.elapsed * SPIN_SPEED;

        for item in &self.items {
            let colour = tint(&item.kind).unwrap_or(FALLBACK_TINT);
            // The ring never disappears: it is the timer players read the map
            // from, so it is drawn at every presence including zero, only dimmer.
            let ring_shade = 0.35 + item.presence * 0.4;
            push_ring(
                out,
                [item.at[0], item.at[1], item.at[2] + RING_LIFT],
                scale(rgb(colour), ring_shade),
            );

            if item.presence <= 0.02 {
                continue;
            }
            // Sinks into the floor as it goes rather than shrinking in place: an
            // item being *taken off the floor* is the thing being depicted.
            let z = item.at[2] + HOVER + bob - (1.0 - item.presence) * (HOVER + BOB);
            let size = 0.35 + item.presence * 0.65;
            push_shape(
                out,
                &item.kind,
                [item.at[0], item.at[1], z],
                size,
                spin,
                rgb(colour),
            );
        }
    }
}

/// Colour for a kind, or `None` for one this build has never heard of.
pub fn tint(kind: &str) -> Option<u32> {
    match kind {
        "health" => Some(TINT_HEALTH),
        "helmet" => Some(TINT_HELMET),
        "armour" => Some(TINT_ARMOUR),
        "ammo" => Some(TINT_AMMO),
        "clips" => Some(TINT_CLIPS),
        "grenade" => Some(TINT_GRENADE),
        _ => None,
    }
}

/// What an unrecognised kind is drawn in. Deliberately not one of the six, so it
/// reads as "something is here that this client does not know" rather than as a
/// health pack.
const FALLBACK_TINT: u32 = 0x9aa0a6;

fn scale(c: [f32; 3], k: f32) -> [f32; 3] {
    [c[0] * k, c[1] * k, c[2] * k]
}

/// One item's silhouette, from primitives.
///
/// The same six the browser draws. Exact vertex-for-vertex agreement between two
/// renderers is not a thing worth chasing — the colour is the identifying signal
/// and that *is* pinned — but the silhouettes match, so a player who learns to
/// read a map in one client can read it in the other.
fn push_shape(
    out: &mut Vec<Vertex>,
    kind: &str,
    at: [f32; 3],
    size: f32,
    spin: f32,
    colour: [f32; 3],
) {
    match kind {
        // A cross: the one item shape that needs no legend.
        "health" => {
            push_box(
                out,
                at,
                [0.9 * size, 0.28 * size, 0.28 * size],
                spin,
                colour,
            );
            push_box(
                out,
                at,
                [0.28 * size, 0.28 * size, 0.9 * size],
                spin,
                colour,
            );
        }
        // A dome. A squashed sphere rather than a true hemisphere: the flat
        // underside of one is never visible on a floating item, so the half that
        // would need a new primitive is the half nobody sees.
        "helmet" => push_sphere(
            out,
            at,
            [0.45 * size, 0.45 * size, 0.3 * size],
            10,
            6,
            colour,
        ),
        // A plate, wider than it is thick, so it reads as a vest end-on too.
        "armour" => push_box(out, at, [0.75 * size, 0.3 * size, 0.9 * size], spin, colour),
        "ammo" => push_box(out, at, [0.8 * size, 0.5 * size, 0.5 * size], spin, colour),
        "clips" => push_box(
            out,
            at,
            [0.45 * size, 0.28 * size, 0.6 * size],
            spin,
            colour,
        ),
        "grenade" => push_sphere(
            out,
            at,
            [0.24 * size, 0.24 * size, 0.41 * size],
            8,
            6,
            colour,
        ),
        _ => push_box(out, at, [0.5 * size; 3], spin, colour),
    }
}

/// An axis-aligned box in **cube** space, spun about the vertical, emitted in
/// render coordinates.
///
/// Wound counter-clockwise seen from outside, matching the world mesher and
/// `push_sphere` — the body pass culls back faces, so a box wound the other way
/// is invisible rather than inside-out.
fn push_box(out: &mut Vec<Vertex>, centre: [f32; 3], size: [f32; 3], spin: f32, colour: [f32; 3]) {
    let (hx, hy, hz) = (size[0] / 2.0, size[1] / 2.0, size[2] / 2.0);
    let (sin, cos) = spin.sin_cos();
    // Cube space: rotate about z, then map to render space at emission.
    let place = |p: [f32; 3]| -> [f32; 3] {
        let x = p[0] * cos - p[1] * sin;
        let y = p[0] * sin + p[1] * cos;
        [centre[0] + x, centre[2] + p[2], centre[1] + y]
    };
    let rotate_n = |n: [f32; 3]| -> [f32; 3] {
        let x = n[0] * cos - n[1] * sin;
        let y = n[0] * sin + n[1] * cos;
        [x, n[2], y]
    };

    let faces: [([f32; 3], [[f32; 3]; 4]); 6] = [
        (
            [1.0, 0.0, 0.0],
            [[hx, -hy, -hz], [hx, hy, -hz], [hx, hy, hz], [hx, -hy, hz]],
        ),
        (
            [-1.0, 0.0, 0.0],
            [
                [-hx, hy, -hz],
                [-hx, -hy, -hz],
                [-hx, -hy, hz],
                [-hx, hy, hz],
            ],
        ),
        (
            [0.0, 1.0, 0.0],
            [[hx, hy, -hz], [-hx, hy, -hz], [-hx, hy, hz], [hx, hy, hz]],
        ),
        (
            [0.0, -1.0, 0.0],
            [
                [-hx, -hy, -hz],
                [hx, -hy, -hz],
                [hx, -hy, hz],
                [-hx, -hy, hz],
            ],
        ),
        (
            [0.0, 0.0, 1.0],
            [[-hx, -hy, hz], [hx, -hy, hz], [hx, hy, hz], [-hx, hy, hz]],
        ),
        (
            [0.0, 0.0, -1.0],
            [
                [-hx, hy, -hz],
                [hx, hy, -hz],
                [hx, -hy, -hz],
                [-hx, -hy, -hz],
            ],
        ),
    ];
    for (normal, corners) in faces {
        let n = rotate_n(normal);
        for idx in [0usize, 1, 2, 0, 2, 3] {
            out.push(Vertex {
                position: place(corners[idx]),
                normal: n,
                color: colour,
            });
        }
    }
}

/// A flat annulus lying on the floor, in cube space.
///
/// Drawn face-up only: it sits on the ground, so the underside is inside the
/// floor and a second ring of triangles for it would be vertices nobody can ever
/// see.
fn push_ring(out: &mut Vec<Vertex>, centre: [f32; 3], colour: [f32; 3]) {
    let up = [0.0, 1.0, 0.0];
    let at = |angle: f32, r: f32| -> [f32; 3] {
        [
            centre[0] + angle.cos() * r,
            centre[2],
            centre[1] + angle.sin() * r,
        ]
    };
    for i in 0..RING_SEGMENTS {
        let a0 = (i as f32 / RING_SEGMENTS as f32) * std::f32::consts::TAU;
        let a1 = ((i + 1) as f32 / RING_SEGMENTS as f32) * std::f32::consts::TAU;
        let quad = [
            at(a0, RING_INNER),
            at(a1, RING_INNER),
            at(a1, RING_OUTER),
            at(a0, RING_OUTER),
        ];
        for idx in [0usize, 2, 1, 0, 3, 2] {
            out.push(Vertex {
                position: quad[idx],
                normal: up,
                color: colour,
            });
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn row(id: i32, kind: &str) -> ItemRow {
        ItemRow {
            id,
            kind: kind.to_string(),
            x: 10.0,
            y: 12.0,
            z: 3.0,
        }
    }

    /// Every vertex's y, which is *height* in render space.
    fn heights(verts: &[Vertex]) -> Vec<f32> {
        verts.iter().map(|v| v.position[1]).collect()
    }

    #[test]
    fn an_absent_items_out_is_not_the_same_as_an_empty_one() {
        // The bug this prevents: reading a server's silence as "none are gone"
        // pops every taken item back into existence once a tick.
        let mut field = ItemField::place(&[row(0, "health")]);
        field.sync(Some(&vec![0]));
        field.update(1.0);
        assert!(field.items[0].presence < 0.05, "not taken");

        field.sync(None);
        field.update(1.0);
        assert!(
            field.items[0].presence < 0.05,
            "an absent itemsOut brought a taken item back"
        );
    }

    #[test]
    fn a_taken_item_sinks_rather_than_vanishing() {
        let mut field = ItemField::place(&[row(0, "armour")]);
        field.sync(Some(&vec![0]));
        // One frame in: on its way out, not gone.
        field.update(1.0 / 60.0);
        assert!(field.items[0].presence < 1.0);
        assert!(field.items[0].presence > 0.5, "snapped instead of easing");
    }

    #[test]
    fn the_floor_ring_is_drawn_even_when_the_item_is_gone() {
        // Players learn spawn timings off the ring. An item that took its ring
        // with it would make the map's rhythm invisible, which is most of what
        // makes an item worth fighting over.
        let mut field = ItemField::place(&[row(0, "health")]);
        field.sync(Some(&vec![0]));
        field.update(10.0);
        let mut verts = Vec::new();
        field.vertices(&mut verts);
        assert_eq!(
            verts.len(),
            RING_SEGMENTS * 6,
            "a fully taken item should draw its ring and nothing else"
        );
    }

    #[test]
    fn a_present_item_draws_a_body_above_its_ring() {
        let mut field = ItemField::place(&[row(0, "health")]);
        field.update(0.0);
        let mut verts = Vec::new();
        field.vertices(&mut verts);
        let hs = heights(&verts);
        let top = hs.iter().cloned().fold(f32::MIN, f32::max);
        // The ring sits on the floor; the body floats a cube up.
        assert!(top > 3.0 + HOVER * 0.5, "the body is not above the floor");
    }

    #[test]
    fn an_unknown_kind_is_still_drawn() {
        // A node that grows a seventh item type should leave this client showing
        // *something* where one is: a bare patch of floor reads as an empty
        // spawn, which is worse than an unfamiliar box.
        let mut field = ItemField::place(&[row(0, "jetpack")]);
        field.update(0.0);
        let mut verts = Vec::new();
        field.vertices(&mut verts);
        assert!(
            verts.len() > RING_SEGMENTS * 6,
            "an unknown kind drew no body"
        );
    }

    #[test]
    fn a_pickup_effect_starts_the_sink_before_the_next_snapshot() {
        let mut field = ItemField::place(&[row(7, "ammo")]);
        field.take(7);
        field.update(1.0 / 60.0);
        assert!(field.items[0].presence < 1.0);
    }

    #[test]
    fn every_kind_the_server_can_place_has_a_colour() {
        // Mirrors `pickups.ITEMS` on the server. A kind added there and not here
        // still draws, but in the fallback grey — this is what says so.
        for kind in ["clips", "ammo", "grenade", "health", "helmet", "armour"] {
            assert!(tint(kind).is_some(), "no colour for '{kind}'");
        }
    }
}
