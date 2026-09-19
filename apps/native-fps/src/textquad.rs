//! The GPU half of `textlayer`: one texture, drawn as one quad over the HUD.
//!
//! Kept out of `renderer.rs` because it is a separate thing with its own
//! lifecycle — the texture is recreated only when the chat panel changes size and
//! rewritten only when its pixels change, which is a few times a minute, while
//! everything in the renderer is rebuilt per frame.
//!
//! The texture is **sRGB** when the surface is: `textlayer` writes sRGB bytes, the
//! sampler decodes them to linear and the surface re-encodes, so the colours on
//! screen are the bytes that were written. Sampled with `Nearest` at a pixel-exact
//! rect, because a text raster filtered even slightly goes soft.

use crate::textlayer::ChatImage;

const SHADER: &str = r#"
@group(0) @binding(0) var tex: texture_2d<f32>;
@group(0) @binding(1) var samp: sampler;
// The quad in clip space: x0, y0 (top-left), x1, y1 (bottom-right).
@group(0) @binding(2) var<uniform> rect: vec4<f32>;

struct VOut {
    @builtin(position) pos: vec4<f32>,
    @location(0) uv: vec2<f32>,
};

@vertex
fn vs_text(@builtin(vertex_index) i: u32) -> VOut {
    var corners = array<vec2<f32>, 6>(
        vec2<f32>(0.0, 0.0), vec2<f32>(1.0, 0.0), vec2<f32>(0.0, 1.0),
        vec2<f32>(0.0, 1.0), vec2<f32>(1.0, 0.0), vec2<f32>(1.0, 1.0),
    );
    let c = corners[i];
    var out: VOut;
    out.pos = vec4<f32>(mix(rect.x, rect.z, c.x), mix(rect.y, rect.w, c.y), 0.0, 1.0);
    out.uv = c;
    return out;
}

@fragment
fn fs_text(v: VOut) -> @location(0) vec4<f32> {
    return textureSample(tex, samp, v.uv);
}
"#;

struct Upload {
    texture: wgpu::Texture,
    bind_group: wgpu::BindGroup,
    width: u32,
    height: u32,
}

pub struct TextQuad {
    pipeline: wgpu::RenderPipeline,
    layout: wgpu::BindGroupLayout,
    sampler: wgpu::Sampler,
    rect: wgpu::Buffer,
    format: wgpu::TextureFormat,
    upload: Option<Upload>,
    visible: bool,
}

