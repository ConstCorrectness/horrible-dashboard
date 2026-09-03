//! The grain on every surface in the world.
//!
//! A port of `packages/core/src/modules/hassault/surfaces.ts`. The map mesh is
//! untextured by necessity — AssaultCube's textures are its copyright and are
//! never bundled — and the consequence was that every wall in the game was one
//! flat colour across its whole face. Lighting alone cannot fix that: a flat
//! surface lit evenly is still flat, and at a distance the world reads as
//! coloured cardboard. That is precisely what the native client looked like
//! until this landed, while the browser had had the fix for some time.
//!
//! The detail is **generated at runtime from nothing**: one small seamless tile
//! of value noise and cube seams, used as a multiplier over the vertex colours
//! the mesh already carries. It is nobody's artwork, it adds one texture to the
//! whole scene, and it gives the eye something to resolve scale and distance
//! against — which is the entire job.
//!
//! Two things it deliberately is *not*:
//!
//! - **Not a colour.** The tile is greyscale around neutral, so it modulates the
//!   tint `geometry.rs` assigns per texture id rather than replacing it. A
//!   detail map carrying its own hue would make every surface in every map the
//!   same colour, which is the flatness this exists to remove.
//! - **Not sRGB.** It is a multiplier, so it must be sampled linearly: authored
//!   1.0 has to mean "leave this pixel alone", and an sRGB decode would darken
//!   every surface in the game by a third and look like a lighting bug.
//!
//! The maths is `surfaces.ts`'s, in `f64` because JavaScript numbers are `f64`
//! and the hash multiplies by 43758.5453123 — at `f32` the fraction that comes
//! back is a different number entirely, and the two clients would grain the same
//! wall differently.

use crate::mipmap::Space;

/// Tile resolution. Small on purpose: it is sampled once per cube, so detail
/// beyond this is smaller than a pixel at any distance you would notice.
pub const SIZE: u32 = 128;

/// How far the grain swings either side of neutral.
///
/// Low enough that it reads as a material rather than as dirt: the surface tints
/// are already muted, and noise loud enough to see on its own turns every wall
/// into television static.
const GRAIN: f64 = 0.11;

/// How much darker the seam at the edge of each cube is.
const SEAM: f64 = 0.16;

/// What the tile writes for a pixel with no grain and no seam.
///
/// Stored with 1.0 mapped to 189 rather than 255 so the tile can **brighten** as
/// well as darken without clipping — a multiplier that can only subtract is a
/// dirt map, and it drags the whole world dark. The shader multiplies by the
/// reciprocal, so a neutral pixel leaves a surface exactly as `geometry.rs`
/// coloured it.
pub const NEUTRAL: f32 = 189.0 / 255.0;

/// A small deterministic hash, so the same map always gets the same grain.
///
/// Determinism matters more than it looks: a random tile would mean the same
/// wall in the same map looking different every time you pressed Play, with
/// nothing to explain why.
fn hash(x: f64, y: f64) -> f64 {
    let n = (x * 127.1 + y * 311.7).sin() * 43758.5453123;
    n - n.floor()
}

/// Value noise: a hashed lattice, smoothly interpolated.
fn value_noise(x: f64, y: f64, period: f64) -> f64 {
    let xi = x.floor();
    let yi = y.floor();
    let xf = x - xi;
    let yf = y - yi;
    // Smoothstep the fraction, or the lattice shows as a grid of diamonds.
    let u = xf * xf * (3.0 - 2.0 * xf);
    let v = yf * yf * (3.0 - 2.0 * yf);
    // Wrapped, so the tile is seamless — an unwrapped lattice puts a hard line
    // down every cube boundary in the world.
    let w = |n: f64| ((n % period) + period) % period;
    let a = hash(w(xi), w(yi));
    let b = hash(w(xi + 1.0), w(yi));
    let c = hash(w(xi), w(yi + 1.0));
    let d = hash(w(xi + 1.0), w(yi + 1.0));
    a + (b - a) * u + (c - a) * v + (a - b - c + d) * u * v
}

