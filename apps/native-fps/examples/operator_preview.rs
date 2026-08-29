//! Render the operator to a PNG, with no window and no match.
//!
//! ```text
//! cargo run --example operator_preview -- operator.png
//! ```
//!
//! This exists because the native client's character is the one part of it that
//! cannot be checked by a unit test or by reading the code. The tests in
//! `character.rs` prove the rig is 34 bones, that every joint index is in range
//! and that the bind pose is 5.2 cubes tall — all of which stayed true while the
//! character was drawn facing the wrong way, or inside out, or in the wrong
//! colour. Those are properties of the *picture*, so there has to be a picture.
//!
//! An `examples/` binary rather than a test on purpose: it needs a real GPU
//! adapter, and a test suite that fails on a machine without one is a test suite
//! that gets ignored.
//!
//! It draws one operator per clip across a row, the same five clips the browser
//! check page uses, so the two can be compared side by side.

use std::f32::consts::PI;

use glam::{Mat4, Vec4};
use hassault_native::animator::{model_matrix, ActorPose};
use hassault_native::camera::Camera;
use hassault_native::character::{Mask, Operator, Pose};
use hassault_native::characters_gpu::Characters;
use hassault_native::held;
use hassault_native::protocol::PlayerRow;
use hassault_native::renderer::{Vertex, DEPTH_FORMAT};
use wgpu::util::DeviceExt;

const WIDTH: u32 = 1200;
const HEIGHT: u32 = 720;
const FORMAT: wgpu::TextureFormat = wgpu::TextureFormat::Rgba8UnormSrgb;

/// The same five the browser's `operator-check.html` poses, so a difference
/// between the two clients is a difference in one picture rather than in two
/// different ones.
const SHOWCASE: [(&str, f32); 5] = [
    ("rifle_aiming_idle", 0.35),
    ("standard_walk", 0.56),
    ("crouch_walking", 0.77),
    ("firing_rifle", 0.98),
    ("dying", 1.19),
];

fn main() {
    let path = std::env::args().nth(1).unwrap_or("operator.png".into());
    pollster::block_on(run(&path));
}

