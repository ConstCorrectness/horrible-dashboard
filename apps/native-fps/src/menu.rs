//! The pause menu: Escape, and everything you can change without leaving.
//!
//! Escape used to just release the pointer, which was the right *reflex* to serve
//! — "give me my mouse back" — and nothing else. Now it opens this, which does
//! that too: the pointer is released whenever the menu is up, because a menu you
//! cannot click is a menu with a second, worse set of controls.
//!
//! **Rows, not widgets.** Every line is one of three things — an action, a choice
//! that cycles, or a number that steps — and all three are driven by the same
//! four keys and the same click. That is deliberate: a native menu built out of
//! bespoke controls is where a client starts needing a UI framework, and this one
//! has a 5×7 font and a rectangle painter. What it costs is a colour *picker*;
//! what it buys is that the whole menu is 300 lines and testable without a GPU.
//!
//! Layout and hit-testing are the same arithmetic run twice — `rows_at` produces
//! the on-screen rectangle for every row, and both drawing and the mouse read it.
//! Two copies of that arithmetic is how a menu develops the bug where clicking a
//! row activates the one above it.

use crate::hud::{OverlayVertex, Painter};
use crate::settings::Settings;

/// Which page is showing. Flat rather than a stack: there are three of them, and
/// a back button that always means "up one" needs no history.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum Page {
    #[default]
    Root,
    Crosshair,
    Video,
    Controls,
}

/// What a row does when you activate or step it.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Action {
    Resume,
    Open(Page),
    Back,
    Quit,
    CrosshairStyle,
    CrosshairSize,
    CrosshairGap,
    CrosshairThickness,
    CrosshairOutline,
    CrosshairDot,
    CrosshairAlpha,
    CrosshairColor,
    Fullscreen,
    RenderScale,
    Quality,
    Vsync,
    Fov,
    Antialias,
    Shadows,
    FpsLimit,
    ShowHitboxes,
    Sensitivity,
}

/// One line of the menu, as it will be drawn.
pub struct Row {
    pub label: String,
    /// The right-hand side: the current value, or empty for a plain action.
    pub value: String,
    pub action: Action,
}

/// Where a row sits on screen, in pixels. Produced once and used by both the
/// painter and the mouse, so they cannot disagree about what you clicked.
#[derive(Debug, Clone, Copy)]
pub struct RowRect {
    pub x: f32,
    pub y: f32,
    pub w: f32,
    pub h: f32,
}

impl RowRect {
    fn contains(&self, px: f32, py: f32) -> bool {
        px >= self.x && px <= self.x + self.w && py >= self.y && py <= self.y + self.h
    }
}

const PANEL_W: f32 = 460.0;
const ROW_H: f32 = 34.0;
const HEADER: f32 = 64.0;
const FOOTER: f32 = 40.0;

pub(crate) const TEXT: [f32; 4] = [0.88, 0.91, 0.94, 0.95];
pub(crate) const TEXT_DIM: [f32; 4] = [0.62, 0.66, 0.70, 0.85];
pub(crate) const ACCENT: [f32; 4] = [0.49, 0.91, 0.53, 0.95];
pub(crate) const PANEL_BG: [f32; 4] = [0.04, 0.05, 0.07, 0.96];
const ROW_BG: [f32; 4] = [1.0, 1.0, 1.0, 0.05];
const SELECTED_BG: [f32; 4] = [0.49, 0.91, 0.53, 0.14];
pub(crate) const SCRIM: [f32; 4] = [0.0, 0.0, 0.0, 0.55];

#[derive(Default)]
pub struct Menu {
    pub open: bool,
    pub page: Page,
    /// The highlighted row. Kept in range by `rows`, never trusted from input:
    /// a page change shortens the list, and a cursor left past the end would
    /// draw a highlight on nothing and activate whatever ended up there.
    cursor: usize,
    /// Last known pointer position, in window pixels.
    pointer: (f32, f32),
}

