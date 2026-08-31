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

// -- bullet marks --------------------------------------------------------------

const DECALS_TS: &str = include_str!("../../../packages/core/src/modules/hassault/decals.ts");
const DECALS_RS: &str = include_str!("../src/decals.rs");
const TRACE_TS: &str = include_str!("../../../packages/core/src/modules/hassault/trace.ts");
const TRACE_RS: &str = include_str!("../src/trace.rs");

#[test]
fn both_clients_remember_the_same_number_of_marks_for_the_same_time() {
    // A wall of bullet holes is how a player reads their own spray pattern back.
    // If one client keeps 128 for 22 seconds and the other keeps 32 for 5, the
    // same magazine teaches two different things — and each client looks
    // perfectly reasonable on its own.
    let native_cap: u32 = between(DECALS_RS, "pub const MAX_MARKS: usize = ", ";")
        .trim()
        .parse()
        .expect("a mark cap");
    assert_eq!(
        native_cap,
        browser_f32(DECALS_TS, "DECAL_MAX").round() as u32,
        "how many marks the world remembers"
    );
    for (rust_name, ts_name) in [
        ("DECAL_LIFE", "DECAL_LIFE"),
        ("DECAL_FADE", "DECAL_FADE"),
        ("DECAL_LIFT", "DECAL_LIFT"),
        ("DECAL_SIZE", "DECAL_SIZE"),
    ] {
        assert_eq!(
            rust_f32(DECALS_RS, rust_name),
            browser_f32(DECALS_TS, ts_name),
            "{ts_name}"
        );
    }
}

#[test]
fn all_three_implementations_agree_on_the_six_face_normals() {
    // The index is on the wire, so its *meaning* is a contract between the
    // server and both clients. Get the order wrong and every mark lands on the
    // wrong face of the right cube, which is inside a wall about half the time
    // — invisible, and reported by nothing.
    let ts = between(TRACE_TS, "export const FACE_PX = ", "export const FACE_NONE");
    let rs = between(TRACE_RS, "pub const FACE_PX: i32 = ", "pub const FACE_NONE");
    for (i, name) in ["FACE_PX", "FACE_NX", "FACE_PY", "FACE_NY", "FACE_PZ", "FACE_NZ"]
        .iter()
        .enumerate()
    {
        if i == 0 {
            continue;
        }
        assert!(
            ts.contains(&format!("{name} = {i};")),
            "the browser does not give {name} the index {i}"
        );
        assert!(
            rs.contains(&format!("{name}: i32 = {i};")),
            "this client does not give {name} the index {i}"
        );
    }
    // And both agree a body hit is negative rather than a seventh face.
    assert!(TRACE_TS.contains("export const FACE_NONE = -1;"));
    assert!(TRACE_RS.contains("pub const FACE_NONE: i32 = -1;"));
}

// -- the first-person arms -----------------------------------------------------

const ARMS_TS: &str = include_str!("../../../packages/core/src/modules/hassault/arms.ts");
const ARMS_RS: &str = include_str!("../src/arms.rs");
const VIEWCLIPS_TS: &str =
    include_str!("../../../packages/core/src/modules/hassault/viewclips.ts");
const VIEWCLIPS_RS: &str = include_str!("../src/viewclips.rs");

#[test]
fn both_clients_read_the_same_grips_and_the_same_clips() {
    // **Not two copies compared — one file, read twice.** The browser imports
    // `models/grips.json` and this client `include_str!`s the very same bytes,
    // which is the only arrangement in which forty keyframes cannot drift. This
    // test exists to catch somebody replacing either read with a local table,
    // which is the change that would look like a harmless refactor.
    assert!(
        ARMS_TS.contains("from './models/grips.json'"),
        "the browser stopped reading the shared grips"
    );
    assert!(
        ARMS_RS.contains("models/grips.json"),
        "this client stopped reading the shared grips"
    );
    assert!(
        VIEWCLIPS_TS.contains("from './models/viewclips.json'"),
        "the browser stopped reading the shared clips"
    );
    assert!(
        VIEWCLIPS_RS.contains("models/viewclips.json"),
        "this client stopped reading the shared clips"
    );
}