impl TextQuad {
    pub fn new(device: &wgpu::Device, target: wgpu::TextureFormat) -> TextQuad {
        let module = device.create_shader_module(wgpu::ShaderModuleDescriptor {
            label: Some("text-shader"),
            source: wgpu::ShaderSource::Wgsl(SHADER.into()),
        });
        let layout = device.create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
            label: Some("text-layout"),
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
                wgpu::BindGroupLayoutEntry {
                    binding: 2,
                    visibility: wgpu::ShaderStages::VERTEX,
                    ty: wgpu::BindingType::Buffer {
                        ty: wgpu::BufferBindingType::Uniform,
                        has_dynamic_offset: false,
                        min_binding_size: None,
                    },
                    count: None,
                },
            ],
        });
        let pipeline = device.create_render_pipeline(&wgpu::RenderPipelineDescriptor {
            label: Some("text-pipeline"),
            layout: Some(
                &device.create_pipeline_layout(&wgpu::PipelineLayoutDescriptor {
                    label: Some("text-pipeline-layout"),
                    bind_group_layouts: &[Some(&layout)],
                    immediate_size: 0,
                }),
            ),
            vertex: wgpu::VertexState {
                module: &module,
                entry_point: Some("vs_text"),
                buffers: &[],
                compilation_options: Default::default(),
            },
            fragment: Some(wgpu::FragmentState {
                module: &module,
                entry_point: Some("fs_text"),
                targets: &[Some(wgpu::ColorTargetState {
                    format: target,
                    // Straight alpha, like the HUD's own pipeline.
                    blend: Some(wgpu::BlendState::ALPHA_BLENDING),
                    write_mask: wgpu::ColorWrites::ALL,
                })],
                compilation_options: Default::default(),
            }),
            primitive: wgpu::PrimitiveState::default(),
            depth_stencil: None,
            multisample: wgpu::MultisampleState::default(),
            multiview_mask: None,
            cache: None,
        });
        let sampler = device.create_sampler(&wgpu::SamplerDescriptor {
            label: Some("text-sampler"),
            mag_filter: wgpu::FilterMode::Nearest,
            min_filter: wgpu::FilterMode::Nearest,
            ..Default::default()
        });
        let rect = device.create_buffer(&wgpu::BufferDescriptor {
            label: Some("text-rect"),
            size: 16,
            usage: wgpu::BufferUsages::UNIFORM | wgpu::BufferUsages::COPY_DST,
            mapped_at_creation: false,
        });
        TextQuad {
            pipeline,
            layout,
            sampler,
            rect,
            format: if target.is_srgb() {
                wgpu::TextureFormat::Rgba8UnormSrgb
            } else {
                wgpu::TextureFormat::Rgba8Unorm
            },
            upload: None,
            visible: false,
        }
    }

    /// This frame's chat image, if any. Pixels are uploaded only when `changed`
    /// (or the size moved); the rect is rewritten every frame, which is sixteen
    /// bytes and keeps a window resize from leaving the quad in the wrong place.
    pub fn set(
        &mut self,
        device: &wgpu::Device,
        queue: &wgpu::Queue,
        image: Option<(&ChatImage, bool)>,
        screen: (u32, u32),
    ) {
        let Some((img, changed)) = image else {
            self.visible = false;
            return;
        };
        if img.width == 0 || img.height == 0 {
            self.visible = false;
            return;
        }
        let resized = self
            .upload
            .as_ref()
            .is_none_or(|u| u.width != img.width || u.height != img.height);
        if resized {
            let texture = device.create_texture(&wgpu::TextureDescriptor {
                label: Some("text-texture"),
                size: wgpu::Extent3d {
                    width: img.width,
                    height: img.height,
                    depth_or_array_layers: 1,
                },
                mip_level_count: 1,
                sample_count: 1,
                dimension: wgpu::TextureDimension::D2,
                format: self.format,
                usage: wgpu::TextureUsages::TEXTURE_BINDING | wgpu::TextureUsages::COPY_DST,
                view_formats: &[],
            });
            let view = texture.create_view(&wgpu::TextureViewDescriptor::default());
            let bind_group = device.create_bind_group(&wgpu::BindGroupDescriptor {
                label: Some("text-bind-group"),
                layout: &self.layout,
                entries: &[
                    wgpu::BindGroupEntry {
                        binding: 0,
                        resource: wgpu::BindingResource::TextureView(&view),
                    },
                    wgpu::BindGroupEntry {
                        binding: 1,
                        resource: wgpu::BindingResource::Sampler(&self.sampler),
                    },
                    wgpu::BindGroupEntry {
                        binding: 2,
                        resource: self.rect.as_entire_binding(),
                    },
                ],
            });
            self.upload = Some(Upload {
                texture,
                bind_group,
                width: img.width,
                height: img.height,
            });
        }
        if changed || resized {
            if let Some(up) = &self.upload {
                queue.write_texture(
                    wgpu::TexelCopyTextureInfo {
                        texture: &up.texture,
                        mip_level: 0,
                        origin: wgpu::Origin3d::ZERO,
                        aspect: wgpu::TextureAspect::All,
                    },
                    &img.rgba,
                    wgpu::TexelCopyBufferLayout {
                        offset: 0,
                        bytes_per_row: Some(4 * img.width),
                        rows_per_image: Some(img.height),
                    },
                    wgpu::Extent3d {
                        width: img.width,
                        height: img.height,
                        depth_or_array_layers: 1,
                    },
                );
            }
        }
        queue.write_buffer(&self.rect, 0, bytemuck::cast_slice(&clip_rect(img, screen)));
        self.visible = true;
    }

    /// Record the draw. Called inside a pass on the swapchain, after the HUD.
    pub fn draw(&self, pass: &mut wgpu::RenderPass<'_>) {
        if !self.visible {
            return;
        }
        let Some(up) = &self.upload else {
            return;
        };
        pass.set_pipeline(&self.pipeline);
        pass.set_bind_group(0, &up.bind_group, &[]);
        pass.draw(0..6, 0..1);
    }

    pub fn visible(&self) -> bool {
        self.visible && self.upload.is_some()
    }
}

/// The image's screen rect in clip space, snapped to whole pixels so `Nearest`
/// sampling lands one texel per pixel.
pub fn clip_rect(img: &ChatImage, (w, h): (u32, u32)) -> [f32; 4] {
    let (w, h) = (w.max(1) as f32, h.max(1) as f32);
    let x0 = img.x.round();
    let y0 = img.y.round();
    let x1 = x0 + img.width as f32;
    let y1 = y0 + img.height as f32;
    [
        x0 / w * 2.0 - 1.0,
        1.0 - y0 / h * 2.0,
        x1 / w * 2.0 - 1.0,
        1.0 - y1 / h * 2.0,
    ]
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_rect_maps_pixels_to_clip_space() {
        let img = ChatImage {
            width: 100,
            height: 50,
            rgba: vec![],
            x: 0.0,
            y: 0.0,
        };
        assert_eq!(clip_rect(&img, (200, 100)), [-1.0, 1.0, 0.0, 0.0]);
    }

    #[test]
    fn the_text_shader_validates() {
        let module = naga::front::wgsl::parse_str(SHADER).expect("WGSL parses");
        naga::valid::Validator::new(
            naga::valid::ValidationFlags::all(),
            naga::valid::Capabilities::all(),
        )
        .validate(&module)
        .expect("WGSL validates");
    }
}
