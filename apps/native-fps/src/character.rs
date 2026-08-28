//! The operator: one GLB, parsed once, posed per player.
//!
//! This is the native half of the character the browser pane got first. The
//! asset is the same file — `apps/web/public/hassault-operator.glb`, built by
//! `scripts/build_hassault_character.mjs` from a Mixamo-rigged character plus
//! the clip FBXs — carrying nine skinned meshes, one shared 34-bone skeleton and
//! all 23 animations.
//!
//! It replaces a rig of boxes parented into hand-written joints (`bodies.rs`),
//! which could only be *posed*, never deformed, so its motion had to be written
//! in trigonometry. What is drawn now is what the clips say.
//!
//! ## Why the asset is compiled in
//!
//! Every other read this client makes goes to the node over HTTP, on the
//! principle that the server owns the numbers. The character does not, because
//! it is not a number: it is a fixed asset that ships with the binary it belongs
//! to, and fetching it would make the native client refuse to draw players
//! whenever the web layout's static directory was not where it expected. The
//! game's own maps follow the same rule for the same reason — our content is
//! bundled, AssaultCube's never is.
//!
//! ## Two things that fail silently
//!
//! **Global transforms are computed for every node, not just the joints.** The
//! build script's scale-to-5.2-cubes may land on a node *above* the armature,
//! which is not itself a joint. Walking only the joint hierarchy therefore
//! misses it and produces a character of the wrong size with no error anywhere —
//! and "wrong size" here means the drawn body disagrees with the capsule a shot
//! is resolved against.
//!
//! **`JOINTS_0` is per-skin, not global.** Each of the nine primitives indexes
//! into *its own* skin's joint list, and those lists are different lengths (1,
//! 24, 18, 1, 3, 8, 10, 21, 1). Uploading them unremapped binds the shoes to the
//! spine's matrices; nothing errors, the character just turns inside out.

use std::collections::HashMap;

use glam::{Mat4, Quat, Vec3, Vec4};

use crate::clips::{bone_key, is_upper_body};

/// The built asset, compiled into the binary. See the module note.
pub const OPERATOR_GLB: &[u8] = include_bytes!("../../web/public/hassault-operator.glb");

/// Mixamo characters face **+Z** in model space; the renderer's forward for a
/// player at yaw 0 is derived in `bodies.rs` as `[-sin, cos]` of
/// `-yaw - FRAC_PI_2`. Solving the two against each other gives a model rotation
/// of `yaw + FRAC_PI_2` about the render-space Y axis.
///
/// It is a named constant rather than an inline literal because a character
/// facing exactly backwards is the single most likely thing to be wrong here,
/// and a reader needs somewhere to look.
pub const FACING_OFFSET: f32 = std::f32::consts::FRAC_PI_2;

/// A node's local transform, kept decomposed because that is what animation
/// channels write and what blending has to interpolate. Recomposing to a matrix
/// early would mean blending matrices, which does not interpolate rotation.
#[derive(Debug, Clone, Copy)]
pub struct Trs {
    pub translation: Vec3,
    pub rotation: Quat,
    pub scale: Vec3,
}

impl Trs {
    fn matrix(&self) -> Mat4 {
        Mat4::from_scale_rotation_translation(self.scale, self.rotation, self.translation)
    }
}

/// Which bones a layer is allowed to write.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Mask {
    /// The base locomotion layer: everything.
    All,
    /// An action layered over the legs — arms, chest and head only.
    UpperBody,
}

/// One animation channel: a keyframe track for one property of one node.
#[derive(Debug)]
struct Channel {
    node: usize,
    times: Vec<f32>,
    values: Values,
}

#[derive(Debug)]
enum Values {
    Translation(Vec<Vec3>),
    Rotation(Vec<Quat>),
    Scale(Vec<Vec3>),
}

/// One named animation.
#[derive(Debug)]
pub struct Clip {
    pub name: String,
    pub duration: f32,
    channels: Vec<Channel>,
}

/// A decoded base-colour map, kept as RGBA8 ready for upload.
pub struct TextureImage {
    pub width: u32,
    pub height: u32,
    pub rgba: Vec<u8>,
}

