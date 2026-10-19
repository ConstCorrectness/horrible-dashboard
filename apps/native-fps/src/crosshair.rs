//! The crosshair: spec v1, its `HXC1-` share code, and the shapes it draws.
//!
//! A port of `backend/modules/hassault/crosshair.py`, which is the reference.
//! The browser's `crosshair.ts` is the other port, and `crosshair-vectors.json`
//! — written from the Python by `scripts/gen_hassault_crosshair_vectors.py` and
//! replayed by `tests/conformance.rs` — is what makes a code pasted into either
//! client draw the same rectangles. A change belongs in the Python first.
//!
//! **f64 throughout**, though the HUD draws in f32: the quantization is
//! `floor(x * k + 0.5)`, and 1.45 is 14.499… in f64 but 14.500… once it has been
//! through an f32, which would give the two clients different codes for the same
//! slider position. Only the painter narrows.
//!
//! Hit and kill feedback are not here. The hitmarker is the HUD's own overlay,
//! drawn around the reticle, and never recolours it.

use serde_json::{json, Value};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum Style {
    /// Four arms with a gap — the default, and the one that shows spread.
    #[default]
    Cross,
    /// The cross with a larger, fainter centre pip under the dot.
    CrossDot,
    /// A dot alone.
    Dot,
    /// A ring at the spread radius, the honest picture of a cone.
    Circle,
}

impl Style {
    pub const ALL: [Style; 4] = [Style::Cross, Style::CrossDot, Style::Dot, Style::Circle];

    pub fn key(self) -> &'static str {
        match self {
            Style::Cross => "cross",
            Style::CrossDot => "crossDot",
            Style::Dot => "dot",
            Style::Circle => "circle",
        }
    }

    pub fn label(self) -> &'static str {
        match self {
            Style::Cross => "CROSS",
            Style::CrossDot => "CROSS + PIP",
            Style::Dot => "DOT",
            Style::Circle => "RING",
        }
    }

    pub fn parse(s: &str) -> Option<Style> {
        Style::ALL.into_iter().find(|v| v.key() == s)
    }

    fn index(self) -> u8 {
        Style::ALL.iter().position(|v| *v == self).unwrap() as u8
    }
}

/// Six named colours, all bright, plus a custom RGB lifted to a luma floor.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum Color {
    #[default]
    White,
    Green,
    Cyan,
    Amber,
    Magenta,
    Red,
    Custom,
}

impl Color {
    pub const NAMED: [Color; 6] = [
        Color::White,
        Color::Green,
        Color::Cyan,
        Color::Amber,
        Color::Magenta,
        Color::Red,
    ];

    pub fn key(self) -> &'static str {
        match self {
            Color::White => "white",
            Color::Green => "green",
            Color::Cyan => "cyan",
            Color::Amber => "amber",
            Color::Magenta => "magenta",
            Color::Red => "red",
            Color::Custom => "custom",
        }
    }

    pub fn label(self) -> &'static str {
        match self {
            Color::White => "WHITE",
            Color::Green => "GREEN",
            Color::Cyan => "CYAN",
            Color::Amber => "AMBER",
            Color::Magenta => "MAGENTA",
            Color::Red => "RED",
            Color::Custom => "CUSTOM",
        }
    }

    /// The named colour's 8-bit sRGB; `None` for `Custom`, whose RGB is the spec's.
    pub fn named_rgb(self) -> Option<[u8; 3]> {
        Some(match self {
            Color::White => [235, 240, 245],
            Color::Green => [115, 242, 115],
            Color::Cyan => [102, 230, 242],
            Color::Amber => [247, 199, 89],
            Color::Magenta => [242, 115, 230],
            Color::Red => [247, 89, 82],
            Color::Custom => return None,
        })
    }

    pub fn parse(s: &str) -> Option<Color> {
        Color::NAMED.into_iter().find(|v| v.key() == s)
    }
}

pub const LUMA_FLOOR: i64 = 90;
const LUMA_WEIGHTS: [i64; 3] = [2126, 7152, 722];

