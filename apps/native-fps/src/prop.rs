//! Static textured props: the weapon models, as this client reads them.
//!
//! The browser fetches `hassault-weapon-<id>.glb` and hands it to three. This
//! parses the same file into flat vertex arrays and RGBA images, the way
//! `character.rs` does for the operator — and it is a **separate parser rather
//! than a mode of that one**, because the two files differ in the one way that
//! decides how a mesh is placed:
//!
//! - The operator is **skinned**. Its vertex positions are in skin space and the
//!   node hierarchy reaches them through the skinning matrices, so the parser
//!   reads positions raw and is right to.
//! - A weapon is **not**. Its scale, rotation and offset — everything
//!   `build_hassault_weapon.mjs` computed — live in node transforms, and reading
//!   its positions raw yields a pistol thirty-five times too large sitting in
//!   the wrong place. There is no skeleton to carry them.
//!
//! So this walks the node tree and **bakes each node's world matrix into its
//! vertices**. That is the whole difference, and it is invisible if you get it
//! wrong in the direction of doing nothing: the GLB loads, the primitives are
//! there, the textures decode, and the weapon is simply enormous.
//!
//! Two things are shared with `character.rs` rather than reimplemented: folding
//! `EXT_texture_webp` away (these GLBs declare it **required**, so a stock
//! parser must refuse the file), and the decoded-image and material shapes the
//! renderer uploads.

use std::sync::mpsc::{self, Receiver};

use glam::{Mat4, Quat, Vec3, Vec4};

use crate::character::{decode_image, normalise_glb, MaterialDef, Primitive, TextureImage};

/// The weapon props this client ships, by the backend's weapon id.
///
/// Compiled in, like the operator GLB and for the same reasons: the native
/// client has no asset directory and fetching them would put three downloads
/// between deploying and having a weapon in your hands.
///
/// `knife` is **absent, and that is the answer rather than a gap** — there is no
/// knife model at all. A weapon with no entry keeps the procedural boxes, which
/// is what every weapon had before any of this. The M4A1 used to be absent too,
/// at 687k triangles — twenty times the whole map — and is here now because
/// `scripts/decimate_weapon.py` takes it to 30k. `models/weapons.ts` carries the
/// identical list for the browser.
pub const WEAPON_GLBS: &[(&str, &[u8])] = &[
    (
        "pistol",
        include_bytes!("../../web/public/hassault-weapon-pistol.glb"),
    ),
    (
        "assault",
        include_bytes!("../../web/public/hassault-weapon-assault.glb"),
    ),
    (
        "shotgun",
        include_bytes!("../../web/public/hassault-weapon-shotgun.glb"),
    ),
    (
        "sniper",
        include_bytes!("../../web/public/hassault-weapon-sniper.glb"),
    ),
];

/// The compiled-in GLB for a weapon, if it has one.
pub fn weapon_glb(id: &str) -> Option<&'static [u8]> {
    WEAPON_GLBS
        .iter()
        .find(|(name, _)| *name == id)
        .map(|(_, bytes)| *bytes)
}

/// Parse every compiled-in weapon prop on a background thread.
///
/// The parse is **57–110 ms per weapon** (measured, release build: the webp
/// textures are the bulk of it), and it used to run inside `sync_prop` on the
/// frame the player pressed a number key. Two presses of the same key paid it
/// twice, because only one prop was ever resident. That is the whole of the
/// "switching guns lags the game" report: a tenth of a second of frame thread,
/// on the one input that has to be instant in a firefight.
///
/// So it happens here instead, off the loop, starting the moment the client
/// comes up — the same decision `Squad::load` documents for the operator's
/// fourteen textures. The receiver hands each result to the frame loop, which
/// only pays for the upload; a weapon whose prop has not landed yet keeps its
/// boxes, which is what a weapon with no prop at all has always drawn.
pub fn preload() -> Receiver<(String, Result<Prop, String>)> {
    let (tx, rx) = mpsc::channel();
    std::thread::spawn(move || {
        for (id, bytes) in WEAPON_GLBS {
            // A closed receiver means the client is shutting down; there is no
            // point decoding the rest of the textures for nobody.
            if tx.send((id.to_string(), Prop::from_slice(bytes))).is_err() {
                return;
            }
        }
    });
    rx
}

