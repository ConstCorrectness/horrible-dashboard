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

// The surface layers, and the image-based light a modelled map's physical
// surfaces read: the map's prefiltered sky cube (`environment.rs`) and three's
// DFG table (`dfg_lut.rs`), both through one clamped, trilinear sampler.
@group(3) @binding(0) var pbr_textures: texture_2d_array<f32>;
@group(3) @binding(1) var pbr_sampler: sampler;
@group(3) @binding(2) var env_cube: texture_cube<f32>;
@group(3) @binding(3) var env_sampler: sampler;
@group(3) @binding(4) var dfg_lut: texture_2d<f32>;

struct VertexIn {
    @location(0) position: vec3<f32>,
    @location(1) normal: vec3<f32>,
    @location(2) color: vec3<f32>,
    @location(3) uv: vec2<f32>,
    @location(4) material: f32,
    @location(5) ao: f32,
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
    @location(5) uv: vec2<f32>,
    @location(6) material: f32,
    // Baked ambient occlusion, 1 for none. Content, the same at every
    // quality level: see `Vertex::ao`.
    @location(7) ao: f32,
    // The point lights that can reach this vertex's cell: `point_mask` in
    // `lighting.wgsl.inc`. Flat, because it is the draw's, not the vertex's.
    @location(8) @interpolate(flat) point_mask: vec2<u32>,
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

/// Every light: the preview examples, which draw the map as one buffer.
@vertex
fn vs_main(in: VertexIn) -> VertexOut {
    return world_vertex(in, vec2<u32>(0xffffffffu, 0xffffffffu));
}

/// The game's world pipeline. `point_mask` is per instance, and each cell of
/// the world is drawn as its own instance (`world_chunks`); instance 0 is
/// `ALL_LIGHTS`, which is what bodies and the view model draw as.
@vertex
fn vs_world(in: VertexIn, @location(8) point_mask: vec2<u32>) -> VertexOut {
    return world_vertex(in, point_mask);
}

fn world_vertex(in: VertexIn, point_mask: vec2<u32>) -> VertexOut {
    var out: VertexOut;
    out.point_mask = point_mask;
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
    out.uv = in.uv;
    out.material = in.material;
    out.ao = in.ao;
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

// A surface's tangent frame from the screen-space derivatives of its position and
// UV (Schuler's cotangent frame), so a normal map needs no tangents in the vertex
// format. The UVs here are planar, per vertex, so this is the frame of whichever
// face the pixel is on. The derivatives are taken by the caller, at the top of
// `fs_main` before any branch or discard, because they are only defined in
// uniform control flow.
fn perturb_normal(
    n: vec3<f32>,
    dp1: vec3<f32>,
    dp2: vec3<f32>,
    duv1: vec2<f32>,
    duv2: vec2<f32>,
    tangent_normal: vec3<f32>,
) -> vec3<f32> {
    let dp2perp = cross(dp2, n);
    let dp1perp = cross(n, dp1);
    let t = dp2perp * duv1.x + dp1perp * duv2.x;
    let b = dp2perp * duv1.y + dp1perp * duv2.y;
    let invmax = inverseSqrt(max(max(dot(t, t), dot(b, b)), 1e-12));
    return normalize(t * (tangent_normal.x * invmax) + b * (tangent_normal.y * invmax) + n * tangent_normal.z);
}

fn get_pbr_properties(mat_id: u32) -> vec2<f32> {
    switch (mat_id) {
        case 1u: { return vec2<f32>(0.88, 0.05); } // Asphalt
        case 2u: { return vec2<f32>(0.16, 0.06); } // Marble
        case 3u: { return vec2<f32>(0.75, 0.02); } // Concrete
        case 4u: { return vec2<f32>(0.32, 0.88); } // VaultSteel
        case 5u: { return vec2<f32>(0.45, 0.10); } // Hazard
        case 6u: { return vec2<f32>(0.32, 0.04); } // Wood
        case 7u: { return vec2<f32>(0.65, 0.05); } // Crate
        case 8u: { return vec2<f32>(0.58, 0.55); } // Container
        case 9u: { return vec2<f32>(0.06, 0.15); } // Glass
        case 10u: { return vec2<f32>(0.20, 0.95); } // Gold
        case 13u: { return vec2<f32>(0.85, 0.02); } // Masonry
        case 14u: { return vec2<f32>(0.90, 0.02); } // Plaster
        case 15u: { return vec2<f32>(0.80, 0.02); } // Cobblestone
        case 16u: { return vec2<f32>(0.70, 0.02); } // RoofTile
        case 17u: { return vec2<f32>(0.95, 0.00); } // Carpet
        case 18u: { return vec2<f32>(0.85, 0.02); } // Brick
        // Photographed kinds: the roughness here is overridden by the map, and
        // only the metalness is read. Rusted iron is the one that is metal.
        case 25u: { return vec2<f32>(0.60, 0.50); } // RustIron
        case 19u, 20u, 21u, 22u, 23u, 24u, 26u, 27u: { return vec2<f32>(0.80, 0.02); }
        case 28u, 29u: { return vec2<f32>(0.80, 0.04); } // Cast and slab concrete
        case 30u: { return vec2<f32>(0.55, 0.45); } // Corrugated
        case 31u: { return vec2<f32>(0.50, 0.55); } // PaintedMetal
        case 32u: { return vec2<f32>(0.40, 0.85); } // TreadPlate
        case 33u: { return vec2<f32>(0.85, 0.02); } // YardAsphalt
        case 34u: { return vec2<f32>(0.95, 0.00); } // CarpetTile
        case 35u: { return vec2<f32>(0.30, 0.02); } // CeramicTile
        case 36u: { return vec2<f32>(0.15, 0.04); } // PolishedMarble
        case 37u, 38u: { return vec2<f32>(0.75, 0.02); } // RedBrick, WallPlaster
        default: { return vec2<f32>(0.70, 0.04); }
    }
}

// --- three's MeshStandardMaterial ---------------------------------------------
//
// What the browser draws a modelled map with, ported from three r184's
// `lights_physical_pars_fragment`, `lights_physical_fragment` and
// `envmap_physical_pars_fragment` line for line: GGX with Smith-correlated
// visibility and Epic's Schlick for the sun, the fill and every lamp, with
// three's multiple-scattering compensation; the hemisphere as indirect diffuse;
// and the map's environment (`environment.rs`, `environment.ts`) as the
// split-sum specular and as irradiance, energy-conserved against the diffuse
// the way `RE_IndirectSpecular_Physical` does it. A cube map stays Lambert
// (`shade`), as the browser keeps `MeshLambertMaterial` there.

/// `environment::LEVELS`: roughness 0 at mip 0 to 1 at the last.
const ENV_LEVELS: f32 = 6.0;
const PI: f32 = 3.141592653589793;
const EPSILON: f32 = 1e-6;

struct PhysicalSurface {
    // `diffuseContribution`: the albedo less what a metal does not diffuse.
    contribution: vec3<f32>,
    // `diffuseColor`, the metal's tint.
    diffuse: vec3<f32>,
    // `specularColorBlended`: 0.04 for a dielectric, the albedo for a metal.
    specular: vec3<f32>,
    metalness: f32,
    roughness: f32,
    // The view's half of the multiscatter terms, the same for every light:
    // `dfg(roughness, dot_nv)` read once per fragment instead of once per light
    // (and again, twice, by the environment).
    dot_nv: f32,
    dfg_v: vec2<f32>,
    f_avg: vec3<f32>,
};

/// three's `textureCubeUV(envMap, direction, roughness)`, on a cube whose
/// levels are prefiltered for evenly spaced roughness.
fn env_sample(direction: vec3<f32>, roughness: f32) -> vec3<f32> {
    return textureSampleLevel(env_cube, env_sampler, direction, roughness * (ENV_LEVELS - 1.0)).rgb;
}

/// The DFG table at (`roughness`, `cos_theta`): scale in x, bias in y.
fn dfg(roughness: f32, cos_theta: f32) -> vec2<f32> {
    return textureSampleLevel(dfg_lut, env_sampler, vec2<f32>(roughness, cos_theta), 0.0).rg;
}

fn f_schlick(f0: vec3<f32>, f90: f32, dot_vh: f32) -> vec3<f32> {
    let fresnel = exp2((-5.55473 * dot_vh - 6.98316) * dot_vh);
    return f0 * (1.0 - fresnel) + f90 * fresnel;
}

fn v_ggx_smith_correlated(alpha: f32, dot_nl: f32, dot_nv: f32) -> f32 {
    let a2 = alpha * alpha;
    let gv = dot_nl * sqrt(a2 + (1.0 - a2) * dot_nv * dot_nv);
    let gl = dot_nv * sqrt(a2 + (1.0 - a2) * dot_nl * dot_nl);
    return 0.5 / max(gv + gl, EPSILON);
}

fn d_ggx(alpha: f32, dot_nh: f32) -> f32 {
    let a2 = alpha * alpha;
    let denom = dot_nh * dot_nh * (a2 - 1.0) + 1.0;
    return RECIPROCAL_PI * a2 / (denom * denom);
}

/// `BRDF_GGX_Multiscatter`: the single-scattering lobe plus Turquin's
/// compensation for the energy it loses at high roughness.
fn brdf_ggx(l: vec3<f32>, v: vec3<f32>, n: vec3<f32>, s: PhysicalSurface) -> vec3<f32> {
    let alpha = s.roughness * s.roughness;
    let h = normalize(l + v);
    let dot_nl = saturate(dot(n, l));
    let dot_nv = saturate(dot(n, v));
    let dot_nh = saturate(dot(n, h));
    let dot_vh = saturate(dot(v, h));
    let single = f_schlick(s.specular, 1.0, dot_vh)
        * (v_ggx_smith_correlated(alpha, dot_nl, dot_nv) * d_ggx(alpha, dot_nh));
    let dfg_l = dfg(s.roughness, dot_nl);
    let fss_ess_v = s.specular * s.dfg_v.x + s.dfg_v.y;
    let fss_ess_l = s.specular * dfg_l.x + dfg_l.y;
    let ems_v = 1.0 - (s.dfg_v.x + s.dfg_v.y);
    let ems_l = 1.0 - (dfg_l.x + dfg_l.y);
    let f_avg = s.f_avg;
    let f_ms = fss_ess_v * fss_ess_l * f_avg / (1.0 - ems_v * ems_l * f_avg + EPSILON);
    return single + f_ms * (ems_v * ems_l);
}

/// `RE_Direct_Physical`: one light's diffuse and specular, from its colour at
/// the surface (`radiance`, already attenuated and shadowed).
fn direct_physical(l: vec3<f32>, radiance: vec3<f32>, n: vec3<f32>, v: vec3<f32>, s: PhysicalSurface) -> vec3<f32> {
    let irradiance = saturate(dot(n, l)) * radiance;
    return irradiance * (brdf_ggx(l, v, n, s) + s.contribution * RECIPROCAL_PI);
}

/// `computeMultiscattering`'s single-scattering output for one F0, from the
/// view's DFG texel (`fab`); `multiscatter_ms` is its second output.
fn multiscatter_ss(f0: vec3<f32>, fab: vec2<f32>) -> vec3<f32> {
    return f0 * fab.x + fab.y;
}

fn multiscatter_ms(f0: vec3<f32>, fab: vec2<f32>) -> vec3<f32> {
    let ems = 1.0 - (fab.x + fab.y);
    let f_avg = f0 + (1.0 - f0) * 0.047619;
    return multiscatter_ss(f0, fab) * f_avg / (1.0 - ems * f_avg) * ems;
}

/// A physical surface under the map's rig and environment: what the browser's
/// `MeshStandardMaterial` returns before tone mapping. `detail` gates the fill
/// as `shade` does; `shadow` attenuates only the sun.
fn shade_physical(
    albedo: vec3<f32>,
    metalness: f32,
    roughness: f32,
    normal: vec3<f32>,
    world_position: vec3<f32>,
    detail: f32,
    shadow: f32,
) -> vec3<f32> {
    let n = normalize(normal);
    let v = normalize(lights.eye.xyz - world_position);
    var s: PhysicalSurface;
    s.diffuse = albedo;
    s.contribution = albedo * (1.0 - metalness);
    s.specular = mix(vec3<f32>(0.04), albedo, metalness);
    s.metalness = metalness;
    s.roughness = roughness;
    s.dot_nv = saturate(dot(n, v));
    s.dfg_v = dfg(roughness, s.dot_nv);
    s.f_avg = s.specular + (1.0 - s.specular) * 0.047619;

    var color = direct_physical(
        lights.sun_dir.xyz,
        lights.sun_color.rgb * lights.sun_color.w * shadow,
        n, v, s,
    );
    if (detail >= 2.0) {
        color = color + direct_physical(lights.fill_dir.xyz, lights.fill_color.rgb * lights.fill_color.w, n, v, s);
    }
    let slots = point_slots();
    var low = slots.x;
    var high = slots.y;
    loop {
        var i: u32;
        if (low != 0u) {
            i = firstTrailingBit(low);
            low = low & (low - 1u);
        } else if (high != 0u) {
            i = 32u + firstTrailingBit(high);
            high = high & (high - 1u);
        } else {
            break;
        }
        let light = lights.point[i];
        let to_light = light.position_range.xyz - world_position;
        let distance = length(to_light);
        if (distance < light.position_range.w) {
            let radiance = light.color.rgb * point_attenuation(distance, light.position_range.w);
            color = color + direct_physical(to_light / max(distance, 1e-4), radiance, n, v, s);
        }
    }

    // The hemisphere: indirect diffuse (`RE_IndirectDiffuse_Physical`).
    let hemi = mix(lights.hemi_ground.rgb, lights.hemi_sky.rgb, n.y * 0.5 + 0.5) * lights.hemi_sky.w;
    color = color + hemi * s.contribution * RECIPROCAL_PI;

    // The environment (`getIBLRadiance`, `getIBLIrradiance`,
    // `RE_IndirectSpecular_Physical`).
    let r2 = s.roughness * s.roughness;
    let reflected = normalize(mix(reflect(-v, n), n, r2 * r2));
    let radiance = env_sample(reflected, s.roughness);
    let irradiance = PI * env_sample(n, 1.0);
    let dielectric_ss = multiscatter_ss(vec3<f32>(0.04), s.dfg_v);
    let dielectric_ms = multiscatter_ms(vec3<f32>(0.04), s.dfg_v);
    let single = mix(dielectric_ss, multiscatter_ss(s.diffuse, s.dfg_v), s.metalness);
    let multi = mix(dielectric_ms, multiscatter_ms(s.diffuse, s.dfg_v), s.metalness);
    let diffuse_ibl = s.contribution * (1.0 - (dielectric_ss + dielectric_ms));
    let cosine_weighted = irradiance * RECIPROCAL_PI;
    color = color + radiance * single + multi * cosine_weighted + diffuse_ibl * cosine_weighted;
    return color;
}

// ---------------------------------------------------------------------------
// Surface breakup: what stops a photographed floor reading as one tile stamped
// a thousand times. All of it is content — the same UVs, the same world
// positions, the same baked AO on every machine — so it is the same picture at
// every quality level and in both clients (`surface-breakup.ts` is the
// browser's copy; `browser_parity.rs` pins the numbers).

/// How often the anti-tiling's offset changes, per tile: a band every ~7 tiles.
const ANTI_TILE_FREQ: f32 = 0.15;
/// The macro tint's frequency in world units (a swell every ~22 cubes), and how
/// far it moves a surface's albedo either way.
const MACRO_FREQ: f32 = 0.045;
const MACRO_AMOUNT: f32 = 0.10;
/// Grime: full at or below this baked AO, none at or above the next, so it
/// gathers where the bake already found wall bases, corners and undersides.
const GRIME_AO_FULL: f32 = 0.55;
const GRIME_AO_NONE: f32 = 0.95;
const GRIME_STRENGTH: f32 = 0.55;
const GRIME_TINT: vec3<f32> = vec3<f32>(0.62, 0.55, 0.47);

/// Dave Hoskins' hash without sine: `sin` of a large argument is not the same
/// number on every GPU, and both clients have to land on the same pattern.
fn breakup_hash(p: vec2<f32>) -> f32 {
    var p3 = fract(vec3<f32>(p.x, p.y, p.x) * 0.1031);
    p3 = p3 + dot(p3, p3.yzx + 33.33);
    return fract((p3.x + p3.y) * p3.z);
}

/// Value noise over the hash, smoothstep-faded so the lattice does not show.
fn breakup_noise(p: vec2<f32>) -> f32 {
    let i = floor(p);
    let f = p - i;
    let w = f * f * (3.0 - 2.0 * f);
    let a = breakup_hash(i);
    let b = breakup_hash(i + vec2<f32>(1.0, 0.0));
    let c = breakup_hash(i + vec2<f32>(0.0, 1.0));
    let d = breakup_hash(i + vec2<f32>(1.0, 1.0));
    return mix(mix(a, b, w.x), mix(c, d, w.x), w.y);
}

/// Anti-tiling, Quílez's offset technique: a noise over the UV picks a band,
/// each band shifts the tile by its own offset, and a pixel blends the two
/// bands it falls between. Offsets only, never a rotation, so a normal map
/// sampled at the same offsets stays a valid tangent-space normal.
///
/// The weight is the band's fraction alone. Quilez also nudges it by how the
/// two samples differ, which bends the seam along the texture, but then the
/// weight never quite reaches 0 or 1 at a band's edge, where one offset jumps.
/// Saturated, the sample whose offset jumps has no weight there, so the
/// implicit derivatives' one-pixel smallest-mip line is invisible, and every
/// read stays a plain `textureSample` (`textureSampleGrad` here cost about
/// 1 ms on an Intel iGPU at 1080p).
struct AntiTile {
    off_a: vec2<f32>,
    off_b: vec2<f32>,
    w: f32,
}

fn anti_tile(uv: vec2<f32>) -> AntiTile {
    let k = breakup_noise(uv * ANTI_TILE_FREQ) * 8.0;
    let i = floor(k);
    return AntiTile(
        sin(vec2<f32>(3.0, 7.0) * i),
        sin(vec2<f32>(3.0, 7.0) * (i + 1.0)),
        smoothstep(0.2, 0.8, k - i),
    );
}

/// A tiled layer: at its two band offsets at High and Ultra (`detail` 2), one
/// plain read below. `anti` is uniform (the level), so this branch costs
/// nothing and the reads stay in uniform control flow per draw.
fn sample_tiled(uv: vec2<f32>, layer: u32, tile: AntiTile, anti: bool) -> vec4<f32> {
    if (anti) {
        return mix(
            textureSample(pbr_textures, pbr_sampler, uv + tile.off_a, i32(layer)),
            textureSample(pbr_textures, pbr_sampler, uv + tile.off_b, i32(layer)),
            tile.w,
        );
    }
    return textureSample(pbr_textures, pbr_sampler, uv, i32(layer));
}

/// The macro tint and the grime, on an albedo that already carries its AO.
///
/// Only High and Ultra (`detail` 2) draw the tint, as only they anti-tile:
/// both move a colour either way round its mean, so leaving them out hides
/// nothing, and on an integrated GPU together they cost Medium a fifth of its
/// frame rate. The grime darkens corners, so it is drawn at every level. A
/// level without it would light the corners a player hides in.
fn surface_breakup(albedo: vec3<f32>, world: vec3<f32>, ao: f32, macro_tint: bool) -> vec3<f32> {
    // World xz, leaned on by height so a wall varies up its face as well.
    let q = (world.xz + world.yy * vec2<f32>(0.37, 0.61)) * MACRO_FREQ;
    var tinted = albedo;
    if (macro_tint) {
        let swell = breakup_noise(q) * 0.65 + breakup_noise(q * 2.7) * 0.35;
        tinted = albedo * (1.0 + MACRO_AMOUNT * (swell * 2.0 - 1.0));
    }
    // Most of a map is unoccluded: only a corner pays for the grime's noise.
    if (ao >= GRIME_AO_NONE) {
        return tinted;
    }
    let patchy = 0.4 + 0.6 * smoothstep(0.2, 0.8, breakup_noise(q * 3.1 + vec2<f32>(17.0, 5.0)));
    let dirt = (1.0 - smoothstep(GRIME_AO_FULL, GRIME_AO_NONE, ao)) * patchy;
    return mix(tinted, tinted * GRIME_TINT, dirt * GRIME_STRENGTH);
}

@fragment
fn fs_main(in: VertexOut) -> @location(0) vec4<f32> {
    // Derivatives first, in uniform control flow: a photographed surface's normal
    // map needs them, and the `discard` and the material branch below are not.
    let dp_x = dpdx(in.world_position);
    let dp_y = dpdy(in.world_position);
    let duv_x = dpdx(in.uv);
    let duv_y = dpdy(in.uv);
    // three's `geometryRoughness`: how fast the unperturbed normal turns across
    // the pixel, added to a physical surface's roughness so a curve that is
    // smaller than a pixel does not sparkle.
    let dn = max(abs(dpdx(in.normal)), abs(dpdy(in.normal)));
    let geometry_roughness = max(max(dn.x, dn.y), dn.z);
    // A vertex that has not arrived is not drawn at all, so the world *builds*
    // rather than fading up. Discarded before any lighting work, both because
    // there is no point shading a fragment that is thrown away and because a
    // half-lit ghost of the finished map is exactly what the effect must not
    // look like.
    if (in.build <= 0.0) {
        discard;
    }
    let detail = camera.params.y;
    var albedo = in.color;
    // The AO attribute carries a glowing material's strength as `1 + strength`
    // (`world3d.rs`): the occlusion is what is at or under 1, the glow what is over.
    let ao = min(in.ao, 1.0);
    let glow = max(in.ao - 1.0, 0.0);
    // The shading normal: the face's, unless a photographed surface's map bends it.
    var normal = normalize(in.normal);
    // What the lighting is given: the interpolated normal as it always was, so no
    // surface but a photographed one changes by a rounding error.
    var shade_normal = in.normal;

    // Unlit: the muzzle flash, which the browser draws with `MeshBasicMaterial`.
    // It used to carry the *sun's* direction as its normal to land at full
    // brightness, which stopped being possible the moment the sun became a
    // property of the map — see `viewmodel::MATERIAL_UNLIT`.
    if (in.material < -0.5) {
        return vec4<f32>(expose(albedo), 1.0);
    }

    let mat_id = u32(round(in.material));
    // A modelled map's surface is physical (three's `MeshStandardMaterial`);
    // a cube map's stays Lambert.
    var roughness = 0.70;
    var metalness = 0.04;
    if (mat_id > 0u) {
        let layer_idx = mat_id - 1u;

        if (layer_idx == 10u || layer_idx == 11u) {
            // The site markings: a letter, so never anti-tiled.
            let pbr_sample = textureSample(pbr_textures, pbr_sampler, in.uv, i32(layer_idx));
            albedo = mix(albedo, pbr_sample.rgb, pbr_sample.a);
            roughness = 0.70;
            metalness = 0.04;
        } else if (layer_idx == 8u) {
            let pbr_sample = textureSample(pbr_textures, pbr_sampler, in.uv, i32(layer_idx));
            albedo = mix(albedo, pbr_sample.rgb, 0.45);
            roughness = 0.06;
            metalness = 0.15;
        } else {
            // Every tiled surface, sampled at two offsets and blended
            // (`anti_tile`), at High and Ultra.
            let anti = detail > 1.5;
            var tile = AntiTile(vec2<f32>(0.0, 0.0), vec2<f32>(0.0, 0.0), 0.0);
            if (anti) {
                tile = anti_tile(in.uv);
            }
            let pbr_sample = sample_tiled(in.uv, layer_idx, tile, anti);
            albedo = albedo * pbr_sample.rgb * 1.5;
            let props = get_pbr_properties(mat_id);
            roughness = props.x;
            metalness = props.y;
            if (layer_idx >= 18u && layer_idx < 38u) {
                // A photographed kind (`PHOTO_COUNT` of them, from layer 18):
                // its detail layer is `PHOTO_COUNT` on from its
                // albedo. Red and green are the normal's x and y (stored encoded
                // in an sRGB array, so sampled back exactly), blue is roughness.
                let d = sample_tiled(in.uv, layer_idx + 20u, tile, anti);
                let nxy = d.rg * 2.0 - vec2<f32>(1.0, 1.0);
                let nz = sqrt(max(1.0 - dot(nxy, nxy), 0.0));
                normal = perturb_normal(normal, dp_x, dp_y, duv_x, duv_y, vec3<f32>(nxy, nz));
                shade_normal = normal;
                roughness = d.b;
            } else if (layer_idx < 18u && detail > 0.5) {
                // Procedural generated surface detail layer (normal + micro-roughness at
                // `GENERATED_DETAIL_BASE` = 18 + 2 * PHOTO_COUNT = 58, + layer_idx)
                let d = sample_tiled(in.uv, layer_idx + 58u, tile, anti);
                let nxy = d.rg * 2.0 - vec2<f32>(1.0, 1.0);
                let nz = sqrt(max(1.0 - dot(nxy, nxy), 0.0));
                normal = perturb_normal(normal, dp_x, dp_y, duv_x, duv_y, vec3<f32>(nxy, nz));
                shade_normal = normal;
                roughness = d.b;
            }
        }

        // After the photograph is mixed in, as three multiplies a vertex colour
        // after its map, so the darkening survives layers 8, 10 and 11.
        albedo = albedo * ao;
        // Weathering and the macro tint, on everything but glass. A glowing
        // material carries an AO of 1 (`glow` is over it), so it gathers none.
        if (layer_idx != 8u) {
            albedo = surface_breakup(albedo, in.world_position, ao, detail > 1.5);
        }
        // three's floor and geometric widening (`lights_physical_fragment`):
        // 0.0525 is the sharpest level a 256 PMREM resolves.
        roughness = min(max(roughness, 0.0525) + geometry_roughness, 1.0);
    } else {
        // Fallback grain multiplier for legacy/untextured geometry
        var grain_uv = detail_uv(in.world_position, in.normal);
        if (camera.params.x <= 0.0001) {
            grain_uv = grain_uv * 0.25;
        }
        let grain = textureSample(
            detail_texture,
            detail_sampler,
            grain_uv,
        ).r;
        albedo = albedo * grain * DETAIL_GAIN * ao;
    }

    // A pass that does not receive the shadow is fully lit, not fully dark —
    // and it skips the compares rather than throwing them away.
    var occlusion = 1.0;
    if (camera.params.w > 0.5) {
        occlusion = sun_shadow(
            shadow_map,
            shadow_sampler,
            shadow.light_view_proj * vec4<f32>(in.world_position, 1.0),
            in.normal,
            shadow.params.x,
            shadow.params.y,
            shadow.params.z,
        );
    }
    point_mask = in.point_mask;
    var radiance: vec3<f32>;
    if (mat_id > 0u && detail >= 1.0) {
        radiance = shade_physical(albedo, metalness, roughness, shade_normal, in.world_position, detail, occlusion);
    } else {
        radiance = shade(albedo, shade_normal, in.world_position, detail, occlusion);
    }
    // Emitted light, as three's `totalEmissiveRadiance`: the emissive colour
    // times its strength, on top of what the surface reflects and under the
    // fog, at every quality level. At 4–10 it is well past white, which is
    // what the bloom's threshold is for.
    radiance = radiance + in.color * glow;
    let lit = expose(radiance);
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

    // Emissive radiance bloom: self-luminous elements (radioactive coolant, server telemetry screens, LEDs)
    // radiate optical radiance without tone-curve desaturation or shadow occlusion.
    let max_comp = max(in.color.r, max(in.color.g, in.color.b));
    if (max_comp > 1.0) {
        let bloom = in.color * (max_comp - 0.75) * 0.35;
        out_color = vec4<f32>(out_color.rgb + bloom, out_color.a);
    }
    return out_color;
}

// ---------------------------------------------------------------------------
// The scaling of the scene into the window — the blit that used to live here —
// is `post.wgsl`'s composite now, with bloom and sharpening on the way.
//
// The sky: drawn first into the main pass on a map open to it, where the flat
// clear colour used to be.
//
// The same fullscreen triangle as the blit, and a view ray per pixel recovered
// from the inverse view projection — the far plane's point for this pixel, less
// the eye. No depth write and no depth test: it is drawn before anything else,
// so everything after it simply lands on top.

struct SkyCamera {
    inverse_view_proj: mat4x4<f32>,
};
@group(0) @binding(0) var<uniform> sky_camera: SkyCamera;

struct SkyOut {
    @builtin(position) clip_position: vec4<f32>,
    @location(0) ndc: vec2<f32>,
};

@vertex
fn vs_sky(@builtin(vertex_index) index: u32) -> SkyOut {
    var out: SkyOut;
    let x = f32(i32(index) / 2) * 4.0 - 1.0;
    let y = f32(i32(index) & 1) * 4.0 - 1.0;
    out.clip_position = vec4<f32>(x, y, 0.0, 1.0);
    out.ndc = vec2<f32>(x, y);
    return out;
}

@fragment
fn fs_sky(in: SkyOut) -> @location(0) vec4<f32> {
    let far = sky_camera.inverse_view_proj * vec4<f32>(in.ndc, 1.0, 1.0);
    let direction = far.xyz / far.w - lights.eye.xyz;
    return vec4<f32>(expose(sky_radiance(direction)), 1.0);
}

// (The overlay — the HUD — has its own module in `overlay_gpu.rs`: it binds a
// glyph atlas, which nothing else here does.)

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

// The bullet tunnels through this frame's smokes. See `SmokeUniform`.
struct Smoke {
    head: vec4<f32>,
    channels: array<vec4<f32>, 16>,
};
@group(3) @binding(0) var<uniform> smoke: Smoke;

// How much of a cloud a tunnel has carved away along this view ray, 0..1.
//
// The ray, not the surface: a tunnel is a line of sight, and the question is
// the server's — does the line from the eye pass within the tunnel's radius of
// its centre (`grenades.sight_blocked_by`). Testing the cloud's *surface* point
// against the centre, which this used to be modelled on, only ever opened a
// hole for a shot that grazed the edge.
fn smoke_tunnel(world: vec3<f32>) -> f32 {
    let eye = lights.eye.xyz;
    let dir = normalize(world - eye);
    var carved = 0.0;
    let count = min(u32(smoke.head.x), 16u);
    for (var i = 0u; i < count; i = i + 1u) {
        let ch = smoke.channels[i];
        if (ch.w <= 0.001) {
            continue;
        }
        let t = max(dot(ch.xyz - eye, dir), 0.0);
        let d = length(ch.xyz - (eye + dir * t));
        carved = max(carved, smoothstep(ch.w, ch.w * 0.25, d));
    }
    return carved;
}

// ---------------------------------------------------------------------------
// Water: the map's environment reflected off a surface ruffled by three waves,
// by Schlick's fresnel. `water.ts` draws the same surface in the browser and
// `browser_parity.rs` pins the numbers.

/// Water's reflectance head-on (n = 1.33). Next to nothing: looking straight
/// down into a pool shows the pool.
const WATER_F0: f32 = 0.02;

/// The slope of the surface at `p` (render x, z) at time `t`: three travelling
/// waves, each `direction * slope * cos(phase)`. Their frequencies (0.25, 0.375
/// and 0.5 Hz) all divide 8 s, so the clock wraps every 64 s without a seam.
/// The waves only bend the normal: the plane does not move, because it is the
/// line a player judges a jump against (`water.rs`).
fn water_wave_slope(p: vec2<f32>, t: f32) -> vec2<f32> {
    let tau = 6.2831853;
    let d1 = vec2<f32>(0.80, 0.60);
    let d2 = vec2<f32>(-0.60, 0.80);
    let d3 = vec2<f32>(0.28, -0.96);
    return d1 * (0.045 * cos(tau * (dot(d1, p) / 4.0 - 0.25 * t)))
        + d2 * (0.035 * cos(tau * (dot(d2, p) / 2.3 - 0.375 * t)))
        + d3 * (0.025 * cos(tau * (dot(d3, p) / 1.1 - 0.5 * t)));
}

/// The map's environment in direction `d`: its served sky above, `hemiGround`
/// below, eased by `sqrt(|y|)` — `environment.rs` `radiance`, which the world's
/// prefiltered cube is built from, evaluated here at roughness 0.
fn environment_radiance(d: vec3<f32>) -> vec3<f32> {
    let far = select(lights.hemi_ground.rgb, lights.sky_zenith.rgb, d.y >= 0.0);
    return mix(lights.sky_horizon.rgb, far, sqrt(min(1.0, abs(d.y))));
}

fn water_surface(in: VolumeOut) -> vec4<f32> {
    // `smoke.head.y` is the water clock, wrapped at 64 s (`set_smoke_channels`).
    let slope = water_wave_slope(in.world_position.xz, smoke.head.y);
    var n = normalize(vec3<f32>(-slope.x, 1.0, -slope.y));
    // The underside, seen from in the pool, faces down.
    if (in.normal.y < 0.0) {
        n = -n;
    }
    let v = normalize(lights.eye.xyz - in.world_position);
    let fresnel = WATER_F0 + (1.0 - WATER_F0) * pow(1.0 - max(dot(n, v), 0.0), 5.0);
    // The body is the map's colour lit like any Lambert surface, as three lights
    // the browser's `MeshLambertMaterial` — but unshadowed: this pass has no
    // shadow map, and the browser's does.
    let body = shade(in.color.rgb, n, in.world_position, camera.params.y, 1.0);
    let rgb = mix(body, environment_radiance(reflect(-v, n)), fresnel);
    // Grazing, a surface is a mirror rather than a window.
    let alpha = mix(in.color.a, 1.0, fresnel);
    return vec4<f32>(apply_fog(expose(rgb), in.view_depth, camera.params.x), alpha);
}

@fragment
fn fs_volume(in: VolumeOut) -> @location(0) vec4<f32> {
    if (in.mode > 1.5) {
        return water_surface(in);
    }
    // Flat: a tracer is a beam a few centimetres across, and cloud noise across
    // it would eat the line entirely rather than texture it. Fogged like
    // everything else, so a tracer across a long hall still fades with distance.
    if (in.mode > 0.5) {
        // `params.x` is the fog **density** the world pass integrates, not a
        // distance to divide by — see `fog_amount`, which this pass got wrong
        // and which made everything here invisible past one cube.
        let far_fog = fog_amount(in.view_depth, camera.params.x);
        return vec4<f32>(expose(in.color.rgb), in.color.a * (1.0 - far_fog));
    }
    // Two octaves is enough at this scale and costs sixteen hashes; a third
    // buys nothing you can see through a cloud you are meant to be blinded by.
    let n = volume_noise(in.world_position * 0.9) * 0.65
        + volume_noise(in.world_position * 2.3) * 0.35;

    // View-ray chord thickness through spherical cloud volume:
    let norm_clip = camera.view_proj * vec4<f32>(in.normal, 0.0);
    let chord = clamp(abs(norm_clip.w), 0.0, 1.0);

    // CS2-style volumetric smoke:
    // Core area (chord > 0.38) is completely opaque (alpha = 1.0),
    // strictly preventing anything behind it from being seen.
    // Opacity smoothly rolls off toward the soft boundary edge.
    let core_density = smoothstep(0.04, 0.42, chord);
    var density = clamp(core_density * 1.3 + (n - 0.5) * 0.35, 0.0, 1.0);
    if (chord > 0.38) {
        density = 1.0;
    }
    density = density * (1.0 - smoke_tunnel(in.world_position) * 0.95);

    let shaded = in.color.rgb * mix(0.82, 1.14, n * 0.7 + chord * 0.3);
    var alpha = in.color.a * density;
    // Fade the last stretch into the fog, so a cloud at the edge of sight does
    // not sit as a hard disc against the haze.
    let fog = fog_amount(in.view_depth, camera.params.x);
    alpha = alpha * (1.0 - fog);
    return vec4<f32>(expose(shaded), alpha);
}
