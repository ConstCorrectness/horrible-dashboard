//! Mip chains, built on the CPU.
//!
//! Every texture this client uses is produced on the CPU — the detail tile is
//! drawn by [`crate::detail::draw_tile`], and character and prop maps arrive as
//! decoded RGBA out of a glTF. So the whole chain is a box filter over an array
//! we already hold, and none of the GPU machinery a mip generator usually needs
//! (a blit chain, or a compute pass, and a `RENDER_ATTACHMENT` usage on every
//! texture to go with it) has to exist.
//!
//! Without mips the world moires: the map's UVs are in cube units, so a floor
//! down a corridor covers hundreds of tiles per pixel row and the sampler picks
//! one of them per pixel, near enough at random. That boils as you walk, and it
//! was the loudest artefact in the client.
//!
//! ## The colour-space trap, which is the whole reason this is one function
//!
//! **Averaging is only meaningful in linear light**, and whether these bytes
//! *are* linear depends on the texture's format:
//!
//! - `Rgba8Unorm` — the detail tile. It is a *multiplier*, not a colour, so its
//!   bytes are already linear. Decoding it as sRGB would drop the mean of every
//!   level below the first, and the world would get dimmer with distance. That
//!   reads as fog rather than as a mipmap bug.
//! - `Rgba8UnormSrgb` — character and prop maps. These are sRGB-encoded, so
//!   averaging the raw bytes is averaging in the wrong space: it comes out
//!   *darker* than the surface it represents, worst in the mid-tones, and it
//!   reads as a character who dims as they walk away from you.
//!
//! Both mistakes are silent and both look like a lighting problem rather than a
//! filtering one, which is why the caller has to say which it has rather than
//! getting a default. Hence [`Space`], with no `Default`.
//!
//! **Alpha is never sRGB-encoded**, in either case. It is a coverage fraction
//! and is always averaged as-is; running it through the colour transfer function
//! is the classic third version of this bug, and it eats soft edges.

/// What the bytes of a texture actually mean, and therefore how to average them.
///
/// No `Default` on purpose: getting this wrong is invisible until somebody
/// notices the world dimming with distance, so the caller states it.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Space {
    /// `Rgba8Unorm` — the bytes are already linear. Average them directly.
    Linear,
    /// `Rgba8UnormSrgb` — decode, average, re-encode.
    Srgb,
}

/// Every mip level of an RGBA8 image, from the one given down to 1×1.
///
/// Returns `(width, height, pixels)` per level, including level 0, so a caller
/// can upload the chain in one loop and never re-derives a size it would then
/// have to keep in step.
///
/// Non-square and non-power-of-two images are handled: each axis halves
/// independently and stops at 1, which is exactly what `mip_level_count` counts,
/// and an odd parent edge samples its last row or column twice rather than
/// reading past the end.
pub fn chain(base: Vec<u8>, width: u32, height: u32, space: Space) -> Vec<(u32, u32, Vec<u8>)> {
    let (width, height) = (width.max(1), height.max(1));
    let mut levels = vec![(width, height, base)];
    let (mut w, mut h) = (width, height);
    while w > 1 || h > 1 {
        let (pw, ph, prev) = levels.last().expect("a base level");
        let (pw, ph) = (*pw, *ph);
        w = (w / 2).max(1);
        h = (h / 2).max(1);
        let mut next = vec![0u8; (w * h * 4) as usize];
        for y in 0..h {
            for x in 0..w {
                let mut acc = [0.0f32; 4];
                for (dy, dx) in [(0, 0), (0, 1), (1, 0), (1, 1)] {
                    let sy = (y * 2 + dy).min(ph - 1);
                    let sx = (x * 2 + dx).min(pw - 1);
                    let i = ((sy * pw + sx) * 4) as usize;
                    for c in 0..3 {
                        let v = prev[i + c] as f32 / 255.0;
                        acc[c] += match space {
                            Space::Linear => v,
                            Space::Srgb => srgb_to_linear(v),
                        };
                    }
                    // Alpha is coverage, never colour.
                    acc[3] += prev[i + 3] as f32 / 255.0;
                }
                let o = ((y * w + x) * 4) as usize;
                for c in 0..3 {
                    let v = acc[c] / 4.0;
                    let v = match space {
                        Space::Linear => v,
                        Space::Srgb => linear_to_srgb(v),
                    };
                    next[o + c] = (v * 255.0).round().clamp(0.0, 255.0) as u8;
                }
                next[o + 3] = (acc[3] / 4.0 * 255.0).round().clamp(0.0, 255.0) as u8;
            }
        }
        levels.push((w, h, next));
    }
    levels
}

/// The sRGB electro-optical transfer function, exactly as the spec writes it —
/// a linear toe below the knee and a 2.4 power above, **not** a plain `powf(2.2)`
/// approximation. The approximation is close enough to look right and wrong
/// enough that a round trip does not land back where it started, which would
/// show up here as a level-0-to-level-1 step on a flat surface.
fn srgb_to_linear(v: f32) -> f32 {
    if v <= 0.040_45 {
        v / 12.92
    } else {
        ((v + 0.055) / 1.055).powf(2.4)
    }
}

fn linear_to_srgb(v: f32) -> f32 {
    if v <= 0.003_130_8 {
        v * 12.92
    } else {
        1.055 * v.powf(1.0 / 2.4) - 0.055
    }
}