/// What a primitive needs at draw time.
pub struct MaterialDef {
    pub base_color_texture: Option<usize>,
    pub base_color_factor: Vec4,
    /// `MASK` materials discard below this; `OPAQUE` ones use 0.0 so one shader
    /// branch covers both without a second pipeline.
    pub alpha_cutoff: f32,
}

/// One drawable run of vertices, all sharing a material.
pub struct Primitive {
    pub first_vertex: u32,
    pub vertex_count: u32,
    pub material: usize,
}

/// A vertex as the skinning shader reads it.
///
/// `joints` is `u32x4` rather than the `u16x4` the GLB stores, because a
/// `Uint16x4` vertex attribute is not something every backend accepts as an
/// index into a storage array, and four extra bytes on a buffer uploaded exactly
/// once is not a cost worth an obscure portability bug.
#[repr(C)]
#[derive(Debug, Clone, Copy, bytemuck::Pod, bytemuck::Zeroable)]
pub struct SkinVertex {
    pub position: [f32; 3],
    pub normal: [f32; 3],
    pub uv: [f32; 2],
    pub joints: [u32; 4],
    pub weights: [f32; 4],
}

/// The parsed operator.
pub struct Operator {
    /// Rest pose, indexed by glTF node index.
    rest: Vec<Trs>,
    /// Parent of each node, `None` for a scene root.
    parents: Vec<Option<usize>>,
    /// Node indices, parents always before children.
    order: Vec<usize>,
    /// The global bone list: the union of every skin's joints.
    bone_nodes: Vec<usize>,
    inverse_bind: Vec<Mat4>,
    /// Sanitised bone name -> index into `bone_nodes`.
    bone_by_name: HashMap<String, usize>,
    /// Whether each *node* is one an upper-body layer may write.
    upper_body: Vec<bool>,

    pub vertices: Vec<SkinVertex>,
    pub primitives: Vec<Primitive>,
    pub materials: Vec<MaterialDef>,
    pub textures: Vec<TextureImage>,
    clips: HashMap<String, Clip>,
}

impl Operator {
    /// Parse the compiled-in asset.
    pub fn load() -> Result<Operator, String> {
        Operator::from_slice(OPERATOR_GLB)
    }

