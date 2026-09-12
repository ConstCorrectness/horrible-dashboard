//! Render the tactical utility grenades offscreen and write a PNG.

use glam::Vec3;
use hassault_native::camera::Camera;
use hassault_native::prop::Prop;
use hassault_native::props_gpu::Props;
use hassault_native::renderer::DEPTH_FORMAT;

const WIDTH: u32 = 900;
const HEIGHT: u32 = 320;
const FORMAT: wgpu::TextureFormat = wgpu::TextureFormat::Rgba8UnormSrgb;

const GRENADE_GLBS: [(&str, &[u8]); 3] = [
    ("he", include_bytes!("../../web/public/hassault-grenade-he.glb")),
    ("flash", include_bytes!("../../web/public/hassault-grenade-flash.glb")),
    ("smoke", include_bytes!("../../web/public/hassault-grenade-smoke.glb")),
];

fn main() {
    let path = std::env::args().nth(1).unwrap_or("grenades.png".into());
    pollster::block_on(run(&path));
}

async fn run(path: &str) {
    let instance = wgpu::Instance::default();
    let adapter = instance
        .request_adapter(&wgpu::RequestAdapterOptions::default())
        .await
        .expect("no GPU adapter");
    println!("adapter: {}", adapter.get_info().name);
    let (device, queue) = adapter
        .request_device(&wgpu::DeviceDescriptor {
            label: Some("grenade-preview"),
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

    let mut props = Props::new(&device, &camera_layout, FORMAT, 1);

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

    let mut cleared = false;
    let count = GRENADE_GLBS.len() as u32;

    for (index, (name, bytes)) in GRENADE_GLBS.iter().enumerate() {
        let prop = Prop::from_slice(bytes).expect("grenade GLB should parse");
        let (min, max) = prop.bounds();
        let extent = max - min;
        let centre = (min + max) * 0.5;
        let dim = extent.x.max(extent.y).max(extent.z).max(0.1);
        println!("{name}: bounds {min} to {max}, dim {dim}");

        props.set(&device, &queue, name, &prop);
        props.select(name).expect("just uploaded");

        let camera = Camera {
            x: centre.x + dim * 2.6,
            y: centre.z - dim * 1.4,
            z: centre.y + dim * 1.4,
            yaw: 150.0,
            pitch: -14.0,
            roll: 0.0,
            fov: 46.0,
        };
        let view_proj = camera.view_projection(WIDTH / count, HEIGHT);
        let mut uniform = [0f32; 40];
        uniform[..16].copy_from_slice(&view_proj.to_cols_array());
        uniform[16] = 0.0055;
        uniform[17] = 2.0;
        uniform[19] = 0.0;
        uniform[24..40].copy_from_slice(&glam::Mat4::IDENTITY.to_cols_array());

        let buffer = wgpu::util::DeviceExt::create_buffer_init(
            &device,
            &wgpu::util::BufferInitDescriptor {
                label: Some("camera"),
                contents: bytemuck::cast_slice(&uniform),
                usage: wgpu::BufferUsages::UNIFORM,
            },
        );
        let camera_group = device.create_bind_group(&wgpu::BindGroupDescriptor {
            label: Some("camera-group"),
            layout: &camera_layout,
            entries: &[wgpu::BindGroupEntry {
                binding: 0,
                resource: buffer.as_entire_binding(),
            }],
        });

        let mut encoder = device.create_command_encoder(&Default::default());
        {
            let mut pass = encoder.begin_render_pass(&wgpu::RenderPassDescriptor {
                label: Some("grenade-preview"),
                color_attachments: &[Some(wgpu::RenderPassColorAttachment {
                    view: &color_view,
                    depth_slice: None,
                    resolve_target: None,
                    ops: wgpu::Operations {
                        load: if cleared {
                            wgpu::LoadOp::Load
                        } else {
                            wgpu::LoadOp::Clear(wgpu::Color {
                                r: 0.015,
                                g: 0.018,
                                b: 0.024,
                                a: 1.0,
                            })
                        },
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
            pass.set_viewport(
                (index as u32 * WIDTH / count) as f32,
                0.0,
                (WIDTH / count) as f32,
                HEIGHT as f32,
                0.0,
                1.0,
            );
            cleared = true;
            props.draw(&mut pass, &camera_group);
        }
        queue.submit([encoder.finish()]);
    }

    let unpadded = WIDTH * 4;
    let padded = unpadded.div_ceil(256) * 256;
    let readback = device.create_buffer(&wgpu::BufferDescriptor {
        label: Some("readback"),
        size: (padded * HEIGHT) as u64,
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
