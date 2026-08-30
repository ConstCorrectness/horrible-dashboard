//! Edit mode: flying a map you are building, and pointing at the thing to change.
//!
//! The other half of the map designer. `backend/modules/hassault/drafts.py` owns
//! the document, the history and the compile; this owns the **pointing** — where
//! the camera is, which brush the crosshair is on, and what a drag does to it.
//! The split is not arbitrary: a brush list is a thing you can only really judge
//! by standing in it, and a coordinate is a thing you can only really set by
//! typing it, so the console has the numbers and this has the crosshair.
//!
//! ## Why the editor is native
//!
//! Two reasons, and neither is preference. This is the only client with real
//! lighting and shadows, so a `light` entity can be authored where it actually
//! falls on a wall. And `geometry::build_world_mesh` is a flat loop over the grid
//! that finishes in single-digit milliseconds here, so an edit can rebuild the
//! *whole* map and still feel immediate — the browser's equivalent allocates JS
//! arrays for every vertex and would need chunking to do the same job.
//!
//! ## The edit loop
//!
//! A drag draws a **wireframe ghost** and nothing else: no request, no compile,
//! no mesh. On release it sends one `PATCH`, re-fetches the map, and rebuilds.
//! That is what keeps the whole thing honest — there is one compiler
//! (`mapsource.build`) and this never guesses what it would produce. A local
//! reimplementation would be a fourth copy of a rule this project already keeps
//! in three places, and it would fail the way those fail if they drift: silently,
//! with an editor showing a map the server will not serve.
//!
//! Free of the renderer and of winit, like `world.rs` and `geometry.rs`, so all
//! of it is testable with no GPU and no window.

use crate::api::{Entity, MapInfo};
use crate::effects::push_beam;
use crate::renderer::VolumeVertex;
use crate::trace::{aim_vector, raycast_world, Vec3};
use crate::world::World;

/// How far the crosshair reaches when picking. Beyond this a click selects
/// nothing rather than something across the map you cannot see well enough to
/// have meant.
pub const PICK_RANGE: f32 = 96.0;

/// Fly speed in cubes per second, and the range the scroll wheel moves it over.
pub const FLY_SPEED: f32 = 18.0;
pub const FLY_SPEED_MIN: f32 = 2.0;
pub const FLY_SPEED_MAX: f32 = 120.0;

/// Where the camera looks when edit mode opens: down the +y axis at a shallow
/// angle, so the whole map is in frame. Degrees, because `Camera` is degrees.
pub const START_YAW: f32 = 90.0;
pub const START_PITCH: f32 = -48.0;

/// How close to a cell boundary a hit has to be to count as landing on that
/// face. The pick point is derived from a distance, so it lands *on* the plane;
/// this is the slack for the arithmetic that put it there.
const FACE_EPSILON: f32 = 0.02;

/// `65535` in the owners payload means "no brush painted this cell". Unsigned
/// because the wire is `uint16`; the document says `-1`.
pub const OWNER_NONE: u16 = 65535;

/// Which surface the crosshair landed on. A drag means different things on each:
/// a wall face resizes that side, the floor moves the whole brush.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Face {
    Floor,
    Ceiling,
    /// A wall, with the outward normal in cell steps: (-1,0), (1,0), (0,-1), (0,1).
    Wall(i32, i32),
}

impl Face {
    /// Which edge of a rect this face is on, as an index into `[x, y, w, h]`
    /// semantics: 0 = -x side, 1 = +x side, 2 = -y side, 3 = +y side.
    pub fn wall_side(self) -> Option<usize> {
        match self {
            Face::Wall(-1, 0) => Some(0),
            Face::Wall(1, 0) => Some(1),
            Face::Wall(0, -1) => Some(2),
            Face::Wall(0, 1) => Some(3),
            _ => None,
        }
    }
}

/// What the crosshair is on.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Pick {
    pub cell: (i32, i32),
    pub face: Face,
    pub point: Vec3,
    pub distance: f32,
}

/// What is selected, and therefore what the keys and the console act on.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum Selection {
    #[default]
    None,
    Brush(usize),
    Entity(usize),
}

/// A free camera. No gravity, no collision, and **local only** — it never
/// reaches the wire, so the "deliberately no noclip on the server" rule is
/// untouched. There is no server here to lie to: edit mode has no socket, the
/// way Train has none.
/// **Position and speed only — no angles.** `Camera` already owns yaw and pitch,
/// in degrees, and the mouse-look path writes them. A second copy here would be
/// a second owner of the same two numbers, in different units, with a conversion
/// at every seam; the first one anybody forgot would be a camera that moved
/// somewhere other than where it was looking. So the angles are passed in.
#[derive(Debug, Clone, Copy)]
pub struct FlyCamera {
    pub x: f32,
    pub y: f32,
    pub z: f32,
    pub speed: f32,
}