/// `(lowest, highest, quanta per unit)`, in quanta: a value is `q / k`.
pub type Range = (i32, i32, i32);
pub const SIZE: Range = (2, 24, 2);
pub const GAP: Range = (-10, 40, 2);
pub const THICKNESS: Range = (2, 30, 10);
pub const OUTLINE: Range = (0, 20, 10);
pub const ALPHA: Range = (15, 100, 100);

pub const OUTLINE_ALPHA: f64 = 0.85;
pub const PIP_ALPHA: f64 = 0.45;
pub const CONE_PX_PER_RAD: f64 = 130.0;

pub const PREFIX: &str = "HXC1-";
pub const VERSION: u8 = 1;
const FIELDS: usize = 11;
const ALPHABET: &[u8; 32] = b"0123456789ABCDEFGHJKMNPQRSTVWXYZ";

pub const KEY_STYLE: &str = "hassault.crosshair.style";
pub const KEY_SIZE: &str = "hassault.crosshair.size";
pub const KEY_GAP: &str = "hassault.crosshair.gap";
pub const KEY_THICKNESS: &str = "hassault.crosshair.thickness";
pub const KEY_OUTLINE: &str = "hassault.crosshair.outline";
pub const KEY_OUTLINE_THICKNESS: &str = "hassault.crosshair.outlineThickness";
pub const KEY_ALPHA: &str = "hassault.crosshair.alpha";
pub const KEY_DOT: &str = "hassault.crosshair.dot";
pub const KEY_T_STYLE: &str = "hassault.crosshair.tStyle";
pub const KEY_DYNAMIC: &str = "hassault.crosshair.dynamic";
pub const KEY_COLOR: &str = "hassault.crosshair.color";

/// Every row a spec lives in, for persisting one whole.
pub const KEYS: [&str; 11] = [
    KEY_STYLE,
    KEY_SIZE,
    KEY_GAP,
    KEY_THICKNESS,
    KEY_OUTLINE,
    KEY_OUTLINE_THICKNESS,
    KEY_ALPHA,
    KEY_DOT,
    KEY_T_STYLE,
    KEY_DYNAMIC,
    KEY_COLOR,
];

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Spec {
    pub style: Style,
    pub size: f64,
    /// Signed: below zero the arms cross the centre.
    pub gap: f64,
    pub thickness: f64,
    /// Outline width in HUD units; 0 is no outline.
    pub outline: f64,
    pub alpha: f64,
    pub dot: bool,
    /// Drop the top arm.
    pub t_style: bool,
    /// Open with the weapon's cone.
    pub dynamic: bool,
    pub color: Color,
    pub rgb: [u8; 3],
}

impl Default for Spec {
    fn default() -> Spec {
        Spec {
            style: Style::Cross,
            size: 3.0,
            gap: 4.0,
            thickness: 0.6,
            outline: 0.3,
            alpha: 0.9,
            dot: true,
            t_style: false,
            dynamic: true,
            color: Color::White,
            rgb: [235, 240, 245],
        }
    }
}

fn q(value: f64, (lo, hi, k): Range) -> i32 {
    if !value.is_finite() {
        return lo;
    }
    ((value * k as f64 + 0.5).floor()).clamp(lo as f64, hi as f64) as i32
}

fn from_q(n: i32, range: Range) -> f64 {
    n as f64 / range.2 as f64
}

/// Lift a colour towards white until its luma reaches `LUMA_FLOOR`; integer maths.
pub fn lift_luma(rgb: [u8; 3]) -> [u8; 3] {
    let c = rgb.map(i64::from);
    let luma: i64 = (0..3).map(|i| LUMA_WEIGHTS[i] * c[i]).sum();
    let floor = LUMA_FLOOR * 10000;
    if luma >= floor {
        return rgb;
    }
    let need = floor - luma;
    let room = 255 * 10000 - luma;
    c.map(|v| (v + ((255 - v) * need + room - 1) / room) as u8)
}

