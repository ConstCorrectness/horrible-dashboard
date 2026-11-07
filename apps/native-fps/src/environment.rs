//! The light a modelled map's surfaces reflect and take ambient from — the
//! native half of `environment.ts`, which is the reference.
//!
//! **What it is made of.** Above the horizon the served sky, the dome's own
//! horizon-to-zenith gradient (`sky_radiance` in `lighting.wgsl.inc`); below it
//! the same shape down to the served hemisphere *ground* colour. No sun disc:
//! the sun is an analytic light with its own GGX highlight, and in the
//! environment too it would light every glossy surface twice. A function of the
//! direction's height only, so it is the same whichever way a map faces.
//!
//! **How it is stored.** A half-float cube with `LEVELS` mips, each one the
//! function prefiltered with GGX for the roughness `level_roughness` names —
//! what three's `PMREMGenerator` builds in the browser, here computed on the CPU
//! at map load, because the source is an analytic function rather than a
//! picture: importance-sampling it is exact where sampling a texture would
//! alias. `world_ibl` in `shader.wgsl` reads it the way three reads its CUBE_UV
//! map: the reflected ray at the surface's roughness for the specular, the
//! normal at roughness 1, times pi, for the irradiance.
//!
//! The DFG table three's split-sum reads is `dfg_lut.rs`, three's own data.

use crate::atmosphere::{srgb_hex_to_linear, Atmosphere};

/// The cube's base face, in texels. The sharpest level is the sky as a mirror
/// sees it; 128 is plenty for a gradient with no detail in it.
pub const SIZE: u32 = 128;
/// Mip levels, roughness 0 at the base to 1 at the last (4 x 4). `shader.wgsl`'s
/// `ENV_LEVELS` must agree; `browser_parity.rs`-style pin in the tests below.
pub const LEVELS: u32 = 6;
/// GGX samples per texel. The source is smooth and the sample set is the same
/// for every texel, so the error is a smooth bias, not noise.
const SAMPLES: u32 = 256;

/// The roughness a mip level is prefiltered for.
pub fn level_roughness(level: u32) -> f32 {
    level as f32 / (LEVELS - 1) as f32
}

/// The three colours the environment is made of, decoded once.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Palette {
    zenith: [f32; 3],
    horizon: [f32; 3],
    ground: [f32; 3],
}

impl Palette {
    pub fn of(a: &Atmosphere) -> Palette {
        Palette {
            zenith: srgb_hex_to_linear(a.sky_zenith),
            horizon: srgb_hex_to_linear(a.sky_horizon),
            ground: srgb_hex_to_linear(a.hemi_ground),
        }
    }

    /// `environmentRadiance` in `environment.ts`: the radiance looking along a
    /// direction whose height is `y`. Linear, before exposure.
    pub fn radiance(&self, y: f32) -> [f32; 3] {
        let far = if y >= 0.0 { self.zenith } else { self.ground };
        let t = y.abs().min(1.0).sqrt();
        [0, 1, 2].map(|i| self.horizon[i] + (far[i] - self.horizon[i]) * t)
    }
}

/// The direction through texel (`x`, `y`) of cube face `face`, as wgpu (and
/// Vulkan, D3D and Metal, which share GL's table) address a cube: faces +X, -X,
/// +Y, -Y, +Z, -Z, texel rows running down the face.
pub fn face_direction(face: u32, x: u32, y: u32, size: u32) -> [f32; 3] {
    let s = 2.0 * (x as f32 + 0.5) / size as f32 - 1.0;
    let t = 2.0 * (y as f32 + 0.5) / size as f32 - 1.0;
    let d = match face {
        0 => [1.0, -t, -s],
        1 => [-1.0, -t, s],
        2 => [s, 1.0, t],
        3 => [s, -1.0, -t],
        4 => [s, -t, 1.0],
        _ => [-s, -t, -1.0],
    };
    normalize(d)
}

fn normalize(d: [f32; 3]) -> [f32; 3] {
    let l = (d[0] * d[0] + d[1] * d[1] + d[2] * d[2]).sqrt().max(1e-12);
    d.map(|c| c / l)
}

fn hammersley(i: u32, n: u32) -> (f32, f32) {
    (i as f32 / n as f32, i.reverse_bits() as f32 * 2.328_306_4e-10)
}

