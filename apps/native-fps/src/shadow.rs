//! The sun's shadow map: one depth pass, rendered **once per map**.
//!
//! This is where most of the browser client's moodiness came from, and its
//! absence is why the native world read as lit everywhere at once. The browser
//! sets `shadowMap.autoUpdate = false` and renders the map exactly once
//! (`HorribleAssaultPanel.tsx`); this does the same thing for the same reason —
//! the geometry is static and so is the sun, so a per-frame shadow pass would be
//! paying sixty times a second for a picture that never changes. It is the whole
//! reason real shadows are affordable here at all.
//!
//! ## What casts, and what does not
//!
//! **Only the static world casts.** Players do not, and that is a match with the
//! browser rather than a shortcut: with `autoUpdate` off, an avatar added to the
//! scene never appears in a map rendered before it existed. Players still
//! *receive*, which is the half that matters — an operator standing in a
//! building's shadow should be dark, and a shadow that follows them around at 20
//! Hz interpolated positions would be a lie about where they are anyway.
//!
//! ## The one line that makes shadows possible in a Cube world
//!
//! **Front faces only.** A Cube 1 map is a sealed box: every open cell emits a
//! *ceiling* quad facing down. Render back faces into the shadow map and the sky
//! lid catches all the light, putting the entire level in shadow — which reads
//! as the lighting simply being broken rather than as a culling bug. three has
//! the same trap and the same fix, spelled `material.shadowSide = FrontSide`.
//!
//! Here that is `cull_mode: Back`: the mesher winds every quad counter-clockwise
//! as seen from the open side, so a ceiling seen from the sun above is
//! back-facing and drops out, while walls and floors cast exactly as they should.

use glam::{Mat4, Vec3};

use crate::renderer::Vertex;

/// Shadow map resolution, matching the browser's `sun.shadow.mapSize`.
const SIZE: u32 = 2048;

/// Depth-only, so the cheapest format that holds a depth.
const FORMAT: wgpu::TextureFormat = wgpu::TextureFormat::Depth32Float;

/// The sun's direction, as a *direction toward the light*.
///
/// Must stay the same vector `lighting.wgsl.inc` shades with: a shadow map cast
/// from one direction and a Lambert term computed from another produces surfaces
/// lit from one side and shadowed from the other, which looks like broken
/// geometry rather than a mismatched constant.
pub const SUN_DIR: Vec3 = Vec3::new(0.523, 0.780, 0.343);

#[repr(C)]
#[derive(Clone, Copy, bytemuck::Pod, bytemuck::Zeroable)]
struct ShadowUniform {
    light_view_proj: [[f32; 4]; 4],
    /// x: one shadow texel in **UV** units, for the PCF kernel's offsets.
    /// y: depth bias. The rest is padding to the 16-byte minimum.
    params: [f32; 4],
}

pub struct ShadowMap {
    pub layout: wgpu::BindGroupLayout,
    pub bind_group: wgpu::BindGroup,
}