#[test]
fn both_clients_build_an_arm_to_the_same_dimensions() {
    // A shoulder in a different place, or a forearm a different length, and the
    // hands reach the same grip from two different postures — one game, two
    // pairs of arms. Nothing errors; the two just look like different games.
    for (rust, ts) in [("UPPER_LEN", "UPPER_LEN"), ("LOWER_LEN", "LOWER_LEN")] {
        assert_eq!(
            rust_f32(ARMS_RS, rust),
            browser_f32(ARMS_TS, ts),
            "{ts}"
        );
    }
    // The shoulders are a `Vec3::new(..)` here and a tuple there, so they are
    // compared component by component rather than by a shared parser.
    for name in ["SHOULDER_R", "SHOULDER_L"] {
        let rs = between(ARMS_RS, &format!("pub const {name}: Vec3 = Vec3::new("), ")");
        let ts = between(ARMS_TS, &format!("export const {name}: Vec3 = ["), "]");
        let parse = |text: &str| -> Vec<f32> {
            text.split(',')
                .map(|p| p.trim().parse().expect("a float"))
                .collect()
        };
        assert_eq!(parse(rs), parse(ts), "{name}");
    }
}

#[test]
fn both_clients_time_the_hand_animations_the_same() {
    // A landing dip that lasts twice as long in one client is a different feel,
    // and feel is the whole reason these exist.
    for name in ["LAND_DURATION", "DRAW_DURATION", "THROW_DURATION"] {
        assert_eq!(
            rust_f32(VIEWCLIPS_RS, name),
            browser_f32(VIEWCLIPS_TS, name),
            "{name}"
        );
    }
}

#[test]
fn both_clients_layer_the_action_over_locomotion_rather_than_blending_it() {
    // The rule `clips.rs` already documents for the third-person rig: two poses
    // averaged on one bone give you half a reload, a motion belonging to neither
    // animation. A client that blended would play a visibly different reload
    // while agreeing about every number in the file.
    assert!(
        VIEWCLIPS_RS.contains("action.primary.or(self.primary)"),
        "this client blends rather than replaces"
    );
    assert!(
        VIEWCLIPS_TS.contains("{ ...base, ...action }"),
        "the browser blends rather than replaces"
    );
}

// -- the swap animation --------------------------------------------------------

#[test]
fn both_clients_stow_and_draw_on_the_same_numbers() {
    // **This had already drifted.** The native client grew `holster`, `stow`,
    // `DRAW_TIME` and a fraction-based reload dip; the browser kept a fixed-rate
    // exponential approach that neither reached the bottom on a fast reload nor
    // came back up on time on a slow one. Nothing caught it, because a weapon
    // animation looks plausible whatever it does — so the numbers are pinned now
    // that both clients have them.
    for (name, browser) in [
        ("HOLSTER_TIME", "HOLSTER_TIME"),
        ("DRAW_TIME", "DRAW_TIME"),
        ("HOLSTER_HOLD", "HOLSTER_HOLD"),
        ("RELOAD_DIP_IN", "RELOAD_DIP_IN"),
        ("RELOAD_DIP_OUT", "RELOAD_DIP_OUT"),
    ] {
        assert_eq!(
            rust_f32(VIEWMODEL_RS, name),
            browser_f32(VIEWMODEL_TS, browser),
            "{name}"
        );
    }
}

#[test]
fn both_clients_equip_a_grenade_rather_than_merely_readying_one() {
    // The design that lets the mouse mean throw and toss without taking the
    // right button away from the sniper's scope. A client where a number key
    // only *readied* would leave the two buttons doing something else entirely
    // while the other client threw — one game, two control schemes.
    assert!(
        UTILITY_RS.contains("pub fn equip(&mut self, slot: usize)"),
        "the native client has no equip"
    );
    assert!(
        UTILITY_TS.contains("equip(slot: number): void"),
        "the browser has no equip"
    );
    for source in [UTILITY_RS, UTILITY_TS] {
        assert!(
            source.contains("holster"),
            "a client can equip a grenade but never put it away"
        );
    }
}

const UTILITY_RS: &str = include_str!("../src/utility.rs");
const UTILITY_TS: &str = include_str!("../../../packages/core/src/modules/hassault/utility.ts");

// -- the muzzle flash ----------------------------------------------------------

/// The browser's flash, read at test time.
const FLASH_TS: &str = include_str!("../../../packages/core/src/modules/hassault/flash.ts");

/// One weapon's `{ radius: r, stretch: s }` out of `flash.ts`'s `SHAPES` table.
fn browser_flash_shape(id: &str) -> (f32, f32) {
    let row = between(FLASH_TS, &format!("{id}: {{ radius: "), " },");
    let (radius, rest) = row.split_once(", stretch: ").expect("a shape row");
    (
        radius.trim().parse().expect("a radius"),
        rest.trim().parse().expect("a stretch"),
    )
}