impl Default for FlyCamera {
    fn default() -> FlyCamera {
        FlyCamera {
            x: 0.0,
            y: 0.0,
            z: 0.0,
            speed: FLY_SPEED,
        }
    }
}

impl FlyCamera {
    /// Above the middle of a map, far enough back to see all of it. The angles
    /// that go with this are `START_YAW` / `START_PITCH`, applied by the caller
    /// to the camera that owns them.
    pub fn overlooking(ssize: i32) -> FlyCamera {
        let mid = ssize as f32 / 2.0;
        FlyCamera {
            x: mid,
            y: mid * 0.35,
            z: ssize as f32 * 0.55,
            speed: FLY_SPEED,
        }
    }

    /// Move for one frame.
    ///
    /// `forward` is along the **aim**, pitch included, which is the difference
    /// between a fly camera and the walking one: a player's W is yaw-only
    /// because their feet are on the ground, and a camera that flew that way
    /// could never get to the ceiling without a separate control for height.
    /// `lift` is that separate control anyway, for moving straight up and down
    /// while looking level.
    #[allow(clippy::too_many_arguments)]
    pub fn fly(
        &mut self,
        yaw: f32,
        pitch: f32,
        forward: f32,
        strafe: f32,
        lift: f32,
        boost: bool,
        dt: f32,
    ) {
        let [fx, fy, fz] = aim_vector(yaw, pitch);
        // Strafe stays horizontal even when looking up: a right-hand vector that
        // tilted with pitch would roll the movement, and there is no roll here.
        let (sx, sy) = (-yaw.sin(), yaw.cos());
        let speed = self.speed * if boost { 3.0 } else { 1.0 } * dt;
        self.x += (fx * forward + sx * strafe) * speed;
        self.y += (fy * forward + sy * strafe) * speed;
        self.z += (fz * forward + lift) * speed;
    }

    /// The scroll wheel changes speed *multiplicatively*, so one notch does the
    /// same proportional thing whether you are inching along a wall or crossing
    /// the map.
    pub fn adjust_speed(&mut self, notches: f32) {
        self.speed = (self.speed * 1.15f32.powf(notches)).clamp(FLY_SPEED_MIN, FLY_SPEED_MAX);
    }

    pub fn eye(&self) -> Vec3 {
        [self.x, self.y, self.z]
    }
}

/// What the crosshair is on, or `None` if it reached nothing within `PICK_RANGE`.
///
/// Deliberately built on `raycast_world` rather than on a DDA of its own. That
/// function is already the authority on where a ray stops in this world — it is
/// what a shot uses — and a second traversal beside it would be a fifth copy of
/// the same grid walk, free to disagree with the one the game plays by. So this
/// asks it for the distance and reads the cell off the point.
pub fn pick(world: &World, origin: Vec3, direction: Vec3) -> Option<Pick> {
    let distance = raycast_world(world, origin, direction, PICK_RANGE);
    if distance >= PICK_RANGE {
        return None;
    }
    let point = [
        origin[0] + direction[0] * distance,
        origin[1] + direction[1] * distance,
        origin[2] + direction[2] * distance,
    ];
    // Step *back* along the ray for the cell, so the pick names the open cell the
    // surface belongs to rather than the rock behind it. A wall in this engine is
    // emitted from the open side only, so that is the side that owns it — and it
    // is the side a brush was painted on.
    let back = [
        point[0] - direction[0] * FACE_EPSILON,
        point[1] - direction[1] * FACE_EPSILON,
        point[2] - direction[2] * FACE_EPSILON,
    ];
    let cell = (back[0].floor() as i32, back[1].floor() as i32);

    let face = classify_face(world, cell, point, direction);
    Some(Pick {
        cell,
        face,
        point,
        distance,
    })
}

