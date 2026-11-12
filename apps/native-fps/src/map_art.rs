//! Map key art for the front end: the picture behind the menu and on each tile.
//!
//! The menu used to be rectangles and text on a near-black slab, because that
//! is all its renderer could draw (`front_gpu.rs`: the overlay pipeline, which
//! knows flat colour and glyphs). A menu whose whole subject is *which map*
//! had no picture of any of them.
//!
//! The pictures already existed. `apps/assault-web/src/assets/maps/` holds one
//! frame per map, captured from this game's own renderer for the web client's
//! landing page. They are compiled in here, decoded once on a worker thread,
//! and packed into **one** texture — so the menu's image pass is one bind
//! group and one draw call however many tiles are showing.
//!
//! This module is the atlas and the quads cut from it. It does not decide what
//! is drawn where: `Front::images` lists rectangles by map name, and a map
//! with no art simply yields no quad, leaving the flat card that was there
//! before.

use std::collections::HashMap;

use crate::hud::Hit;

/// The art, by map name. A map missing from this list is drawn without one.
const ART: &[(&str, &[u8])] = &[
    (
        "hd_assault",
        include_bytes!("../../assault-web/src/assets/maps/hd_assault.webp"),
    ),
    (
        "hd_bank",
        include_bytes!("../../assault-web/src/assets/maps/hd_bank.webp"),
    ),
    (
        "hd_dust2",
        include_bytes!("../../assault-web/src/assets/maps/hd_dust2.webp"),
    ),
    (
        "hd_facility",
        include_bytes!("../../assault-web/src/assets/maps/hd_facility.webp"),
    ),
    (
        "hd_inferno",
        include_bytes!("../../assault-web/src/assets/maps/hd_inferno.webp"),
    ),
    (
        "hd_junkflea",
        include_bytes!("../../assault-web/src/assets/maps/hd_junkflea.webp"),
    ),
    (
        "hd_mirage",
        include_bytes!("../../assault-web/src/assets/maps/hd_mirage.webp"),
    ),
    (
        "hd_nuke",
        include_bytes!("../../assault-web/src/assets/maps/hd_nuke.webp"),
    ),
    (
        "hd_office",
        include_bytes!("../../assault-web/src/assets/maps/hd_office.webp"),
    ),
];

/// One cell of the atlas. Half the source's 1600x782: a tile is a few hundred
/// pixels wide, and the backdrop is darkened and sits behind everything, where
/// softness reads as depth rather than as a small picture stretched.
const CELL_W: u32 = 800;
const CELL_H: u32 = 391;
const COLUMNS: u32 = 3;
/// Empty pixels between cells, so bilinear sampling at a cell's edge blends
/// with black rather than with the map next door.
const GUTTER: u32 = 2;

/// The names with art, without decoding anything: what the front end is told
/// before the atlas has finished loading.
pub fn names() -> Vec<String> {
    ART.iter().map(|(name, _)| name.to_string()).collect()
}

/// Every map's art in one RGBA image, and where each one is in it.
pub struct Atlas {
    pub width: u32,
    pub height: u32,
    pub rgba: Vec<u8>,
    /// Map name to its cell, as `[u0, v0, u1, v1]`.
    cells: HashMap<String, [f32; 4]>,
}

impl Atlas {
    /// Decode and pack. A picture that will not decode is left out and said
    /// so: one bad file costs one tile its art, not the menu its background.
    pub fn build() -> Atlas {
        let count = ART.len() as u32;
        let rows = count.div_ceil(COLUMNS).max(1);
        let width = COLUMNS * (CELL_W + GUTTER);
        let height = rows * (CELL_H + GUTTER);
        let mut canvas = image::RgbaImage::new(width, height);
        let mut cells = HashMap::new();
        for (i, (name, bytes)) in ART.iter().enumerate() {
            let decoded = match image::load_from_memory(bytes) {
                Ok(decoded) => decoded,
                Err(e) => {
                    eprintln!("hassault: no art for {name} ({e})");
                    continue;
                }
            };
            let cell = image::imageops::resize(
                &decoded.to_rgba8(),
                CELL_W,
                CELL_H,
                image::imageops::FilterType::Triangle,
            );
            let (col, row) = (i as u32 % COLUMNS, i as u32 / COLUMNS);
            let (x, y) = (col * (CELL_W + GUTTER), row * (CELL_H + GUTTER));
            image::imageops::replace(&mut canvas, &cell, i64::from(x), i64::from(y));
            cells.insert(name.to_string(), cell_uv(x, y, width, height));
        }
        Atlas {
            width,
            height,
            rgba: canvas.into_raw(),
            cells,
        }
    }

