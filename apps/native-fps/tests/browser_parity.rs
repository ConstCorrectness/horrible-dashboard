//! The constants this client copies out of the browser client.
//!
//! Several numbers here are duplicates by design — a hex colour, a fog density,
//! a light direction — and the reason they are duplicated is always the same:
//! this is presentation, and a drift makes the game look slightly different
//! rather than play differently, so a shared source of truth on the wire would
//! cost more than it saves. What that trade buys is a *silent* failure mode, and
//! this file is the price of it.
//!
//! Every case reads the **browser's own source** rather than a second copy of
//! the expected value. Asserting against a literal typed in here would pin the
//! two clients to a number that was true on the day somebody wrote the test; it
//! would not notice the browser moving, which is the only direction drift has
//! ever come from.
//!
//! The history each case pins is real:
//!
//! - The view model's palette was deliberately brightened for a renderer with
//!   "one ambient floor and a single directional wash". That renderer stopped
//!   existing when the browser's light rig was ported and the view model started
//!   being drawn with the world pipeline; the compensation stayed, and the
//!   weapon was brightened twice.
//! - `viewmodel.rs`'s `LIGHT_DIR` — the normal the muzzle flash carries so it
//!   comes out unlit — was still pointing where that single wash used to come
//!   from, which dimmed every flash by the cosine between the two.
//! - The fog was a linear ramp against the browser's exponential-squared one.

/// The browser's weapon view model.
const VIEWMODEL_TS: &str = include_str!("../../../packages/core/src/modules/hassault/viewmodel.ts");
/// The browser's renderer, scene and lights.
const PANEL_TSX: &str =
    include_str!("../../../packages/core/src/modules/hassault/HorribleAssaultPanel.tsx");
/// This client's shared lighting, which the two shaders are built from.
const LIGHTING_WGSL: &str = include_str!("../src/lighting.wgsl.inc");
/// This client's weapon view model.
const VIEWMODEL_RS: &str = include_str!("../src/viewmodel.rs");

/// The text between `after` and the next `until`, searched from the start.
fn between<'a>(haystack: &'a str, after: &str, until: &str) -> &'a str {
    let start = haystack
        .find(after)
        .unwrap_or_else(|| panic!("no '{after}' in the source — did it move or get renamed?"))
        + after.len();
    let rest = &haystack[start..];
    let end = rest
        .find(until)
        .unwrap_or_else(|| panic!("'{after}' is not followed by '{until}'"));
    rest[..end].trim()
}

/// A `0xrrggbb` literal as three channels in 0..1.
fn hex_channels(hex: &str) -> [f32; 3] {
    let digits = hex.trim().trim_start_matches("0x");
    let value = u32::from_str_radix(digits, 16).unwrap_or_else(|_| panic!("not hex: {hex:?}"));
    [
        ((value >> 16) & 0xff) as f32 / 255.0,
        ((value >> 8) & 0xff) as f32 / 255.0,
        (value & 0xff) as f32 / 255.0,
    ]
}

/// The sRGB transfer function's inverse, as `srgb_to_linear` in the shader.
fn srgb_to_linear(c: f32) -> f32 {
    if c <= 0.04045 {
        c / 12.92
    } else {
        ((c + 0.055) / 1.055).powf(2.4)
    }
}

/// The three floats of a `vec3<f32>(a, b, c)` constant named `name`.
fn wgsl_vec3(name: &str) -> [f32; 3] {
    let body = between(
        LIGHTING_WGSL,
        &format!("const {name}: vec3<f32> = vec3<f32>("),
        ")",
    );
    let parts: Vec<f32> = body
        .split(',')
        .map(|p| p.trim().parse().expect("a float"))
        .collect();
    assert_eq!(parts.len(), 3, "{name} is not three components");
    [parts[0], parts[1], parts[2]]
}

/// The three floats of a `const NAME: [f32; 3] = [a, b, c];` in `viewmodel.rs`.
fn rust_vec3(name: &str) -> [f32; 3] {
    let body = between(VIEWMODEL_RS, &format!("const {name}: [f32; 3] = ["), "]");
    let parts: Vec<f32> = body
        .split(',')
        .map(|p| p.trim().parse().expect("a float"))
        .collect();
    assert_eq!(parts.len(), 3, "{name} is not three components");
    [parts[0], parts[1], parts[2]]
}

fn assert_close(what: &str, got: [f32; 3], want: [f32; 3], tolerance: f32) {
    for i in 0..3 {
        assert!(
            (got[i] - want[i]).abs() <= tolerance,
            "{what}: component {i} is {} and the browser's is {} \
             (whole value {got:?} against {want:?})",
            got[i],
            want[i],
        );
    }
}