impl Menu {
    pub fn toggle(&mut self) {
        self.open = !self.open;
        if self.open {
            // Always opens at the top level. Reopening three pages deep, where
            // you left off ten minutes ago, is disorienting every time.
            self.page = Page::Root;
            self.cursor = 0;
        }
    }

    pub fn close(&mut self) {
        self.open = false;
    }

    /// Escape, in the menu. One step out rather than straight to the game, so a
    /// sub-page has a way back that is not the mouse.
    pub fn escape(&mut self) -> bool {
        match self.page {
            Page::Root => {
                self.open = false;
                true
            }
            _ => {
                self.page = Page::Root;
                self.cursor = 0;
                false
            }
        }
    }

    pub fn rows(&self, settings: &Settings, in_match: bool) -> Vec<Row> {
        let row = |label: &str, value: String, action: Action| Row {
            label: label.to_string(),
            value,
            action,
        };
        match self.page {
            Page::Root => vec![
                row("RESUME", String::new(), Action::Resume),
                row(
                    "CROSSHAIR",
                    String::from(">"),
                    Action::Open(Page::Crosshair),
                ),
                row("VIDEO", String::from(">"), Action::Open(Page::Video)),
                row("CONTROLS", String::from(">"), Action::Open(Page::Controls)),
                row(
                    // A match is left by disconnecting; Train is left by closing.
                    // Saying which one this is avoids the worst possible surprise
                    // in a menu — a button that ends something you were winning.
                    if in_match { "LEAVE MATCH" } else { "QUIT" },
                    String::new(),
                    Action::Quit,
                ),
            ],
            Page::Crosshair => vec![
                row(
                    "STYLE",
                    settings.crosshair.style.label().to_string(),
                    Action::CrosshairStyle,
                ),
                row(
                    "SIZE",
                    format!("{:.1}", settings.crosshair.size),
                    Action::CrosshairSize,
                ),
                row(
                    "GAP",
                    format!("{:.1}", settings.crosshair.gap),
                    Action::CrosshairGap,
                ),
                row(
                    "THICKNESS",
                    format!("{:.1}", settings.crosshair.thickness),
                    Action::CrosshairThickness,
                ),
                row(
                    "COLOUR",
                    settings.crosshair.color.label().to_string(),
                    Action::CrosshairColor,
                ),
                row(
                    "OUTLINE",
                    if settings.crosshair.outline {
                        "ON"
                    } else {
                        "OFF"
                    }
                    .to_string(),
                    Action::CrosshairOutline,
                ),
                row(
                    "CENTRE DOT",
                    if settings.crosshair.dot { "ON" } else { "OFF" }.to_string(),
                    Action::CrosshairDot,
                ),
                row(
                    "OPACITY",
                    format!("{:.0}%", settings.crosshair.alpha * 100.0),
                    Action::CrosshairAlpha,
                ),
                row("BACK", String::new(), Action::Back),
            ],
            Page::Video => vec![
                row(
                    "DISPLAY",
                    if settings.video.fullscreen {
                        "FULLSCREEN".into()
                    } else {
                        "WINDOWED".into()
                    },
                    Action::Fullscreen,
                ),
                row(
                    "RESOLUTION",
                    format!("{:.0}%", settings.video.render_scale * 100.0),
                    Action::RenderScale,
                ),
                row(
                    "FIELD OF VIEW",
                    format!("{:.0}", settings.video.fov),
                    Action::Fov,
                ),
                // The preset first and the knobs it writes directly under it, so
                // the relationship is visible: stepping QUALITY moves the rows
                // below rather than shadowing them, and a player who then changes
                // one of those sees the preset row stay where they left it.
                row(
                    "QUALITY",
                    settings.video.quality.label().to_string(),
                    Action::Quality,
                ),
                row(
                    "ANTI-ALIASING",
                    if settings.video.antialias {
                        "4X"
                    } else {
                        "OFF"
                    }
                    .to_string(),
                    Action::Antialias,
                ),
                row(
                    "SHADOWS",
                    if settings.video.shadows { "ON" } else { "OFF" }.to_string(),
                    Action::Shadows,
                ),
                row(
                    "VSYNC",
                    if settings.video.vsync { "ON" } else { "OFF" }.to_string(),
                    Action::Vsync,
                ),
                row(
                    "FRAME CAP",
                    if settings.video.fps_limit == 0 {
                        "UNCAPPED".to_string()
                    } else {
                        format!("{} FPS", settings.video.fps_limit)
                    },
                    Action::FpsLimit,
                ),
                // On the video page rather than a debug one, because it is a
                // thing you turn on for thirty seconds to answer a question and
                // then turn off — and a debug page is somewhere you never look.
                row(
                    "SHOW HITBOXES",
                    if settings.show_hitboxes { "ON" } else { "OFF" }.to_string(),
                    Action::ShowHitboxes,
                ),
                row("BACK", String::new(), Action::Back),
            ],
            Page::Controls => vec![
                row(
                    "SENSITIVITY",
                    format!("{:.2}", settings.sensitivity),
                    Action::Sensitivity,
                ),
                row("BACK", String::new(), Action::Back),
            ],
        }
    }

