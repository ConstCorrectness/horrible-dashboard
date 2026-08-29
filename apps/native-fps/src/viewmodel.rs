//! The gun in your hands, natively.
//!
//! A port of the browser client's `viewmodel.ts`, and deliberately a *port*
//! rather than a second design: the two clients are the same game, and a weapon
//! that sat somewhere else, kicked differently, or bobbed at another rate would
//! make switching between them feel like switching games. The constants here are
//! the TypeScript ones, unchanged — `HOME`, the kick decay, the bob rates.
//!
//! **Procedural, like everything else in this module.** AssaultCube's weapon
//! models are its copyright and are never bundled, so these are boxes and
//! cylinders in the shape of a gun. A shotgun reads as a shotgun and a sniper
//! reads as a sniper, which is the whole job.
//!
//! Two things differ from the browser and both are forced by this renderer:
//!
//! - **There is no scene graph to parent to.** three.js lets a view model be a
//!   child of the camera and does the arithmetic; here the transform is applied
//!   on the CPU and the result is drawn *in camera space*, with the view matrix
//!   left as the identity (`renderer::VIEWMODEL_PASS`). Same idea, done by hand:
//!   a view model has no world position, it has a position in front of your eyes.
//! - **It is drawn in its own pass with the depth buffer cleared**, which is what
//!   stops a 2.5-cube rifle from being sawn in half by a wall you are standing
//!   against. In three that was a `renderOrder`; here it is a second pass.
//!
//! The muzzle flash is lit by *cheating the normal*: the shader has no notion of
//! an unlit material, so the flash's vertices carry the **sun's** direction as
//! their normal, which lands them at full brightness. Cheaper than a second
//! pipeline for six triangles — and the reason `LIGHT_DIR` below has to be kept
//! in step with `lighting.wgsl.inc`.

use std::collections::HashMap;

use glam::{Mat4, Vec3};

use crate::renderer::Vertex;

/// Where the weapon rests, in camera space: right hand, below the sight line.
///
/// The sizes below are in cube units, which are worth a sanity check: the eye
/// sits 4.5 cubes up and eyes are about 1.6 m off the ground, so a cube is
/// roughly 36 cm and a 90 cm rifle is about two and a half cubes long.
const HOME: Vec3 = Vec3::new(0.92, -0.86, -1.35);

/// How long the muzzle flash stays lit. Two frames at 60 fps.
const FLASH_LIFE: f32 = 0.055;

/// Recoil decay and reload-dip rates, per second.
const KICK_DECAY: f32 = 11.0;

/// How fast the reload dip approaches when the reload's **length is unknown**.
///
/// The fallback only — see `reload_envelope`. A server too old to send
/// `reloadTime`, or a weapon whose spec has not arrived yet, leaves nothing to
/// stretch the animation across, and an exponential approach at least starts in
/// the right direction.
const RELOAD_RATE: f32 = 6.0;

/// Fractions of a reload spent taking the weapon down, and bringing it back.
///
/// **Fractions, not seconds.** That is the whole change: expressed this way the
/// dip stretches to whatever `reloadTime` the server serves, so a 1.2s pistol
/// reload and a 3.4s sniper reload both come back up on the frame the magazine
/// is full. Written in seconds, the two would need a table nobody would keep in
/// step with `weapons.py`.
const RELOAD_DIP_IN: f32 = 0.22;
const RELOAD_DIP_OUT: f32 = 0.30;

/// Seconds to take a weapon out of frame, and to bring the next one up.
///
/// The holster is faster than the draw on purpose: putting something away is a
/// motion you have already committed to, while bringing one up is the moment
/// that has to read as an *arrival*. Equal times make a swap look like a single
/// mechanical sweep in one direction.
const HOLSTER_TIME: f32 = 0.13;
const DRAW_TIME: f32 = 0.25;

/// How long the weapon waits down after a switch was *asked for*.
///
/// **This client does not own the slot** — in a match `select_weapon` sends a
/// request and the server decides. So the holster is an anticipation, and this
/// is how long it is willing to be wrong for: comfortably past a round trip,
/// and short enough that a refused switch does not leave a player staring at
/// their own knees. When the swap does arrive the hold is cancelled outright,
/// so a confirmed switch never waits out the remainder.
const HOLSTER_HOLD: f32 = 0.4;

/// The sun's direction, used as the flash's normal so it comes out at full
/// brightness.
///
/// Must equal `SUN_DIR` in `lighting.wgsl.inc`. It was `[0.35, 0.9, 0.2]` — the
/// direction of a single hardcoded wash this renderer has not had since the
/// browser's light rig was ported — and a stale copy here does not fail, it
/// dims the muzzle flash by the cosine of the angle between the two and looks
/// like the flash is simply weak.
const LIGHT_DIR: [f32; 3] = [0.523, 0.780, 0.343];

/// Where a loaded prop sits, once fitted to the box model it replaces.
///
/// The fit is **measured, not tuned per weapon**, and it is the same rule the
/// browser applies in `fitWeaponModel`: translate the prop so its bounding-box
/// centre lands on the box model's. Everything the pose is expressed in — `HOME`,
/// the bob, the sway, the recoil kick — is relative to that space, so a prop
/// that occupies it needs none of them changed.
///
/// Aligning **centres and not origins** is the whole of it. `build_hassault_weapon.mjs`
/// puts a prop's origin at the rear of its box, which on a rifle is the
/// buttstock, while the box models are built around roughly where a hand is.
/// Matching origins hangs every rifle a foot in front of the screen.
#[derive(Debug, Clone, Copy)]
pub struct PropFit {
    /// Added to the model transform to place the prop.
    pub offset: Vec3,
    /// The prop's own muzzle, in the same space, for the flare.
    pub muzzle: Vec3,
}

/// The palette. **The browser's own hex values**, not a brightened variant of
/// them.
///
/// These used to sit a notch higher, on the grounds that "the TS models are lit
/// by the scene's lights; this renderer has one ambient floor and a single
/// directional wash". That stopped being true when the rig was ported: the view
/// model is drawn with the *world* pipeline (`Renderer::render` binds
/// `self.pipeline` for it), so it gets the same hemisphere, sun, fill, Lambert
/// normalisation and ACES curve every wall does. The compensation outlived the
/// thing it compensated for and the weapon was being brightened twice, which is
/// why the native rifle read as white plastic beside the browser's gunmetal.
///
/// `0x3a4048`, `0x1c2026`, `0x4a3f33`, `0x8a929c` — `viewmodel.ts`'s `PALETTE`.
const METAL: [f32; 3] = [0.2275, 0.2510, 0.2824];
const DARK: [f32; 3] = [0.1098, 0.1255, 0.1490];
const GRIP: [f32; 3] = [0.2902, 0.2471, 0.2000];
const ACCENT: [f32; 3] = [0.5412, 0.5725, 0.6118];
const FLASH: [f32; 3] = [1.0, 0.82, 0.48];

/// The equipped skin for the weapon in your hands.
///
/// The same four things the browser's `WeaponSkin` carries, for the same reason:
/// they are all a weapon made of boxes can express. The economy also has a
/// rarity, a collection, a pattern seed and a name, and none of those change what
/// the gun looks like.
#[derive(Debug, Clone, PartialEq)]
pub struct Skin {
    pub base_color: String,
    pub accent_color: String,
    /// `solid` | `camo` | `anodized` | `custom_art` | `patina` | `fade`.
    pub pattern_type: String,
    /// 0 Factory New ... 1 Battle-Scarred.
    pub float_value: f32,
}

/// Where a skin's colours end up, once wear has been applied.
struct Palette {
    body: [f32; 3],
    dark: [f32; 3],
    grip: [f32; 3],
    accent: [f32; 3],
}

/// Grime. What a Battle-Scarred rifle is mixed toward.
const WEAR_COLOR: [f32; 3] = [0.35, 0.33, 0.31];
const BLACK: [f32; 3] = [0.0, 0.0, 0.0];
const WHITE: [f32; 3] = [1.0, 1.0, 1.0];

/// `#rrggbb` into floats, or the fallback.
///
/// The catalogue is data, and a client that rendered an unparseable colour as
/// black would show a weapon nobody designed.
fn parse_color(value: &str, fallback: [f32; 3]) -> [f32; 3] {
    let hex = value.trim().trim_start_matches('#');
    if hex.len() != 6 || !hex.chars().all(|c| c.is_ascii_hexdigit()) {
        return fallback;
    }
    let channel = |i: usize| u8::from_str_radix(&hex[i..i + 2], 16).unwrap_or(0) as f32 / 255.0;
    [channel(0), channel(2), channel(4)]
}

/// The darkest a *skinned* surface is allowed to be.
///
/// `assault_slate`'s base colour is `#09090b`, which is a legitimate design and
/// renders as a gun-shaped hole: there are no speculars here, so a near-black
/// surface has nothing to catch. The floor keeps the skin's hue and lifts only
/// its brightness, which is the smallest lie that leaves the weapon readable —
/// and it is applied **only to skins**, so a player carrying none sees exactly
/// the palette they always did. `viewmodel.ts` does the identical lift for the
/// identical reason.
const MIN_LUMA: f32 = 0.14;

fn luma(c: [f32; 3]) -> f32 {
    0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]
}

fn lift(c: [f32; 3]) -> [f32; 3] {
    let l = luma(c);
    if l >= MIN_LUMA {
        return c;
    }
    mix(c, WHITE, (MIN_LUMA - l) / (1.0 - l).max(1e-3))
}

fn mix(a: [f32; 3], b: [f32; 3], t: f32) -> [f32; 3] {
    let f = t.clamp(0.0, 1.0);
    [
        a[0] + (b[0] - a[0]) * f,
        a[1] + (b[1] - a[1]) * f,
        a[2] + (b[2] - a[2]) * f,
    ]
}

