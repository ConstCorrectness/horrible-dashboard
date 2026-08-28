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
const RELOAD_RATE: f32 = 6.0;

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
}

/// How long one inspect takes, in seconds.
///
/// Long enough to read the weapon, short enough that it is over before it costs
/// you a gunfight — and it is interruptible anyway, so this is a maximum rather
/// than a commitment.
const INSPECT_DURATION: f32 = 1.35;

/// The inspect pose's weight over its own duration: ease in, hold, ease out.
///
/// Smoothstepped at both ends rather than linear. A linear ramp reverses
/// direction instantly at the hold, which reads as the animation being cut off
/// and restarted — the one thing a "look at this weapon" flourish must not do.
fn inspect_envelope(t: f32) -> f32 {
    const RISE: f32 = 0.28;
    const FALL: f32 = 0.42;
    let out = INSPECT_DURATION - FALL;
    let x = if t < RISE {
        t / RISE
    } else if t > out {
        1.0 - (t - out) / FALL
    } else {
        1.0
    }
    .clamp(0.0, 1.0);
    x * x * (3.0 - 2.0 * x)
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
        self.weapon = id.to_string();
        self.skin = skin.cloned();
        self.shape = if id.is_empty() {
            None
        } else {
            Some(build(id, skin))
        };
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
            return;
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
        let reload_target = if frame.reloading { 1.0 } else { 0.0 };
        self.reload_t += (reload_target - self.reload_t) * (dt * RELOAD_RATE).min(1.0);

        // A reload takes the weapon away for its own animation, and two poses
        // fighting over the same pivot is one that looks broken. The reload
        // wins because it is the one the *server* is doing.
        if frame.reloading {
            self.inspect = None;
        }
        // The envelope: in, hold, out. Advanced before it is read, so the frame
        // it completes on is the frame it is back at rest rather than one after.
        let inspect = match self.inspect {
            None => 0.0,
            Some(t) => {
                let t = t + dt;
                if t >= INSPECT_DURATION {
                    self.inspect = None;
                    0.0
                } else {
                    self.inspect = Some(t);
                    inspect_envelope(t)
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
        let position = Vec3::new(
            HOME.x + bob_x + self.sway_x - inspect * 0.30,
            HOME.y + bob_y + self.sway_y - self.reload_t * 0.55 + inspect * 0.16,
            HOME.z + self.kick * 0.28 + inspect * 0.20,
        );
        let rotation = Vec3::new(
            self.kick * -0.16 + self.reload_t * 0.7 + bob_y * 0.4 + inspect * 0.34,
            self.sway_x * 0.7 + self.reload_t * 0.25 - inspect * 0.95,
            self.sway_x * 0.5 + bob_x * 0.6 + inspect * 2.15,
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
    pub fn prop_model(&self) -> Option<Mat4> {
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
            // Squeezed in x/y only, exactly as the browser scales it, so the
            // flare's length stays put and only its girth varies.
            let flash = model
                * Mat4::from_translation(muzzle - Vec3::new(0.0, 0.0, 0.2))
                * Mat4::from_scale(Vec3::new(scale, scale, 1.0));
            for v in flash_cone() {
                out.push(transform_vertex(&flash, &v));
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
fn flash_cone() -> Vec<Vertex> {
    let sides = 5;
    let radius = 0.16;
    let length = 0.42;
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
        }
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
    fn recoil_kicks_the_weapon_back_and_then_settles() {
        let mut vm = WeaponViewModel::default();
        vm.set_weapon("assault", None);
        vm.update(0.016, &frame(true));
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
