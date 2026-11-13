//! Render the HUD offscreen and write a PNG.
//!
//! The counterpart to `weapon_preview` and `operator_preview`, and it exists for
//! the reason `docs/architecture/hassault-two-clients.mdx` gives in its
//! checklist: *"Is it visual? Then it will not be caught by any test. Render it
//! and look."*
//!
//! Every unit test around this HUD checks a property a badly laid-out HUD would
//! still satisfy — the tray cells are wide enough, the feed is capped and
//! ordered, a flash of zero draws nothing. None of them would catch two panels
//! overlapping, a bar drawn off its own trough, a colour that vanishes against
//! the map behind it, or text running off the edge of the screen. Those are
//! properties of a picture.
//!
//! ```text
//! cargo run --manifest-path apps/native-fps/Cargo.toml --example hud_preview
//! ```
//!
//! Takes an optional output path, an optional `WIDTHxHEIGHT`, an optional
//! `summary` to draw the post-match card over the top, and an optional `defuse`
//! or `ctf` to draw the objective layer. `menu`, `menu-video` or
//! `menu-crosshair` draws that page of the pause menu over the HUD, `drill`
//! the aim drill's score card, `chat` a chat with the prompt open (emoji, CJK,
//! Arabic) and `console` the console with a scrollback (`console-list` with a
//! completion list open). `front` and its variants (`front-loadout`,
//! `front-search`, `front-loading`, ... see `front_fixture`) draw a screen of
//! the front end instead of the HUD. The size is a parameter
//! because the whole layout is derived from one unit, `u = height / 360`, so a
//! HUD that reads at 1080p can still be a smear at 720p and overlapping at 4K —
//! and the only way to know is to render all three.
//!
//! The summary card rides this example rather than having one of its own,
//! because what has to be checked about it is exactly what it looks like *over a
//! live HUD*: the card is drawn last and its scrim is the only thing stopping
//! the ammo counter and the kill feed showing through it.
//!
//! `full` and `low` swap the wounded player for one at 100/100 with a full
//! magazine and one nearly dead with nothing left; `flash` whites the screen
//! as a flashbang half way through its fade does.
//!
//! `radar` draws the radar over a synthetic floor plan with one of every layer
//! on it — bands, walls, sites, a planted bomb, teammates, a spotted enemy, a
//! last-known `?`, a gunfire dot mid-arrival, footstep blobs and the own-noise
//! ring — and `northup` turns rotation off with it. Every one of those layers is
//! pinned by numbers in `radar.rs`; none of the numbers says whether they read.
//!
//! The scene behind it is a **mid-grey gradient with a bright band**, not black.
//! A HUD checked against black passes with translucent panels that turn out to
//! be unreadable over a lit wall, which is the failure this is most likely to
//! have.

use hassault_native::damage::Placed;
use hassault_native::hud::{Hud, HudView, OverlayVertex, ScoreRow, UtilitySlot, UtilityView};
use hassault_native::overlay_gpu::OverlayGpu;
use hassault_native::protocol::{BuyItem, ModeBomb, ModeFlag, ModeInfo, ModeSelf, ModeShared};
use hassault_native::protocol::{HeardRow, ModeSite, NoiseEvent, PlayerRow};
use hassault_native::radar::{self, Blip, Edge, Mark, RadarSettings, Run};
use hassault_native::hud::RadarView;
use hassault_native::protocol::{Fx, HitMarker, HurtMarker, SelfState};
use hassault_native::summary::{MatchTally, Summary, SummaryScreen};

const FORMAT: wgpu::TextureFormat = wgpu::TextureFormat::Rgba8UnormSrgb;

fn main() {
    let mut args = std::env::args().skip(1);
    let path = args.next().unwrap_or("hud.png".into());
    let (width, height) = args
        .next()
        .and_then(|s| {
            let (w, h) = s.split_once('x')?;
            Some((w.parse().ok()?, h.parse().ok()?))
        })
        .unwrap_or((1600u32, 900u32));
    let rest: Vec<String> = args.collect();
    let summary = rest.iter().any(|a| a == "summary");
    let live = rest.iter().any(|a| a == "live");
    let nobuy = rest.iter().any(|a| a == "nobuy");
    // Which objective layer to draw, if any. A parameter because there is no
    // other way to see it: the mode HUD is the part of this client with no unit
    // test that could catch a banner printing through the round clock, which is
    // exactly what this example exists for.
    let mode = rest
        .iter()
        .find(|a| matches!(a.as_str(), "defuse" | "ctf"))
        .cloned();
    pollster::block_on(run(&path, width, height, summary, live, nobuy, mode.as_deref()));
}

/// A player mid-firefight: hurt, part way through a magazine, reloading, with
/// armour on and damage arriving from two sides.
///
/// Deliberately **not** a fresh spawn. A HUD at 100/100 with a full magazine
/// draws every bar full and every counter at its widest, which is the one state
/// in which none of the things that go wrong can go wrong.
fn wounded() -> SelfState {
    SelfState {
        hp: 38.0,
        armour: 21.0,
        alive: true,
        weapon: 2,
        ammo: 7,
        reserve: 84,
        mag: 20,
        reloading: true,
        reload_in: 0.9,
        kills: 11,
        deaths: 4,
        hits: vec![HitMarker {
            victim: "b".into(),
            damage: 21.0,
            head: false,
            killed: false,
        }],
        hurt: vec![
            HurtMarker {
                bearing: 2.4,
                amount: 21.0,
            },
            HurtMarker {
                bearing: -1.1,
                amount: 12.0,
            },
        ],
        ..Default::default()
    }
}