async fn run(path: &str) {
    let operator = Operator::load().expect("the compiled-in operator GLB should parse");
    println!(
        "operator: {} bones, {} vertices, {} primitives, {} textures, {} clips",
        operator.bone_count(),
        operator.vertices.len(),
        operator.primitives.len(),
        operator.textures.len(),
        operator.clip_names().count(),
    );

    let instance = wgpu::Instance::default();
    let adapter = instance
        .request_adapter(&wgpu::RequestAdapterOptions::default())
        .await
        .expect("no GPU adapter — this example needs a real one");
    println!("adapter: {}", adapter.get_info().name);
    let (device, queue) = adapter
        .request_device(&wgpu::DeviceDescriptor {
            label: Some("operator-preview"),
            ..Default::default()
        })
        .await
        .expect("device");

    // The camera bind group layout the shared shader expects. Rebuilt here
    // rather than borrowed from `Renderer`, which owns a surface this example
    // deliberately does not have.
    let camera_layout = device.create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
        label: Some("camera"),
        entries: &[wgpu::BindGroupLayoutEntry {
            binding: 0,
            visibility: wgpu::ShaderStages::VERTEX_FRAGMENT,
            ty: wgpu::BindingType::Buffer {
                ty: wgpu::BufferBindingType::Uniform,
                has_dynamic_offset: false,
                min_binding_size: None,
            },
            count: None,
        }],
    });

    // Placed to look along +x at a row of operators spread across y, from a
    // little above the waist — roughly where another player's eyes would be.
    let camera = Camera {
        x: -21.0,
        y: 0.0,
        z: 3.4,
        yaw: 0.0,
        pitch: -2.0,
        roll: 0.0,
        fov: 48.0,
    };
    // `params.x` is the fog end and `.y` the detail level: 2 is the highest, so
    // the preview shows what the shader actually does rather than its flat path.
    let mut uniform = [0f32; 20];
    uniform[..16].copy_from_slice(&camera.view_projection(WIDTH, HEIGHT).to_cols_array());
    uniform[16] = 400.0;
    uniform[17] = 2.0;
    let camera_buffer = device.create_buffer(&wgpu::BufferDescriptor {
        label: Some("camera"),
        size: std::mem::size_of_val(&uniform) as u64,
        usage: wgpu::BufferUsages::UNIFORM | wgpu::BufferUsages::COPY_DST,
        mapped_at_creation: false,
    });
    queue.write_buffer(&camera_buffer, 0, bytemuck::cast_slice(&uniform));
    let camera_group = device.create_bind_group(&wgpu::BindGroupDescriptor {
        label: Some("camera"),
        layout: &camera_layout,
        entries: &[wgpu::BindGroupEntry {
            binding: 0,
            resource: camera_buffer.as_entire_binding(),
        }],
    });

    // A shadow map with nothing in it: the preview has no world, so every
    // fragment is lit. Built rather than skipped because the pipeline layout has
    // to match the shader either way, and an empty map is the honest answer to
    // "what does this character's surroundings cast?" when there are none.
    let empty_world = device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
        label: Some("no-world"),
        contents: bytemuck::cast_slice(&[Vertex {
            position: [0.0; 3],
            normal: [0.0, 1.0, 0.0],
            color: [0.0; 3],
        }]),
        usage: wgpu::BufferUsages::VERTEX,
    });
    let shadow = hassault_native::shadow::ShadowMap::new(
        &device,
        &queue,
        &empty_world,
        0,
        (glam::Vec3::ZERO, glam::Vec3::splat(8.0)),
    );

    let mut characters = Characters::new(
        &device,
        &queue,
        &operator,
        &camera_layout,
        &shadow.layout,
        FORMAT,
        1,
    );

    // One actor per clip. Posed directly rather than through `Squad`, because
    // what is being checked is the rig and the shader — driving it from derived
    // velocities would put clip *selection* in the picture too, and that part
    // already has unit tests.
    let mut poses = Vec::new();
    for (index, (clip_name, time)) in SHOWCASE.iter().enumerate() {
        let clip = operator
            .clip(clip_name)
            .unwrap_or_else(|| panic!("{clip_name} missing from the GLB"));
        let mut pose = Pose::new(&operator);
        pose.reset(&operator);
        pose.blend(&operator, clip, *time, 1.0, Mask::All);

        // Spread across the camera's right, placed and turned through the
        // client's own `model_matrix` rather than a hand-built transform — the
        // facing convention is exactly what this picture is here to check, and a
        // second copy of it could agree with itself while both were wrong.
        //
        // The camera looks along +x, so a player at yaw = PI faces it head on —
        // which is the worst angle for checking a weapon, because a rifle held
        // forward then points straight at the lens and foreshortens into the
        // chest. Turned three-quarters instead, so both the face and the gun
        // are in the picture.
        let offset = (index as f32 - (SHOWCASE.len() as f32 - 1.0) / 2.0) * 3.6;
        let model = model_matrix(&PlayerRow {
            x: 0.0,
            y: offset,
            z: 0.0,
            yaw: PI + 0.8,
            ..Default::default()
        });
        let mut bones = vec![Mat4::IDENTITY; operator.bone_count()];
        pose.skinning(&operator, model, &mut bones);
        poses.push(ActorPose {
            bones,
            // Alternating teams, so the wash is visible as a difference rather
            // than as a colour you have to take on faith.
            tint: if index % 2 == 0 {
                Vec4::new(0.29, 0.23, 0.17, 0.28)
            } else {
                Vec4::new(0.12, 0.16, 0.23, 0.28)
            },
            grip: pose.bone_matrix(&operator, "RightHand", model),
            // A different weapon per actor, so the five silhouettes are all in
            // one picture rather than one of them five times.
            weapon: index as i32,
        });
    }
    characters.prepare(&queue, &poses);

    // The weapons in their hands ride the untextured world pipeline, so the
    // preview has to stand one up too — the grip transform is the part of this
    // port most likely to be subtly wrong, and it is only wrong in a picture.
    let held_verts = held::build(&poses);
    println!("held weapon triangles: {}", held_verts.len() / 3);
    // Reported because it is the trap: the rig's internal unit is about 1/35th
    // of a cube, so anything parented to a bone inherits that scale and a prop
    // stated in cubes renders sub-pixel. `held.rs` drops it deliberately.
    if let Some(grip) = poses.first().and_then(|p| p.grip) {
        let (scale, _, translation) = grip.to_scale_rotation_translation();
        println!(
            "  right hand at {translation} cubes, bone scale {:.4}",
            scale.x
        );
    }
    let held_buffer = device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
        label: Some("held"),
        contents: bytemuck::cast_slice(&held_verts),
        usage: wgpu::BufferUsages::VERTEX,
    });
    let world_shader = device.create_shader_module(wgpu::ShaderModuleDescriptor {
        label: Some("world"),
        source: wgpu::ShaderSource::Wgsl(
            // Same concatenation the renderer does — the lighting lives in its
            // own file so both shaders share one copy.
            concat!(
                include_str!("../src/lighting.wgsl.inc"),
                include_str!("../src/shader.wgsl")
            )
            .into(),
        ),
    });
    // The world shader samples the surface grain, so its layout needs that group
    // too — built through the client's own helper rather than a copy here.
    let detail_layout = hassault_native::detail::bind_group_layout(&device);
    let detail_group = hassault_native::detail::bind_group(&device, &queue, &detail_layout);
    let world_layout = device.create_pipeline_layout(&wgpu::PipelineLayoutDescriptor {
        label: Some("world"),
        bind_group_layouts: &[
            Some(&camera_layout),
            Some(&detail_layout),
            Some(&shadow.layout),
        ],
        immediate_size: 0,
    });
    let world_pipeline = device.create_render_pipeline(&wgpu::RenderPipelineDescriptor {
        label: Some("world"),
        layout: Some(&world_layout),
        vertex: wgpu::VertexState {
            module: &world_shader,
            entry_point: Some("vs_main"),
            buffers: &[Some(wgpu::VertexBufferLayout {
                array_stride: std::mem::size_of::<Vertex>() as wgpu::BufferAddress,
                step_mode: wgpu::VertexStepMode::Vertex,
                attributes: &wgpu::vertex_attr_array![0 => Float32x3, 1 => Float32x3, 2 => Float32x3],
            })],
            compilation_options: Default::default(),
        },
        fragment: Some(wgpu::FragmentState {
            module: &world_shader,
            entry_point: Some("fs_main"),
            targets: &[Some(wgpu::ColorTargetState {
                format: FORMAT,
                blend: Some(wgpu::BlendState::REPLACE),
                write_mask: wgpu::ColorWrites::ALL,
            })],
            compilation_options: Default::default(),
        }),
        primitive: wgpu::PrimitiveState {
            topology: wgpu::PrimitiveTopology::TriangleList,
            front_face: wgpu::FrontFace::Ccw,
            cull_mode: Some(wgpu::Face::Back),
            ..Default::default()
        },
        depth_stencil: Some(wgpu::DepthStencilState {
            format: DEPTH_FORMAT,
            depth_write_enabled: Some(true),
            depth_compare: Some(wgpu::CompareFunction::Less),
            stencil: wgpu::StencilState::default(),
            bias: wgpu::DepthBiasState::default(),
        }),
        multisample: wgpu::MultisampleState::default(),
        multiview_mask: None,
        cache: None,
    });

    // --- draw ------------------------------------------------------------
    let size = wgpu::Extent3d {
        width: WIDTH,
        height: HEIGHT,
        depth_or_array_layers: 1,
    };
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

    // Rows must be 256-byte aligned for a texture-to-buffer copy; the padding is
    // trimmed back out below.
    let unpadded = WIDTH * 4;
    let padded = unpadded.div_ceil(256) * 256;
    let readback = device.create_buffer(&wgpu::BufferDescriptor {
        label: Some("readback"),
        size: (padded * HEIGHT) as u64,
        usage: wgpu::BufferUsages::COPY_DST | wgpu::BufferUsages::MAP_READ,
        mapped_at_creation: false,
    });

    let mut encoder = device.create_command_encoder(&Default::default());
    {
        let mut pass = encoder.begin_render_pass(&wgpu::RenderPassDescriptor {
            label: Some("preview"),
            color_attachments: &[Some(wgpu::RenderPassColorAttachment {
                view: &color_view,
                depth_slice: None,
                resolve_target: None,
                ops: wgpu::Operations {
                    // The client's own fog colour, so the preview is lit and
                    // backed the way a real frame is.
                    load: wgpu::LoadOp::Clear(wgpu::Color {
                        r: 0.02,
                        g: 0.024,
                        b: 0.035,
                        a: 1.0,
                    }),
                    store: wgpu::StoreOp::Store,
                },
            })],
            depth_stencil_attachment: Some(wgpu::RenderPassDepthStencilAttachment {
                view: &depth,
                depth_ops: Some(wgpu::Operations {
                    load: wgpu::LoadOp::Clear(1.0),
                    store: wgpu::StoreOp::Store,
                }),
                stencil_ops: None,
            }),
            timestamp_writes: None,
            occlusion_query_set: None,
            multiview_mask: None,
        });
        characters.draw(&mut pass, &camera_group, &shadow.bind_group);
        if !held_verts.is_empty() {
            pass.set_pipeline(&world_pipeline);
            pass.set_bind_group(0, &camera_group, &[]);
            pass.set_bind_group(1, &detail_group, &[]);
            pass.set_bind_group(2, &shadow.bind_group, &[]);
            pass.set_vertex_buffer(0, held_buffer.slice(..));
            pass.draw(0..held_verts.len() as u32, 0..1);
        }
    }
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
                rows_per_image: Some(HEIGHT),
            },
        },
        size,
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
    let mapped = slice.get_mapped_range().expect("map range");
    let mut pixels = Vec::with_capacity((unpadded * HEIGHT) as usize);
    for row in 0..HEIGHT {
        let start = (row * padded) as usize;
        pixels.extend_from_slice(&mapped[start..start + unpadded as usize]);
    }
    drop(mapped);
    readback.unmap();

    let file = std::fs::File::create(path).expect("create png");
    let mut encoder = png::Encoder::new(std::io::BufWriter::new(file), WIDTH, HEIGHT);
    encoder.set_color(png::ColorType::Rgba);
    encoder.set_depth(png::BitDepth::Eight);
    encoder
        .write_header()
        .expect("png header")
        .write_image_data(&pixels)
        .expect("png data");
    println!("wrote {path}");
}
