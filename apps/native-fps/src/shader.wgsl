// The world's shading.
//
// The lighting, tone mapping and colour space live in `lighting.wgsl.inc`,
// which is concatenated ahead of this file — see the note there. What is left
// here is what the *world* adds on top: a per-vertex colour derived from the
// texture id, a per-face shade baked into it, and the detail grain.

struct Camera {
    view_proj: mat4x4<f32>,
    // x: the fog's density, in inverse cubes. y: shading detail — 0 flat, 1 the
    // hemisphere and sun, 2 the fill as well. z: the map's height, for the
    // build-in. w: 1 if this pass receives the sun's shadow, 0 if not. Packed
    // into one vec4 rather than given their own uniform because a uniform
    // buffer's minimum binding size is 16 bytes anyway.
    params: vec4<f32>,
    // The build-in: x progress, yz centre, w radius. See `reveal.rs`. The view
    // model binds a *different* camera uniform whose progress is already
    // finished, which is how the weapon in your hands is excluded from the
    // animation without a branch anywhere.
    reveal: vec4<f32>,
    // This pass's vertices into **world space, for lighting**. The identity for
    // the world; the camera-to-world matrix for the view model, whose vertices
    // are camera space. See `CameraUniform::attached_to`.
    light_transform: mat4x4<f32>,
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
    // How far into its own arrival this vertex is: 0 not yet here, 1 settled.
    @location(4) build: f32,
    // Passed through so fog can be distance-based without reconstructing the
    // position from depth, which would need the inverse matrix for no reason.
    @location(2) view_depth: f32,
    // World space, for the detail UV. See `detail_uv`.
    @location(3) world_position: vec3<f32>,
};

// How wide the moving front is, in units of overall progress. `reveal::BAND`.
const REVEAL_BAND: f32 = 0.14;
// How far below its resting place a cube starts.
const REVEAL_RISE: f32 = 14.0;

fn reveal_hash(p: vec2<f32>) -> f32 {
    return fract(sin(dot(p, vec2<f32>(127.1, 311.7))) * 43758.5453123);
}

// This vertex's place in the queue, 0 (first) to 1 (last): mostly distance from
// the map's centre, nudged later by height so a wall arrives after the floor it
// stands on, plus a per-column hash so the front is ragged rather than a clean
// expanding ring.
//
// Computed from the position rather than from a vertex attribute, exactly as the
// browser does it: the mesher emits positions in world space with an identity
// transform, so a position is all the ordering needs — and `geometry.rs`, which
// is pure, tested and shared with the physics, stays untouched by a visual
// effect.
fn reveal_build(p: vec3<f32>) -> f32 {
    let radial = clamp(
        length(p.xz - camera.reveal.yz) / max(camera.reveal.w, 0.001),
        0.0,
        1.0
    );
    let height = clamp(p.y / max(camera.params.z, 0.001), 0.0, 1.0);
    // Hashed per 4-unit column so a whole cube shares one offset. Hashing per
    // vertex tears individual quads apart at their own corners.
    let jitter = reveal_hash(floor(p.xz * 0.25));
    return clamp(radial * 0.70 + height * 0.16 + jitter * 0.14, 0.0, 1.0);
}

