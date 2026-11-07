//! The GPU half of the HUD: one vertex buffer, one glyph atlas, one draw.
//!
//! Shapes and text arrive interleaved in one `OverlayVertex` stream, in the order
//! they were painted, so a label lands on its panel and under the menu's scrim
//! without the renderer knowing either exists. A vertex's `uv` says which it is:
//! negative is a flat colour, anything else a texel of `text_kit`'s atlas.
//!
//! Its own module, shared by `Renderer` and `examples/hud_preview`, so the preview
//! draws the HUD with the client's pipeline instead of a restatement of it.
//!
//! The atlas is **sRGB when the target is**, as chat's old texture pass was: colour
//! glyphs (emoji) are sRGB bytes, the sampler decodes them and the target
//! re-encodes, so they land on screen as written. Coverage lives in alpha, which
//! sRGB formats leave linear, and a mono glyph's colour channels are white either
//! way. Sampled `Nearest` at pixel-exact quads, because a filtered glyph goes
//! soft.

use crate::hud::OverlayVertex;
use crate::text_kit::{AtlasUpload, ATLAS_SIZE};

pub const OVERLAY_SHADER: &str = r#"
@group(0) @binding(0) var atlas: texture_2d<f32>;
@group(0) @binding(1) var samp: sampler;

struct OverlayIn {
    @location(0) position: vec2<f32>,
    @location(1) color: vec4<f32>,
    @location(2) uv: vec2<f32>,
};

struct OverlayOut {
    @builtin(position) clip_position: vec4<f32>,
    @location(0) color: vec4<f32>,
    @location(1) uv: vec2<f32>,
};

@vertex
fn vs_overlay(in: OverlayIn) -> OverlayOut {
    var out: OverlayOut;
    // The overlay pass has no depth attachment; z only has to be in range.
    out.clip_position = vec4<f32>(in.position, 0.0, 1.0);
    out.color = in.color;
    out.uv = in.uv;
    return out;
}

@fragment
fn fs_overlay(in: OverlayOut) -> @location(0) vec4<f32> {
    // Sampled before branching, with an explicit level: a derivative-taking
    // sample is not allowed in non-uniform control flow, and a solid vertex's
    // negative uv clamps harmlessly.
    let texel = textureSampleLevel(atlas, samp, max(in.uv, vec2<f32>(0.0)), 0.0);
    if (in.uv.x < -1.5) {
        // Grid fill: a one-pixel line every `uv.y` pixels, in screen space so
        // the grid is fixed to the screen, not stretched with the panel.
        let cell = max(in.uv.y, 2.0);
        let f = in.clip_position.xy - floor(in.clip_position.xy / cell) * cell;
        let on = f.x < 1.0 || f.y < 1.0;
        return vec4<f32>(in.color.rgb, select(0.0, in.color.a, on));
    }
    if (in.uv.x < 0.0) {
        return in.color;
    }
    return vec4<f32>(in.color.rgb * texel.rgb, in.color.a * texel.a);
}
"#;

impl OverlayVertex {
    const ATTRS: [wgpu::VertexAttribute; 3] =
        wgpu::vertex_attr_array![0 => Float32x2, 1 => Float32x4, 2 => Float32x2];

    pub fn layout() -> wgpu::VertexBufferLayout<'static> {
        wgpu::VertexBufferLayout {
            array_stride: std::mem::size_of::<OverlayVertex>() as wgpu::BufferAddress,
            step_mode: wgpu::VertexStepMode::Vertex,
            attributes: &Self::ATTRS,
        }
    }
}

pub struct OverlayGpu {
    pipeline: wgpu::RenderPipeline,
    atlas: wgpu::Texture,
    bind_group: wgpu::BindGroup,
    buffer: wgpu::Buffer,
    capacity: usize,
    verts: u32,
}

