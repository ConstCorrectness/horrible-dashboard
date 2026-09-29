// After the scene: bloom, then the composite into the window with sharpening.
//
// The scene arrives **already tone mapped** — every pass that draws into it
// shades, tone maps and fogs in its own fragment shader, which is what keeps it
// the browser's picture. So this bloom is on display-referred light: what passes
// the threshold is what is already close to white, which is lamps, screens, the
// sun's disc and a muzzle flash, and not a sunlit wall. A physically-based bloom
// would need the scene in linear HDR, which would mean moving the tone curve out
// of every shader in the client; the glow on the things that should glow is
// what this is for, and it does not need that.
//
// Three passes and a composite, all fullscreen triangles:
//
// 1. `fs_prefilter` — the scene at half resolution, keeping only what is over the
//    threshold, with a soft knee so the edge of a lamp is not a hard ring.
// 2. `fs_down` — each mip from the one above, a 13-tap filter (Jimenez's, from
//    the Call of Duty bloom talk) that does not flicker as bright pixels move.
// 3. `fs_up` — back up the chain with a 3×3 tent, **added** onto each larger mip,
//    which is what gives a bloom its wide soft tail.
// 4. `fs_composite` — the scene plus the bloom, then contrast-adaptive
//    sharpening (AMD's CAS, simplified) into the swapchain.

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
    // x: bloom intensity. y: sharpening 0..1. z: bloom threshold. w: knee.
    params: vec4<f32>,
};

@group(0) @binding(0) var source: texture_2d<f32>;
@group(0) @binding(1) var source_sampler: sampler;
@group(0) @binding(2) var<uniform> post: Post;
// The composite's second input: the top of the bloom chain.
@group(0) @binding(3) var bloom: texture_2d<f32>;

fn luminance(c: vec3<f32>) -> f32 {
    return dot(c, vec3<f32>(0.2126, 0.7152, 0.0722));
}

@fragment
fn fs_prefilter(in: FullscreenOut) -> @location(0) vec4<f32> {
    let texel = 1.0 / vec2<f32>(textureDimensions(source));
    // Four taps, one per source pixel under this half-resolution one, so a
    // single bright pixel is not lost between samples.
    var c = textureSample(source, source_sampler, in.uv + texel * vec2<f32>(-0.5, -0.5)).rgb;
    c = c + textureSample(source, source_sampler, in.uv + texel * vec2<f32>(0.5, -0.5)).rgb;
    c = c + textureSample(source, source_sampler, in.uv + texel * vec2<f32>(-0.5, 0.5)).rgb;
    c = c + textureSample(source, source_sampler, in.uv + texel * vec2<f32>(0.5, 0.5)).rgb;
    c = c * 0.25;

    // A quadratic soft knee around the threshold.
    let threshold = post.params.z;
    let knee = max(post.params.w, 1e-4);
    let l = luminance(c);
    let soft = clamp(l - threshold + knee, 0.0, 2.0 * knee);
    let contribution = max(soft * soft / (4.0 * knee), l - threshold) / max(l, 1e-4);
    return vec4<f32>(c * max(contribution, 0.0), 1.0);
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

@fragment
fn fs_composite(in: FullscreenOut) -> @location(0) vec4<f32> {
    var color = textureSample(source, source_sampler, in.uv).rgb;

    // Contrast-adaptive sharpening: a negative-lobe cross whose weight falls as
    // local contrast rises, so edges gain definition and already-sharp detail
    // does not ring. Sampled one *output* pixel apart, which is what matters
    // after an upscale from a lower render scale.
    let amount = post.params.y;
    if (amount > 0.001) {
        let t = 1.0 / vec2<f32>(textureDimensions(source));
        let n = textureSample(source, source_sampler, in.uv + vec2<f32>(0.0, -t.y)).rgb;
        let s = textureSample(source, source_sampler, in.uv + vec2<f32>(0.0, t.y)).rgb;
        let w = textureSample(source, source_sampler, in.uv + vec2<f32>(-t.x, 0.0)).rgb;
        let e = textureSample(source, source_sampler, in.uv + vec2<f32>(t.x, 0.0)).rgb;
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

    let intensity = post.params.x;
    if (intensity > 0.001) {
        let glow = textureSample(bloom, source_sampler, in.uv).rgb;
        // Screen, not add: the scene is display-referred and already near 1 where
        // the glow is strongest, and adding would clip the lamp into a flat disc.
        let g = glow * intensity;
        color = vec3<f32>(1.0) - (vec3<f32>(1.0) - color) * (vec3<f32>(1.0) - g);
    }
    return vec4<f32>(color, 1.0);
}