#[test]
fn the_unskinned_weapon_is_the_browsers_palette() {
    // A quantisation tolerance and nothing more: the Rust side spells the
    // channels as decimals, so it can be a half-bit out and no further.
    let tolerance = 0.5 / 255.0;
    let block = between(VIEWMODEL_TS, "const DEFAULT_PALETTE = {", "};");
    for (field, constant) in [
        ("body", "METAL"),
        ("dark", "DARK"),
        ("grip", "GRIP"),
        ("accent", "ACCENT"),
    ] {
        let hex = between(block, &format!("{field}: "), ",");
        assert_close(
            &format!("{constant} against DEFAULT_PALETTE.{field}"),
            rust_vec3(constant),
            hex_channels(hex),
            tolerance,
        );
    }
}

#[test]
fn the_muzzle_flash_faces_the_sun_the_shader_actually_uses() {
    // Not "close enough to look lit": the flash carries this as its normal
    // precisely so `dot(n, SUN_DIR)` comes out at 1, and any angle at all
    // between the two takes brightness off it for no visible reason.
    assert_close(
        "viewmodel.rs's LIGHT_DIR against lighting.wgsl.inc's SUN_DIR",
        rust_vec3("LIGHT_DIR"),
        wgsl_vec3("SUN_DIR"),
        1e-6,
    );
}

#[test]
fn high_quality_fog_is_exactly_the_browsers() {
    // The lower levels are free to trade distance for fill rate. High is not: it
    // is the level at which the two clients are meant to be the same picture.
    let density: f32 = between(PANEL_TSX, "new THREE.FogExp2(HORIZON, ", ")")
        .parse()
        .expect("a float");
    assert_eq!(
        hassault_native::settings::Quality::High.fog_density(),
        density,
        "the browser's FogExp2 density"
    );
}

#[test]
fn the_fog_colour_is_the_browsers_horizon_decoded() {
    // Decoded, because this one is applied before an sRGB target encodes it.
    // Handing the shader the raw hex is the mistake that makes the far end of a
    // corridor three shades too pale, and it looks like the density is wrong.
    let hex = between(PANEL_TSX, "const HORIZON = ", ";");
    let want = hex_channels(hex).map(srgb_to_linear);
    // Looser than the palette's: the shader spells these to four decimals, which
    // is coarse against values this near black.
    assert_close(
        "lighting.wgsl.inc's FOG_COLOR against the browser's HORIZON",
        wgsl_vec3("FOG_COLOR"),
        want,
        5e-4,
    );
}

// -- weapon props --------------------------------------------------------------

/// The browser's list of which weapons have a prop.
const WEAPON_MODELS_TS: &str =
    include_str!("../../../packages/core/src/modules/hassault/models/weapons.ts");

#[test]
fn both_clients_agree_which_weapons_have_a_prop() {
    // A weapon with a prop on one side and boxes on the other is the exact
    // divergence this file exists for, and it is invisible from either client
    // alone: each renders something that looks deliberate. It would show up as
    // two players describing the same gun differently.
    let block = between(WEAPON_MODELS_TS, "export const WEAPON_MODEL_URLS", "};");
    let mut browser: Vec<String> = Vec::new();
    for line in block.lines() {
        let line = line.trim();
        let Some((name, _)) = line.split_once(':') else {
            continue;
        };
        let name = name.trim();
        if name.is_empty() || name.starts_with('/') || name.starts_with('*') {
            continue;
        }
        browser.push(name.to_string());
    }
    browser.sort();

    let mut native: Vec<String> = hassault_native::prop::WEAPON_GLBS
        .iter()
        .map(|(name, _)| (*name).to_string())
        .collect();
    native.sort();

    assert_eq!(
        native, browser,
        "the two clients disagree about which weapons have a prop"
    );
}

#[test]
fn every_prop_this_client_ships_actually_parses() {
    // `include_bytes!` proves the file existed at build time and nothing more.
    // A truncated or mis-converted GLB compiles in perfectly and fails on the
    // frame the weapon is first drawn, which is mid-match.
    for (name, bytes) in hassault_native::prop::WEAPON_GLBS {
        let prop = hassault_native::prop::Prop::from_slice(bytes)
            .unwrap_or_else(|e| panic!("the compiled-in '{name}' prop does not parse: {e}"));
        assert!(!prop.vertices.is_empty(), "{name} has no geometry");
        assert!(
            prop.materials
                .iter()
                .any(|m| m.base_color_texture.is_some()),
            "{name} has no base colour map, so it would render flat"
        );
    }
}
