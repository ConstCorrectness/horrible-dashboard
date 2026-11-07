// After the scene: bloom, the tone curve, anti-aliasing and sharpening, then a
// dithered write into the window.
//
// The scene arrives in **pre-exposed linear light**, in a half-float target:
// every pass that draws into it shades, fogs and multiplies by the exposure
// (`expose`), and none of them tone maps. The bloom is taken from that light
// and added back into it, and only then does the curve run, once — `display`,
// three's own `ACESFilmicToneMapping`. The browser's chain (`post-chain.ts`)
// runs the same passes in the same order.
//
// **Bloom is HDR.** The threshold is on linear light, so what glows is what is
// brighter than white — a lamp, a screen, the sun's disc — by how much
// it is brighter, not everything the curve has squeezed up near 1. A sunlit
// wall sits under the threshold (about 0.95 on Dust II against 1.4), and a
// lamp at 8 glows eight times as hard as one at 1, which a display-referred
// bloom (the old one, thresholded after the curve) could not tell apart. The
// glow is added before the curve, so it rolls off into the shoulder like the
// light it came from instead of being screened on top.
//
// The passes, all fullscreen triangles:
//
// 1. `fs_prefilter` — the scene at half resolution, keeping only what is over the
//    threshold, with a soft knee so the edge of a lamp is not a hard ring.
// 2. `fs_down` — each mip from the one above, a 13-tap filter (Jimenez's, from
//    the Call of Duty bloom talk) that does not flicker as bright pixels move.
// 3. `fs_up` — back up the chain with a 3×3 tent, **added** onto each larger mip,
//    which is what gives a bloom its wide soft tail.
// 4. `fs_tonemap` — only with FXAA on: the scene plus the bloom through the
//    curve, into a display-referred image FXAA can read many taps of.
// 5. `fs_composite` — FXAA (when on), contrast-adaptive sharpening (AMD's CAS,
//    simplified), the flashbang's ghost and the dither, into the swapchain.

struct FullscreenOut {
    @builtin(position) clip_position: vec4<f32>,
    @location(0) uv: vec2<f32>,
};

@vertex
fn vs_fullscreen(@builtin(vertex_index) index: u32) -> FullscreenOut {
    var out: FullscreenOut;
    let x = f32(i32(index) / 2) * 4.0 - 1.0;
    let y = f32(i32(index) & 1) * 4.0 - 1.0;
    out.clip_position = vec4<f32>(x, y, 0.0, 1.0);
    // Texture space is y-down and clip space is y-up, so this is not a typo.
    out.uv = vec2<f32>((x + 1.0) * 0.5, 1.0 - (y + 1.0) * 0.5);
    return out;
}

struct Post {
    // x: bloom intensity (the setting times `BLOOM_SCALE`). y: sharpening 0..1.
    // z: bloom threshold. w: knee. Both in linear light.
    params: vec4<f32>,
    // x: the flashbang afterimage's strength, 0..1. y: 1 when `source` is
    // `fs_tonemap`'s display-referred image (FXAA on), 0 when it is the scene.
    // z: 1 to dither the write. w: 1 when the output encodes sRGB on write.
    flash: vec4<f32>,
};

@group(0) @binding(0) var source: texture_2d<f32>;
@group(0) @binding(1) var source_sampler: sampler;
@group(0) @binding(2) var<uniform> post: Post;
// The composite's second input: the top of the bloom chain.
@group(0) @binding(3) var bloom: texture_2d<f32>;
// The flashbang's ghost, taken by `fs_capture` the frame a flash landed.
@group(0) @binding(4) var afterimage: texture_2d<f32>;

fn luminance(c: vec3<f32>) -> f32 {
    return dot(c, vec3<f32>(0.2126, 0.7152, 0.0722));
}

// three's `ACESFilmicToneMapping` (Stephen Hill's fit, `tonemapping_pars_fragment`
// in three 0.184), verbatim, on light that is already exposed — the `/ 0.6` is
// three's own. `post-chain.ts`'s `DISPLAY` and `atmosphere.rs`'s `aces` carry
// the same coefficients; `browser_parity.rs` pins all three. The output is
// linear: the swapchain's sRGB format encodes it, where the browser encodes by
// hand.
fn rrt_odt_fit(v: vec3<f32>) -> vec3<f32> {
    let a = v * (v + 0.0245786) - 0.000090537;
    let b = v * (0.983729 * v + 0.4329510) + 0.238081;
    return a / b;
}