    pub fn from_slice(bytes: &[u8]) -> Result<Operator, String> {
        let normalised = normalise_glb(bytes)?;
        let gltf = gltf::Gltf::from_slice(&normalised).map_err(|e| format!("operator GLB: {e}"))?;
        let blob = gltf.blob.as_deref();
        let doc = &gltf.document;
        let buffer = |b: gltf::Buffer| -> Option<&[u8]> {
            match b.source() {
                gltf::buffer::Source::Bin => blob,
                // An external .bin would mean the build script stopped producing
                // a self-contained GLB, which is a build bug rather than
                // something to paper over at runtime.
                gltf::buffer::Source::Uri(_) => None,
            }
        };

        let nodes: Vec<gltf::Node> = doc.nodes().collect();
        let node_count = nodes.len();
        let mut rest = vec![
            Trs {
                translation: Vec3::ZERO,
                rotation: Quat::IDENTITY,
                scale: Vec3::ONE,
            };
            node_count
        ];
        let mut parents: Vec<Option<usize>> = vec![None; node_count];
        for node in &nodes {
            let (t, r, s) = node.transform().decomposed();
            rest[node.index()] = Trs {
                translation: Vec3::from(t),
                rotation: Quat::from_array(r),
                scale: Vec3::from(s),
            };
            for child in node.children() {
                parents[child.index()] = Some(node.index());
            }
        }

        // Topological order. Walking the scene roots rather than sorting means a
        // node orphaned from every scene is simply never posed, which is what
        // should happen to it.
        let mut order = Vec::with_capacity(node_count);
        let mut stack: Vec<usize> = doc
            .scenes()
            .flat_map(|s| s.nodes().map(|n| n.index()))
            .collect();
        stack.reverse();
        while let Some(index) = stack.pop() {
            order.push(index);
            for child in nodes[index].children() {
                stack.push(child.index());
            }
        }

        // --- bones -------------------------------------------------------
        // The union of all nine skins' joints. First inverse bind wins: a node
        // shared between skins carries the same bind pose in both, and if it
        // ever did not, the skins would disagree about the same skeleton.
        let mut bone_nodes: Vec<usize> = Vec::new();
        let mut inverse_bind: Vec<Mat4> = Vec::new();
        let mut bone_of_node: HashMap<usize, usize> = HashMap::new();
        // Per skin, the map from that skin's local joint index to the global one.
        let mut skin_remap: Vec<Vec<u32>> = Vec::new();
        for skin in doc.skins() {
            let reader = skin.reader(buffer);
            let ibms: Vec<Mat4> = match reader.read_inverse_bind_matrices() {
                Some(iter) => iter.map(|m| Mat4::from_cols_array_2d(&m)).collect(),
                None => Vec::new(),
            };
            let mut remap = Vec::new();
            for (local, joint) in skin.joints().enumerate() {
                let node = joint.index();
                let global = *bone_of_node.entry(node).or_insert_with(|| {
                    bone_nodes.push(node);
                    inverse_bind.push(ibms.get(local).copied().unwrap_or(Mat4::IDENTITY));
                    bone_nodes.len() - 1
                });
                remap.push(global as u32);
            }
            skin_remap.push(remap);
        }

        let mut bone_by_name: HashMap<String, usize> = HashMap::new();
        for (index, &node) in bone_nodes.iter().enumerate() {
            let name = nodes[node].name().unwrap_or_default();
            // First wins, matching the browser: glTF export uniquifies repeated
            // names with `_1`, and the unsuffixed one is the real joint.
            bone_by_name
                .entry(bone_key(name).to_string())
                .or_insert(index);
        }

        let upper_body: Vec<bool> = nodes
            .iter()
            .map(|n| is_upper_body(n.name().unwrap_or_default()))
            .collect();

        // --- geometry ----------------------------------------------------
        let mut vertices: Vec<SkinVertex> = Vec::new();
        let mut primitives: Vec<Primitive> = Vec::new();
        for node in &nodes {
            let (Some(mesh), Some(skin)) = (node.mesh(), node.skin()) else {
                continue;
            };
            let remap = &skin_remap[skin.index()];
            for prim in mesh.primitives() {
                let reader = prim.reader(buffer);
                let Some(positions) = reader.read_positions() else {
                    continue;
                };
                let positions: Vec<[f32; 3]> = positions.collect();
                let normals: Vec<[f32; 3]> = reader
                    .read_normals()
                    .map(|n| n.collect())
                    .unwrap_or_else(|| vec![[0.0, 1.0, 0.0]; positions.len()]);
                let uvs: Vec<[f32; 2]> = reader
                    .read_tex_coords(0)
                    .map(|t| t.into_f32().collect())
                    .unwrap_or_else(|| vec![[0.0, 0.0]; positions.len()]);
                let joints: Vec<[u16; 4]> = reader
                    .read_joints(0)
                    .map(|j| j.into_u16().collect())
                    .unwrap_or_else(|| vec![[0; 4]; positions.len()]);
                let weights: Vec<[f32; 4]> = reader
                    .read_weights(0)
                    .map(|w| w.into_f32().collect())
                    .unwrap_or_else(|| vec![[1.0, 0.0, 0.0, 0.0]; positions.len()]);

                // Expand indices here rather than carrying an index buffer. The
                // asset arrives non-indexed anyway (90,093 vertices, 0 indices),
                // so an index path would be code that never runs on the one file
                // it exists for.
                let indices: Vec<u32> = match reader.read_indices() {
                    Some(read) => read.into_u32().collect(),
                    None => (0..positions.len() as u32).collect(),
                };

                let first_vertex = vertices.len() as u32;
                for &i in &indices {
                    let i = i as usize;
                    if i >= positions.len() {
                        continue;
                    }
                    let j = joints.get(i).copied().unwrap_or([0; 4]);
                    vertices.push(SkinVertex {
                        position: positions[i],
                        normal: normals.get(i).copied().unwrap_or([0.0, 1.0, 0.0]),
                        uv: uvs.get(i).copied().unwrap_or([0.0, 0.0]),
                        joints: [
                            remap.get(j[0] as usize).copied().unwrap_or(0),
                            remap.get(j[1] as usize).copied().unwrap_or(0),
                            remap.get(j[2] as usize).copied().unwrap_or(0),
                            remap.get(j[3] as usize).copied().unwrap_or(0),
                        ],
                        weights: weights.get(i).copied().unwrap_or([1.0, 0.0, 0.0, 0.0]),
                    });
                }
                primitives.push(Primitive {
                    first_vertex,
                    vertex_count: vertices.len() as u32 - first_vertex,
                    material: prim.material().index().unwrap_or(0),
                });
            }
        }

        // --- materials and textures --------------------------------------
        let mut textures = Vec::new();
        for image in doc.images() {
            textures.push(decode_image(&image, blob)?);
        }
        let mut materials = Vec::new();
        for material in doc.materials() {
            let pbr = material.pbr_metallic_roughness();
            materials.push(MaterialDef {
                base_color_texture: pbr.base_color_texture().map(|t| t.texture().index()),
                base_color_factor: Vec4::from(pbr.base_color_factor()),
                alpha_cutoff: match material.alpha_mode() {
                    gltf::material::AlphaMode::Mask => material.alpha_cutoff().unwrap_or(0.5),
                    _ => 0.0,
                },
            });
        }
        if materials.is_empty() {
            materials.push(MaterialDef {
                base_color_texture: None,
                base_color_factor: Vec4::ONE,
                alpha_cutoff: 0.0,
            });
        }

        // --- animations --------------------------------------------------
        let mut clips = HashMap::new();
        for animation in doc.animations() {
            let name = animation.name().unwrap_or_default().to_string();
            if name.is_empty() {
                continue;
            }
            let mut channels = Vec::new();
            let mut duration: f32 = 0.0;
            for channel in animation.channels() {
                let reader = channel.reader(buffer);
                let Some(times) = reader.read_inputs() else {
                    continue;
                };
                let times: Vec<f32> = times.collect();
                if let Some(&last) = times.last() {
                    duration = duration.max(last);
                }
                let Some(outputs) = reader.read_outputs() else {
                    continue;
                };
                use gltf::animation::util::ReadOutputs;
                let values = match outputs {
                    ReadOutputs::Translations(t) => {
                        Values::Translation(t.map(Vec3::from).collect())
                    }
                    ReadOutputs::Rotations(r) => {
                        Values::Rotation(r.into_f32().map(Quat::from_array).collect())
                    }
                    ReadOutputs::Scales(s) => Values::Scale(s.map(Vec3::from).collect()),
                    // Morph targets: the operator has none, and a weight track
                    // has no bone to write to.
                    ReadOutputs::MorphTargetWeights(_) => continue,
                };
                channels.push(Channel {
                    node: channel.target().node().index(),
                    times,
                    values,
                });
            }
            clips.insert(
                name.clone(),
                Clip {
                    name,
                    duration,
                    channels,
                },
            );
        }

        Ok(Operator {
            rest,
            parents,
            order,
            bone_nodes,
            inverse_bind,
            bone_by_name,
            upper_body,
            vertices,
            primitives,
            materials,
            textures,
            clips,
        })
    }

