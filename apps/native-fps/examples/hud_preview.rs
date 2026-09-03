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
//! or `ctf` to draw the objective layer. The size is a parameter
//! because the whole layout is derived from one unit, `u = height / 360`, so a
//! HUD that reads at 1080p can still be a smear at 720p and overlapping at 4K —
//! and the only way to know is to render all three.
//!
//! The summary card rides this example rather than having one of its own,
//! because what has to be checked about it is exactly what it looks like *over a
//! live HUD*: the card is drawn last and its scrim is the only thing stopping
//! the ammo counter and the kill feed showing through it.
//!
//! The scene behind it is a **mid-grey gradient with a bright band**, not black.
//! A HUD checked against black passes with translucent panels that turn out to
//! be unreadable over a lit wall, which is the failure this is most likely to
//! have.

use hassault_native::damage::Placed;
use hassault_native::hud::{Hud, HudView, OverlayVertex, ScoreRow, UtilitySlot, UtilityView};
use hassault_native::protocol::{Fx, HitMarker, HurtMarker, SelfState};
use hassault_native::protocol::{ModeBomb, ModeFlag, ModeInfo, ModeSelf, ModeShared};
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
    // Which objective layer to draw, if any. A parameter because there is no
    // other way to see it: the mode HUD is the part of this client with no unit
    // test that could catch a banner printing through the round clock, which is
    // exactly what this example exists for.
    let mode = rest
        .iter()
        .find(|a| matches!(a.as_str(), "defuse" | "ctf"))
        .cloned();
    pollster::block_on(run(&path, width, height, summary, mode.as_deref()));
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

async fn run(path: &str, width: u32, height: u32, summary: bool, mode_name: Option<&str>) {
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

    let you = wounded();
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
    let view = HudView {
        hud_scale: 1.0,
        team: 0,
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
        scoreboard: Some(&board),
        damage: &numbers,
        scores: &[14, 11],
        rtt: Some(38.0),
        fps: Some(144.0),
        net_graph: 1,
        crosshair: Default::default(),
        radar: None,
        utility: Some(&utility),
        flash: 0.0,
        console: None,
    };

    let mut verts: Vec<OverlayVertex> = Vec::new();
    hud.build(&view, &mut verts);
    if summary {
        // Drawn over a finished HUD, appended rather than replacing it — which
        // is exactly what the client does, and the only arrangement in which the
        // scrim's job is visible.
        let mut screen = SummaryScreen::default();
        screen.open();
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
                won: false,
                mvp: false,
                recordable: true,
            },
            width as f32,
            height as f32,
            &mut verts,
        );
    }
    println!(
        "{} overlay vertices ({:.1}% of the client's 65536 cap)",
        verts.len(),
        verts.len() as f32 / 65536.0 * 100.0
    );

    let shader = device.create_shader_module(wgpu::ShaderModuleDescriptor {
        label: Some("overlay"),
        // The same concatenation the client makes: WGSL has no include, and
        // `shader.wgsl` calls into `lighting.wgsl.inc`. Compiling the overlay
        // half alone fails on an identifier the HUD never uses.
        source: wgpu::ShaderSource::Wgsl(
            concat!(
                include_str!("../src/lighting.wgsl.inc"),
                include_str!("../src/shader.wgsl")
            )
            .into(),
        ),
    });
    let layout = device.create_pipeline_layout(&wgpu::PipelineLayoutDescriptor {
        label: Some("overlay"),
        bind_group_layouts: &[],
        ..Default::default()
    });
    const ATTRS: [wgpu::VertexAttribute; 2] =
        wgpu::vertex_attr_array![0 => Float32x2, 1 => Float32x4];
    // The client's own overlay pipeline, restated: alpha blended, no depth, no
    // culling. Restated rather than borrowed because `Renderer` owns a surface
    // and there is no window here — and it is nine lines, all of which the
    // shader validation test already covers for correctness.
    let pipeline = device.create_render_pipeline(&wgpu::RenderPipelineDescriptor {
        label: Some("overlay"),
        layout: Some(&layout),
        vertex: wgpu::VertexState {
            module: &shader,
            entry_point: Some("vs_overlay"),
            buffers: &[Some(wgpu::VertexBufferLayout {
                array_stride: std::mem::size_of::<OverlayVertex>() as wgpu::BufferAddress,
                step_mode: wgpu::VertexStepMode::Vertex,
                attributes: &ATTRS,
            })],
            compilation_options: Default::default(),
        },
        fragment: Some(wgpu::FragmentState {
            module: &shader,
            entry_point: Some("fs_overlay"),
            targets: &[Some(wgpu::ColorTargetState {
                format: FORMAT,
                blend: Some(wgpu::BlendState::ALPHA_BLENDING),
                write_mask: wgpu::ColorWrites::ALL,
            })],
            compilation_options: Default::default(),
        }),
        // No culling, like the client's: a HUD is flat quads and a quad has no
        // outside, so culling would silently drop whichever half of the layout
        // happened to be wound the other way.
        primitive: wgpu::PrimitiveState {
            cull_mode: None,
            ..Default::default()
        },
        depth_stencil: None,
        multisample: wgpu::MultisampleState::default(),
        multiview_mask: None,
        cache: None,
    });

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
            backdrop.push(OverlayVertex { position, color });
        }
    }

    let all: Vec<OverlayVertex> = backdrop.iter().copied().chain(verts).collect();
    let buffer = device.create_buffer(&wgpu::BufferDescriptor {
        label: Some("overlay"),
        size: (all.len() * std::mem::size_of::<OverlayVertex>()) as wgpu::BufferAddress,
        usage: wgpu::BufferUsages::VERTEX | wgpu::BufferUsages::COPY_DST,
        mapped_at_creation: false,
    });
    queue.write_buffer(&buffer, 0, bytemuck::cast_slice(&all));

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
        pass.set_pipeline(&pipeline);
        pass.set_vertex_buffer(0, buffer.slice(..));
        pass.draw(0..all.len() as u32, 0..1);
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
