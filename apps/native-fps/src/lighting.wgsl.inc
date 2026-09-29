// Shared lighting, tone mapping and colour space.
//
// Textually included into both `shader.wgsl` (the world) and `skin.wgsl` (the
// operator) at build time, because WGSL has no `#include` and the one thing that
// must never drift between them is how they are lit: a character lit differently
// from the room it stands in reads as a sprite pasted over the world.
//
// These are the browser client's lights, ported from `HorribleAssaultPanel.tsx`.
// The two clients render the same maps, and until this landed they did not look
// like the same game: the native side lit everything with a single hardcoded
// direction over a flat 0.55 ambient floor and no tone curve, which gave every
// surface in every map nearly the same value. The result was a pale, flat world
// the browser never had.
//
// It was **not** a colour-space bug, which is the first thing it looks like.
// Both clients write linear values to an sRGB target and let the GPU encode on
// write, which is correct and always was. What was missing was shape: a light
// rig with direction to it, Lambert's own normalisation, a tone curve with a
// shoulder, and the surface grain to resolve any of it against.

// --- colour space ---------------------------------------------------------
//
// Used for the values that genuinely are authored as sRGB — a hex colour someone
// picked, like the operator's team wash. Those must be decoded before they are
// shaded, because shading happens in linear light.
//
// The world's vertex tints are **not** among them: they are raw floats the
// browser hands three as a `BufferAttribute`, which three treats as already
// linear. Decoding them here would darken and saturate every map relative to
// the browser rendering the same map. Which values are sRGB is a question about
// where each value came from, not about what type it has.
fn srgb_to_linear(c: vec3<f32>) -> vec3<f32> {
    let cutoff = c <= vec3<f32>(0.04045);
    let low = c / 12.92;
    let high = pow((c + vec3<f32>(0.055)) / 1.055, vec3<f32>(2.4));
    return select(high, low, cutoff);
}

// --- the lights -----------------------------------------------------------
//
// **Served, not constant.** These used to be the browser's hex values typed in
// here as WGSL constants, which lit every map the same way — a desert at noon
// and an office at night under one sun. Each map now carries an `atmosphere`
// (`backend/modules/hassault/atmosphere.py`), resolved on the server so neither
// client holds a default, and `atmosphere.rs` decodes it into this uniform.
// A map with none gets exactly the old rig, so nothing changed for it.
//
// Bound at group 0, binding 1 — beside the camera, in every pass that uses the
// camera layout — because the downlevel limit is four bind groups and the world
// pass uses all four. See `atmosphere::camera_layout`.
//
// - Hemisphere: carries the ambient, and being a *gradient* rather than a
//   constant is what stops a surface facing away from every light reading as a
//   hole.
// - Sun: the one light that casts, and the one the shadow map is rendered from.
// - Fill: a cool light from behind at a fraction of the sun's strength. Without
//   it every surface facing away from the sun comes out the same value.
// - Point lights: the map's own, nearest the eye first, up to the quality's cap.
//   three's `PointLight` with `decay = 2` and `distance = radius`, so the falloff
//   below is `getDistanceAttenuation` exactly.
struct PointLight {
    // xyz world position, w the range (the light's radius).
    position_range: vec4<f32>,
    // rgb linear colour times intensity in candela; w unused.
    color: vec4<f32>,
};

struct Lights {
    hemi_sky: vec4<f32>,      // rgb linear, w intensity
    hemi_ground: vec4<f32>,   // rgb linear
    sun_dir: vec4<f32>,       // xyz unit, toward the sun
    sun_color: vec4<f32>,     // rgb linear, w intensity
    fill_dir: vec4<f32>,
    fill_color: vec4<f32>,    // rgb linear, w intensity
    fog_color: vec4<f32>,     // rgb linear
    sky_zenith: vec4<f32>,    // rgb linear, w 1 if the sun disc is drawn
    sky_horizon: vec4<f32>,   // rgb linear, w exposure
    eye: vec4<f32>,           // xyz world-space eye, w the number of point lights
    point: array<PointLight, 64>,
};

@group(0) @binding(1) var<uniform> lights: Lights;

/// three's `getDistanceAttenuation` for `decay = 2`: inverse square, windowed
/// to zero at `range` so a light has an edge rather than a tail that never ends.
fn point_attenuation(distance: f32, range: f32) -> f32 {
    let falloff = 1.0 / max(distance * distance, 0.01);
    let ratio = distance / range;
    let window = clamp(1.0 - ratio * ratio * ratio * ratio, 0.0, 1.0);
    return falloff * window * window;
}

/// The irradiance the map's point lights put on a surface.
fn point_irradiance(n: vec3<f32>, world_position: vec3<f32>) -> vec3<f32> {
    var sum = vec3<f32>(0.0);
    let count = u32(lights.eye.w);
    for (var i = 0u; i < count; i = i + 1u) {
        let light = lights.point[i];
        let to_light = light.position_range.xyz - world_position;
        let distance = length(to_light);
        if (distance >= light.position_range.w) {
            continue;
        }
        let l = to_light / max(distance, 1e-4);
        let ndotl = max(dot(n, l), 0.0);
        sum = sum + light.color.rgb * point_attenuation(distance, light.position_range.w) * ndotl;
    }
    return sum;
}

