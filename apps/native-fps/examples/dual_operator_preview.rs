//! Render both Counter-Terrorist (SWAT) and Terrorist (Phoenix/Yaku Ignite)
//! operators side-by-side with weapons and tactical combat knives to a PNG.

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

const WIDTH: u32 = 1600;
const HEIGHT: u32 = 900;
const FORMAT: wgpu::TextureFormat = wgpu::TextureFormat::Rgba8UnormSrgb;

struct ShowcaseEntry {
    team: usize, // 0 = CT, 1 = T
    clip_name: &'static str,
    time: f32,
    weapon: i32, // 0 = knife, 1 = pistol, 2 = assault, 3 = shotgun, 4 = sniper
    x: f32,
    y: f32,
    yaw: f32,
}

const LINEUP: [ShowcaseEntry; 4] = [
    // CT - SWAT with Assault Rifle (M4A1)
    ShowcaseEntry {
        team: 0,
        clip_name: "rifle_aiming_idle",
        time: 0.35,
        weapon: 2, // Assault
        x: 0.0,
        y: -4.8,
        yaw: PI + 0.65,
    },
    // CT - SWAT with Tactical Knife
    ShowcaseEntry {
        team: 0,
        clip_name: "standard_walk",
        time: 0.55,
        weapon: 0, // Knife
        x: 0.0,
        y: -1.6,
        yaw: PI + 0.35,
    },
    // T - Phoenix with Tactical Knife
    ShowcaseEntry {
        team: 1,
        clip_name: "crouch_walking",
        time: 0.70,
        weapon: 0, // Knife
        x: 0.0,
        y: 1.6,
        yaw: PI - 0.35,
    },
    // T - Phoenix with Sniper Rifle
    ShowcaseEntry {
        team: 1,
        clip_name: "firing_rifle",
        time: 0.98,
        weapon: 4, // Sniper
        x: 0.0,
        y: 4.8,
        yaw: PI - 0.65,
    },
];

fn main() {
    let path = std::env::args().nth(1).unwrap_or("dual_operators.png".into());
    pollster::block_on(run(&path));
}

