//! A map's light rig, sky, fog and point lights — decoded for the GPU.
//!
//! The numbers are the server's (`backend/modules/hassault/atmosphere.py`),
//! resolved there so this client and the browser draw one set of them. They used
//! to be WGSL constants in `lighting.wgsl.inc`, which lit every map identically.
//!
//! What lives here:
//!
//! - [`Atmosphere`], the served block, plus the two defaults a node older than
//!   the field implies (`CUBE`, `GLTF`) — the server's own defaults, pinned
//!   against it and against the browser in `tests/browser_parity.rs`.
//! - [`Lighting`], the map's rig and lights in world space, which builds the
//!   per-frame [`LightsUniform`] — nearest lights first, up to the quality cap.
//! - [`camera_layout`], the one definition of group 0: the camera at binding 0
//!   and the lights at binding 1. Every pipeline that shades uses it, which is
//!   what stops a pass being lit by a different rig from the room it is in.

use bytemuck::{Pod, Zeroable};
use glam::Vec3;
use serde::Deserialize;

use crate::api::MapInfo;

/// The most point lights a frame shades. The uniform is sized for this many;
/// a quality level asks for as many or fewer.
pub const MAX_POINT_LIGHTS: usize = 64;

/// `models.Atmosphere`. Colours are sRGB hex; directions are y-up and point
/// toward the light.
#[derive(Debug, Clone, Copy, PartialEq, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Atmosphere {
    pub sky_zenith: u32,
    pub sky_horizon: u32,
    pub hemi_sky: u32,
    pub hemi_ground: u32,
    pub hemi_intensity: f32,
    pub sun_color: u32,
    pub sun_intensity: f32,
    pub sun_dir: [f32; 3],
    pub fill_color: u32,
    pub fill_intensity: f32,
    pub fill_dir: [f32; 3],
    pub fog_color: u32,
    pub fog_density: f32,
    pub exposure: f32,
    pub sun_disc: bool,
}

impl Atmosphere {
    /// `atmosphere.CUBE_DEFAULT`: the rig the browser always lit cube maps with.
    pub const CUBE: Atmosphere = Atmosphere {
        sky_zenith: 0x11161f,
        sky_horizon: 0x11161f,
        hemi_sky: 0xbfd4ff,
        hemi_ground: 0x33302c,
        hemi_intensity: 1.55,
        sun_color: 0xfff2dd,
        sun_intensity: 1.75,
        sun_dir: [0.55, 0.82, 0.36],
        fill_color: 0x9fb6ff,
        fill_intensity: 0.45,
        fill_dir: [-0.5, 0.35, -0.7],
        fog_color: 0x11161f,
        fog_density: 0.0055,
        exposure: 1.15,
        sun_disc: false,
    };

    /// `atmosphere.GLTF_DEFAULT`: daylight, for a modelled map open to the sky.
    pub const GLTF: Atmosphere = Atmosphere {
        sky_zenith: 0x3f74c8,
        sky_horizon: 0x9cbde8,
        fog_color: 0x9cbde8,
        fog_density: 0.001,
        sun_disc: true,
        ..Atmosphere::CUBE
    };

    /// What to draw this map with: the served block, or — from a node that
    /// predates it — the default for the map's format, which is exactly what
    /// that node would have served.
    pub fn for_map(info: &MapInfo) -> Atmosphere {
        info.atmosphere
            .unwrap_or(if info.format.as_deref() == Some("gltf") {
                Atmosphere::GLTF
            } else {
                Atmosphere::CUBE
            })
    }

    pub fn sun_direction(&self) -> Vec3 {
        Vec3::from(self.sun_dir).try_normalize().unwrap_or(Vec3::Y)
    }
}

/// `models.MapLight`, in **map** coordinates: z is up.
#[derive(Debug, Clone, Copy, PartialEq, Deserialize, Default)]
pub struct MapLight {
    pub x: f32,
    pub y: f32,
    pub z: f32,
    pub radius: f32,
    pub color: u32,
    pub intensity: f32,
}

/// An sRGB hex colour as linear channels — how three decodes a `Color`.
pub fn srgb_hex_to_linear(hex: u32) -> [f32; 3] {
    let channel = |shift: u32| {
        let c = ((hex >> shift) & 0xff) as f32 / 255.0;
        if c <= 0.04045 {
            c / 12.92
        } else {
            ((c + 0.055) / 1.055).powf(2.4)
        }
    };
    [channel(16), channel(8), channel(0)]
}