/// Lambert's BRDF normalisation.
///
/// three's `MeshLambertMaterial` shades as `albedo / PI * irradiance`, and
/// leaving the divide out makes every surface pi times too bright. That is not a
/// subtle exposure difference once there is a tone curve in front of it: ACES
/// compresses hard above 1.0, so the over-bright albedo lands in the shoulder,
/// the light's own colour starts to dominate the surface's, and a muted tint
/// comes out as saturated teal and hot pink. It looks like the *palette* is
/// wrong rather than the exposure.
const RECIPROCAL_PI: f32 = 0.31830987;

const POISSON_16: array<vec2<f32>, 16> = array<vec2<f32>, 16>(
    vec2<f32>(-0.94201624, -0.39906216),
    vec2<f32>(0.94558609, -0.76890725),
    vec2<f32>(-0.094184101, -0.92938870),
    vec2<f32>(0.34495938, 0.29387760),
    vec2<f32>(-0.91588581, 0.45771432),
    vec2<f32>(-0.81544232, -0.87912464),
    vec2<f32>(-0.38277543, 0.27676845),
    vec2<f32>(0.97484398, 0.75648379),
    vec2<f32>(0.44323325, -0.97511554),
    vec2<f32>(0.53742981, -0.47373420),
    vec2<f32>(-0.26496911, -0.41893023),
    vec2<f32>(0.79197514, 0.19090188),
    vec2<f32>(-0.24188840, 0.99706507),
    vec2<f32>(-0.81409955, 0.91437590),
    vec2<f32>(0.19984126, 0.78641367),
    vec2<f32>(0.14383161, -0.14100790)
);

/// How much of the sun reaches a point, 0 fully shadowed to 1 fully lit.
///
/// 16-tap Poisson disk PCF over a comparison sampler: provides cinematic soft
/// contact shadows with smooth penumbra transitions without grid artifacts.
fn sun_shadow(
    map: texture_depth_2d,
    map_sampler: sampler_comparison,
    light_position: vec4<f32>,
    normal: vec3<f32>,
    texel: f32,
    bias: f32,
    taps: f32,
) -> f32 {
    // Perspective divide, then into texture space.
    var ndc = light_position.xyz / light_position.w;
    var uv = vec2<f32>(ndc.x * 0.5 + 0.5, 0.5 - ndc.y * 0.5);

    if (ndc.z > 1.0 || ndc.z < 0.0 || uv.x < 0.0 || uv.x > 1.0 || uv.y < 0.0 || uv.y > 1.0) {
        return 1.0;
    }

    let ndotl = max(dot(normalize(normal), lights.sun_dir.xyz), 0.0);
    let slope = clamp(1.0 - ndotl, 0.0, 1.0);
    let depth = ndc.z - (bias + bias * 4.0 * slope);

    // The first `taps` points of the disk. The Poisson set is ordered so any
    // prefix still spreads round the centre, which is what lets a lower shadow
    // quality take fewer of them rather than a different kernel.
    let count = clamp(i32(taps), 1, 16);
    var sum = 0.0;
    let radius = texel * 1.5;
    for (var i = 0; i < count; i = i + 1) {
        let offset = POISSON_16[i] * radius;
        sum = sum + textureSampleCompare(map, map_sampler, uv + offset, depth);
    }
    return sum / f32(count);
}

/// Lambert shading with the three lights above. `albedo` must already be linear.
///
/// `detail` selects how much of the rig is used: at 0 the surface is returned
/// flat, which is the lowest quality level's *choice* rather than a cheaper
/// approximation of the same thing. At 1 the hemisphere and sun run; at 2 the
/// fill joins them.
///
/// `shadow` attenuates **only the sun**. The hemisphere is ambient — a surface in
/// shadow is still lit by the sky above it — and shadowing that too would make
/// every shadowed face uniformly black, which is the thing real shadows are
/// supposed to stop happening.
///
/// `world_position` is for the point lights, which are the only term here that
/// depends on where the surface is rather than which way it faces.
fn shade(
    albedo: vec3<f32>,
    normal: vec3<f32>,
    world_position: vec3<f32>,
    detail: f32,
    shadow: f32,
) -> vec3<f32> {
    if (detail < 1.0) {
        return albedo;
    }
    let n = normalize(normal);

    // three's hemisphere light: the sky above, the ground below, mixed by how
    // far the normal has tipped between them.
    let hemi = mix(lights.hemi_ground.rgb, lights.hemi_sky.rgb, n.y * 0.5 + 0.5) * lights.hemi_sky.w;

    var irradiance = hemi;
    irradiance = irradiance
        + lights.sun_color.rgb * lights.sun_color.w * max(dot(n, lights.sun_dir.xyz), 0.0) * shadow;
    if (detail >= 2.0) {
        irradiance = irradiance
            + lights.fill_color.rgb * lights.fill_color.w * max(dot(n, lights.fill_dir.xyz), 0.0);
    }
    irradiance = irradiance + point_irradiance(n, world_position);
    return albedo * irradiance * RECIPROCAL_PI;
}