    pub fn has(&self, map: &str) -> bool {
        self.cells.contains_key(map)
    }

    /// The six vertices that draw `map`'s art over `rect`, or nothing if it
    /// has none. The picture **covers** the rectangle — scaled until it fills
    /// it and cropped about its centre — so a wide backdrop and a squat tile
    /// both show the map undistorted.
    pub fn quad(
        &self,
        map: &str,
        rect: &Hit,
        tint: [f32; 4],
        window: (f32, f32),
        out: &mut Vec<ArtVertex>,
    ) {
        let Some(cell) = self.cells.get(map) else {
            return;
        };
        if rect.w <= 0.0 || rect.h <= 0.0 || window.0 <= 0.0 || window.1 <= 0.0 {
            return;
        }
        let [u0, v0, u1, v1] = cover(*cell, CELL_W as f32 / CELL_H as f32, rect.w / rect.h);
        let ndc = |x: f32, y: f32| [x / window.0 * 2.0 - 1.0, 1.0 - y / window.1 * 2.0];
        let (l, t, r, b) = (rect.x, rect.y, rect.x + rect.w, rect.y + rect.h);
        let corner = |x, y, u, v| ArtVertex {
            position: ndc(x, y),
            uv: [u, v],
            tint,
        };
        out.extend([
            corner(l, t, u0, v0),
            corner(r, t, u1, v0),
            corner(r, b, u1, v1),
            corner(l, t, u0, v0),
            corner(r, b, u1, v1),
            corner(l, b, u0, v1),
        ]);
    }
}

fn cell_uv(x: u32, y: u32, width: u32, height: u32) -> [f32; 4] {
    [
        x as f32 / width as f32,
        y as f32 / height as f32,
        (x + CELL_W) as f32 / width as f32,
        (y + CELL_H) as f32 / height as f32,
    ]
}

/// Narrow a cell's UVs so a picture of `picture` aspect covers a rectangle of
/// `target` aspect: crop the sides for a taller rectangle, the top and bottom
/// for a wider one, about the centre either way.
fn cover(cell: [f32; 4], picture: f32, target: f32) -> [f32; 4] {
    let [u0, v0, u1, v1] = cell;
    if target < picture {
        let keep = (target / picture).clamp(0.0, 1.0);
        let pad = (u1 - u0) * (1.0 - keep) * 0.5;
        [u0 + pad, v0, u1 - pad, v1]
    } else {
        let keep = (picture / target).clamp(0.0, 1.0);
        let pad = (v1 - v0) * (1.0 - keep) * 0.5;
        [u0, v0 + pad, u1, v1 - pad]
    }
}

/// One corner of an art quad: where, which texel, and how bright.
#[repr(C)]
#[derive(Debug, Clone, Copy, PartialEq, bytemuck::Pod, bytemuck::Zeroable)]
pub struct ArtVertex {
    /// Clip space, like the overlay's.
    pub position: [f32; 2],
    pub uv: [f32; 2],
    /// Multiplies the picture: the backdrop is drawn dark, a tile as it is.
    pub tint: [f32; 4],
}

impl ArtVertex {
    const ATTRS: [wgpu::VertexAttribute; 3] =
        wgpu::vertex_attr_array![0 => Float32x2, 1 => Float32x2, 2 => Float32x4];