fn rows() -> Vec<ScoreRow> {
    let mk = |name: &str, kills, deaths, you, bot, team| ScoreRow {
        name: name.into(),
        kills,
        deaths,
        you,
        bot,
        team,
    };
    vec![
        mk("VALKYRIE", 19, 6, false, false, 0),
        mk("HORRIBLECPP", 11, 4, true, false, 0),
        mk("RECON-04", 9, 11, false, true, 1),
        mk("SABLE", 8, 9, false, false, 1),
        mk("MERIDIAN", 3, 14, false, false, 0),
        mk("RECON-07", 2, 15, false, true, 1),
    ]
}

/// A defuse or capture-the-flag state worth looking at.
///
/// Deliberately the *awkward* moment in each rather than a tidy one: a defuse
/// with the bomb down and the fuse nearly out, and a CTF with our flag taken and
/// theirs dropped. A HUD checked against the calm case passes with a layout that
/// collides the moment two things are true at once.
fn mode_fixture(which: &str) -> (ModeInfo, ModeShared, ModeSelf) {
    match which {
        "defuse" => (
            ModeInfo {
                id: "defuse".into(),
                name: "Bomb Defuse".into(),
                score_label: "Rounds".into(),
                v: 1,
                teams: true,
                // A real catalogue, because the menu is the half of an economy
                // that has to be *looked at*: prices right-aligned against
                // names of very different lengths is exactly the layout a unit
                // test would pass and a screenshot would not.
                catalog: vec![
                    BuyItem {
                        id: "assault".into(),
                        name: "Assault Rifle".into(),
                        kind: "weapon".into(),
                        slot: 2,
                        price: 2700,
                        ..Default::default()
                    },
                    BuyItem {
                        id: "shotgun".into(),
                        name: "Shotgun".into(),
                        kind: "weapon".into(),
                        slot: 3,
                        price: 1800,
                        ..Default::default()
                    },
                    BuyItem {
                        id: "sniper".into(),
                        name: "Sniper Rifle".into(),
                        kind: "weapon".into(),
                        slot: 4,
                        price: 4750,
                        ..Default::default()
                    },
                    BuyItem {
                        id: "armour".into(),
                        name: "Armour".into(),
                        kind: "armour".into(),
                        slot: -1,
                        price: 1000,
                        ..Default::default()
                    },
                    BuyItem {
                        id: "he".into(),
                        name: "HE Grenade".into(),
                        kind: "nade".into(),
                        slot: 0,
                        price: 300,
                        ..Default::default()
                    },
                    BuyItem {
                        id: "flash".into(),
                        name: "Flashbang".into(),
                        kind: "nade".into(),
                        slot: 1,
                        price: 200,
                        ..Default::default()
                    },
                    BuyItem {
                        id: "smoke".into(),
                        name: "Smoke Grenade".into(),
                        kind: "nade".into(),
                        slot: 2,
                        price: 300,
                        ..Default::default()
                    },
                    BuyItem {
                        id: "molotov".into(),
                        name: "Incendiary".into(),
                        kind: "nade".into(),
                        slot: 3,
                        price: 600,
                        ..Default::default()
                    },
                ],
                ..Default::default()
            },
            ModeShared {
                phase: "live".into(),
                phase_in: 41.0,
                round: 7,
                attackers: 1,
                swapped: true,
                bomb: ModeBomb {
                    state: "planted".into(),
                    site: "A".into(),
                    x: 63.5,
                    y: 28.5,
                    fuse_in: 4.2,
                    ..Default::default()
                },
                ..Default::default()
            },
            ModeSelf {
                attacking: false,
                progress: 0.62,
                progress_kind: "defuse".into(),
                // Mid-range on purpose: some rows affordable and some not is
                // what makes the three states visible at once.
                money: 2000,
                can_buy: true,
                buy_in: 14.0,
                bought: vec![3, 5],
                ..Default::default()
            },
        ),
        _ => (
            ModeInfo {
                id: "ctf".into(),
                name: "Capture the Flag".into(),
                score_label: "Captures".into(),
                v: 1,
                teams: true,
                ..Default::default()
            },
            ModeShared {
                flags: vec![
                    ModeFlag {
                        team: 0,
                        state: "carried".into(),
                        by: "someone".into(),
                        ..Default::default()
                    },
                    ModeFlag {
                        team: 1,
                        state: "dropped".into(),
                        return_in: 12.0,
                        ..Default::default()
                    },
                ],
                ..Default::default()
            },
            ModeSelf {
                carrying: true,
                captures: 2,
                ..Default::default()
            },
        ),
    }
}