/// The radiance a surface of `roughness` facing `n` reflects back along `n`
/// (the split-sum's N = V = R): GGX importance sampling, weighted by N.L.
/// `alpha` is roughness squared, three's convention.
pub fn prefiltered(palette: &Palette, n: [f32; 3], roughness: f32, samples: u32) -> [f32; 3] {
    if roughness <= 0.0 {
        return palette.radiance(n[1]);
    }
    let alpha = roughness * roughness;
    let up = if n[1].abs() < 0.999 { [0.0, 1.0, 0.0] } else { [1.0, 0.0, 0.0] };
    let tangent = normalize(cross(up, n));
    let bitangent = cross(n, tangent);
    let mut sum = [0.0f32; 3];
    let mut weight = 0.0f32;
    for i in 0..samples {
        let (u, v) = hammersley(i, samples);
        let phi = 2.0 * std::f32::consts::PI * u;
        let cos_theta = ((1.0 - v) / (1.0 + (alpha * alpha - 1.0) * v)).sqrt();
        let sin_theta = (1.0 - cos_theta * cos_theta).max(0.0).sqrt();
        let (hx, hy, hz) = (sin_theta * phi.cos(), sin_theta * phi.sin(), cos_theta);
        let h = [0, 1, 2].map(|k| tangent[k] * hx + bitangent[k] * hy + n[k] * hz);
        let vh = n[0] * h[0] + n[1] * h[1] + n[2] * h[2];
        let l = [0, 1, 2].map(|k| 2.0 * vh * h[k] - n[k]);
        let nl = n[0] * l[0] + n[1] * l[1] + n[2] * l[2];
        if nl > 0.0 {
            let c = palette.radiance(l[1]);
            for k in 0..3 {
                sum[k] += c[k] * nl;
            }
            weight += nl;
        }
    }
    sum.map(|c| c / weight.max(1e-6))
}

fn cross(a: [f32; 3], b: [f32; 3]) -> [f32; 3] {
    [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]
}

/// Every level of the cube as RGBA16F texels: level, then face, then rows.
pub fn levels(palette: &Palette, size: u32, levels: u32, samples: u32) -> Vec<Vec<u16>> {
    (0..levels)
        .map(|level| {
            let edge = (size >> level).max(1);
            let roughness = level as f32 / (levels - 1).max(1) as f32;
            let mut texels = Vec::with_capacity((6 * edge * edge * 4) as usize);
            for face in 0..6 {
                for y in 0..edge {
                    for x in 0..edge {
                        let c = prefiltered(palette, face_direction(face, x, y, edge), roughness, samples);
                        for v in [c[0], c[1], c[2], 1.0] {
                            texels.push(half::f16::from_f32(v).to_bits());
                        }
                    }
                }
            }
            texels
        })
        .collect()
}

/// The environment cube and the DFG table, on the GPU.
pub struct Environment {
    cube: wgpu::Texture,
    pub cube_view: wgpu::TextureView,
    _lut: wgpu::Texture,
    pub lut_view: wgpu::TextureView,
    pub sampler: wgpu::Sampler,
    palette: Option<Palette>,
}

impl Environment {
    pub fn new(device: &wgpu::Device, queue: &wgpu::Queue, atmosphere: &Atmosphere) -> Environment {
        let cube = device.create_texture(&wgpu::TextureDescriptor {
            label: Some("environment-cube"),
            size: wgpu::Extent3d {
                width: SIZE,
                height: SIZE,
                depth_or_array_layers: 6,
            },
            mip_level_count: LEVELS,
            sample_count: 1,
            dimension: wgpu::TextureDimension::D2,
            format: wgpu::TextureFormat::Rgba16Float,
            usage: wgpu::TextureUsages::TEXTURE_BINDING | wgpu::TextureUsages::COPY_DST,
            view_formats: &[],
        });
        let cube_view = cube.create_view(&wgpu::TextureViewDescriptor {
            label: Some("environment-cube-view"),
            dimension: Some(wgpu::TextureViewDimension::Cube),
            ..Default::default()
        });
        let lut = device.create_texture(&wgpu::TextureDescriptor {
            label: Some("dfg-lut"),
            size: wgpu::Extent3d {
                width: crate::dfg_lut::SIZE,
                height: crate::dfg_lut::SIZE,
                depth_or_array_layers: 1,
            },
            mip_level_count: 1,
            sample_count: 1,
            dimension: wgpu::TextureDimension::D2,
            format: wgpu::TextureFormat::Rg16Float,
            usage: wgpu::TextureUsages::TEXTURE_BINDING | wgpu::TextureUsages::COPY_DST,
            view_formats: &[],
        });
        queue.write_texture(
            wgpu::TexelCopyTextureInfo {
                texture: &lut,
                mip_level: 0,
                origin: wgpu::Origin3d::ZERO,
                aspect: wgpu::TextureAspect::All,
            },
            bytemuck::cast_slice(&crate::dfg_lut::DATA),
            wgpu::TexelCopyBufferLayout {
                offset: 0,
                bytes_per_row: Some(crate::dfg_lut::SIZE * 4),
                rows_per_image: Some(crate::dfg_lut::SIZE),
            },
            wgpu::Extent3d {
                width: crate::dfg_lut::SIZE,
                height: crate::dfg_lut::SIZE,
                depth_or_array_layers: 1,
            },
        );
        let lut_view = lut.create_view(&wgpu::TextureViewDescriptor::default());
        // Bilinear with clamped edges for both: three's `dfgLUT` sampler, and
        // trilinear across the cube's roughness levels.
        let sampler = device.create_sampler(&wgpu::SamplerDescriptor {
            label: Some("environment-sampler"),
            address_mode_u: wgpu::AddressMode::ClampToEdge,
            address_mode_v: wgpu::AddressMode::ClampToEdge,
            address_mode_w: wgpu::AddressMode::ClampToEdge,
            mag_filter: wgpu::FilterMode::Linear,
            min_filter: wgpu::FilterMode::Linear,
            mipmap_filter: wgpu::MipmapFilterMode::Linear,
            ..Default::default()
        });
        let mut environment = Environment {
            cube,
            cube_view,
            _lut: lut,
            lut_view,
            sampler,
            palette: None,
        };
        environment.update(queue, atmosphere);
        environment
    }

