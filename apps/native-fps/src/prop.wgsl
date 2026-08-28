// Static textured props: the weapon in your hands.
//
// A third shader rather than a mode of the other two, and the reason is the same
// one that makes `prop.rs` a separate parser: what this draws has textures but
// no bones. `skin.wgsl` would work if every vertex carried one bone at identity,
// at the cost of a storage buffer and a bone array on geometry that never
// deforms; `shader.wgsl` has no sampler at all.
//
// The lighting is `lighting.wgsl.inc`, concatenated ahead of this file exactly as
// it is ahead of the other two. That is the whole point of it being shared: a
// weapon lit differently from the room it is carried through reads as pasted on,
// which is the same failure a character lit differently would be.

struct Camera {
    view_proj: mat4x4<f32>,
    // x: fog density. y: shading detail. z: reveal height. w: 1 if this pass
    // receives the sun's shadow.
    params: vec4<f32>,
    reveal: vec4<f32>,
    // This pass's vertices into world space, for lighting. The view model's
    // vertices are **camera space**, so without this a weapon is shaded against
    // a sun in the wrong hemisphere and its lit side never moves as you turn.
    light_transform: mat4x4<f32>,
};

struct Material {
    base_color: vec4<f32>,
    // x: alpha cutoff, 0 for opaque. Props set 0 — see `Prop::from_slice`.
    params: vec4<f32>,
};

@group(0) @binding(0) var<uniform> camera: Camera;
@group(1) @binding(0) var<uniform> material: Material;
@group(1) @binding(1) var base_color_texture: texture_2d<f32>;
@group(1) @binding(2) var base_color_sampler: sampler;

struct VertexIn {
    @location(0) position: vec3<f32>,
    @location(1) normal: vec3<f32>,
    @location(2) uv: vec2<f32>,
};

struct VertexOut {
    @builtin(position) clip_position: vec4<f32>,
    @location(0) normal: vec3<f32>,
    @location(1) uv: vec2<f32>,
    @location(2) view_depth: f32,
};

@vertex
fn vs_prop(in: VertexIn) -> VertexOut {
    var out: VertexOut;
    let clip = camera.view_proj * vec4<f32>(in.position, 1.0);
    out.clip_position = clip;
    out.view_depth = clip.w;
    // `0.0` in w so this rotates without translating. The matrix is rigid, so
    // the normal stays unit length and needs no inverse transpose here — the
    // *model's* own transform was already folded in on the CPU, by `prop.rs`,
    // with the inverse transpose it did need.
    out.normal = (camera.light_transform * vec4<f32>(in.normal, 0.0)).xyz;
    out.uv = in.uv;
    return out;
}

@fragment
fn fs_prop(in: VertexOut) -> @location(0) vec4<f32> {
    let sampled = textureSample(base_color_texture, base_color_sampler, in.uv);
    // The texture is uploaded as `Rgba8UnormSrgb`, so the sample is **already
    // linear** — the hardware decoded it. `base_color` is a glTF factor, which
    // is linear by specification. Neither is decoded again here: doing so is the
    // mistake that darkens a weapon by a third and looks like the model being
    // badly textured.
    let albedo = sampled.rgb * material.base_color.rgb;

    // Always fully lit. A prop is a hand's width from the eye, so a shadow it
    // received would cross an edge as a hard flicker across the whole model
    // rather than as a shadow moving over something — and the browser does not
    // shadow it either, since three only shadows a mesh with `receiveShadow`
    // and `HorribleAssaultPanel.tsx` sets that on the map's mesh alone.
    let lit = tonemap(shade(albedo, in.normal, camera.params.y, 1.0));
    // No fog: it is in your hands, roughly a cube and a half away, where every
    // fog curve worth having is still the identity. Applying it anyway would be
    // a per-fragment `exp` to multiply by one.
    return vec4<f32>(lit, 1.0);
}