fn display(color: vec3<f32>) -> vec3<f32> {
    let aces_in = mat3x3<f32>(
        vec3<f32>(0.59719, 0.07600, 0.02840),
        vec3<f32>(0.35458, 0.90834, 0.13383),
        vec3<f32>(0.04823, 0.01566, 0.83777),
    );
    let aces_out = mat3x3<f32>(
        vec3<f32>(1.60475, -0.10208, -0.00327),
        vec3<f32>(-0.53108, 1.10813, -0.07276),
        vec3<f32>(-0.07367, -0.00605, 1.07602),
    );
    let c = rrt_odt_fit(aces_in * (color / 0.6));
    return clamp(aces_out * c, vec3<f32>(0.0), vec3<f32>(1.0));
}

// The scene's linear light at `uv`.
fn scene_linear(uv: vec2<f32>) -> vec3<f32> {
    return textureSample(source, source_sampler, uv).rgb;
}

// The scene at `uv`, tone mapped, without the bloom: what the afterimage keeps.
fn scene_at(uv: vec2<f32>) -> vec3<f32> {
    return display(scene_linear(uv));
}

// The frame at `uv` as the player sees it before the composite's own steps:
// the scene plus its bloom, through the curve. With FXAA on, `fs_tonemap` has
// already done this and `source` *is* that image.
fn frame_at(uv: vec2<f32>) -> vec3<f32> {
    if (post.flash.y > 0.5) {
        return textureSample(source, source_sampler, uv).rgb;
    }
    return frame_from_scene(uv);
}

// The scene plus its bloom through the curve, from the scene itself: what
// `fs_tonemap` writes, and what `frame_at` is with FXAA off.
fn frame_from_scene(uv: vec2<f32>) -> vec3<f32> {
    var light = scene_linear(uv);
    let intensity = post.params.x;
    if (intensity > 0.001) {
        light = light + textureSample(bloom, source_sampler, uv).rgb * intensity;
    }
    return display(light);
}

@fragment
fn fs_prefilter(in: FullscreenOut) -> @location(0) vec4<f32> {
    let texel = 1.0 / vec2<f32>(textureDimensions(source));
    // Four taps, one per source pixel under this half-resolution one, so a
    // single bright pixel is not lost between samples.
    var c = scene_linear(in.uv + texel * vec2<f32>(-0.5, -0.5));
    c = c + scene_linear(in.uv + texel * vec2<f32>(0.5, -0.5));
    c = c + scene_linear(in.uv + texel * vec2<f32>(-0.5, 0.5));
    c = c + scene_linear(in.uv + texel * vec2<f32>(0.5, 0.5));
    c = c * 0.25;

    // A quadratic soft knee around the threshold.
    let threshold = post.params.z;
    let knee = max(post.params.w, 1e-4);
    let l = luminance(c);
    let soft = clamp(l - threshold + knee, 0.0, 2.0 * knee);
    let contribution = max(soft * soft / (4.0 * knee), l - threshold) / max(l, 1e-4);
    return vec4<f32>(c * max(contribution, 0.0), 1.0);
}

// The afterimage: the scene at half resolution, softened by its four taps. An
// afterimage is a ghost, not a screenshot — a sharp copy reads as the game
// having frozen.
@fragment
fn fs_capture(in: FullscreenOut) -> @location(0) vec4<f32> {
    let texel = 1.0 / vec2<f32>(textureDimensions(source));
    var c = scene_at(in.uv + texel * vec2<f32>(-0.75, -0.75));
    c = c + scene_at(in.uv + texel * vec2<f32>(0.75, -0.75));
    c = c + scene_at(in.uv + texel * vec2<f32>(-0.75, 0.75));
    c = c + scene_at(in.uv + texel * vec2<f32>(0.75, 0.75));
    return vec4<f32>(c * 0.25, 1.0);
}

@fragment
fn fs_down(in: FullscreenOut) -> @location(0) vec4<f32> {
    let t = 1.0 / vec2<f32>(textureDimensions(source));
    let s = in.uv;
    let a = textureSample(source, source_sampler, s + t * vec2<f32>(-2.0, -2.0)).rgb;
    let b = textureSample(source, source_sampler, s + t * vec2<f32>(0.0, -2.0)).rgb;
    let c = textureSample(source, source_sampler, s + t * vec2<f32>(2.0, -2.0)).rgb;
    let d = textureSample(source, source_sampler, s + t * vec2<f32>(-2.0, 0.0)).rgb;
    let e = textureSample(source, source_sampler, s).rgb;
    let f = textureSample(source, source_sampler, s + t * vec2<f32>(2.0, 0.0)).rgb;
    let g = textureSample(source, source_sampler, s + t * vec2<f32>(-2.0, 2.0)).rgb;
    let h = textureSample(source, source_sampler, s + t * vec2<f32>(0.0, 2.0)).rgb;
    let i = textureSample(source, source_sampler, s + t * vec2<f32>(2.0, 2.0)).rgb;
    let j = textureSample(source, source_sampler, s + t * vec2<f32>(-1.0, -1.0)).rgb;
    let k = textureSample(source, source_sampler, s + t * vec2<f32>(1.0, -1.0)).rgb;
    let l = textureSample(source, source_sampler, s + t * vec2<f32>(-1.0, 1.0)).rgb;
    let m = textureSample(source, source_sampler, s + t * vec2<f32>(1.0, 1.0)).rgb;
    var out = e * 0.125;
    out = out + (a + c + g + i) * 0.03125;
    out = out + (b + d + f + h) * 0.0625;
    out = out + (j + k + l + m) * 0.125;
    return vec4<f32>(out, 1.0);
}