/// Which surface a hit point sits on.
///
/// Height first, then the wall the ray was travelling toward. Order matters: a
/// ray that grazes a floor exactly at a cell boundary is on the floor, and
/// calling it a wall would move a brush when the user meant to raise it.
fn classify_face(world: &World, cell: (i32, i32), point: Vec3, direction: Vec3) -> Face {
    let (cx, cy) = cell;
    if (point[2] - world.floor_at(cx, cy)).abs() <= FACE_EPSILON {
        return Face::Floor;
    }
    if (point[2] - world.ceil_at(cx, cy)).abs() <= FACE_EPSILON {
        return Face::Ceiling;
    }
    let fx = point[0] - cx as f32;
    let fy = point[1] - cy as f32;
    let to_x = fx.min(1.0 - fx);
    let to_y = fy.min(1.0 - fy);
    if to_x < to_y {
        Face::Wall(if direction[0] > 0.0 { 1 } else { -1 }, 0)
    } else {
        Face::Wall(0, if direction[1] > 0.0 { 1 } else { -1 })
    }
}

/// The brush that last painted a cell, from the `owners` payload.
///
/// Brushes compose by overwrite, so this is only knowable while they are being
/// applied — which is why the backend reports it rather than the client deriving
/// it. It is what turns "the crosshair is on that wall" into "that wall is brush
/// 7, and dragging it changes brush 7".
pub fn owner_at(owners: &[u16], ssize: i32, cell: (i32, i32)) -> Option<usize> {
    let (x, y) = cell;
    if x < 0 || y < 0 || x >= ssize || y >= ssize {
        return None;
    }
    let index = (y as usize) * (ssize as usize) + x as usize;
    match owners.get(index).copied() {
        Some(OWNER_NONE) | None => None,
        Some(owner) => Some(owner as usize),
    }
}

/// Decode the `uint16` owners payload. Length is checked against the grid rather
/// than trusted, the same rule `World::new` applies to the cube planes.
pub fn decode_owners(bytes: &[u8], cubic_size: usize) -> Option<Vec<u16>> {
    if bytes.len() < cubic_size * 2 {
        return None;
    }
    Some(
        bytes[..cubic_size * 2]
            .chunks_exact(2)
            .map(|pair| u16::from_le_bytes([pair[0], pair[1]]))
            .collect(),
    )
}

/// A rect as the document writes it: `[x, y, w, h]` in cells.
pub type Rect = [i32; 4];

/// Move a rect by whole cells, keeping it off the outer ring.
///
/// The clamp is not tidiness. `mapsource` refuses a brush that touches the
/// border rather than clipping it, because the physics reads out of bounds as
/// solid and that ring is the only thing keeping a player on the map — so a drag
/// that would produce a refused edit is stopped at the edge instead of making a
/// round trip to be told no.
pub fn move_rect(rect: Rect, dx: i32, dy: i32, ssize: i32) -> Rect {
    let [x, y, w, h] = rect;
    let max_x = ssize - 1 - w;
    let max_y = ssize - 1 - h;
    [
        (x + dx).clamp(1, max_x.max(1)),
        (y + dy).clamp(1, max_y.max(1)),
        w,
        h,
    ]
}

/// Drag one side of a rect. `side` is `Face::wall_side`'s index.
///
/// A side dragged past its opposite would invert the rect, and `mapsource`
/// refuses a rect with no area — so a side stops one cell short of collapsing
/// instead. Dragging *through* a brush to flip it is not a gesture anybody
/// means.
pub fn resize_rect(rect: Rect, side: usize, amount: i32, ssize: i32) -> Rect {
    let [mut x, mut y, mut w, mut h] = rect;
    match side {
        // The -x and -y edges move the origin and change the size together, or
        // the far edge would walk with them.
        0 => {
            let moved = (x + amount).clamp(1, x + w - 1);
            w -= moved - x;
            x = moved;
        }
        1 => w = (w + amount).clamp(1, ssize - 1 - x),
        2 => {
            let moved = (y + amount).clamp(1, y + h - 1);
            h -= moved - y;
            y = moved;
        }
        _ => h = (h + amount).clamp(1, ssize - 1 - y),
    }
    [x, y, w, h]
}

/// A rect from two corner cells, as a click-drag produces them.
pub fn rect_from_corners(a: (i32, i32), b: (i32, i32), ssize: i32) -> Rect {
    let x = a.0.min(b.0).clamp(1, ssize - 2);
    let y = a.1.min(b.1).clamp(1, ssize - 2);
    let far_x = a.0.max(b.0).clamp(1, ssize - 2);
    let far_y = a.1.max(b.1).clamp(1, ssize - 2);
    [x, y, far_x - x + 1, far_y - y + 1]
}