impl Spec {
    /// Quantized, clamped, colour lifted — what a share code carries.
    pub fn normalized(self) -> Spec {
        let rgb = match self.color.named_rgb() {
            Some(rgb) => rgb,
            None => lift_luma(self.rgb),
        };
        Spec {
            size: from_q(q(self.size, SIZE), SIZE),
            gap: from_q(q(self.gap, GAP), GAP),
            thickness: from_q(q(self.thickness, THICKNESS), THICKNESS),
            outline: from_q(q(self.outline, OUTLINE), OUTLINE),
            alpha: from_q(q(self.alpha, ALPHA), ALPHA),
            rgb,
            ..self
        }
    }

    /// The spec the settings bag describes, normalized; per key, a bad row is its default.
    pub fn from_settings(bag: &Value) -> Spec {
        let d = Spec::default();
        let num = |k: &str, def: f64| bag.get(k).and_then(Value::as_f64).unwrap_or(def);
        let flag = |k: &str, def: bool| bag.get(k).and_then(Value::as_bool).unwrap_or(def);
        let text = |k: &str| bag.get(k).and_then(Value::as_str);
        let (color, rgb) = match text(KEY_COLOR) {
            Some(t) => match hex_rgb(t) {
                Some(rgb) => (Color::Custom, rgb),
                None => (Color::parse(t).unwrap_or(Color::White), d.rgb),
            },
            None => (d.color, d.rgb),
        };
        Spec {
            style: text(KEY_STYLE).and_then(Style::parse).unwrap_or_default(),
            size: num(KEY_SIZE, d.size),
            gap: num(KEY_GAP, d.gap),
            thickness: num(KEY_THICKNESS, d.thickness),
            outline: if flag(KEY_OUTLINE, true) {
                num(KEY_OUTLINE_THICKNESS, d.outline)
            } else {
                0.0
            },
            alpha: num(KEY_ALPHA, d.alpha),
            dot: flag(KEY_DOT, d.dot),
            t_style: flag(KEY_T_STYLE, d.t_style),
            dynamic: flag(KEY_DYNAMIC, d.dynamic),
            color,
            rgb,
        }
        .normalized()
    }

    /// The rows to write for this spec. An outline of 0 writes the switch off and
    /// leaves the width row alone, so the width comes back when it is turned on.
    pub fn to_settings(self) -> Vec<(&'static str, Value)> {
        let s = self.normalized();
        let mut rows = vec![
            (KEY_STYLE, json!(s.style.key())),
            (KEY_SIZE, json!(s.size)),
            (KEY_GAP, json!(s.gap)),
            (KEY_THICKNESS, json!(s.thickness)),
            (KEY_OUTLINE, json!(s.outline > 0.0)),
            (KEY_ALPHA, json!(s.alpha)),
            (KEY_DOT, json!(s.dot)),
            (KEY_T_STYLE, json!(s.t_style)),
            (KEY_DYNAMIC, json!(s.dynamic)),
            (KEY_COLOR, json!(s.color_setting())),
        ];
        if s.outline > 0.0 {
            rows.push((KEY_OUTLINE_THICKNESS, json!(s.outline)));
        }
        rows
    }

    /// `hassault.crosshair.color` as stored: a name, or `#rrggbb` when custom.
    pub fn color_setting(&self) -> String {
        if self.color == Color::Custom {
            rgb_hex(self.rgb)
        } else {
            self.color.key().to_string()
        }
    }

    /// The fill colour as linear-ish floats for the painter, with the spec's alpha.
    pub fn rgba(&self) -> [f32; 4] {
        [
            self.rgb[0] as f32 / 255.0,
            self.rgb[1] as f32 / 255.0,
            self.rgb[2] as f32 / 255.0,
            self.alpha as f32,
        ]
    }
}