/// The anisotropy every sampler in this client asks for.
///
/// **A sampler using this must have `mag_filter`, `min_filter` *and*
/// `mipmap_filter` all set to `Linear`.** `anisotropy_clamp > 1` with any of the
/// three on `Nearest` is a validation error at sampler creation — which is a
/// hard stop on the first frame, not a softer picture — so the constant lives
/// next to the chain builder that makes those filters meaningful, and the two
/// changes are made together or not at all. wgpu clamps this down on adapters
/// that support less, so 16 is a ceiling rather than a demand.
///
/// Mips alone trade the shimmer for a blur; anisotropy is what recovers the
/// sharpness at the grazing angle a floor is nearly always seen at, which is the
/// angle that matters in a shooter.
pub const ANISOTROPY: u16 = 16;

#[cfg(test)]
mod tests {
    use super::*;

    fn flat(w: u32, h: u32, px: [u8; 4]) -> Vec<u8> {
        (0..w * h).flat_map(|_| px).collect()
    }

    #[test]
    fn a_chain_ends_at_one_by_one_and_declares_every_level() {
        // A chain that stops short leaves the smallest levels undefined, and a
        // sampler told `mip_level_count` reads them anyway: the far end of a
        // corridor samples uninitialised memory, which is black on some drivers
        // and whatever was there on others.
        let levels = chain(flat(64, 64, [128; 4]), 64, 64, Space::Linear);
        assert_eq!(levels.len(), 7, "64 -> 1 is seven levels");
        assert_eq!((levels[0].0, levels[0].1), (64, 64));
        let last = levels.last().expect("a level");
        assert_eq!((last.0, last.1), (1, 1));
        for (w, h, px) in &levels {
            assert_eq!(
                px.len(),
                (w * h * 4) as usize,
                "level {w}x{h} is the wrong size"
            );
        }
    }

    #[test]
    fn a_non_square_image_halves_each_axis_independently() {
        // Character maps are not square, and an axis that stops at 1 must let
        // the other keep going — the level count is the *longest* edge's.
        let levels = chain(flat(8, 2, [200; 4]), 8, 2, Space::Srgb);
        let sizes: Vec<(u32, u32)> = levels.iter().map(|(w, h, _)| (*w, *h)).collect();
        assert_eq!(sizes, vec![(8, 2), (4, 1), (2, 1), (1, 1)]);
    }

    #[test]
    fn a_flat_image_keeps_its_value_at_every_level() {
        // The strongest statement of both colour-space rules at once: filtering
        // a constant image must be a no-op. Averaging sRGB bytes as if they were
        // linear fails this and darkens; decoding linear bytes as if they were
        // sRGB fails it and dims. Mid-grey is used because that is where the two
        // spaces disagree most — 0 and 255 agree in both and would pass a broken
        // implementation.
        for space in [Space::Linear, Space::Srgb] {
            let levels = chain(flat(32, 32, [128, 128, 128, 255]), 32, 32, space);
            for (w, h, px) in &levels {
                assert!(
                    px.chunks(4).all(|p| (p[0] as i32 - 128).abs() <= 1),
                    "{space:?} level {w}x{h} drifted to {}",
                    px[0]
                );
            }
        }
    }

    #[test]
    fn averaging_sRGB_is_brighter_than_averaging_its_raw_bytes() {
        // Black and white in a checker. In linear light the answer is mid-grey,
        // which is sRGB 188 — *not* 128, which is what averaging the encoded
        // bytes gives. This is the whole difference between the two spaces, and
        // it is a 60-value error hiding behind a plausible-looking number.
        let checker: Vec<u8> = (0..4)
            .flat_map(|i| {
                let v = if i % 2 == 0 { 0u8 } else { 255u8 };
                [v, v, v, 255]
            })
            .collect();
        let naive = chain(checker.clone(), 2, 2, Space::Linear);
        let correct = chain(checker, 2, 2, Space::Srgb);
        // (0 + 255 + 0 + 255) / 4 = 127.5, rounded.
        assert_eq!(naive[1].2[0], 128, "the raw-byte average moved");
        assert!(
            correct[1].2[0] >= 186 && correct[1].2[0] <= 190,
            "sRGB average came out {}, want ~188",
            correct[1].2[0]
        );
    }

    #[test]
    fn alpha_is_averaged_as_coverage_in_both_spaces() {
        // Alpha is never sRGB-encoded. Running it through the colour transfer
        // function is the third version of this bug and it eats soft edges.
        let px: Vec<u8> = [[0u8, 0, 0, 0], [0, 0, 0, 255], [0, 0, 0, 0], [0, 0, 0, 255]].concat();
        for space in [Space::Linear, Space::Srgb] {
            let levels = chain(px.clone(), 2, 2, space);
            assert_eq!(
                levels[1].2[3], 128,
                "{space:?} did not average alpha linearly"
            );
        }
    }

    #[test]
    fn the_transfer_function_round_trips() {
        // A `powf(2.2)` approximation looks right and does not round-trip, which
        // would show as a step between level 0 and level 1 on a flat surface.
        for i in 0..=255u32 {
            let v = i as f32 / 255.0;
            let back = linear_to_srgb(srgb_to_linear(v));
            assert!((back - v).abs() < 1e-4, "{v} came back as {back}");
        }
    }
}