async fn run(path: &str, width: u32, height: u32, summary: bool, live: bool, nobuy: bool, mode_name: Option<&str>) {
    let instance = wgpu::Instance::default();
    let adapter = instance
        .request_adapter(&wgpu::RequestAdapterOptions::default())
        .await
        .expect("no GPU adapter — this example needs a real one");
    println!("adapter: {}", adapter.get_info().name);
    let (device, queue) = adapter
        .request_device(&wgpu::DeviceDescriptor {
            label: Some("hud-preview"),
            ..Default::default()
        })
        .await
        .expect("device");

    // The HUD's own state, advanced far enough for the entrance cascade to have
    // finished and for one kill to have aged into a fade.
    let mut hud = Hud::default();
    hud.note("HORRIBLECPP  X  SABLE", true);
    hud.note("VALKYRIE  X  MERIDIAN", false);
    hud.note("RECON-04  X  HORRIBLECPP", false);
    // **Healthy first, then hurt.** The lag trail is driven by a *drop*, and
    // `on_self` deliberately ignores the first sample it ever sees — there is no
    // previous value to have fallen from. Seeding the HUD straight into the
    // wounded state would draw no trail at all and quietly make this preview
    // useless for the one element it was added to check.
    let mut healthy = wounded();
    healthy.hp = 74.0;
    healthy.hurt.clear();
    hud.on_self(&healthy);

    // `full` and `low` are the two ends the default deliberately is not: every
    // figure at its widest (three digits in both cells of the health panel, a
    // full magazine), and nearly dead with nothing left — the red state, an
    // empty armour cell, a dry magazine and the spawn shield's notice.
    let variant = |name: &str| std::env::args().any(|a| a == name);
    let mut you = wounded();
    if variant("full") {
        you.hp = 100.0;
        you.armour = 100.0;
        you.ammo = 20;
        you.reserve = 120;
        you.reloading = false;
        you.hurt.clear();
    } else if variant("low") {
        you.hp = 18.0;
        you.armour = 0.0;
        you.ammo = 0;
        you.reloading = false;
        you.protected = true;
    }
    hud.on_self(&you);
    hud.on_hits(&you.hits);
    // A kill of ours, so the centre notice is in the picture. It is the one
    // element that sits directly above the crosshair, which makes "does it
    // collide with anything" a question only a rendering can answer.
    // Three kills without dying, so the streak reaches its first milestone and
    // the notice under the kill line is in the picture. The two share one clock,
    // so this is also the only way to see whether they collide.
    hud.on_hits(&vec![
        HitMarker {
            victim: "b".into(),
            damage: 40.0,
            head: true,
            killed: true,
        };
        3
    ]);
    hud.on_fx(
        &Fx::Kill {
            victim: "sable".into(),
            victim_name: "SABLE".into(),
            killer: "me".into(),
            killer_name: "HORRIBLECPP".into(),
            head: true,
            weapon: "assault".into(),
            nutshot: false,
            smoke: true,
            airborne: true,
            wallbang: false,
            noscope: false,
            blind: false,
            killer_team: 1,
            victim_team: 0,
            assister: String::new(),
            assister_name: String::new(),
            assister_team: 0,
        },
        "me",
    );
    // Two frames: one to register the damage, then enough time for the lag trail
    // to be part way down rather than at either end of its travel — which is the
    // only position that shows whether it is drawn in the right place.
    hud.update(0.016, true);
    hud.update(0.20, true);

    let utility = UtilityView {
        slots: vec![
            UtilitySlot {
                kind: "he".into(),
                name: "FRAG".into(),
                count: 2,
            },
            UtilitySlot {
                kind: "flash".into(),
                name: "FLASH".into(),
                count: 1,
            },
            UtilitySlot {
                kind: "smoke".into(),
                name: "SMOKE".into(),
                count: 0,
            },
            UtilitySlot {
                kind: "molotov".into(),
                name: "FIRE".into(),
                count: 1,
            },
        ],
        selected: 1,
    };
    let board = rows();

    // Floating damage numbers, in all three readings. Positions are given
    // directly here because the projection is `damage.rs`'s job and has its own
    // tests; what this preview is for is whether they are *legible* over a lit
    // wall.
    //
    // Placed **below the scoreboard**, which is where they land in play: bodies
    // you are shooting at are around the crosshair, and the board is a panel you
    // hold over the top half. The first version of this preview put all three
    // inside the board's footprint, where they are correctly but uselessly drawn
    // underneath it — which says nothing about whether the colours work.
    // The last one is deliberately left overlapping its lower edge, so the
    // layering is visible rather than only asserted.
    let numbers = [
        Placed {
            x: width as f32 * 0.40,
            y: height as f32 * 0.56,
            amount: 24,
            head: false,
            killed: false,
            fade: 1.0,
        },
        Placed {
            x: width as f32 * 0.60,
            y: height as f32 * 0.52,
            amount: 87,
            head: true,
            killed: false,
            fade: 1.0,
        },
        Placed {
            x: width as f32 * 0.66,
            y: height as f32 * 0.60,
            amount: 112,
            head: false,
            killed: true,
            fade: 0.55,
        },
        // Under the board's lower edge, where it must be covered.
        Placed {
            x: width as f32 * 0.52,
            y: height as f32 * 0.44,
            amount: 41,
            head: false,
            killed: false,
            fade: 1.0,
        },
    ];

    let fixture = mode_name.map(mode_fixture);
    // Read here rather than threaded through `run`: two flags for one fixture.
    let flags: Vec<String> = std::env::args().skip(3).collect();
    let show_radar = flags.iter().any(|a| a == "radar");
    let north_up = flags.iter().any(|a| a == "northup");
    let radar_map = radar_fixture(north_up);
    // Real fonts, loaded here and now (`HUDFONT=bitmap` for the old 5×7 font).
    let kit = hassault_native::text_kit::TextKit::blocking_from_env();
    kit.begin_frame();
    let preview_flags: Vec<String> = std::env::args().skip(3).collect();
    let show_chat = preview_flags.iter().any(|a| a == "chat");
    let mut chat = hassault_native::chat::ChatState::new();
    chat.receive("1", "p1", "constcorrectness", 0, false, "gg wp \u{1F389}");
    chat.receive("2", "p2", "horrible", 1, true, "rotate B, smoke mid \u{1F4A8}\u{1F525}");
    chat.receive("3", "p3", "\u{3055}\u{304F}\u{3089}", 1, false, "日本語もOK \u{1F44D}\u{1F3FD}");
    chat.receive("4", "p4", "\u{0644}\u{064A}\u{0644}\u{0649}", 0, false, "مرحبا 👋");
    chat.receive(
        "5",
        "p1",
        "Zoë",
        0,
        false,
        "a longer line, to see it wrap where the panel ends rather than run off under the radar or across the kill feed",
    );
    chat.open_prompt(hassault_native::chat::ChatChannel::Team);
    chat.type_text("typing an emoji \u{1F600}");

    let show_console = preview_flags.iter().any(|a| a == "console");
    let mut console = hassault_native::console::Console::default();
    let cvars = hassault_native::console::ClientCvars::default();
    if show_console {
        use hassault_native::console::Tone;
        console.open = true;
        for i in 0..60 {
            console.push(format!("net.graph {} -> snapshot {} applied in {:.2} ms", i % 4, 1200 + i, 0.3 + i as f32 * 0.01), Tone::Output);
        }
        console.push("] bot_add ct", Tone::Echo);
        console.push("bot Zoë joined CT", Tone::Note);
        console.push("unknown command: bot_kick_al", Tone::Error);
        if preview_flags.iter().any(|a| a == "console-list") {
            // A completion list: the built-ins and borrowed spellings need no
            // registry, so this is what a console with no server offers.
            console.insert("c");
        } else {
            console.insert("say naïve café");
        }
    }
    let console_lines = console.visible();
    let console_quick = console.quick_actions(&cvars);
    let console_hints = console.suggestion_hints(&cvars);
    let console_detail = console.suggestion_detail(&cvars);
    let console_view = show_console.then(|| hassault_native::hud::ConsoleView {
        lines: &console_lines,
        input: &console.input,
        cursor: console.cursor,
        scroll: 0,
        suggestions: &console.suggestions,
        suggestion: console.suggestion,
        hints: &console_hints,
        detail: console_detail.as_deref(),
        registry_loaded: true,
        filter: console.filter.label(),
        hidden: console.hidden_count(),
        room: "4f2a9c",
        map: "de_dust2",
        rtt: Some(38.0),
        cheats: Some(false),
        quick: &console_quick,
    });

    let view = HudView {
        hud_scale: 1.0,
        team: 0,
        // Held open when a mode fixture is drawn, unless live/nobuy requested.
        buy_open: fixture.is_some() && !live && !nobuy,
        // Opened on the first group, so the shot shows rows and prices: the
        // layout a unit test passes and a screenshot does not.
        buy_group: Some(0),
        mode: fixture.as_ref().map(|f| &f.0),
        mode_state: fixture.as_ref().map(|f| &f.1),
        mode_self: fixture.as_ref().map(|f| &f.2),
        width,
        height,
        you: Some(&you),
        weapon_name: "assault rifle",
        spread: 0.02,
        reload_time: 2.4,
        magnification: 1.0,
        speed: 14.2,
        move_speed: 16.0,
        yaw: 0.4,
        on_ground: true,
        crouching: false,
        underwater: false,
        playing: true,
        // Withheld when the card or live gameplay is being previewed.
        scoreboard: if summary || live { None } else { Some(&board) },
        damage: &numbers,
        scores: &[14, 11],
        rtt: Some(38.0),
        fps: Some(144.0),
        net_graph: 1,
        perf: Default::default(),
        crosshair: Default::default(),
        radar: show_radar.then(|| radar_map.view()),
        utility: Some(&utility),
        // `flash` draws the tail of a flashbang's fade over everything.
        flash: if variant("flash") { 0.55 } else { 0.0 },
        console: console_view,
        chat: show_chat.then_some(&chat),
        voice_transmitting: false,
        voice_speakers: &[],
        text: Some(&kit),
    };

    // Spatial noise bearings for the crosshair NoiseRing overlay
    hud.push_noise("shot", 0.95, 0.45, 0); // urgent amber gunshot to the right
    hud.push_noise("step", 0.65, -1.15, 1); // soft blue footstep to the left, elevated above
    hud.push_noise("step", 0.35, 2.75, -1); // subtle footstep behind and below

    let mut verts: Vec<OverlayVertex> = Vec::new();
    hud.build(&view, &mut verts);
    if summary {
        // Drawn over a finished HUD, appended rather than replacing it — which
        // is exactly what the client does, and the only arrangement in which the
        // scrim's job is visible.
        let mut screen = SummaryScreen::default();
        screen.open();
        // The card's figures arrive over a fifth of a second; wait them in, so
        // the render is the card as it is read and not its first frame.
        std::thread::sleep(std::time::Duration::from_millis(400));
        // Hovered, because the button at rest is an outline and the filled state
        // is the one that has to be checked against the card behind it.
        let (bx, by, bw, bh) = hassault_native::summary::button_rect(width as f32, height as f32);
        screen.pointer(bx + bw / 2.0, by + bh / 2.0, width as f32, height as f32);
        screen.build(
            &Summary {
                map: "hd_atrium".into(),
                name: "horriblecpp".into(),
                kills: 11,
                deaths: 4,
                tally: MatchTally {
                    hits: 138,
                    head_kills: 3,
                },
                opponents: 5,
                // Non-zero so the preview renders the substituted cell — the
                // one a defuse card shows and a deathmatch card does not.
                objectives: 3,
                won: false,
                mvp: false,
                recordable: true,
            },
            width as f32,
            height as f32,
            Some(&kit),
            &mut verts,
        );
    }
    let flags: Vec<String> = std::env::args().skip(3).collect();
    if let Some(page) = flags.iter().find_map(|a| match a.as_str() {
        "menu" => Some(hassault_native::menu::Page::Root),
        "menu-video" => Some(hassault_native::menu::Page::Video),
        "menu-crosshair" => Some(hassault_native::menu::Page::Crosshair),
        _ => None,
    }) {
        let mut menu = hassault_native::menu::Menu::default();
        menu.toggle();
        menu.page = page;
        menu.build(
            &hassault_native::settings::Settings::default(),
            &hassault_native::controls::Controls::default(),
            true,
            width as f32,
            height as f32,
            Some(&kit),
            &mut verts,
        );
    }
    if flags.iter().any(|a| a == "drill") {
        use hassault_native::training::{DrillPhase, DrillStatus};
        hassault_native::drill_hud::build(
            &DrillStatus {
                id: "flick".into(),
                name: "Flick".into(),
                phase: DrillPhase::Done,
                remaining: 0.0,
                stats: Some(hassault_native::drills::DrillStats {
                    shots: 64,
                    hit_shots: 41,
                    accuracy: 41.0 / 64.0,
                    hits: 47,
                    head_hits: 12,
                    headshot_share: 12.0 / 47.0,
                    kills: 30,
                    kills_per_minute: 30.0,
                    median_ttk: Some(0.412),
                    median_reaction: Some(0.268),
                }),
            },
            width as f32,
            height as f32,
            Some(&kit),
            &mut verts,
        );
    }
    // The front end is a whole screen, not a layer: it replaces the HUD.
    if let Some(which) = flags.iter().find(|a| a.starts_with("front")) {
        verts.clear();
        front_fixture(which).paint(Some(&kit), width as f32, height as f32, &mut verts);
    }
    println!(
        "{} overlay vertices ({:.1}% of the client's 65536 cap)",
        verts.len(),
        verts.len() as f32 / 65536.0 * 100.0
    );

    let target = device.create_texture(&wgpu::TextureDescriptor {
        label: Some("hud-target"),
        size: wgpu::Extent3d {
            width,
            height,
            depth_or_array_layers: 1,
        },
        mip_level_count: 1,
        sample_count: 1,
        dimension: wgpu::TextureDimension::D2,
        format: FORMAT,
        usage: wgpu::TextureUsages::RENDER_ATTACHMENT | wgpu::TextureUsages::COPY_SRC,
        view_formats: &[],
    });
    let target_view = target.create_view(&Default::default());

    // The stand-in for a map: a vertical gradient with a bright band across the
    // middle, drawn with the same pipeline. Panels have to stay readable over
    // the bright part, which black would never ask of them.
    let mut backdrop: Vec<OverlayVertex> = Vec::new();
    const BANDS: usize = 24;
    for i in 0..BANDS {
        let t0 = i as f32 / BANDS as f32;
        let t1 = (i + 1) as f32 / BANDS as f32;
        // Dark sky, a lit band where a wall would catch the sun, dark floor.
        let lum = 0.10 + 0.42 * (1.0 - (t0 * 2.0 - 1.0).abs()).powf(2.5);
        let color = [lum * 0.82, lum * 0.88, lum, 1.0];
        let (y0, y1) = (1.0 - t0 * 2.0, 1.0 - t1 * 2.0);
        for position in [
            [-1.0, y0],
            [1.0, y0],
            [1.0, y1],
            [-1.0, y0],
            [1.0, y1],
            [-1.0, y1],
        ] {
            backdrop.push(OverlayVertex::solid(position, color));
        }
    }

    let all: Vec<OverlayVertex> = backdrop.iter().copied().chain(verts).collect();
    // The client's own overlay pipeline and atlas, not a restatement of them.
    let mut overlay = OverlayGpu::new(&device, FORMAT, all.len().max(1));
    overlay.set_vertices(&queue, &all);
    if let Some(upload) = kit.take_upload() {
        overlay.upload_atlas(&queue, &upload);
    }

    let mut encoder = device.create_command_encoder(&Default::default());
    {
        let mut pass = encoder.begin_render_pass(&wgpu::RenderPassDescriptor {
            label: Some("overlay"),
            color_attachments: &[Some(wgpu::RenderPassColorAttachment {
                view: &target_view,
                depth_slice: None,
                resolve_target: None,
                ops: wgpu::Operations {
                    load: wgpu::LoadOp::Clear(wgpu::Color::BLACK),
                    store: wgpu::StoreOp::Store,
                },
            })],
            depth_stencil_attachment: None,
            timestamp_writes: None,
            occlusion_query_set: None,
            multiview_mask: None,
        });
        overlay.draw(&mut pass);
    }

    // Readback. `bytes_per_row` must be a multiple of 256, which is why the
    // rows are copied into a padded buffer and unpadded on the way out rather
    // than mapped straight into the encoder.
    let padded = (width * 4).div_ceil(256) * 256;
    let readback = device.create_buffer(&wgpu::BufferDescriptor {
        label: Some("readback"),
        size: (padded * height) as wgpu::BufferAddress,
        usage: wgpu::BufferUsages::COPY_DST | wgpu::BufferUsages::MAP_READ,
        mapped_at_creation: false,
    });
    encoder.copy_texture_to_buffer(
        wgpu::TexelCopyTextureInfo {
            texture: &target,
            mip_level: 0,
            origin: wgpu::Origin3d::ZERO,
            aspect: wgpu::TextureAspect::All,
        },
        wgpu::TexelCopyBufferInfo {
            buffer: &readback,
            layout: wgpu::TexelCopyBufferLayout {
                offset: 0,
                bytes_per_row: Some(padded),
                rows_per_image: Some(height),
            },
        },
        wgpu::Extent3d {
            width,
            height,
            depth_or_array_layers: 1,
        },
    );
    queue.submit([encoder.finish()]);

    let slice = readback.slice(..);
    slice.map_async(wgpu::MapMode::Read, |r| r.expect("map"));
    device
        .poll(wgpu::PollType::Wait {
            submission_index: None,
            timeout: None,
        })
        .expect("poll");
    let data = slice.get_mapped_range().expect("map range");
    let mut pixels = Vec::with_capacity((width * height * 4) as usize);
    for row in 0..height {
        let start = (row * padded) as usize;
        pixels.extend_from_slice(&data[start..start + (width * 4) as usize]);
    }
    drop(data);
    readback.unmap();

    let file = std::fs::File::create(path).expect("create png");
    let mut enc = png::Encoder::new(std::io::BufWriter::new(file), width, height);
    enc.set_color(png::ColorType::Rgba);
    enc.set_depth(png::BitDepth::Eight);
    enc.write_header()
        .expect("header")
        .write_image_data(&pixels)
        .expect("write");
    println!("wrote {path} at {width}x{height}");
}