/// Draw one seamless tile of surface detail, as RGBA8.
pub fn draw_tile(size: u32) -> Vec<u8> {
    let mut out = vec![0u8; (size * size * 4) as usize];
    for py in 0..size {
        for px in 0..size {
            // Three octaves. The coarse one gives large-scale mottling so the
            // surface does not read as uniform sandpaper; the fine one is the
            // grain itself.
            let u = px as f64 / size as f64;
            let v = py as f64 / size as f64;
            let mut n = 0.0;
            n += (value_noise(u * 4.0, v * 4.0, 4.0) - 0.5) * 0.55;
            n += (value_noise(u * 12.0, v * 12.0, 12.0) - 0.5) * 0.3;
            n += (value_noise(u * 32.0, v * 32.0, 32.0) - 0.5) * 0.15;

            // The seam: a soft dark line at the tile edge. The UVs are in cube
            // units, so this draws the cube lattice the map is actually built
            // on — which is architecture in a Cube-engine level, not a tiling
            // artefact.
            let edge = u.min(1.0 - u).min(v.min(1.0 - v));
            let seam = SEAM * (1.0 - (edge / 0.035).min(1.0));

            let value = (1.0 + n * GRAIN * 2.0 - seam).clamp(0.0, 1.35);
            let byte = (value * 189.0).round().clamp(0.0, 255.0) as u8;
            let i = ((py * size + px) * 4) as usize;
            out[i] = byte;
            out[i + 1] = byte;
            out[i + 2] = byte;
            out[i + 3] = 255;
        }
    }
    out
}

/// The bind group layout the world shader expects for the grain.
///
/// Here rather than in `renderer.rs` so the offscreen preview in
/// `examples/operator_preview.rs` builds the *same* layout — a second copy is a
/// second thing to keep in step with the shader, and the shader only tells you
/// they have diverged at pipeline creation.
pub fn bind_group_layout(device: &wgpu::Device) -> wgpu::BindGroupLayout {
    device.create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
        label: Some("detail-layout"),
        entries: &[
            wgpu::BindGroupLayoutEntry {
                binding: 0,
                visibility: wgpu::ShaderStages::FRAGMENT,
                ty: wgpu::BindingType::Texture {
                    sample_type: wgpu::TextureSampleType::Float { filterable: true },
                    view_dimension: wgpu::TextureViewDimension::D2,
                    multisampled: false,
                },
                count: None,
            },
            wgpu::BindGroupLayoutEntry {
                binding: 1,
                visibility: wgpu::ShaderStages::FRAGMENT,
                ty: wgpu::BindingType::Sampler(wgpu::SamplerBindingType::Filtering),
                count: None,
            },
        ],
    })
}

/// Generate the surface grain and upload it.
///
/// Mipmapped and anisotropic, which it was not.
///
/// The UVs are in cube units, so a floor seen down a corridor samples hundreds
/// of tiles per pixel row. With one mip level that boils into moire as you walk
/// — the single loudest artefact in the client, and the thing that makes a
/// corridor shimmer. The note that used to be here deferred it as "a blit chain
/// this change does not need", which was true of a chain built on the GPU and
/// beside the point: [`draw_tile`] produces the pixels on the CPU, so the whole
/// chain is a box filter over an array we already hold. See [`mip_chain`].
///
/// Anisotropy is the other half and is what actually fixes the grazing angle a
/// floor is nearly always seen at; mips alone trade the shimmer for a blur.
/// Every mip level of the detail tile.
///
/// Delegates to [`crate::mipmap::chain`] with [`Space::Linear`], and the choice
/// is the load-bearing part: this texture is `Rgba8Unorm` because it is a
/// *multiplier* and not a colour, so its bytes are already linear. Filtering it
/// as sRGB would dim every level below the first and the world would darken with
/// distance — which reads as fog, not as a mipmap bug. See the module docs on
/// `mipmap` for the other half of that trap.
fn mip_chain(base: Vec<u8>, size: u32) -> Vec<(u32, Vec<u8>)> {
    crate::mipmap::chain(base, size, size, Space::Linear)
        .into_iter()
        .map(|(edge, _, pixels)| (edge, pixels))
        .collect()
}

