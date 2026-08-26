// The world's shading.
//
// The lighting, tone mapping and colour space live in `lighting.wgsl.inc`,
// which is concatenated ahead of this file — see the note there. What is left
// here is what the *world* adds on top: a per-vertex colour derived from the
// texture id, a per-face shade baked into it, and the detail grain.

struct Camera {
    view_proj: mat4x4<f32>,
    // x: where the fog ends, in cubes. y: shading detail — 0 flat, 1 the
    // hemisphere and sun, 2 the fill as well. Packed into one vec4 rather than
    // given their own uniform because a uniform buffer's minimum binding size is
    // 16 bytes anyway, so two floats and two of padding is what a second one
    // would cost.
    params: vec4<f32>,
};

@group(0) @binding(0) var<uniform> camera: Camera;
@group(1) @binding(0) var detail_texture: texture_2d<f32>;
@group(1) @binding(1) var detail_sampler: sampler;

// The sun's shadow map. Declared here rather than in the shared lighting because
// the two shaders bind it at different group indices — see `sun_shadow`.
struct Shadow {
    light_view_proj: mat4x4<f32>,
    // x: one shadow texel in UV units. y: depth bias.
    params: vec4<f32>,
};
@group(2) @binding(0) var<uniform> shadow: Shadow;
@group(2) @binding(1) var shadow_map: texture_depth_2d;
@group(2) @binding(2) var shadow_sampler: sampler_comparison;

struct VertexIn {
    @location(0) position: vec3<f32>,
    @location(1) normal: vec3<f32>,
    @location(2) color: vec3<f32>,
};

struct VertexOut {
    @builtin(position) clip_position: vec4<f32>,
    @location(0) color: vec3<f32>,
    @location(1) normal: vec3<f32>,
    // Passed through so fog can be distance-based without reconstructing the
    // position from depth, which would need the inverse matrix for no reason.
    @location(2) view_depth: f32,
    // World space, for the detail UV. See `detail_uv`.
    @location(3) world_position: vec3<f32>,
};

@vertex
fn vs_main(in: VertexIn) -> VertexOut {
    var out: VertexOut;
    let clip = camera.view_proj * vec4<f32>(in.position, 1.0);
    out.clip_position = clip;
    out.color = in.color;
    out.normal = in.normal;
    out.view_depth = clip.w;
    out.world_position = in.position;
    return out;
}

/// The reciprocal of the detail tile's neutral value (189/255).
///
/// A pixel with no grain and no seam then leaves the surface exactly as
/// `geometry.rs` coloured it, while the tile is still free to brighten. Without
/// this the detail map would darken the entire world by a quarter.
const DETAIL_GAIN: f32 = 255.0 / 189.0;

/// Where to sample the grain, in **cube units**.
///
/// Projected on whichever axis the surface faces, rather than carried as a
/// vertex attribute. The map is axis-aligned cubes, so for this geometry the two
/// are identical — and deriving it here means the mesh, and every other thing
/// that builds a `Vertex`, does not have to grow a UV it would only ever fill
/// with the same projection.
///
/// One unit per cube is what makes the tile's edge seam land on the cube lattice
/// the map is actually built on.
fn detail_uv(world_position: vec3<f32>, normal: vec3<f32>) -> vec2<f32> {
    let n = abs(normal);
    if (n.y >= n.x && n.y >= n.z) {
        return world_position.xz;
    }
    if (n.x >= n.z) {
        return world_position.zy;
    }
    return world_position.xy;
}

@fragment
fn fs_main(in: VertexOut) -> @location(0) vec4<f32> {
    let detail = camera.params.y;

    // **Not** sRGB-decoded, and that is a deliberate match rather than an
    // oversight. The browser hands these same floats to three as a raw
    // `BufferAttribute`, which three uses directly as linear working-space
    // values — it only converts a colour that arrives through `Color`. Decoding
    // here would make the native client render every map darker and more
    // saturated than the browser renders the same map, which is the divergence
    // this whole change exists to remove.
    var albedo = in.color;

    // The grain, as a multiplier. Sampled linearly — it is not a colour, and an
    // sRGB decode here would darken every surface in the game by a third.
    let grain = textureSample(
        detail_texture,
        detail_sampler,
        detail_uv(in.world_position, in.normal),
    ).r;
    albedo = albedo * grain * DETAIL_GAIN;

    let occlusion = sun_shadow(
        shadow_map,
        shadow_sampler,
        shadow.light_view_proj * vec4<f32>(in.world_position, 1.0),
        in.normal,
        shadow.params.x,
        shadow.params.y,
    );
    let lit = tonemap(shade(albedo, in.normal, detail, occlusion));
    return vec4<f32>(apply_fog(lit, in.view_depth, camera.params.x), 1.0);
}

// ---------------------------------------------------------------------------
// The blit: the world, rendered at whatever resolution the player chose, scaled
// into the window.
//
// A fullscreen triangle rather than a quad — two triangles meet on a diagonal
// seam that some drivers shade twice — and no vertex buffer at all: three
// vertices are cheaper to compute from the index than to bind a buffer for.

struct BlitOut {
    @builtin(position) clip_position: vec4<f32>,
    @location(0) uv: vec2<f32>,
};

@vertex
fn vs_blit(@builtin(vertex_index) index: u32) -> BlitOut {
    var out: BlitOut;
    let x = f32(i32(index) / 2) * 4.0 - 1.0;
    let y = f32(i32(index) & 1) * 4.0 - 1.0;
    out.clip_position = vec4<f32>(x, y, 0.0, 1.0);
    // Texture space is y-down and clip space is y-up, so this is not a typo.
    out.uv = vec2<f32>((x + 1.0) * 0.5, 1.0 - (y + 1.0) * 0.5);
    return out;
}

@group(0) @binding(0) var scene_texture: texture_2d<f32>;
@group(0) @binding(1) var scene_sampler: sampler;

@fragment
fn fs_blit(in: BlitOut) -> @location(0) vec4<f32> {
    return textureSample(scene_texture, scene_sampler, in.uv);
}

// ---------------------------------------------------------------------------
// The overlay: the HUD, in clip space already.
//
// A second pair of entry points rather than a second file, because it shares
// nothing with the world but the module. There is no camera here on purpose —
// `hud.rs` lays the HUD out in *pixels* and converts once, since a HUD placed in
// normalized coordinates stretches with the window and a stretched crosshair
// misreports the spread it is drawing.

struct OverlayIn {
    @location(0) position: vec2<f32>,
    @location(1) color: vec4<f32>,
};

struct OverlayOut {
    @builtin(position) clip_position: vec4<f32>,
    @location(0) color: vec4<f32>,
};

@vertex
fn vs_overlay(in: OverlayIn) -> OverlayOut {
    var out: OverlayOut;
    // z of 0 is the near plane in wgpu's 0..1 clip space. The overlay pass has
    // no depth attachment at all, so this only has to be inside the frustum.
    out.clip_position = vec4<f32>(in.position, 0.0, 1.0);
    out.color = in.color;
    return out;
}

@fragment
fn fs_overlay(in: OverlayOut) -> @location(0) vec4<f32> {
    return in.color;
}
