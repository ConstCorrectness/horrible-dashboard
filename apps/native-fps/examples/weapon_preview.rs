//! Render the weapon props offscreen and write a PNG.
//!
//! The native client's counterpart to opening the game and looking at the gun.
//! Every unit test around the props checks a property a *wrong-looking* weapon
//! would still satisfy — the GLB parses, the bounds are right, the normals are
//! unit length, the shader validates — and none of them would catch a weapon
//! rendered inside out, unlit, or with its base colour in the wrong slot. Those
//! are properties of a picture, so this makes one.
//!
//! ```text
//! cargo run --manifest-path apps/native-fps/Cargo.toml --example weapon_preview
//! ```
//!
//! It also prints the fraction of the frame the weapon covers and its mean
//! luminance, which is what makes it usable without eyes on it: the failure this
//! pipeline is most likely to have — a base colour sampled as linear, or a
//! normal left at the source scale — comes out as a weapon that is *there* and
//! *dark*, and a number says so where a glance might not.

use hassault_native::camera::Camera;
use hassault_native::prop::{Prop, WEAPON_GLBS};
use hassault_native::props_gpu::Props;
use hassault_native::renderer::DEPTH_FORMAT;

const WIDTH: u32 = 900;
const HEIGHT: u32 = 320;
/// Matches the client's own surface: sRGB, so the shader's linear output is
/// encoded on write exactly as it is in a real frame. A linear target here would
/// make every prop look washed out and send somebody hunting for a bug in the
/// lighting.
const FORMAT: wgpu::TextureFormat = wgpu::TextureFormat::Rgba8UnormSrgb;

fn main() {
    let path = std::env::args().nth(1).unwrap_or("weapons.png".into());
    pollster::block_on(run(&path));
}

async fn run(path: &str) {
    let instance = wgpu::Instance::default();
    let adapter = instance
        .request_adapter(&wgpu::RequestAdapterOptions::default())
        .await
        .expect("no GPU adapter — this example needs a real one");
    println!("adapter: {}", adapter.get_info().name);
    let (device, queue) = adapter
        .request_device(&wgpu::DeviceDescriptor {
            label: Some("weapon-preview"),
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

    let unpadded = WIDTH * 4;
    let padded = unpadded.div_ceil(256) * 256;
    let readback = device.create_buffer(&wgpu::BufferDescriptor {
        label: Some("readback"),
        size: (padded * HEIGHT) as u64,
        usage: wgpu::BufferUsages::COPY_DST | wgpu::BufferUsages::MAP_READ,
        mapped_at_creation: false,
    });

    // Each weapon in its own third of the frame. Drawn one at a time because one
    // prop is resident at a time — which is the client's own behaviour, so this
    // exercises the swap path as well as the draw.
    let count = WEAPON_GLBS.len() as u32;
    let mut cleared = false;
    for (index, (name, bytes)) in WEAPON_GLBS.iter().enumerate() {
        let prop = Prop::from_slice(bytes).unwrap_or_else(|e| panic!("{name}: {e}"));
        let (min, max) = prop.bounds();
        let extent = max - min;
        println!(
            "{name}: {} vertices, {} primitives, {} textures, {:.2} x {:.2} x {:.2} cubes",
            prop.vertices.len(),
            prop.primitives.len(),
            prop.textures.len(),
            extent.x,
            extent.y,
            extent.z,
        );
        props.set(&device, &queue, name, &prop);

        // Framed side-on, centred on the model, far enough back for the longest
        // of them. Its own camera per weapon so a pistol is not a speck beside a
        // shotgun — the point is to see each, not to compare their sizes.
        let centre = (min + max) * 0.5;
        let camera = Camera {
            x: centre.x + extent.z * 1.1,
            y: centre.z - extent.z * 0.55,
            z: centre.y + extent.y * 0.9,
            yaw: 150.0,
            pitch: -14.0,
            fov: 42.0,
        };
        let view_proj = camera.view_projection(WIDTH / count, HEIGHT);
        // The full `CameraUniform`: `view_proj` 16 floats, `params` 4, `reveal`
        // 4, `light_transform` 16 — forty, not the thirty-six it is easy to
        // count. wgpu rejects a short one at draw time naming both byte counts,
        // which makes it the one mistake here that fails loudly.
        let mut uniform = [0f32; 40];
        uniform[..16].copy_from_slice(&view_proj.to_cols_array());
        uniform[16] = 0.0055; // fog density, unused by the prop shader
        uniform[17] = 2.0; // detail: the full rig, not the flat path
        uniform[19] = 0.0; // no shadow, as in the client
                           // `reveal` at 20..24 stays zero; the prop shader never reads it.
        uniform[24..40].copy_from_slice(&glam::Mat4::IDENTITY.to_cols_array());
        // Identity: this example puts the prop in world space directly rather
        // than in camera space, so world and model already agree. The client's
        // own path folds the view model's pose into both matrices instead.

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
                label: Some("weapon-preview"),
                color_attachments: &[Some(wgpu::RenderPassColorAttachment {
                    view: &color_view,
                    depth_slice: None,
                    resolve_target: None,
                    ops: wgpu::Operations {
                        // Cleared once, then loaded: three passes each clearing
                        // would leave only the last weapon on the frame.
                        load: if cleared {
                            wgpu::LoadOp::Load
                        } else {
                            wgpu::LoadOp::Clear(wgpu::Color {
                                r: 0.0056,
                                g: 0.0080,
                                b: 0.0137,
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
            assert!(props.draw(&mut pass, &camera_group), "{name} drew nothing");
        }
        queue.submit([encoder.finish()]);
        cleared = true;
    }

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

    // The numbers that make this checkable without looking. A weapon whose base
    // colour was sampled in the wrong colour space, or whose normals came
    // through at the source scale, renders dark rather than absent — so
    // "something was drawn" is not the assertion worth making, "it was drawn
    // with light on it" is.
    let mut covered = 0u64;
    let mut luma = 0f64;
    for px in pixels.chunks_exact(4) {
        let (r, g, b) = (px[0] as f64, px[1] as f64, px[2] as f64);
        // Anything meaningfully off the clear colour is geometry.
        if (r - 17.0).abs() < 10.0 && (g - 22.0).abs() < 10.0 && (b - 31.0).abs() < 10.0 {
            continue;
        }
        covered += 1;
        luma += 0.299 * r + 0.587 * g + 0.114 * b;
    }
    let total = (WIDTH * HEIGHT) as f64;
    println!(
        "coverage {:.1}% of the frame, mean luminance {:.1}/255",
        100.0 * covered as f64 / total,
        luma / covered.max(1) as f64,
    );

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
