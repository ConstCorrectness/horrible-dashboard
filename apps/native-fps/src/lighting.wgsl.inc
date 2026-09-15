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
// Colours are the browser's hex values already decoded to linear, so the shader
// does not pay for a conversion of a constant on every fragment.
//
// Hemisphere: sky 0xbfd4ff, ground 0x33302c, intensity 1.55. It carries the
// ambient, and being a *gradient* rather than a constant is what stops a surface
// facing away from every light reading as a hole.
const SKY_COLOR: vec3<f32> = vec3<f32>(0.5029, 0.6584, 1.0000);
const GROUND_COLOR: vec3<f32> = vec3<f32>(0.0331, 0.0295, 0.0241);
const HEMI_INTENSITY: f32 = 1.55;

// Sun: 0xfff2dd at 1.75, from the direction the browser puts it — normalised
// from `sun.position` minus its target, which is a fixed offset from the map's
// centre, so the same direction serves every map.
const SUN_COLOR: vec3<f32> = vec3<f32>(1.0000, 0.9046, 0.7305);
const SUN_INTENSITY: f32 = 1.75;
const SUN_DIR: vec3<f32> = vec3<f32>(0.523, 0.780, 0.343);

// A cool fill from behind at a fraction of the sun's strength: 0x9fb6ff at 0.45.
// Without it every surface facing away from the sun is lit only by the
// hemisphere and comes out the same value.
const FILL_COLOR: vec3<f32> = vec3<f32>(0.3564, 0.4735, 1.0000);
const FILL_INTENSITY: f32 = 0.45;
const FILL_DIR: vec3<f32> = vec3<f32>(-0.538, 0.377, -0.754);

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
) -> f32 {
    // Perspective divide, then into texture space.
    var ndc = light_position.xyz / light_position.w;
    var uv = vec2<f32>(ndc.x * 0.5 + 0.5, 0.5 - ndc.y * 0.5);

    if (ndc.z > 1.0 || ndc.z < 0.0 || uv.x < 0.0 || uv.x > 1.0 || uv.y < 0.0 || uv.y > 1.0) {
        return 1.0;
    }

    let ndotl = max(dot(normalize(normal), normalize(SUN_DIR)), 0.0);
    let slope = clamp(1.0 - ndotl, 0.0, 1.0);
    let depth = ndc.z - (bias + bias * 4.0 * slope);

    var sum = 0.0;
    let radius = texel * 1.5;
    for (var i = 0; i < 16; i = i + 1) {
        let offset = POISSON_16[i] * radius;
        sum = sum + textureSampleCompare(map, map_sampler, uv + offset, depth);
    }
    return sum * 0.0625;
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
fn shade(albedo: vec3<f32>, normal: vec3<f32>, detail: f32, shadow: f32) -> vec3<f32> {
    if (detail < 1.0) {
        return albedo;
    }
    let n = normalize(normal);

    // three's hemisphere light: the sky above, the ground below, mixed by how
    // far the normal has tipped between them.
    let hemi = mix(GROUND_COLOR, SKY_COLOR, n.y * 0.5 + 0.5) * HEMI_INTENSITY;

    var irradiance = hemi;
    irradiance = irradiance + SUN_COLOR * SUN_INTENSITY * max(dot(n, normalize(SUN_DIR)), 0.0) * shadow;
    if (detail >= 2.0) {
        irradiance = irradiance + FILL_COLOR * FILL_INTENSITY * max(dot(n, normalize(FILL_DIR)), 0.0);
    }
    return albedo * irradiance * RECIPROCAL_PI;
}

// --- tone mapping ---------------------------------------------------------
//
// ACES rather than a clip, and it is not decoration: the sun plus a hemisphere
// light puts lit floors above 1.0, and clipping flattens everything past that
// into the same white — which is exactly where a bright surface loses the grain
// the detail texture exists to give it.
//
// Narkowicz's fit, which is what three's `ACESFilmicToneMapping` is.
const EXPOSURE: f32 = 1.15;

fn tonemap(color: vec3<f32>) -> vec3<f32> {
    let x = color * EXPOSURE;
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
const FOG_COLOR: vec3<f32> = vec3<f32>(0.0056, 0.0080, 0.0137);

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
    return mix(color, FOG_COLOR, fog_amount(view_depth, density));
}