async fn run(path: &str) {
    let op_ct = Operator::load().expect("the CT operator GLB should parse");
    let op_t = Operator::load_t().expect("the Terrorist operator GLB should parse");
    println!(
        "CT operator: {} bones, {} vertices, {} primitives, {} textures",
        op_ct.bone_count(),
        op_ct.vertices.len(),
        op_ct.primitives.len(),
        op_ct.textures.len(),
    );
    println!(
        "T operator: {} bones, {} vertices, {} primitives, {} textures",
        op_t.bone_count(),
        op_t.vertices.len(),
        op_t.primitives.len(),
        op_t.textures.len(),
    );

    let instance = wgpu::Instance::default();
    let adapter = instance
        .request_adapter(&wgpu::RequestAdapterOptions::default())
        .await
        .expect("no GPU adapter");
    println!("adapter: {}", adapter.get_info().name);
    let (device, queue) = adapter
        .request_device(&wgpu::DeviceDescriptor {
            label: Some("dual-operator-preview"),
            ..Default::default()
        })
        .await
        .expect("device");

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

    let camera = Camera {
        x: -10.5,
        y: 0.0,
        z: 3.2,
        yaw: 0.0,
        pitch: -2.0,
        roll: 0.0,
        fov: 52.0,
    };
    let mut uniform = [0f32; 40];
    uniform[..16].copy_from_slice(&camera.view_projection(WIDTH, HEIGHT).to_cols_array());
    uniform[16] = 0.0;
    uniform[17] = 2.0;
    uniform[24..40].copy_from_slice(&glam::Mat4::IDENTITY.to_cols_array());
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

    // Floor plane
    let mut floor_verts = Vec::new();
    let floor_half = 20.0f32;
    let floor_color = [0.18f32, 0.20, 0.23];
    let n = [0.0f32, 0.0, 1.0];
    floor_verts.extend_from_slice(&[
        Vertex { position: [-floor_half, -floor_half, 0.0], normal: n, color: floor_color },
        Vertex { position: [floor_half, -floor_half, 0.0], normal: n, color: floor_color },
        Vertex { position: [floor_half, floor_half, 0.0], normal: n, color: floor_color },
        Vertex { position: [-floor_half, -floor_half, 0.0], normal: n, color: floor_color },
        Vertex { position: [floor_half, floor_half, 0.0], normal: n, color: floor_color },
        Vertex { position: [-floor_half, floor_half, 0.0], normal: n, color: floor_color },
    ]);

    let world_buffer = device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
        label: Some("floor"),
        contents: bytemuck::cast_slice(&floor_verts),
        usage: wgpu::BufferUsages::VERTEX,
    });

    let shadow = hassault_native::shadow::ShadowMap::new(
        &device,
        &queue,
        &world_buffer,
        floor_verts.len() as u32,
        (glam::Vec3::new(-10.0, -10.0, -1.0), glam::Vec3::new(10.0, 10.0, 8.0)),
    );

    let mut characters_ct = Characters::new(
        &device,
        &queue,
        &op_ct,
        &camera_layout,
        &shadow.layout,
        FORMAT,
        1,
    );
    let mut characters_t = Characters::new(
        &device,
        &queue,
        &op_t,
        &camera_layout,
        &shadow.layout,
        FORMAT,
        1,
    );

    let mut ct_poses = Vec::new();
    let mut t_poses = Vec::new();
    let mut all_held_poses = Vec::new();

    for entry in &LINEUP {
        let is_ct = entry.team == 0;
        let op = if is_ct { &op_ct } else { &op_t };
        let clip = op.clip(entry.clip_name).expect("clip missing");

        let mut pose = Pose::new(op);
        pose.reset(op);
        pose.blend(op, clip, entry.time, 1.0, Mask::All);

        let model = model_matrix(&PlayerRow {
            x: entry.x,
            y: entry.y,
            z: 0.0,
            yaw: entry.yaw,
            ..Default::default()
        });

        let mut bones = vec![Mat4::IDENTITY; op.bone_count()];
        pose.skinning(op, model, &mut bones);

        let grip = pose.bone_matrix(op, "RightHand", model);

        all_held_poses.push(ActorPose {
            bones: Vec::new(),
            tint: Vec4::ZERO,
            grip,
            weapon: entry.weapon,
        });

        let actor_pose = ActorPose {
            bones,
            tint: if is_ct {
                Vec4::new(0.12, 0.20, 0.32, 0.22) // CT blue tint
            } else {
                Vec4::new(0.32, 0.18, 0.12, 0.22) // T amber tint
            },
            grip,
            weapon: entry.weapon,
        };

        if is_ct {
            ct_poses.push(actor_pose);
        } else {
            t_poses.push(actor_pose);
        }
    }

    if !ct_poses.is_empty() {
        characters_ct.prepare(&queue, &ct_poses);
    }
    if !t_poses.is_empty() {
        characters_t.prepare(&queue, &t_poses);
    }

    let held_verts = held::build(&all_held_poses);
    println!("held weapon vertices: {}", held_verts.len());

    let held_buffer = device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
        label: Some("held"),
        contents: bytemuck::cast_slice(&held_verts),
        usage: wgpu::BufferUsages::VERTEX,
    });

    let world_shader = device.create_shader_module(wgpu::ShaderModuleDescriptor {
        label: Some("world"),
        source: wgpu::ShaderSource::Wgsl(
            concat!(
                include_str!("../src/lighting.wgsl.inc"),
                include_str!("../src/shader.wgsl")
            )
            .into(),
        ),
    });

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
                    load: wgpu::LoadOp::Clear(wgpu::Color {
                        r: 0.03,
                        g: 0.035,
                        b: 0.045,
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

        // 1. Draw floor
        pass.set_pipeline(&world_pipeline);
        pass.set_bind_group(0, &camera_group, &[]);
        pass.set_bind_group(1, &detail_group, &[]);
        pass.set_bind_group(2, &shadow.bind_group, &[]);
        pass.set_vertex_buffer(0, world_buffer.slice(..));
        pass.draw(0..floor_verts.len() as u32, 0..1);

        // 2. Draw CT Characters
        characters_ct.draw(&mut pass, &camera_group, &shadow.bind_group);

        // 3. Draw T Characters
        characters_t.draw(&mut pass, &camera_group, &shadow.bind_group);

        // 4. Draw Held Weapons
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
        .expect("png write");
    println!("wrote {path} ({}x{})", WIDTH, HEIGHT);
}
