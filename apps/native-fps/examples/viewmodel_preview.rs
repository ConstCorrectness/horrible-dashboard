//! Render the first-person view model — weapon, skinned hands, inspect — to a PNG.
//!
//! ```text
//! cargo run --manifest-path apps/native-fps/Cargo.toml --example viewmodel_preview -- out.png
//! ```
//!
//! One row per weapon (and per knife archetype), one column per moment of its
//! inspect: at rest, then a fifth of the way through at a time. Every cell is
//! the client's own path — `WeaponViewModel` posed by `update`, the prop fitted
//! and drawn with `prop_model` / `prop_part_models`, the hands solved and
//! skinned by `hands_vertices` — drawn with the prop pipeline and the view
//! model's own projection. What it cannot show is the world behind the gun.
//!
//! The picture is the test here: whether a fist closes on a grip, or hangs a
//! finger's width off it, is a property no unit test can see.

use hassault_native::prop::{weapon_glb, Prop};
use hassault_native::props_gpu::{Props, MAX_PROP_PARTS};
use hassault_native::renderer::DEPTH_FORMAT;
use hassault_native::viewmodel::{Frame, Skin, WeaponViewModel};

// Bigger when only a few rows are asked for, for judging a grip up close.
fn cell(rows: usize) -> (u32, u32) {
    if rows <= 2 { (720, 450) } else { (360, 240) }
}
const MOMENTS: [Option<f32>; 5] = [None, Some(0.2), Some(0.4), Some(0.6), Some(0.8)];
const FORMAT: wgpu::TextureFormat = wgpu::TextureFormat::Rgba8UnormSrgb;
const FOV: f32 = 75.0;

fn skin(id: &str) -> Skin {
    Skin {
        id: Some(id.into()),
        base_color: "#ffffff".into(),
        accent_color: "#000000".into(),
        pattern_type: "fade".into(),
        float_value: 0.05,
    }
}

fn frame() -> Frame {
    Frame {
        ads: 0.0,
        speed: 0.0,
        sprint: false,
        on_ground: true,
        reloading: false,
        yaw: 0.0,
        pitch: 0.0,
        visible: true,
        move_speed: 22.0,
        reload_progress: None,
        fov: FOV.to_radians(),
        since_landed: 99.0,
    }
}

fn main() {
    let path = std::env::args().nth(1).unwrap_or("viewmodel.png".into());
    // Any further arguments pick rows by label: `-- out.png pistol assault`.
    let only: Vec<String> = std::env::args().skip(2).collect();
    pollster::block_on(run(&path, &only));
}

