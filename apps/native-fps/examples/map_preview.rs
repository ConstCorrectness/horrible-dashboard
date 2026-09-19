//! Render a 3D map with procedural PBR textures, planar UVs, and IBL reflections offscreen to a PNG.
//!
//! ```text
//! cargo run --example map_preview -- [map_name] [output.png]
//! ```

use glam::Mat4;
use hassault_native::api::MapInfo;
use hassault_native::camera::Camera;
use hassault_native::renderer::{mesh_vertices, Vertex, DEPTH_FORMAT};
use hassault_native::world3d::{
    create_procedural_assault_3d, create_procedural_bank_3d, create_procedural_facility_3d,
};
use wgpu::util::DeviceExt;

const WIDTH: u32 = 1600;
const HEIGHT: u32 = 900;
const FORMAT: wgpu::TextureFormat = wgpu::TextureFormat::Rgba8UnormSrgb;

fn main() {
    let mut args = std::env::args().skip(1);
    let mut map_name = "hd_assault".to_string();
    let mut out_path = "map_preview.png".to_string();

    while let Some(arg) = args.next() {
        if arg.ends_with(".png") {
            out_path = arg;
        } else {
            map_name = arg;
        }
    }

    pollster::block_on(run(&map_name, &out_path));
}

async fn run(map_name: &str, path: &str) {
    let info = MapInfo {
        name: map_name.to_string(),
        title: map_name.to_string(),
        ssize: 64,
        ..Default::default()
    };

    let world = match map_name {
        "hd_bank" => create_procedural_bank_3d(info),
        "hd_facility" => create_procedural_facility_3d(info),
        _ => create_procedural_assault_3d(info),
    };

    let m3d = world.to_mesh_data();
    println!("loaded map {}: {} collision vertices", map_name, world.col_vertices.len());

    let verts = mesh_vertices(&m3d);
    println!("generated {} render vertices with PBR attributes", verts.len());

    let instance = wgpu::Instance::default();
    let adapter = instance
        .request_adapter(&wgpu::RequestAdapterOptions::default())
        .await
        .expect("GPU adapter");
    println!("adapter: {}", adapter.get_info().name);

    let (device, queue) = adapter
        .request_device(&wgpu::DeviceDescriptor {
            label: Some("map-preview"),
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

    // Scenic eye-level perspective looking along the street or interior
    let camera = match map_name {
        "hd_bank" => Camera {
            // Standing inside the grand marble bank lobby looking toward tellers and vault
            x: 32.0,
            y: 21.0,
            z: 2.2,
            yaw: 0.0,
            pitch: 6.0,
            roll: 0.0,
            fov: 75.0,
        },
        _ => Camera {
            // Standing on the asphalt road looking down the double yellow lines and crosswalk toward SWAT van
            x: 10.5,
            y: 14.8,
            z: 2.2,
            yaw: 78.0,
            pitch: 2.0,
            roll: 0.0,
            fov: 78.0,
        },
    };

    let vp = camera.view_projection(WIDTH, HEIGHT);

    let mut camera_uniform = [0.0f32; 40];
    camera_uniform[0..16].copy_from_slice(vp.as_ref());
    camera_uniform[16] = 0.0055; // fog density
    camera_uniform[17] = 2.0;    // detail (sun + fill + hemisphere)
    camera_uniform[18] = 0.0;    // height
    camera_uniform[19] = 1.0;    // receives shadow
    camera_uniform[20] = 1.0;    // reveal progress (1.0 = fully visible)
    camera_uniform[21] = 0.0;
    camera_uniform[22] = 0.0;
    camera_uniform[23] = 1000.0; // reveal radius
    let identity = Mat4::IDENTITY;
    camera_uniform[24..40].copy_from_slice(identity.as_ref());

    let camera_buffer = device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
        label: Some("camera"),
        contents: bytemuck::cast_slice(&camera_uniform),
        usage: wgpu::BufferUsages::UNIFORM,
    });

    let camera_group = device.create_bind_group(&wgpu::BindGroupDescriptor {
        label: Some("camera"),
        layout: &camera_layout,
        entries: &[wgpu::BindGroupEntry {
            binding: 0,
            resource: camera_buffer.as_entire_binding(),
        }],
    });

    let world_buffer = device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
        label: Some("world-verts"),
        contents: bytemuck::cast_slice(&verts),
        usage: wgpu::BufferUsages::VERTEX,
    });

    let shadow = hassault_native::shadow::ShadowMap::new(
        &device,
        &queue,
        &world_buffer,
        (verts.len() as u32).min(3000),
        (glam::Vec3::new(-50.0, -50.0, -10.0), glam::Vec3::new(100.0, 100.0, 40.0)),
    );

    let detail_layout = hassault_native::detail::bind_group_layout(&device);
    let detail_group = hassault_native::detail::bind_group(&device, &queue, &detail_layout);

    let pbr_layout = hassault_native::textures3d::bind_group_layout(&device);
    let (pbr_group, _, _) = hassault_native::textures3d::bind_group(&device, &queue, &pbr_layout);

    let world_shader = device.create_shader_module(wgpu::ShaderModuleDescriptor {
        label: Some("world-pbr"),
        source: wgpu::ShaderSource::Wgsl(
            concat!(
                include_str!("../src/lighting.wgsl.inc"),
                include_str!("../src/shader.wgsl")
            )
            .into(),
        ),
    });

    let world_layout = device.create_pipeline_layout(&wgpu::PipelineLayoutDescriptor {
        label: Some("world-layout"),
        bind_group_layouts: &[
            Some(&camera_layout),
            Some(&detail_layout),
            Some(&shadow.layout),
            Some(&pbr_layout),
        ],
        immediate_size: 0,
    });

    let world_pipeline = device.create_render_pipeline(&wgpu::RenderPipelineDescriptor {
        label: Some("world-pipeline"),
        layout: Some(&world_layout),
        vertex: wgpu::VertexState {
            module: &world_shader,
            entry_point: Some("vs_main"),
            buffers: &[Some(Vertex::layout())],
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
            label: Some("map-render"),
            color_attachments: &[Some(wgpu::RenderPassColorAttachment {
                view: &color_view,
                depth_slice: None,
                resolve_target: None,
                ops: wgpu::Operations {
                    load: wgpu::LoadOp::Clear(wgpu::Color {
                        r: 0.067, // 0x11 / 255
                        g: 0.086, // 0x16 / 255
                        b: 0.121, // 0x1f / 255 - HORIZON sky blue/dark
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

        pass.set_pipeline(&world_pipeline);
        pass.set_bind_group(0, &camera_group, &[]);
        pass.set_bind_group(1, &detail_group, &[]);
        pass.set_bind_group(2, &shadow.bind_group, &[]);
        pass.set_bind_group(3, &pbr_group, &[]);
        pass.set_vertex_buffer(0, world_buffer.slice(..));
        pass.draw(0..verts.len() as u32, 0..1);
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

    println!("wrote rendered map to {path} ({}x{})", WIDTH, HEIGHT);
}