    fn layout() -> wgpu::VertexBufferLayout<'static> {
        wgpu::VertexBufferLayout {
            array_stride: std::mem::size_of::<ArtVertex>() as wgpu::BufferAddress,
            step_mode: wgpu::VertexStepMode::Vertex,
            attributes: &Self::ATTRS,
        }
    }
}

const ART_SHADER: &str = r#"
struct VsOut {
    @builtin(position) clip: vec4<f32>,
    @location(0) uv: vec2<f32>,
    @location(1) tint: vec4<f32>,
};

@group(0) @binding(0) var art: texture_2d<f32>;
@group(0) @binding(1) var art_sampler: sampler;

@vertex
fn vs_art(
    @location(0) position: vec2<f32>,
    @location(1) uv: vec2<f32>,
    @location(2) tint: vec4<f32>,
) -> VsOut {
    var out: VsOut;
    out.clip = vec4<f32>(position, 0.0, 1.0);
    out.uv = uv;
    out.tint = tint;
    return out;
}

@fragment
fn fs_art(in: VsOut) -> @location(0) vec4<f32> {
    return textureSample(art, art_sampler, in.uv) * in.tint;
}
"#;

/// As many quads as a menu shows at once, with room: a backdrop and a tile
/// per map.
const MAX_ART_VERTS: usize = 6 * 64;

/// The image pass: the atlas on the GPU, and this frame's quads.
pub struct ArtGpu {
    pipeline: wgpu::RenderPipeline,
    layout: wgpu::BindGroupLayout,
    sampler: wgpu::Sampler,
    srgb: bool,
    /// `None` until the atlas has been decoded and handed over.
    bind_group: Option<wgpu::BindGroup>,
    buffer: wgpu::Buffer,
    verts: u32,
}

