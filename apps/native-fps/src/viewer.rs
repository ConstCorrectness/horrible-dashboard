//! The model viewer: every weapon, in every finish, turned over in the hands.
//!
//! The dashboard has a Studio window for this, built on a browser's scene
//! graph. This is the same question — *what does that weapon look like, in
//! that skin, at that wear* — answered by the renderer that will actually
//! draw it in a match: the view model, on the range, running the weapon's own
//! inspect clip on a loop.
//!
//! It holds no models and draws none. It is a cursor over two served tables —
//! the weapons and the skin catalog — and the view model is handed whatever
//! the cursor is on, exactly as it is handed an equipped skin in a match. So
//! a skin that looks wrong here looks wrong there, which is the point of
//! looking.

use crate::api::SkinDefinition;
use crate::hud::{OverlayVertex, Painter};
use crate::menu::{ACCENT, TEXT, TEXT_DIM};
use crate::text_kit::TextKit;
use crate::viewmodel::Skin;

/// Wear moves in tenths: Factory New at 0, Battle-Scarred at 1.
const WEAR_STEP: f32 = 0.1;

pub const HINT: &str = "[ ] WEAPON   /   , . FINISH   /   - = WEAR   /   F INSPECT";

#[derive(Debug, Clone, Default)]
pub struct Viewer {
    skins: Vec<SkinDefinition>,
    /// The weapon in hand, as an index into the served weapon table.
    pub weapon: usize,
    /// Which of this weapon's finishes is on; `None` is the stock one.
    finish: Option<usize>,
    pub wear: f32,
}

impl Viewer {
    pub fn new(skins: Vec<SkinDefinition>) -> Viewer {
        Viewer {
            // A skin with no base colour is not a skin: see `SkinInstance`.
            skins: skins
                .into_iter()
                .filter(|s| !s.weapon_id.is_empty() && !s.base_color.is_empty())
                .collect(),
            ..Viewer::default()
        }
    }

    fn finishes(&self, weapon_id: &str) -> Vec<&SkinDefinition> {
        self.skins
            .iter()
            .filter(|s| s.weapon_id == weapon_id)
            .collect()
    }

    /// Move to the next weapon of `count`, back on its stock finish.
    pub fn step_weapon(&mut self, step: i32, count: usize) -> usize {
        if count > 0 {
            self.weapon = (self.weapon as i32 + step).rem_euclid(count as i32) as usize;
            self.finish = None;
        }
        self.weapon
    }

    /// Step through stock and then each catalog finish for `weapon_id`.
    pub fn step_finish(&mut self, step: i32, weapon_id: &str) {
        let n = self.finishes(weapon_id).len() as i32 + 1;
        let at = self.finish.map_or(0, |i| i as i32 + 1);
        let next = (at + step).rem_euclid(n);
        self.finish = (next > 0).then(|| (next - 1) as usize);
    }

    pub fn step_wear(&mut self, step: i32) {
        // Snapped to the tenth, so ten steps down from 1 is exactly 0 rather
        // than whatever ten float subtractions leave.
        let tenths = (self.wear / WEAR_STEP).round() as i32 + step;
        self.wear = tenths.clamp(0, 10) as f32 * WEAR_STEP;
    }

    fn chosen(&self, weapon_id: &str) -> Option<&SkinDefinition> {
        self.finishes(weapon_id).get(self.finish?).copied()
    }

    /// The finish to draw `weapon_id` in, as the view model takes it.
    pub fn skin(&self, weapon_id: &str) -> Option<Skin> {
        let def = self.chosen(weapon_id)?;
        Some(Skin {
            id: Some(def.id.clone()),
            base_color: def.base_color.clone(),
            accent_color: def.accent_color.clone(),
            pattern_type: def.pattern_type.clone(),
            float_value: self.wear,
        })
    }

    /// What is in hand, as two lines: the weapon, then its finish and wear.
    pub fn caption(&self, weapon_name: &str, weapon_id: &str) -> (String, String) {
        let total = self.finishes(weapon_id).len();
        let finish = match self.chosen(weapon_id) {
            Some(def) => format!(
                "{}  /  {} OF {}  /  WEAR {:.1}",
                def.name.to_uppercase(),
                self.finish.map_or(0, |i| i + 1),
                total,
                self.wear
            ),
            None if total == 0 => "STOCK  /  NO FINISHES IN THE CATALOG".to_string(),
            None => format!("STOCK  /  {total} FINISHES"),
        };
        (weapon_name.to_uppercase(), finish)
    }
}

fn scale(height: f32) -> f32 {
    (height / 800.0).clamp(0.75, 2.6)
}