    pub fn bone_count(&self) -> usize {
        self.bone_nodes.len()
    }

    pub fn node_count(&self) -> usize {
        self.rest.len()
    }

    pub fn clip(&self, name: &str) -> Option<&Clip> {
        self.clips.get(name)
    }

    pub fn clip_names(&self) -> impl Iterator<Item = &str> {
        self.clips.values().map(|c| c.name.as_str())
    }

    /// The node index of a bone, by its sanitised Mixamo name.
    pub fn bone_node(&self, name: &str) -> Option<usize> {
        self.bone_by_name.get(name).map(|&i| self.bone_nodes[i])
    }
}

/// One player's evaluated pose.
///
/// Held per player and reused every frame rather than allocated: eight of these
/// is nothing, but allocating them inside the frame loop is a per-frame
/// allocation in the one place that cannot afford a hitch.
pub struct Pose {
    locals: Vec<Trs>,
    globals: Vec<Mat4>,
}

impl Pose {
    pub fn new(op: &Operator) -> Pose {
        Pose {
            locals: op.rest.clone(),
            globals: vec![Mat4::IDENTITY; op.rest.len()],
        }
    }

    /// Start the frame from the rest pose.
    ///
    /// Necessary rather than tidy: a clip animates only the nodes it has
    /// channels for, so a pose carried over from last frame keeps the *previous*
    /// clip's values on every node the new clip is silent about — which is how a
    /// character ends up with one arm from a reload and the rest from a walk.
    pub fn reset(&mut self, op: &Operator) {
        self.locals.copy_from_slice(&op.rest);
    }

