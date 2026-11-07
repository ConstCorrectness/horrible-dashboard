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
fn the_muzzle_flash_is_unlit_in_both_clients() {
    // The browser draws it with `MeshBasicMaterial`. The native flash used to
    // carry the sun's direction as its normal to come out at full brightness,
    // which stopped working the moment the sun became a property of the map —
    // so it is unlit now, and this pins that the shader still honours the mark.
    assert!(
        VIEWMODEL_RS.contains("MATERIAL_UNLIT,"),
        "the flash no longer carries MATERIAL_UNLIT"
    );
    assert!(
        LIGHTING_WGSL.len() > 0
            && include_str!("../src/shader.wgsl").contains("in.material < -0.5"),
        "the world shader no longer draws the unlit mark unlit"
    );
    assert!(FLASH_TS.contains("MeshBasicMaterial"));
}

// -- the served atmosphere -----------------------------------------------------
//
// The light rig, the fog and the sky are **served** now
// (`backend/modules/hassault/atmosphere.py`), so neither client holds the
// numbers. What both still hold is a fallback for a node older than the field,
// and those fallbacks are pinned against the server's own defaults here — read
// out of the Python at test time, like everything else in this file.

const ATMOSPHERE_PY: &str = include_str!("../../../backend/modules/hassault/atmosphere.py");
const ATMOSPHERE_TS: &str =
    include_str!("../../../packages/core/src/modules/hassault/atmosphere.ts");

/// `"key": 0xABCDEF` or `"key": 1.23` in the Python dict literal after `block`.
fn py_value(block: &str, key: &str) -> String {
    let dict = between(ATMOSPHERE_PY, block, "\n}");
    between(dict, &format!("\"{key}\": "), ",").to_string()
}

fn py_int(block: &str, key: &str) -> u32 {
    let v = py_value(block, key);
    u32::from_str_radix(v.trim_start_matches("0x").trim_start_matches("0X"), 16)
        .unwrap_or_else(|_| panic!("{key} is not hex: {v}"))
}

fn py_float(block: &str, key: &str) -> f32 {
    py_value(block, key).parse().expect("a float")
}

#[test]
fn the_native_fallback_rig_is_the_servers_cube_default() {
    use hassault_native::atmosphere::Atmosphere;
    let a = Atmosphere::CUBE;
    let block = "CUBE_DEFAULT: dict[str, Any] = {";
    assert_eq!(a.hemi_sky, py_int(block, "hemiSky"));
    assert_eq!(a.hemi_ground, py_int(block, "hemiGround"));
    assert_eq!(a.sun_color, py_int(block, "sunColor"));
    assert_eq!(a.fill_color, py_int(block, "fillColor"));
    assert_eq!(a.fog_color, py_int(block, "fogColor"));
    assert_eq!(a.hemi_intensity, py_float(block, "hemiIntensity"));
    assert_eq!(a.sun_intensity, py_float(block, "sunIntensity"));
    assert_eq!(a.fill_intensity, py_float(block, "fillIntensity"));
    assert_eq!(a.fog_density, py_float(block, "fogDensity"));
    assert_eq!(a.exposure, py_float(block, "exposure"));
}

#[test]
fn the_native_fallback_daylight_is_the_servers_gltf_default() {
    use hassault_native::atmosphere::Atmosphere;
    let a = Atmosphere::GLTF;
    let block = "GLTF_DEFAULT: dict[str, Any] = {";
    assert_eq!(a.sky_zenith, py_int(block, "skyZenith"));
    assert_eq!(a.sky_horizon, py_int(block, "skyHorizon"));
    assert_eq!(a.fog_color, py_int(block, "fogColor"));
    assert_eq!(a.fog_density, py_float(block, "fogDensity"));
}

#[test]
fn the_browser_fallback_rig_is_the_servers_too() {
    // The browser's copy lives in `atmosphere.ts`; the same three numbers that
    // most change the picture are pinned there.
    let block = "CUBE_DEFAULT: dict[str, Any] = {";
    for (key, want) in [
        ("hemiSky", py_int(block, "hemiSky")),
        ("sunColor", py_int(block, "sunColor")),
        ("fogColor", py_int(block, "fogColor")),
    ] {
        let got = between(ATMOSPHERE_TS, &format!("  {key}: "), ",");
        assert_eq!(
            u32::from_str_radix(got.trim_start_matches("0x"), 16).expect("hex"),
            want,
            "atmosphere.ts CUBE_DEFAULT.{key}"
        );
    }
}

#[test]
fn no_quality_level_changes_how_far_you_can_see() {
    // Every level draws the map's own fog density, in both clients: the
    // distance a player can see somebody at is gameplay, and a quality preset
    // that thickened it (as Low and Medium used to) is a setting that loses
    // gunfights. Read from the sources, so a fog term creeping back into either
    // shows up here.
    const SETTINGS_RS: &str = include_str!("../src/settings.rs");
    const RENDERER_RS: &str = include_str!("../src/renderer.rs");
    const GRAPHICS_TS: &str =
        include_str!("../../../packages/core/src/modules/hassault/graphics.ts");
    assert!(
        !SETTINGS_RS.contains("fn fog_scale"),
        "a quality level scales the fog again"
    );
    assert!(
        RENDERER_RS.contains("let fog = self.lighting.atmosphere.fog_density;"),
        "the renderer no longer draws the map's own fog density"
    );
    assert!(
        !GRAPHICS_TS.contains("fogScale"),
        "a browser tier scales the fog again"
    );
    assert!(
        PANEL_TSX.contains("new THREE.FogExp2(a.fogColor, a.fogDensity)"),
        "the browser no longer draws the map's own fog density"
    );
}