/// A skin's colours, arranged for a weapon made of boxes.
///
/// **Wear is applied here rather than being decoration**: a float value you
/// cannot see is a number the whole economy is built on and no player can check.
/// Factory New is essentially untouched; Battle-Scarred is visibly dulled toward
/// grime. A mix rather than a texture, for the same reason everything else in
/// this module is procedural.
///
/// `pattern_type` cannot be a *pattern* without textures either, so it decides
/// how the two colours are distributed across the parts instead -- enough for a
/// Fade to read as a fade and a Camo not to read as a Slate.
///
/// Deliberately the same arithmetic as `viewmodel.ts`'s `paletteFor`, and
/// duplicated for the same reason a weapon's voice is: this is presentation. A
/// drift makes a gun the wrong colour; it does not make a shot land elsewhere.
fn palette_for(skin: Option<&Skin>) -> Palette {
    let Some(skin) = skin else {
        return Palette {
            body: METAL,
            dark: DARK,
            grip: GRIP,
            accent: ACCENT,
        };
    };
    let base = parse_color(&skin.base_color, METAL);
    let accent = parse_color(&skin.accent_color, ACCENT);
    let plain = match skin.pattern_type.as_str() {
        // Two colours across the length of the weapon, which is what a fade is.
        "fade" => Palette {
            body: base,
            dark: mix(base, accent, 0.5),
            grip: accent,
            accent: mix(accent, WHITE, 0.25),
        },
        // Blotches are not available, so the parts alternate instead.
        "camo" => Palette {
            body: base,
            dark: mix(base, BLACK, 0.55),
            grip: mix(base, accent, 0.65),
            accent: mix(base, BLACK, 0.3),
        },
        // Metal dyed in one colour, with bright hardware.
        "anodized" => Palette {
            body: base,
            dark: mix(base, BLACK, 0.4),
            grip: mix(base, BLACK, 0.65),
            accent,
        },
        // `solid`, `patina`, `custom_art`: the base carries the weapon and the
        // accent picks out the barrel and the sights.
        _ => Palette {
            body: base,
            dark: mix(base, BLACK, 0.5),
            grip: mix(accent, BLACK, 0.5),
            accent,
        },
    };
    let wear = skin.float_value.clamp(0.0, 1.0) * 0.55;
    Palette {
        body: lift(mix(plain.body, WEAR_COLOR, wear)),
        dark: lift(mix(plain.dark, WEAR_COLOR, wear * 0.7)),
        grip: lift(mix(plain.grip, WEAR_COLOR, wear)),
        accent: lift(mix(plain.accent, WEAR_COLOR, wear)),
    }
}

/// The equipped skin for each weapon, keyed by weapon id.
///
/// Takes the whole inventory because that is what the node serves -- there is no
/// "what am I wearing" route, and asking for one to save this filter would be a
/// second source of truth for the same fact. An instance whose definition did not
/// come with it is **skipped rather than guessed**: without a base colour there
/// is no skin, and inventing one would put a colour on the weapon that the
/// armoury never showed the player.
pub fn equipped_skins(inventory: &[crate::api::SkinInstance]) -> HashMap<String, Skin> {
    let mut out = HashMap::new();
    for item in inventory {
        if !item.is_equipped {
            continue;
        }
        let Some(def) = &item.definition else {
            continue;
        };
        out.insert(
            def.weapon_id.clone(),
            Skin {
                base_color: def.base_color.clone(),
                accent_color: def.accent_color.clone(),
                pattern_type: def.pattern_type.clone(),
                float_value: item.float_value,
            },
        );
    }
    out
}

/// What the animation needs to know about this frame.
pub struct Frame {
    /// Horizontal speed in cubes per second, for the walk cycle.
    pub speed: f32,
    pub on_ground: bool,
    pub reloading: bool,
    /// View angles in **radians**, so the weapon can lag a turn slightly instead
    /// of being welded to the screen.
    pub yaw: f32,
    pub pitch: f32,
    /// False while dead, or before the pointer has been captured.
    pub visible: bool,
    /// The run speed the walk cycle is measured against.
    pub move_speed: f32,
    /// How far through the reload we are, 0..1, or `None` when that cannot be
    /// known — the weapon's `reloadTime` has not been served, or is zero.
    ///
    /// `Option` rather than a bare float, and the distinction is load-bearing:
    /// `Some(0.0)` is "the reload has just started" and `None` is "there is no
    /// length to measure against". Collapsed into one number, a server that
    /// serves no `reloadTime` would look like a reload permanently at its first
    /// frame — the weapon would go down and stay there.
    pub reload_progress: Option<f32>,
}

/// How long one inspect takes, in seconds.
///
/// Long enough to read the weapon, short enough that it is over before it costs
/// you a gunfight — and it is interruptible anyway, so this is a maximum rather
/// than a commitment.
const INSPECT_DURATION: f32 = 1.5;

/// How long the pose takes to reach full weight, and to return.
///
/// The fall is longer than the rise on purpose: a flourish that snaps back to
/// the aim faster than it left reads as being yanked away.
const INSPECT_RISE: f32 = 0.30;
const INSPECT_FALL: f32 = 0.46;

/// The roll at full weight, before the turn adds to it, in radians.
const INSPECT_ROLL: f32 = 2.15;

/// How much further the weapon turns across the hold, in radians.
///
/// **This is the whole difference between an inspect and a freeze frame.** The
/// pose used to be one scalar driving every axis, so the weapon travelled out,
/// stopped dead for the ~0.7s of the hold, and retraced its path — which reads
/// as a stutter rather than as somebody turning a weapon over. Keeping it
/// rotating through the hold is what makes the same journey read as deliberate.
const INSPECT_TURN: f32 = 0.85;

/// How far the lift leads the roll, in seconds.
///
/// Every axis starting and stopping on the same frame is the signature of a
/// single rigid transform, which is exactly what this is. Sixty milliseconds of
/// lead costs nothing and buys the weapon coming up first and rolling over as it
/// goes, instead of doing both as one motion.
const INSPECT_LEAD: f32 = 0.06;

/// Smootherstep — Perlin's, with a continuous second derivative.
///
/// Smoothstep's acceleration jumps at both ends; over a 0.3s rise that is a
/// visible tick as the weapon leaves rest. This costs two more multiplies.
fn ease(x: f32) -> f32 {
    let x = x.clamp(0.0, 1.0);
    // Clamped on the way out as well as in. The polynomial is monotonic on [0,1]
    // and cannot exceed 1 algebraically, but in floats it lands just past it
    // near the top — enough for a caller that trusts the range to scale a pose
    // very slightly past the pose it was told about.
    (x * x * x * (x * (x * 6.0 - 15.0) + 10.0)).clamp(0.0, 1.0)
}

/// The inspect pose's weight over its own duration: ease in, hold, ease out.
///
/// Eased at both ends rather than linear. A linear ramp reverses direction
/// instantly at the hold, which reads as the animation being cut off and
/// restarted — the one thing a "look at this weapon" flourish must not do.
fn inspect_envelope(t: f32) -> f32 {
    let out = INSPECT_DURATION - INSPECT_FALL;
    ease(if t < INSPECT_RISE {
        t / INSPECT_RISE
    } else if t > out {
        1.0 - (t - out) / INSPECT_FALL
    } else {
        1.0
    })
}

/// How far through the turn the weapon is, 0..1, monotonic across the whole
/// animation.
///
/// Deliberately **not** the envelope: the envelope comes back down, and a roll
/// driven by it unwinds along the path it wound up. This only ever climbs, so
/// the weapon keeps turning the same way throughout — and because the roll is
/// still *scaled* by the envelope, it lands back at rest anyway.
fn inspect_turn(t: f32) -> f32 {
    ease(t / INSPECT_DURATION)
}

/// The reload dip's weight across the reload's own length: down, hold, up.
///
/// Takes a **fraction**, so the shape is the same on every weapon and its
/// duration is whatever the server said. The old behaviour — an exponential
/// approach at a fixed rate — did neither end well: a fast reload was still on
/// its way down when the magazine filled, and a slow one sat at the bottom for
/// two seconds and then snapped back up.
fn reload_envelope(progress: f32) -> f32 {
    let p = progress.clamp(0.0, 1.0);
    ease(if p < RELOAD_DIP_IN {
        p / RELOAD_DIP_IN
    } else if p > 1.0 - RELOAD_DIP_OUT {
        (1.0 - p) / RELOAD_DIP_OUT
    } else {
        1.0
    })
}

/// Move `value` toward `target` at a fixed rate, arriving exactly.
///
/// Linear rather than the exponential approach used for sway and kick, because
/// a swap is an *action with a length*: an exponential never arrives, so the
/// weapon would be perpetually a few percent stowed and the draw would have no
/// moment at which it is over.
fn approach(value: f32, target: f32, seconds: f32, dt: f32) -> f32 {
    let step = dt / seconds.max(1e-4);
    if target > value {
        (value + step).min(target)
    } else {
        (value - step).max(target)
    }
}

/// One part of a weapon, in the model's own space.
struct Part {
    size: [f32; 3],
    at: [f32; 3],
    rot: [f32; 3],
    color: [f32; 3],
    /// A cylinder rather than a box: `size` is then `[radius, radius, length]`.
    round: bool,
}

/// A built weapon: its geometry, where its muzzle is, and how it is held.
struct Shape {
    verts: Vec<Vertex>,
    muzzle: Vec3,
    rest: Vec3,
}