/// One weapon's `Some((radius, stretch, segments))` out of `viewmodel.rs`.
fn native_flash_shape(id: &str) -> (f32, f32) {
    let row = between(VIEWMODEL_RS, &format!("\"{id}\" => Some(("), ")),");
    let mut parts = row.split(',');
    (
        parts.next().unwrap().trim().parse().expect("a radius"),
        parts.next().unwrap().trim().parse().expect("a stretch"),
    )
}

#[test]
fn both_clients_shape_the_flash_per_weapon_identically() {
    // The flash's shape is a real cue: at the far end of a corridor it is often
    // the only thing that says whether a shotgun or a sniper just fired at you.
    // A shotgun that blooms wide in one client and narrow in the other teaches
    // two different games, and each looks deliberate from inside itself.
    for id in ["pistol", "assault", "shotgun", "sniper"] {
        let (bw, bs) = browser_flash_shape(id);
        let (nw, ns) = native_flash_shape(id);
        assert!(
            (bw - nw).abs() < 1e-6,
            "{id}: browser radius {bw}, native {nw}"
        );
        assert!(
            (bs - ns).abs() < 1e-6,
            "{id}: browser stretch {bs}, native {ns}"
        );
    }
}

#[test]
fn both_clients_flash_for_the_same_length_and_cap_it_the_same() {
    // `FLASH_LIFE` is two frames at 60fps in both, and the cap is what keeps
    // the flash from becoming a screen effect again — the complaint that
    // started all of this was that firing lit up the whole viewport.
    assert_eq!(
        rust_f32(VIEWMODEL_RS, "FLASH_LIFE"),
        browser_f32(FLASH_TS, "FLASH_LIFE"),
        "how long the flash is lit"
    );
    assert_eq!(
        rust_f32(VIEWMODEL_RS, "FLASH_MAX_SCREEN_FRACTION"),
        browser_f32(FLASH_TS, "FLASH_MAX_SCREEN_FRACTION"),
        "the largest fraction of the viewport one flash may cover"
    );
    assert_eq!(
        rust_f32(VIEWMODEL_RS, "FLASH_HALO_SCALE"),
        browser_f32(FLASH_TS, "FLASH_HALO_SCALE"),
        "how much wider the halo is than the core"
    );
}

#[test]
fn both_clients_use_the_same_flash_colours() {
    // The browser spells them as hex and this client as linear triples, so the
    // parity is only checkable by decoding one into the other. Sprites are drawn
    // unlit and the fan carries its colour on the vertex, so neither goes
    // through a lighting term — these are the colours as seen.
    for (hex_name, rust_name) in [("FLASH_CORE", "FLASH_CORE"), ("FLASH_HALO", "FLASH_HALO")] {
        let hex = between(FLASH_TS, &format!("export const {hex_name} = "), ";");
        assert_close(
            &format!("{rust_name} against the browser's {hex_name}"),
            rust_vec3(rust_name),
            hex_channels(hex),
            // The Rust side spells four decimals, which is the whole precision
            // available to it.
            1e-4,
        );
    }
}