/// A vertex of a static prop.
///
/// No joints and no weights, unlike `SkinVertex`. A prop could be pushed through
/// the character pipeline by giving every vertex one bone at identity, and it
/// would work — at the cost of eight bytes a vertex, a storage-buffer binding
/// and a bone array, on geometry that never deforms. The extra pipeline is
/// cheaper than the pretence.
#[repr(C)]
#[derive(Debug, Clone, Copy, bytemuck::Pod, bytemuck::Zeroable)]
pub struct PropVertex {
    pub position: [f32; 3],
    pub normal: [f32; 3],
    pub uv: [f32; 2],
}

/// One parsed weapon model.
pub struct Prop {
    pub vertices: Vec<PropVertex>,
    pub primitives: Vec<Primitive>,
    pub materials: Vec<MaterialDef>,
    pub textures: Vec<TextureImage>,
}

impl Prop {
    /// Parse a weapon GLB.
    pub fn from_slice(bytes: &[u8]) -> Result<Prop, String> {
        let normalised = normalise_glb(bytes)?;
        // `Gltf::from_slice`, not `import_slice`: the crate's `import` feature is
        // deliberately off, because it pulls the `image` crate in to decode
        // textures for us and it cannot read the webp these files carry. Buffers
        // are resolved from the binary chunk by hand, exactly as `character.rs`
        // does.
        let gltf = gltf::Gltf::from_slice(&normalised).map_err(|e| format!("weapon GLB: {e}"))?;
        let blob = gltf.blob.as_deref();
        let document = &gltf.document;

        let mut textures = Vec::new();
        for image in document.images() {
            textures.push(decode_image(&image, blob)?);
        }

        let mut materials = Vec::new();
        for material in document.materials() {
            let pbr = material.pbr_metallic_roughness();
            materials.push(MaterialDef {
                base_color_texture: pbr.base_color_texture().map(|t| t.texture().index()),
                base_color_factor: Vec4::from_array(pbr.base_color_factor()),
                // Props are opaque. The cutoff exists on the shared struct for
                // the operator's masked kit; carrying a value here would make a
                // weapon's own alpha channel — which its base-colour map is free
                // to contain anything in — start discarding pixels.
                alpha_cutoff: 0.0,
            });
        }

        let mut vertices = Vec::new();
        let mut primitives = Vec::new();

        // Every node in every scene, with its accumulated world transform. A
        // recursive walk rather than a flat pass over `document.nodes()`,
        // because a node's transform means nothing without its parents' — and
        // the converter puts the scale on one node and the mesh on its child.
        for scene in document.scenes() {
            for node in scene.nodes() {
                walk(&node, Mat4::IDENTITY, blob, &mut vertices, &mut primitives);
            }
        }

        if vertices.is_empty() {
            return Err("weapon glb has no geometry".into());
        }
        Ok(Prop {
            vertices,
            primitives,
            materials,
            textures,
        })
    }

    /// The model's bounding box, as `(min, max)`.
    ///
    /// Used to place the prop where the procedural weapon was, the same fit the
    /// browser does — see `fitWeaponModel` in `models/weapons.ts`. Measured here
    /// rather than carried in the file so the two clients derive it the same way
    /// from the same bytes.
    pub fn bounds(&self) -> (Vec3, Vec3) {
        let mut min = Vec3::splat(f32::INFINITY);
        let mut max = Vec3::splat(f32::NEG_INFINITY);
        for v in &self.vertices {
            let p = Vec3::from_array(v.position);
            min = min.min(p);
            max = max.max(p);
        }
        (min, max)
    }
}