/// `#rrggbb` as bytes, or `None`. Strict: no shorthand, no names, no spaces.
pub fn hex_rgb(text: &str) -> Option<[u8; 3]> {
    let t = text.trim();
    let b = t.as_bytes();
    if b.len() != 7 || b[0] != b'#' || !b[1..].iter().all(u8::is_ascii_hexdigit) {
        return None;
    }
    let byte = |i: usize| u8::from_str_radix(&t[i..i + 2], 16).ok();
    Some([byte(1)?, byte(3)?, byte(5)?])
}

pub fn rgb_hex(rgb: [u8; 3]) -> String {
    format!("#{:02x}{:02x}{:02x}", rgb[0], rgb[1], rgb[2])
}

// ---- share code ---------------------------------------------------------------

/// Why a code was refused. The reason strings are the fixture's.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DecodeError {
    Prefix,
    Char,
    Short,
    Crc,
    Version,
}

impl DecodeError {
    pub fn reason(self) -> &'static str {
        match self {
            DecodeError::Prefix => "prefix",
            DecodeError::Char => "char",
            DecodeError::Short => "short",
            DecodeError::Crc => "crc",
            DecodeError::Version => "version",
        }
    }

    /// What the menu says, in the HUD font's character set.
    pub fn message(self) -> &'static str {
        match self {
            DecodeError::Prefix => "NOT A CROSSHAIR CODE - ONE STARTS HXC1-",
            DecodeError::Char => "THE CODE HAS A CHARACTER NO CODE USES",
            DecodeError::Short => "THE CODE IS TOO SHORT",
            DecodeError::Crc => "CHECKSUM MISMATCH - A CHARACTER WAS MISTYPED",
            DecodeError::Version => "MADE BY A NEWER VERSION OF THE GAME",
        }
    }
}

/// CRC-8/SMBUS: polynomial 0x07, init 0, no reflection.
pub fn crc8(data: &[u8]) -> u8 {
    let mut crc = 0u8;
    for &b in data {
        crc ^= b;
        for _ in 0..8 {
            crc = if crc & 0x80 != 0 {
                (crc << 1) ^ 0x07
            } else {
                crc << 1
            };
        }
    }
    crc
}

pub fn pack(spec: Spec) -> Vec<u8> {
    let s = spec.normalized();
    let flags = u8::from(s.dot) | (u8::from(s.t_style) << 1) | (u8::from(s.dynamic) << 2);
    let color = match s.color {
        Color::Custom => Color::NAMED.len() as u8,
        c => Color::NAMED.iter().position(|v| *v == c).unwrap() as u8,
    };
    let rgb = if s.color == Color::Custom {
        s.rgb
    } else {
        [0, 0, 0]
    };
    let mut body = vec![
        VERSION,
        s.style.index(),
        flags,
        q(s.size, SIZE) as u8,
        q(s.gap, GAP) as i8 as u8,
        q(s.thickness, THICKNESS) as u8,
        q(s.outline, OUTLINE) as u8,
        q(s.alpha, ALPHA) as u8,
        color,
        rgb[0],
        rgb[1],
        rgb[2],
    ];
    body.push(crc8(&body));
    body
}

pub fn unpack(payload: &[u8]) -> Result<Spec, DecodeError> {
    if payload.len() < 2 + FIELDS {
        return Err(DecodeError::Short);
    }
    let (body, crc) = payload.split_at(payload.len() - 1);
    if crc8(body) != crc[0] {
        return Err(DecodeError::Crc);
    }
    if payload[0] != VERSION {
        return Err(DecodeError::Version);
    }
    let f = &payload[1..1 + FIELDS];
    let (color, rgb) = match f[7] as usize {
        i if i < Color::NAMED.len() => (Color::NAMED[i], Color::NAMED[i].named_rgb().unwrap()),
        i if i == Color::NAMED.len() => (Color::Custom, [f[8], f[9], f[10]]),
        _ => (Color::White, Color::White.named_rgb().unwrap()),
    };
    Ok(Spec {
        style: Style::ALL.get(f[0] as usize).copied().unwrap_or_default(),
        size: from_q(f[2] as i32, SIZE),
        gap: from_q(f[3] as i8 as i32, GAP),
        thickness: from_q(f[4] as i32, THICKNESS),
        outline: from_q(f[5] as i32, OUTLINE),
        alpha: from_q(f[6] as i32, ALPHA),
        dot: f[1] & 1 != 0,
        t_style: f[1] & 2 != 0,
        dynamic: f[1] & 4 != 0,
        color,
        rgb,
    }
    .normalized())
}