    /// The subtitle under the page title. Where a page needs a sentence, this is
    /// it — a menu row is four words and some settings genuinely need more.
    fn hint(&self) -> &'static str {
        match self.page {
            Page::Root => "ESC RESUMES - ARROWS AND ENTER, OR THE MOUSE",
            Page::Crosshair => "THE GAP ALSO OPENS WITH THE WEAPON'S SPREAD",
            Page::Video => "RESOLUTION SCALES THE WORLD, NEVER THE HUD",
            Page::Controls => "DIVIDED BY THE SCOPE'S MAGNIFICATION WHILE SCOPED",
        }
    }

    fn title(&self) -> &'static str {
        match self.page {
            Page::Root => "PAUSED",
            Page::Crosshair => "CROSSHAIR",
            Page::Video => "VIDEO",
            Page::Controls => "CONTROLS",
        }
    }

    /// Where every row lands, in window pixels.
    pub fn rows_at(&self, count: usize, width: f32, height: f32) -> Vec<RowRect> {
        let panel_h = HEADER + count as f32 * ROW_H + FOOTER;
        let x = (width - PANEL_W) / 2.0;
        let y = (height - panel_h) / 2.0;
        (0..count)
            .map(|i| RowRect {
                x: x + 16.0,
                y: y + HEADER + i as f32 * ROW_H,
                w: PANEL_W - 32.0,
                h: ROW_H,
            })
            .collect()
    }

    pub fn move_cursor(&mut self, delta: i32, count: usize) {
        if count == 0 {
            return;
        }
        let n = count as i32;
        // Wrapping, because a list this short has no scrollbar and pressing down
        // at the bottom to reach the top is the shape everybody expects.
        self.cursor = (((self.cursor as i32 + delta) % n + n) % n) as usize;
    }

    pub fn cursor(&self) -> usize {
        self.cursor
    }

    /// Point the cursor at whatever the mouse is over. Returns true if it moved,
    /// so the caller can make a sound only when the selection actually changes.
    pub fn hover(&mut self, x: f32, y: f32, count: usize, width: f32, height: f32) -> bool {
        self.pointer = (x, y);
        for (i, rect) in self.rows_at(count, width, height).iter().enumerate() {
            if rect.contains(x, y) {
                let moved = self.cursor != i;
                self.cursor = i;
                return moved;
            }
        }
        false
    }

    /// Which row a click at the last pointer position lands on, if any.
    pub fn hit(&self, count: usize, width: f32, height: f32) -> Option<usize> {
        let (x, y) = self.pointer;
        self.rows_at(count, width, height)
            .iter()
            .position(|r| r.contains(x, y))
    }

    pub fn build(
        &self,
        settings: &Settings,
        in_match: bool,
        width: f32,
        height: f32,
        out: &mut Vec<OverlayVertex>,
    ) {
        if !self.open {
            return;
        }
        let rows = self.rows(settings, in_match);
        let rects = self.rows_at(rows.len(), width, height);
        let mut p = Painter::new(out, width, height);

        // The world is still being drawn behind this and still moving — a scrim
        // is what makes the text readable over it without hiding that the game
        // is there.
        p.rect(0.0, 0.0, width, height, SCRIM);

        let panel_h = HEADER + rows.len() as f32 * ROW_H + FOOTER;
        let px = (width - PANEL_W) / 2.0;
        let py = (height - panel_h) / 2.0;
        p.rect(px, py, PANEL_W, panel_h, PANEL_BG);
        // A rule under the title rather than a border all round: the panel edge
        // is already given by the fill, and a full outline on a translucent panel
        // reads as a dialog box from another decade.
        p.rect(px, py, PANEL_W, 2.0, ACCENT);

        p.text(px + 16.0, py + 18.0, 2.6, TEXT, self.title());
        p.text(px + 16.0, py + 42.0, 1.3, TEXT_DIM, self.hint());

        for (i, (row, rect)) in rows.iter().zip(rects.iter()).enumerate() {
            let selected = i == self.cursor;
            p.rect(
                rect.x,
                rect.y + 2.0,
                rect.w,
                rect.h - 4.0,
                if selected { SELECTED_BG } else { ROW_BG },
            );
            if selected {
                // The selection marker is a bar on the leading edge, not a
                // highlight colour on the text: the text has to stay readable,
                // and a row whose label changes colour when picked reads as
                // disabled to about half the people who see it.
                p.rect(rect.x, rect.y + 2.0, 3.0, rect.h - 4.0, ACCENT);
            }
            let text_y = rect.y + rect.h / 2.0 - 5.0;
            p.text(
                rect.x + 14.0,
                text_y,
                1.7,
                if selected { TEXT } else { TEXT_DIM },
                &row.label,
            );
            if !row.value.is_empty() {
                p.text_right(
                    rect.x + rect.w - 14.0,
                    text_y,
                    1.7,
                    if selected { ACCENT } else { TEXT_DIM },
                    &row.value,
                );
            }
        }

        let footer_y = py + panel_h - FOOTER + 12.0;
        p.text(px + 16.0, footer_y, 1.3, TEXT_DIM, "< > CHANGES A VALUE");
        p.text_right(
            px + PANEL_W - 16.0,
            footer_y,
            1.3,
            TEXT_DIM,
            "SAVED ON THE NODE",
        );
    }
}

