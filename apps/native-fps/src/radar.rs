//! The radar: what is on it, and the floor plan it is drawn over.
//!
//! **Which enemies appear is not this client's decision.** `you.spotted` is
//! resolved on the server by `MatchRoom.spotted_by`, because the answer depends
//! on two things only the server holds — the level's geometry and the smoke
//! standing in it. A client that decided for itself would be a wall hack with
//! extra steps, which is the same rule the noise mechanic follows and for the
//! same reason. This module *filters by team* and draws; it never judges
//! visibility.
//!
//! Everything here is deliberately outside `hud.rs` and free of the painter, so
//! the rule that decides who is on the radar can be tested without a window: the
//! drawing is decoration, and the filter is the part that would be a cheat if it
//! were wrong.
//!
//! ## Why the floor plan is a list of runs
//!
//! The browser rasterises the map into an offscreen canvas once and blits it
//! every frame (`Radar.tsx`), which it can do because a canvas has an image and
//! a transform. This client has neither: the overlay is triangles, rebuilt from
//! nothing each frame. Emitting one quad per open cell would be ~12,000 quads a
//! frame for the 110-cube span the browser shows — more geometry than the map
//! itself.
//!
//! So open cells are merged along each row into **runs**, computed once when the
//! world loads. A floor plan is mostly long corridors, so this collapses to a
//! few hundred segments. It also happens to solve the rotation problem for free:
//! the radar turns with the player, and a rotated axis-aligned rectangle is not
//! axis-aligned any more — but a run is a *segment*, and `Painter::line` already
//! draws an oriented thick quad.

use crate::protocol::PlayerRow;
use crate::world::World;

/// How many cubes fit across the radar. The browser's `SPAN`; anything outside
/// is clipped.
pub const SPAN: f32 = 110.0;

/// One horizontal run of open cells, in cube coordinates.
///
/// `x0..x1` is a half-open span along `y`, so a run of one cell is one cube
/// wide rather than zero.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Run {
    pub y: f32,
    pub x0: f32,
    pub x1: f32,
}

/// One body on the radar, already resolved to "should be drawn".
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Blip {
    pub x: f32,
    pub y: f32,
    /// Ours, which is what decides the colour and the size. There is no third
    /// state: a body that is neither is not on the radar at all.
    pub friendly: bool,
}

/// Merge the map's open cells into horizontal runs, once per map.
///
/// Built from `is_solid` rather than from the mesh, exactly as the browser does:
/// a radar wants the **floor plan** — where you cannot walk — and a mesh is a
/// set of surfaces, which is a different question with a much more expensive
/// answer.
///
/// Open cells are what gets painted, not solid ones. Painting the solid cube
/// instead gives a map that reads inside-out, and it is an easy mistake to make
/// because both produce a plausible-looking picture.
pub fn floor_plan(world: &World) -> Vec<Run> {
    let mut runs = Vec::new();
    for y in 0..world.ssize {
        let mut start: Option<i32> = None;
        for x in 0..world.ssize {
            let open = !world.is_solid(x, y);
            match (open, start) {
                (true, None) => start = Some(x),
                (false, Some(x0)) => {
                    runs.push(Run {
                        y: y as f32 + 0.5,
                        x0: x0 as f32,
                        x1: x as f32,
                    });
                    start = None;
                }
                _ => {}
            }
        }
        // A row that is open all the way to the edge has no closing cell to end
        // it. Forgetting this drops the last run of every such row, which on an
        // open map is most of the floor plan and reads as a map with a chunk
        // missing on one side.
        if let Some(x0) = start {
            runs.push(Run {
                y: y as f32 + 0.5,
                x0: x0 as f32,
                x1: world.ssize as f32,
            });
        }
    }
    runs
}