    /// Blend a clip into the pose at `weight`, restricted to `mask`.
    ///
    /// A weight of 1 overwrites, which is what the base layer and a fully faded
    /// overlay both want; anything between is the crossfade.
    pub fn blend(&mut self, op: &Operator, clip: &Clip, time: f32, weight: f32, mask: Mask) {
        if weight <= 0.0 {
            return;
        }
        let weight = weight.min(1.0);
        for channel in &clip.channels {
            if channel.node >= self.locals.len() {
                continue;
            }
            if mask == Mask::UpperBody && !op.upper_body[channel.node] {
                continue;
            }
            let target = &mut self.locals[channel.node];
            match &channel.values {
                Values::Translation(values) => {
                    if let Some(v) = sample_vec3(&channel.times, values, time) {
                        target.translation = target.translation.lerp(v, weight);
                    }
                }
                Values::Rotation(values) => {
                    if let Some(q) = sample_quat(&channel.times, values, time) {
                        target.rotation = target.rotation.slerp(q, weight);
                    }
                }
                Values::Scale(values) => {
                    if let Some(v) = sample_vec3(&channel.times, values, time) {
                        target.scale = target.scale.lerp(v, weight);
                    }
                }
            }
        }
    }

    /// Rotate one bone about its local X, on top of whatever the clips posed.
    ///
    /// This is the aim pitch, and it is a *delta* rather than a set so the
    /// clip's own spine motion survives. No clip knows the pitch — Mixamo's
    /// animations all look at the horizon — so without it an enemy shooting down
    /// at you from a balcony appears to be firing straight ahead, which misreads
    /// their attention entirely.
    ///
    /// Pre-multiplied, which is what the browser's `bone.rotation.x -= p` comes
    /// out as for three's default XYZ Euler order.
    pub fn rotate_bone_x(&mut self, op: &Operator, bone: &str, radians: f32) {
        if radians == 0.0 {
            return;
        }
        let Some(node) = op.bone_node(bone) else {
            return;
        };
        let local = &mut self.locals[node];
        local.rotation = Quat::from_rotation_x(radians) * local.rotation;
    }

    /// Resolve the pose to one skinning matrix per bone, in world space.
    ///
    /// `model` carries the player's position and facing, folded in here rather
    /// than passed to the shader separately: the shader already multiplies by a
    /// matrix per vertex, and a second one would be a second multiply on every
    /// vertex to save 64 bytes per player.
    pub fn skinning(&mut self, op: &Operator, model: Mat4, out: &mut [Mat4]) {
        for &node in &op.order {
            let local = self.locals[node].matrix();
            self.globals[node] = match op.parents[node] {
                Some(parent) => self.globals[parent] * local,
                None => local,
            };
        }
        for (slot, (&node, inverse)) in op.bone_nodes.iter().zip(op.inverse_bind.iter()).enumerate()
        {
            if slot >= out.len() {
                break;
            }
            out[slot] = model * self.globals[node] * *inverse;
        }
    }

    /// The world transform of one bone, for hanging a weapon prop off a hand.
    ///
    /// Only meaningful after `skinning` has run this frame — it reads the
    /// globals that call computes.
    pub fn bone_matrix(&self, op: &Operator, bone: &str, model: Mat4) -> Option<Mat4> {
        let node = op.bone_node(bone)?;
        Some(model * self.globals[node])
    }
}