#[test]
fn both_clients_attenuate_a_point_light_the_same_way() {
    // three's `getDistanceAttenuation` with decay 2, in `atmosphere.ts`, and
    // its port in `lighting.wgsl.inc`. The two are text here, so the formula's
    // three load-bearing pieces are what is pinned.
    for needle in ["distance * distance", "ratio * ratio * ratio * ratio"] {
        assert!(
            ATMOSPHERE_TS.contains(needle),
            "atmosphere.ts lost `{needle}`"
        );
        assert!(
            LIGHTING_WGSL.contains(needle),
            "lighting.wgsl.inc lost `{needle}`"
        );
    }
    // And the candela convention: intensity × radius / 2, linear in reach.
    assert!(ATMOSPHERE_TS.contains("light.intensity * light.radius * 0.5"));
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
    let ts = between(
        TRACE_TS,
        "export const FACE_PX = ",
        "export const FACE_NONE",
    );
    let rs = between(TRACE_RS, "pub const FACE_PX: i32 = ", "pub const FACE_NONE");
    for (i, name) in [
        "FACE_PX", "FACE_NX", "FACE_PY", "FACE_NY", "FACE_PZ", "FACE_NZ",
    ]
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
const VIEWCLIPS_TS: &str = include_str!("../../../packages/core/src/modules/hassault/viewclips.ts");
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

const INSPECTS_TS: &str = include_str!("../../../packages/core/src/modules/hassault/inspects.ts");
const INSPECTS_RS: &str = include_str!("../src/inspects.rs");
const HANDS_TS: &str = include_str!("../../../packages/core/src/modules/hassault/hands.ts");
const HANDS_RS: &str = include_str!("../src/hands.rs");

#[test]
fn both_clients_run_the_same_inspects_on_the_same_hands() {
    // The same arrangement as the grips: one clip file and one rig, read by
    // both. The samplers are held to `inspects.golden.json` by each client's
    // own tests; this catches a client quietly going back to formulas.
    assert!(INSPECTS_TS.contains("from './models/inspects.json'"));
    assert!(INSPECTS_RS.contains("models/inspects.json"));
    assert!(HANDS_TS.contains("/hassault-hands.glb"));
    assert!(HANDS_RS.contains("hassault-hands.glb"));
    // The flex axes are the one part of the hands not read from the file.
    assert!(HANDS_TS.contains("FINGER_FLEX_AXIS: Vec3 = [0, 1, 0]"));
    assert!(HANDS_RS.contains("const FINGER_FLEX_AXIS: Vec3 = Vec3::Y"));
    assert!(HANDS_TS.contains("normalize([-0.7, 0, 0.57])"));
    assert!(HANDS_RS.contains("Vec3::new(-0.7, 0.0, 0.57).normalize()"));
}

#[test]
fn both_clients_build_an_arm_to_the_same_dimensions() {
    // A shoulder in a different place, or a forearm a different length, and the
    // hands reach the same grip from two different postures — one game, two
    // pairs of arms. Nothing errors; the two just look like different games.
    for (rust, ts) in [("UPPER_LEN", "UPPER_LEN"), ("LOWER_LEN", "LOWER_LEN")] {
        assert_eq!(rust_f32(ARMS_RS, rust), browser_f32(ARMS_TS, ts), "{ts}");
    }
    // The shoulders are a `Vec3::new(..)` here and a tuple there, so they are
    // compared component by component rather than by a shared parser.
    for name in ["SHOULDER_R", "SHOULDER_L"] {
        let rs = between(
            ARMS_RS,
            &format!("pub const {name}: Vec3 = Vec3::new("),
            ")",
        );
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
        UTILITY_TS.contains("equip(slot: number): boolean"),
        "the browser has no equip"
    );
    // Both refuse a slot with none left: equipping puts the weapon away, and
    // one client swapping a loaded rifle for an empty hand is a way to die the
    // other does not have.
    assert!(
        UTILITY_RS.contains("&& self.count_of(slot) > 0"),
        "the native client equips an empty grenade slot"
    );
    assert!(
        UTILITY_TS.contains("if (this.countOf(slot) <= 0) return false;"),
        "the browser equips an empty grenade slot"
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
    for (name, bytes) in hassault_native::prop::WEAPON_GLBS
        .iter()
        .chain(hassault_native::prop::KNIFE_ARCHETYPE_GLBS.iter())
    {
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

// ---- key map ---------------------------------------------------------------

const CONTROLS_TS: &str = include_str!("../../../packages/core/src/modules/hassault/controls.ts");

/// The browser's `DEFAULT_CONTROLS`, parsed out of the TypeScript: each
/// `name: ['Code', 'Code'],` line of the object literal.
fn ts_default_controls() -> Vec<(String, Vec<String>)> {
    let body = between(
        CONTROLS_TS,
        "export const DEFAULT_CONTROLS: Bindings = {",
        "\n};",
    );
    body.lines()
        .map(str::trim)
        .filter(|l| !l.starts_with("//") && l.contains(": ["))
        .map(|l| {
            let (name, rest) = l.split_once(':').expect("name: [..]");
            let codes = rest
                .split('\'')
                .enumerate()
                .filter(|(i, _)| i % 2 == 1)
                .map(|(_, s)| s.to_string())
                .collect();
            (name.trim().to_string(), codes)
        })
        .collect()
}

/// One key map, two clients. The native client used to hard-code its keys and
/// ignore the Controls screen entirely; now it reads the same stored table, and
/// that only works if the *defaults* it merges over are the browser's too — a
/// stored diff is relative to them.
#[test]
fn the_default_key_map_is_the_browsers() {
    let ts = ts_default_controls();
    assert!(
        ts.len() >= 30,
        "parsed only {} actions from controls.ts",
        ts.len()
    );
    let rust: Vec<(String, Vec<String>)> = hassault_native::controls::DEFAULTS
        .iter()
        .map(|(_, name, keys)| {
            (
                name.to_string(),
                keys.iter().map(|k| k.to_string()).collect(),
            )
        })
        .collect();
    assert_eq!(
        rust, ts,
        "controls.rs DEFAULTS drifted from DEFAULT_CONTROLS"
    );
}

/// The browser's flashbang screen and ears.
const FLASHBANG_TS: &str = include_str!("../../../packages/core/src/modules/hassault/flashbang.ts");
/// This client's.
const FLASHBANG_RS: &str = include_str!("../src/flashbang.rs");
/// The browser's grenade renderer, for the smoke's opacity curve.
const NADES_TS: &str = include_str!("../../../packages/core/src/modules/hassault/nades.ts");
const NADES_RS: &str = include_str!("../src/nades.rs");
/// The browser's flashbang layer, for the afterimage's strength.
const FLASH_LAYER_TS: &str =
    include_str!("../../../packages/core/src/modules/hassault/flash-layer.ts");

#[test]
fn both_clients_ring_and_muffle_a_flash_the_same() {
    // The curve itself is pinned by `flash-vectors.json` in `conformance.rs`;
    // these are the presentation numbers around it. A ring that lasts longer on
    // one client, or a muffle that bites harder, is a flash that costs one
    // player more than the other.
    for name in [
        "FLASH_BLIND",
        "FLASH_RING_HZ",
        "FLASH_RING_Q",
        "FLASH_RING_GAIN",
        "FLASH_RING_BODY",
        "FLASH_MUFFLE_HZ",
        "FLASH_OPEN_HZ",
        "FLASH_RING_MIN",
    ] {
        assert_eq!(
            rust_f32(FLASHBANG_RS, name),
            browser_f32(FLASHBANG_TS, name),
            "{name}"
        );
    }
    assert_eq!(
        rust_f32(FLASHBANG_RS, "AFTERIMAGE_STRENGTH"),
        browser_f32(FLASH_LAYER_TS, "AFTERIMAGE_STRENGTH"),
        "how strong the burned-in frame is"
    );
}

#[test]
fn both_clients_thin_and_clear_a_smoke_on_the_same_clock() {
    // A smoke that blooms later, fades sooner or opens wider after an HE on
    // one client is one that player sees through first.
    for name in [
        "SMOKE_BLOOM",
        "SMOKE_FADE",
        "SMOKE_CLEARED",
        "SMOKE_CHANNEL_LIFE",
        "SMOKE_CHANNEL_RADIUS",
    ] {
        assert_eq!(
            rust_f32(NADES_RS, name),
            browser_f32(NADES_TS, name),
            "{name}"
        );
    }
}

#[test]
fn both_clients_offer_the_same_field_of_view_and_brightness() {
    // The two settings on the video page that change how the game plays rather
    // than how it looks. Both are allowed; a wider range on one client is not.
    use hassault_native::settings::{BRIGHTNESS_RANGE, FOV_RANGE, KEY_BRIGHTNESS};
    const MENU_TS: &str =
        include_str!("../../../packages/core/src/modules/hassault/menu-panels.tsx");
    assert_eq!(
        FOV_RANGE.0,
        browser_f32(MENU_TS, "FOV_MIN"),
        "the FOV floor"
    );
    assert_eq!(
        FOV_RANGE.1,
        browser_f32(MENU_TS, "FOV_MAX"),
        "the FOV ceiling"
    );
    assert_eq!(BRIGHTNESS_RANGE.0, browser_f32(MENU_TS, "BRIGHTNESS_MIN"));
    assert_eq!(BRIGHTNESS_RANGE.1, browser_f32(MENU_TS, "BRIGHTNESS_MAX"));
    assert!(
        MENU_TS.contains(&format!("BRIGHTNESS_KEY = '{KEY_BRIGHTNESS}'")),
        "the two clients store brightness under different keys"
    );
}

#[test]
fn both_clients_give_a_player_the_same_light_floor_at_every_level() {
    // How visible somebody standing in a dark corner is decides gunfights, so
    // it is not a graphics setting. Both clients add the same floor, and the
    // native skin shader shades at full detail whatever the quality level —
    // Low used to return the raw albedo, a fully lit silhouette on the setting
    // meant to look worse.
    const SKIN_WGSL: &str = include_str!("../src/skin.wgsl");
    const OPERATOR_TS: &str =
        include_str!("../../../packages/core/src/modules/hassault/models/operator.ts");
    assert_eq!(
        rust_f32(SKIN_WGSL, "CHARACTER_FLOOR"),
        browser_f32(OPERATOR_TS, "CHARACTER_FLOOR"),
        "the player light floor"
    );
    assert!(
        SKIN_WGSL.contains("shade(albedo, in.normal, in.world_position, 2.0, occlusion)"),
        "the skin shader takes its detail from the quality level again"
    );
}

#[test]
fn baked_ambient_occlusion_is_content_on_the_world_and_never_a_setting() {
    // A map's GLB carries AO in COLOR_0 (`tools/blender/maplib.py`). It is the
    // same darkening for everyone at every level, so it is fair: the shader
    // folds it into the albedo before `shade()`, whose Low early-out returns
    // that albedo; it never reaches a character; no setting turns it off; and
    // the browser keeps it through the static-batch merge.
    const SHADER_WGSL: &str = include_str!("../src/shader.wgsl");
    const SKIN_WGSL: &str = include_str!("../src/skin.wgsl");
    const WORLD3D_TS: &str = include_str!("../../../packages/core/src/modules/hassault/world3d.ts");
    const GRAPHICS_TS: &str = include_str!("../../../packages/core/src/modules/hassault/graphics.ts");
    const SETTINGS_RS: &str = include_str!("../src/settings.rs");
    // The attribute also carries a glowing material's strength over 1
    // (`world3d.rs`); the occlusion is what is clamped to 1.
    assert!(SHADER_WGSL.contains("let ao = min(in.ao, 1.0);"), "the AO is no longer split from the glow");
    assert!(SHADER_WGSL.contains("albedo = albedo * ao;"), "textured world surfaces lost their AO");
    assert!(SHADER_WGSL.contains("* DETAIL_GAIN * ao"), "untextured world surfaces lost their AO");
    assert!(!SKIN_WGSL.contains(".ao") && !LIGHTING_WGSL.contains(".ao"), "AO reached the characters");
    assert!(WORLD3D_TS.contains("merged.setAttribute('color'"), "the browser drops COLOR_0 when it batches");
    for (name, src) in [("graphics.ts", GRAPHICS_TS), ("settings.rs", SETTINGS_RS)] {
        assert!(!src.to_lowercase().contains("occlusion"), "{name} has an AO setting");
    }
}

// -- the post chain ------------------------------------------------------------

const POST_CHAIN_TS: &str =
    include_str!("../../../packages/core/src/modules/hassault/post-chain.ts");
const POST_RS: &str = include_str!("../src/post.rs");
const POST_WGSL: &str = include_str!("../src/post.wgsl");

#[test]
fn both_clients_bloom_and_sharpen_with_the_same_numbers() {
    // The browser's chain is a port of this one (`post-chain.ts`), and a bloom
    // that started lower in one client would make a sunlit wall glow there
    // and not here. The constants, the filter weights and the blends are text
    // in both, so the load-bearing pieces are pinned.
    for (native, browser) in [
        ("const LEVELS: usize = 5;", "export const LEVELS = 5;"),
        ("const THRESHOLD: f32 = 1.4;", "export const THRESHOLD = 1.4;"),
        ("const KNEE: f32 = 0.5;", "export const KNEE = 0.5;"),
    ] {
        assert!(POST_RS.contains(native), "post.rs lost `{native}`");
        assert!(POST_CHAIN_TS.contains(browser), "post-chain.ts lost `{browser}`");
    }
    for needle in [
        // The 13-tap downsample's weights.
        "0.03125",
        "0.0625",
        "0.125",
        // The tent upsample's normalisation.
        "/ 16.0",
        // CAS: the lobe's range, and the headroom term.
        "mix(8.0, 5.0, amount)",
        "(1.0) - hi",
        // The soft knee.
        "soft * soft / (4.0 *",
        // The dither: interleaved gradient noise, twice, for a triangular
        // distribution of one output step.
        "52.9829189",
        "0.06711056, 0.00583715",
        "47.0, 17.0",
    ] {
        assert!(POST_WGSL.contains(needle), "post.wgsl lost `{needle}`");
        assert!(POST_CHAIN_TS.contains(needle), "post-chain.ts lost `{needle}`");
    }
}

#[test]
fn both_clients_tone_map_once_with_three_s_aces() {
    // The native scene is linear light in a half-float target, tone mapped
    // once in `post.wgsl`'s `display`; the browser's chain renders to a
    // half-float target and runs `DISPLAY`, and its materials run three's
    // `ACESFilmicToneMapping`. The CPU copy in `atmosphere.rs` runs the curve
    // backwards for the clear and fog colours. Four copies of one curve: the
    // fit, both matrices and three's `/ 0.6` are pinned in all of them, so a
    // slip in one shows here and not as a map that is a shade off in one
    // client. (Native once used Narkowicz's fit here, and its mid-tones sat
    // about a fifth above the browser's for the same light.)
    const ATMOSPHERE_RS: &str = include_str!("../src/atmosphere.rs");
    const RENDERER_RS: &str = include_str!("../src/renderer.rs");
    for needle in [
        "0.0245786",
        "0.000090537",
        "0.983729",
        "0.432951",
        "0.238081",
        "/ 0.6",
        // ACESInputMat, column by column.
        "0.59719", "0.07600", "0.02840",
        "0.35458", "0.90834", "0.13383",
        "0.04823", "0.01566", "0.83777",
        // ACESOutputMat.
        "1.60475", "-0.10208", "-0.00327",
        "-0.53108", "1.10813", "-0.07276",
        "-0.07367", "-0.00605", "1.07602",
    ] {
        assert!(POST_WGSL.contains(needle), "post.wgsl lost `{needle}`");
        assert!(POST_CHAIN_TS.contains(needle), "post-chain.ts lost `{needle}`");
        assert!(ATMOSPHERE_RS.contains(needle), "atmosphere.rs lost `{needle}`");
    }
    // Once, at the end: no scene shader tone maps on its own again, and the
    // scene target is half float in both.
    assert!(!LIGHTING_WGSL.contains("fn tonemap"), "a scene shader tone maps again");
    assert!(
        RENDERER_RS.contains("SCENE_FORMAT: wgpu::TextureFormat = wgpu::TextureFormat::Rgba16Float"),
        "the native scene is no longer half float"
    );
    assert!(POST_CHAIN_TS.contains("t.HalfFloatType"), "the browser's chain target is no longer half float");
}

#[test]
fn both_clients_fog_in_linear_light_before_the_curve() {
    // The order the plan settled: fog is mixed into the scene's linear light,
    // before the tone curve, with its colour run backwards through the curve
    // so a fully fogged wall comes out as the map's hex. Native always draws
    // that way; the browser does whenever its chain is on (with it off, three
    // mixes fog after its encode, which also lands on the hex). A client that
    // forgets the inverse fogs into a lifted grey.
    const ATMOSPHERE_RS: &str = include_str!("../src/atmosphere.rs");
    const RENDERER_RS: &str = include_str!("../src/renderer.rs");
    assert!(
        ATMOSPHERE_RS.contains("fog_color: with(display_hex_to_scene(a.fog_color), 0.0)"),
        "native fog colour is no longer pre-inverted"
    );
    assert!(
        RENDERER_RS.contains("crate::atmosphere::display_hex_to_scene(if self.is_3d"),
        "native clear colour is no longer pre-inverted"
    );
    assert!(
        POST_CHAIN_TS.contains("if (fog) fog.color.copy(toScene(fog.color));"),
        "the browser's chain no longer pre-inverts the fog colour"
    );
    assert!(
        POST_CHAIN_TS.contains("scene.background = toScene(background as THREE.Color);"),
        "the browser's chain no longer pre-inverts the clear colour"
    );
}

// --- Image-based light and GGX (G6) ------------------------------------------

const ENVIRONMENT_TS: &str =
    include_str!("../../../packages/core/src/modules/hassault/environment.ts");
const SHADER_WGSL: &str = include_str!("../src/shader.wgsl");

/// A file of three's own source, read at test time from the core package's
/// `node_modules` (not `include_str!`, so a checkout without node_modules still
/// builds): `None` when it is not installed, and the caller skips.
fn three_source(relative: &str) -> Option<String> {
    let path = format!(
        "{}/../../packages/core/node_modules/three/src/{relative}",
        env!("CARGO_MANIFEST_DIR")
    );
    std::fs::read_to_string(&path).ok()
}

#[test]
fn both_clients_read_three_s_dfg_table() {
    // `dfg_lut.rs` is a copy of three's table, value for value, so both
    // clients' split-sum specular is one table rather than two fits. A three
    // upgrade that retunes it fails here; regenerate the copy from the file.
    let Some(js) = three_source("renderers/shaders/DFGLUTData.js") else {
        eprintln!("skipping: three is not installed under packages/core/node_modules");
        return;
    };
    let body = between(&js, "new Uint16Array( [", "] );");
    let three: Vec<u16> = body
        .split(',')
        .map(str::trim)
        .filter(|v| v.starts_with("0x"))
        .map(|v| u16::from_str_radix(&v[2..], 16).expect("hex"))
        .collect();
    assert_eq!(three.len(), (hassault_native::dfg_lut::SIZE * hassault_native::dfg_lut::SIZE * 2) as usize);
    assert_eq!(three.as_slice(), hassault_native::dfg_lut::DATA.as_slice(), "three's DFG table changed");
}

#[test]
fn both_clients_shade_with_three_s_ggx() {
    // The native physical path is a port of MeshStandardMaterial's: the same
    // Fresnel fit, the same multiscatter average and the same roughness floor
    // and geometric widening. Each constant is checked against three's own
    // chunk when three is installed, and against the port always.
    for (constant, chunk) in [
        ("-5.55473 * dot_vh - 6.98316", "renderers/shaders/ShaderChunk/common.glsl.js"),
        ("0.047619", "renderers/shaders/ShaderChunk/lights_physical_pars_fragment.glsl.js"),
        ("0.0525", "renderers/shaders/ShaderChunk/lights_physical_fragment.glsl.js"),
    ] {
        assert!(SHADER_WGSL.contains(constant), "shader.wgsl no longer has three's {constant}");
        if let Some(three) = three_source(chunk) {
            let spelled = constant.replace("-5.55473 * dot_vh - 6.98316", "- 5.55473 * dotVH - 6.98316");
            assert!(three.contains(&spelled), "three's {chunk} no longer has {spelled}");
        }
    }
    assert!(
        SHADER_WGSL.contains("roughness = min(max(roughness, 0.0525) + geometry_roughness, 1.0);"),
        "native roughness no longer floors and widens as three's does"
    );
}

#[test]
fn both_clients_light_the_world_with_the_map_s_own_sky_and_ground() {
    // One environment function, written twice: the horizon, eased toward the
    // zenith above it and toward the hemisphere's ground below it by
    // sqrt(|y|), with no sun disc (the sun is a light). The native palette is
    // probed at its ends and midpoint; the browser's spelling is pinned.
    use hassault_native::atmosphere::Atmosphere;
    use hassault_native::environment::Palette;
    let a = Atmosphere {
        sky_zenith: 0x2e6cc7,
        sky_horizon: 0xd6dce0,
        hemi_ground: 0xb58c5c,
        ..Atmosphere::GLTF
    };
    let linear = |hex: u32| hex_channels(&format!("0x{hex:06x}")).map(srgb_to_linear);
    let palette = Palette::of(&a);
    assert_close("straight up", palette.radiance(1.0), linear(a.sky_zenith), 1e-5);
    assert_close("level", palette.radiance(0.0), linear(a.sky_horizon), 1e-5);
    assert_close("straight down", palette.radiance(-1.0), linear(a.hemi_ground), 1e-5);
    let (h, z) = (linear(a.sky_horizon), linear(a.sky_zenith));
    let quarter_up = [0, 1, 2].map(|i| h[i] + (z[i] - h[i]) * 0.5);
    assert_close("a quarter up eases by sqrt", palette.radiance(0.25), quarter_up, 1e-5);

    assert!(
        ENVIRONMENT_TS.contains("const far = srgbHexToLinear(y >= 0 ? a.skyZenith : a.hemiGround);"),
        "the browser's environment no longer reaches for the zenith above and the ground below"
    );
    assert!(
        ENVIRONMENT_TS.contains("const t = Math.sqrt(Math.min(1, Math.abs(y)));"),
        "the browser's environment no longer eases by sqrt(|y|)"
    );
    assert!(
        PANEL_TSX.contains("mapEnvironment = createMapEnvironment(THREE, renderer, served);")
            && PANEL_TSX.contains("scene.environment = mapEnvironment.texture;"),
        "the browser no longer lights a modelled map with its own served sky"
    );
}

// --- AA, bloom and dither (G6) -----------------------------------------------

#[test]
fn both_clients_bloom_linear_light_before_the_curve() {
    // The bloom is thresholded on the scene's linear light and added back into
    // it, and the curve runs after: native in `frame_from_scene`, the browser
    // in `DISPLAY`. The old order (threshold and screen-blend after the curve)
    // could not tell a lamp at 8 from one at 1.
    assert!(
        POST_WGSL.contains("light = light + textureSample(bloom, source_sampler, uv).rgb * intensity;")
            && POST_WGSL.contains("return display(light);"),
        "native no longer adds the bloom before the curve"
    );
    assert!(
        POST_WGSL.contains("var c = scene_linear(in.uv + texel * vec2<f32>(-0.5, -0.5));"),
        "native's prefilter no longer reads linear light"
    );
    assert!(
        POST_CHAIN_TS.contains("if (intensity > 0.001) light += texture2D(bloom, vUv).rgb * intensity;")
            && POST_CHAIN_TS.contains("gl_FragColor = vec4(srgb(aces(light)), 1.0);"),
        "the browser no longer adds the bloom before the curve"
    );
    assert!(
        POST_CHAIN_TS.contains("c *= 0.25 * exposure;"),
        "the browser's prefilter no longer thresholds exposed light, as native's scene is"
    );
}

#[test]
fn moderate_bloom_is_medium_and_high_in_both_clients_and_off_at_low() {
    // One default in four places: native's presets, the browser's chain, the
    // server's schema and the dashboard's setting. Low draws none in either
    // client: native's Low preset writes 0, the browser's Low tier row is off.
    const SETTINGS_RS: &str = include_str!("../src/settings.rs");
    const GRAPHICS_TS: &str = include_str!("../../../packages/core/src/modules/hassault/graphics.ts");
    const INDEX_TS: &str = include_str!("../../../packages/core/src/modules/hassault/index.ts");
    const SCHEMA_PY: &str = include_str!("../../../backend/modules/hassault/settings_schema.py");
    assert!(SETTINGS_RS.contains("pub const MODERATE_BLOOM: f32 = 0.25;"));
    assert!(POST_CHAIN_TS.contains("export const MODERATE_BLOOM = 0.25;"));
    assert!(SCHEMA_PY.contains(r#"_num("hassault.video.bloom", 0.25, "machine", 0, 1, native=True)"#));
    assert!(between(INDEX_TS, "key: 'hassault.video.bloom'", "}").contains("default: 0.25,"));
    let video = |q| {
        let mut v = hassault_native::settings::Video::default();
        v.apply_preset(q);
        v
    };
    use hassault_native::settings::Quality;
    assert_eq!(video(Quality::Low).bloom, 0.0);
    assert_eq!(video(Quality::Medium).bloom, 0.25);
    assert_eq!(video(Quality::High).bloom, 0.25);
    let low = between(GRAPHICS_TS, "low: {", "},");
    assert!(low.contains("bloom: false,"), "the browser's Low draws bloom");
    for tier in ["medium: {", "high: {"] {
        assert!(between(GRAPHICS_TS, tier, "},").contains("bloom: true,"), "the browser's {tier} has no bloom");
    }
}

#[test]
fn native_medium_anti_aliases_with_fxaa_and_the_rest_multisample() {
    // The browser's Medium multisamples its canvas; native's Medium has MSAA
    // 1 for fill rate and smooths its edges with FXAA instead. High and Ultra
    // multisample in both.
    use hassault_native::settings::{Quality, Video};
    for (q, msaa, fxaa) in [
        (Quality::Low, 1, false),
        (Quality::Medium, 1, true),
        (Quality::High, 4, false),
        (Quality::Ultra, 8, false),
    ] {
        let mut v = Video::default();
        v.apply_preset(q);
        assert_eq!((v.msaa, v.fxaa), (msaa, fxaa), "{q:?}");
    }
    assert!(Video::default().fxaa, "the default level is Medium, which has FXAA");
}

#[test]
fn a_glowing_material_emits_in_both_clients() {
    // Every map's lamps, screens and LEDs are `KHR_materials_emissive_strength`
    // materials at 4-10. three adds `emissive * emissiveIntensity` to the light
    // leaving the surface; native carries the strength in the AO attribute as
    // `1 + strength` and adds `color * glow` before the exposure, so the HDR
    // bloom has the same lamps to find in both.
    const WORLD3D_RS: &str = include_str!("../src/world3d.rs");
    assert!(WORLD3D_RS.contains("prim.material().emissive_strength().unwrap_or(1.0)"));
    assert!(WORLD3D_RS.contains("return 1.0 + glow;"));
    assert!(SHADER_WGSL.contains("let glow = max(in.ao - 1.0, 0.0);"));
    assert!(SHADER_WGSL.contains("radiance = radiance + in.color * glow;"));
}

// --- Shadows and normals (G6) ------------------------------------------------

#[test]
fn both_clients_fit_the_sun_shadow_round_what_is_drawn_at_4096_on_high() {
    // One fit in both: a sphere round the bounds of the drawn geometry (native
    // `shadow.rs` `fit` over the render vertices, the browser's `shadowSphere`
    // over `drawnBounds`). The browser used to fit `MAP_CONFIGS`' hand-written
    // extent, which was coarser than native on eight maps and narrower than
    // Junk Flea. Both shadow maps render once per map, so there is no texel
    // snapping to keep in step: nothing moves under the camera.
    const SHADOW_RS: &str = include_str!("../src/shadow.rs");
    const SHADOW_FIT_TS: &str = include_str!("../../../packages/core/src/modules/hassault/shadow-fit.ts");
    const PANEL_TSX: &str = include_str!("../../../packages/core/src/modules/hassault/HorribleAssaultPanel.tsx");
    const GRAPHICS_TS: &str = include_str!("../../../packages/core/src/modules/hassault/graphics.ts");
    assert!(SHADOW_RS.contains("let radius = ((max - min) * 0.5).length().max(1.0);"));
    assert!(SHADOW_RS.contains("Mat4::orthographic_rh(-radius, radius, -radius, radius,"));
    assert!(SHADOW_FIT_TS.contains("radius: Math.max(1, Math.sqrt(dx * dx + dy * dy + dz * dz) / 2),"));
    assert!(PANEL_TSX.contains("? shadowSphere(drawn.min, drawn.max)"), "the browser's modelled maps no longer fit the drawn bounds");
    assert!(PANEL_TSX.contains("cam.left = -radius;") && PANEL_TSX.contains("cam.top = radius;"));

    use hassault_native::settings::{Quality, Video};
    let mut v = Video::default();
    v.apply_preset(Quality::High);
    assert_eq!(v.shadow_level.quality().size, 4096, "native High's shadow map");
    assert!(between(GRAPHICS_TS, "high: {", "},").contains("shadowSize: 4096,"), "the browser High's shadow map");
}

#[test]
fn both_clients_shade_the_maps_with_their_exported_normals() {
    // The export keeps creases hard (`mark_sharp_edges`) and smooths the rest;
    // native reads those normals, and the browser now keeps them too rather than
    // averaging every face round each vertex (`computeVertexNormals`), which
    // moved 20% of vertices by over a degree.
    const WORLD3D_TS: &str = include_str!("../../../packages/core/src/modules/hassault/world3d.ts");
    const WORLD3D_RS: &str = include_str!("../src/world3d.rs");
    assert!(WORLD3D_TS.contains("if (!geom.attributes.normal) geom.computeVertexNormals();"));
    assert!(!WORLD3D_TS.contains("\n    geom.computeVertexNormals();"), "the browser recomputes the map's normals again");
    assert!(WORLD3D_RS.contains(".read_normals()"));
}

/// The browser's surface breakup, the native `surface_breakup` and anti-tiling.
const BREAKUP_TS: &str = include_str!("../../../packages/core/src/modules/hassault/surface-breakup.ts");

/// A TS `export const NAME = n;` as a float.
fn ts_const(source: &str, name: &str) -> f32 {
    between(source, &format!("export const {name} = "), ";")
        .parse()
        .unwrap_or_else(|_| panic!("{name} is not a plain number"))
}

/// A WGSL `const NAME: f32 = n;` as a float.
fn wgsl_const(name: &str) -> f32 {
    between(SHADER_WGSL, &format!("const {name}: f32 = "), ";")
        .parse()
        .unwrap_or_else(|_| panic!("{name} is not a plain number"))
}

#[test]
fn both_clients_break_up_a_surface_with_the_same_numbers() {
    // Content, not a setting: every tier and both clients draw the same tile
    // offsets, the same tint and the same grime, so nobody's wall is cleaner.
    for name in [
        "ANTI_TILE_FREQ",
        "MACRO_FREQ",
        "MACRO_AMOUNT",
        "GRIME_AO_FULL",
        "GRIME_AO_NONE",
        "GRIME_STRENGTH",
    ] {
        assert_eq!(ts_const(BREAKUP_TS, name), wgsl_const(name), "{name}");
    }
    let tint: Vec<f32> = between(BREAKUP_TS, "GRIME_TINT: readonly [number, number, number] = [", "]")
        .split(',')
        .map(|p| p.trim().parse().expect("a float"))
        .collect();
    let native = between(SHADER_WGSL, "const GRIME_TINT: vec3<f32> = vec3<f32>(", ")");
    let native: Vec<f32> = native.split(',').map(|p| p.trim().parse().expect("a float")).collect();
    assert_eq!(tint, native, "GRIME_TINT");

    // The hash and noise are the same function: the same constants, the same
    // fade. A different hash is a different pattern, not a slightly different one.
    for both in ["* 0.1031)", "+ 33.33)", "f * f * (3.0 - 2.0 * f)"] {
        assert!(BREAKUP_TS.contains(both), "browser lost {both:?}");
    }
    for both in ["* 0.1031)", "+ 33.33)", "f * f * (3.0 - 2.0 * f)"] {
        assert!(SHADER_WGSL.contains(both), "native lost {both:?}");
    }

    // The anti-tiling bands, their offsets and the blend.
    assert!(BREAKUP_TS.contains("* 8.0;") && SHADER_WGSL.contains("* ANTI_TILE_FREQ) * 8.0;"));
    assert!(BREAKUP_TS.contains("sin(vec2(3.0, 7.0) * i)") && BREAKUP_TS.contains("sin(vec2(3.0, 7.0) * (i + 1.0))"));
    assert!(SHADER_WGSL.contains("sin(vec2<f32>(3.0, 7.0) * i)") && SHADER_WGSL.contains("sin(vec2<f32>(3.0, 7.0) * (i + 1.0))"));
    // The weight is the band's fraction alone, saturating where an offset jumps,
    // which is what makes implicit-derivative reads safe in both.
    assert!(BREAKUP_TS.contains("hdTileW = smoothstep(0.2, 0.8, k - i);"));
    assert!(SHADER_WGSL.contains("smoothstep(0.2, 0.8, k - i),"));
    assert!(!BREAKUP_TS.contains("textureGrad") && !SHADER_WGSL.contains("textureSampleGrad(pbr_textures"));
    // Grime's noise only where the AO can grime.
    assert!(BREAKUP_TS.contains("if (ao >= ${f(GRIME_AO_NONE)}) return tinted;"));
    assert!(SHADER_WGSL.contains("if (ao >= GRIME_AO_NONE) {"));

    // The tiers: only High and up anti-tile and tint (mean-preserving detail,
    // a fifth of Medium's frame rate on an iGPU); every tier keeps the grime (a
    // darkening: a tier without it would light the corners). Native's High and
    // Ultra are `detail` 2; the browser's is `TierSpec.breakup`, and its Low is
    // the Lambert twin, grime only.
    const SETTINGS_RS: &str = include_str!("../src/settings.rs");
    assert!(SETTINGS_RS.contains("Quality::High | Quality::Ultra => 2.0,"));
    assert!(SHADER_WGSL.contains("let anti = detail > 1.5;"));
    assert!(SHADER_WGSL.contains("albedo = surface_breakup(albedo, in.world_position, ao, detail > 1.5);"));
    const SURFACE_QUALITY_TS: &str =
        include_str!("../../../packages/core/src/modules/hassault/surface-quality.ts");
    const TIERS_TS: &str = include_str!("../../../packages/core/src/modules/hassault/graphics.ts");
    assert_eq!(TIERS_TS.matches("breakup: true").count(), 1, "the full breakup is High's alone");
    assert!(between(TIERS_TS, "high: {", "}").contains("breakup: true"));
    assert!(SURFACE_QUALITY_TS.contains("setBreakupDetail(three, m, breakup);"));
    assert_eq!(PANEL_TSX.matches("setSurfaceQuality(THREE, world3dGroup, tier.lambert, tier.breakup);").count(), 2);
    assert!(SURFACE_QUALITY_TS.contains("installLowTierBreakup(three, twin, m);"));
    assert!(BREAKUP_TS.contains("setPatch(three, twin, false, false, true);"));
    assert!(BREAKUP_TS.contains("else if (spec.breakup) setPatch(three, material, false, false, true);"));
    assert_eq!(TIERS_TS.matches("lambert: true").count(), 1, "the Lambert twin is Low's alone");
    assert!(between(TIERS_TS, "low: {", "}").contains("lambert: true"));

    // The tint and grime, expression for expression.
    for (ts, wgsl) in [
        ("(world.xz + world.yy * vec2(0.37, 0.61))", "(world.xz + world.yy * vec2<f32>(0.37, 0.61))"),
        ("hdNoise(q) * 0.65 + hdNoise(q * 2.7) * 0.35", "breakup_noise(q) * 0.65 + breakup_noise(q * 2.7) * 0.35"),
        ("0.4 + 0.6 * smoothstep(0.2, 0.8, hdNoise(q * 3.1 + vec2(17.0, 5.0)))", "0.4 + 0.6 * smoothstep(0.2, 0.8, breakup_noise(q * 3.1 + vec2<f32>(17.0, 5.0)))"),
    ] {
        assert!(BREAKUP_TS.contains(ts), "browser lost {ts:?}");
        assert!(SHADER_WGSL.contains(wgsl), "native lost {wgsl:?}");
    }

    // Glass is left alone and a site letter is never offset, in both.
    assert!(BREAKUP_TS.contains("if (kind === 'glass') return { antiTile: false, breakup: false };"));
    assert!(BREAKUP_TS.contains("if (kind === 'site_a' || kind === 'site_b') return { antiTile: false, breakup: true };"));
    assert!(SHADER_WGSL.contains("if (layer_idx != 8u) {"), "native breaks up glass");
}

#[test]
fn both_clients_draw_the_same_water_surface() {
    assert_eq!(ts_const(WATER_TS, "WATER_F0"), wgsl_const("WATER_F0"));
    const RENDERER_RS: &str = include_str!("../src/renderer.rs");
    let wrap: f32 = between(RENDERER_RS, "pub const WATER_CLOCK_WRAP: f32 = ", ";").parse().unwrap();
    assert_eq!(ts_const(WATER_TS, "WATER_CLOCK_WRAP"), wrap);

    // The three waves, term for term (the vec2 spelling aside).
    for wave in [
        "d1 * (0.045 * cos(tau * (dot(d1, p) / 4.0 - 0.25 * t)))",
        "d2 * (0.035 * cos(tau * (dot(d2, p) / 2.3 - 0.375 * t)))",
        "d3 * (0.025 * cos(tau * (dot(d3, p) / 1.1 - 0.5 * t)))",
        "float tau = 6.2831853;",
    ] {
        let native = wave.replace("float tau", "let tau");
        assert!(WATER_TS.contains(wave), "browser lost {wave:?}");
        assert!(SHADER_WGSL.contains(&native), "native lost {native:?}");
    }
    for (name, dir) in [("d1", "0.80, 0.60"), ("d2", "-0.60, 0.80"), ("d3", "0.28, -0.96")] {
        assert!(WATER_TS.contains(&format!("vec2 {name} = vec2({dir});")), "browser {name}");
        assert!(SHADER_WGSL.contains(&format!("let {name} = vec2<f32>({dir});")), "native {name}");
    }

    // The environment: horizon to zenith above, horizon to ground below.
    assert!(WATER_TS.contains("mix(hdSkyHorizon, far, sqrt(min(1.0, abs(d.y))))"));
    assert!(SHADER_WGSL.contains("mix(lights.sky_horizon.rgb, far, sqrt(min(1.0, abs(d.y))))"));
    // Schlick, and the alpha it raises.
    assert!(WATER_TS.contains("pow(1.0 - max(dot(hdN, hdV), 0.0), 5.0)"));
    assert!(SHADER_WGSL.contains("pow(1.0 - max(dot(n, v), 0.0), 5.0)"));
    assert!(WATER_TS.contains("diffuseColor.a = mix(diffuseColor.a, 1.0, hdF);"));
    assert!(SHADER_WGSL.contains("let alpha = mix(in.color.a, 1.0, fresnel);"));
    // Fed the map's served colours, linearised the same way.
    assert!(WATER_TS.contains("srgbHexToLinear(atmosphere.skyZenith)"));
    assert!(WATER_TS.contains("srgbHexToLinear(atmosphere.hemiGround)"));
}