// --- tone mapping ---------------------------------------------------------
//
// ACES rather than a clip, and it is not decoration: the sun plus a hemisphere
// light puts lit floors above 1.0, and clipping flattens everything past that
// into the same white — which is exactly where a bright surface loses the grain
// the detail texture exists to give it.
//
// Narkowicz's fit, which is what three's `ACESFilmicToneMapping` is. The
// exposure is the map's (1.15 unless it says otherwise, the browser's
// `toneMappingExposure`) times the player's brightness setting.
fn tonemap(color: vec3<f32>) -> vec3<f32> {
    let x = color * lights.sky_horizon.w;
    let a = 2.51;
    let b = 0.03;
    let c = 2.43;
    let d = 0.59;
    let e = 0.14;
    return clamp((x * (a * x + b)) / (x * (c * x + d) + e), vec3<f32>(0.0), vec3<f32>(1.0));
}

// --- fog ------------------------------------------------------------------
//
// Depth cueing only: the largest map is 512 cubes across and a corridor of
// identically tinted walls has no other cue for how far away its far end is.
// The colour is the clear colour, so geometry fading into the distance meets a
// matching background rather than a hard edge against the void.
//
// **Exponential-squared, and the same colour the browser uses**, which is the
// point: this was a linear ramp from 40 cubes to a quality-dependent end, and a
// linear fog does not have an exponential one's shape anywhere along its length
// — near geometry the browser leaves untouched came out hazed, and the far end
// of a long corridor came out of the ramp's end as a hard edge the browser
// never had. `FogExp2(0x11161f, 0.0055)` is what `HorribleAssaultPanel.tsx`
// sets, and `Quality::fog_density` returns exactly 0.0055 at High.
//
// The colour is that hex **decoded to linear**, because this is applied before
// the surface's sRGB target encodes it. three mixes its fog in *after* the
// colour-space encode (`fog_fragment` follows `colorspace_fragment` in every
// three material) while still handing the shader a linearised uniform, so the
// one thing that cannot be matched here without encoding by hand and giving up
// the sRGB target is the blend *space* — mid-distance comes out a shade deeper
// here than in the browser. The colour and the curve are the parts that read.
//
// Applied **after** tone mapping: fogging before it means the tone curve then
// lifts the fog itself, and the far end of a corridor comes back grey instead
// of receding.
//
// The colour is the map's `fogColor`, decoded to linear on the CPU.

/// How much fog stands between the eye and something `view_depth` away.
///
/// **`density` is a density, not a distance.** It is `Quality::fog_density()`,
/// which is 0.0055 to 0.011 — the reciprocal of a length, integrated here into
/// the same exponential-squared curve three's `FogExp2` uses. The translucent
/// pass used to fade itself with `view_depth / max(density, 1.0)` instead,
/// reading that number as "how far away is fully fogged"; `max(_, 1.0)` then
/// pinned the divisor at 1, so **every volume fragment more than one cube from
/// the eye came out at zero alpha** — bullet marks, tracers, impacts, smoke,
/// water and the throw arc alike, all of them invisible except within arm's
/// reach of the muzzle. Shared as one function now so the two passes cannot
/// disagree about what the number means again.
fn fog_amount(view_depth: f32, density: f32) -> f32 {
    let d = density * view_depth;
    return clamp(1.0 - exp(-d * d), 0.0, 1.0);
}

fn apply_fog(color: vec3<f32>, view_depth: f32, density: f32) -> vec3<f32> {
    return mix(color, lights.fog_color.rgb, fog_amount(view_depth, density));
}

// --- the sky --------------------------------------------------------------
//
// Shared with the browser's sky dome (`sky.ts`), which is why it is a function
// of a direction and nothing else: a gradient from the horizon colour up to the
// zenith, the horizon colour held below it (the ground is geometry, not sky),
// and a sun disc with a soft halo where the map is open enough to see one.
//
// Returned **before** tone mapping, like any other radiance.
fn sky_radiance(direction: vec3<f32>) -> vec3<f32> {
    let d = normalize(direction);
    let up = clamp(d.y, 0.0, 1.0);
    // A square root keeps the horizon band narrow, which is what reads as
    // distance: a linear ramp makes the whole upper half of the sky one haze.
    var color = mix(lights.sky_horizon.rgb, lights.sky_zenith.rgb, sqrt(up));
    if (lights.sky_zenith.w > 0.5) {
        let s = max(dot(d, lights.sun_dir.xyz), 0.0);
        let sun = lights.sun_color.rgb * lights.sun_color.w;
        color = color + sun * (pow(s, 1800.0) * 6.0 + pow(s, 24.0) * 0.12);
    }
    return color;
}
