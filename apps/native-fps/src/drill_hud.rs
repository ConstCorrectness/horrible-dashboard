//! The aim drill's two overlays, natively: the clock while it runs, the score
//! when it ends. `panels/DrillCard.tsx` is the browser's.
//!
//! The card is driven from the keyboard — R runs the drill again, F returns to
//! the free range, Escape is the menu as always — because Train keeps the
//! pointer captured and a card you have to free the mouse to answer is a card
//! with a worse set of controls than the game around it.

use crate::drills::DrillStats;
use crate::hud::Painter;
use crate::menu::{ACCENT, SCRIM, TEXT, TEXT_DIM};
use crate::hud::OverlayVertex;
use crate::training::{DrillPhase, DrillStatus};

/// Opaque for the reason `summary::CARD_BG` is: a card somebody stops to read
/// must not show the HUD through it.
const CARD_BG: [f32; 4] = [0.04, 0.05, 0.07, 1.0];
const CARD_W: f32 = 560.0;
const CARD_H: f32 = 300.0;

fn scale(height: f32) -> f32 {
    (height / 800.0).clamp(0.75, 2.6)
}

fn text_width(s: &str, scale: f32) -> f32 {
    let n = s.chars().count() as f32;
    if n == 0.0 {
        0.0
    } else {
        n * 6.0 * scale - scale
    }
}

/// The six figures the card shows, as label and value. One function so the
/// browser's tile list and this one can be compared side by side.
pub fn cells(stats: &DrillStats) -> [(&'static str, String); 6] {
    let pct = |v: f64| format!("{}%", (v * 100.0).round() as i64);
    let ms = |v: Option<f64>| match v {
        Some(v) => format!("{} MS", (v * 1000.0).round() as i64),
        None => "N/A".to_string(),
    };
    [
        ("KILLS / MIN", format!("{:.1}", stats.kills_per_minute)),
        ("ACCURACY", pct(stats.accuracy)),
        ("HEADSHOTS", pct(stats.headshot_share)),
        ("REACTION", ms(stats.median_reaction)),
        ("TIME TO KILL", ms(stats.median_ttk)),
        ("KILLS", stats.kills.to_string()),
    ]
}

/// Draw whichever overlay `status` calls for, appended over the HUD.
pub fn build(status: &DrillStatus, width: f32, height: f32, out: &mut Vec<OverlayVertex>) {
    let mut p = Painter::new(out, width, height);
    let k = scale(height);
    match status.phase {
        DrillPhase::Countdown | DrillPhase::Running => {
            let label = status.name.to_uppercase();
            let figure = if status.phase == DrillPhase::Countdown {
                format!("{}", status.remaining.ceil() as i32)
            } else {
                format!("{}S", status.remaining.ceil() as i32)
            };
            let w = (text_width(&label, 1.3 * k).max(text_width(&figure, 3.0 * k)) + 32.0 * k)
                .max(140.0 * k);
            let x = (width - w) / 2.0;
            let y = 12.0 * k;
            p.panel(x, y, w, 58.0 * k, 6.0 * k, Some(ACCENT));
            p.text(
                (width - text_width(&label, 1.3 * k)) / 2.0,
                y + 10.0 * k,
                1.3 * k,
                TEXT_DIM,
                &label,
            );
            p.text(
                (width - text_width(&figure, 3.0 * k)) / 2.0,
                y + 26.0 * k,
                3.0 * k,
                TEXT,
                &figure,
            );
        }
        DrillPhase::Done => {
            let Some(stats) = status.stats.as_ref() else {
                return;
            };
            p.rect(0.0, 0.0, width, height, SCRIM);
            let cx = (width - CARD_W * k) / 2.0;
            let cy = (height - CARD_H * k) / 2.0;
            p.rect(cx, cy, CARD_W * k, CARD_H * k, CARD_BG);
            p.rect(cx, cy, CARD_W * k, 2.0 * k, ACCENT);
            p.text(
                cx + 24.0 * k,
                cy + 22.0 * k,
                2.6 * k,
                TEXT,
                &status.name.to_uppercase(),
            );
            p.text(
                cx + 24.0 * k,
                cy + 52.0 * k,
                1.3 * k,
                TEXT_DIM,
                &format!(
                    "{}/{} SHOTS   {} HITS",
                    stats.hit_shots, stats.shots, stats.hits
                ),
            );
            let col_w = (CARD_W - 48.0) / 3.0 * k;
            for (i, (label, value)) in cells(stats).iter().enumerate() {
                let x = cx + 24.0 * k + (i % 3) as f32 * col_w;
                let y = cy + (90.0 + (i / 3) as f32 * 80.0) * k;
                p.text(x, y, 1.3 * k, TEXT_DIM, label);
                p.text(x, y + 20.0 * k, 3.0 * k, TEXT, value);
            }
            p.text(
                cx + 24.0 * k,
                cy + (CARD_H - 34.0) * k,
                1.4 * k,
                TEXT_DIM,
                "R RUN AGAIN   F FREE PRACTICE   ESC MENU",
            );
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_missing_median_reads_as_not_applicable_not_zero() {
        // Zero milliseconds of reaction would be a superhuman score for a run
        // that never hit anything.
        let stats = DrillStats::default();
        let cells = cells(&stats);
        assert_eq!(cells[3].1, "N/A");
        assert_eq!(cells[4].1, "N/A");
    }

    #[test]
    fn the_card_draws_only_when_there_is_a_score() {
        let mut out = Vec::new();
        let status = DrillStatus {
            id: "aim".into(),
            name: "Aim".into(),
            phase: DrillPhase::Done,
            remaining: 0.0,
            stats: None,
        };
        build(&status, 1280.0, 720.0, &mut out);
        assert!(out.is_empty());
    }
}