pub fn bind_group(
    device: &wgpu::Device,
    queue: &wgpu::Queue,
    layout: &wgpu::BindGroupLayout,
) -> wgpu::BindGroup {
    let size = wgpu::Extent3d {
        width: SIZE,
        height: SIZE,
        depth_or_array_layers: 1,
    };
    let levels = mip_chain(draw_tile(SIZE), SIZE);
    let texture = device.create_texture(&wgpu::TextureDescriptor {
        label: Some("detail"),
        size,
        mip_level_count: levels.len() as u32,
        sample_count: 1,
        dimension: wgpu::TextureDimension::D2,
        // **Unorm, not Srgb.** The tile is a multiplier, not a colour: authored
        // 1.0 has to mean "leave this pixel alone", and an sRGB decode would
        // darken every surface in the game by a third and look like a lighting
        // bug.
        format: wgpu::TextureFormat::Rgba8Unorm,
        usage: wgpu::TextureUsages::TEXTURE_BINDING | wgpu::TextureUsages::COPY_DST,
        view_formats: &[],
    });
    for (level, (edge, pixels)) in levels.iter().enumerate() {
        queue.write_texture(
            wgpu::TexelCopyTextureInfo {
                texture: &texture,
                mip_level: level as u32,
                origin: wgpu::Origin3d::ZERO,
                aspect: wgpu::TextureAspect::All,
            },
            pixels,
            wgpu::TexelCopyBufferLayout {
                offset: 0,
                bytes_per_row: Some(4 * edge),
                rows_per_image: Some(*edge),
            },
            wgpu::Extent3d {
                width: *edge,
                height: *edge,
                depth_or_array_layers: 1,
            },
        );
    }
    let view = texture.create_view(&wgpu::TextureViewDescriptor::default());
    let sampler = device.create_sampler(&wgpu::SamplerDescriptor {
        label: Some("detail-sampler"),
        // Repeating is the whole point: one tile per cube, across the map.
        address_mode_u: wgpu::AddressMode::Repeat,
        address_mode_v: wgpu::AddressMode::Repeat,
        address_mode_w: wgpu::AddressMode::Repeat,
        mag_filter: wgpu::FilterMode::Linear,
        min_filter: wgpu::FilterMode::Linear,
        mipmap_filter: wgpu::MipmapFilterMode::Linear,
        // **All three filters above must be `Linear` for this to be legal.**
        // `anisotropy_clamp > 1` with any of them on `Nearest` is a validation
        // error at sampler creation, which is a hard stop on the first frame
        // rather than a softer picture — so the two changes belong in one edit.
        // 16 is the ceiling every desktop adapter has supported for years and
        // wgpu clamps it down where it is not.
        anisotropy_clamp: crate::mipmap::ANISOTROPY,
        ..Default::default()
    });
    device.create_bind_group(&wgpu::BindGroupDescriptor {
        label: Some("detail"),
        layout,
        entries: &[
            wgpu::BindGroupEntry {
                binding: 0,
                resource: wgpu::BindingResource::TextureView(&view),
            },
            wgpu::BindGroupEntry {
                binding: 1,
                resource: wgpu::BindingResource::Sampler(&sampler),
            },
        ],
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_mip_chain_runs_all_the_way_down_to_one_pixel() {
        // A chain that stops short leaves the smallest levels undefined, and a
        // sampler told `mip_level_count` reads them: the far end of a corridor
        // samples uninitialised memory, which on some drivers is black and on
        // others is whatever was there.
        let levels = mip_chain(draw_tile(SIZE), SIZE);
        assert_eq!(levels[0].0, SIZE);
        assert_eq!(levels.last().expect("a level").0, 1);
        assert_eq!(levels.len(), SIZE.ilog2() as usize + 1);
        for (edge, pixels) in &levels {
            assert_eq!(
                pixels.len(),
                (edge * edge * 4) as usize,
                "level {edge} is the wrong size"
            );
        }
    }

    #[test]
    fn averaging_does_not_darken_the_tile() {
        // The trap this pins: the detail tile is `Rgba8Unorm` because it is a
        // *multiplier*, not a colour, so its bytes are already linear. Filtering
        // it through an sRGB decode — the reflex when the word "texture" appears
        // — would drop the mean of every level below the first, and the world
        // would get darker with distance. That reads as fog, not as a mipmap
        // bug, which is why it is worth a test rather than a comment.
        let levels = mip_chain(draw_tile(SIZE), SIZE);
        let mean = |px: &[u8]| px.iter().map(|b| *b as f64).sum::<f64>() / px.len() as f64;
        let base = mean(&levels[0].1);
        for (edge, pixels) in &levels {
            let m = mean(pixels);
            assert!(
                (m - base).abs() < 2.0,
                "level {edge} averages {m}, base is {base}"
            );
        }
    }

    /// The same two properties `surfaces.test.ts` pins, and for the same reason:
    /// there is no reference image to compare a generated texture against, so
    /// what can be checked is that it stays near neutral and that it wraps.
    #[test]
    fn the_tile_stays_near_neutral() {
        let tile = draw_tile(SIZE);
        let mean: f64 =
            tile.iter().step_by(4).map(|&b| b as f64).sum::<f64>() / (SIZE * SIZE) as f64;
        // Below 189 because the seam only ever subtracts, but not far below —
        // a tile that drifts dark drags the whole world down with it.
        assert!(
            (170.0..=192.0).contains(&mean),
            "mean is {mean}, expected near the neutral 189"
        );
    }

    #[test]
    fn the_noise_lattice_is_periodic() {
        // A lattice that does not wrap puts a hard line down every cube boundary
        // in the world, which looks like a mesh bug rather than a texture one.
        //
        // Checked on the noise rather than on the drawn tile: the tile's edge
        // columns are deliberately *not* equal, because the seam is sampled at
        // pixel centres and column 0 sits exactly on it while the last column is
        // one pixel inside. Asserting the edges match would be asserting the
        // seam is absent — which is the opposite of what it is for.
        for i in 0..64 {
            let t = i as f64 * 0.17;
            for period in [4.0, 12.0, 32.0] {
                let a = value_noise(t, t * 0.6, period);
                let b = value_noise(t + period, t * 0.6, period);
                let c = value_noise(t, t * 0.6 + period, period);
                assert!((a - b).abs() < 1e-9, "period {period} does not wrap in x");
                assert!((a - c).abs() < 1e-9, "period {period} does not wrap in y");
            }
        }
    }

    #[test]
    fn the_seam_is_drawn_at_the_tile_edge() {
        // The seam is the cube lattice. If it stopped being drawn the world
        // would lose the only cue that it is built out of cubes at all.
        let tile = draw_tile(SIZE);
        let at = |x: u32, y: u32| tile[((y * SIZE + x) * 4) as usize] as i32;
        let middle = at(SIZE / 2, SIZE / 2);
        assert!(
            at(0, SIZE / 2) < middle - 15,
            "the left edge is not darker than the middle"
        );
        assert!(
            at(SIZE / 2, 0) < middle - 15,
            "the top edge is not darker than the middle"
        );
    }

    #[test]
    fn it_is_deterministic() {
        assert_eq!(draw_tile(32), draw_tile(32));
    }

    #[test]
    fn it_can_brighten_as_well_as_darken() {
        // The whole point of mapping 1.0 to 189 rather than 255. If every pixel
        // were at or below neutral this would be a dirt map.
        let tile = draw_tile(SIZE);
        let max = tile.iter().step_by(4).copied().max().unwrap_or(0);
        assert!(max > 189, "nothing brightens: peak is {max}");
    }
}

/// The tile's seat at the shared-fixture table.
///
/// The tests above argue this tile is *usable* — near neutral, wrapping, able to
/// brighten. They say nothing about whether it is the **same** tile
/// `surfaces.ts` draws, and it has to be: the grain is sampled once per cube, so
/// a port that drifted would give the same wall in the same map a visibly
/// different material depending on which client the player launched, with
/// nothing failing anywhere to say so.
///
/// The fixture is the browser's own output, and the comparison is **exact**
/// rather than approximate. Both sides do this arithmetic in f64 — see the
/// header — and both round to a byte at the end, so there is no precision to
/// budget for. A tolerance here would quietly admit exactly the drift the file
/// exists to catch.
#[cfg(test)]
mod conformance {
    use super::*;

    /// The vectors the browser client replays too. Same file, resolved at
    /// compile time — see the note in `geometry.rs`'s conformance module.
    const VECTORS: &str =
        include_str!("../../../packages/core/src/modules/hassault/__tests__/surface-vectors.json");

    fn detail() -> serde_json::Value {
        serde_json::from_str::<serde_json::Value>(VECTORS)
            .expect("surface-vectors.json should parse")["detail"]
            .clone()
    }

    /// The red channel at a pixel of an RGBA8 tile.
    fn at(tile: &[u8], size: u32, x: u32, y: u32) -> u8 {
        tile[((y * size + x) * 4) as usize]
    }

    #[test]
    fn the_tile_matches_the_browsers_pixel_for_pixel() {
        let d = detail();
        let size = d["size"].as_u64().expect("size") as u32;
        assert_eq!(size, SIZE, "the fixture was generated at a different size");
        let tile = draw_tile(size);

        let pixels = d["pixels"].as_array().expect("pixels");
        assert!(!pixels.is_empty(), "the fixture has no pixels in it");
        for p in pixels {
            let x = p["x"].as_u64().expect("x") as u32;
            let y = p["y"].as_u64().expect("y") as u32;
            let want = p["value"].as_u64().expect("value") as u8;
            let got = at(&tile, size, x, y);
            assert_eq!(
                got, want,
                "pixel ({x}, {y}) is {got} here and {want} in the browser"
            );
        }
    }

    #[test]
    fn the_neutral_byte_matches_the_browsers() {
        // This side's `NEUTRAL` and the browser's `DETAIL_NEUTRAL` are the
        // reciprocal each shader multiplies by. If the byte the tile stores and
        // the compensation disagree, every surface in the game is uniformly too
        // dark or too bright, and it reads as a lighting bug rather than as a
        // texture one.
        let d = detail();
        let want = d["neutralByte"].as_u64().expect("neutralByte") as f32;
        assert!(
            (NEUTRAL * 255.0 - want).abs() < 0.5,
            "neutral is {} here",
            NEUTRAL * 255.0
        );
    }

    #[test]
    fn the_whole_tile_agrees_and_not_just_the_sampled_pixels() {
        // Nineteen sampled pixels can match by luck while the rest of the tile
        // has moved. These three numbers are over all 16384 of them.
        let d = detail();
        let size = d["size"].as_u64().expect("size") as u32;
        let tile = draw_tile(size);
        let values: Vec<u8> = tile.iter().step_by(4).copied().collect();

        let stats = &d["stats"];
        assert_eq!(
            *values.iter().min().unwrap() as u64,
            stats["min"].as_u64().expect("min"),
            "the darkest pixel differs"
        );
        assert_eq!(
            *values.iter().max().unwrap() as u64,
            stats["max"].as_u64().expect("max"),
            "the brightest pixel differs"
        );
        let mean = values.iter().map(|&v| v as f64).sum::<f64>() / values.len() as f64;
        let want = stats["mean"].as_f64().expect("mean");
        assert!(
            (mean - want).abs() < 1e-6,
            "the tile averages {mean} here and {want} in the browser"
        );
    }

    #[test]
    fn the_wrap_period_follows_the_size_it_was_asked_for() {
        // `size` is a parameter and the lattice's period is derived from it. A
        // port that hardcoded 128 anywhere inside the noise would match every
        // sampled pixel of the full-size tile and fail only here.
        let d = detail();
        let size = d["smallSize"].as_u64().expect("smallSize") as u32;
        let tile = draw_tile(size);
        for p in d["smallPixels"].as_array().expect("smallPixels") {
            let x = p["x"].as_u64().expect("x") as u32;
            let y = p["y"].as_u64().expect("y") as u32;
            let want = p["value"].as_u64().expect("value") as u8;
            let got = at(&tile, size, x, y);
            assert_eq!(
                got, want,
                "the {size}-wide tile is {got} at ({x}, {y}) and {want} in the browser"
            );
        }
    }
}