/// One node and its children, with `parent` already applied.
fn walk(
    node: &gltf::Node,
    parent: Mat4,
    blob: Option<&[u8]>,
    vertices: &mut Vec<PropVertex>,
    primitives: &mut Vec<Primitive>,
) {
    let world = parent * local_matrix(node);

    if let Some(mesh) = node.mesh() {
        // The normal matrix is the inverse transpose, not the world matrix. They
        // agree only while the transform is a rigid motion with uniform scale —
        // which the converter's output happens to be — but a non-uniform scale
        // would tilt every normal off its surface, and the lighting would be
        // subtly, unaccountably wrong rather than broken.
        let normal_matrix = world.inverse().transpose();
        for prim in mesh.primitives() {
            // Resolved per primitive rather than through one shared closure:
            // the reader borrows the primitive, and a closure outliving it is a
            // lifetime knot for no gain.
            let reader = prim.reader(|b| match b.source() {
                gltf::buffer::Source::Bin => blob,
                // An external .bin means the build script stopped producing a
                // self-contained GLB — a build bug, not something to paper over
                // at runtime.
                gltf::buffer::Source::Uri(_) => None,
            });
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
            // Expanded rather than kept as an index buffer, matching the
            // operator: one draw path, and these meshes are a few thousand
            // triangles.
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
                let p = world.transform_point3(Vec3::from_array(positions[i]));
                let n = normal_matrix
                    .transform_vector3(Vec3::from_array(
                        normals.get(i).copied().unwrap_or([0.0, 1.0, 0.0]),
                    ))
                    .normalize_or_zero();
                vertices.push(PropVertex {
                    position: p.to_array(),
                    normal: n.to_array(),
                    uv: uvs.get(i).copied().unwrap_or([0.0, 0.0]),
                });
            }
            primitives.push(Primitive {
                first_vertex,
                vertex_count: vertices.len() as u32 - first_vertex,
                material: prim.material().index().unwrap_or(0),
            });
        }
    }

    for child in node.children() {
        walk(&child, world, blob, vertices, primitives);
    }
}