/// Find the keyframe span containing `time` and how far through it we are.
///
/// Clamps at both ends rather than wrapping: looping is the caller's job,
/// because a one-shot clip must hold its last frame and a wrap here would
/// silently restart a death animation forever.
fn span(times: &[f32], time: f32) -> Option<(usize, usize, f32)> {
    if times.is_empty() {
        return None;
    }
    if times.len() == 1 || time <= times[0] {
        return Some((0, 0, 0.0));
    }
    let last = times.len() - 1;
    if time >= times[last] {
        return Some((last, last, 0.0));
    }
    // Keyframe times are sorted by the spec, so this is a binary search rather
    // than the linear scan a long clip would otherwise cost every frame.
    let upper = times.partition_point(|&t| t <= time).min(last);
    let lower = upper - 1;
    let range = times[upper] - times[lower];
    let t = if range > 0.0 {
        (time - times[lower]) / range
    } else {
        0.0
    };
    Some((lower, upper, t))
}

fn sample_vec3(times: &[f32], values: &[Vec3], time: f32) -> Option<Vec3> {
    let (a, b, t) = span(times, time)?;
    Some(values.get(a)?.lerp(*values.get(b)?, t))
}

fn sample_quat(times: &[f32], values: &[Quat], time: f32) -> Option<Quat> {
    let (a, b, t) = span(times, time)?;
    Some(values.get(a)?.slerp(*values.get(b)?, t))
}

/// GLB chunk types, from the spec's ASCII tags.
const CHUNK_JSON: u32 = 0x4E4F_534A;
const CHUNK_BIN: u32 = 0x004E_4942;