pub struct WeaponViewModel {
    weapon: String,
    /// The skin the current model was built with. The palette is baked into the
    /// vertices, so a change of skin has to rebuild the model -- and an
    /// unchanged one must not.
    skin: Option<Skin>,
    shape: Option<Shape>,
    kick: f32,
    bob_phase: f32,
    reload_t: f32,
    last_yaw: Option<f32>,
    last_pitch: f32,
    sway_x: f32,
    sway_y: f32,
    /// Smoothed walk factor. The *input* is a step function, and a bob that snaps
    /// to full amplitude on the frame W goes down looks like a glitch, not a
    /// stride.
    walk: f32,
    flash_age: f32,
    /// How far out of frame the weapon is, 0..1. 1 is fully stowed.
    stow: f32,
    /// Seconds left of a holster that was *asked for* but not yet confirmed.
    /// See `HOLSTER_HOLD`.
    holster_hold: f32,
    /// Set while the weapon is not being drawn at all, so that coming back —
    /// respawning, or the match starting — plays a draw rather than having the
    /// gun appear already at rest. Without it, the only swap that reads as an
    /// action is one you asked for, and spawning into a fight does not.
    draw_on_return: bool,
    /// How far through the inspect animation we are, in seconds, or `None`.
    ///
    /// A *duration* rather than a bool with a separate clock: the pose is a
    /// function of how far in it is, and every frame of it — including the fact
    /// that it has finished — falls out of one number.
    inspect: Option<f32>,
    /// The pivot's transform for this frame, rebuilt by `update`.
    transform: Mat4,
    /// Where a loaded prop sits relative to the box model it replaces, and the
    /// muzzle that comes with it.
    ///
    /// `None` means the boxes are what is being drawn — a weapon with no prop, a
    /// GLB that failed to parse, or one not uploaded yet. The boxes are the
    /// fallback rather than the loading state's placeholder: they are a complete
    /// working weapon and always were.
    prop: Option<PropFit>,
    visible: bool,
    /// A flash that is a different size every frame it is lit reads better than
    /// a fade, and needs no crate: two shots never look identical.
    rng: u32,
}

impl Default for WeaponViewModel {
    fn default() -> WeaponViewModel {
        WeaponViewModel {
            weapon: String::new(),
            inspect: None,
            skin: None,
            shape: None,
            kick: 0.0,
            bob_phase: 0.0,
            reload_t: 0.0,
            last_yaw: None,
            last_pitch: 0.0,
            sway_x: 0.0,
            sway_y: 0.0,
            walk: 0.0,
            stow: 0.0,
            holster_hold: 0.0,
            draw_on_return: true,
            flash_age: FLASH_LIFE,
            transform: Mat4::IDENTITY,
            prop: None,
            visible: false,
            rng: 0x9e37_79b9,
        }
    }
}

impl WeaponViewModel {
    /// Swap the model. A no-op when already holding this weapon in this skin, so
    /// the frame loop can call it every frame with whatever the server last said.
    ///
    /// The skin is part of the identity rather than a property applied
    /// afterwards: the colours are baked into the vertices, so equipping one
    /// without rebuilding would leave the previous skin on the gun with nothing
    /// anywhere to say that a change had been made.
    pub fn set_weapon(&mut self, id: &str, skin: Option<&Skin>) {
        if id == self.weapon && skin == self.skin.as_ref() {
            return;
        }
        // **The id, not the skin.** `set_weapon` is also how a skin is equipped,
        // and re-holstering because somebody changed a colour would make the
        // weapon dive out of frame in the middle of a firefight.
        let swapped = id != self.weapon;
        self.weapon = id.to_string();
        self.skin = skin.cloned();
        self.shape = if id.is_empty() {
            None
        } else {
            Some(build(id, skin))
        };
        if swapped && !id.is_empty() {
            // Snapped fully stowed rather than eased there: the model has
            // *already* changed, so there is nothing left to take down — easing
            // from here would lower the new weapon out of frame and then raise
            // it again. The hold is cleared because the swap it was waiting for
            // has arrived.
            self.stow = 1.0;
            self.holster_hold = 0.0;
        }
    }

    /// A switch was asked for: take the weapon down while we wait to hear.
    ///
    /// Called from the key, **not** from the server's answer, and that is the
    /// point — a swap that only began once the server confirmed would start a
    /// round trip after the press and read as input lag. In a match the server
    /// owns the slot, so this is a guess; `HOLSTER_HOLD` is how long the guess
    /// is allowed to stand before the weapon comes back up on its own.
    ///
    /// Purely cosmetic: nothing here changes what is held, what can be fired,
    /// or what the wire says. A refused switch costs a dip and nothing else.
    pub fn holster(&mut self) {
        if self.shape.is_some() {
            self.holster_hold = HOLSTER_HOLD;
        }
    }

    pub fn weapon(&self) -> &str {
        &self.weapon
    }

    /// A shot left the barrel: kick the model and light the muzzle.
    ///
    /// Called from the server's own `shot` effect rather than from the fire key.
    /// The browser client can drive this off its local trigger because it has a
    /// trigger *controller* that knows about the fire interval, the magazine and
    /// being dead; this client does not, so the key would flash on shots the
    /// server refused.
    pub fn fire(&mut self) {
        // Additive but capped: holding an assault rifle should climb to a steady
        // shake, not to a weapon behind the player's ear.
        self.kick = (self.kick + 0.8).min(1.0);
        self.flash_age = 0.0;
        // Firing cancels an inspect. It has to: the animation swings the barrel
        // away from the crosshair, and a shot that came out of a weapon pointing
        // at the floor would be a picture of a shot that did not happen. The
        // server has already resolved it against the real view angles.
        self.inspect = None;
    }

    /// Start the inspect animation — the weapon turned over in the hands.
    ///
    /// Purely cosmetic, purely local, and deliberately *not* on the wire: it
    /// changes nothing about where a shot goes, what can be hit, or what anyone
    /// else sees. That is what makes it safe to interrupt at any moment, which
    /// in turn is what makes it usable in a match rather than a state you have
    /// to wait out.
    ///
    /// Re-pressing while it runs restarts it rather than queueing a second pass:
    /// the button means "show me the gun", and it should answer every press.
    pub fn inspect(&mut self) {
        if self.shape.is_some() {
            self.inspect = Some(0.0);
        }
    }

    /// Whether the inspect animation is running. The HUD reads it to name what
    /// the weapon is doing, so it is not a fact this module keeps to itself.
    pub fn inspecting(&self) -> bool {
        self.inspect.is_some()
    }

    /// Advance the animation.
    ///
    /// Everything here is a *local* effect — nothing the server knows or cares
    /// about, the same concession client-side recoil makes.
    pub fn update(&mut self, dt: f32, frame: &Frame) {
        self.visible = frame.visible && self.shape.is_some();
        if !self.visible {
            // Reset the walk cycle rather than freezing it: coming back from
            // death mid-stride would otherwise resume with the gun wherever it
            // happened to be.
            self.bob_phase = 0.0;
            self.last_yaw = None;
            self.walk = 0.0;
            // Dying mid-inspect must not resume it on respawn: the animation is
            // a thing you asked for, not a state of the weapon.
            self.inspect = None;
            // Nor may a half-finished holster: the request that started it
            // belongs to a life that has ended.
            self.holster_hold = 0.0;
            self.draw_on_return = true;
            return;
        }

        if self.draw_on_return {
            // First frame back. Start fully stowed so the weapon comes up into
            // frame rather than materialising at rest — a spawn is an arrival.
            self.draw_on_return = false;
            self.stow = 1.0;
        }

        let target = (frame.speed / frame.move_speed.max(0.001)).clamp(0.0, 1.0);
        self.walk += (target - self.walk) * (dt * 8.0).min(1.0);
        let walk = self.walk;
        self.bob_phase += dt * (4.5 + walk * 7.5);
        // Airborne, the weapon settles: bobbing in mid-air reads as a bug.
        let bob_amount = if frame.on_ground { walk } else { walk * 0.15 };

        // Turning drags the weapon behind the view for a fraction of a second,
        // which is the difference between a held object and a decal on the
        // screen. The yaw delta is taken the short way round: this client wraps
        // yaw at 360°, so a turn across the seam would otherwise fling the gun.
        let yaw_delta = match self.last_yaw {
            None => 0.0,
            Some(prev) => wrap_angle(frame.yaw - prev),
        };
        let pitch_delta = frame.pitch - self.last_pitch;
        self.last_yaw = Some(frame.yaw);
        self.last_pitch = frame.pitch;
        let settle = (dt * 9.0).min(1.0);
        self.sway_x += ((-yaw_delta * 2.2).clamp(-0.22, 0.22) - self.sway_x) * settle;
        self.sway_y += ((-pitch_delta * 1.6).clamp(-0.18, 0.18) - self.sway_y) * settle;

        self.kick -= self.kick * (dt * KICK_DECAY).min(1.0);
        // The dip, on the server's clock where there is one. `reload_progress`
        // is `None` only when there is no length to stretch across, and the old
        // fixed-rate approach is kept for exactly that case — see `RELOAD_RATE`.
        self.reload_t = match frame.reload_progress {
            Some(p) => reload_envelope(p),
            None => {
                let target = if frame.reloading { 1.0 } else { 0.0 };
                self.reload_t + (target - self.reload_t) * (dt * RELOAD_RATE).min(1.0)
            }
        };

        // The swap. A holster that was asked for holds the weapon down until it
        // expires; anything else brings it back up, so a refused switch recovers
        // on its own with nothing anywhere having to notice it was refused.
        self.holster_hold = (self.holster_hold - dt).max(0.0);
        let stowing = self.holster_hold > 0.0;
        self.stow = approach(
            self.stow,
            if stowing { 1.0 } else { 0.0 },
            if stowing { HOLSTER_TIME } else { DRAW_TIME },
            dt,
        );
        // A weapon on its way in or out is not one you are looking at. Both of
        // the other poses swing the barrel off the crosshair as well, and three
        // of them fighting over one pivot is the picture of a broken rig.
        if self.stow > 0.0 {
            self.inspect = None;
        }

        // A reload takes the weapon away for its own animation, and two poses
        // fighting over the same pivot is one that looks broken. The reload
        // wins because it is the one the *server* is doing.
        if frame.reloading {
            self.inspect = None;
        }
        // The envelope: in, hold, out. Advanced before it is read, so the frame
        // it completes on is the frame it is back at rest rather than one after.
        //
        // Three numbers, not one: the weight (how much of the pose is applied),
        // the lift (the same weight, run slightly ahead so the gun rises before
        // it rolls) and the turn (monotonic, so the roll keeps going through the
        // hold instead of freezing). See the constants above.
        let (inspect, lift, turn) = match self.inspect {
            None => (0.0, 0.0, 0.0),
            Some(t) => {
                let t = t + dt;
                if t >= INSPECT_DURATION {
                    self.inspect = None;
                    (0.0, 0.0, 0.0)
                } else {
                    self.inspect = Some(t);
                    (
                        inspect_envelope(t),
                        inspect_envelope(t + INSPECT_LEAD),
                        inspect_turn(t),
                    )
                }
            }
        };

        let bob_x = (self.bob_phase * 0.5).cos() * 0.05 * bob_amount;
        let bob_y = (self.bob_phase).sin().abs() * -0.055 * bob_amount;

        // Where the inspect pose takes the weapon: in towards the centre of
        // the screen, up, and rolled most of the way over so the side of the
        // receiver — which is where a skin's pattern actually lives — faces the
        // camera. A pose that only lifted the gun would show the same face it
        // already shows.
        //
        // The translation rides `lift` and the rotation rides `inspect`, which
        // is the lead: the weapon is already on its way up before it starts
        // turning, and it finishes unrolling after it has come back down.
        //
        // The roll is `inspect * (ROLL + TURN * turn)` rather than `inspect *
        // ROLL`. The envelope still scales it, so it starts and ends at rest;
        // the turn is what keeps it moving in between.
        // The swap, eased rather than applied raw: a linear `stow` moved
        // linearly reads as the gun being winched, and the arrival is the part
        // that has to land. Down and slightly back, muzzle tipping toward the
        // floor — far enough that the model is genuinely clear of the frame,
        // since a weapon that stops just short of gone reads as a bug.
        let stow = ease(self.stow);
        let position = Vec3::new(
            HOME.x + bob_x + self.sway_x - lift * 0.30,
            HOME.y + bob_y + self.sway_y - self.reload_t * 0.55 + lift * 0.16 - stow * 1.15,
            HOME.z + self.kick * 0.28 + lift * 0.20 + stow * 0.22,
        );
        let rotation = Vec3::new(
            self.kick * -0.16 + self.reload_t * 0.7 + bob_y * 0.4 + inspect * 0.34 + stow * 1.05,
            self.sway_x * 0.7 + self.reload_t * 0.25 - inspect * 0.95,
            self.sway_x * 0.5
                + bob_x * 0.6
                + inspect * (INSPECT_ROLL + INSPECT_TURN * turn)
                + stow * 0.35,
        );
        self.transform = Mat4::from_translation(position)
            * Mat4::from_euler(glam::EulerRot::XYZ, rotation.x, rotation.y, rotation.z);
        self.flash_age += dt;
    }