@fragment
fn fs_up(in: FullscreenOut) -> @location(0) vec4<f32> {
    let t = 1.0 / vec2<f32>(textureDimensions(source));
    let s = in.uv;
    var out = textureSample(source, source_sampler, s).rgb * 4.0;
    out = out + textureSample(source, source_sampler, s + t * vec2<f32>(-1.0, 0.0)).rgb * 2.0;
    out = out + textureSample(source, source_sampler, s + t * vec2<f32>(1.0, 0.0)).rgb * 2.0;
    out = out + textureSample(source, source_sampler, s + t * vec2<f32>(0.0, -1.0)).rgb * 2.0;
    out = out + textureSample(source, source_sampler, s + t * vec2<f32>(0.0, 1.0)).rgb * 2.0;
    out = out + textureSample(source, source_sampler, s + t * vec2<f32>(-1.0, -1.0)).rgb;
    out = out + textureSample(source, source_sampler, s + t * vec2<f32>(1.0, -1.0)).rgb;
    out = out + textureSample(source, source_sampler, s + t * vec2<f32>(-1.0, 1.0)).rgb;
    out = out + textureSample(source, source_sampler, s + t * vec2<f32>(1.0, 1.0)).rgb;
    // Blended *additively* onto the mip below by the pipeline, so this is only
    // this level's share.
    return vec4<f32>(out / 16.0, 1.0);
}

// The frame through the curve, for FXAA to read: the composite's `frame_at`,
// written out once so the filter's taps do not each run the curve. Alpha is
// unused.
@fragment
fn fs_tonemap(in: FullscreenOut) -> @location(0) vec4<f32> {
    return vec4<f32>(frame_from_scene(in.uv), 1.0);
}

// FXAA's edge measure: perceptual, as the filter was tuned on gamma-encoded
// luma. The square root is close enough to sRGB for finding edges.
fn fxaa_luma(c: vec3<f32>) -> f32 {
    return sqrt(luminance(c));
}

// FXAA in its compact form (Lottes' FXAA 3.11 "PC console" variant): five luma
// taps find the edge's direction, then two or four taps along it blend across.
// Reads `source`, which with FXAA on is `fs_tonemap`'s image.
//
// The local-contrast test comes first, with FXAA 3.11's default quality
// thresholds: a pixel whose neighbourhood spans less than a sixth of its
// brightest luma (or less than 1/12 absolute, for dark corners) is not on an
// edge and is returned untouched. Without it the filter runs on every pixel
// and blurs the photographed surfaces' grain along with the stairs.
const FXAA_EDGE_THRESHOLD: f32 = 0.166;
const FXAA_EDGE_THRESHOLD_MIN: f32 = 0.0833;
const FXAA_REDUCE_MIN: f32 = 0.0078125;
const FXAA_REDUCE_MUL: f32 = 0.125;
const FXAA_SPAN_MAX: f32 = 8.0;

fn fxaa(uv: vec2<f32>) -> vec3<f32> {
    let t = 1.0 / vec2<f32>(textureDimensions(source));
    let rgb_m = textureSample(source, source_sampler, uv).rgb;
    let nw = fxaa_luma(textureSample(source, source_sampler, uv + vec2<f32>(-1.0, -1.0) * t).rgb);
    let ne = fxaa_luma(textureSample(source, source_sampler, uv + vec2<f32>(1.0, -1.0) * t).rgb);
    let sw = fxaa_luma(textureSample(source, source_sampler, uv + vec2<f32>(-1.0, 1.0) * t).rgb);
    let se = fxaa_luma(textureSample(source, source_sampler, uv + vec2<f32>(1.0, 1.0) * t).rgb);
    let m = fxaa_luma(rgb_m);
    let luma_min = min(m, min(min(nw, ne), min(sw, se)));
    let luma_max = max(m, max(max(nw, ne), max(sw, se)));
    if (luma_max - luma_min < max(FXAA_EDGE_THRESHOLD_MIN, luma_max * FXAA_EDGE_THRESHOLD)) {
        return rgb_m;
    }

    var dir = vec2<f32>(-((nw + ne) - (sw + se)), (nw + sw) - (ne + se));
    let reduce = max((nw + ne + sw + se) * (0.25 * FXAA_REDUCE_MUL), FXAA_REDUCE_MIN);
    let rcp_min = 1.0 / (min(abs(dir.x), abs(dir.y)) + reduce);
    dir = clamp(dir * rcp_min, vec2<f32>(-FXAA_SPAN_MAX), vec2<f32>(FXAA_SPAN_MAX)) * t;

    let a = 0.5 * (
        textureSample(source, source_sampler, uv + dir * (1.0 / 3.0 - 0.5)).rgb
        + textureSample(source, source_sampler, uv + dir * (2.0 / 3.0 - 0.5)).rgb
    );
    let b = a * 0.5 + 0.25 * (
        textureSample(source, source_sampler, uv + dir * -0.5).rgb
        + textureSample(source, source_sampler, uv + dir * 0.5).rgb
    );
    let luma_b = fxaa_luma(b);
    if (luma_b < luma_min || luma_b > luma_max) {
        return a;
    }
    return b;
}