/// A light's intensity in candela: `intensity × radius / 2`.
///
/// Mirrored by `candela` in `atmosphere.ts`. **Linear in the radius**, which is
/// the part that matters: the first convention was `(r/2)^2`, "fully bright at
/// half the radius", and under inverse-square falloff that put a 32-cube lamp at
/// 16× the sun on the wall it hung beside — every lamp a white blowout. A wider
/// light still reaches further and burns brighter, only not quadratically so.
pub fn candela(light: &MapLight) -> f32 {
    light.intensity * light.radius * 0.5
}

/// three's `getDistanceAttenuation` with `decay = 2`, for the tests: the shader
/// carries its own copy, `point_attenuation`, and a test holds the two together.
pub fn point_attenuation(distance: f32, range: f32) -> f32 {
    let falloff = 1.0 / (distance * distance).max(0.01);
    let ratio = distance / range;
    let window = (1.0 - ratio.powi(4)).clamp(0.0, 1.0);
    falloff * window * window
}

#[repr(C)]
#[derive(Copy, Clone, Debug, Pod, Zeroable)]
pub struct PointLightGpu {
    pub position_range: [f32; 4],
    pub color: [f32; 4],
}

/// `struct Lights` in `lighting.wgsl.inc`, byte for byte.
#[repr(C)]
#[derive(Copy, Clone, Debug, Pod, Zeroable)]
pub struct LightsUniform {
    pub hemi_sky: [f32; 4],
    pub hemi_ground: [f32; 4],
    pub sun_dir: [f32; 4],
    pub sun_color: [f32; 4],
    pub fill_dir: [f32; 4],
    pub fill_color: [f32; 4],
    pub fog_color: [f32; 4],
    pub sky_zenith: [f32; 4],
    pub sky_horizon: [f32; 4],
    pub eye: [f32; 4],
    pub point: [PointLightGpu; MAX_POINT_LIGHTS],
}

/// One point light, already in world space and in candela.
#[derive(Debug, Clone, Copy)]
struct WorldLight {
    position: Vec3,
    range: f32,
    color: [f32; 3],
}

/// A map's rig and lights, ready to become a uniform each frame.
#[derive(Debug, Clone)]
pub struct Lighting {
    pub atmosphere: Atmosphere,
    lights: Vec<WorldLight>,
}

impl Default for Lighting {
    fn default() -> Lighting {
        Lighting::new(Atmosphere::CUBE, &[])
    }
}

impl Lighting {
    pub fn new(atmosphere: Atmosphere, lights: &[MapLight]) -> Lighting {
        let lights = lights
            .iter()
            .filter(|l| l.radius > 0.0 && l.intensity > 0.0)
            .map(|l| {
                let c = srgb_hex_to_linear(l.color);
                let k = candela(l);
                WorldLight {
                    // Map (x, y, z-up) into the world's y-up, as every other
                    // placement does: `three.x = x, three.y = z, three.z = y`.
                    position: Vec3::new(l.x, l.z, l.y),
                    range: l.radius,
                    color: [c[0] * k, c[1] * k, c[2] * k],
                }
            })
            .collect();
        Lighting { atmosphere, lights }
    }

    pub fn for_map(info: &MapInfo) -> Lighting {
        Lighting::new(Atmosphere::for_map(info), &info.lights)
    }

    pub fn light_count(&self) -> usize {
        self.lights.len()
    }