/// A small two-level map with a corridor, two sites and something of every
/// kind on the radar.
struct RadarFixture {
    plan: Vec<Run>,
    edges: Vec<Edge>,
    blips: Vec<Blip>,
    marks: Vec<Mark>,
    sites: Vec<ModeSite>,
    settings: RadarSettings,
}

impl RadarFixture {
    fn view(&self) -> RadarView<'_> {
        RadarView {
            plan: &self.plan,
            edges: &self.edges,
            x: 40.0,
            y: 52.0,
            yaw: 0.4,
            blips: &self.blips,
            marks: &self.marks,
            sites: &self.sites,
            settings: self.settings,
            ssize: 96.0,
            scores: false,
            zoom: false,
            own_noise: Some((42.0, 0.55)),
        }
    }
}

fn radar_fixture(north_up: bool) -> RadarFixture {
    const N: i32 = 96;
    // Rooms carved out of solid: (x0, y0, x1, y1, floor).
    let rooms: [(i32, i32, i32, i32, f32); 7] = [
        (6, 40, 34, 66, 0.0),   // T spawn
        (34, 48, 62, 54, 0.0),  // mid corridor
        (62, 30, 88, 60, 4.0),  // site A, raised
        (40, 12, 46, 48, 0.0),  // connector north
        (14, 8, 52, 18, 2.0),   // site B
        (52, 60, 58, 86, 0.0),  // lower tunnel
        (30, 80, 70, 90, -2.0), // the pit
    ];
    let floor = |x: i32, y: i32| -> Option<f32> {
        rooms
            .iter()
            .find(|(x0, y0, x1, y1, _)| x >= *x0 && x < *x1 && y >= *y0 && y < *y1)
            .map(|r| r.4)
    };
    let (lo, hi) = (-2.0, 4.0);
    let mut plan = Vec::new();
    for y in 0..N {
        let mut start: Option<(i32, u8)> = None;
        for x in 0..=N {
            let band = (x < N).then(|| floor(x, y)).flatten().map(|f| radar::floor_band(f, lo, hi));
            match (band, start) {
                (Some(b), None) => start = Some((x, b)),
                (Some(b), Some((x0, open))) if b != open => {
                    plan.push(Run { y: y as f32 + 0.5, x0: x0 as f32, x1: x as f32, band: open });
                    start = Some((x, b));
                }
                (None, Some((x0, open))) => {
                    plan.push(Run { y: y as f32 + 0.5, x0: x0 as f32, x1: x as f32, band: open });
                    start = None;
                }
                _ => {}
            }
        }
    }
    let edges = radar::wall_edges(N, |x, y| floor(x, y).is_none());
    let blips = vec![
        Blip { x: 28.0, y: 58.0, friendly: true },
        Blip { x: 44.0, y: 30.0, friendly: true },
        Blip { x: 70.0, y: 50.0, friendly: false },
    ];
    // Built through the real memory, so the preview draws what the app would.
    let mut memory = radar::Memory::default();
    let row = |id: &str, x: f32, y: f32| PlayerRow {
        id: id.into(),
        team: 1,
        alive: true,
        x,
        y,
        ..Default::default()
    };
    memory.track(&[row("lurker", 56.0, 70.0)], "me", 0, &["lurker".into()], 0.0);
    memory.track(&[row("lurker", 56.0, 76.0)], "me", 0, &[], 0.5);
    memory.hear(
        &[
            NoiseEvent { kind: "step".into(), bearing: -1.2, foe: true, est: Some(24.0), ..Default::default() },
            NoiseEvent { kind: "land".into(), bearing: 2.6, foe: true, est: Some(16.0), ..Default::default() },
        ],
        (40.0, 52.0),
        1.0,
    );
    let heard = [HeardRow { id: "awper".into(), x: 74.0, y: 36.0, age: 0.2 }];
    let bomb = ModeBomb {
        state: "planted".into(),
        site: "a".into(),
        x: 80.0,
        y: 40.0,
        fuse_in: 18.0,
        ..Default::default()
    };
    let mut marks = Vec::new();
    memory.marks(1.3, &heard, Some(&bomb), None, true, &mut marks);
    let sites = vec![
        ModeSite { id: "a".into(), x: 78.0, y: 42.0, z: 4.0, radius: 7.0 },
        ModeSite { id: "b".into(), x: 26.0, y: 13.0, z: 2.0, radius: 5.0 },
    ];
    let settings = RadarSettings {
        rotate: !north_up,
        hud_scale: 1.3,
        ..RadarSettings::default()
    };
    RadarFixture { plan, edges, blips, marks, sites, settings }
}

