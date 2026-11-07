//! The native UI's palette, in one place.
//!
//! The HUD (`hud.rs`), the pause menu (`menu.rs`), the post-match card
//! (`summary.rs`), the drill card and chat used to each define their own
//! colours. A palette that lives in five files drifts: the menu's accent was
//! green while the browser's is blue, and nothing said which was the house
//! colour. These are the tokens; the modules name their own roles on top of
//! them.
//!
//! Values follow the browser client, the look reference
//! (`docs/architecture/hassault-two-clients.mdx`): panels are the browser's
//! `rgba(13,17,23,a)`, the accent is its theme's `--accent` `#6ea8fe`.
//!
//! The radar's colours are **not** here: they are pinned to the browser by a
//! shared fixture (`radar-vectors.json`) and live beside that test in
//! `radar.rs`.

/// `rgb(13,17,23)` — the browser HUD's panel colour — at a given opacity.
pub const fn panel(alpha: f32) -> [f32; 4] {
    [0.051, 0.067, 0.090, alpha]
}

/// Primary text and figures.
pub const TEXT: [f32; 4] = [0.92, 0.94, 0.96, 0.9];
/// Labels and secondary readouts.
pub const TEXT_DIM: [f32; 4] = [0.72, 0.76, 0.80, 0.65];
/// Structure that must be visible without being read: troughs, rules, spent
/// ticks.
pub const FAINT: [f32; 4] = [0.52, 0.57, 0.62, 0.40];

/// The house accent: the browser theme's `--accent`, `#6ea8fe`. A panel's 2 px
/// top edge, the selected row, the focused control.
pub const ACCENT: [f32; 4] = [0.431, 0.659, 0.996, 0.95];
/// "Yours": your row, your kill, your money.
pub const AMBER: [f32; 4] = [0.94, 0.83, 0.54, 0.95];
/// "You are losing something."
pub const RED: [f32; 4] = [0.97, 0.32, 0.28, 0.95];
/// "Over the line", "can afford", "owned".
pub const GREEN: [f32; 4] = [0.49, 0.91, 0.53, 0.95];

/// The faint grid some large panels carry, as a colour: barely there on purpose.
pub const GRID: [f32; 4] = [1.0, 1.0, 1.0, 0.035];
/// The grid's cell, in HUD units.
pub const GRID_CELL_UNITS: f32 = 6.0;

/// Darken behind a menu or card: the game is still running behind it.
pub const SCRIM: [f32; 4] = [0.0, 0.0, 0.0, 0.55];

/// `color` with its alpha multiplied by `k` — the one operation every fade is.
pub fn faded(color: [f32; 4], k: f32) -> [f32; 4] {
    [color[0], color[1], color[2], color[3] * k.clamp(0.0, 1.0)]
}

/// `color` at alpha `a`.
pub const fn with_alpha(color: [f32; 4], a: f32) -> [f32; 4] {
    [color[0], color[1], color[2], a]
}