async fn run(path: &str, only: &[String]) {
    // (row label, weapon id, skin, prop id)
    let rows: Vec<(&str, &str, Option<Skin>, &str)> = vec![
        ("knife", "knife", None, "knife"),
        ("karambit", "knife", Some(skin("knife_karambit_fade")), "knife_karambit"),
        ("butterfly", "knife", Some(skin("knife_butterfly_marble")), "knife_butterfly"),
        ("bayonet", "knife", Some(skin("knife_bayonet_lore")), "knife_bayonet"),
        ("pistol", "pistol", None, "pistol"),
        ("assault", "assault", None, "assault"),
        ("shotgun", "shotgun", None, "shotgun"),
        ("sniper", "sniper", None, "sniper"),
    ];
    let rows: Vec<_> = rows
        .into_iter()
        .filter(|(label, ..)| only.is_empty() || only.iter().any(|o| o == label))
        .collect();
    let (cell_w, cell_h) = cell(rows.len());
    let width = cell_w * MOMENTS.len() as u32;
    let height = cell_h * rows.len() as u32;

    let instance = wgpu::Instance::default();
    let adapter = instance
        .request_adapter(&wgpu::RequestAdapterOptions::default())
        .await
        .expect("no GPU adapter — this example needs a real one");
    let (device, queue) = adapter
        .request_device(&wgpu::DeviceDescriptor { label: Some("viewmodel-preview"), ..Default::default() })
        .await
        .expect("device");
    let camera_layout = hassault_native::atmosphere::camera_layout(&device);
    let lights_buffer = hassault_native::atmosphere::default_lights_buffer(&device);
    let mut props = Props::new(&device, &camera_layout, FORMAT, 1);
    let hands = hassault_native::hands::asset().expect("hands GLB parses");
    props.set_hands(&device, &queue, hands.materials(), hands.textures(), hands.primitives(), hands.vertex_count());

    let size = wgpu::Extent3d { width, height, depth_or_array_layers: 1 };
    let color = device.create_texture(&wgpu::TextureDescriptor {
        label: Some("color"),
        size,
        mip_level_count: 1,
        sample_count: 1,
        dimension: wgpu::TextureDimension::D2,
        format: FORMAT,
        usage: wgpu::TextureUsages::RENDER_ATTACHMENT | wgpu::TextureUsages::COPY_SRC,
        view_formats: &[],
    });
    let color_view = color.create_view(&wgpu::TextureViewDescriptor::default());
    let depth = device
        .create_texture(&wgpu::TextureDescriptor {
            label: Some("depth"),
            size,
            mip_level_count: 1,
            sample_count: 1,
            dimension: wgpu::TextureDimension::D2,
            format: DEPTH_FORMAT,
            usage: wgpu::TextureUsages::RENDER_ATTACHMENT,
            view_formats: &[],
        })
        .create_view(&wgpu::TextureViewDescriptor::default());

    let projection = glam::camera::rh::proj::directx::perspective(
        FOV.to_radians(),
        cell_w as f32 / cell_h as f32,
        0.01,
        50.0,
    );
    // PREVIEW_ORBIT=<degrees> swings the eye round the weapon — a diagnostic
    // view of the arms that the first-person camera hides.
    let orbit: f32 = std::env::var("PREVIEW_ORBIT").ok().and_then(|v| v.parse().ok()).unwrap_or(0.0);
    let view = if orbit == 0.0 {
        glam::Mat4::IDENTITY
    } else {
        let focus = glam::Vec3::new(0.3, -0.5, -0.9);
        let eye = focus
            + glam::Vec3::new(orbit.to_radians().sin(), 0.35, orbit.to_radians().cos()).normalize() * 2.6;
        glam::Mat4::look_at_rh(eye, focus, glam::Vec3::Y)
    };
    let camera = |pose: glam::Mat4| {
        let mut uniform = [0f32; 40];
        uniform[..16].copy_from_slice(&(projection * view * pose).to_cols_array());
        uniform[16] = 0.0055;
        uniform[17] = 2.0;
        uniform[24..40].copy_from_slice(&pose.to_cols_array());
        let buffer = wgpu::util::DeviceExt::create_buffer_init(
            &device,
            &wgpu::util::BufferInitDescriptor {
                label: Some("camera"),
                contents: bytemuck::cast_slice(&uniform),
                usage: wgpu::BufferUsages::UNIFORM,
            },
        );
        device.create_bind_group(&wgpu::BindGroupDescriptor {
            label: Some("camera-group"),
            layout: &camera_layout,
            entries: &[
                wgpu::BindGroupEntry { binding: 0, resource: buffer.as_entire_binding() },
                wgpu::BindGroupEntry { binding: 1, resource: lights_buffer.as_entire_binding() },
            ],
        })
    };

    let mut cleared = false;
    let mut hand_verts = Vec::new();
    for (row, (label, weapon, skin, prop_id)) in rows.iter().enumerate() {
        let prop = Prop::from_slice(weapon_glb(prop_id).unwrap_or_else(|| panic!("no {prop_id}")))
            .unwrap_or_else(|e| panic!("{prop_id}: {e}"));
        props.set(&device, &queue, prop_id, &prop);
        let info = props.select(prop_id).expect("just uploaded");

        for (col, moment) in MOMENTS.iter().enumerate() {
            let mut vm = WeaponViewModel::default();
            vm.set_weapon(weapon, skin.as_ref());
            for _ in 0..40 {
                vm.update(0.016, &frame());
            }
            vm.fit_prop(info.bounds.0, info.bounds.1).expect("prop fits");
            vm.set_prop_layout(info.parts.clone(), info.markers.clone());
            vm.update(0.016, &frame());
            if let Some(fraction) = moment {
                vm.inspect();
                let clip = hassault_native::inspects::inspect_clip_for(
                    weapon,
                    skin.as_ref().and_then(|s| s.id.as_deref()).unwrap_or(""),
                    "",
                );
                let duration = hassault_native::inspects::clip(&clip).unwrap().duration;
                let mut t = 0.0;
                while t < duration * fraction {
                    vm.update(0.016, &frame());
                    t += 0.016;
                }
            }
            let body = vm.prop_model().expect("visible");
            let mut cameras = vec![camera(body)];
            for part in vm.prop_part_models().into_iter().take(MAX_PROP_PARTS - 1) {
                cameras.push(camera(part));
            }
            match vm.hands_vertices(&mut hand_verts) {
                Some((r, l)) => props.write_hands(&queue, &hand_verts, r, l),
                None => props.hide_hands(),
            }
            let hands_camera = camera(glam::Mat4::IDENTITY);

            let mut encoder = device.create_command_encoder(&Default::default());
            {
                let mut pass = encoder.begin_render_pass(&wgpu::RenderPassDescriptor {
                    label: Some("viewmodel-preview"),
                    color_attachments: &[Some(wgpu::RenderPassColorAttachment {
                        view: &color_view,
                        depth_slice: None,
                        resolve_target: None,
                        ops: wgpu::Operations {
                            load: if cleared {
                                wgpu::LoadOp::Load
                            } else {
                                wgpu::LoadOp::Clear(wgpu::Color { r: 0.32, g: 0.35, b: 0.4, a: 1.0 })
                            },
                            store: wgpu::StoreOp::Store,
                        },
                    })],
                    depth_stencil_attachment: Some(wgpu::RenderPassDepthStencilAttachment {
                        view: &depth,
                        depth_ops: Some(wgpu::Operations { load: wgpu::LoadOp::Clear(1.0), store: wgpu::StoreOp::Store }),
                        stencil_ops: None,
                    }),
                    timestamp_writes: None,
                    occlusion_query_set: None,
                    multiview_mask: None,
                });
                pass.set_viewport(
                    (col as u32 * cell_w) as f32,
                    (row as u32 * cell_h) as f32,
                    cell_w as f32,
                    cell_h as f32,
                    0.0,
                    1.0,
                );
                props.draw_hands(&mut pass, &hands_camera);
                props.draw(&mut pass, &cameras);
            }
            queue.submit([encoder.finish()]);
            cleared = true;
        }
        println!("{label}: {} parts, {} markers", info.parts.len(), info.markers.len());
    }

    let unpadded = width * 4;
    let padded = unpadded.div_ceil(256) * 256;
    let readback = device.create_buffer(&wgpu::BufferDescriptor {
        label: Some("readback"),
        size: (padded * height) as u64,
        usage: wgpu::BufferUsages::COPY_DST | wgpu::BufferUsages::MAP_READ,
        mapped_at_creation: false,
    });
    let mut encoder = device.create_command_encoder(&Default::default());
    encoder.copy_texture_to_buffer(
        wgpu::TexelCopyTextureInfo {
            texture: &color,
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
        size,
    );
    queue.submit([encoder.finish()]);
    let slice = readback.slice(..);
    slice.map_async(wgpu::MapMode::Read, |r| r.expect("map"));
    device
        .poll(wgpu::PollType::Wait { submission_index: None, timeout: None })
        .expect("poll");
    let mapped = slice.get_mapped_range().expect("map range");
    let mut pixels = Vec::with_capacity((unpadded * height) as usize);
    for row in 0..height {
        let start = (row * padded) as usize;
        pixels.extend_from_slice(&mapped[start..start + unpadded as usize]);
    }
    drop(mapped);
    readback.unmap();

    let file = std::fs::File::create(path).expect("create png");
    let mut encoder = png::Encoder::new(std::io::BufWriter::new(file), width, height);
    encoder.set_color(png::ColorType::Rgba);
    encoder.set_depth(png::BitDepth::Eight);
    encoder
        .write_header()
        .expect("png header")
        .write_image_data(&pixels)
        .expect("png data");
    println!("wrote {path}");
}