/// The viewer's card, top centre: what is in hand and the keys that change it.
pub fn build(
    title: &str,
    finish: &str,
    width: f32,
    height: f32,
    text: Option<&TextKit>,
    out: &mut Vec<OverlayVertex>,
) {
    let mut p = Painter::with_text(out, width, height, text);
    let k = scale(height);
    let w = (p
        .text_width(title, 1.9 * k)
        .max(p.num_width(finish, 1.15 * k))
        .max(p.text_width(HINT, 1.0 * k))
        + 40.0 * k)
        .min(width - 24.0 * k);
    let x = (width - w) / 2.0;
    let y = 12.0 * k;
    p.panel(x, y, w, 78.0 * k, 6.0 * k, Some(ACCENT));
    p.center_text(y + 10.0 * k, 1.9 * k, TEXT, title);
    p.center_num(y + 36.0 * k, 1.15 * k, ACCENT, finish);
    p.center_text(y + 56.0 * k, 1.0 * k, TEXT_DIM, HINT);
}

#[cfg(test)]
mod tests {
    use super::*;

    fn def(id: &str, weapon: &str, base: &str) -> SkinDefinition {
        SkinDefinition {
            id: id.into(),
            name: id.replace('_', " "),
            weapon_id: weapon.into(),
            base_color: base.into(),
            accent_color: "#ffffff".into(),
            pattern_type: "solid".into(),
        }
    }

    fn viewer() -> Viewer {
        Viewer::new(vec![
            def("knife_fade", "knife", "#38bdf8"),
            def("assault_ember", "assault", "#f97316"),
            def("knife_karambit_fade", "knife", "#38bdf8"),
            // Unresolved: no colour, so nothing to show.
            def("ghost", "knife", ""),
        ])
    }

    #[test]
    fn finishes_cycle_through_stock_and_only_this_weapons() {
        let mut v = viewer();
        assert_eq!(v.skin("knife"), None, "it opens on the stock finish");
        let mut seen = Vec::new();
        for _ in 0..3 {
            v.step_finish(1, "knife");
            seen.push(v.skin("knife").and_then(|s| s.id));
        }
        assert_eq!(
            seen,
            [
                Some("knife_fade".to_string()),
                Some("knife_karambit_fade".to_string()),
                None
            ]
        );
        // Backwards from stock is the last one.
        v.step_finish(-1, "knife");
        assert_eq!(
            v.skin("knife").unwrap().id.as_deref(),
            Some("knife_karambit_fade")
        );
        // A weapon with nothing in the catalog stays stock whichever way.
        v.step_finish(1, "pistol");
        assert_eq!(v.skin("pistol"), None);
    }

    #[test]
    fn changing_weapon_wraps_and_returns_to_stock() {
        let mut v = viewer();
        v.step_finish(1, "knife");
        assert_eq!(v.step_weapon(1, 3), 1);
        assert_eq!(v.skin("knife"), None);
        assert_eq!(v.step_weapon(-2, 3), 2);
        assert_eq!(v.step_weapon(1, 3), 0);
        // No weapons served: nothing to step to, and no division by zero.
        assert_eq!(v.step_weapon(1, 0), 0);
    }

    #[test]
    fn wear_stays_between_new_and_scarred_and_rides_the_skin() {
        let mut v = viewer();
        v.step_wear(-1);
        assert_eq!(v.wear, 0.0);
        for _ in 0..14 {
            v.step_wear(1);
        }
        assert_eq!(v.wear, 1.0);
        for _ in 0..10 {
            v.step_wear(-1);
        }
        assert_eq!(v.wear, 0.0, "ten steps down did not land on zero");
        v.step_wear(3);
        v.step_finish(1, "assault");
        assert!((v.skin("assault").unwrap().float_value - 0.3).abs() < 1e-6);
    }

    #[test]
    fn the_caption_says_what_is_in_hand() {
        let mut v = viewer();
        assert_eq!(
            v.caption("Combat Knife", "knife"),
            (
                "COMBAT KNIFE".to_string(),
                "STOCK  /  2 FINISHES".to_string()
            )
        );
        v.step_finish(1, "knife");
        assert_eq!(
            v.caption("Combat Knife", "knife").1,
            "KNIFE FADE  /  1 OF 2  /  WEAR 0.0"
        );
        assert_eq!(
            v.caption("Pistol", "pistol").1,
            "STOCK  /  NO FINISHES IN THE CATALOG"
        );
        // Until the system fonts load, the card is in the bitmap font.
        for label in [HINT, &v.caption("Combat Knife", "knife").1] {
            for ch in label.chars() {
                assert!(
                    crate::hud::has_glyph(ch),
                    "no glyph for {ch:?} in {label:?}"
                );
            }
        }
    }

    #[test]
    fn the_card_draws_inside_the_window() {
        let mut out = Vec::new();
        build(
            "ASSAULT RIFLE",
            "STOCK  /  3 FINISHES",
            1280.0,
            720.0,
            None,
            &mut out,
        );
        assert!(!out.is_empty());
        for v in &out {
            assert!(v.position[0].abs() <= 1.0 && v.position[1].abs() <= 1.0);
        }
    }
}