@vertex
fn vs_main(in: VertexIn) -> VertexOut {
    var out: VertexOut;
    // Once the build is over, every vertex is settled by definition and the
    // ordering function is dead weight — a hash, a length and a floor per
    // vertex per frame, for the whole match, to compute a number that is always
    // 1. The branch is on a *uniform*, so it is coherent across every invocation
    // and costs nothing. This is what `Reveal::finished` is about on the CPU
    // side, and it is the difference between a two-second animation and a
    // permanent tax for having had one.
    var local = 1.0;
    if (camera.reveal.x < 1.0 + REVEAL_BAND) {
        local = clamp((camera.reveal.x - reveal_build(in.position)) / REVEAL_BAND, 0.0, 1.0);
    }
    out.build = local;
    // Smoothstep so a cube decelerates into place rather than arriving linearly.
    let eased = local * local * (3.0 - 2.0 * local);
    var position = in.position;
    position.y = position.y - (1.0 - eased) * REVEAL_RISE;

    let clip = camera.view_proj * vec4<f32>(position, 1.0);
    out.clip_position = clip;
    out.color = in.color;
    out.view_depth = clip.w;
    // The **resting** position, not the risen one: the detail grain is anchored
    // to the world, and sampling it at the animated position would make the
    // texture slide up the wall as the wall arrives.
    //
    // Through `light_transform`, which is the identity for the world and so
    // costs it nothing but leaves the view model — whose vertices are camera
    // space — shaded and shadow-tested where it actually is rather than a
    // hand's width from the map's origin.
    out.world_position = (camera.light_transform * vec4<f32>(in.position, 1.0)).xyz;
    // `0.0` in w, so this rotates without translating. The matrix is rigid, so
    // no inverse-transpose is needed and the normal stays unit length.
    out.normal = (camera.light_transform * vec4<f32>(in.normal, 0.0)).xyz;
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
    // A vertex that has not arrived is not drawn at all, so the world *builds*
    // rather than fading up. Discarded before any lighting work, both because
    // there is no point shading a fragment that is thrown away and because a
    // half-lit ghost of the finished map is exactly what the effect must not
    // look like.
    if (in.build <= 0.0) {
        discard;
    }
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

    // A pass that does not receive the shadow is fully lit, not fully dark —
    // and it skips the nine compares rather than throwing them away.
    var occlusion = 1.0;
    if (camera.params.w > 0.5) {
        occlusion = sun_shadow(
            shadow_map,
            shadow_sampler,
            shadow.light_view_proj * vec4<f32>(in.world_position, 1.0),
            in.normal,
            shadow.params.x,
            shadow.params.y,
        );
    }
    let lit = tonemap(shade(albedo, in.normal, detail, occlusion));
    var out_color = vec4<f32>(apply_fog(lit, in.view_depth, camera.params.x), 1.0);
    // The frontier glow, added over the final lit colour so cubes land hot and
    // cool into their normal shading. Last, after the tone curve and the fog,
    // for the same reason the browser puts it in `dithering_fragment`: it is a
    // light the world does not *have*, not a light the world is lit by.
    let edge = 1.0 - smoothstep(0.0, 0.85, in.build);
    out_color = vec4<f32>(
        out_color.rgb + vec3<f32>(0.35, 0.62, 1.0) * edge * 0.85,
        out_color.a
    );
    return out_color;
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

// ---------------------------------------------------------------------------
// Volumes: smoke and fire.
//
// A cloud is drawn as **the sphere the server tests sight against**, not as a
// particle billboard. A billboard looks better in a screenshot and is a lie in a
// firefight: its visual edge is nowhere near the volume that blocks the shot, so
// players learn a shape that is not the rule.
//
// Three things this pass does differently from the world:
//
// - **No depth write.** A cloud must not occlude what is behind it in the depth
//   buffer, or the geometry drawn after it disappears.
// - **No back-face culling.** Walking into smoke has to fill the screen, which
//   means seeing the far wall of the sphere from inside.
// - **Alpha blending**, with the density carried per vertex.
//
// The density is broken up by a value noise in *world* space, so a cloud has
// internal structure and, being world-anchored, does not swim when the camera
// moves. It is deliberately static: the browser's version animates on a time
// uniform, and this pass has no clock — see the note in the two-clients doc,
// which lists that as a known difference rather than pretending to parity.

struct VolumeIn {
    @location(0) position: vec3<f32>,
    @location(1) normal: vec3<f32>,
    @location(2) color: vec4<f32>,
    // 0 = cloud (noisy interior), 1 = flat. See `VolumeVertex::mode`: tracers
    // and impacts share this pass because they share its blending and its
    // depth state, and nothing else about them is alike.
    @location(3) mode: f32,
};

struct VolumeOut {
    @builtin(position) clip_position: vec4<f32>,
    @location(0) color: vec4<f32>,
    @location(1) normal: vec3<f32>,
    @location(2) world_position: vec3<f32>,
    @location(3) view_depth: f32,
    @location(4) @interpolate(flat) mode: f32,
};

@vertex
fn vs_volume(in: VolumeIn) -> VolumeOut {
    var out: VolumeOut;
    let clip = camera.view_proj * vec4<f32>(in.position, 1.0);
    out.clip_position = clip;
    out.color = in.color;
    out.normal = in.normal;
    out.world_position = in.position;
    out.view_depth = clip.w;
    out.mode = in.mode;
    return out;
}

fn volume_hash(p: vec3<f32>) -> f32 {
    return fract(sin(dot(p, vec3<f32>(12.9898, 78.233, 37.719))) * 43758.5453);
}

/// Value noise: trilinear between the hashes of the eight surrounding lattice
/// points. `smoothstep` on the fraction rather than the raw one, or the cell
/// boundaries show as a visible grid.
fn volume_noise(p: vec3<f32>) -> f32 {
    let i = floor(p);
    let f = p - i;
    let w = f * f * (3.0 - 2.0 * f);
    let c000 = volume_hash(i + vec3<f32>(0.0, 0.0, 0.0));
    let c100 = volume_hash(i + vec3<f32>(1.0, 0.0, 0.0));
    let c010 = volume_hash(i + vec3<f32>(0.0, 1.0, 0.0));
    let c110 = volume_hash(i + vec3<f32>(1.0, 1.0, 0.0));
    let c001 = volume_hash(i + vec3<f32>(0.0, 0.0, 1.0));
    let c101 = volume_hash(i + vec3<f32>(1.0, 0.0, 1.0));
    let c011 = volume_hash(i + vec3<f32>(0.0, 1.0, 1.0));
    let c111 = volume_hash(i + vec3<f32>(1.0, 1.0, 1.0));
    let x00 = mix(c000, c100, w.x);
    let x10 = mix(c010, c110, w.x);
    let x01 = mix(c001, c101, w.x);
    let x11 = mix(c011, c111, w.x);
    return mix(mix(x00, x10, w.y), mix(x01, x11, w.y), w.z);
}

@fragment
fn fs_volume(in: VolumeOut) -> @location(0) vec4<f32> {
    // Flat: a tracer is a beam a few centimetres across, and cloud noise across
    // it would eat the line entirely rather than texture it. Fogged like
    // everything else, so a tracer across a long hall still fades with distance.
    if (in.mode > 0.5) {
        let far_fog = clamp(in.view_depth / max(camera.params.x, 1.0), 0.0, 1.0);
        return vec4<f32>(tonemap(in.color.rgb), in.color.a * (1.0 - far_fog));
    }
    // Two octaves is enough at this scale and costs sixteen hashes; a third
    // buys nothing you can see through a cloud you are meant to be blinded by.
    let n = volume_noise(in.world_position * 0.9) * 0.65
        + volume_noise(in.world_position * 2.3) * 0.35;
    // Never all the way to zero: the density the server uses is uniform inside
    // the sphere, and a hole you can see a body through would be the exact lie
    // the sphere exists to avoid.
    let density = mix(0.62, 1.0, n);
    let shaded = in.color.rgb * mix(0.75, 1.12, n);
    var alpha = in.color.a * density;
    // Fade the last stretch into the fog, so a cloud at the edge of sight does
    // not sit as a hard disc against the haze.
    let fog = clamp(in.view_depth / max(camera.params.x, 1.0), 0.0, 1.0);
    alpha = alpha * (1.0 - fog);
    return vec4<f32>(tonemap(shaded), alpha);
}