/// The twelve edges of a box over a rect, as line-segment pairs in render space
/// (x, height, y). What a drag draws while it is still a drag.
pub fn rect_wireframe(rect: Rect, floor: f32, ceil: f32) -> Vec<[f32; 3]> {
    let [x, y, w, h] = rect;
    let (x0, y0) = (x as f32, y as f32);
    let (x1, y1) = ((x + w) as f32, (y + h) as f32);
    let corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)];
    let mut lines = Vec::with_capacity(24);
    for level in [floor, ceil] {
        for index in 0..4 {
            let (ax, ay) = corners[index];
            let (bx, by) = corners[(index + 1) % 4];
            lines.push([ax, level, ay]);
            lines.push([bx, level, by]);
        }
    }
    for (cx, cy) in corners {
        lines.push([cx, floor, cy]);
        lines.push([cx, ceil, cy]);
    }
    lines
}

/// The edit that a finished drag becomes.
///
/// Returned rather than sent, so the gesture logic is testable without a network
/// and the caller owns the one place a request goes out.
#[derive(Debug, Clone, PartialEq)]
pub enum EditRequest {
    /// Replace a brush's rect. `brush.update` with a `rect` patch.
    Reshape {
        index: usize,
        rect: Rect,
    },
    /// Nudge a brush's floor or ceiling.
    Height {
        index: usize,
        field: &'static str,
        value: i32,
    },
    /// A new brush from a floor drag.
    Add {
        op: &'static str,
        rect: Rect,
    },
    /// Place an entity at a cell.
    Place {
        kind: String,
        cell: (i32, i32),
    },
    Remove {
        index: usize,
    },
}

/// A drag in progress. `None` between drags.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Drag {
    pub start: (i32, i32),
    pub current: (i32, i32),
    /// The brush being reshaped, and which of its sides. `None` means the drag
    /// is drawing a new rect on open floor rather than moving an existing one.
    pub brush: Option<(usize, Face)>,
}

/// Everything edit mode owns.
pub struct Editor {
    pub draft: String,
    pub camera: FlyCamera,
    pub selection: Selection,
    pub owners: Vec<u16>,
    pub brush_rects: Vec<Rect>,
    /// Each brush's op, parallel to `brush_rects`. Kept because the ops are not
    /// interchangeable: a `room` has a `floor`, a `stairs` has a `from`, and a
    /// `solid` has neither — it stores nothing but `wtex`, so asking the node to
    /// put a height on one is an edit that will be refused.
    pub brush_ops: Vec<String>,
    pub entities: Vec<Entity>,
    pub drag: Option<Drag>,
    pub hover: Option<Pick>,
    /// Cells the lint has complained about, drawn on the floor. The reason a
    /// live validator beats a test run: "2348 cells are cut off" is a number,
    /// and these same cells painted red are an answer.
    pub problem_cells: Vec<(i32, i32)>,
    pub status: String,
    /// What a click places when nothing is being dragged.
    pub place_kind: String,
    pub ssize: i32,
}

impl Editor {
    pub fn new(draft: String, info: &MapInfo) -> Editor {
        Editor {
            draft,
            camera: FlyCamera::overlooking(info.ssize),
            selection: Selection::None,
            owners: Vec::new(),
            brush_rects: Vec::new(),
            brush_ops: Vec::new(),
            entities: info.entities.clone(),
            drag: None,
            hover: None,
            problem_cells: Vec::new(),
            status: String::new(),
            place_kind: "playerstart".to_string(),
            ssize: info.ssize,
        }
    }

    /// Select whatever the crosshair is on.
    ///
    /// An entity close to the hit point wins over the brush behind it: you are
    /// pointing at the thing in front, and a spawn marker standing on a floor is
    /// always in front of that floor.
    pub fn select_at(&mut self, hit: &Pick) -> Selection {
        if let Some(index) = self.entity_near(hit.point) {
            self.selection = Selection::Entity(index);
        } else if let Some(index) = owner_at(&self.owners, self.ssize, hit.cell) {
            self.selection = Selection::Brush(index);
        } else {
            self.selection = Selection::None;
        }
        self.selection
    }

    /// The entity nearest a point, within a cell and a half of it.
    fn entity_near(&self, point: Vec3) -> Option<usize> {
        let mut best: Option<(usize, f32)> = None;
        for (index, entity) in self.entities.iter().enumerate() {
            // Entity coordinates are cell indices, so the centre is +0.5 —
            // the same offset `physics.ladders_from` uses to place one.
            let dx = entity.x + 0.5 - point[0];
            let dy = entity.y + 0.5 - point[1];
            let distance = (dx * dx + dy * dy).sqrt();
            if distance <= 1.5 && best.is_none_or(|(_, b)| distance < b) {
                best = Some((index, distance));
            }
        }
        best.map(|(index, _)| index)
    }