impl OverlayGpu {
    pub fn new(device: &wgpu::Device, target: wgpu::TextureFormat, capacity: usize) -> OverlayGpu {
        let module = device.create_shader_module(wgpu::ShaderModuleDescriptor {
            label: Some("overlay-shader"),
            source: wgpu::ShaderSource::Wgsl(OVERLAY_SHADER.into()),
        });
        let layout = device.create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
            label: Some("overlay-atlas-layout"),
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
            label: Some("overlay-layout"),
            bind_group_layouts: &[Some(&layout)],
            immediate_size: 0,
        });
        // No camera, no depth, and **alpha blending** — the one state that
        // differs from everything else drawn: without it a panel behind the kill
        // feed is opaque black rather than a wash.
        let pipeline = device.create_render_pipeline(&wgpu::RenderPipelineDescriptor {
            label: Some("overlay-pipeline"),
            layout: Some(&pipeline_layout),
            vertex: wgpu::VertexState {
                module: &module,
                entry_point: Some("vs_overlay"),
                buffers: &[Some(OverlayVertex::layout())],
                compilation_options: Default::default(),
            },
            fragment: Some(wgpu::FragmentState {
                module: &module,
                entry_point: Some("fs_overlay"),
                targets: &[Some(wgpu::ColorTargetState {
                    format: target,
                    blend: Some(wgpu::BlendState::ALPHA_BLENDING),
                    write_mask: wgpu::ColorWrites::ALL,
                })],
                compilation_options: Default::default(),
            }),
            primitive: wgpu::PrimitiveState {
                topology: wgpu::PrimitiveTopology::TriangleList,
                // **No culling.** The HUD is flat quads, and a quad has no
                // outside: culling would drop whichever half of the layout
                // happened to be wound the other way, silently.
                cull_mode: None,
                ..Default::default()
            },
            depth_stencil: None,
            multisample: wgpu::MultisampleState::default(),
            multiview_mask: None,
            cache: None,
        });
        let atlas = device.create_texture(&wgpu::TextureDescriptor {
            label: Some("hud-atlas"),
            size: wgpu::Extent3d {
                width: ATLAS_SIZE,
                height: ATLAS_SIZE,
                depth_or_array_layers: 1,
            },
            mip_level_count: 1,
            sample_count: 1,
            dimension: wgpu::TextureDimension::D2,
            format: if target.is_srgb() {
                wgpu::TextureFormat::Rgba8UnormSrgb
            } else {
                wgpu::TextureFormat::Rgba8Unorm
            },
            usage: wgpu::TextureUsages::TEXTURE_BINDING | wgpu::TextureUsages::COPY_DST,
            view_formats: &[],
        });
        let sampler = device.create_sampler(&wgpu::SamplerDescriptor {
            label: Some("hud-atlas-sampler"),
            mag_filter: wgpu::FilterMode::Nearest,
            min_filter: wgpu::FilterMode::Nearest,
            ..Default::default()
        });
        let bind_group = device.create_bind_group(&wgpu::BindGroupDescriptor {
            label: Some("hud-atlas"),
            layout: &layout,
            entries: &[
                wgpu::BindGroupEntry {
                    binding: 0,
                    resource: wgpu::BindingResource::TextureView(
                        &atlas.create_view(&Default::default()),
                    ),
                },
                wgpu::BindGroupEntry {
                    binding: 1,
                    resource: wgpu::BindingResource::Sampler(&sampler),
                },
            ],
        });
        let buffer = device.create_buffer(&wgpu::BufferDescriptor {
            label: Some("overlay"),
            size: (capacity * std::mem::size_of::<OverlayVertex>()) as wgpu::BufferAddress,
            usage: wgpu::BufferUsages::VERTEX | wgpu::BufferUsages::COPY_DST,
            mapped_at_creation: false,
        });
        OverlayGpu {
            pipeline,
            atlas,
            bind_group,
            buffer,
            capacity,
            verts: 0,
        }
    }

    /// Replace this frame's geometry. Truncated rather than grown — a HUD that
    /// reallocated mid-frame would stutter — and **reported** when it is, since a
    /// silently truncated HUD just loses whatever was painted last.
    pub fn set_vertices(&mut self, queue: &wgpu::Queue, vertices: &[OverlayVertex]) {
        if vertices.len() > self.capacity {
            crate::divergence::note_overflow("overlay", vertices.len(), self.capacity);
        }
        let count = vertices.len().min(self.capacity);
        self.verts = count as u32;
        if count > 0 {
            queue.write_buffer(&self.buffer, 0, bytemuck::cast_slice(&vertices[..count]));
        }
    }

    /// Copy new glyph rows into the atlas.
    pub fn upload_atlas(&self, queue: &wgpu::Queue, upload: &AtlasUpload) {
        if upload.rows == 0 {
            return;
        }
        queue.write_texture(
            wgpu::TexelCopyTextureInfo {
                texture: &self.atlas,
                mip_level: 0,
                origin: wgpu::Origin3d {
                    x: 0,
                    y: upload.y,
                    z: 0,
                },
                aspect: wgpu::TextureAspect::All,
            },
            &upload.bytes,
            wgpu::TexelCopyBufferLayout {
                offset: 0,
                bytes_per_row: Some(ATLAS_SIZE * 4),
                rows_per_image: Some(upload.rows),
            },
            wgpu::Extent3d {
                width: ATLAS_SIZE,
                height: upload.rows,
                depth_or_array_layers: 1,
            },
        );
    }

    pub fn has_vertices(&self) -> bool {
        self.verts > 0
    }

    pub fn draw(&self, pass: &mut wgpu::RenderPass<'_>) {
        if self.verts == 0 {
            return;
        }
        pass.set_pipeline(&self.pipeline);
        pass.set_bind_group(0, &self.bind_group, &[]);
        pass.set_vertex_buffer(0, self.buffer.slice(..));
        pass.draw(0..self.verts, 0..1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_overlay_shader_validates() {
        let module = naga::front::wgsl::parse_str(OVERLAY_SHADER).expect("parses");
        naga::valid::Validator::new(
            naga::valid::ValidationFlags::all(),
            naga::valid::Capabilities::empty(),
        )
        .validate(&module)
        .expect("validates");
        let names: Vec<&str> = module.entry_points.iter().map(|e| e.name.as_str()).collect();
        assert!(names.contains(&"vs_overlay") && names.contains(&"fs_overlay"));
    }

    #[test]
    fn an_overlay_vertex_is_laid_out_the_way_the_shader_reads_it() {
        assert_eq!(std::mem::size_of::<OverlayVertex>(), 8 * 4);
        let v = OverlayVertex {
            position: [1.0, 2.0],
            color: [3.0, 4.0, 5.0, 6.0],
            uv: [7.0, 8.0],
        };
        let floats: &[f32] = bytemuck::cast_slice(std::slice::from_ref(&v));
        assert_eq!(floats, &[1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]);
        let offsets: Vec<u64> = OverlayVertex::ATTRS.iter().map(|a| a.offset).collect();
        assert_eq!(offsets, vec![0, 8, 24]);
    }
}