impl ShadowMap {
    /// Build the map and render the world into it.
    ///
    /// Takes the vertices rather than the uploaded buffer because the pass needs
    /// its own draw anyway, and handing it the data keeps the caller from having
    /// to think about whether the world buffer is bound.
    pub fn new(
        device: &wgpu::Device,
        queue: &wgpu::Queue,
        world: &wgpu::Buffer,
        world_verts: u32,
        bounds: (Vec3, Vec3),
    ) -> ShadowMap {
        let (light_view_proj, _world_texel) = fit(bounds);

        let texture = device.create_texture(&wgpu::TextureDescriptor {
            label: Some("shadow-map"),
            size: wgpu::Extent3d {
                width: SIZE,
                height: SIZE,
                depth_or_array_layers: 1,
            },
            mip_level_count: 1,
            sample_count: 1,
            dimension: wgpu::TextureDimension::D2,
            format: FORMAT,
            usage: wgpu::TextureUsages::RENDER_ATTACHMENT | wgpu::TextureUsages::TEXTURE_BINDING,
            view_formats: &[],
        });
        let view = texture.create_view(&wgpu::TextureViewDescriptor::default());

        let uniform = device.create_buffer(&wgpu::BufferDescriptor {
            label: Some("shadow-uniform"),
            size: std::mem::size_of::<ShadowUniform>() as u64,
            usage: wgpu::BufferUsages::UNIFORM | wgpu::BufferUsages::COPY_DST,
            mapped_at_creation: false,
        });
        queue.write_buffer(
            &uniform,
            0,
            bytemuck::cast_slice(&[ShadowUniform {
                light_view_proj: light_view_proj.to_cols_array_2d(),
                // The PCF kernel steps in UV, so the texel it needs is 1/SIZE —
                // not the world-space size `fit` also returns, which is what the
                // same word means one function over.
                params: [1.0 / SIZE as f32, 0.0015, 0.0, 0.0],
            }]),
        );

        // A **comparison** sampler, not a filtering one: the hardware then does
        // the depth test as part of the fetch and returns a filtered 0..1
        // occlusion rather than a depth, which is what makes a 3x3 PCF four
        // instructions instead of nine compares.
        let sampler = device.create_sampler(&wgpu::SamplerDescriptor {
            label: Some("shadow-sampler"),
            address_mode_u: wgpu::AddressMode::ClampToEdge,
            address_mode_v: wgpu::AddressMode::ClampToEdge,
            address_mode_w: wgpu::AddressMode::ClampToEdge,
            mag_filter: wgpu::FilterMode::Linear,
            min_filter: wgpu::FilterMode::Linear,
            mipmap_filter: wgpu::MipmapFilterMode::Nearest,
            compare: Some(wgpu::CompareFunction::LessEqual),
            ..Default::default()
        });

        let layout = device.create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
            label: Some("shadow-layout"),
            entries: &[
                wgpu::BindGroupLayoutEntry {
                    binding: 0,
                    visibility: wgpu::ShaderStages::VERTEX_FRAGMENT,
                    ty: wgpu::BindingType::Buffer {
                        ty: wgpu::BufferBindingType::Uniform,
                        has_dynamic_offset: false,
                        min_binding_size: None,
                    },
                    count: None,
                },
                wgpu::BindGroupLayoutEntry {
                    binding: 1,
                    visibility: wgpu::ShaderStages::FRAGMENT,
                    ty: wgpu::BindingType::Texture {
                        sample_type: wgpu::TextureSampleType::Depth,
                        view_dimension: wgpu::TextureViewDimension::D2,
                        multisampled: false,
                    },
                    count: None,
                },
                wgpu::BindGroupLayoutEntry {
                    binding: 2,
                    visibility: wgpu::ShaderStages::FRAGMENT,
                    ty: wgpu::BindingType::Sampler(wgpu::SamplerBindingType::Comparison),
                    count: None,
                },
            ],
        });
        let bind_group = device.create_bind_group(&wgpu::BindGroupDescriptor {
            label: Some("shadow"),
            layout: &layout,
            entries: &[
                wgpu::BindGroupEntry {
                    binding: 0,
                    resource: uniform.as_entire_binding(),
                },
                wgpu::BindGroupEntry {
                    binding: 1,
                    resource: wgpu::BindingResource::TextureView(&view),
                },
                wgpu::BindGroupEntry {
                    binding: 2,
                    resource: wgpu::BindingResource::Sampler(&sampler),
                },
            ],
        });

        render_once(device, queue, &view, &uniform, world, world_verts);

        ShadowMap { layout, bind_group }
    }
}