fn base32(data: &[u8]) -> String {
    let mut out = String::new();
    let (mut acc, mut bits) = (0u32, 0u32);
    for &b in data {
        acc = ((acc << 8) | b as u32) & 0xFFFF;
        bits += 8;
        while bits >= 5 {
            bits -= 5;
            out.push(ALPHABET[((acc >> bits) & 31) as usize] as char);
        }
    }
    if bits > 0 {
        out.push(ALPHABET[((acc << (5 - bits)) & 31) as usize] as char);
    }
    out
}

fn digit(ch: char) -> Option<u32> {
    match ch {
        'O' => Some(0),
        'I' | 'L' => Some(1),
        _ => ALPHABET
            .iter()
            .position(|&a| a as char == ch)
            .map(|i| i as u32),
    }
}

fn unbase32(text: &str) -> Result<Vec<u8>, DecodeError> {
    let mut out = Vec::new();
    let (mut acc, mut bits) = (0u32, 0u32);
    for ch in text.chars() {
        let v = digit(ch).ok_or(DecodeError::Char)?;
        acc = ((acc << 5) | v) & 0xFFFF;
        bits += 5;
        if bits >= 8 {
            bits -= 8;
            out.push(((acc >> bits) & 0xFF) as u8);
        }
    }
    Ok(out)
}

pub fn encode_payload(payload: &[u8]) -> String {
    let chars: Vec<char> = base32(payload).chars().collect();
    let groups: Vec<String> = chars.chunks(5).map(|g| g.iter().collect()).collect();
    format!("{PREFIX}{}", groups.join("-"))
}

pub fn encode(spec: Spec) -> String {
    encode_payload(&pack(spec))
}

/// Forgiving about what a paste does to a code — case, surrounding space, dashes
/// and spaces inside, O/I/L — and strict about anything that changes what it draws.
pub fn decode(code: &str) -> Result<Spec, DecodeError> {
    let t = code.trim().to_uppercase();
    let body = t.strip_prefix(PREFIX).ok_or(DecodeError::Prefix)?;
    let body: String = body.chars().filter(|c| *c != '-' && *c != ' ').collect();
    unpack(&unbase32(&body)?)
}

// ---- geometry -----------------------------------------------------------------

/// Pixels per HUD unit: whole pixels per 360 of height, times the HUD scale.
pub fn hud_unit(height_px: f64, hud_scale: f64) -> f64 {
    let scale = hud_scale.clamp(0.75, 1.5);
    ((height_px / 360.0 + 0.5).floor() * scale).max(2.0)
}