/// The front end with one of everything on it, on the screen `which` names:
/// `front` (play), `front-browse`, `front-practice`, `front-loadout`,
/// `front-operator`, `front-search`, `front-found`, `front-loading`,
/// `front-boot`, `front-bootfail`, `front-notice`, `front-name`.
fn front_fixture(which: &str) -> hassault_native::front::Front {
    use hassault_native::api::WeaponSpec;
    use hassault_native::front::*;
    use hassault_native::menu::{LoadoutAgent, LoadoutWeapon};

    let mut f = Front::new("horrible-games.fly.dev", "Operative-417");
    f.maps = [
        ("hd_aim", 8_763),
        ("hd_assault", 69_567),
        ("hd_bank", 105_213),
        ("hd_dust2", 136_472),
        ("hd_facility", 49_293),
        ("hd_inferno", 85_431),
        ("hd_junkflea", 42_359),
        ("hd_mirage", 80_914),
        ("hd_nuke", 98_532),
        ("hd_office", 97_286),
    ]
    .iter()
    .map(|(name, size)| MapEntry {
        name: name.to_string(),
        size: *size,
    })
    .collect();
    f.modes = [
        ("dm", "Deathmatch"),
        ("tdm", "Team Deathmatch"),
        ("defuse", "Bomb Defuse"),
    ]
    .iter()
    .map(|(id, name)| ModeEntry {
        id: id.to_string(),
        name: name.to_string(),
    })
    .collect();
    f.rooms = [
        ("3d8081a0-77c1", "hd_dust2", "defuse", 7),
        ("a41f09be-1c20", "hd_mirage", "tdm", 4),
        ("0b77e5d2-9a01", "hd_aim", "dm", 10),
        ("f2c96a13-55d0", "hd_office", "dm", 1),
    ]
    .iter()
    .map(|(id, map, mode, players)| RoomEntry {
        id: id.to_string(),
        map: map.to_string(),
        players: *players,
        seats: 10,
        mode: mode.to_string(),
    })
    .collect();
    // (id, name, slot, side, damage, rpm, mag, range, head, reload, kick, auto, scoped)
    #[allow(clippy::type_complexity)]
    let guns: [(&str, &str, &str, &str, f32, f32, i32, f32, f32, f32, f32, bool, bool); 6] = [
        ("assault", "Assault Rifle", "primary", "any", 26.0, 600.0, 30, 220.0, 3.5, 2.4, 1.4, true, false),
        ("raider", "Raider", "primary", "t", 34.0, 560.0, 30, 240.0, 4.0, 2.5, 2.2, true, false),
        ("lancer", "Lancer", "primary", "ct", 30.0, 640.0, 30, 260.0, 4.0, 3.1, 1.6, true, false),
        ("sniper", "Sniper Rifle", "primary", "any", 96.0, 44.0, 5, 800.0, 2.5, 3.6, 4.0, false, true),
        ("pistol", "Pistol", "secondary", "any", 14.0, 420.0, 10, 140.0, 2.2, 1.4, 0.0, false, false),
        ("hand_cannon", "Hand Cannon", "secondary", "any", 53.0, 220.0, 7, 200.0, 2.0, 2.2, 3.0, false, false),
    ];
    for (id, name, slot, side, damage, rpm, mag, range, head, reload, kick, auto, scoped) in guns {
        f.loadout.weapons.push(LoadoutWeapon {
            id: id.into(),
            name: name.into(),
            slot: slot.into(),
            side: side.into(),
        });
        f.weapons.push(WeaponSpec {
            id: id.into(),
            name: name.into(),
            slot: slot.into(),
            side: side.into(),
            model: id.into(),
            damage,
            rpm,
            mag,
            range,
            head_multiplier: head,
            reload_time: reload,
            kickback: kick,
            auto,
            pellets: 1,
            zoom_levels: if scoped { vec![2.0, 4.0] } else { Vec::new() },
            ..Default::default()
        });
    }
    f.loadout.agents = vec![
        LoadoutAgent {
            id: "sable".into(),
            name: "Sable".into(),
            sides: vec!["t".into()],
        },
        LoadoutAgent {
            id: "meridian".into(),
            name: "Meridian".into(),
            sides: vec!["ct".into()],
        },
    ];
    f.show(Screen::Home);
    f.map = Some(3);
    f.mode = 1;
    // Long past every entrance: a preview is of the settled screen.
    f.clock = 30.0;
    // Over the selected kind, so a hover state is in the picture too.
    f.pointer = (width_hint() * 0.5, 300.0);

    let boot = ["WEAPONS", "GRENADES", "MAPS AND GAMES", "OPERATORS", "CONSOLE COMMANDS", "OPEN ROOMS"];
    match which {
        "front-browse" => f.kind = PlayKind::Browse,
        "front-practice" => f.kind = PlayKind::Practice,
        "front-loadout" => {
            f.set_tab(Tab::Loadout);
            f.clock = 60.0;
        }
        "front-operator" => {
            f.set_tab(Tab::Loadout);
            f.slot = 2;
            f.side = 1;
            f.clock = 60.0;
        }
        "front-search" => {
            f.begin_search();
            f.clock += 3.4;
        }
        "front-found" => {
            f.begin_search();
            f.clock += 1.2;
            f.search.found = Some(Found::Room(f.rooms[0].clone()));
            f.search.status = "DUST2  /  BOMB DEFUSE  /  7 OF 10 PLAYING".into();
        }
        "front-loading" => {
            f.begin_loading(
                "hd_dust2",
                "BOMB DEFUSE",
                "ROOM 3D8081A0",
                &["FETCH THE MAP", "BUILD THE GEOMETRY", "CONNECT", "JOIN THE ROOM", "START THE RENDERER"],
            );
            f.step(0, StepState::Done, "136 KB");
            f.step(1, StepState::Done, "48,212 TRIANGLES");
            f.step(2, StepState::Running, "HORRIBLE-GAMES.FLY.DEV");
            // A plausible floor plan: rooms and corridors as merged runs.
            let size = 96.0;
            let mut plan = Vec::new();
            for y in 8..88 {
                let yf = y as f32;
                for (x0, x1, y0, y1, band) in [
                    (10.0, 38.0, 10.0, 34.0, 0u8),
                    (38.0, 52.0, 18.0, 24.0, 1),
                    (52.0, 86.0, 8.0, 40.0, 1),
                    (20.0, 28.0, 34.0, 60.0, 0),
                    (10.0, 44.0, 60.0, 86.0, 2),
                    (44.0, 64.0, 70.0, 76.0, 1),
                    (64.0, 88.0, 52.0, 88.0, 0),
                    (70.0, 78.0, 40.0, 52.0, 1),
                ] {
                    if yf >= y0 && yf < y1 {
                        plan.push(hassault_native::radar::Run {
                            y: yf,
                            x0,
                            x1,
                            band,
                        });
                    }
                }
            }
            f.loading.plan = plan;
            f.loading.plan_size = size;
            f.clock += 4.0;
        }
        // The loading screen over a **real** map's floor plan, fetched from the
        // server named in `HASSAULT_PREVIEW_SERVER` (a local game server). The
        // synthetic plan above is rooms and corridors; a real one is thousands
        // of thin runs, and only a real one says whether that reads.
        "front-loading-real" => {
            let server = std::env::var("HASSAULT_PREVIEW_SERVER")
                .unwrap_or_else(|_| "http://127.0.0.1:9200".into());
            let map = std::env::var("HASSAULT_PREVIEW_MAP").unwrap_or_else(|_| "hd_dust2".into());
            let node = hassault_native::api::NodeApi::new(&server);
            let info = node.map_info(&map).expect("the map's info");
            let cubes = node
                .map_cubes(&map, info.cubic_size * info.plane_order.len())
                .expect("the map's cubes");
            let size = info.ssize as f32;
            let world = hassault_native::world::World::new(info, &cubes).expect("a world");
            f.begin_loading(&map, "DEATHMATCH", "A NEW ROOM", &["FETCH THE MAP", "BUILD THE WORLD", "CONNECT"]);
            f.step(0, StepState::Done, "576 KB");
            f.step(1, StepState::Done, "256 X 256 GRID");
            f.step(2, StepState::Running, "");
            f.loading.plan = hassault_native::radar::floor_plan(&world);
            f.loading.plan_size = size;
            println!("{} runs on a {size} grid", f.loading.plan.len());
            f.clock += 4.0;
        }
        "front-boot" => {
            f.show(Screen::Boot);
            f.steps = boot.iter().map(|s| Step::waiting(s)).collect();
            f.step(0, StepState::Done, "12");
            f.step(1, StepState::Done, "4");
            f.step(2, StepState::Running, "");
        }
        "front-bootfail" => {
            f.show(Screen::Boot);
            f.steps = boot.iter().map(|s| Step::waiting(s)).collect();
            f.step(0, StepState::Failed, "TIMED OUT");
            f.boot_failed = Some("the request timed out after 8 seconds - check the connection and try again".into());
        }
        "front-notice" => {
            f.notice = Some((
                "THE SERVER REFUSED THE JOIN: 'hd_aim' cannot host Bomb Defuse".into(),
                true,
            ));
        }
        "front-name" => f.editing = Some("Sab".into()),
        // The guest's INVENTORY tab: the sign-in page, idle, waiting on the
        // browser with a device code, and refused.
        "front-signin" => {
            f.tab = Tab::Inventory;
            f.providers = [true, false];
        }
        "front-signin-wait" => {
            f.tab = Tab::Inventory;
            f.providers = [true, true];
            f.signin = SignIn::Waiting {
                provider: 0,
                url: "https://github.com/login/device".into(),
                code: Some("WDJB-MJHT".into()),
            };
        }
        "front-signin-fail" => {
            f.tab = Tab::Inventory;
            f.providers = [true, true];
            f.signin = SignIn::Failed("the server did not answer".into());
        }
        _ => {}
    }
    f
}

/// The pointer's x for the fixture: over the left column whatever the size.
fn width_hint() -> f32 {
    360.0
}
