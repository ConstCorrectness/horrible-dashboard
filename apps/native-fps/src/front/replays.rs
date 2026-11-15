//! The REPLAYS tab: the matches this client has recorded (`demo.rs`), to
//! watch again or export as a video.
//!
//! The list is read from the demos folder on a worker when the tab opens —
//! a long match is tens of megabytes of JSON — and until it lands the tab
//! says it is looking. Watching one loads its map with no server and hands the
//! `App` the demo (`App::start_replay`); exporting does the same and steps it
//! at a fixed rate into `ffmpeg`.
//!
//! A child of `front` so it lays out with the same private pieces as the other
//! tabs: the margins, the cards, the one big button.

use super::*;

/// One recording, as the list shows it.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct ReplayView {
    pub path: String,
    pub map: String,
    /// Wall clock, epoch milliseconds.
    pub recorded_at: f64,
    pub duration_ms: f64,
    /// Frags bookmarked in it.
    pub kills: usize,
    /// Whose eyes it was recorded through.
    pub pov_name: Option<String>,
}

const ROW_H: f32 = 52.0;

/// `4:07`, or `1:02:30` for a long one.
pub fn length_label(ms: f64) -> String {
    let s = (ms / 1000.0).round().max(0.0) as u64;
    if s >= 3600 {
        format!("{}:{:02}:{:02}", s / 3600, s % 3600 / 60, s % 60)
    } else {
        format!("{}:{:02}", s / 60, s % 60)
    }
}

impl Front {
    /// The recording the cursor is on.
    pub fn selected_replay(&self) -> Option<&ReplayView> {
        self.replays.as_ref()?.get(self.replay)
    }

    /// The selected recording's map, for the backdrop.
    pub(super) fn replay_map(&self) -> Option<&str> {
        self.selected_replay().map(|r| r.map.as_str())
    }

    /// The list's left column and the detail panel to its right.
    fn replay_columns(width: f32, k: f32) -> (f32, f32, f32) {
        let left = MARGIN * k;
        let right = width - MARGIN * k;
        let list_w = ((right - left) * 0.5).min(720.0 * k);
        (left, left + list_w, right)
    }

    pub(super) fn replay_hits(
        &self,
        width: f32,
        height: f32,
        k: f32,
        top: f32,
        out: &mut Vec<(Target, Hit)>,
    ) {
        let (left, list_end, right) = Self::replay_columns(width, k);
        let floor = height - (MARGIN + 34.0) * k;
        for i in 0..self.replays.as_ref().map_or(0, Vec::len) {
            let y = top + 30.0 * k + i as f32 * ROW_H * k;
            if y + ROW_H * k > floor {
                break;
            }
            out.push((
                Target::Replay(i),
                Hit {
                    x: left,
                    y,
                    w: list_end - left,
                    h: (ROW_H - 6.0) * k,
                },
            ));
        }
        let panel_x = list_end + 32.0 * k;
        if self.selected_replay().is_some() {
            let w = ((right - panel_x) * 0.5 - 6.0 * k).max(80.0 * k);
            let y = top + 214.0 * k;
            out.push((
                Target::ReplayExport,
                Hit {
                    x: panel_x,
                    y,
                    w,
                    h: 40.0 * k,
                },
            ));
            out.push((
                Target::ReplayFolder,
                Hit {
                    x: panel_x + w + 12.0 * k,
                    y,
                    w,
                    h: 40.0 * k,
                },
            ));
        }
        out.push((
            Target::Go,
            Hit {
                x: right - GO_W * k,
                y: height - (MARGIN + GO_H) * k,
                w: GO_W * k,
                h: GO_H * k,
            },
        ));
    }

    pub(super) fn replay_activate(&mut self, target: Target) -> Option<Intent> {
        match target {
            Target::Replay(i) => self.replay = i,
            Target::ReplayExport => {
                let replay = self.selected_replay()?.clone();
                if let Some(why) = &self.export_blocked {
                    self.notice = Some((why.clone(), true));
                    return None;
                }
                return Some(Intent::ExportReplay {
                    path: replay.path,
                    map: replay.map,
                });
            }
            Target::ReplayFolder => return Some(Intent::RevealReplays),
            _ => {}
        }
        None
    }

    /// The big button: watch the selected recording.
    pub(super) fn replay_go(&mut self) -> Option<Intent> {
        self.notice = None;
        let replay = self.selected_replay()?.clone();
        Some(Intent::WatchReplay {
            path: replay.path,
            map: replay.map,
        })
    }

    /// Up and down walk the list.
    pub(super) fn replay_key(&mut self, code: KeyCode) -> Option<Intent> {
        let n = self.replays.as_ref().map_or(0, Vec::len);
        if n == 0 {
            return None;
        }
        match code {
            KeyCode::ArrowUp | KeyCode::KeyW => self.replay = (self.replay + n - 1) % n,
            KeyCode::ArrowDown | KeyCode::KeyS => self.replay = (self.replay + 1) % n,
            KeyCode::Enter | KeyCode::NumpadEnter => return self.replay_go(),
            _ => {}
        }
        None
    }