    /// Fit a prop to the weapon currently held, and start drawing it.
    ///
    /// Returns `None` — and leaves the boxes drawing — when there is no shape to
    /// fit against or the prop is degenerate. A prop is an upgrade over a
    /// working model, never a dependency of one.
    pub fn fit_prop(&mut self, min: Vec3, max: Vec3) -> Option<PropFit> {
        let shape = self.shape.as_ref()?;
        if !min.is_finite() || !max.is_finite() || (max - min).min_element() <= 0.0 {
            return None;
        }
        let mut box_min = Vec3::splat(f32::INFINITY);
        let mut box_max = Vec3::splat(f32::NEG_INFINITY);
        for v in &shape.verts {
            let p = Vec3::from(v.position);
            box_min = box_min.min(p);
            box_max = box_max.max(p);
        }
        if !box_min.is_finite() || !box_max.is_finite() {
            return None;
        }
        let offset = (box_min + box_max) * 0.5 - (min + max) * 0.5;
        // Front-centre of the fitted prop. The converter points every barrel
        // down -Z, so the front is the minimum z.
        let centre = (min + max) * 0.5 + offset;
        let fit = PropFit {
            offset,
            muzzle: Vec3::new(centre.x, centre.y, min.z + offset.z),
        };
        self.prop = Some(fit);
        Some(fit)
    }

    /// Go back to the box model — a weapon with no prop, or one that failed.
    pub fn clear_prop(&mut self) {
        self.prop = None;
    }

    /// The matrix a resident prop is drawn with, or `None` when the boxes are.
    ///
    /// Deliberately **without** the shape's `rest` rotation, which the box models
    /// need and a prop does not: `rest` describes how a pile of boxes had to be
    /// turned to look like a weapon, and the GLB is exported already oriented.
    ///
    /// **`visible` is checked here, exactly as `vertices` checks it.** The
    /// renderer decides whether to run the view-model pass at all from this and
    /// the vertex count together, so a pose returned while dead or in the menu
    /// would put a gun on screen at the one moment there is meant to be none —
    /// the mirror image of the bug that made props draw *only* during a muzzle
    /// flash.
    pub fn prop_model(&self) -> Option<Mat4> {
        if !self.visible {
            return None;
        }
        let fit = self.prop?;
        Some(self.transform * Mat4::from_translation(fit.offset))
    }

    /// This frame's vertices, in camera space, ready for the view-model pass.
    ///
    /// Rebuilt per frame rather than transformed on the GPU: a weapon is a few
    /// hundred vertices and this is one matrix multiply each, which costs less
    /// than the second uniform and bind group the alternative needs.
    ///
    /// With a prop loaded this emits **only the muzzle flare**: the weapon
    /// itself is geometry on the GPU that never comes back to the CPU, and
    /// pushing the boxes as well would draw a box gun inside the real one.
    pub fn vertices(&mut self, out: &mut Vec<Vertex>) {
        out.clear();
        if !self.visible {
            return;
        }
        // Copied out before the borrow of `self.shape`: the flare's random scale
        // needs `&mut self`, and holding a reference into the shape across it
        // borrows `self` twice.
        let Some((muzzle, rest)) = self.shape.as_ref().map(|s| (s.muzzle, s.rest)) else {
            return;
        };
        let flare = self.flash_age < FLASH_LIFE;
        let scale = if flare {
            0.85 + self.next_random() * 0.5
        } else {
            1.0
        };
        let Some(shape) = &self.shape else { return };
        let model = self.transform * Mat4::from_euler(glam::EulerRot::XYZ, rest.x, rest.y, rest.z);
        if self.prop.is_none() {
            for v in &shape.verts {
                out.push(transform_vertex(&model, v));
            }
        }
        // The flare rides the **prop's** muzzle when there is one — its barrel
        // is somewhere else entirely, and a flash left at the box model's muzzle
        // hangs in the air beside the gun.
        let (model, muzzle) = match &self.prop {
            Some(fit) => (
                self.transform * Mat4::from_translation(fit.offset),
                fit.muzzle,
            ),
            None => (model, muzzle),
        };
        if flare {
            if let Some((radius, length, sides)) = flash_shape(&self.weapon) {
                // Squeezed in x/y only, exactly as the browser scales it, so the
                // flare's length stays put and only its girth varies.
                let flash = model
                    * Mat4::from_translation(muzzle - Vec3::new(0.0, 0.0, 0.2))
                    * Mat4::from_scale(Vec3::new(scale, scale, 1.0));
                // **The bloom is a second cone, not a post-process.** There is
                // no bright-pass, no blur target and no second pipeline here —
                // the view model is drawn with the world pipeline — so a halo
                // wider and shorter than the core, at a fraction of its
                // opacity, is the whole of it. Drawn *first*, so the core lands
                // on top of it rather than being averaged into it.
                //
                // Alpha rides the flare's own age, which is the difference
                // between a bloom and a second flash: the halo is already
                // fading while the core is still at full brightness.
                let fade = 1.0 - (self.flash_age / FLASH_LIFE).clamp(0.0, 1.0);
                for v in flash_cone(radius * 2.1, length * 0.55, sides) {
                    let mut v = transform_vertex(&flash, &v);
                    v.color = [
                        v.color[0] * 0.55 * fade,
                        v.color[1] * 0.48 * fade,
                        v.color[2] * 0.36 * fade,
                    ];
                    out.push(v);
                }
                for v in flash_cone(radius, length, sides) {
                    out.push(transform_vertex(&flash, &v));
                }
            }
        }
    }

    /// A cheap xorshift. Not for anything that matters — see `vertices`.
    fn next_random(&mut self) -> f32 {
        self.rng ^= self.rng << 13;
        self.rng ^= self.rng >> 17;
        self.rng ^= self.rng << 5;
        (self.rng >> 8) as f32 / (1u32 << 24) as f32
    }
}

/// The short way round, in radians. Yaw wraps at 2π and a turn across the seam
/// is a small movement, not a full revolution.
fn wrap_angle(a: f32) -> f32 {
    let tau = std::f32::consts::TAU;
    let mut d = a % tau;
    if d > std::f32::consts::PI {
        d -= tau;
    }
    if d < -std::f32::consts::PI {
        d += tau;
    }
    d
}

fn transform_vertex(m: &Mat4, v: &Vertex) -> Vertex {
    let p = m.transform_point3(Vec3::from(v.position));
    // The direction, not the point: a normal carries no translation, and running
    // it through `transform_point3` would push every face's shading around with
    // the weapon's position. There is no non-uniform scale here, so the rotation
    // part is enough — no inverse transpose needed.
    let n = m
        .transform_vector3(Vec3::from(v.normal))
        .normalize_or_zero();
    Vertex {
        position: p.to_array(),
        normal: n.to_array(),
        color: v.color,
    }
}