    /// Rebuild the cube for a map's atmosphere. A new map with the same sky and
    /// ground (most of them) costs nothing.
    pub fn update(&mut self, queue: &wgpu::Queue, atmosphere: &Atmosphere) {
        let palette = Palette::of(atmosphere);
        if self.palette == Some(palette) {
            return;
        }
        self.palette = Some(palette);
        for (level, texels) in levels(&palette, SIZE, LEVELS, SAMPLES).iter().enumerate() {
            let edge = (SIZE >> level).max(1);
            queue.write_texture(
                wgpu::TexelCopyTextureInfo {
                    texture: &self.cube,
                    mip_level: level as u32,
                    origin: wgpu::Origin3d::ZERO,
                    aspect: wgpu::TextureAspect::All,
                },
                bytemuck::cast_slice(texels),
                wgpu::TexelCopyBufferLayout {
                    offset: 0,
                    bytes_per_row: Some(edge * 8),
                    rows_per_image: Some(edge),
                },
                wgpu::Extent3d {
                    width: edge,
                    height: edge,
                    depth_or_array_layers: 6,
                },
            );
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_radiance_is_the_sky_above_and_the_ground_below() {
        let p = Palette::of(&Atmosphere::GLTF);
        let near = |a: [f32; 3], b: [f32; 3]| (0..3).all(|i| (a[i] - b[i]).abs() < 1e-5);
        assert!(near(p.radiance(1.0), srgb_hex_to_linear(Atmosphere::GLTF.sky_zenith)));
        assert!(near(p.radiance(0.0), srgb_hex_to_linear(Atmosphere::GLTF.sky_horizon)));
        assert!(near(p.radiance(-1.0), srgb_hex_to_linear(Atmosphere::GLTF.hemi_ground)));
        // The square root: a quarter of the way up is half way to the zenith.
        let quarter = p.radiance(0.25);
        let (h, z) = (srgb_hex_to_linear(0x9cbde8), srgb_hex_to_linear(0x3f74c8));
        assert!((quarter[2] - (h[2] + (z[2] - h[2]) * 0.5)).abs() < 1e-5);
    }

    #[test]
    fn the_cube_faces_point_where_wgpu_reads_them() {
        // +Y is up, its centre straight up; the side faces' top rows look up.
        let up = face_direction(2, 0, 0, 1);
        assert!((up[1] - 1.0).abs() < 1e-6, "{up:?}");
        assert!(face_direction(3, 0, 0, 1)[1] < -0.99);
        for face in [0, 1, 4, 5] {
            assert!(face_direction(face, 3, 0, 8)[1] > 0.5, "face {face} top row");
            assert!(face_direction(face, 3, 7, 8)[1] < -0.5, "face {face} bottom row");
        }
    }

    #[test]
    fn rougher_levels_are_blurrier_and_keep_the_energy() {
        let p = Palette::of(&Atmosphere::GLTF);
        let up = [0.0, 1.0, 0.0];
        let sharp = prefiltered(&p, up, 0.0, 256);
        let rough = prefiltered(&p, up, 1.0, 256);
        // Straight up a mirror sees the zenith; a rough surface sees the horizon
        // too, and the horizon is the brighter of the two.
        assert!(rough[0] > sharp[0], "{sharp:?} {rough:?}");
        // Looking at the horizon, a rough surface averages sky and ground.
        let side = prefiltered(&p, [1.0, 0.0, 0.0], 1.0, 256);
        let horizon = p.radiance(0.0);
        assert!(side[1] < horizon[1], "{side:?}");
        // Averages stay between the palette's extremes.
        for c in [sharp, rough, side] {
            assert!(c.iter().all(|v| v.is_finite() && *v >= 0.0 && *v <= 1.0), "{c:?}");
        }
    }

    #[test]
    fn every_level_is_its_size_and_halves() {
        let p = Palette::of(&Atmosphere::GLTF);
        let all = levels(&p, 16, 3, 8);
        assert_eq!(all.len(), 3);
        assert_eq!(all[0].len(), 6 * 16 * 16 * 4);
        assert_eq!(all[2].len(), 6 * 4 * 4 * 4);
        assert_eq!(level_roughness(0), 0.0);
        assert_eq!(level_roughness(LEVELS - 1), 1.0);
        assert_eq!(SIZE >> (LEVELS - 1), 4, "the roughest level is 4 x 4");
    }

    #[test]
    fn the_shader_reads_as_many_levels_as_there_are() {
        let wgsl = include_str!("shader.wgsl");
        assert!(
            wgsl.contains(&format!("const ENV_LEVELS: f32 = {}.0;", LEVELS)),
            "shader.wgsl's ENV_LEVELS no longer matches environment::LEVELS"
        );
    }
}