/// A node's own transform, however the file spells it.
///
/// glTF allows either a full matrix **or** a TRS triple, and a file may mix the
/// two across its nodes — `GLTFExporter` writes a matrix for a node carrying a
/// rotation and TRS for one that does not. Reading only TRS silently drops the
/// scale off every matrix node, which is exactly the failure this module exists
/// to avoid.
fn local_matrix(node: &gltf::Node) -> Mat4 {
    match node.transform() {
        gltf::scene::Transform::Matrix { matrix } => Mat4::from_cols_array_2d(&matrix),
        gltf::scene::Transform::Decomposed {
            translation,
            rotation,
            scale,
        } => Mat4::from_scale_rotation_translation(
            Vec3::from_array(scale),
            Quat::from_array(rotation),
            Vec3::from_array(translation),
        ),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The three weapon GLBs the build script produces, as the browser serves
    /// them. Read from disk rather than `include_bytes!` so this file does not
    /// compile 7 MB of art into every test binary — and skipped when they are
    /// absent, since they are build output and a fresh clone has not run the
    /// converter.
    fn weapon(name: &str) -> Option<Vec<u8>> {
        let path = concat!(env!("CARGO_MANIFEST_DIR"), "/../web/public/");
        std::fs::read(format!("{path}hassault-weapon-{name}.glb")).ok()
    }

    #[test]
    fn a_weapon_glb_parses_with_its_textures() {
        let Some(bytes) = weapon("pistol") else {
            eprintln!("skipping: run `pnpm build:hassault-weapon` first");
            return;
        };
        let prop = Prop::from_slice(&bytes).expect("the pistol parses");
        assert!(!prop.vertices.is_empty());
        assert!(!prop.primitives.is_empty());
        // Three maps: base colour, the packed metallic-roughness, and normals.
        // Only the first is uploaded, but all three are in the file and a parser
        // that choked on one it does not use would be refusing a valid asset.
        assert_eq!(prop.textures.len(), 3, "base colour, packed MR, normal");
        for texture in &prop.textures {
            assert!(texture.width > 0 && texture.height > 0);
            assert_eq!(
                texture.rgba.len(),
                (texture.width * texture.height * 4) as usize,
                "decoded to RGBA8"
            );
        }
        assert!(
            prop.materials
                .iter()
                .any(|m| m.base_color_texture.is_some()),
            "a weapon with no base colour map renders flat and reads as a lighting bug"
        );
    }

    #[test]
    fn node_transforms_are_baked_into_the_vertices() {
        // **The whole reason this parser exists.** The converter puts the scale
        // on a node, not on the vertices — a pistol's raw positions span about
        // 21 units and its node scale is ~0.028. Reading positions raw gives a
        // weapon thirty-five times too large, which loads, draws and is simply
        // enormous.
        let Some(bytes) = weapon("pistol") else {
            eprintln!("skipping: run `pnpm build:hassault-weapon` first");
            return;
        };
        let prop = Prop::from_slice(&bytes).expect("the pistol parses");
        let (min, max) = prop.bounds();
        let size = max - min;
        let longest = size.x.max(size.y).max(size.z);
        assert!(
            (longest - 0.6).abs() < 0.01,
            "the pistol should be 0.6 cubes along its longest axis, measured {longest}"
        );
    }

    #[test]
    fn every_weapon_points_down_negative_z_from_its_origin() {
        // The converter's contract, and the thing the view model's `HOME` and
        // muzzle offsets are expressed against. A model pointing the other way
        // renders a weapon held backwards, which looks like a pose bug rather
        // than an export one.
        for (name, length) in [
            ("pistol", 0.6f32),
            ("assault", 2.3),
            ("shotgun", 2.9),
            ("sniper", 2.72),
        ] {
            let Some(bytes) = weapon(name) else {
                eprintln!("skipping: run `pnpm build:hassault-weapon` first");
                return;
            };
            let prop = Prop::from_slice(&bytes).unwrap_or_else(|e| panic!("{name}: {e}"));
            let (min, max) = prop.bounds();
            assert!(
                max.z <= 0.001,
                "{name} extends to +z {} — the origin is not at the rear",
                max.z
            );
            assert!(
                (min.z + length).abs() < 0.02,
                "{name} should reach {} in -z, reached {}",
                -length,
                min.z
            );
        }
    }

    #[test]
    fn normals_survive_the_bake_as_unit_vectors() {
        // Transformed by the inverse transpose and renormalised. A normal left at
        // the source scale is ~0.028 long, and a shader normalising it recovers
        // the direction — but one that does not gets a surface lit at 3% of its
        // proper brightness, which reads as the texture being dark.
        let Some(bytes) = weapon("pistol") else {
            eprintln!("skipping: run `pnpm build:hassault-weapon` first");
            return;
        };
        let prop = Prop::from_slice(&bytes).expect("parses");
        for v in prop.vertices.iter().take(500) {
            let n = Vec3::from_array(v.normal).length();
            assert!((n - 1.0).abs() < 1e-3, "normal length {n}");
        }
    }

    #[test]
    fn a_file_that_is_not_a_glb_is_refused_rather_than_guessed_at() {
        assert!(Prop::from_slice(b"not a glb at all").is_err());
    }

    #[test]
    fn every_compiled_in_prop_is_parsed_off_the_frame_thread() {
        // The guard on the weapon-switch stall: if `preload` ever stops
        // delivering one of these, `sync_prop` draws that weapon as boxes
        // forever — silently, since boxes are also what a weapon with no model
        // draws. Blocking here is the test blocking, not the game: the point of
        // the receiver is that the frame loop never does.
        let rx = preload();
        let mut seen: Vec<String> = rx
            .iter()
            .map(|(id, parsed)| {
                assert!(parsed.is_ok(), "{id} did not parse");
                id
            })
            .collect();
        seen.sort();
        let mut expected: Vec<String> =
            WEAPON_GLBS.iter().map(|(id, _)| id.to_string()).collect();
        expected.sort();
        assert_eq!(seen, expected);
    }
}