// Interleaved gradient noise (Jimenez), 0..1, from a pixel position.
fn ign(p: vec2<f32>) -> f32 {
    return fract(52.9829189 * fract(dot(p, vec2<f32>(0.06711056, 0.00583715))));
}

// Triangular dither, -1..1 of one output step: two independent samples summed,
// so the noise is the same strength at every level and does not itself band.
// `post-chain.ts` runs the same function on `gl_FragCoord`.
fn dither_noise(p: vec2<f32>) -> f32 {
    return ign(p) + ign(p + vec2<f32>(47.0, 17.0)) - 1.0;
}

fn srgb_encode(c: vec3<f32>) -> vec3<f32> {
    return select(1.055 * pow(c, vec3<f32>(1.0 / 2.4)) - 0.055, c * 12.92, c <= vec3<f32>(0.0031308));
}

fn srgb_decode(c: vec3<f32>) -> vec3<f32> {
    return select(pow((c + 0.055) / 1.055, vec3<f32>(2.4)), c / 12.92, c <= vec3<f32>(0.04045));
}

@fragment
fn fs_composite(in: FullscreenOut) -> @location(0) vec4<f32> {
    var color: vec3<f32>;
    if (post.flash.y > 0.5) {
        color = fxaa(in.uv);
    } else {
        color = frame_at(in.uv);
    }

    // Contrast-adaptive sharpening: a negative-lobe cross whose weight falls as
    // local contrast rises, so edges gain definition and already-sharp detail
    // does not ring. Sampled one *output* pixel apart, which is what matters
    // after an upscale from a lower render scale.
    let amount = post.params.y;
    if (amount > 0.001) {
        let t = 1.0 / vec2<f32>(textureDimensions(source));
        let n = frame_at(in.uv + vec2<f32>(0.0, -t.y));
        let s = frame_at(in.uv + vec2<f32>(0.0, t.y));
        let w = frame_at(in.uv + vec2<f32>(-t.x, 0.0));
        let e = frame_at(in.uv + vec2<f32>(t.x, 0.0));
        let lo = min(color, min(min(n, s), min(w, e)));
        let hi = max(color, max(max(n, s), max(w, e)));
        // Headroom to the nearer end of 0..1: little room means a strong edge,
        // which is where sharpening would ring.
        let room = min(lo, vec3<f32>(1.0) - hi) / max(hi, vec3<f32>(1e-4));
        let adapt = sqrt(clamp(room, vec3<f32>(0.0), vec3<f32>(1.0)));
        let peak = -1.0 / mix(8.0, 5.0, amount);
        let wgt = adapt * peak;
        color = clamp(
            (color + (n + s + w + e) * wgt) / (vec3<f32>(1.0) + 4.0 * wgt),
            vec3<f32>(0.0),
            vec3<f32>(1.0),
        );
    }

    // The flashbang's ghost, under the HUD's white. Brightened like the
    // browser's (`brightness(1.35)` on its canvas copy): a burned-in image is
    // brighter than the scene it came from.
    let ghost = post.flash.x;
    if (ghost > 0.001) {
        let after = min(textureSample(afterimage, source_sampler, in.uv).rgb * 1.35, vec3<f32>(1.0));
        color = mix(color, after, ghost);
    }

    // Dither: one output step of triangular noise, so a sky gradient or a
    // fogged corridor quantises to 8 bits as grain rather than as bands. In
    // the encoded space, because that is where the steps are; the sRGB
    // swapchain re-encodes what this returns.
    if (post.flash.z > 0.5) {
        let n = dither_noise(in.clip_position.xy) / 255.0;
        if (post.flash.w > 0.5) {
            color = srgb_decode(clamp(srgb_encode(color) + n, vec3<f32>(0.0), vec3<f32>(1.0)));
        } else {
            color = clamp(color + n, vec3<f32>(0.0), vec3<f32>(1.0));
        }
    }
    return vec4<f32>(color, 1.0);
}