    pub(super) fn paint_replays(
        &self,
        p: &mut Painter,
        hits: &[(Target, Hit)],
        width: f32,
        k: f32,
        top: f32,
    ) {
        let (left, list_end, right) = Self::replay_columns(width, k);
        let panel_x = list_end + 32.0 * k;
        let count = self.replays.as_ref().map(Vec::len);
        heading(
            p,
            left,
            top,
            k,
            &match count {
                Some(n) => format!("REPLAYS  /  {n} SAVED"),
                None => "REPLAYS".to_string(),
            },
        );
        match count {
            None => p.num(
                left,
                top + 40.0 * k,
                1.2 * k,
                theme::FAINT,
                "LOOKING FOR RECORDINGS",
            ),
            Some(0) => {
                p.num(
                    left,
                    top + 40.0 * k,
                    1.2 * k,
                    theme::FAINT,
                    "NOTHING RECORDED YET",
                );
                p.num(
                    left,
                    top + 62.0 * k,
                    1.0 * k,
                    theme::FAINT,
                    "IN A MATCH: ESC  /  REPLAYS  /  RECORD THIS MATCH",
                );
            }
            Some(_) => {}
        }

        for (target, rect) in hits {
            let hot = self.hovered(rect);
            match target {
                Target::Replay(i) => {
                    let Some(r) = self.replays.as_ref().and_then(|l| l.get(*i)) else {
                        continue;
                    };
                    let a = self.arrive(*i);
                    card(p, rect, 8.0 * k, *i == self.replay, hot, a);
                    let ink = theme::faded(
                        if *i == self.replay {
                            theme::TEXT
                        } else {
                            theme::TEXT_DIM
                        },
                        a,
                    );
                    p.text(
                        rect.x + 14.0 * k,
                        rect.y + 8.0 * k,
                        1.45 * k,
                        ink,
                        &map_title(&r.map),
                    );
                    p.num(
                        rect.x + 14.0 * k,
                        rect.y + 28.0 * k,
                        1.0 * k,
                        theme::faded(theme::FAINT, a * 1.6),
                        &crate::demo::utc_label(r.recorded_at),
                    );
                    p.num_right(
                        rect.x + rect.w - 14.0 * k,
                        rect.y + 9.0 * k,
                        1.3 * k,
                        theme::faded(theme::AMBER, a),
                        &length_label(r.duration_ms),
                    );
                    p.num_right(
                        rect.x + rect.w - 14.0 * k,
                        rect.y + 28.0 * k,
                        1.0 * k,
                        theme::faded(theme::FAINT, a * 1.6),
                        &match r.kills {
                            1 => "1 FRAG".to_string(),
                            n => format!("{n} FRAGS"),
                        },
                    );
                }
                Target::ReplayExport => {
                    let label = if self.export_blocked.is_some() {
                        "EXPORT MP4  /  NEEDS FFMPEG"
                    } else {
                        "EXPORT MP4"
                    };
                    button(
                        p,
                        rect,
                        k,
                        label,
                        false,
                        hot && self.export_blocked.is_none(),
                    );
                }
                Target::ReplayFolder => button(p, rect, k, "OPEN FOLDER", false, hot),
                Target::Go => {
                    let (ready, sub) = match self.selected_replay() {
                        Some(r) => (
                            true,
                            format!("{}  /  {}", map_title(&r.map), length_label(r.duration_ms)),
                        ),
                        None => (false, "NOTHING SELECTED".to_string()),
                    };
                    go_button(p, rect, k, hot, ready, "WATCH", &sub);
                }
                _ => {}
            }
        }

        let Some(r) = self.selected_replay() else {
            return;
        };
        heading(p, panel_x, top, k, "RECORDING");
        p.text(
            panel_x,
            top + 30.0 * k,
            3.2 * k,
            theme::TEXT,
            &map_title(&r.map),
        );
        let rows = [
            ("RECORDED", crate::demo::utc_label(r.recorded_at)),
            ("LENGTH", length_label(r.duration_ms)),
            ("FRAGS", r.kills.to_string()),
            (
                "THROUGH THE EYES OF",
                r.pov_name
                    .clone()
                    .unwrap_or_else(|| "-".to_string())
                    .to_uppercase(),
            ),
        ];
        for (i, (label, value)) in rows.iter().enumerate() {
            let y = top + (84.0 + 26.0 * i as f32) * k;
            p.num(panel_x, y, 1.0 * k, theme::FAINT, label);
            p.num_right(right, y, 1.1 * k, theme::TEXT_DIM, value);
            p.rect(panel_x, y + 18.0 * k, right - panel_x, 1.0, theme::GRID);
        }
        let note = match &self.export_blocked {
            Some(why) => why.clone(),
            None => "EXPORT PLAYS IT INTO AN MP4 AT 60 FPS, BESIDE THE DEMO".to_string(),
        };
        p.num(panel_x, top + 266.0 * k, 0.95 * k, theme::FAINT, &note);
        p.num(
            panel_x,
            top + 286.0 * k,
            0.95 * k,
            theme::FAINT,
            "WATCHING: P PLAYS  /  ARROWS SEEK AND SPEED  /  B NEXT FRAG  /  F6 FREECAM",
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn front() -> Front {
        let mut f = Front::new("games.example", "Operative-7");
        f.show(Screen::Home);
        f.clock = 10.0;
        f.set_tab(Tab::Replays);
        f.replays = Some(vec![
            ReplayView {
                path: "/demos/dust2-1.hademo".into(),
                map: "hd_dust2".into(),
                recorded_at: 1.79e12,
                duration_ms: 247_000.0,
                kills: 6,
                pov_name: Some("Sable".into()),
            },
            ReplayView {
                path: "/demos/aim-1.hademo".into(),
                map: "hd_aim".into(),
                recorded_at: 1.78e12,
                duration_ms: 61_000.0,
                kills: 1,
                pov_name: None,
            },
        ]);
        f
    }

    fn click(f: &mut Front, target: Target) -> Option<Intent> {
        let hit = f
            .hits(None, 1280.0, 720.0)
            .into_iter()
            .find(|(t, _)| *t == target)
            .unwrap_or_else(|| panic!("{target:?} is not on screen"))
            .1;
        f.pointer = (hit.x + hit.w * 0.5, hit.y + hit.h * 0.5);
        f.click(None, 1280.0, 720.0)
    }

    #[test]
    fn lengths_read_as_a_clock() {
        assert_eq!(length_label(61_000.0), "1:01");
        assert_eq!(length_label(3_750_000.0), "1:02:30");
        assert_eq!(length_label(400.0), "0:00");
    }

    #[test]
    fn the_selected_recording_is_watched_and_exported() {
        let mut f = front();
        assert_eq!(click(&mut f, Target::Replay(1)), None);
        assert_eq!(
            click(&mut f, Target::Go),
            Some(Intent::WatchReplay {
                path: "/demos/aim-1.hademo".into(),
                map: "hd_aim".into()
            })
        );
        assert_eq!(
            click(&mut f, Target::ReplayExport),
            Some(Intent::ExportReplay {
                path: "/demos/aim-1.hademo".into(),
                map: "hd_aim".into()
            })
        );
        assert_eq!(
            click(&mut f, Target::ReplayFolder),
            Some(Intent::RevealReplays)
        );
    }

    #[test]
    fn export_without_ffmpeg_says_why_instead() {
        let mut f = front();
        f.export_blocked = Some("NO FFMPEG ON THIS SYSTEM'S PATH".into());
        assert_eq!(click(&mut f, Target::ReplayExport), None);
        assert!(f.notice.as_ref().is_some_and(|(n, _)| n.contains("FFMPEG")));
    }

    #[test]
    fn opening_the_tab_asks_for_the_list_and_arrows_walk_it() {
        let mut f = front();
        f.set_tab(Tab::Play);
        assert_eq!(
            click(&mut f, Target::Tab(Tab::Replays)),
            Some(Intent::ListReplays)
        );
        f.replays = front().replays;
        f.replay = 0;
        f.key(KeyCode::ArrowUp);
        assert_eq!(f.replay, 1, "up from the top wraps to the bottom");
        assert!(matches!(
            f.key(KeyCode::Enter),
            Some(Intent::WatchReplay { .. })
        ));
    }

    #[test]
    fn the_replays_tab_draws_inside_the_window_without_overlaps() {
        let mut f = front();
        f.replays = Some(
            (0..30)
                .map(|n| ReplayView {
                    path: format!("/d/{n}.hademo"),
                    map: "hd_dust2".into(),
                    recorded_at: 1.79e12,
                    duration_ms: 90_000.0,
                    kills: n,
                    pov_name: Some("Sable".into()),
                })
                .collect(),
        );
        for (w, h) in [(1280.0, 720.0), (1920.0, 1080.0)] {
            let hits = f.hits(None, w, h);
            for (i, (a, ra)) in hits.iter().enumerate() {
                assert!(
                    ra.x >= 0.0 && ra.y >= 0.0 && ra.x + ra.w <= w && ra.y + ra.h <= h,
                    "{a:?}"
                );
                for (b, rb) in &hits[i + 1..] {
                    let apart = ra.x >= rb.x + rb.w
                        || rb.x >= ra.x + ra.w
                        || ra.y >= rb.y + rb.h
                        || rb.y >= ra.y + ra.h;
                    assert!(apart, "{a:?} overlaps {b:?} at {w}x{h}");
                }
            }
        }
    }
}