/// The muzzle flare: a five-sided cone lying along -Z, pointing away.
/// How a weapon's muzzle flash is shaped, by weapon id.
///
/// `(radius, length, sides)`, or `None` for a weapon that has no muzzle.
///
/// **Matched on the id, like `build`, and deliberately not served.** This is the
/// same call `audio::weapon_voice` documents: a served number is one the client
/// *acts* on, and a flash that drifted from the server's idea of a weapon makes
/// a gun look wrong rather than making a shot land somewhere else. The shapes
/// come from what the weapon is — a shotgun throws a wide short bloom, a sniper
/// a long narrow lance — which is the cue that tells a player at the far end of
/// a corridor what is being fired at them.
///
/// The knife returns `None`. It reaches here because a swing is resolved as a
/// `Shot` like everything else, and a flare on it would light up the one weapon
/// whose whole value is that carrying it gives nothing away.
fn flash_shape(id: &str) -> Option<(f32, f32, usize)> {
    match id {
        "knife" => None,
        "pistol" => Some((0.13, 0.30, 5)),
        "shotgun" => Some((0.30, 0.34, 7)),
        "sniper" => Some((0.13, 0.78, 6)),
        // The rifle, and anything the server has grown since this client was
        // built — a new weapon should look ordinary, not invisible.
        _ => Some((0.17, 0.44, 5)),
    }
}

/// A cone of `sides` triangles, `length` long and `radius` across at the base.
fn flash_cone(radius: f32, length: f32, sides: usize) -> Vec<Vertex> {
    let mut out = Vec::with_capacity(sides * 3);
    for i in 0..sides {
        let a0 = (i as f32 / sides as f32) * std::f32::consts::TAU;
        let a1 = ((i + 1) as f32 / sides as f32) * std::f32::consts::TAU;
        let p0 = [a0.cos() * radius, a0.sin() * radius, 0.0];
        let p1 = [a1.cos() * radius, a1.sin() * radius, 0.0];
        let apex = [0.0, 0.0, -length];
        // Wound so the outward side faces the viewer standing behind the gun.
        for p in [p1, p0, apex] {
            out.push(Vertex {
                position: p,
                // See the module header: the shader's own light direction, so the
                // flare lands at full brightness without a second pipeline.
                normal: LIGHT_DIR,
                color: FLASH,
            });
        }
    }
    out
}

/// The weapon, by id.
///
/// Ids are the backend's (`weapons.py`): knife, pistol, assault, shotgun,
/// sniper. An unknown id gets the rifle rather than nothing — a new weapon
/// should look wrong, not invisible.
fn build(id: &str, skin: Option<&Skin>) -> Shape {
    let palette = palette_for(skin);
    let (metal, dark, grip, accent) = (palette.body, palette.dark, palette.grip, palette.accent);
    let (parts, muzzle, rest): (Vec<Part>, Vec3, Vec3) = match id {
        "knife" => (
            vec![
                part([0.14, 0.17, 0.6], [0.0, 0.0, 0.1], grip),
                part([0.05, 0.05, 0.1], [0.0, 0.0, -0.24], accent),
                // Blade: a flat box, already tapered in its own proportions
                // rather than by a scale on the mesh, which this builder has no
                // node to hang.
                part([0.045, 0.18, 1.0], [0.0, 0.03, -0.8], accent),
            ],
            Vec3::new(0.0, 0.03, -1.3),
            Vec3::new(0.06, -0.32, 0.22),
        ),
        "pistol" => (
            vec![
                part([0.22, 0.3, 1.05], [0.0, 0.0, -0.5], metal),
                tube(0.05, 0.3, [0.0, 0.0, -1.12], accent),
                rotated(
                    [0.2, 0.62, 0.34],
                    [0.0, -0.42, -0.02],
                    DARK,
                    [0.3, 0.0, 0.0],
                ),
                // Trigger guard, as a bar under the receiver: small, but its
                // absence is what makes a box read as a box.
                part([0.1, 0.06, 0.3], [0.0, -0.24, -0.35], dark),
                part([0.06, 0.08, 0.05], [0.0, 0.19, -0.98], accent),
            ],
            Vec3::new(0.0, 0.0, -1.3),
            Vec3::new(0.0, -0.05, 0.0),
        ),
        "shotgun" => (
            vec![
                tube(0.08, 2.1, [-0.09, 0.02, -1.45], metal),
                tube(0.08, 2.1, [0.09, 0.02, -1.45], metal),
                part([0.34, 0.32, 0.8], [0.0, -0.02, -0.3], dark),
                // Pump, forward under the barrels.
                part([0.3, 0.2, 0.55], [0.0, -0.16, -1.15], grip),
                rotated(
                    [0.24, 0.36, 0.9],
                    [0.0, -0.16, 0.5],
                    GRIP,
                    [-0.08, 0.0, 0.0],
                ),
            ],
            Vec3::new(0.0, 0.02, -2.5),
            Vec3::new(0.0, -0.04, 0.0),
        ),
        "sniper" => (
            vec![
                tube(0.055, 2.5, [0.0, 0.02, -1.75], metal),
                part([0.26, 0.32, 1.1], [0.0, -0.04, -0.5], dark),
                // Scope on two mounts.
                tube(0.12, 0.9, [0.0, 0.32, -0.85], dark),
                part([0.08, 0.18, 0.08], [0.0, 0.18, -0.5], metal),
                part([0.08, 0.18, 0.08], [0.0, 0.18, -1.2], metal),
                // Bolt handle, out to the right where you would work it.
                part([0.3, 0.07, 0.07], [0.18, 0.02, -0.15], accent),
                rotated([0.2, 0.5, 0.3], [0.0, -0.34, -0.35], dark, [0.18, 0.0, 0.0]),
                rotated(
                    [0.24, 0.4, 1.1],
                    [0.0, -0.14, 0.55],
                    GRIP,
                    [-0.06, 0.0, 0.0],
                ),
            ],
            Vec3::new(0.0, 0.02, -3.0),
            Vec3::new(0.0, -0.03, 0.0),
        ),
        // Assault rifle, and the fallback for anything new.
        _ => (
            vec![
                part([0.26, 0.36, 1.6], [0.0, 0.0, -0.8], dark),
                tube(0.055, 1.0, [0.0, 0.04, -2.0], metal),
                // Top rail and front sight.
                part([0.14, 0.09, 0.9], [0.0, 0.23, -0.7], metal),
                part([0.07, 0.16, 0.06], [0.0, 0.28, -2.35], accent),
                // Magazine, raked forward the way a curved one sits.
                rotated(
                    [0.2, 0.66, 0.32],
                    [0.0, -0.46, -0.85],
                    METAL,
                    [-0.14, 0.0, 0.0],
                ),
                rotated(
                    [0.18, 0.46, 0.3],
                    [0.0, -0.32, -0.2],
                    DARK,
                    [0.34, 0.0, 0.0],
                ),
                rotated(
                    [0.22, 0.32, 0.75],
                    [0.0, -0.02, 0.35],
                    DARK,
                    [-0.04, 0.0, 0.0],
                ),
            ],
            Vec3::new(0.0, 0.04, -2.5),
            Vec3::new(0.0, -0.04, 0.0),
        ),
    };

    let mut verts = Vec::new();
    for p in &parts {
        let m = Mat4::from_translation(Vec3::from(p.at))
            * Mat4::from_euler(glam::EulerRot::XYZ, p.rot[0], p.rot[1], p.rot[2]);
        let local = if p.round {
            cylinder(p.size[0], p.size[2], p.color)
        } else {
            box_verts(p.size, p.color)
        };
        for v in &local {
            verts.push(transform_vertex(&m, v));
        }
    }
    Shape {
        verts,
        muzzle,
        rest,
    }
}

fn part(size: [f32; 3], at: [f32; 3], color: [f32; 3]) -> Part {
    rotated(size, at, color, [0.0, 0.0, 0.0])
}

fn rotated(size: [f32; 3], at: [f32; 3], color: [f32; 3], rot: [f32; 3]) -> Part {
    Part {
        size,
        at,
        rot,
        color,
        round: false,
    }
}

/// A cylinder lying along -Z, which is the direction every barrel points.
fn tube(radius: f32, length: f32, at: [f32; 3], color: [f32; 3]) -> Part {
    Part {
        size: [radius, radius, length],
        at,
        rot: [0.0, 0.0, 0.0],
        color,
        round: true,
    }
}

/// A box centred on the origin, wound counter-clockwise seen from outside.
///
/// The winding matters as much as it does for a body: the pipeline culls back
/// faces, so a face wound the wrong way is simply not there — and on a closed
/// box that reads as seeing *through* the gun rather than as a missing triangle.
fn box_verts(size: [f32; 3], color: [f32; 3]) -> Vec<Vertex> {
    let (hx, hy, hz) = (size[0] / 2.0, size[1] / 2.0, size[2] / 2.0);
    let faces: [([f32; 3], [[f32; 3]; 4]); 6] = [
        (
            [1.0, 0.0, 0.0],
            [[hx, -hy, hz], [hx, -hy, -hz], [hx, hy, -hz], [hx, hy, hz]],
        ),
        (
            [-1.0, 0.0, 0.0],
            [
                [-hx, -hy, -hz],
                [-hx, -hy, hz],
                [-hx, hy, hz],
                [-hx, hy, -hz],
            ],
        ),
        (
            [0.0, 1.0, 0.0],
            [[-hx, hy, hz], [hx, hy, hz], [hx, hy, -hz], [-hx, hy, -hz]],
        ),
        (
            [0.0, -1.0, 0.0],
            [
                [-hx, -hy, -hz],
                [hx, -hy, -hz],
                [hx, -hy, hz],
                [-hx, -hy, hz],
            ],
        ),
        (
            [0.0, 0.0, 1.0],
            [[-hx, -hy, hz], [hx, -hy, hz], [hx, hy, hz], [-hx, hy, hz]],
        ),
        (
            [0.0, 0.0, -1.0],
            [
                [hx, -hy, -hz],
                [-hx, -hy, -hz],
                [-hx, hy, -hz],
                [hx, hy, -hz],
            ],
        ),
    ];
    let mut out = Vec::with_capacity(36);
    for (normal, corners) in faces {
        for idx in [0usize, 1, 2, 0, 2, 3] {
            out.push(Vertex {
                position: corners[idx],
                normal,
                color,
            });
        }
    }
    out
}