    /// This frame's uniform.
    ///
    /// `max_lights` is the quality cap; the lights kept are the ones whose reach
    /// comes **nearest the eye** — distance less range — so a big lamp across
    /// the room outranks a small one just behind you that lights nothing you
    /// can see. `brightness` multiplies the map's exposure.
    pub fn uniform(&self, eye: Vec3, max_lights: usize, brightness: f32) -> LightsUniform {
        let a = &self.atmosphere;
        let with = |rgb: [f32; 3], w: f32| [rgb[0], rgb[1], rgb[2], w];
        let dir = |d: [f32; 3]| {
            let v = Vec3::from(d).try_normalize().unwrap_or(Vec3::Y);
            [v.x, v.y, v.z, 0.0]
        };

        let mut order: Vec<(f32, usize)> = self
            .lights
            .iter()
            .enumerate()
            .map(|(i, l)| (l.position.distance(eye) - l.range, i))
            .collect();
        let keep = max_lights.min(MAX_POINT_LIGHTS).min(order.len());
        if keep < order.len() {
            order.select_nth_unstable_by(keep, |p, q| p.0.total_cmp(&q.0));
        }
        let mut point = [PointLightGpu::zeroed(); MAX_POINT_LIGHTS];
        for (slot, &(_, i)) in order.iter().take(keep).enumerate() {
            let l = &self.lights[i];
            point[slot] = PointLightGpu {
                position_range: [l.position.x, l.position.y, l.position.z, l.range],
                color: with(l.color, 0.0),
            };
        }

        LightsUniform {
            hemi_sky: with(srgb_hex_to_linear(a.hemi_sky), a.hemi_intensity),
            hemi_ground: with(srgb_hex_to_linear(a.hemi_ground), 0.0),
            sun_dir: dir(a.sun_dir),
            sun_color: with(srgb_hex_to_linear(a.sun_color), a.sun_intensity),
            fill_dir: dir(a.fill_dir),
            fill_color: with(srgb_hex_to_linear(a.fill_color), a.fill_intensity),
            fog_color: with(srgb_hex_to_linear(a.fog_color), 0.0),
            sky_zenith: with(
                srgb_hex_to_linear(a.sky_zenith),
                if a.sun_disc { 1.0 } else { 0.0 },
            ),
            sky_horizon: with(srgb_hex_to_linear(a.sky_horizon), a.exposure * brightness),
            eye: [eye.x, eye.y, eye.z, keep as f32],
            point,
        }
    }
}

/// Group 0 for every pass that shades: the camera at binding 0, the lights at
/// binding 1.
///
/// One function so the renderer, the characters, the props and the preview
/// examples cannot build different ones. A layout missing binding 1 is a
/// validation error at pipeline creation — for a shader that reads `lights`,
/// which since this module is every one of them.
pub fn camera_layout(device: &wgpu::Device) -> wgpu::BindGroupLayout {
    let entry = |binding: u32| wgpu::BindGroupLayoutEntry {
        binding,
        // **Both stages.** The matrix is the vertex shader's and the quality
        // parameters are the fragment shader's, and they share a buffer. A
        // `VERTEX`-only visibility here is a validation error at pipeline
        // creation, not a wrong-looking frame.
        visibility: wgpu::ShaderStages::VERTEX_FRAGMENT,
        ty: wgpu::BindingType::Buffer {
            ty: wgpu::BufferBindingType::Uniform,
            has_dynamic_offset: false,
            min_binding_size: None,
        },
        count: None,
    };
    device.create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
        label: Some("camera-layout"),
        entries: &[entry(0), entry(1)],
    })
}

/// A uniform buffer holding the default rig, for whoever needs a binding 1
/// before a map has been decided — the previews, and the tests.
pub fn default_lights_buffer(device: &wgpu::Device) -> wgpu::Buffer {
    use wgpu::util::DeviceExt;
    device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
        label: Some("lights"),
        contents: bytemuck::cast_slice(&[Lighting::default().uniform(Vec3::ZERO, 0, 1.0)]),
        usage: wgpu::BufferUsages::UNIFORM | wgpu::BufferUsages::COPY_DST,
    })
}