    pub fn begin_drag(&mut self, hit: &Pick) {
        let brush = owner_at(&self.owners, self.ssize, hit.cell).map(|index| (index, hit.face));
        self.drag = Some(Drag {
            start: hit.cell,
            current: hit.cell,
            brush,
        });
    }

    pub fn update_drag(&mut self, hit: &Pick) {
        if let Some(drag) = self.drag.as_mut() {
            drag.current = hit.cell;
        }
    }

    /// Finish a drag, producing the edit it means — or nothing, if it did not
    /// move. A click that selects should not also send an edit.
    pub fn end_drag(&mut self) -> Option<EditRequest> {
        let drag = self.drag.take()?;
        let dx = drag.current.0 - drag.start.0;
        let dy = drag.current.1 - drag.start.1;
        if dx == 0 && dy == 0 {
            return None;
        }
        match drag.brush {
            Some((index, face)) => {
                let rect = *self.brush_rects.get(index)?;
                let moved = match face.wall_side() {
                    // A wall drag resizes that side by however far the crosshair
                    // travelled along the axis the wall faces.
                    Some(side @ (0 | 1)) => resize_rect(rect, side, dx, self.ssize),
                    Some(side) => resize_rect(rect, side, dy, self.ssize),
                    // The floor or ceiling moves the whole brush.
                    None => move_rect(rect, dx, dy, self.ssize),
                };
                (moved != rect).then_some(EditRequest::Reshape { index, rect: moved })
            }
            None => Some(EditRequest::Add {
                op: "room",
                rect: rect_from_corners(drag.start, drag.current, self.ssize),
            }),
        }
    }

    /// The wireframe for whatever is being dragged or hovered, in render space.
    pub fn ghost(&self, world: &World) -> Vec<[f32; 3]> {
        let rect = match self.drag {
            Some(drag) => match drag.brush {
                Some((index, face)) => {
                    let rect = match self.brush_rects.get(index) {
                        Some(rect) => *rect,
                        None => return Vec::new(),
                    };
                    let dx = drag.current.0 - drag.start.0;
                    let dy = drag.current.1 - drag.start.1;
                    match face.wall_side() {
                        Some(side @ (0 | 1)) => resize_rect(rect, side, dx, self.ssize),
                        Some(side) => resize_rect(rect, side, dy, self.ssize),
                        None => move_rect(rect, dx, dy, self.ssize),
                    }
                }
                None => rect_from_corners(drag.start, drag.current, self.ssize),
            },
            None => match (self.selection, self.hover) {
                (Selection::Brush(index), _) => match self.brush_rects.get(index) {
                    Some(rect) => *rect,
                    None => return Vec::new(),
                },
                (_, Some(hit)) => [hit.cell.0, hit.cell.1, 1, 1],
                _ => return Vec::new(),
            },
        };
        let (cx, cy) = (rect[0] + rect[2] / 2, rect[1] + rect[3] / 2);
        let floor = world.floor_at(cx, cy);
        let ceil = world.ceil_at(cx, cy);
        rect_wireframe(rect, floor, ceil.max(floor + 1.0))
    }
}

// ---- drawing ----------------------------------------------------------------------
//
// Emitted into the translucent volume pass, the one water and blast effects
// already share, and out of the same `push_beam` the tracers use. A second beam
// builder would be two things that have to keep looking alike.

/// Ghost wireframe colour, and the colour of a selected brush.
const GHOST_COLOR: [f32; 3] = [0.35, 0.72, 1.0];
/// Lint errors on the floor. Red because it is the one thing here that means
/// "this map does not work", and it has to read as different from a selection.
const PROBLEM_COLOR: [f32; 3] = [0.95, 0.28, 0.22];
const GHOST_RADIUS: f32 = 0.06;