impl ArtGpu {
    pub fn new(device: &wgpu::Device, target: wgpu::TextureFormat) -> ArtGpu {
        let module = device.create_shader_module(wgpu::ShaderModuleDescriptor {
            label: Some("art-shader"),
            source: wgpu::ShaderSource::Wgsl(ART_SHADER.into()),
        });
        let layout = device.create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
            label: Some("art-layout"),
            entries: &[
                wgpu::BindGroupLayoutEntry {
                    binding: 0,
                    visibility: wgpu::ShaderStages::FRAGMENT,
                    ty: wgpu::BindingType::Texture {
                        sample_type: wgpu::TextureSampleType::Float { filterable: true },
                        view_dimension: wgpu::TextureViewDimension::D2,
                        multisampled: false,
                    },
                    count: None,
                },
                wgpu::BindGroupLayoutEntry {
                    binding: 1,
                    visibility: wgpu::ShaderStages::FRAGMENT,
                    ty: wgpu::BindingType::Sampler(wgpu::SamplerBindingType::Filtering),
                    count: None,
                },
            ],
        });
        let pipeline_layout = device.create_pipeline_layout(&wgpu::PipelineLayoutDescriptor {
            label: Some("art-pipeline-layout"),
            bind_group_layouts: &[Some(&layout)],
            immediate_size: 0,
        });
        let pipeline = device.create_render_pipeline(&wgpu::RenderPipelineDescriptor {
            label: Some("art-pipeline"),
            layout: Some(&pipeline_layout),
            vertex: wgpu::VertexState {
                module: &module,
                entry_point: Some("vs_art"),
                buffers: &[Some(ArtVertex::layout())],
                compilation_options: Default::default(),
            },
            fragment: Some(wgpu::FragmentState {
                module: &module,
                entry_point: Some("fs_art"),
                targets: &[Some(wgpu::ColorTargetState {
                    format: target,
                    // Blended, so a tile's art can fade in over the backdrop.
                    blend: Some(wgpu::BlendState::ALPHA_BLENDING),
                    write_mask: wgpu::ColorWrites::ALL,
                })],
                compilation_options: Default::default(),
            }),
            primitive: wgpu::PrimitiveState {
                topology: wgpu::PrimitiveTopology::TriangleList,
                cull_mode: None,
                ..Default::default()
            },
            depth_stencil: None,
            multisample: wgpu::MultisampleState::default(),
            multiview_mask: None,
            cache: None,
        });
        let sampler = device.create_sampler(&wgpu::SamplerDescriptor {
            label: Some("art-sampler"),
            mag_filter: wgpu::FilterMode::Linear,
            min_filter: wgpu::FilterMode::Linear,
            ..Default::default()
        });
        let buffer = device.create_buffer(&wgpu::BufferDescriptor {
            label: Some("art"),
            size: (MAX_ART_VERTS * std::mem::size_of::<ArtVertex>()) as wgpu::BufferAddress,
            usage: wgpu::BufferUsages::VERTEX | wgpu::BufferUsages::COPY_DST,
            mapped_at_creation: false,
        });
        ArtGpu {
            pipeline,
            layout,
            sampler,
            srgb: target.is_srgb(),
            bind_group: None,
            buffer,
            verts: 0,
        }
    }

    /// Put the atlas on the GPU. Called once per device: a new surface after a
    /// match is a new device, with no texture on it.
    pub fn upload(&mut self, device: &wgpu::Device, queue: &wgpu::Queue, atlas: &Atlas) {
        let size = wgpu::Extent3d {
            width: atlas.width,
            height: atlas.height,
            depth_or_array_layers: 1,
        };
        let texture = device.create_texture(&wgpu::TextureDescriptor {
            label: Some("map-art"),
            size,
            mip_level_count: 1,
            sample_count: 1,
            dimension: wgpu::TextureDimension::D2,
            // The pictures are sRGB-encoded, like any image file.
            format: if self.srgb {
                wgpu::TextureFormat::Rgba8UnormSrgb
            } else {
                wgpu::TextureFormat::Rgba8Unorm
            },
            usage: wgpu::TextureUsages::TEXTURE_BINDING | wgpu::TextureUsages::COPY_DST,
            view_formats: &[],
        });
        queue.write_texture(
            wgpu::TexelCopyTextureInfo {
                texture: &texture,
                mip_level: 0,
                origin: wgpu::Origin3d::ZERO,
                aspect: wgpu::TextureAspect::All,
            },
            &atlas.rgba,
            wgpu::TexelCopyBufferLayout {
                offset: 0,
                bytes_per_row: Some(atlas.width * 4),
                rows_per_image: Some(atlas.height),
            },
            size,
        );
        self.bind_group = Some(device.create_bind_group(&wgpu::BindGroupDescriptor {
            label: Some("map-art"),
            layout: &self.layout,
            entries: &[
                wgpu::BindGroupEntry {
                    binding: 0,
                    resource: wgpu::BindingResource::TextureView(
                        &texture.create_view(&Default::default()),
                    ),
                },
                wgpu::BindGroupEntry {
                    binding: 1,
                    resource: wgpu::BindingResource::Sampler(&self.sampler),
                },
            ],
        }));
    }

    pub fn ready(&self) -> bool {
        self.bind_group.is_some()
    }

    pub fn set_vertices(&mut self, queue: &wgpu::Queue, vertices: &[ArtVertex]) {
        let count = vertices.len().min(MAX_ART_VERTS);
        self.verts = count as u32;
        if count > 0 {
            queue.write_buffer(&self.buffer, 0, bytemuck::cast_slice(&vertices[..count]));
        }
    }

    pub fn draw(&self, pass: &mut wgpu::RenderPass<'_>) {
        let Some(bind_group) = &self.bind_group else {
            return;
        };
        if self.verts == 0 {
            return;
        }
        pass.set_pipeline(&self.pipeline);
        pass.set_bind_group(0, bind_group, &[]);
        pass.set_vertex_buffer(0, self.buffer.slice(..));
        pass.draw(0..self.verts, 0..1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_art_shader_validates() {
        let module = naga::front::wgsl::parse_str(ART_SHADER).expect("parses");
        naga::valid::Validator::new(
            naga::valid::ValidationFlags::all(),
            naga::valid::Capabilities::empty(),
        )
        .validate(&module)
        .expect("validates");
        let names: Vec<&str> = module
            .entry_points
            .iter()
            .map(|e| e.name.as_str())
            .collect();
        assert!(names.contains(&"vs_art") && names.contains(&"fs_art"));
    }

    #[test]
    fn an_art_vertex_is_laid_out_the_way_the_shader_reads_it() {
        assert_eq!(std::mem::size_of::<ArtVertex>(), 8 * 4);
        let offsets: Vec<u64> = ArtVertex::ATTRS.iter().map(|a| a.offset).collect();
        assert_eq!(offsets, vec![0, 8, 16]);
    }

    #[test]
    fn every_picture_decodes_into_its_own_cell() {
        let atlas = Atlas::build();
        assert_eq!(atlas.rgba.len() as u32, atlas.width * atlas.height * 4);
        let mut seen: Vec<[u32; 2]> = Vec::new();
        for name in names() {
            assert!(atlas.has(&name), "{name} did not decode");
            let [u0, v0, u1, v1] = atlas.cells[&name];
            assert!(u0 >= 0.0 && v0 >= 0.0 && u1 <= 1.0 && v1 <= 1.0 && u0 < u1 && v0 < v1);
            let origin = [
                (u0 * atlas.width as f32).round() as u32,
                (v0 * atlas.height as f32).round() as u32,
            ];
            assert!(!seen.contains(&origin), "{name} shares a cell");
            seen.push(origin);
            // And it is a picture: the middle of the cell is not empty.
            let (x, y) = (origin[0] + CELL_W / 2, origin[1] + CELL_H / 2);
            let at = ((y * atlas.width + x) * 4) as usize;
            assert_eq!(
                atlas.rgba[at + 3],
                255,
                "{name} is transparent at its centre"
            );
        }
        assert!(!atlas.has("hd_no_such_map"));
    }

    #[test]
    fn a_picture_covers_its_rectangle_without_stretching() {
        let cell = [0.0, 0.0, 0.5, 0.5];
        // The same shape as the picture: all of it.
        assert_eq!(cover(cell, 2.0, 2.0), cell);
        // A square from a 2:1 picture: the middle half of its width.
        assert_eq!(cover(cell, 2.0, 1.0), [0.125, 0.0, 0.375, 0.5]);
        // A 4:1 strip from it: the middle half of its height.
        assert_eq!(cover(cell, 2.0, 4.0), [0.0, 0.125, 0.5, 0.375]);
    }

    #[test]
    fn a_quad_lands_where_its_rectangle_is_and_a_map_without_art_draws_nothing() {
        let mut atlas = Atlas {
            width: 100,
            height: 100,
            rgba: Vec::new(),
            cells: HashMap::new(),
        };
        atlas.cells.insert("hd_x".into(), [0.0, 0.0, 1.0, 1.0]);
        let rect = Hit {
            x: 0.0,
            y: 0.0,
            w: 640.0,
            h: 360.0,
        };
        let mut out = Vec::new();
        atlas.quad("hd_none", &rect, [1.0; 4], (1280.0, 720.0), &mut out);
        assert!(out.is_empty());
        atlas.quad("hd_x", &rect, [0.5; 4], (1280.0, 720.0), &mut out);
        assert_eq!(out.len(), 6);
        // The top-left quarter of the window: clip space -1..0 across, 1..0 down.
        assert_eq!(out[0].position, [-1.0, 1.0]);
        assert_eq!(out[2].position, [0.0, 0.0]);
        assert!(out.iter().all(|v| v.tint == [0.5; 4]));
        // A degenerate rectangle is not a division by zero.
        let flat = Hit { h: 0.0, ..rect };
        atlas.quad("hd_x", &flat, [1.0; 4], (1280.0, 720.0), &mut out);
        assert_eq!(out.len(), 6);
    }
}