/// Fold `EXT_texture_webp` away so a stock glTF parser will read the file.
///
/// The operator's 14 maps are webp, which is not a core glTF image format, so
/// the exporter declares the extension in `extensionsRequired` and hangs the
/// image index off `textures[i].extensions.EXT_texture_webp.source` instead of
/// `textures[i].source`. A conforming parser must **refuse** a file whose
/// required extensions it does not implement, and the `gltf` crate does exactly
/// that — the whole asset fails to load over an image format we can in fact
/// decode.
///
/// So the extension is resolved here rather than worked around downstream:
/// promote each texture's webp source to the core `source` field and drop the
/// requirement. The alternative was re-encoding the shared asset to PNG, which
/// would inflate the file the browser also downloads to fix a parser limitation
/// that only exists on this side.
///
/// Deliberately narrow. Any *other* required extension is left in place and
/// still rejected: silently stripping requirements is how you end up rendering a
/// file wrong instead of failing to render it.
pub(crate) fn normalise_glb(bytes: &[u8]) -> Result<Vec<u8>, String> {
    if bytes.len() < 12 || &bytes[0..4] != b"glTF" {
        return Err("not a GLB: bad magic".into());
    }
    let read_u32 = |at: usize| -> u32 {
        u32::from_le_bytes([bytes[at], bytes[at + 1], bytes[at + 2], bytes[at + 3]])
    };

    let mut json: Option<&[u8]> = None;
    let mut bin: Option<&[u8]> = None;
    let mut at = 12;
    while at + 8 <= bytes.len() {
        let length = read_u32(at) as usize;
        let kind = read_u32(at + 4);
        let start = at + 8;
        let end = start
            .checked_add(length)
            .filter(|&e| e <= bytes.len())
            .ok_or("GLB chunk runs past the end of the file")?;
        match kind {
            CHUNK_JSON => json = Some(&bytes[start..end]),
            CHUNK_BIN => bin = Some(&bytes[start..end]),
            // Unknown chunks are to be ignored by the spec, not treated as an
            // error — they are how the format grows.
            _ => {}
        }
        // Chunks are 4-byte aligned; the padding is not counted in `length`.
        at = end + (4 - (length % 4)) % 4;
    }
    let json = json.ok_or("GLB has no JSON chunk")?;

    let mut doc: serde_json::Value =
        serde_json::from_slice(json).map_err(|e| format!("GLB JSON: {e}"))?;

    let mut changed = false;
    if let Some(textures) = doc.get_mut("textures").and_then(|t| t.as_array_mut()) {
        for texture in textures {
            let source = texture
                .get("extensions")
                .and_then(|e| e.get("EXT_texture_webp"))
                .and_then(|w| w.get("source"))
                .and_then(|s| s.as_u64());
            let Some(source) = source else { continue };
            texture["source"] = serde_json::json!(source);
            if let Some(extensions) = texture
                .get_mut("extensions")
                .and_then(|e| e.as_object_mut())
            {
                extensions.remove("EXT_texture_webp");
            }
            changed = true;
        }
    }
    if let Some(required) = doc
        .get_mut("extensionsRequired")
        .and_then(|r| r.as_array_mut())
    {
        let before = required.len();
        required.retain(|e| e.as_str() != Some("EXT_texture_webp"));
        changed |= required.len() != before;
    }
    if !changed {
        return Ok(bytes.to_vec());
    }

    let mut patched = serde_json::to_vec(&doc).map_err(|e| format!("GLB JSON: {e}"))?;
    // JSON chunks pad with spaces and binary chunks with zeros — not
    // interchangeable: a zero inside the JSON chunk is not whitespace and the
    // parser on the other side will choke on it.
    while patched.len() % 4 != 0 {
        patched.push(b' ');
    }

    let mut out = Vec::with_capacity(bytes.len() + patched.len());
    out.extend_from_slice(b"glTF");
    out.extend_from_slice(&2u32.to_le_bytes());
    out.extend_from_slice(&0u32.to_le_bytes()); // total length, back-filled below
    out.extend_from_slice(&(patched.len() as u32).to_le_bytes());
    out.extend_from_slice(&CHUNK_JSON.to_le_bytes());
    out.extend_from_slice(&patched);
    if let Some(bin) = bin {
        let mut padded = bin.len();
        while padded % 4 != 0 {
            padded += 1;
        }
        out.extend_from_slice(&(padded as u32).to_le_bytes());
        out.extend_from_slice(&CHUNK_BIN.to_le_bytes());
        out.extend_from_slice(bin);
        out.resize(out.len() + (padded - bin.len()), 0);
    }
    let total = out.len() as u32;
    out[8..12].copy_from_slice(&total.to_le_bytes());
    Ok(out)
}

