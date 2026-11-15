// The stage's last step: its finished picture, drawn into its rectangle of the
// menu.
//
// One triangle over whatever viewport the pass was given, so the rectangle is
// the caller's and this never hears about the window. The picture is already
// tone mapped — `post.wgsl` did that into the texture read here — so this is a
// copy of its colour.
//
// Its **coverage** comes from somewhere else: the scene the picture was made
// from, whose alpha is zero where the stage was cleared and one where a model
// was drawn. The tone curve does not carry alpha, and without it the stage is
// an opaque box of backdrop pasted onto the panel; with it only the weapon is
// there, and the panel's own grid runs behind it. The scene is drawn larger
// than the rectangle, so that edge is already a soft one.

struct Out {
    @builtin(position) position: vec4<f32>,
    @location(0) uv: vec2<f32>,
};

@group(0) @binding(0) var picture: texture_2d<f32>;
@group(0) @binding(1) var picture_sampler: sampler;
@group(0) @binding(2) var scene: texture_2d<f32>;

@vertex
fn vs_stage(@builtin(vertex_index) index: u32) -> Out {
    // (0,0) (2,0) (0,2): the triangle that covers the viewport.
    let corner = vec2<f32>(f32((index << 1u) & 2u), f32(index & 2u));
    var out: Out;
    out.position = vec4<f32>(corner * 2.0 - 1.0, 0.0, 1.0);
    out.uv = vec2<f32>(corner.x, 1.0 - corner.y);
    return out;
}

@fragment
fn fs_stage(in: Out) -> @location(0) vec4<f32> {
    let color = textureSample(picture, picture_sampler, in.uv);
    let cover = textureSample(scene, picture_sampler, in.uv).a;
    // Faded toward the rectangle's own edges too, so a zoomed-in subject
    // leaves the stage rather than being cut off by it.
    let edge = min(min(in.uv.x, 1.0 - in.uv.x), min(in.uv.y, 1.0 - in.uv.y));
    return vec4<f32>(color.rgb, cover * smoothstep(0.0, 0.05, edge));
}