/// A group-0 bind group: a camera-shaped uniform at 0, the lights at 1.
pub fn camera_bind_group(
    device: &wgpu::Device,
    layout: &wgpu::BindGroupLayout,
    camera: &wgpu::Buffer,
    lights: &wgpu::Buffer,
    label: &str,
) -> wgpu::BindGroup {
    device.create_bind_group(&wgpu::BindGroupDescriptor {
        label: Some(label),
        layout,
        entries: &[
            wgpu::BindGroupEntry {
                binding: 0,
                resource: camera.as_entire_binding(),
            },
            wgpu::BindGroupEntry {
                binding: 1,
                resource: lights.as_entire_binding(),
            },
        ],
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_uniform_is_laid_out_the_way_the_shader_reads_it() {
        // Ten vec4s, then 64 lights of two vec4s each. A uniform struct's
        // alignment is 16, and every field here is a whole vec4 so no padding
        // can hide between them.
        assert_eq!(std::mem::size_of::<PointLightGpu>(), 32);
        assert_eq!(
            std::mem::size_of::<LightsUniform>(),
            10 * 16 + MAX_POINT_LIGHTS * 32
        );
    }

    #[test]
    fn a_node_without_the_field_gets_its_formats_default() {
        let mut info = MapInfo::default();
        assert_eq!(Atmosphere::for_map(&info), Atmosphere::CUBE);
        info.format = Some("gltf".into());
        assert_eq!(Atmosphere::for_map(&info), Atmosphere::GLTF);
    }

    #[test]
    fn map_lights_land_in_world_space_z_up_to_y_up() {
        let lighting = Lighting::new(
            Atmosphere::CUBE,
            &[MapLight {
                x: 10.0,
                y: 20.0,
                z: 5.0,
                radius: 8.0,
                color: 0xffffff,
                intensity: 1.0,
            }],
        );
        let u = lighting.uniform(Vec3::ZERO, 8, 1.0);
        assert_eq!(u.eye[3], 1.0);
        assert_eq!(u.point[0].position_range, [10.0, 5.0, 20.0, 8.0]);
        // White at 1.0: radius / 2 candela.
        assert!((u.point[0].color[0] - 4.0).abs() < 1e-4);
    }

    #[test]
    fn the_cap_keeps_the_lights_nearest_the_eye() {
        let lights: Vec<MapLight> = (0..10)
            .map(|i| MapLight {
                x: i as f32 * 100.0,
                radius: 10.0,
                color: 0xffffff,
                intensity: 1.0,
                ..MapLight::default()
            })
            .collect();
        let u = Lighting::new(Atmosphere::CUBE, &lights).uniform(Vec3::new(0.0, 0.0, 0.0), 3, 1.0);
        assert_eq!(u.eye[3], 3.0);
        let mut kept: Vec<f32> = (0..3).map(|i| u.point[i].position_range[0]).collect();
        kept.sort_by(f32::total_cmp);
        assert_eq!(kept, vec![0.0, 100.0, 200.0]);
    }

    #[test]
    fn a_cap_of_zero_shades_no_point_lights() {
        let lights = [MapLight {
            radius: 10.0,
            color: 0xffffff,
            intensity: 1.0,
            ..MapLight::default()
        }];
        let u = Lighting::new(Atmosphere::CUBE, &lights).uniform(Vec3::ZERO, 0, 1.0);
        assert_eq!(u.eye[3], 0.0);
    }

    #[test]
    fn attenuation_is_inverse_square_windowed_to_the_range() {
        assert!((point_attenuation(2.0, 1000.0) - 0.25).abs() < 1e-4);
        assert_eq!(point_attenuation(10.0, 10.0), 0.0);
        assert_eq!(point_attenuation(12.0, 10.0), 0.0);
        // At half the range the window is (1 - 1/16)^2.
        let w = (1.0f32 - 1.0 / 16.0).powi(2);
        assert!((point_attenuation(5.0, 10.0) - w / 25.0).abs() < 1e-6);
    }

    #[test]
    fn the_shader_windows_the_light_the_same_way() {
        let wgsl = include_str!("lighting.wgsl.inc");
        let body = &wgsl[wgsl
            .find("fn point_attenuation")
            .expect("the shader's copy")..];
        let body = &body[..body.find('}').unwrap()];
        for needle in [
            "1.0 / max(distance * distance, 0.01)",
            "ratio * ratio * ratio * ratio",
            "falloff * window * window",
        ] {
            assert!(body.contains(needle), "shader lost `{needle}`");
        }
    }

    #[test]
    fn the_sun_the_shadow_is_cast_from_is_the_sun_the_shader_lights_with() {
        // Both come from the one `Atmosphere`; this pins that the uniform's
        // direction is the normalised served one, which is what `shadow.rs`
        // builds its light camera from.
        let u = Lighting::new(Atmosphere::CUBE, &[]).uniform(Vec3::ZERO, 0, 1.0);
        let d = Atmosphere::CUBE.sun_direction();
        assert!((u.sun_dir[0] - d.x).abs() < 1e-6);
        assert!((u.sun_dir[1] - d.y).abs() < 1e-6);
        assert!((u.sun_dir[2] - d.z).abs() < 1e-6);
    }
}
