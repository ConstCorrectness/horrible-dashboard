// The shadow cast pass: depth only, from the sun's point of view.
//
// There is deliberately no fragment stage. Depth is written by the rasteriser,
// and a pass that outputs no colour is the cheapest thing a GPU can be asked to
// do with a map's worth of triangles — which is what makes rendering this at all
// affordable, alongside the fact that it happens once per map rather than once
// per frame.

struct Shadow {
    light_view_proj: mat4x4<f32>,
    params: vec4<f32>,
};

@group(0) @binding(0) var<uniform> shadow: Shadow;

@vertex
fn vs_shadow(@location(0) position: vec3<f32>) -> @builtin(position) vec4<f32> {
    return shadow.light_view_proj * vec4<f32>(position, 1.0);
}
