//! Render the menu's stage offscreen and write a PNG.
//!
//! The stage is a picture, and the properties worth checking about it are a
//! picture's: that a weapon is framed whole and lit, that an operator stands
//! upright facing the camera. This draws both through `ShowcaseGpu` — the same
//! object the menu owns, on a device opened with the menu's limits — so what
//! comes out is what the LOADOUT tab will show.
//!
//! ```text
//! cargo run --example showcase_preview -- stage.png
//! ```
//!
//! One tile per weapon prop at rest, one mid-inspect, then each side's
//! operator.

use hassault_native::animator::Squad;
use hassault_native::prop::{Prop, WEAPON_GLBS};
use hassault_native::showcase::{Showcase, Subject, INSPECT_TIME};
use hassault_native::showcase_gpu::ShowcaseGpu;

const TILE_W: u32 = 420;
const TILE_H: u32 = 300;
const FORMAT: wgpu::TextureFormat = wgpu::TextureFormat::Rgba8UnormSrgb;

fn main() {
    let path = std::env::args().nth(1).unwrap_or("stage.png".into());
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
            label: Some("showcase-preview"),
            // The menu's own limits (`front_gpu.rs`): a stage that only works
            // on a device the menu does not open proves nothing.
            required_limits: wgpu::Limits::downlevel_defaults().using_resolution(adapter.limits()),
            ..Default::default()
        })
        .await
        .expect("device");

    let mut stage = ShowcaseGpu::new(&device, &queue, FORMAT);
    for (id, bytes) in WEAPON_GLBS {
        let prop = Prop::from_slice(bytes).unwrap_or_else(|e| panic!("{id}: {e}"));
        let (min, max) = prop.bounds();
        let extent = max - min;
        println!("{id}: {:.2} x {:.2} x {:.2} cubes", extent.x, extent.y, extent.z);
        stage.add_prop(&device, &queue, id, &prop);
    }
    stage.add_operators(&device, &queue, Squad::load().expect("operators"));

    // What to draw, and how far into an inspect.
    let mut shots: Vec<(Subject, f32)> = WEAPON_GLBS
        .iter()
        .map(|(id, _)| (Subject::Weapon(id.to_string()), 0.0))
        .collect();
    shots.push((Subject::Weapon("assault".into()), INSPECT_TIME * 0.35));
    shots.push((Subject::Operator(0), 0.0));
    shots.push((Subject::Operator(1), 0.0));

    let columns = 4u32;
    let rows = (shots.len() as u32).div_ceil(columns);
    let (width, height) = (TILE_W * columns, TILE_H * rows);
    let mut sheet = vec![0u8; (width * height * 4) as usize];

    for (index, (subject, inspect)) in shots.iter().enumerate() {
        let mut show = Showcase::default();
        show.show(Some(subject.clone()));
        show.press();
        if *inspect > 0.0 {
            show.inspect();
            show.tick(*inspect);
        }
        // A few frames, so an operator has settled into its pose.
        for _ in 0..20 {
            let mut encoder = device.create_command_encoder(&Default::default());
            // The stage supersamples; ask for a rectangle that comes out at
            // exactly one tile.
            stage.render(
                &device,
                &queue,
                &mut encoder,
                &show,
                TILE_W as f32 / 1.5,
                TILE_H as f32 / 1.5,
                1.0 / 60.0,
            );
            queue.submit([encoder.finish()]);
        }
        let (texture, w, h) = stage.picture().expect("the stage drew nothing");
        assert_eq!((w, h), (TILE_W, TILE_H));
        let pixels = read_back(&device, &queue, texture, w, h);

        // Lit, and present: the backdrop is one flat colour, so anything that
        // differs from the corner pixel is the subject.
        let corner = [pixels[0], pixels[1], pixels[2]];
        let (mut covered, mut luma) = (0u64, 0f64);
        for px in pixels.chunks_exact(4) {
            if (0..3).all(|c| (px[c] as i32 - corner[c] as i32).abs() < 6) {
                continue;
            }
            covered += 1;
            luma += 0.299 * px[0] as f64 + 0.587 * px[1] as f64 + 0.114 * px[2] as f64;
        }
        println!(
            "{subject:?} at {inspect:.2}s: {:.1}% of the tile, mean luminance {:.0}/255",
            100.0 * covered as f64 / (w * h) as f64,
            luma / covered.max(1) as f64,
        );

        let (ox, oy) = (index as u32 % columns * TILE_W, index as u32 / columns * TILE_H);
        for row in 0..h {
            let from = (row * w * 4) as usize;
            let to = (((oy + row) * width + ox) * 4) as usize;
            sheet[to..to + (w * 4) as usize].copy_from_slice(&pixels[from..from + (w * 4) as usize]);
        }
    }

    let file = std::fs::File::create(path).expect("create png");
    let mut encoder = png::Encoder::new(std::io::BufWriter::new(file), width, height);
    encoder.set_color(png::ColorType::Rgba);
    encoder.set_depth(png::BitDepth::Eight);
    encoder
        .write_header()
        .expect("png header")
        .write_image_data(&sheet)
        .expect("png data");
    println!("wrote {path} ({width}x{height})");
}

fn read_back(
    device: &wgpu::Device,
    queue: &wgpu::Queue,
    texture: &wgpu::Texture,
    width: u32,
    height: u32,
) -> Vec<u8> {
    let unpadded = width * 4;
    let padded = unpadded.div_ceil(256) * 256;
    let buffer = device.create_buffer(&wgpu::BufferDescriptor {
        label: Some("readback"),
        size: (padded * height) as u64,
        usage: wgpu::BufferUsages::COPY_DST | wgpu::BufferUsages::MAP_READ,
        mapped_at_creation: false,
    });
    let mut encoder = device.create_command_encoder(&Default::default());
    encoder.copy_texture_to_buffer(
        wgpu::TexelCopyTextureInfo {
            texture,
            mip_level: 0,
            origin: wgpu::Origin3d::ZERO,
            aspect: wgpu::TextureAspect::All,
        },
        wgpu::TexelCopyBufferInfo {
            buffer: &buffer,
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
    let slice = buffer.slice(..);
    slice.map_async(wgpu::MapMode::Read, |r| r.expect("map"));
    device
        .poll(wgpu::PollType::Wait {
            submission_index: None,
            timeout: None,
        })
        .expect("poll");
    let mapped = slice.get_mapped_range().expect("map range");
    let mut pixels = Vec::with_capacity((unpadded * height) as usize);
    for row in 0..height {
        let start = (row * padded) as usize;
        pixels.extend_from_slice(&mapped[start..start + unpadded as usize]);
    }
    pixels
}