/// Who belongs on the radar this frame.
///
/// Two rules, and the asymmetry between them is the whole thing:
///
/// - **Teammates are unconditional.** That is a radio, not a sensor, and every
///   team shooter works this way. They are deliberately *not* in `spotted` —
///   sending a per-player id list that never changes would be twenty bytes a
///   tick to say something already known.
/// - **An enemy needs to have been seen by somebody on our side**, which is
///   precisely what `spotted` is. Never by us specifically: the point of the
///   mechanic is that a teammate looking down a corridor paints it for the whole
///   team.
///
/// The dead are dropped. A corpse on the radar is a body that is not there, and
/// "an enemy is standing here" is the one thing a radar must not get wrong.
pub fn blips(rows: &[PlayerRow], self_id: &str, my_team: i32, spotted: &[String]) -> Vec<Blip> {
    rows.iter()
        .filter(|row| row.id != self_id && row.alive)
        .filter_map(|row| {
            let friendly = row.team == my_team;
            if !friendly && !spotted.iter().any(|id| id == &row.id) {
                return None;
            }
            Some(Blip {
                x: row.x,
                y: row.y,
                friendly,
            })
        })
        .collect()
}

/// Clip a segment to a circle centred on the origin.
///
/// Returns the portion inside, or `None` when the segment misses entirely.
///
/// Needed because there is no clip here. The browser gets a circular radar from
/// `ctx.clip()`; a triangle painter has to cut the geometry itself, and a floor
/// plan drawn without cutting is a **square** minimap with a ring drawn hopefully
/// around it — corners of the map spilling past the edge of the instrument.
pub fn clip_to_circle(
    (x0, y0): (f32, f32),
    (x1, y1): (f32, f32),
    radius: f32,
) -> Option<((f32, f32), (f32, f32))> {
    let (dx, dy) = (x1 - x0, y1 - y0);
    let a = dx * dx + dy * dy;
    if a <= f32::EPSILON {
        // A degenerate segment is a point: inside or not, with no span to cut.
        return (x0 * x0 + y0 * y0 <= radius * radius).then_some(((x0, y0), (x1, y1)));
    }
    let b = 2.0 * (x0 * dx + y0 * dy);
    let c = x0 * x0 + y0 * y0 - radius * radius;
    let disc = b * b - 4.0 * a * c;
    if disc < 0.0 {
        return None;
    }
    let root = disc.sqrt();
    // The parametric span inside the circle, intersected with the segment's own
    // 0..1. Clamping rather than testing is what keeps a segment that starts
    // inside and leaves — the common case — from being discarded.
    let t0 = ((-b - root) / (2.0 * a)).max(0.0);
    let t1 = ((-b + root) / (2.0 * a)).min(1.0);
    if t1 <= t0 {
        return None;
    }
    Some(((x0 + dx * t0, y0 + dy * t0), (x0 + dx * t1, y0 + dy * t1)))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn row(id: &str, team: i32, alive: bool) -> PlayerRow {
        PlayerRow {
            id: id.into(),
            team,
            alive,
            x: 10.0,
            y: 20.0,
            ..Default::default()
        }
    }

    #[test]
    fn a_teammate_is_on_the_radar_without_being_spotted() {
        // The radio rule. Requiring `spotted` for teammates would make the radar
        // useless for the one thing people actually use it for.
        let rows = [row("mate", 0, true)];
        let out = blips(&rows, "me", 0, &[]);
        assert_eq!(out.len(), 1);
        assert!(out[0].friendly);
    }

    #[test]
    fn an_enemy_appears_only_once_the_server_says_they_are_spotted() {
        let rows = [row("enemy", 1, true)];
        assert!(
            blips(&rows, "me", 0, &[]).is_empty(),
            "an unspotted enemy on the radar is a wall hack"
        );
        let out = blips(&rows, "me", 0, &["enemy".to_string()]);
        assert_eq!(out.len(), 1);
        assert!(!out[0].friendly);
    }

    #[test]
    fn spotted_is_the_teams_answer_and_not_ours() {
        // The id list is resolved per *team* on the server. Nothing here may
        // narrow it to "what I personally can see" — a teammate looking down a
        // corridor painting it for everyone is the entire mechanic.
        let rows = [row("enemy", 1, true)];
        let out = blips(&rows, "me", 0, &["enemy".to_string()]);
        assert_eq!(out.len(), 1, "we are not required to see them ourselves");
    }

    #[test]
    fn the_dead_and_ourselves_are_not_drawn() {
        let rows = [row("me", 0, true), row("mate", 0, false)];
        assert!(
            blips(&rows, "me", 0, &[]).is_empty(),
            "our own arrow is drawn at the centre, and a corpse is not a body"
        );
    }

    #[test]
    fn a_spotted_id_for_a_body_that_is_gone_draws_nothing() {
        // The roster and `spotted` are built in the same tick, but a client may
        // hold a snapshot where one has moved on. Iterating the roster and
        // consulting `spotted` — rather than the reverse — means a stale id is
        // simply not found, instead of a blip at a position nobody supplied.
        let rows = [row("mate", 0, true)];
        let out = blips(&rows, "me", 0, &["ghost".to_string()]);
        assert_eq!(out.len(), 1, "only the teammate");
    }

    fn open_world(ssize: i32) -> World {
        // A solid border with an open interior — the shape every real map has.
        World::test_box(ssize)
    }

    #[test]
    fn the_floor_plan_covers_the_open_cells_and_not_the_walls() {
        let world = open_world(8);
        let runs = floor_plan(&world);
        assert!(!runs.is_empty());
        for run in &runs {
            assert!(run.x1 > run.x0, "a run must have width");
            for x in (run.x0 as i32)..(run.x1 as i32) {
                assert!(
                    !world.is_solid(x, run.y as i32),
                    "run at y={} covers solid cell x={x}",
                    run.y
                );
            }
        }
        // Every open cell is inside exactly one run: a floor plan with holes in
        // it reads as a map with rooms that do not exist.
        for y in 0..world.ssize {
            for x in 0..world.ssize {
                if world.is_solid(x, y) {
                    continue;
                }
                let covered = runs
                    .iter()
                    .filter(|r| r.y as i32 == y && (r.x0 as i32..r.x1 as i32).contains(&x))
                    .count();
                assert_eq!(covered, 1, "cell {x},{y} is in {covered} runs");
            }
        }
    }

    #[test]
    fn a_row_open_to_the_edge_still_ends_its_run() {
        // The off-by-one that drops the last run of every row with no closing
        // wall. On an open map that is most of the floor plan.
        let world = World::test_open(4);
        let runs = floor_plan(&world);
        assert_eq!(runs.len(), 4, "one full-width run per row");
        assert_eq!(runs[0].x0, 0.0);
        assert_eq!(runs[0].x1, 4.0);
    }

    #[test]
    fn a_segment_crossing_the_circle_is_cut_at_the_rim() {
        let (a, b) = clip_to_circle((-10.0, 0.0), (10.0, 0.0), 5.0).unwrap();
        assert!((a.0 + 5.0).abs() < 1e-4, "{a:?}");
        assert!((b.0 - 5.0).abs() < 1e-4, "{b:?}");
    }

    #[test]
    fn a_segment_that_starts_inside_and_leaves_keeps_its_start() {
        // The common case, and the one a naive "both ends outside?" test throws
        // away: most runs begin under the player and run off the edge.
        let (a, b) = clip_to_circle((0.0, 0.0), (20.0, 0.0), 5.0).unwrap();
        assert_eq!(a, (0.0, 0.0));
        assert!((b.0 - 5.0).abs() < 1e-4, "{b:?}");
    }

    #[test]
    fn a_segment_that_misses_entirely_is_dropped() {
        assert!(clip_to_circle((-10.0, 9.0), (10.0, 9.0), 5.0).is_none());
        // Collinear with the centre but entirely beyond it, which is the case a
        // discriminant test alone lets through: the *line* hits the circle, the
        // *segment* does not.
        assert!(clip_to_circle((6.0, 0.0), (20.0, 0.0), 5.0).is_none());
    }

    #[test]
    fn a_segment_wholly_inside_is_returned_untouched() {
        let (a, b) = clip_to_circle((-1.0, 0.0), (1.0, 0.0), 5.0).unwrap();
        assert_eq!(a, (-1.0, 0.0));
        assert_eq!(b, (1.0, 0.0));
    }
}