/// How far a cone of `cone_rad` opens the crosshair, in pixels.
pub fn cone_gap_px(cone_rad: f64, unit: f64) -> f64 {
    cone_rad.max(0.0) * CONE_PX_PER_RAD * unit
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Layer {
    Outline,
    Fill,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Geometry {
    Rect { x: f64, y: f64, w: f64, h: f64 },
    Ring { r0: f64, r1: f64 },
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Shape {
    pub layer: Layer,
    /// Final opacity: the spec's alpha, times the pip's fraction, times
    /// `OUTLINE_ALPHA` on the outline layer.
    pub alpha: f64,
    pub geometry: Geometry,
}

/// The shapes to draw, around the centre (y down), outline layer first.
pub fn crosshair_rects(spec: Spec, cone_gap: f64, unit: f64) -> Vec<Shape> {
    let s = spec.normalized();
    let half = (s.thickness * unit).max(0.35);
    let arm = s.size * unit;
    let gap = s.gap * unit * 0.5 + if s.dynamic { cone_gap.max(0.0) } else { 0.0 };
    let out = if s.outline > 0.0 {
        (s.outline * unit).max(1.0)
    } else {
        0.0
    };

    let mut elements: Vec<(Geometry, f64)> = Vec::new();
    let square = |els: &mut Vec<(Geometry, f64)>, r: f64, frac: f64| {
        els.push((
            Geometry::Rect {
                x: -r,
                y: -r,
                w: r * 2.0,
                h: r * 2.0,
            },
            frac,
        ));
    };
    let arms = |els: &mut Vec<(Geometry, f64)>| {
        let rect = |x, y, w, h| (Geometry::Rect { x, y, w, h }, 1.0);
        els.push(rect(-gap - arm, -half, arm, half * 2.0));
        els.push(rect(gap, -half, arm, half * 2.0));
        if !s.t_style {
            els.push(rect(-half, -gap - arm, half * 2.0, arm));
        }
        els.push(rect(-half, gap, half * 2.0, arm));
    };
    match s.style {
        Style::Cross => {
            arms(&mut elements);
            if s.dot {
                square(&mut elements, half, 1.0);
            }
        }
        Style::CrossDot => {
            arms(&mut elements);
            square(&mut elements, half * 2.0, PIP_ALPHA);
            if s.dot {
                square(&mut elements, half, 1.0);
            }
        }
        Style::Dot => square(&mut elements, half, 1.0),
        Style::Circle => {
            let inner = gap.max(1.0);
            elements.push((
                Geometry::Ring {
                    r0: inner,
                    r1: inner + half * 2.0,
                },
                1.0,
            ));
            if s.dot {
                square(&mut elements, half, 1.0);
            }
        }
    }

    let mut shapes = Vec::with_capacity(elements.len() * 2);
    if out > 0.0 {
        for &(g, frac) in &elements {
            let geometry = match g {
                Geometry::Rect { x, y, w, h } => Geometry::Rect {
                    x: x - out,
                    y: y - out,
                    w: w + out * 2.0,
                    h: h + out * 2.0,
                },
                Geometry::Ring { r0, r1 } => Geometry::Ring {
                    r0: (r0 - out).max(0.0),
                    r1: r1 + out,
                },
            };
            shapes.push(Shape {
                layer: Layer::Outline,
                alpha: OUTLINE_ALPHA * s.alpha * frac,
                geometry,
            });
        }
    }
    for &(geometry, frac) in &elements {
        shapes.push(Shape {
            layer: Layer::Fill,
            alpha: s.alpha * frac,
            geometry,
        });
    }
    shapes
}

/// Starting points, as share codes — pinned by the fixture's `presets`.
pub const PRESETS: [(&str, &str); 6] = [
    ("DEFAULT", "HXC1-0400A-1G80R-1NM00-00008-P"),
    ("CLASSIC", "HXC1-04008-2040M-1NM08-00009-E"),
    ("TIGHT", "HXC1-04000-17Y0G-05M0G-00000-G"),
    ("T-CROSS", "HXC1-0400E-2G60M-1NM0R-0000D-E"),
    ("RING", "HXC1-041GA-1GC0G-1NM10-00000-E"),
    ("DOT", "HXC1-0410A-1G818-26818-0000B-Y"),
];

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_code_round_trips_every_field() {
        let spec = Spec {
            style: Style::Circle,
            size: 7.5,
            gap: -2.5,
            thickness: 1.3,
            outline: 0.7,
            alpha: 0.42,
            dot: false,
            t_style: true,
            dynamic: false,
            color: Color::Custom,
            rgb: [200, 120, 255],
        };
        assert_eq!(decode(&encode(spec)), Ok(spec));
    }

    #[test]
    fn every_preset_decodes() {
        for (name, code) in PRESETS {
            assert!(decode(code).is_ok(), "{name}");
        }
    }
}
