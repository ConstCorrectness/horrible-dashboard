// Skinned character shading.
//
// A second shader rather than two more entry points in `shader.wgsl`, because
// this is the one thing in the renderer that has textures and bones. The world
// has neither and never will — its surfaces are tinted by texture id precisely
// because the game ships no texture set — so folding the two together would put
// a sampler and a bone array in every world draw to serve the character.
//
// The lighting itself is in `lighting.wgsl.inc`, concatenated ahead of this
// file and ahead of `shader.wgsl` both. It is shared rather than copied because
// a character lit differently from the room it is standing in reads as a sprite
// pasted over the world — so the two must move together, and the surest way to
// guarantee that is for there to be one copy.

struct Camera {
    view_proj: mat4x4<f32>,
    params: vec4<f32>,
};

// Per player: the team wash, then one matrix per bone.
//
// A runtime-sized array, bound with an explicit size so each player's binding
// covers exactly their own slice of the buffer. Sized `array<mat4x4<f32>, 34>`
// instead would hardcode the rig's bone count in a second place — and the count
// that matters is whatever the GLB actually carries.
struct Skin {
    tint: vec4<f32>,
    bones: array<mat4x4<f32>>,
};

struct Material {
    base_color: vec4<f32>,
    // x: alpha cutoff, 0 for an opaque material. The rest is padding — a
    // uniform buffer's minimum binding size is 16 bytes anyway.
    params: vec4<f32>,
};

@group(0) @binding(0) var<uniform> camera: Camera;
@group(1) @binding(0) var<storage, read> skin: Skin;
@group(2) @binding(0) var<uniform> material: Material;
@group(2) @binding(1) var base_color_texture: texture_2d<f32>;
@group(2) @binding(2) var base_color_sampler: sampler;

// The sun's shadow map. Players **receive** but do not cast: the map is rendered
// once, before any of them exist. See `shadow.rs`.
struct Shadow {
    light_view_proj: mat4x4<f32>,
    params: vec4<f32>,
};
@group(3) @binding(0) var<uniform> shadow: Shadow;
@group(3) @binding(1) var shadow_map: texture_depth_2d;
@group(3) @binding(2) var shadow_sampler: sampler_comparison;

struct VertexIn {
    @location(0) position: vec3<f32>,
    @location(1) normal: vec3<f32>,
    @location(2) uv: vec2<f32>,
    @location(3) joints: vec4<u32>,
    @location(4) weights: vec4<f32>,
};

struct VertexOut {
    @builtin(position) clip_position: vec4<f32>,
    @location(0) uv: vec2<f32>,
    @location(1) normal: vec3<f32>,
    @location(2) view_depth: f32,
    @location(3) world_position: vec3<f32>,
};

@vertex
fn vs_skin(in: VertexIn) -> VertexOut {
    // The weighted sum of the four bone matrices — linear blend skinning, which
    // is what the exporter's weights were authored against. Normalising the
    // weights here would be wrong as often as right: glTF requires them to sum
    // to 1 already, and a mesh whose weights do not is a mesh with a real
    // problem that should be visible rather than papered over.
    var m = mat4x4<f32>(
        vec4<f32>(0.0, 0.0, 0.0, 0.0),
        vec4<f32>(0.0, 0.0, 0.0, 0.0),
        vec4<f32>(0.0, 0.0, 0.0, 0.0),
        vec4<f32>(0.0, 0.0, 0.0, 0.0),
    );
    for (var i = 0u; i < 4u; i = i + 1u) {
        let w = in.weights[i];
        if (w > 0.0) {
            m = m + skin.bones[in.joints[i]] * w;
        }
    }

    let world = m * vec4<f32>(in.position, 1.0);
    let clip = camera.view_proj * world;

    var out: VertexOut;
    out.clip_position = clip;
    out.uv = in.uv;
    // w of 0, so the bones' translation does not move a direction. The scale in
    // them still does, which is why this is normalised in the fragment shader
    // rather than trusted here.
    out.normal = (m * vec4<f32>(in.normal, 0.0)).xyz;
    out.view_depth = clip.w;
    out.world_position = world.xyz;
    return out;
}

@fragment
fn fs_skin(in: VertexOut) -> @location(0) vec4<f32> {
    let sampled = textureSample(base_color_texture, base_color_sampler, in.uv) * material.base_color;

    // Alpha masking, not blending. The operator's body material is glTF `MASK`
    // at 0.5, and the parts it masks away — the cut edges of the kit — are
    // opaque quads without this: no error, just a character wearing rectangles.
    // A discard also keeps the whole pass order-independent, which a blended
    // character would not be.
    let cutoff = material.params.x;
    if (cutoff > 0.0 && sampled.a < cutoff) {
        discard;
    }

    // The base colour map is an sRGB texture, so the sample is **already
    // linear** — unlike the world's vertex colours, which are authored sRGB and
    // have to be decoded. Decoding here as well would darken the character by a
    // third against a world that had not been, which reads as the operator
    // being in shadow no matter where they stand.
    //
    // The team wash is authored sRGB, so that half is converted.
    let wash = srgb_to_linear(skin.tint.rgb);
    let albedo = mix(sampled.rgb, wash, skin.tint.a);

    let occlusion = sun_shadow(
        shadow_map,
        shadow_sampler,
        shadow.light_view_proj * vec4<f32>(in.world_position, 1.0),
        in.normal,
        shadow.params.x,
        shadow.params.y,
    );
    let lit = tonemap(shade(albedo, in.normal, camera.params.y, occlusion));
    return vec4<f32>(apply_fog(lit, in.view_depth, camera.params.x), 1.0);
}
