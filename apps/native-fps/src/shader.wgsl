// The whole renderer's shading, which is deliberately almost nothing.
//
// There are no textures and no light sources, because this game ships neither —
// the licensing rule that keeps AssaultCube's media out also means there is no
// texture set to sample. What replaces them is what the mesher already encodes:
// a per-vertex colour derived from the texture id, and a per-face shade baked
// into that colour so floors, ceilings and the two wall axes read apart.
//
// The one thing done here rather than on the CPU is a **directional wash** from a
// fixed overhead-ish direction. It is not lighting in any physical sense; it is
// the minimum needed to keep two perpendicular walls of the same texture id from
// merging into one flat shape when you stand in a corner.

struct Camera {
    view_proj: mat4x4<f32>,
};

@group(0) @binding(0) var<uniform> camera: Camera;

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
};

@vertex
fn vs_main(in: VertexIn) -> VertexOut {
    var out: VertexOut;
    let clip = camera.view_proj * vec4<f32>(in.position, 1.0);
    out.clip_position = clip;
    out.color = in.color;
    out.normal = in.normal;
    out.view_depth = clip.w;
    return out;
}

// Roughly overhead and a little to one side. Normalised in the shader rather
// than trusted as a literal, since an unnormalised direction scales the whole
// wash and makes the map look washed out or muddy depending which way it drifts.
const LIGHT_DIR: vec3<f32> = vec3<f32>(0.35, 0.9, 0.2);

// How much of the surface colour survives with the light behind it. Not zero:
// an unlit face in a game with no light sources is a black hole in the wall, and
// there is nothing else in the scene to bounce light off it.
const AMBIENT: f32 = 0.55;

// Distance fog, in world units. Its only job is depth cueing — the largest map is
// 512 cubes across and a corridor of identically tinted walls has no other cue
// for how far away its far end is.
const FOG_START: f32 = 40.0;
const FOG_END: f32 = 260.0;
const FOG_COLOR: vec3<f32> = vec3<f32>(0.02, 0.024, 0.035);

@fragment
fn fs_main(in: VertexOut) -> @location(0) vec4<f32> {
    let n = normalize(in.normal);
    let l = normalize(LIGHT_DIR);
    let lambert = max(dot(n, l), 0.0);
    let lit = in.color * (AMBIENT + (1.0 - AMBIENT) * lambert);

    let fog = clamp((in.view_depth - FOG_START) / (FOG_END - FOG_START), 0.0, 1.0);
    return vec4<f32>(mix(lit, FOG_COLOR, fog), 1.0);
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
