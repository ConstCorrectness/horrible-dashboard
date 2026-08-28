//! The shaders compile.
//!
//! `cargo check` never looks at a `.wgsl` file: it is a string literal to the
//! compiler. A typo, a wrong type, an entry point that does not exist — none of
//! it fails a build. It fails at `create_render_pipeline`, on a machine with a
//! GPU, at the instant somebody launches the game, which is both the latest and
//! the least convenient moment it could.
//!
//! So the same source the renderer concatenates is parsed and validated here,
//! and every entry point a pipeline names is checked to actually be one. That is
//! not the whole of what a driver checks — vertex layouts and bind group types
//! are still only proven by running — but it catches the entire class of "the
//! shader does not compile", which is what a shader change usually breaks.

use naga::valid::{Capabilities, ValidationFlags, Validator};

/// Exactly what `Renderer::new` hands to `create_shader_module`.
fn source() -> String {
    format!(
        "{}{}",
        include_str!("../src/lighting.wgsl.inc"),
        include_str!("../src/shader.wgsl")
    )
}

/// And what `characters_gpu.rs` hands it: the same lighting, a different shader.
///
/// Covered because the shared half is shared: a change to `lighting.wgsl.inc`
/// that suits the world can break the character, and the world's own test would
/// pass while the operator pass failed on the first frame of the first match
/// anybody joined.
fn skin_source() -> String {
    format!(
        "{}{}",
        include_str!("../src/lighting.wgsl.inc"),
        include_str!("../src/skin.wgsl")
    )
}

/// And what `props_gpu.rs` hands it: the weapon in your hands.
fn prop_source() -> String {
    format!(
        "{}{}",
        include_str!("../src/lighting.wgsl.inc"),
        include_str!("../src/prop.wgsl")
    )
}

fn validate(what: &str, source: &str) {
    let module = naga::front::wgsl::parse_str(source)
        .unwrap_or_else(|e| panic!("{what} does not parse:\n{}", e.emit_to_string(source)));
    Validator::new(ValidationFlags::all(), Capabilities::empty())
        .validate(&module)
        .unwrap_or_else(|e| panic!("{what} does not validate: {e:?}"));
}

#[test]
fn the_world_shader_parses_and_validates() {
    validate("shader.wgsl", &source());
}

#[test]
fn the_character_shader_parses_and_validates() {
    validate("skin.wgsl", &skin_source());
}

#[test]
fn the_prop_shader_parses_and_validates() {
    validate("prop.wgsl", &prop_source());
}

#[test]
fn the_prop_shader_names_the_entry_points_its_pipeline_uses() {
    let module = naga::front::wgsl::parse_str(&prop_source()).expect("parses");
    let names: Vec<&str> = module
        .entry_points
        .iter()
        .map(|e| e.name.as_str())
        .collect();
    for wanted in ["vs_prop", "fs_prop"] {
        assert!(
            names.contains(&wanted),
            "no entry point '{wanted}'; the module has {names:?}"
        );
    }
}

#[test]
fn every_entry_point_a_pipeline_names_exists() {
    // The failure this catches is silent in a different way from a syntax error:
    // the module compiles, and only the pipeline that names the missing function
    // fails — so a renamed fragment shader takes out one pass and leaves the
    // rest of the frame looking fine.
    let module = naga::front::wgsl::parse_str(&source()).expect("parses");
    let names: Vec<&str> = module
        .entry_points
        .iter()
        .map(|e| e.name.as_str())
        .collect();
    for wanted in [
        "vs_main",
        "fs_main",
        "vs_volume",
        "fs_volume",
        "vs_overlay",
        "fs_overlay",
        "vs_blit",
        "fs_blit",
    ] {
        assert!(
            names.contains(&wanted),
            "no entry point '{wanted}'; the module has {names:?}"
        );
    }
}