#[test]
fn neither_client_gives_the_knife_a_flash() {
    // The one weapon whose entire value is that carrying it gives nothing away.
    // A swing resolves as a `Shot` like everything else, so it reaches the flash
    // code in both clients and both have to refuse it explicitly.
    assert!(
        VIEWMODEL_RS.contains("\"knife\" => None"),
        "the native client flashes for the knife"
    );
    assert!(
        FLASH_TS.contains("if (weaponId === 'knife') return null;"),
        "the browser flashes for the knife"
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

// -- map objects ---------------------------------------------------------------

/// The browser's item tints.
const ITEMS_TS: &str = include_str!("../../../packages/core/src/modules/hassault/items.ts");
/// The browser's water surface.
const WATER_TS: &str = include_str!("../../../packages/core/src/modules/hassault/water.ts");
/// The browser's ladder geometry.
const LADDERS_TS: &str = include_str!("../../../packages/core/src/modules/hassault/ladders.ts");
/// This client's items.
const ITEMS_RS: &str = include_str!("../src/items.rs");
const WATER_RS: &str = include_str!("../src/water.rs");
const GEOMETRY_RS: &str = include_str!("../src/geometry.rs");

/// One `kind: 0xrrggbb,` out of the browser's `TINT` table.
fn browser_tint(kind: &str) -> u32 {
    let block = between(ITEMS_TS, "const TINT: Record<string, number> = {", "};");
    for line in block.lines() {
        let line = line.trim().trim_end_matches(',');
        let Some((name, value)) = line.split_once(':') else {
            continue;
        };
        if name.trim() == kind {
            let digits = value.trim().trim_start_matches("0x");
            return u32::from_str_radix(digits, 16).expect("a hex tint");
        }
    }
    panic!("the browser's TINT has no '{kind}' — did it get renamed?");
}

/// A `pub const NAME: u32 = 0xrrggbb;` out of one of this client's sources.
fn rust_hex(source: &str, name: &str) -> u32 {
    let body = between(source, &format!("const {name}: u32 = "), ";");
    u32::from_str_radix(body.trim().trim_start_matches("0x"), 16).expect("a hex constant")
}

/// A `const NAME: f32 = v;` out of one of this client's sources.
fn rust_f32(source: &str, name: &str) -> f32 {
    between(source, &format!("const {name}: f32 = "), ";")
        .trim()
        .parse()
        .expect("a float")
}

/// A `const NAME = v;` out of one of the browser's sources.
fn browser_f32(source: &str, name: &str) -> f32 {
    between(source, &format!("const {name} = "), ";")
        .trim()
        .parse()
        .expect("a float")
}

#[test]
fn both_clients_tint_items_identically() {
    // The colour is the identifying signal on an item — the shapes are small and
    // seen at a distance, and what a player actually reads across a room is "red
    // means health". A health pack that is red in one client and orange in the
    // other is invisible from either client alone: each looks deliberate.
    for (kind, constant) in [
        ("health", "TINT_HEALTH"),
        ("helmet", "TINT_HELMET"),
        ("armour", "TINT_ARMOUR"),
        ("ammo", "TINT_AMMO"),
        ("clips", "TINT_CLIPS"),
        ("grenade", "TINT_GRENADE"),
    ] {
        assert_eq!(
            rust_hex(ITEMS_RS, constant),
            browser_tint(kind),
            "the '{kind}' item is a different colour in the two clients"
        );
    }
}

#[test]
fn both_clients_bob_and_fade_items_on_the_same_numbers() {
    // Not cosmetic: `FADE` is how long an item takes to sink, and the bob is
    // shared by every item on the map precisely so a *missing* one is easy to
    // pick out of a moving field. Two clients disagreeing on either would make
    // the same map read differently to two players in it.
    for (name, ts) in [
        ("HOVER", "HOVER"),
        ("BOB", "BOB"),
        ("BOB_SPEED", "BOB_SPEED"),
        ("SPIN_SPEED", "SPIN_SPEED"),
        ("FADE", "FADE"),
    ] {
        assert_close(
            name,
            [rust_f32(ITEMS_RS, name), 0.0, 0.0],
            [browser_f32(ITEMS_TS, ts), 0.0, 0.0],
            1e-6,
        );
    }
}

#[test]
fn both_clients_draw_water_the_same_way_when_the_map_says_nothing() {
    // A map with no `watercolor` is the common case, so the fallback is what most
    // water is actually drawn in — and the opacity decides whether you can see
    // the bottom of a pool, which is the difference between water you can fight
    // over and water you cannot.
    assert_eq!(
        rust_hex(WATER_RS, "DEFAULT_COLOR"),
        u32::from_str_radix(
            between(WATER_TS, "const DEFAULT_COLOR = 0x", ";").trim(),
            16
        )
        .expect("a hex colour"),
        "the two clients fall back to different water colours"
    );
    for name in ["OPACITY", "RIPPLE", "RIPPLE_SPEED"] {
        assert_close(
            name,
            [rust_f32(WATER_RS, name), 0.0, 0.0],
            [browser_f32(WATER_TS, name), 0.0, 0.0],
            1e-6,
        );
    }
}

#[test]
fn both_clients_build_a_ladder_to_the_same_dimensions() {
    // A ladder is drawn at the width of the *volume that catches you*, not of a
    // real ladder. If the two clients drew it differently, players would learn
    // two different answers to "how close do I have to be", and only one of them
    // matches the physics both of them run.
    for (rs, ts) in [
        ("LADDER_RUNG_SPACING", "RUNG_SPACING"),
        ("LADDER_RAIL", "RAIL_THICKNESS"),
        ("LADDER_RUNG", "RUNG_THICKNESS"),
        ("LADDER_RAIL_INSET", "RAIL_INSET"),
    ] {
        assert_close(
            rs,
            [rust_f32(GEOMETRY_RS, rs), 0.0, 0.0],
            [browser_f32(LADDERS_TS, ts), 0.0, 0.0],
            1e-6,
        );
    }
}