/// Apply one row's action to the settings, returning every key that changed.
///
/// A `Vec` and not one key, because a preset is not one key: stepping QUALITY
/// writes the individual rows under it, and returning only `KEY_QUALITY` would
/// persist a level whose knobs came back at their old values on the next start.
///
/// Returned rather than written here so the caller owns persistence: this
/// function is pure, which is what lets every one of the stepping rules below be
/// tested without a window, a GPU or a node.
///
/// `step` is +1 or -1. An action that is not a value ignores it — activating a
/// choice row with Enter steps it forwards, which is the behaviour that makes
/// one control work for both the keyboard and the mouse.
pub fn apply(action: Action, step: i32, settings: &mut Settings) -> Vec<&'static str> {
    use crate::settings::*;
    match action {
        Action::Resume | Action::Open(_) | Action::Back | Action::Quit => vec![],
        Action::CrosshairStyle => {
            settings.crosshair.style = cycle(&CrosshairStyle::ALL, settings.crosshair.style, step);
            vec![KEY_CROSSHAIR_STYLE]
        }
        Action::CrosshairColor => {
            settings.crosshair.color = cycle(&CrosshairColor::ALL, settings.crosshair.color, step);
            vec![KEY_CROSSHAIR_COLOR]
        }
        Action::CrosshairSize => {
            settings.crosshair.size = step_value(settings.crosshair.size, step, 0.5, 1.0, 12.0);
            vec![KEY_CROSSHAIR_SIZE]
        }
        Action::CrosshairGap => {
            settings.crosshair.gap = step_value(settings.crosshair.gap, step, 1.0, 0.0, 20.0);
            vec![KEY_CROSSHAIR_GAP]
        }
        Action::CrosshairThickness => {
            settings.crosshair.thickness =
                step_value(settings.crosshair.thickness, step, 0.2, 0.2, 3.0);
            vec![KEY_CROSSHAIR_THICKNESS]
        }
        Action::CrosshairOutline => {
            settings.crosshair.outline = !settings.crosshair.outline;
            vec![KEY_CROSSHAIR_OUTLINE]
        }
        Action::CrosshairDot => {
            settings.crosshair.dot = !settings.crosshair.dot;
            vec![KEY_CROSSHAIR_DOT]
        }
        Action::CrosshairAlpha => {
            settings.crosshair.alpha = step_value(settings.crosshair.alpha, step, 0.05, 0.15, 1.0);
            vec![KEY_CROSSHAIR_ALPHA]
        }
        Action::Fullscreen => {
            settings.video.fullscreen = !settings.video.fullscreen;
            vec![KEY_FULLSCREEN]
        }
        Action::Vsync => {
            settings.video.vsync = !settings.video.vsync;
            vec![KEY_VSYNC]
        }
        Action::ShowHitboxes => {
            settings.show_hitboxes = !settings.show_hitboxes;
            vec![KEY_SHOW_HITBOXES]
        }
        Action::Quality => {
            let quality = cycle(&Quality::ALL, settings.video.quality, step);
            settings.video.apply_preset(quality);
            vec![KEY_QUALITY, KEY_ANTIALIAS]
        }
        Action::Fov => {
            settings.video.fov =
                step_value(settings.video.fov, step, 5.0, FOV_RANGE.0, FOV_RANGE.1);
            vec![KEY_FOV]
        }
        Action::Antialias => {
            settings.video.antialias = !settings.video.antialias;
            vec![KEY_ANTIALIAS]
        }
        Action::Shadows => {
            settings.video.shadows = !settings.video.shadows;
            vec![KEY_SHADOWS]
        }
        Action::FpsLimit => {
            // Clamped rather than wrapped, unlike the choice rows above. A cap is
            // an ordered scale with a meaningful end, and wrapping it means one
            // step past UNCAPPED lands on 360 — which is the row doing the
            // opposite of what the player just asked for.
            let current = FPS_LIMITS
                .iter()
                .position(|c| *c == settings.video.fps_limit)
                .unwrap_or(0);
            let next =
                (current as i32 + step.signum().max(-1)).clamp(0, FPS_LIMITS.len() as i32 - 1);
            settings.video.fps_limit = FPS_LIMITS[next as usize];
            vec![KEY_FPS_LIMIT]
        }
        Action::RenderScale => {
            // Discrete steps rather than a continuous slider: the render target
            // is reallocated on every change, and a value that lands on 73%
            // helps nobody.
            const SCALES: [f32; 7] = [0.5, 0.75, 0.9, 1.0, 1.25, 1.5, 2.0];
            let current = SCALES
                .iter()
                .position(|s| (*s - settings.video.render_scale).abs() < 0.01)
                .unwrap_or(SCALES.len() - 1);
            let next = (current as i32 + step.signum().max(-1)).clamp(0, SCALES.len() as i32 - 1);
            settings.video.render_scale = SCALES[next as usize];
            vec![KEY_RENDER_SCALE]
        }
        Action::Sensitivity => {
            settings.sensitivity = step_value(settings.sensitivity, step, 0.05, 0.05, 10.0);
            vec![KEY_SENSITIVITY]
        }
    }
}