/// Decode one glTF image to RGBA8.
pub(crate) fn decode_image(image: &gltf::Image, blob: Option<&[u8]>) -> Result<TextureImage, String> {
    let bytes: &[u8] = match image.source() {
        gltf::image::Source::View { view, .. } => {
            let blob = blob.ok_or("operator GLB has no binary chunk")?;
            let start = view.offset();
            blob.get(start..start + view.length())
                .ok_or("operator GLB image view is out of range")?
        }
        gltf::image::Source::Uri { .. } => {
            return Err("operator GLB references an external image".into())
        }
    };
    let decoded = image::load_from_memory(bytes)
        .map_err(|e| format!("operator texture: {e}"))?
        .to_rgba8();
    Ok(TextureImage {
        width: decoded.width(),
        height: decoded.height(),
        rgba: decoded.into_raw(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Parsing the real asset, because every interesting failure in this module
    /// is a property of *this file* rather than of glTF in general — a synthetic
    /// fixture would pass while the shipped character stayed in a T-pose.
    fn operator() -> Operator {
        Operator::load().expect("the compiled-in operator GLB should parse")
    }

    #[test]
    fn the_asset_carries_one_skeleton_and_every_clip() {
        let op = operator();
        assert_eq!(op.bone_count(), 34, "the nine skins share one 34-bone rig");
        for name in crate::clips::OPERATOR_CLIPS {
            assert!(
                op.clip(name).is_some(),
                "clip {name} is missing from the GLB"
            );
        }
        assert_eq!(op.clip_names().count(), crate::clips::OPERATOR_CLIPS.len());
    }

    #[test]
    fn every_vertex_indexes_a_bone_that_exists() {
        // The per-skin -> global joint remap is the thing most likely to be
        // wrong, and being wrong reads as a character turned inside out rather
        // than as an error. An out-of-range index would sample garbage matrices.
        let op = operator();
        let bones = op.bone_count() as u32;
        for v in &op.vertices {
            for j in v.joints {
                assert!(j < bones, "joint index {j} is outside the {bones}-bone rig");
            }
        }
    }

    #[test]
    fn the_bind_pose_stands_the_documented_height() {
        // 5.2 cubes is the canonical standing height a shot is resolved against.
        // If the scale lives above the armature and we walked only the joints,
        // this is the assertion that catches it.
        let op = operator();
        let mut pose = Pose::new(&op);
        let mut bones = vec![Mat4::IDENTITY; op.bone_count()];
        pose.skinning(&op, Mat4::IDENTITY, &mut bones);

        let (mut low, mut high) = (f32::MAX, f32::MIN);
        for v in &op.vertices {
            let p = Vec3::from(v.position);
            let mut skinned = Vec3::ZERO;
            for k in 0..4 {
                let w = v.weights[k];
                if w > 0.0 {
                    skinned += (bones[v.joints[k] as usize] * p.extend(1.0)).truncate() * w;
                }
            }
            low = low.min(skinned.y);
            high = high.max(skinned.y);
        }
        let height = high - low;
        assert!(
            (height - 5.2).abs() < 0.05,
            "bind pose is {height} cubes tall, expected 5.2"
        );
    }

    #[test]
    fn a_clip_moves_bones_off_the_rest_pose() {
        let op = operator();
        let clip = op.clip("standard_walk").expect("standard_walk");
        let mut rest_bones = vec![Mat4::IDENTITY; op.bone_count()];
        let mut posed_bones = vec![Mat4::IDENTITY; op.bone_count()];

        let mut pose = Pose::new(&op);
        pose.skinning(&op, Mat4::IDENTITY, &mut rest_bones);

        pose.reset(&op);
        pose.blend(&op, clip, 0.4, 1.0, Mask::All);
        pose.skinning(&op, Mat4::IDENTITY, &mut posed_bones);

        let moved = rest_bones
            .iter()
            .zip(posed_bones.iter())
            .filter(|(a, b)| (**a - **b).to_cols_array().iter().any(|d| d.abs() > 1e-4))
            .count();
        // The browser's own check on this asset reports 34/34 bones moving on
        // this clip; anything much lower means a channel target went unresolved.
        assert!(
            moved >= 30,
            "only {moved} of 34 bones moved on standard_walk"
        );
    }

    #[test]
    fn an_upper_body_layer_leaves_the_legs_alone() {
        // The whole layering scheme rests on the two masks being disjoint. If
        // `UpperBody` leaked onto a leg bone, a reload would visibly stop the
        // player walking — but only while reloading, which is easy to miss.
        let op = operator();
        let clip = op.clip("reloading").expect("reloading");
        let leg = op.bone_node("LeftUpLeg").expect("LeftUpLeg");
        let arm = op.bone_node("RightForeArm").expect("RightForeArm");

        let mut pose = Pose::new(&op);
        pose.reset(&op);
        pose.blend(&op, clip, 0.5, 1.0, Mask::UpperBody);

        assert!(
            pose.locals[leg]
                .rotation
                .abs_diff_eq(op.rest[leg].rotation, 1e-6),
            "an upper-body layer wrote a leg bone"
        );
        assert!(
            !pose.locals[arm]
                .rotation
                .abs_diff_eq(op.rest[arm].rotation, 1e-6),
            "an upper-body layer left the arm at rest"
        );
    }

    #[test]
    fn sampling_clamps_rather_than_wrapping() {
        let times = [0.0, 1.0, 2.0];
        assert_eq!(span(&times, -5.0), Some((0, 0, 0.0)));
        assert_eq!(span(&times, 99.0), Some((2, 2, 0.0)));
        let (a, b, t) = span(&times, 1.25).expect("inside the track");
        assert_eq!((a, b), (1, 2));
        assert!((t - 0.25).abs() < 1e-6);
    }
}