/// Draw the static world into the map, once.
fn render_once(
    device: &wgpu::Device,
    queue: &wgpu::Queue,
    view: &wgpu::TextureView,
    uniform: &wgpu::Buffer,
    world: &wgpu::Buffer,
    world_verts: u32,
) {
    let shader = device.create_shader_module(wgpu::ShaderModuleDescriptor {
        label: Some("shadow-shader"),
        source: wgpu::ShaderSource::Wgsl(include_str!("shadow.wgsl").into()),
    });
    let cast_layout = device.create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
        label: Some("shadow-cast-layout"),
        entries: &[wgpu::BindGroupLayoutEntry {
            binding: 0,
            visibility: wgpu::ShaderStages::VERTEX,
            ty: wgpu::BindingType::Buffer {
                ty: wgpu::BufferBindingType::Uniform,
                has_dynamic_offset: false,
                min_binding_size: None,
            },
            count: None,
        }],
    });
    let cast_group = device.create_bind_group(&wgpu::BindGroupDescriptor {
        label: Some("shadow-cast"),
        layout: &cast_layout,
        entries: &[wgpu::BindGroupEntry {
            binding: 0,
            resource: uniform.as_entire_binding(),
        }],
    });
    let pipeline_layout = device.create_pipeline_layout(&wgpu::PipelineLayoutDescriptor {
        label: Some("shadow-cast-pipeline-layout"),
        bind_group_layouts: &[Some(&cast_layout)],
        immediate_size: 0,
    });
    let pipeline = device.create_render_pipeline(&wgpu::RenderPipelineDescriptor {
        label: Some("shadow-cast"),
        layout: Some(&pipeline_layout),
        vertex: wgpu::VertexState {
            module: &shader,
            entry_point: Some("vs_shadow"),
            buffers: &[Some(wgpu::VertexBufferLayout {
                array_stride: std::mem::size_of::<Vertex>() as wgpu::BufferAddress,
                step_mode: wgpu::VertexStepMode::Vertex,
                // Position only. The normal and colour are in the buffer and are
                // simply not read — a depth pass has no use for either.
                attributes: &wgpu::vertex_attr_array![0 => Float32x3],
            })],
            compilation_options: Default::default(),
        },
        // No fragment stage at all: depth is written by the rasteriser, and a
        // pass that outputs nothing is the cheapest thing a GPU can be asked to
        // do with two million triangles.
        fragment: None,
        primitive: wgpu::PrimitiveState {
            topology: wgpu::PrimitiveTopology::TriangleList,
            strip_index_format: None,
            front_face: wgpu::FrontFace::Ccw,
            // Front faces only — see the module note. This single line is the
            // difference between a lit level and one entirely in shadow.
            cull_mode: Some(wgpu::Face::Back),
            polygon_mode: wgpu::PolygonMode::Fill,
            unclipped_depth: false,
            conservative: false,
        },
        depth_stencil: Some(wgpu::DepthStencilState {
            format: FORMAT,
            depth_write_enabled: Some(true),
            depth_compare: Some(wgpu::CompareFunction::Less),
            stencil: wgpu::StencilState::default(),
            // Slope-scaled, because these are large flat quads at every angle to
            // the sun. A constant bias big enough to stop acne on a floor lit
            // head-on detaches the shadow from the foot of every wall; scaling it
            // by the slope is what lets one number serve both.
            bias: wgpu::DepthBiasState {
                constant: 2,
                slope_scale: 2.5,
                clamp: 0.0,
            },
        }),
        multisample: wgpu::MultisampleState::default(),
        multiview_mask: None,
        cache: None,
    });

    let mut encoder = device.create_command_encoder(&wgpu::CommandEncoderDescriptor {
        label: Some("shadow-pass"),
    });
    {
        let mut pass = encoder.begin_render_pass(&wgpu::RenderPassDescriptor {
            label: Some("shadow"),
            color_attachments: &[],
            depth_stencil_attachment: Some(wgpu::RenderPassDepthStencilAttachment {
                view,
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
        if world_verts > 0 {
            pass.set_pipeline(&pipeline);
            pass.set_bind_group(0, &cast_group, &[]);
            pass.set_vertex_buffer(0, world.slice(..));
            pass.draw(0..world_verts, 0..1);
        }
    }
    queue.submit([encoder.finish()]);
}

/// Fit an orthographic light camera around the map.
///
/// Returns the matrix and the world size of one shadow texel, which the receiver
/// uses to offset along the normal — the offset has to be in the same units as
/// the error it is hiding, and that error is one texel wide.
fn fit(bounds: (Vec3, Vec3)) -> (Mat4, f32) {
    let (min, max) = bounds;
    let center = (min + max) * 0.5;
    // The radius of a sphere around the map, so the fit does not change as the
    // sun moves relative to the box — fitting the *box* to the light's axes
    // instead makes the shadow texel density depend on the map's orientation.
    let radius = ((max - min) * 0.5).length().max(1.0);

    let dir = SUN_DIR.normalize();
    let eye = center + dir * radius * 2.0;
    // The map is Y-up, so anything but Y is a degenerate basis only if the sun
    // is straight overhead — it is not, and the constant says so.
    let view = glam::camera::rh::view::look_at_mat4(eye, center, Vec3::Y);
    // `orthographic_rh` for wgpu's 0..1 depth range, not the -1..1 GL one: the
    // wrong one halves the usable depth precision and puts everything nearer
    // than the midpoint in front of the near plane.
    let proj = Mat4::orthographic_rh(-radius, radius, -radius, radius, 0.01, radius * 4.0);
    (proj * view, 2.0 * radius / SIZE as f32)
}

/// The axis-aligned bounds of a mesh, for fitting the light camera.
pub fn bounds_of(vertices: &[Vertex]) -> (Vec3, Vec3) {
    let mut min = Vec3::splat(f32::MAX);
    let mut max = Vec3::splat(f32::MIN);
    for v in vertices {
        let p = Vec3::from(v.position);
        min = min.min(p);
        max = max.max(p);
    }
    if min.x > max.x {
        // An empty mesh. A unit box rather than infinities, so the matrix that
        // comes out is merely useless rather than full of NaN — which would
        // propagate into every shadow lookup in the frame.
        return (Vec3::ZERO, Vec3::ONE);
    }
    (min, max)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn vertex(position: [f32; 3]) -> Vertex {
        Vertex {
            position,
            normal: [0.0, 1.0, 0.0],
            color: [1.0, 1.0, 1.0],
        }
    }

    #[test]
    fn bounds_cover_every_vertex() {
        let verts = [
            vertex([-3.0, 1.0, 4.0]),
            vertex([7.0, -2.0, 0.0]),
            vertex([0.0, 5.0, -6.0]),
        ];
        let (min, max) = bounds_of(&verts);
        assert_eq!(min, Vec3::new(-3.0, -2.0, -6.0));
        assert_eq!(max, Vec3::new(7.0, 5.0, 4.0));
    }

    #[test]
    fn an_empty_mesh_yields_a_usable_box_rather_than_infinities() {
        // A degenerate matrix here does not fail loudly — it fills every shadow
        // lookup in the frame with NaN, which reads as the world flickering.
        let (min, max) = bounds_of(&[]);
        assert!(min.is_finite() && max.is_finite());
        let (matrix, texel) = fit((min, max));
        assert!(matrix.to_cols_array().iter().all(|f| f.is_finite()));
        assert!(texel > 0.0);
    }

    #[test]
    fn the_whole_map_falls_inside_the_light_frustum() {
        // If a corner of the map lands outside, that corner casts nothing and is
        // lit when it should be shadowed — a hole in the shadowing that only
        // appears on large maps.
        let (min, max) = (Vec3::new(0.0, 0.0, 0.0), Vec3::new(256.0, 40.0, 256.0));
        let (matrix, _) = fit((min, max));
        for i in 0..8 {
            let corner = Vec3::new(
                if i & 1 == 0 { min.x } else { max.x },
                if i & 2 == 0 { min.y } else { max.y },
                if i & 4 == 0 { min.z } else { max.z },
            );
            let clip = matrix * corner.extend(1.0);
            assert!(
                clip.x.abs() <= clip.w + 1e-3 && clip.y.abs() <= clip.w + 1e-3,
                "corner {corner} is outside the light frustum"
            );
            assert!(
                (0.0..=clip.w + 1e-3).contains(&clip.z),
                "corner {corner} is outside the light's depth range"
            );
        }
    }

    #[test]
    fn the_sun_matches_the_shader() {
        // The shadow is cast from this vector and the Lambert term is computed
        // from the copy in `lighting.wgsl.inc`. If they drift, surfaces are lit
        // from one side and shadowed from the other.
        let shader = include_str!("lighting.wgsl.inc");
        let line = shader
            .lines()
            .find(|l| l.contains("const SUN_DIR"))
            .expect("SUN_DIR should be declared in the shared lighting");
        for component in [SUN_DIR.x, SUN_DIR.y, SUN_DIR.z] {
            assert!(
                line.contains(&format!("{component}")),
                "SUN_DIR component {component} is not in `{line}`"
            );
        }
    }
}