impl Editor {
    /// Everything edit mode draws, appended to the volume pass.
    pub fn overlay(&self, world: &World, out: &mut Vec<VolumeVertex>) {
        let lines = self.ghost(world);
        for pair in lines.chunks_exact(2) {
            push_beam(out, pair[0], pair[1], GHOST_RADIUS, GHOST_COLOR, 0.85);
        }
        // Lint cells, laid just above the floor they belong to. Slightly above,
        // not on it: coplanar with the floor they z-fight, and a warning that
        // flickers is one people learn to ignore.
        for &(cx, cy) in &self.problem_cells {
            let height = world.floor_at(cx, cy) + 0.06;
            let (x, y) = (cx as f32, cy as f32);
            push_beam(
                out,
                [x + 0.1, height, y + 0.5],
                [x + 0.9, height, y + 0.5],
                0.42,
                PROBLEM_COLOR,
                0.30,
            );
        }
        // A placed entity gets a marker, because most of them have no geometry
        // at all — a light is a number in a document until something draws it.
        for (index, entity) in self.entities.iter().enumerate() {
            let selected = self.selection == Selection::Entity(index);
            let color = entity_color(&entity.name);
            let (x, y) = (entity.x + 0.5, entity.y + 0.5);
            let base = world.floor_at(entity.x as i32, entity.y as i32);
            let top = base + if selected { 3.0 } else { 2.0 };
            push_beam(
                out,
                [x, base, y],
                [x, top, y],
                if selected { 0.16 } else { 0.1 },
                color,
                if selected { 0.95 } else { 0.6 },
            );
            // A light also shows the radius it actually reaches, which is the
            // only way to tune one without guessing.
            if entity.name == "light" {
                let radius = entity.attrs.first().copied().unwrap_or(32) as f32;
                push_ring(out, [x, base + 1.0, y], radius, color, 0.22);
            }
        }
    }
}

/// A flat ring of beams. Used for a light's reach; deliberately drawn on the
/// ground rather than as a sphere, because what a mapper is placing is a
/// footprint on the floor and walls, not a ball in the air.
fn push_ring(out: &mut Vec<VolumeVertex>, at: [f32; 3], radius: f32, color: [f32; 3], alpha: f32) {
    const SEGMENTS: usize = 24;
    for index in 0..SEGMENTS {
        let a = (index as f32 / SEGMENTS as f32) * std::f32::consts::TAU;
        let b = ((index + 1) as f32 / SEGMENTS as f32) * std::f32::consts::TAU;
        push_beam(
            out,
            [at[0] + a.cos() * radius, at[1], at[2] + a.sin() * radius],
            [at[0] + b.cos() * radius, at[1], at[2] + b.sin() * radius],
            0.08,
            color,
            alpha,
        );
    }
}