/// Step through a fixed list, wrapping. Wrapping and not clamping, because a
/// choice row is a cycle: five colours with no way back from the last one would
/// need a second key to mean "the other direction", which is what `step` is for
/// on the *keyboard* and what the mouse does not have.
fn cycle<T: Copy + PartialEq>(all: &[T], current: T, step: i32) -> T {
    if all.is_empty() {
        return current;
    }
    let n = all.len() as i32;
    let at = all.iter().position(|v| *v == current).unwrap_or(0) as i32;
    let next = ((at + step.signum()) % n + n) % n;
    all[next as usize]
}

/// Step a number and clamp it. Rounded to the step, so a value nudged up and
/// back down lands on exactly where it started rather than drifting by float
/// error into `0.7999999`, which is then what the menu prints.
fn step_value(current: f32, step: i32, size: f32, min: f32, max: f32) -> f32 {
    let next = current + size * step.signum() as f32;
    let snapped = (next / size).round() * size;
    snapped.clamp(min, max)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::settings::{CrosshairColor, KEY_CROSSHAIR_COLOR};

    fn menu() -> (Menu, Settings) {
        let m = Menu {
            open: true,
            ..Default::default()
        };
        (m, Settings::default())
    }

    #[test]
    fn escape_walks_out_one_page_at_a_time() {
        // Straight to the game from three pages in is how you lose a setting you
        // were halfway through changing.
        let (mut m, _) = menu();
        m.page = Page::Video;
        assert!(!m.escape(), "a sub-page closed the whole menu");
        assert_eq!(m.page, Page::Root);
        assert!(m.escape());
        assert!(!m.open);
    }

    #[test]
    fn the_cursor_wraps_and_never_leaves_the_list() {
        let (mut m, s) = menu();
        let count = m.rows(&s, false).len();
        m.move_cursor(-1, count);
        assert_eq!(m.cursor(), count - 1, "up from the top wraps to the bottom");
        m.move_cursor(1, count);
        assert_eq!(m.cursor(), 0);
        // And a zero-length list is a no-op rather than a modulo by zero.
        m.move_cursor(1, 0);
    }

    #[test]
    fn opening_the_menu_always_starts_at_the_top() {
        let (mut m, _) = menu();
        m.page = Page::Crosshair;
        m.close();
        m.toggle();
        assert_eq!(m.page, Page::Root);
        assert_eq!(m.cursor(), 0);
    }

    #[test]
    fn a_click_lands_on_the_row_it_is_drawn_over() {
        // The bug this exists to prevent: layout and hit-testing drifting apart,
        // so clicking a row activates its neighbour. Both read `rows_at`.
        let (mut m, s) = menu();
        let count = m.rows(&s, false).len();
        let rects = m.rows_at(count, 1920.0, 1080.0);
        for (i, rect) in rects.iter().enumerate() {
            let (cx, cy) = (rect.x + rect.w / 2.0, rect.y + rect.h / 2.0);
            m.hover(cx, cy, count, 1920.0, 1080.0);
            assert_eq!(m.cursor(), i);
            assert_eq!(m.hit(count, 1920.0, 1080.0), Some(i));
        }
    }

    #[test]
    fn a_click_outside_the_panel_hits_nothing() {
        let (mut m, s) = menu();
        let count = m.rows(&s, false).len();
        m.hover(4.0, 4.0, count, 1920.0, 1080.0);
        assert_eq!(m.hit(count, 1920.0, 1080.0), None);
    }

    #[test]
    fn a_choice_cycles_in_both_directions_and_wraps() {
        let (_, mut s) = menu();
        assert_eq!(
            apply(Action::CrosshairColor, 1, &mut s),
            vec![KEY_CROSSHAIR_COLOR]
        );
        assert_eq!(s.crosshair.color, CrosshairColor::Green);
        apply(Action::CrosshairColor, -1, &mut s);
        assert_eq!(s.crosshair.color, CrosshairColor::White);
        // Backwards off the front wraps to the end rather than sticking.
        apply(Action::CrosshairColor, -1, &mut s);
        assert_eq!(s.crosshair.color, CrosshairColor::Red);
    }

    #[test]
    fn a_number_clamps_at_both_ends_and_snaps_to_its_step() {
        let (_, mut s) = menu();
        for _ in 0..50 {
            apply(Action::CrosshairSize, 1, &mut s);
        }
        assert_eq!(s.crosshair.size, 12.0);
        for _ in 0..100 {
            apply(Action::CrosshairSize, -1, &mut s);
        }
        assert_eq!(s.crosshair.size, 1.0);
        // Up then down returns exactly, rather than drifting into a value the
        // menu then prints as 2.9000001.
        apply(Action::CrosshairSize, 1, &mut s);
        apply(Action::CrosshairSize, -1, &mut s);
        assert_eq!(s.crosshair.size, 1.0);
    }

    #[test]
    fn the_render_scale_clamps_rather_than_wrapping() {
        // The one choice row that must *not* wrap: stepping past 100% round to
        // 50% would quarter the resolution of somebody trying to raise it.
        let (_, mut s) = menu();
        for _ in 0..10 {
            apply(Action::RenderScale, 1, &mut s);
        }
        assert_eq!(s.video.render_scale, 2.0);
        for _ in 0..10 {
            apply(Action::RenderScale, -1, &mut s);
        }
        assert_eq!(s.video.render_scale, 0.5);
    }

    #[test]
    fn a_toggle_ignores_the_direction() {
        let (_, mut s) = menu();
        let before = s.video.vsync;
        apply(Action::Vsync, -1, &mut s);
        assert_eq!(s.video.vsync, !before);
        apply(Action::Vsync, 1, &mut s);
        assert_eq!(s.video.vsync, before);
    }

    #[test]
    fn navigation_actions_change_nothing_and_save_nothing() {
        let (_, mut s) = menu();
        for action in [
            Action::Resume,
            Action::Back,
            Action::Quit,
            Action::Open(Page::Video),
        ] {
            assert!(apply(action, 1, &mut s).is_empty());
        }
        assert_eq!(s.crosshair.size, Settings::default().crosshair.size);
    }

    #[test]
    fn leaving_says_which_thing_it_leaves() {
        let (m, s) = menu();
        let solo = m.rows(&s, false).pop().expect("a last row").label;
        let match_ = m.rows(&s, true).pop().expect("a last row").label;
        assert_eq!(solo, "QUIT");
        assert_eq!(match_, "LEAVE MATCH");
    }

    #[test]
    fn every_label_is_in_the_font() {
        // A missing glyph draws *nothing* — no box, no fallback — so a `›` in a
        // label is an invisible column and a `←` in the footer is a sentence
        // that starts mid-word. Both shipped, and both looked like a layout bug
        // rather than a character the 5×7 font has never had.
        let (mut m, s) = menu();
        for page in [Page::Root, Page::Crosshair, Page::Video, Page::Controls] {
            m.page = page;
            for row in m.rows(&s, true) {
                for ch in row.label.chars().chain(row.value.chars()) {
                    assert!(crate::hud::has_glyph(ch), "{page:?}: no glyph for {ch:?}");
                }
            }
            for ch in m.title().chars().chain(m.hint().chars()) {
                assert!(crate::hud::has_glyph(ch), "{page:?}: no glyph for {ch:?}");
            }
        }
    }

    #[test]
    fn every_page_draws_something_and_a_closed_menu_draws_nothing() {
        let (mut m, s) = menu();
        for page in [Page::Root, Page::Crosshair, Page::Video, Page::Controls] {
            m.page = page;
            let mut out = Vec::new();
            m.build(&s, false, 1920.0, 1080.0, &mut out);
            assert!(!out.is_empty(), "{page:?} drew nothing");
            // Every vertex inside clip space, or the panel is off screen.
            assert!(out
                .iter()
                .all(|v| v.position[0].abs() <= 1.5 && v.position[1].abs() <= 1.5));
        }
        m.close();
        let mut out = Vec::new();
        m.build(&s, false, 1920.0, 1080.0, &mut out);
        assert!(out.is_empty());
    }
}