/// A capped cylinder along Z, centred on the origin. Ten sides, like the
/// browser's — enough that a barrel is round and not so many that a gun costs
/// more vertices than a player.
fn cylinder(radius: f32, length: f32, color: [f32; 3]) -> Vec<Vertex> {
    let sides = 10;
    let hz = length / 2.0;
    let mut out = Vec::with_capacity(sides * 12);
    let at = |i: usize| {
        let a = (i as f32 / sides as f32) * std::f32::consts::TAU;
        (a.cos() * radius, a.sin() * radius, a.cos(), a.sin())
    };
    for i in 0..sides {
        let (x0, y0, nx0, ny0) = at(i);
        let (x1, y1, nx1, ny1) = at(i + 1);
        let quad = [
            ([x0, y0, -hz], [nx0, ny0, 0.0]),
            ([x1, y1, -hz], [nx1, ny1, 0.0]),
            ([x1, y1, hz], [nx1, ny1, 0.0]),
            ([x0, y0, hz], [nx0, ny0, 0.0]),
        ];
        for idx in [0usize, 1, 2, 0, 2, 3] {
            out.push(Vertex {
                position: quad[idx].0,
                normal: quad[idx].1,
                color,
            });
        }
        // Caps. The far one is what you see down the barrel of a weapon lying
        // across the screen, so neither is optional.
        out.push(Vertex {
            position: [0.0, 0.0, hz],
            normal: [0.0, 0.0, 1.0],
            color,
        });
        out.push(Vertex {
            position: [x0, y0, hz],
            normal: [0.0, 0.0, 1.0],
            color,
        });
        out.push(Vertex {
            position: [x1, y1, hz],
            normal: [0.0, 0.0, 1.0],
            color,
        });
        out.push(Vertex {
            position: [0.0, 0.0, -hz],
            normal: [0.0, 0.0, -1.0],
            color,
        });
        out.push(Vertex {
            position: [x1, y1, -hz],
            normal: [0.0, 0.0, -1.0],
            color,
        });
        out.push(Vertex {
            position: [x0, y0, -hz],
            normal: [0.0, 0.0, -1.0],
            color,
        });
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn frame(visible: bool) -> Frame {
        Frame {
            speed: 0.0,
            on_ground: true,
            reloading: false,
            yaw: 0.0,
            pitch: 0.0,
            visible,
            move_speed: 22.0,
            reload_progress: None,
        }
    }

    /// Run the draw out, so a test that means "at rest" starts there.
    ///
    /// Equipping a weapon now plays a draw — see `DRAW_TIME` — so a single
    /// `update` after `set_weapon` leaves the model most of the way out of
    /// frame. Any test that reasons about a *resting* pose has to get past it
    /// first, or it measures the draw instead of the thing it came to measure.
    fn settle(vm: &mut WeaponViewModel) {
        for _ in 0..(DRAW_TIME / 0.016) as i32 + 4 {
            vm.update(0.016, &frame(true));
        }
    }

    /// Where the muzzle end of the drawn weapon is this frame.
    ///
    /// The pose is a transform on a pivot, so the only honest way to ask "did it
    /// move" is to look at a point that has been through it.
    fn tip(vm: &mut WeaponViewModel) -> Vec3 {
        let mut out = Vec::new();
        vm.vertices(&mut out);
        let mut far = Vec3::ZERO;
        for v in &out {
            let p = Vec3::from(v.position);
            if p.length() > far.length() {
                far = p;
            }
        }
        far
    }

    #[test]
    fn an_inspect_never_stops_moving_partway_through() {
        // The bug this pose was rebuilt for. It used to be one scalar driving
        // every axis through an envelope that *holds*, so the weapon travelled
        // out, froze for the length of the hold, and retraced its path — which
        // reads as a stutter rather than as a weapon being turned over.
        //
        // Asserted on the drawn geometry rather than on the envelope, because
        // the envelope still holds at 1.0 and is *supposed* to: what must not
        // hold still is the weapon.
        let mut vm = WeaponViewModel::default();
        vm.set_weapon("assault", None);
        settle(&mut vm);
        vm.inspect();

        let mut previous = tip(&mut vm);
        let mut still = 0;
        let mut worst = 0;
        let steps = (INSPECT_DURATION / 0.016) as i32 - 2;
        for _ in 0..steps {
            vm.update(0.016, &frame(true));
            let now = tip(&mut vm);
            if (now - previous).length() < 1e-4 {
                still += 1;
                worst = worst.max(still);
            } else {
                still = 0;
            }
            previous = now;
        }
        assert_eq!(
            worst, 0,
            "the weapon held still for {worst} frames mid-inspect"
        );
    }

    #[test]
    fn an_inspect_starts_and_ends_at_rest() {
        // The other half: it has to *stop* moving at both ends, or the weapon
        // snaps out of the aim and snaps back into it. `ease` is what buys this
        // — the envelope's slope is zero at 0 and at 1.
        assert_eq!(inspect_envelope(0.0), 0.0);
        assert!(inspect_envelope(INSPECT_DURATION) <= 1e-6);
        // And no step larger than the curve's own steepest, which is what tells
        // a smooth ramp from a discontinuity. Smootherstep's peak slope is 15/8
        // over its span, so the bound comes from the constants rather than from
        // a number that would quietly stop meaning anything if the rise changed.
        const DT: f32 = 0.016;
        let steepest = 1.875 / INSPECT_RISE.min(INSPECT_FALL) * DT * 1.05;
        let mut previous = 0.0;
        let mut t = 0.0;
        while t < INSPECT_DURATION {
            let now = inspect_envelope(t);
            assert!(
                (now - previous).abs() <= steepest,
                "the envelope jumped {:.3} at t={t:.3}, over the curve's own {steepest:.3}",
                now - previous
            );
            previous = now;
            t += DT;
        }
    }

    #[test]
    fn the_turn_only_ever_winds_one_way() {
        // If the roll were driven by the envelope it would unwind along the path
        // it wound up, which is the "played backwards" look. The turn climbs
        // throughout; the envelope scaling it is what still lands it at rest.
        let mut previous = -1.0;
        let mut t = 0.0;
        while t <= INSPECT_DURATION {
            let now = inspect_turn(t);
            assert!(now >= previous, "the turn reversed at t={t:.3}");
            previous = now;
            t += 0.016;
        }
        assert!(inspect_turn(INSPECT_DURATION) > 0.99);
    }

    fn drawn(vm: &mut WeaponViewModel) -> Vec<Vertex> {
        let mut out = Vec::new();
        vm.vertices(&mut out);
        out
    }

    fn skin(base: &str, accent: &str, pattern: &str, wear: f32) -> Skin {
        Skin {
            base_color: base.into(),
            accent_color: accent.into(),
            pattern_type: pattern.into(),
            float_value: wear,
        }
    }

    fn colors(vm: &mut WeaponViewModel) -> Vec<[f32; 3]> {
        let mut out = Vec::new();
        vm.vertices(&mut out);
        out.iter().map(|v| v.color).collect()
    }

    /// How far a palette is from grey. Wear pulls every channel together, so
    /// this is what "duller" means as a number.
    fn saturation(colors: &[[f32; 3]]) -> f32 {
        colors
            .iter()
            .map(|c| {
                let hi = c[0].max(c[1]).max(c[2]);
                let lo = c[0].min(c[1]).min(c[2]);
                hi - lo
            })
            .sum()
    }

    #[test]
    fn an_equipped_skin_reaches_the_weapon() {
        // The bug this exists for: the armoury could equip a skin and the gun in
        // your hands stayed the colour it had always been. A weapon in its
        // default palette looks perfectly fine, so the only symptom was somebody
        // noticing that the thing they equipped was not there.
        let mut plain = WeaponViewModel::default();
        plain.set_weapon("assault", None);
        plain.update(0.016, &frame(true));
        let before = colors(&mut plain);

        let mut skinned = WeaponViewModel::default();
        skinned.set_weapon("assault", Some(&skin("#38bdf8", "#f43f5e", "solid", 0.03)));
        skinned.update(0.016, &frame(true));
        let after = colors(&mut skinned);

        assert_eq!(after.len(), before.len(), "the model changed shape");
        assert_ne!(after, before, "the skin was not applied");
    }

    #[test]
    fn wear_is_visible_rather_than_bookkeeping() {
        // A float value nobody can see is a number the whole economy is built on
        // and no player can check.
        let mut fresh = WeaponViewModel::default();
        fresh.set_weapon("assault", Some(&skin("#38bdf8", "#f43f5e", "solid", 0.0)));
        fresh.update(0.016, &frame(true));
        let mut worn = WeaponViewModel::default();
        worn.set_weapon("assault", Some(&skin("#38bdf8", "#f43f5e", "solid", 0.95)));
        worn.update(0.016, &frame(true));
        assert!(saturation(&colors(&mut worn)) < saturation(&colors(&mut fresh)));
    }

    #[test]
    fn a_change_of_skin_rebuilds_and_an_unchanged_one_does_not() {
        // The colours are baked into the vertices, so a skin swap that did not
        // rebuild would leave the previous one on the gun with nothing to say so.
        let mut vm = WeaponViewModel::default();
        let first = skin("#38bdf8", "#f43f5e", "solid", 0.03);
        vm.set_weapon("assault", Some(&first));
        vm.update(0.016, &frame(true));
        let before = colors(&mut vm);

        vm.set_weapon("assault", Some(&first.clone()));
        assert_eq!(colors(&mut vm), before, "an identical skin rebuilt");

        vm.set_weapon("assault", Some(&skin("#eab308", "#dc2626", "fade", 0.03)));
        vm.update(0.016, &frame(true));
        assert_ne!(colors(&mut vm), before);
    }

    #[test]
    fn a_near_black_skin_is_still_a_visible_weapon() {
        // `assault_slate` is #09090b and there are no speculars here: taken
        // literally it draws a silhouette. The floor keeps the hue and lifts the
        // brightness.
        let mut vm = WeaponViewModel::default();
        vm.set_weapon("assault", Some(&skin("#09090b", "#27272a", "solid", 0.03)));
        vm.update(0.016, &frame(true));
        let brightest = colors(&mut vm)
            .iter()
            .map(|c| c[0].max(c[1]).max(c[2]))
            .fold(0.0f32, f32::max);
        assert!(
            brightest > 0.12,
            "the whole weapon was near black: {brightest}"
        );
    }

    #[test]
    fn the_floor_leaves_an_unskinned_weapon_exactly_as_it_was() {
        // A player carrying no skin must see the palette they always did, so the
        // lift is applied to skins and to nothing else.
        let plain = palette_for(None);
        assert_eq!(plain.body, METAL);
        assert_eq!(plain.dark, DARK);
        assert_eq!(plain.grip, GRIP);
        assert_eq!(plain.accent, ACCENT);
    }

    #[test]
    fn an_unparseable_colour_falls_back_rather_than_rendering_black() {
        // The catalogue is data. A client that turned a missing colour into
        // black would show a weapon nobody designed.
        assert_eq!(parse_color("not-a-colour", METAL), METAL);
        assert_eq!(parse_color("", METAL), METAL);
        assert_eq!(parse_color("#zzzzzz", METAL), METAL);
        // And a real one is read, with or without the hash.
        let red = parse_color("#ff0000", METAL);
        assert!((red[0] - 1.0).abs() < 1e-6 && red[1] == 0.0 && red[2] == 0.0);
        assert_eq!(parse_color("ff0000", METAL), red);
    }

    #[test]
    fn only_equipped_items_with_a_definition_become_skins() {
        use crate::api::{SkinDefinition, SkinInstance};
        let equipped = |weapon: &str, is_equipped: bool, definition: bool| SkinInstance {
            is_equipped,
            float_value: 0.2,
            definition: definition.then(|| SkinDefinition {
                weapon_id: weapon.into(),
                base_color: "#38bdf8".into(),
                accent_color: "#f43f5e".into(),
                pattern_type: "solid".into(),
            }),
        };
        let map = equipped_skins(&[
            equipped("assault", true, true),
            equipped("sniper", false, true),
            // No definition: skipped rather than guessed, or the weapon would
            // wear a colour the armoury never showed.
            equipped("pistol", true, false),
        ]);
        assert_eq!(map.keys().collect::<Vec<_>>(), vec!["assault"]);
        assert!((map["assault"].float_value - 0.2).abs() < 1e-6);
    }

    #[test]
    fn a_weapon_swap_replaces_the_model_and_only_on_a_change() {
        let mut vm = WeaponViewModel::default();
        vm.set_weapon("sniper", None);
        vm.update(0.016, &frame(true));
        let sniper = drawn(&mut vm).len();
        vm.set_weapon("sniper", None);
        assert_eq!(drawn(&mut vm).len(), sniper, "an idempotent swap rebuilt");
        vm.set_weapon("knife", None);
        vm.update(0.016, &frame(true));
        assert_ne!(drawn(&mut vm).len(), sniper);
    }

    #[test]
    fn an_unknown_weapon_is_a_rifle_rather_than_nothing() {
        // A new weapon on the server should look wrong, not invisible: an empty
        // model reads as the view model having broken.
        let mut vm = WeaponViewModel::default();
        vm.set_weapon("railgun", None);
        vm.update(0.016, &frame(true));
        let unknown = drawn(&mut vm).len();
        let mut rifle = WeaponViewModel::default();
        rifle.set_weapon("assault", None);
        rifle.update(0.016, &frame(true));
        assert_eq!(unknown, drawn(&mut rifle).len());
        assert!(unknown > 0);
    }

    #[test]
    fn nothing_is_drawn_while_dead() {
        let mut vm = WeaponViewModel::default();
        vm.set_weapon("assault", None);
        vm.update(0.016, &frame(false));
        assert!(drawn(&mut vm).is_empty());
    }

    /// Fit a prop the size of a rifle, so `prop` is `Some` without a GPU.
    fn with_prop(vm: &mut WeaponViewModel) {
        vm.fit_prop(Vec3::new(-0.1, -0.1, -1.2), Vec3::new(0.1, 0.15, 0.4))
            .expect("a rifle-sized prop fits");
    }

    #[test]
    fn a_fitted_prop_draws_no_vertices_but_still_has_a_pose() {
        // The bug this pins: with a prop loaded, `vertices` emits **only** the
        // muzzle flare, so a renderer that decided whether to run the view-model
        // pass from the vertex count alone drew the gun for the ~55 ms after a
        // shot and never otherwise. The pose being `Some` here is what the
        // renderer must key on instead.
        let mut vm = WeaponViewModel::default();
        vm.set_weapon("assault", None);
        vm.update(0.016, &frame(true));
        with_prop(&mut vm);
        vm.update(0.016, &frame(true));

        assert!(
            drawn(&mut vm).is_empty(),
            "a fitted prop suppresses the box model, flare aside",
        );
        assert!(
            vm.prop_model().is_some(),
            "but there is still a gun, and something has to say so",
        );
    }

    #[test]
    fn a_fitted_prop_has_no_pose_while_dead() {
        // The mirror image: `prop_model` gates the view-model pass now, so a
        // pose returned while dead or in the menu puts a gun on screen at the
        // one moment there is meant to be none.
        let mut vm = WeaponViewModel::default();
        vm.set_weapon("assault", None);
        vm.update(0.016, &frame(true));
        with_prop(&mut vm);

        vm.update(0.016, &frame(true));
        assert!(vm.prop_model().is_some(), "alive: a weapon is drawn");

        vm.update(0.016, &frame(false));
        assert!(vm.prop_model().is_none(), "dead: nothing is");
    }

    #[test]
    fn the_muzzle_flash_is_lit_for_two_frames_and_then_gone() {
        let mut vm = WeaponViewModel::default();
        vm.set_weapon("assault", None);
        vm.update(0.016, &frame(true));
        let quiet = drawn(&mut vm).len();
        vm.fire();
        vm.update(0.016, &frame(true));
        assert!(drawn(&mut vm).len() > quiet, "no flare after firing");
        // Past FLASH_LIFE it is gone rather than fading, which is what the
        // browser does too.
        vm.update(0.1, &frame(true));
        assert_eq!(drawn(&mut vm).len(), quiet);
    }

    #[test]
    fn a_knife_has_no_muzzle_to_flash() {
        // A swing is resolved as a `Shot` like everything else, so it reaches
        // the flare code. Lighting one would give away the position of the one
        // weapon whose entire value is that carrying it does not.
        assert!(flash_shape("knife").is_none());
        let mut vm = WeaponViewModel::default();
        vm.set_weapon("knife", None);
        settle(&mut vm);
        let quiet = drawn(&mut vm).len();
        vm.fire();
        vm.update(0.016, &frame(true));
        assert_eq!(drawn(&mut vm).len(), quiet, "the knife flashed");
    }

    #[test]
    fn a_shotgun_blooms_wide_and_a_sniper_lances_long() {
        // The shape is the cue that tells a player at the far end of a corridor
        // what is being fired at them. Measured on the geometry rather than on
        // the table, so a shape that stopped reaching the vertices would fail.
        let span = |id: &str| {
            let mut vm = WeaponViewModel::default();
            vm.set_weapon(id, None);
            settle(&mut vm);
            let quiet: Vec<Vertex> = drawn(&mut vm);
            vm.fire();
            vm.update(0.001, &frame(true));
            let lit = drawn(&mut vm);
            // Only the vertices the flare added, so the weapon's own model does
            // not dominate the measurement.
            let flare = &lit[quiet.len()..];
            let axis = |i: usize| {
                let lo = flare.iter().map(|v| v.position[i]).fold(f32::MAX, f32::min);
                let hi = flare.iter().map(|v| v.position[i]).fold(f32::MIN, f32::max);
                hi - lo
            };
            (axis(0), axis(2))
        };
        let (shotgun_w, shotgun_l) = span("shotgun");
        let (sniper_w, sniper_l) = span("sniper");
        assert!(
            shotgun_w > sniper_w,
            "shotgun {shotgun_w} was not wider than sniper {sniper_w}"
        );
        assert!(
            sniper_l > shotgun_l,
            "sniper {sniper_l} was not longer than shotgun {shotgun_l}"
        );
    }

    #[test]
    fn an_unknown_weapon_still_flashes() {
        // The same rule `build` follows: a weapon the server has grown since
        // this client was built should look ordinary, never invisible.
        assert!(flash_shape("railgun").is_some());
    }

    #[test]
    fn the_bloom_is_dimmer_than_the_core_and_fades_first() {
        // It is a halo, not a second flash. If the two were equally bright the
        // flare would just be a fatter cone.
        let mut vm = WeaponViewModel::default();
        vm.set_weapon("assault", None);
        settle(&mut vm);
        let quiet = drawn(&mut vm).len();
        vm.fire();
        vm.update(0.001, &frame(true));
        let lit = drawn(&mut vm);
        let flare = &lit[quiet..];
        let brightest = flare.iter().map(|v| v.color[0]).fold(0.0f32, f32::max);
        let dimmest = flare.iter().map(|v| v.color[0]).fold(f32::MAX, f32::min);
        assert!(
            dimmest < brightest * 0.8,
            "the halo ({dimmest}) is as bright as the core ({brightest})"
        );

        // And it is already on its way out while the core still is not.
        let early = dimmest;
        vm.update(FLASH_LIFE * 0.7, &frame(true));
        let late_all = drawn(&mut vm);
        let late = late_all[quiet..]
            .iter()
            .map(|v| v.color[0])
            .fold(f32::MAX, f32::min);
        assert!(late < early, "the halo did not fade: {early} then {late}");
    }

    #[test]
    fn a_swap_takes_the_weapon_out_of_frame_and_brings_it_back() {
        // The point of the whole animation: a swap should read as an action,
        // not as one model being substituted for another between two frames.
        let mut vm = WeaponViewModel::default();
        vm.set_weapon("assault", None);
        settle(&mut vm);
        let rest = vm.transform.w_axis.y;

        vm.set_weapon("pistol", None);
        vm.update(0.016, &frame(true));
        let low = vm.transform.w_axis.y;
        assert!(low < rest - 0.5, "{low} was not below {rest}");

        settle(&mut vm);
        // Back at *this* weapon's rest, which is the same home pose — the model
        // differs, the pivot does not.
        assert!(
            (vm.transform.w_axis.y - rest).abs() < 1e-3,
            "the draw never finished: {} vs {rest}",
            vm.transform.w_axis.y
        );
    }

    #[test]
    fn a_refused_switch_brings_the_weapon_back_up_on_its_own() {
        // The holster is fired on the *key*, before the server has answered, so
        // the case where it never answers has to recover with nothing anywhere
        // noticing. Otherwise a switch the server declines leaves the player
        // looking at their knees for the rest of the match.
        let mut vm = WeaponViewModel::default();
        vm.set_weapon("assault", None);
        settle(&mut vm);
        let rest = vm.transform.w_axis.y;

        vm.holster();
        for _ in 0..(HOLSTER_TIME / 0.016) as i32 + 2 {
            vm.update(0.016, &frame(true));
        }
        assert!(vm.stow > 0.9, "the holster did not take it down: {}", vm.stow);

        // No `set_weapon` ever arrives.
        for _ in 0..((HOLSTER_HOLD + DRAW_TIME) / 0.016) as i32 + 4 {
            vm.update(0.016, &frame(true));
        }
        assert_eq!(vm.stow, 0.0);
        assert!((vm.transform.w_axis.y - rest).abs() < 1e-3);
    }

    #[test]
    fn a_confirmed_switch_does_not_wait_out_the_rest_of_the_hold() {
        // `set_weapon` clears the hold. Without that, a swap confirmed in 40ms
        // would still sit at the bottom for the remaining 360ms of the guess.
        let mut vm = WeaponViewModel::default();
        vm.set_weapon("assault", None);
        settle(&mut vm);
        vm.holster();
        vm.update(0.016, &frame(true));
        vm.set_weapon("pistol", None);
        assert_eq!(vm.holster_hold, 0.0);
        settle(&mut vm);
        assert_eq!(vm.stow, 0.0);
    }

    #[test]
    fn changing_a_skin_does_not_holster_the_weapon() {
        // `set_weapon` is also how a skin is equipped. Re-holstering for one
        // would dive the gun out of frame in the middle of a firefight, for a
        // change of colour.
        let mut vm = WeaponViewModel::default();
        vm.set_weapon("assault", None);
        settle(&mut vm);
        let painted = skin("#c04020", "#f0d060", "solid", 0.1);
        vm.set_weapon("assault", Some(&painted));
        assert_eq!(vm.stow, 0.0);
    }

    #[test]
    fn a_spawn_draws_the_weapon_rather_than_having_it_appear() {
        let mut vm = WeaponViewModel::default();
        vm.set_weapon("assault", None);
        settle(&mut vm);
        // Dead, then alive again holding the same weapon — so `set_weapon`
        // never fires and nothing else would start a draw.
        vm.update(0.016, &frame(false));
        vm.update(0.016, &frame(true));
        assert!(vm.stow > 0.9, "respawn did not draw: {}", vm.stow);
    }

    #[test]
    fn the_reload_dip_stretches_to_the_served_reload_time() {
        // The whole reason the envelope takes a fraction. A 1.2s reload and a
        // 3.4s one must both be at the bottom in the middle and back up at the
        // end; measured in seconds, one of the two is always wrong.
        for total in [1.2f32, 3.4] {
            let mut vm = WeaponViewModel::default();
            vm.set_weapon("assault", None);
            settle(&mut vm);
            let rest = vm.transform.w_axis.y;

            let steps = (total / 0.016) as i32;
            let mut lowest = f32::MAX;
            for i in 0..=steps {
                let progress = i as f32 / steps as f32;
                vm.update(
                    0.016,
                    &Frame {
                        reloading: true,
                        reload_progress: Some(progress),
                        ..frame(true)
                    },
                );
                if (progress - 0.5).abs() < 0.02 {
                    lowest = lowest.min(vm.transform.w_axis.y);
                }
            }
            assert!(
                lowest < rest - 0.4,
                "a {total}s reload never dipped: {lowest} vs {rest}"
            );
            // And it is back up on the frame the magazine is full, not after.
            assert!(
                (vm.transform.w_axis.y - rest).abs() < 1e-3,
                "a {total}s reload had not returned: {} vs {rest}",
                vm.transform.w_axis.y
            );
        }
    }

    #[test]
    fn the_dip_is_the_same_shape_at_the_same_fraction_of_any_reload() {
        // Stated directly on the envelope, because the pose folds in bob and
        // sway that a two-weapon comparison would have to hold still.
        for p in [0.0f32, 0.11, 0.3, 0.5, 0.85, 1.0] {
            assert_eq!(reload_envelope(p), reload_envelope(p));
        }
        assert_eq!(reload_envelope(0.0), 0.0);
        assert_eq!(reload_envelope(1.0), 0.0);
        assert_eq!(reload_envelope(0.5), 1.0);
        // And out of range rather than panicking: `reloadIn` can exceed
        // `reloadTime` by a tick when the two arrive from different messages.
        assert_eq!(reload_envelope(-3.0), 0.0);
        assert_eq!(reload_envelope(9.0), 0.0);
    }

    #[test]
    fn a_reload_with_no_served_length_still_dips() {
        // The fallback. A server too old to send `reloadTime` must not leave the
        // reload with no animation at all — nor hold the weapon down forever,
        // which is what `Some(0.0)` in place of `None` would have done.
        let mut vm = WeaponViewModel::default();
        vm.set_weapon("assault", None);
        settle(&mut vm);
        let rest = vm.transform.w_axis.y;
        for _ in 0..60 {
            vm.update(
                0.016,
                &Frame {
                    reloading: true,
                    reload_progress: None,
                    ..frame(true)
                },
            );
        }
        assert!(vm.transform.w_axis.y < rest - 0.3);
        for _ in 0..120 {
            vm.update(0.016, &frame(true));
        }
        assert!((vm.transform.w_axis.y - rest).abs() < 1e-2);
    }

    #[test]
    fn recoil_kicks_the_weapon_back_and_then_settles() {
        let mut vm = WeaponViewModel::default();
        vm.set_weapon("assault", None);
        settle(&mut vm);
        let rest = vm.transform.w_axis.z;
        vm.fire();
        vm.update(0.016, &frame(true));
        let kicked = vm.transform.w_axis.z;
        // Camera space looks down -Z, so a weapon pushed back moves toward zero.
        assert!(kicked > rest, "{kicked} was not behind {rest}");
        for _ in 0..120 {
            vm.update(0.016, &frame(true));
        }
        assert!(
            (vm.transform.w_axis.z - rest).abs() < 1e-3,
            "recoil never decayed"
        );
    }

    #[test]
    fn holding_the_trigger_does_not_walk_the_weapon_off_the_screen() {
        // The cap is the point: an uncapped additive kick puts the gun behind
        // the player's ear after a second of automatic fire.
        let mut vm = WeaponViewModel::default();
        vm.set_weapon("assault", None);
        for _ in 0..200 {
            vm.fire();
            vm.update(0.016, &frame(true));
        }
        assert!(vm.kick <= 1.0 + 1e-6, "kick ran away to {}", vm.kick);
        assert!(vm.transform.w_axis.z < HOME.z + 0.29);
    }

    #[test]
    fn turning_across_the_yaw_seam_does_not_fling_the_weapon() {
        // This client wraps yaw at 360°, so a small turn can arrive as a delta of
        // nearly 2π. Taken literally that is a fling; taken the short way round it
        // is the small movement it actually was.
        let mut vm = WeaponViewModel::default();
        vm.set_weapon("assault", None);
        let tau = std::f32::consts::TAU;
        let mut f = frame(true);
        f.yaw = tau - 0.01;
        vm.update(0.016, &f);
        f.yaw = 0.01;
        vm.update(0.016, &f);
        assert!(vm.sway_x.abs() < 0.05, "sway flung to {}", vm.sway_x);
    }

    #[test]
    fn reloading_dips_the_weapon_and_returns_it() {
        let mut vm = WeaponViewModel::default();
        vm.set_weapon("assault", None);
        let mut f = frame(true);
        f.reloading = true;
        for _ in 0..60 {
            vm.update(0.016, &f);
        }
        assert!(vm.transform.w_axis.y < HOME.y - 0.2, "the gun never dipped");
        f.reloading = false;
        for _ in 0..120 {
            vm.update(0.016, &f);
        }
        assert!((vm.transform.w_axis.y - HOME.y).abs() < 1e-2);
    }

    #[test]
    fn a_box_is_wound_outward_on_all_six_faces() {
        let verts = box_verts([1.0, 2.0, 3.0], METAL);
        assert_eq!(verts.len(), 36);
        let normals: std::collections::HashSet<[i32; 3]> = verts
            .iter()
            .map(|v| [v.normal[0] as i32, v.normal[1] as i32, v.normal[2] as i32])
            .collect();
        assert_eq!(normals.len(), 6);
        // Half-extents, not extents: a box built at full size would make every
        // weapon twice the length it is described as.
        assert!(verts.iter().all(|v| v.position[2].abs() <= 1.5 + 1e-6));
    }

    #[test]
    fn a_normal_is_rotated_but_never_translated() {
        // Running a normal through the point transform drags the shading around
        // with the weapon's position — which shows up as a gun that changes
        // brightness when you walk, and nowhere near the cause.
        let m = Mat4::from_translation(Vec3::new(10.0, -5.0, 3.0));
        let v = Vertex {
            position: [0.0, 0.0, 0.0],
            normal: [0.0, 1.0, 0.0],
            color: METAL,
        };
        let out = transform_vertex(&m, &v);
        assert_eq!(out.normal, [0.0, 1.0, 0.0]);
        assert_eq!(out.position, [10.0, -5.0, 3.0]);
    }
}