/// A marker colour per entity kind, so a map full of markers still reads.
///
/// Deliberately not the texture palette's hue step: these are not surfaces, and
/// two spawns being far apart in hue would be worse than both being team gold.
fn entity_color(name: &str) -> [f32; 3] {
    match name {
        "playerstart" => [0.85, 0.64, 0.25],
        "light" => [1.0, 0.94, 0.80],
        "ladder" => [0.55, 0.80, 0.45],
        "health" => [0.90, 0.30, 0.35],
        "armour" | "helmet" => [0.40, 0.62, 0.90],
        "ammo" | "clips" | "grenade" => [0.75, 0.72, 0.35],
        _ => [0.60, 0.60, 0.65],
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn flat_world(ssize: i32) -> World {
        // One open room with a solid border, built the way `mapsource` would.
        let n = (ssize * ssize) as usize;
        let mut planes = vec![0u8; n * 9];
        let (type_p, floor_p, ceil_p) = (0, n, 2 * n);
        for y in 0..ssize {
            for x in 0..ssize {
                let index = (y * ssize + x) as usize;
                let open = x > 0 && y > 0 && x < ssize - 1 && y < ssize - 1;
                planes[type_p + index] = if open { 4 } else { 0 };
                planes[floor_p + index] = 0;
                planes[ceil_p + index] = 16;
            }
        }
        let info = MapInfo {
            ssize,
            cubic_size: n,
            plane_order: vec![
                "type", "floor", "ceil", "wtex", "ftex", "ctex", "vdelta", "utex", "tag",
            ]
            .into_iter()
            .map(String::from)
            .collect(),
            ..MapInfo::default()
        };
        World::new(info, &planes).expect("test world")
    }

    #[test]
    fn flying_forward_follows_the_aim_including_pitch() {
        // The difference from the walking camera: a player's W is yaw-only
        // because their feet are on the ground, and a fly camera that moved that
        // way could never reach a ceiling.
        let mut camera = FlyCamera {
            x: 10.0,
            y: 10.0,
            z: 10.0,
            speed: 10.0,
        };
        camera.fly(0.0, std::f32::consts::FRAC_PI_2, 1.0, 0.0, 0.0, false, 1.0);
        assert!(camera.z > 19.0, "straight up, got z={}", camera.z);
        assert!((camera.x - 10.0).abs() < 0.01);
    }

    #[test]
    fn strafing_stays_horizontal_when_looking_up() {
        let mut camera = FlyCamera {
            speed: 10.0,
            ..FlyCamera::default()
        };
        camera.fly(0.0, -1.2, 0.0, 1.0, 0.0, false, 1.0);
        assert!((camera.z).abs() < 0.001, "strafe tilted, z={}", camera.z);
    }

    #[test]
    fn speed_steps_proportionally_and_stays_in_range() {
        let mut camera = FlyCamera::default();
        camera.adjust_speed(100.0);
        assert_eq!(camera.speed, FLY_SPEED_MAX);
        camera.adjust_speed(-1000.0);
        assert_eq!(camera.speed, FLY_SPEED_MIN);
    }

    #[test]
    fn looking_down_picks_the_floor_of_the_cell_underneath() {
        let world = flat_world(32);
        let hit = pick(&world, [10.5, 10.5, 8.0], [0.0, 0.0, -1.0]).expect("a floor");
        assert_eq!(hit.cell, (10, 10));
        assert_eq!(hit.face, Face::Floor);
    }

    #[test]
    fn looking_up_picks_the_ceiling() {
        let world = flat_world(32);
        let hit = pick(&world, [10.5, 10.5, 8.0], [0.0, 0.0, 1.0]).expect("a ceiling");
        assert_eq!(hit.face, Face::Ceiling);
    }

    #[test]
    fn a_wall_is_picked_from_the_open_side() {
        // The mesher emits a wall from the open side only, so that is the side
        // that owns it — and the side a brush was painted on. A pick that named
        // the rock behind would select the wrong brush every time.
        let world = flat_world(32);
        let hit = pick(&world, [10.5, 10.5, 8.0], [-1.0, 0.0, 0.0]).expect("a wall");
        assert_eq!(hit.cell, (1, 10), "the open cell, not the border");
        assert_eq!(hit.face, Face::Wall(-1, 0));
    }

    #[test]
    fn a_ray_that_reaches_nothing_picks_nothing() {
        let world = flat_world(32);
        assert!(pick(&world, [10.5, 10.5, 8.0], [0.0, 0.0, 0.0]).is_none());
    }

    #[test]
    fn owners_decode_and_none_is_none() {
        let bytes = [0u8, 0, 7, 0, 255, 255];
        let owners = decode_owners(&bytes, 3).expect("three cells");
        assert_eq!(owner_at(&owners, 3, (0, 0)), Some(0));
        assert_eq!(owner_at(&owners, 3, (1, 0)), Some(7));
        assert_eq!(
            owner_at(&owners, 3, (2, 0)),
            None,
            "65535 is untouched rock"
        );
        assert_eq!(owner_at(&owners, 3, (9, 9)), None, "off the grid");
    }

    #[test]
    fn a_short_owners_payload_is_refused_rather_than_read_past() {
        assert!(decode_owners(&[0u8, 0], 3).is_none());
    }

    #[test]
    fn moving_a_rect_stops_at_the_border_instead_of_making_a_refused_edit() {
        // `mapsource` refuses a brush touching the outer ring rather than
        // clipping it, so a drag that would produce one is stopped here.
        assert_eq!(move_rect([2, 2, 4, 4], -10, -10, 64), [1, 1, 4, 4]);
        assert_eq!(move_rect([2, 2, 4, 4], 100, 100, 64), [59, 59, 4, 4]);
    }

    #[test]
    fn resizing_a_side_never_collapses_the_rect() {
        // A rect with no area is refused by the compiler, and dragging a side
        // *through* its opposite to flip the brush is not a gesture anyone means.
        assert_eq!(resize_rect([10, 10, 4, 4], 1, -100, 64), [10, 10, 1, 4]);
        assert_eq!(resize_rect([10, 10, 4, 4], 0, 100, 64), [13, 10, 1, 4]);
        assert_eq!(resize_rect([10, 10, 4, 4], 3, -100, 64), [10, 10, 4, 1]);
    }

    #[test]
    fn dragging_the_near_edge_moves_the_origin_and_leaves_the_far_edge_alone() {
        let [x, _, w, _] = resize_rect([10, 10, 6, 6], 0, 2, 64);
        assert_eq!((x, x + w), (12, 16), "the far edge stayed at 16");
    }

    #[test]
    fn a_rect_from_two_corners_includes_both_of_them() {
        assert_eq!(rect_from_corners((10, 10), (13, 12), 64), [10, 10, 4, 3]);
        assert_eq!(rect_from_corners((13, 12), (10, 10), 64), [10, 10, 4, 3]);
    }

    #[test]
    fn a_wireframe_has_twelve_edges() {
        let lines = rect_wireframe([2, 3, 4, 5], 0.0, 8.0);
        assert_eq!(lines.len(), 24, "twelve segments, two points each");
        assert!(lines
            .iter()
            .all(|p| p[1] == 0.0 || p[1] == 8.0 || p[1] == 0.0));
    }

    fn editor_with(rects: Vec<Rect>, owners: Vec<u16>, ssize: i32) -> Editor {
        let info = MapInfo {
            ssize,
            ..MapInfo::default()
        };
        let mut editor = Editor::new("d1".into(), &info);
        editor.brush_rects = rects;
        editor.owners = owners;
        editor.ssize = ssize;
        editor
    }

    #[test]
    fn a_drag_that_did_not_move_is_a_selection_not_an_edit() {
        let mut editor = editor_with(vec![[4, 4, 8, 8]], vec![0; 64], 8);
        editor.begin_drag(&Pick {
            cell: (5, 5),
            face: Face::Floor,
            point: [5.5, 5.5, 0.0],
            distance: 1.0,
        });
        assert_eq!(editor.end_drag(), None);
    }

    #[test]
    fn dragging_a_floor_moves_the_whole_brush() {
        let mut editor = editor_with(vec![[4, 4, 8, 8]], vec![0; 1024], 32);
        editor.begin_drag(&Pick {
            cell: (5, 5),
            face: Face::Floor,
            point: [5.5, 5.5, 0.0],
            distance: 1.0,
        });
        editor.update_drag(&Pick {
            cell: (8, 7),
            face: Face::Floor,
            point: [8.5, 7.5, 0.0],
            distance: 1.0,
        });
        assert_eq!(
            editor.end_drag(),
            Some(EditRequest::Reshape {
                index: 0,
                rect: [7, 6, 8, 8]
            })
        );
    }

    #[test]
    fn dragging_a_wall_resizes_that_side_only() {
        let mut editor = editor_with(vec![[4, 4, 8, 8]], vec![0; 1024], 32);
        editor.begin_drag(&Pick {
            cell: (11, 6),
            face: Face::Wall(1, 0),
            point: [12.0, 6.5, 2.0],
            distance: 1.0,
        });
        editor.update_drag(&Pick {
            cell: (14, 6),
            face: Face::Wall(1, 0),
            point: [15.0, 6.5, 2.0],
            distance: 1.0,
        });
        assert_eq!(
            editor.end_drag(),
            Some(EditRequest::Reshape {
                index: 0,
                rect: [4, 4, 11, 8]
            })
        );
    }

    #[test]
    fn dragging_untouched_rock_draws_a_new_room() {
        let mut editor = editor_with(vec![], vec![OWNER_NONE; 1024], 32);
        editor.begin_drag(&Pick {
            cell: (5, 5),
            face: Face::Floor,
            point: [5.5, 5.5, 0.0],
            distance: 1.0,
        });
        editor.update_drag(&Pick {
            cell: (9, 8),
            face: Face::Floor,
            point: [9.5, 8.5, 0.0],
            distance: 1.0,
        });
        assert_eq!(
            editor.end_drag(),
            Some(EditRequest::Add {
                op: "room",
                rect: [5, 5, 5, 4]
            })
        );
    }

    #[test]
    fn an_entity_in_front_of_a_floor_is_what_gets_selected() {
        // You are pointing at the thing in front. A spawn marker standing on a
        // floor is always in front of that floor.
        let mut editor = editor_with(vec![[4, 4, 8, 8]], vec![0; 1024], 32);
        editor.entities = vec![Entity {
            name: "playerstart".into(),
            x: 6.0,
            y: 6.0,
            ..Entity::default()
        }];
        let hit = Pick {
            cell: (6, 6),
            face: Face::Floor,
            point: [6.5, 6.5, 0.0],
            distance: 1.0,
        };
        assert_eq!(editor.select_at(&hit), Selection::Entity(0));
    }

    #[test]
    fn pointing_at_a_floor_with_no_entity_on_it_selects_the_brush() {
        let mut editor = editor_with(vec![[4, 4, 8, 8]], vec![3; 1024], 32);
        let hit = Pick {
            cell: (6, 6),
            face: Face::Floor,
            point: [6.5, 6.5, 0.0],
            distance: 1.0,
        };
        assert_eq!(editor.select_at(&hit), Selection::Brush(3));
    }

    #[test]
    fn pointing_at_untouched_rock_selects_nothing() {
        let mut editor = editor_with(vec![], vec![OWNER_NONE; 1024], 32);
        let hit = Pick {
            cell: (6, 6),
            face: Face::Floor,
            point: [6.5, 6.5, 0.0],
            distance: 1.0,
        };
        assert_eq!(editor.select_at(&hit), Selection::None);
    }
}
