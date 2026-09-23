//! Crossfire-Grade Procedural PBR Textures & Surfaces Engine for HorribleAssault.
//!
//! Generates high-fidelity, deterministic procedural PBR surface textures and materials
//! matching `packages/core/src/modules/hassault/textures3d.ts`.
//!
//! Provides 18 distinct surface materials packed into a 2D Texture Array on the GPU
//! (the six after the site decals — masonry, plaster, cobblestone, roof tile,
//! carpet, brick — exist for the GLB maps' materials; see `glb-surfaces.json`):
//! 1. Asphalt Road with road stripes and aggregate gravel.
//! 2. Polished Bank Marble Floor with elegant veining and grout lines.
//! 3. Weathered Architectural Concrete with formwork seams and tie-rod holes.
//! 4. Brushed Vault Steel with perimeter rivets and anisotropic brush grain.
//! 5. 45-Degree Industrial Hazard Warning Stripes.
//! 6. Rich Dark Mahogany Wood Planks for teller counters.
//! 7. Military Tactical Crates with metal corner reinforcements.
//! 8. Corrugated Industrial Metal for shipping containers (Junk Flea).
//! 9. Bulletproof Bank Glass (semi-transparent, specular reflection).
//! 10. Polished Gold Bullion for vault pallets.
//! 11. Tactical Bomb Site Spray Stencils ("A").
//! 12. Tactical Bomb Site Spray Stencils ("B").

pub const TEXTURE_SIZE: u32 = 256;
pub const LAYER_COUNT: u32 = 18;

/// Fast deterministic pseudo-random hash.
fn hash(x: f32, y: f32, seed: f32) -> f32 {
    let n = (x * 127.1 + y * 311.7 + seed * 157.3).sin() * 43758.5453123;
    n - n.floor()
}

/// 2D Smooth Value Noise.
fn noise2d(x: f32, y: f32, period: f32) -> f32 {
    let xi = x.floor();
    let yi = y.floor();
    let xf = x - xi;
    let yf = y - yi;
    let u = xf * xf * (3.0 - 2.0 * xf);
    let v = yf * yf * (3.0 - 2.0 * yf);
    let w = |n: f32| ((n % period) + period) % period;

    let a = hash(w(xi), w(yi), 0.0);
    let b = hash(w(xi + 1.0), w(yi), 0.0);
    let c = hash(w(xi), w(yi + 1.0), 0.0);
    let d = hash(w(xi + 1.0), w(yi + 1.0), 0.0);

    a + (b - a) * u + (c - a) * v + (a - b - c + d) * u * v
}

/// Fractal Brownian Motion (fBm) multi-octave noise.
fn fbm(x: f32, y: f32, octaves: usize) -> f32 {
    let mut val = 0.0;
    let mut amp = 0.5;
    let mut freq = 1.0;
    let mut max = 0.0;
    for _ in 0..octaves {
        val += noise2d(x * freq, y * freq, 256.0 * freq) * amp;
        max += amp;
        amp *= 0.5;
        freq *= 2.0;
    }
    val / max
}

/// 1. Tactical Asphalt Road:
/// High-frequency aggregate gravel, bitumen tonal variation, and yellow/white road marking stripes.
pub fn draw_asphalt_tile(width: u32, height: u32) -> Vec<u8> {
    let mut out = vec![0u8; (width * height * 4) as usize];
    for y in 0..height {
        for x in 0..width {
            let u = x as f32 / width as f32;
            let v = y as f32 / height as f32;
            let n_gravel = fbm(u * 64.0, v * 64.0, 4);
            let n_macro = fbm(u * 6.0, v * 6.0, 3);
            let base_gray = (45.0 + n_macro * 25.0 + (n_gravel - 0.5) * 35.0).floor();

            let mut r = base_gray;
            let mut g = base_gray;
            let mut b = base_gray + 2.0;

            // Road markings: center yellow dashed stripe (u in 0.47..0.53, dashed in v)
            if (0.47..=0.53).contains(&u) && (0.15..=0.85).contains(&v) {
                let edge = (u - 0.47).abs().min((0.53 - u).abs()) / 0.03;
                let wear = 0.75 + 0.25 * n_gravel;
                r = (r * (1.0 - edge) + (220.0 * wear) * edge).floor();
                g = (g * (1.0 - edge) + (180.0 * wear) * edge).floor();
                b = (b * (1.0 - edge) + (25.0 * wear) * edge).floor();
            }

            // Edge white border stripes (u near 0.04..0.08 or 0.92..0.96)
            if (0.04..=0.08).contains(&u) || (0.92..=0.96).contains(&u) {
                let wear = 0.70 + 0.30 * n_gravel;
                r = r.max(190.0 * wear).floor();
                g = g.max(190.0 * wear).floor();
                b = b.max(195.0 * wear).floor();
            }

            let idx = ((y * width + x) * 4) as usize;
            out[idx] = r.clamp(0.0, 255.0) as u8;
            out[idx + 1] = g.clamp(0.0, 255.0) as u8;
            out[idx + 2] = b.clamp(0.0, 255.0) as u8;
            out[idx + 3] = 255;
        }
    }
    out
}

/// 2. Bank Grand Lobby Polished Marble Floor:
/// Quad tile grid with clean grout joints, organic veining, and high gloss finish.
pub fn draw_marble_tile(width: u32, height: u32) -> Vec<u8> {
    let mut out = vec![0u8; (width * height * 4) as usize];
    let tiles = 2.0; // 2x2 tile grid per texture
    for y in 0..height {
        for x in 0..width {
            let u = x as f32 / width as f32;
            let v = y as f32 / height as f32;

            // Grout line calculation
            let tu = (u * tiles) % 1.0;
            let tv = (v * tiles) % 1.0;
            let dist_grout = tu.min(1.0 - tu).min(tv.min(1.0 - tv));
            let is_grout = dist_grout < 0.025;
            let grout_factor = (dist_grout / 0.025).min(1.0);

            // Organic marble veining using turbulence noise
            let turb = fbm(u * 8.0, v * 8.0, 4) * 4.0;
            let vein = (u * 12.0 + v * 12.0 + turb).sin();
            let vein_dark = vein.abs().powi(4) * 0.35;

            // Base marble color: luxurious ivory-white with subtle warm gray tint
            let mut r = 230.0 - vein_dark * 120.0;
            let mut g = 226.0 - vein_dark * 115.0;
            let mut b = 220.0 - vein_dark * 105.0;

            // Alternate checkerboard warmth
            let tile_x = (u * tiles).floor() as i32;
            let tile_y = (v * tiles).floor() as i32;
            if (tile_x + tile_y) % 2 == 1 {
                r *= 0.94;
                g *= 0.94;
                b *= 0.95;
            }

            // Dark grout lines
            if is_grout {
                let dark_grout = 75.0;
                r = r * grout_factor + dark_grout * (1.0 - grout_factor);
                g = g * grout_factor + dark_grout * (1.0 - grout_factor);
                b = b * grout_factor + dark_grout * (1.0 - grout_factor);
            }

            let idx = ((y * width + x) * 4) as usize;
            out[idx] = r.clamp(0.0, 255.0) as u8;
            out[idx + 1] = g.clamp(0.0, 255.0) as u8;
            out[idx + 2] = b.clamp(0.0, 255.0) as u8;
            out[idx + 3] = 255;
        }
    }
    out
}

/// 3. Weathered Architectural Concrete Panels:
/// Structural modular concrete panels with formwork seams, tie-rod holes, and aggregate grit.
pub fn draw_concrete_tile(width: u32, height: u32) -> Vec<u8> {
    let mut out = vec![0u8; (width * height * 4) as usize];
    for y in 0..height {
        for x in 0..width {
            let u = x as f32 / width as f32;
            let v = y as f32 / height as f32;

            // Panel seam lines: 2 panels horizontally and vertically
            let pu = (u * 2.0) % 1.0;
            let pv = (v * 2.0) % 1.0;
            let edge_u = pu.min(1.0 - pu);
            let edge_v = pv.min(1.0 - pv);
            let is_seam = edge_u.min(edge_v) < 0.018;

            // Tie-rod indentations in panel corners
            let hole_u = (pu - 0.12).abs() < 0.03 || (pu - 0.88).abs() < 0.03;
            let hole_v = (pv - 0.12).abs() < 0.03 || (pv - 0.88).abs() < 0.03;
            let is_hole = hole_u && hole_v;

            // Aggregate texture
            let n_fine = fbm(u * 32.0, v * 32.0, 4);
            let n_macro = fbm(u * 4.0, v * 4.0, 3);
            let mut val = 145.0 + (n_macro - 0.5) * 30.0 + (n_fine - 0.5) * 20.0;

            // Vertical rain/grime streak
            let streak = noise2d(u * 16.0, 0.5, 256.0) * (1.0 - v) * 25.0;
            val -= streak;

            if is_seam {
                val *= 0.55;
            }
            if is_hole {
                val *= 0.35;
            }

            let idx = ((y * width + x) * 4) as usize;
            out[idx] = (val * 0.98).clamp(0.0, 255.0) as u8;
            out[idx + 1] = (val * 1.00).clamp(0.0, 255.0) as u8;
            out[idx + 2] = (val * 1.02).clamp(0.0, 255.0) as u8;
            out[idx + 3] = 255;
        }
    }
    out
}

/// 4. Brushed Vault Steel Blast Door:
/// Directional anisotropic brushed finish, beveled border, and steel rivet studs.
pub fn draw_vault_steel_tile(width: u32, height: u32) -> Vec<u8> {
    let mut out = vec![0u8; (width * height * 4) as usize];
    for y in 0..height {
        for x in 0..width {
            let u = x as f32 / width as f32;
            let v = y as f32 / height as f32;

            // Horizontal brushed metal grain
            let grain = noise2d(u * 2.0, v * 128.0, 256.0) * 28.0;
            let mut val = 120.0 + grain;

            // Perimeter bevel border
            let edge = u.min(1.0 - u).min(v.min(1.0 - v));
            if edge < 0.06 {
                val *= 0.6 + (edge / 0.06) * 0.4;
            }

            // Perimeter round steel rivets
            let rivet_spacing = 0.125;
            let on_rivet_line = (edge - 0.04).abs() < 0.015;
            let rivet_x = ((u % rivet_spacing) - rivet_spacing / 2.0).abs() < 0.015;
            let rivet_y = ((v % rivet_spacing) - rivet_spacing / 2.0).abs() < 0.015;
            if on_rivet_line && (rivet_x || rivet_y) {
                val = (val + 65.0).min(255.0); // Bright specular rivet cap
            }

            let idx = ((y * width + x) * 4) as usize;
            out[idx] = (val * 0.95).clamp(0.0, 255.0) as u8;
            out[idx + 1] = (val * 1.00).clamp(0.0, 255.0) as u8;
            out[idx + 2] = (val * 1.08).clamp(0.0, 255.0) as u8;
            out[idx + 3] = 255;
        }
    }
    out
}

/// 5. Industrial Hazard Warning Stripes:
/// 45-degree bold diagonal yellow/black safety stripes used on vault frames & dangerous areas.
pub fn draw_hazard_tile(width: u32, height: u32) -> Vec<u8> {
    let mut out = vec![0u8; (width * height * 4) as usize];
    for y in 0..height {
        for x in 0..width {
            let u = x as f32 / width as f32;
            let v = y as f32 / height as f32;

            // 45 degree repeating diagonal bands
            let stripe = ((u + v) * 8.0) % 1.0;
            let is_yellow = stripe < 0.5;

            let n_grit = (noise2d(u * 32.0, v * 32.0, 256.0) - 0.5) * 25.0;

            let (r, g, b) = if is_yellow {
                (245.0 + n_grit, 185.0 + n_grit, 20.0 + n_grit)
            } else {
                (35.0 + n_grit, 35.0 + n_grit, 38.0 + n_grit)
            };

            let idx = ((y * width + x) * 4) as usize;
            out[idx] = r.clamp(0.0, 255.0) as u8;
            out[idx + 1] = g.clamp(0.0, 255.0) as u8;
            out[idx + 2] = b.clamp(0.0, 255.0) as u8;
            out[idx + 3] = 255;
        }
    }
    out
}

/// 6. Mahogany Wood Planks:
/// Rich polished dark mahogany wood with directional organic grain for teller counters.
pub fn draw_wood_tile(width: u32, height: u32) -> Vec<u8> {
    let mut out = vec![0u8; (width * height * 4) as usize];
    let planks = 4.0;
    for y in 0..height {
        for x in 0..width {
            let u = x as f32 / width as f32;
            let v = y as f32 / height as f32;

            // Plank seams
            let pv = (v * planks) % 1.0;
            let is_seam = pv.min(1.0 - pv) < 0.02;

            // Wood grain along X
            let grain = (u * 2.0 + noise2d(u * 4.0, v * 32.0, 256.0) * 8.0).sin();
            let intensity = 0.8 + 0.2 * grain;

            let mut r = 135.0 * intensity;
            let mut g = 65.0 * intensity;
            let mut b = 32.0 * intensity;

            if is_seam {
                r *= 0.4;
                g *= 0.4;
                b *= 0.4;
            }

            let idx = ((y * width + x) * 4) as usize;
            out[idx] = r.clamp(0.0, 255.0) as u8;
            out[idx + 1] = g.clamp(0.0, 255.0) as u8;
            out[idx + 2] = b.clamp(0.0, 255.0) as u8;
            out[idx + 3] = 255;
        }
    }
    out
}

/// 7. Military Tactical Supply Crate:
/// Olive-drab crate with steel corner angle brackets, rivets, and stenciled markings.
pub fn draw_tactical_crate_tile(width: u32, height: u32) -> Vec<u8> {
    let mut out = vec![0u8; (width * height * 4) as usize];
    for y in 0..height {
        for x in 0..width {
            let u = x as f32 / width as f32;
            let v = y as f32 / height as f32;

            let edge = u.min(1.0 - u).min(v.min(1.0 - v));
            let is_steel_frame = edge < 0.12;

            let n_wood = fbm(u * 8.0, v * 8.0, 3);
            let mut r = 85.0 + (n_wood - 0.5) * 20.0;
            let mut g = 95.0 + (n_wood - 0.5) * 20.0;
            let mut b = 65.0 + (n_wood - 0.5) * 15.0;

            if is_steel_frame {
                r = 55.0;
                g = 58.0;
                b = 62.0;
                if (edge - 0.06).abs() < 0.02 && (u < 0.2 || u > 0.8 || v < 0.2 || v > 0.8) {
                    r += 40.0;
                    g += 40.0;
                    b += 45.0;
                }
            }

            // Diagonal cross-brace in center
            let diag1 = (u - v).abs();
            let diag2 = (u - (1.0 - v)).abs();
            if (diag1 < 0.035 || diag2 < 0.035) && !is_steel_frame {
                r *= 0.85;
                g *= 0.85;
                b *= 0.85;
            }

            let idx = ((y * width + x) * 4) as usize;
            out[idx] = r.clamp(0.0, 255.0) as u8;
            out[idx + 1] = g.clamp(0.0, 255.0) as u8;
            out[idx + 2] = b.clamp(0.0, 255.0) as u8;
            out[idx + 3] = 255;
        }
    }
    out
}

/// 8. Corrugated Metal Container (Junk Flea):
/// Industrial shipping container with horizontal ribbed corrugation and weathered paint.
pub fn draw_container_tile(width: u32, height: u32) -> Vec<u8> {
    let mut out = vec![0u8; (width * height * 4) as usize];
    for y in 0..height {
        for x in 0..width {
            let u = x as f32 / width as f32;
            let v = y as f32 / height as f32;

            let rib = (v * std::f32::consts::PI * 16.0).sin();
            let shade = 0.75 + 0.25 * rib;

            let n_rust = fbm(u * 12.0, v * 12.0, 3);
            let mut r = 40.0 * shade;
            let mut g = 75.0 * shade;
            let mut b = 120.0 * shade;

            if n_rust > 0.65 {
                let rust_f = (n_rust - 0.65) / 0.35;
                r = r * (1.0 - rust_f) + 140.0 * rust_f;
                g = g * (1.0 - rust_f) + 65.0 * rust_f;
                b = b * (1.0 - rust_f) + 35.0 * rust_f;
            }

            let idx = ((y * width + x) * 4) as usize;
            out[idx] = r.clamp(0.0, 255.0) as u8;
            out[idx + 1] = g.clamp(0.0, 255.0) as u8;
            out[idx + 2] = b.clamp(0.0, 255.0) as u8;
            out[idx + 3] = 255;
        }
    }
    out
}

/// 9. Bulletproof Bank Glass:
/// Translucent tinted glass surface with delicate edge refraction sheen.
pub fn draw_glass_tile(width: u32, height: u32) -> Vec<u8> {
    let mut out = vec![0u8; (width * height * 4) as usize];
    for y in 0..height {
        for x in 0..width {
            let u = x as f32 / width as f32;
            let v = y as f32 / height as f32;
            let sheen = (u * 6.0 + v * 4.0).sin() * 15.0;
            let r = 196.0 + sheen;
            let g = 226.0 + sheen;
            let b = 248.0 + sheen;
            let idx = ((y * width + x) * 4) as usize;
            out[idx] = r.clamp(0.0, 255.0) as u8;
            out[idx + 1] = g.clamp(0.0, 255.0) as u8;
            out[idx + 2] = b.clamp(0.0, 255.0) as u8;
            out[idx + 3] = 160;
        }
    }
    out
}

/// 10. Polished Gold Bullion:
/// Brushed metallic reflective gold bullion surface.
pub fn draw_gold_tile(width: u32, height: u32) -> Vec<u8> {
    let mut out = vec![0u8; (width * height * 4) as usize];
    for y in 0..height {
        for x in 0..width {
            let u = x as f32 / width as f32;
            let v = y as f32 / height as f32;
            let grain = noise2d(u * 64.0, v * 2.0, 256.0) * 20.0;
            let r: f32 = 255.0;
            let g = 215.0 + grain;
            let b = grain.max(0.0);
            let idx = ((y * width + x) * 4) as usize;
            out[idx] = r.clamp(0.0, 255.0) as u8;
            out[idx + 1] = g.clamp(0.0, 255.0) as u8;
            out[idx + 2] = b.clamp(0.0, 255.0) as u8;
            out[idx + 3] = 255;
        }
    }
    out
}

/// 11 & 12. Tactical Bomb Site Spray Decal ("A" or "B"):
/// Bold red/white stenciled military target tag for walls/floors.
pub fn draw_site_decal_tile(site: char, width: u32, height: u32) -> Vec<u8> {
    let mut out = vec![0u8; (width * height * 4) as usize];
    let cx = width as f32 / 2.0;
    let cy = height as f32 / 2.0;
    let r_outer = width as f32 * 0.42;
    let r_inner = width as f32 * 0.34;

    for y in 0..height {
        for x in 0..width {
            let dx = x as f32 - cx;
            let dy = y as f32 - cy;
            let dist = (dx * dx + dy * dy).sqrt();

            let is_ring = dist <= r_outer && dist >= r_inner;

            let nx = (x as f32 - (cx - width as f32 * 0.25)) / (width as f32 * 0.5);
            let ny = (y as f32 - (cy - height as f32 * 0.25)) / (height as f32 * 0.5);
            let mut is_letter = false;

            if (0.0..=1.0).contains(&nx) && (0.0..=1.0).contains(&ny) {
                if site == 'A' {
                    let in_leg1 = (ny - (1.0 - nx * 1.6)).abs() < 0.12 && nx <= 0.5;
                    let in_leg2 = (ny - (1.0 - (1.0 - nx) * 1.6)).abs() < 0.12 && nx >= 0.5;
                    let in_cross = (ny - 0.55).abs() < 0.08 && nx >= 0.25 && nx <= 0.75;
                    is_letter = in_leg1 || in_leg2 || in_cross;
                } else {
                    let in_stem = nx <= 0.25;
                    let in_top_loop =
                        (((nx - 0.4).powi(2) + (ny - 0.3).powi(2)).sqrt() - 0.25).abs() < 0.09
                            && nx >= 0.25;
                    let in_bot_loop =
                        (((nx - 0.45).powi(2) + (ny - 0.7).powi(2)).sqrt() - 0.28).abs() < 0.09
                            && nx >= 0.25;
                    is_letter = in_stem || in_top_loop || in_bot_loop;
                }
            }

            let idx = ((y * width + x) * 4) as usize;
            if is_ring || is_letter {
                let spray = 0.85 + 0.15 * hash(x as f32, y as f32, 0.0);
                out[idx] = (225.0 * spray).floor() as u8;
                out[idx + 1] = (35.0 * spray).floor() as u8;
                out[idx + 2] = (30.0 * spray).floor() as u8;
                out[idx + 3] = 255;
            } else {
                out[idx] = 0;
                out[idx + 1] = 0;
                out[idx + 2] = 0;
                out[idx + 3] = 0;
            }
        }
    }
    out
}

// ---------------------------------------------------------------------------
// GLB map surfaces — a port of the "GLB map surfaces" block in
// `packages/core/src/modules/hassault/textures3d.ts`.
//
// The modelled maps carry an authored base colour per material and nothing
// else, so the tiles are *detail*: `normalize_detail_tile` turns each into grey
// luminance with a linear mean of `detailMean`, the shader multiplies it by the
// vertex colour times 1.5 (`detailGain`), and a surface averages out to the
// colour the mapper chose.
// ---------------------------------------------------------------------------

/// fBm that tiles: every octave's lattice period divides the tile exactly.
fn fbm_tiled(u: f32, v: f32, cells: f32, octaves: usize) -> f32 {
    let mut val = 0.0;
    let mut amp = 0.5;
    let mut freq = cells;
    let mut max = 0.0;
    for _ in 0..octaves {
        val += noise2d(u * freq, v * freq, freq) * amp;
        max += amp;
        amp *= 0.5;
        freq *= 2.0;
    }
    val / max
}

fn wrap(n: f32, period: f32) -> f32 {
    ((n % period) + period) % period
}

fn write_grey(out: &mut [u8], idx: usize, val: f32) {
    let g = val.floor().clamp(0.0, 255.0) as u8;
    out[idx] = g;
    out[idx + 1] = g;
    out[idx + 2] = g;
    out[idx + 3] = 255;
}

/// Coursed ashlar: four courses of two blocks in running bond, per-block tone,
/// recessed mortar and worn arrises. Sandstone, limestone, paving, curbs.
pub fn draw_masonry_tile(width: u32, height: u32) -> Vec<u8> {
    let mut out = vec![0u8; (width * height * 4) as usize];
    let (rows, cols, mortar) = (4.0f32, 2.0f32, 0.012f32);
    for y in 0..height {
        for x in 0..width {
            let u = x as f32 / width as f32;
            let v = y as f32 / height as f32;
            let row = (v * rows).floor();
            let fv = v * rows - row;
            let cu = u * cols + (row % 2.0) * 0.5;
            let col = cu.floor();
            let fu = cu - col;
            let d = (fu.min(1.0 - fu) / cols).min(fv.min(1.0 - fv) / rows);
            let grain =
                (fbm_tiled(u, v, 32.0, 3) - 0.5) * 22.0 + (fbm_tiled(u, v, 4.0, 3) - 0.5) * 16.0;
            let mut val = 205.0 + (hash(wrap(col, cols), row, 3.0) - 0.5) * 26.0 + grain;
            if d < mortar {
                val = 150.0 + grain * 0.5;
            } else if d < mortar * 2.5 {
                val *= 0.88 + 0.12 * ((d - mortar) / (mortar * 1.5));
            }
            write_grey(&mut out, ((y * width + x) * 4) as usize, val);
        }
    }
    out
}

/// Trowelled stucco / plaster / drywall: soft cloud and fine grit. No cracks — a
/// contour of low-frequency noise reads as a topographic map line at wall scale.
pub fn draw_plaster_tile(width: u32, height: u32) -> Vec<u8> {
    let mut out = vec![0u8; (width * height * 4) as usize];
    for y in 0..height {
        for x in 0..width {
            let u = x as f32 / width as f32;
            let v = y as f32 / height as f32;
            let val = 210.0
                + (fbm_tiled(u, v, 3.0, 4) - 0.5) * 26.0
                + (fbm_tiled(u, v, 48.0, 3) - 0.5) * 14.0;
            write_grey(&mut out, ((y * width + x) * 4) as usize, val);
        }
    }
    out
}

/// Cobbles: a jittered 6×6 Voronoi of domed stones with dark sand joints.
pub fn draw_cobblestone_tile(width: u32, height: u32) -> Vec<u8> {
    let mut out = vec![0u8; (width * height * 4) as usize];
    let n = 6.0f32;
    for y in 0..height {
        for x in 0..width {
            let u = x as f32 / width as f32;
            let v = y as f32 / height as f32;
            let (px, py) = (u * n, v * n);
            let (ix, iy) = (px.floor(), py.floor());
            let (mut d1, mut d2) = (9.0f32, 9.0f32);
            let (mut id_x, mut id_y) = (0.0f32, 0.0f32);
            for oy in -1..=1 {
                for ox in -1..=1 {
                    let (ox, oy) = (ox as f32, oy as f32);
                    let wx = wrap(ix + ox, n);
                    let wy = wrap(iy + oy, n);
                    let fx = ix + ox + 0.15 + 0.7 * hash(wx, wy, 1.0);
                    let fy = iy + oy + 0.15 + 0.7 * hash(wx, wy, 2.0);
                    let d = (px - fx).hypot(py - fy);
                    if d < d1 {
                        d2 = d1;
                        d1 = d;
                        id_x = wx;
                        id_y = wy;
                    } else if d < d2 {
                        d2 = d;
                    }
                }
            }
            let gap = d2 - d1;
            let grit = (fbm_tiled(u, v, 48.0, 3) - 0.5) * 18.0;
            let dome = 1.0 - (d1 / 0.75).min(1.0) * 0.25;
            let mut val = (200.0 + (hash(id_x, id_y, 5.0) - 0.5) * 30.0) * dome + grit;
            if gap < 0.08 {
                val = 100.0 + (gap / 0.08) * 60.0 + grit;
            }
            write_grey(&mut out, ((y * width + x) * 4) as usize, val);
        }
    }
    out
}

/// Barrel roof tiles: six staggered rows, each tile a half-cylinder with a shadowed lip.
pub fn draw_roof_tile_tile(width: u32, height: u32) -> Vec<u8> {
    let mut out = vec![0u8; (width * height * 4) as usize];
    let (rows, cols) = (6.0f32, 5.0f32);
    for y in 0..height {
        for x in 0..width {
            let u = x as f32 / width as f32;
            let v = y as f32 / height as f32;
            let row = (v * rows).floor();
            let fv = v * rows - row;
            let cu = u * cols + (row % 2.0) * 0.5;
            let col = cu.floor();
            let fu = cu - col;
            let mut shade = 0.72 + 0.33 * (std::f32::consts::PI * fu).sin();
            if fv > 0.86 {
                shade *= 0.55 + ((1.0 - fv) / 0.14) * 0.3;
            }
            let tone = (hash(wrap(col, cols), row, 7.0) - 0.5) * 20.0;
            let val = (200.0 + tone) * shade + (fbm_tiled(u, v, 40.0, 3) - 0.5) * 14.0;
            write_grey(&mut out, ((y * width + x) * 4) as usize, val);
        }
    }
    out
}

/// Loop-pile carpet tiles (2×2 per texture): per-fibre speckle and a faint seam.
pub fn draw_carpet_tile(width: u32, height: u32) -> Vec<u8> {
    let mut out = vec![0u8; (width * height * 4) as usize];
    for y in 0..height {
        for x in 0..width {
            let u = x as f32 / width as f32;
            let v = y as f32 / height as f32;
            let mut val = 200.0
                + (hash(x as f32, y as f32, 9.0) - 0.5) * 26.0
                + (fbm_tiled(u, v, 8.0, 3) - 0.5) * 14.0;
            let tu = (u * 2.0) % 1.0;
            let tv = (v * 2.0) % 1.0;
            if tu.min(1.0 - tu).min(tv.min(1.0 - tv)) < 0.006 {
                val *= 0.86;
            }
            write_grey(&mut out, ((y * width + x) * 4) as usize, val);
        }
    }
    out
}

/// Stretcher-bond brick: eight courses of four, per-brick tone, lighter mortar.
pub fn draw_brick_tile(width: u32, height: u32) -> Vec<u8> {
    let mut out = vec![0u8; (width * height * 4) as usize];
    let (rows, cols, mortar) = (8.0f32, 4.0f32, 0.01f32);
    for y in 0..height {
        for x in 0..width {
            let u = x as f32 / width as f32;
            let v = y as f32 / height as f32;
            let row = (v * rows).floor();
            let fv = v * rows - row;
            let cu = u * cols + (row % 2.0) * 0.5;
            let col = cu.floor();
            let fu = cu - col;
            let d = (fu.min(1.0 - fu) / cols).min(fv.min(1.0 - fv) / rows);
            let val = if d < mortar {
                230.0 + (fbm_tiled(u, v, 64.0, 2) - 0.5) * 16.0
            } else {
                185.0
                    + (hash(wrap(col, cols), row, 11.0) - 0.5) * 40.0
                    + (fbm_tiled(u, v, 64.0, 3) - 0.5) * 20.0
            };
            write_grey(&mut out, ((y * width + x) * 4) as usize, val);
        }
    }
    out
}

fn srgb_to_linear(c: f32) -> f32 {
    if c <= 0.04045 {
        c / 12.92
    } else {
        ((c + 0.055) / 1.055).powf(2.4)
    }
}

fn linear_to_srgb(c: f32) -> f32 {
    if c <= 0.0031308 {
        c * 12.92
    } else {
        1.055 * c.powf(1.0 / 2.4) - 0.055
    }
}

/// Turn a tile into pure detail: its luminance, rescaled so the linear mean is
/// `mean`. Colour is discarded on purpose — the GLB material already says what
/// colour the surface is, and a brown wood tile times a mapper's cedar brown is
/// darker and muddier than either meant.
pub fn normalize_detail_tile(data: &[u8], mean: f32) -> Vec<u8> {
    let count = data.len() / 4;
    let luma: Vec<f32> = (0..count)
        .map(|i| {
            let r = srgb_to_linear(data[i * 4] as f32 / 255.0);
            let g = srgb_to_linear(data[i * 4 + 1] as f32 / 255.0);
            let b = srgb_to_linear(data[i * 4 + 2] as f32 / 255.0);
            0.2126 * r + 0.7152 * g + 0.0722 * b
        })
        .collect();
    let sum: f64 = luma.iter().map(|&l| l as f64).sum();
    let scale = if sum > 0.0 {
        (mean as f64 * count as f64 / sum) as f32
    } else {
        0.0
    };
    // A high-contrast tile (hazard's black stripes) scaled to the mean would push
    // its highlights past 1 and clip, dragging the mean back down. Compress the
    // contrast about the mean instead, which keeps the mean exact.
    let peak = luma.iter().fold(0.0f32, |m, &l| m.max(l * scale));
    let squeeze = if peak > 1.0 {
        (1.0 - mean) / (peak - mean)
    } else {
        1.0
    };
    let mut out = vec![0u8; data.len()];
    for (i, l) in luma.iter().enumerate() {
        let l = mean + (l * scale - mean) * squeeze;
        let g = (linear_to_srgb(l.clamp(0.0, 1.0)) * 255.0).round() as u8;
        out[i * 4] = g;
        out[i * 4 + 1] = g;
        out[i * 4 + 2] = g;
        out[i * 4 + 3] = 255;
    }
    out
}

/// The rules both clients classify GLB materials by.
const GLB_SURFACES_JSON: &str =
    include_str!("../../../packages/core/src/modules/hassault/glb-surfaces.json");

struct SurfaceTable {
    default: MaterialKind,
    rules: Vec<(MaterialKind, Vec<String>)>,
    tile_scale: std::collections::HashMap<MaterialKind, f32>,
    detail_mean: f32,
}

fn surface_table() -> &'static SurfaceTable {
    static TABLE: std::sync::OnceLock<SurfaceTable> = std::sync::OnceLock::new();
    TABLE.get_or_init(|| {
        let json: serde_json::Value =
            serde_json::from_str(GLB_SURFACES_JSON).expect("glb-surfaces.json should parse");
        let kind = |v: &serde_json::Value| {
            let name = v.as_str().expect("surface kind should be a string");
            MaterialKind::from_key(name)
                .unwrap_or_else(|| panic!("glb-surfaces.json names unknown kind {name}"))
        };
        let rules = json["rules"]
            .as_array()
            .expect("rules")
            .iter()
            .map(|r| {
                let m = r["match"]
                    .as_array()
                    .expect("match")
                    .iter()
                    .map(|s| s.as_str().expect("match entry").to_string())
                    .collect();
                (kind(&r["kind"]), m)
            })
            .collect();
        let tile_scale = json["tileScale"]
            .as_object()
            .expect("tileScale")
            .iter()
            .map(|(k, v)| {
                (
                    MaterialKind::from_key(k)
                        .unwrap_or_else(|| panic!("tileScale names unknown kind {k}")),
                    v.as_f64().expect("tile scale") as f32,
                )
            })
            .collect();
        SurfaceTable {
            default: kind(&json["default"]),
            rules,
            tile_scale,
            detail_mean: json["detailMean"].as_f64().expect("detailMean") as f32,
        }
    })
}

/// Linear mean every multiplied layer is normalised to (`detailMean`).
pub fn detail_mean() -> f32 {
    surface_table().detail_mean
}

/// Tactical PBR surface material types matching the Three.js material library.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
#[repr(u8)]
pub enum MaterialKind {
    None = 0,
    Asphalt = 1,
    Marble = 2,
    Concrete = 3,
    VaultSteel = 4,
    Hazard = 5,
    Wood = 6,
    Crate = 7,
    Container = 8,
    Glass = 9,
    Gold = 10,
    SiteA = 11,
    SiteB = 12,
    Masonry = 13,
    Plaster = 14,
    Cobblestone = 15,
    RoofTile = 16,
    Carpet = 17,
    Brick = 18,
}

impl MaterialKind {
    /// The kind's key in `glb-surfaces.json` (the browser's `SurfaceKind`).
    pub fn from_key(key: &str) -> Option<Self> {
        Some(match key {
            "none" => MaterialKind::None,
            "asphalt" => MaterialKind::Asphalt,
            "marble" => MaterialKind::Marble,
            "concrete" => MaterialKind::Concrete,
            "vault_steel" => MaterialKind::VaultSteel,
            "hazard" => MaterialKind::Hazard,
            "wood" => MaterialKind::Wood,
            "crate" => MaterialKind::Crate,
            "container" => MaterialKind::Container,
            "glass" => MaterialKind::Glass,
            "gold" => MaterialKind::Gold,
            "site_a" => MaterialKind::SiteA,
            "site_b" => MaterialKind::SiteB,
            "masonry" => MaterialKind::Masonry,
            "plaster" => MaterialKind::Plaster,
            "cobblestone" => MaterialKind::Cobblestone,
            "roof_tile" => MaterialKind::RoofTile,
            "carpet" => MaterialKind::Carpet,
            "brick" => MaterialKind::Brick,
            _ => return None,
        })
    }

    pub fn layer_index(&self) -> u32 {
        match self {
            MaterialKind::None => 0,
            other => *other as u32 - 1,
        }
    }

    pub fn roughness(&self) -> f32 {
        match self {
            MaterialKind::None => 0.8,
            MaterialKind::Asphalt => 0.88,
            MaterialKind::Marble => 0.16,
            MaterialKind::Concrete => 0.75,
            MaterialKind::VaultSteel => 0.32,
            MaterialKind::Hazard => 0.45,
            MaterialKind::Wood => 0.32,
            MaterialKind::Crate => 0.65,
            MaterialKind::Container => 0.58,
            MaterialKind::Glass => 0.06,
            MaterialKind::Gold => 0.20,
            MaterialKind::SiteA | MaterialKind::SiteB => 0.70,
            MaterialKind::Masonry => 0.85,
            MaterialKind::Plaster => 0.90,
            MaterialKind::Cobblestone => 0.80,
            MaterialKind::RoofTile => 0.70,
            MaterialKind::Carpet => 0.95,
            MaterialKind::Brick => 0.85,
        }
    }

    pub fn metalness(&self) -> f32 {
        match self {
            MaterialKind::None => 0.0,
            MaterialKind::Asphalt => 0.05,
            MaterialKind::Marble => 0.06,
            MaterialKind::Concrete => 0.08,
            MaterialKind::VaultSteel => 0.88,
            MaterialKind::Hazard => 0.12,
            MaterialKind::Wood => 0.04,
            MaterialKind::Crate => 0.20,
            MaterialKind::Container => 0.45,
            MaterialKind::Glass => 0.12,
            MaterialKind::Gold => 0.95,
            MaterialKind::SiteA | MaterialKind::SiteB => 0.0,
            MaterialKind::Masonry
            | MaterialKind::Plaster
            | MaterialKind::Cobblestone
            | MaterialKind::RoofTile
            | MaterialKind::Brick => 0.02,
            MaterialKind::Carpet => 0.0,
        }
    }

    /// World units per repeat of the kind's tile (`tileScale`).
    pub fn tile_scale(&self) -> f32 {
        surface_table().tile_scale.get(self).copied().unwrap_or(4.0)
    }

    /// First `glb-surfaces.json` rule whose substring is in the lowercased name.
    pub fn from_name(name: &str) -> Self {
        let lower = name.to_ascii_lowercase();
        let table = surface_table();
        table
            .rules
            .iter()
            .find(|(_, matches)| matches.iter().any(|m| lower.contains(m.as_str())))
            .map(|(kind, _)| *kind)
            .unwrap_or(table.default)
    }
}

/// Every layer of the texture array, in `MaterialKind::layer_index` order.
///
/// The layers the shader *multiplies* the albedo by are normalised to grey detail
/// (`normalize_detail_tile`): every map on this client is a GLB whose materials
/// carry their own colour, and the tile's job is the joints and grain, not a
/// second opinion about the hue. Glass (8) and the site decals (10, 11) are
/// blended by their own colour and alpha instead, so they stay as drawn.
pub fn texture_layers(size: u32) -> Vec<Vec<u8>> {
    let mean = detail_mean();
    let detail = |tile: Vec<u8>| normalize_detail_tile(&tile, mean);
    vec![
        detail(draw_asphalt_tile(size, size)),
        detail(draw_marble_tile(size, size)),
        detail(draw_concrete_tile(size, size)),
        detail(draw_vault_steel_tile(size, size)),
        detail(draw_hazard_tile(size, size)),
        detail(draw_wood_tile(size, size)),
        detail(draw_tactical_crate_tile(size, size)),
        detail(draw_container_tile(size, size)),
        draw_glass_tile(size, size),
        detail(draw_gold_tile(size, size)),
        draw_site_decal_tile('A', size, size),
        draw_site_decal_tile('B', size, size),
        detail(draw_masonry_tile(size, size)),
        detail(draw_plaster_tile(size, size)),
        detail(draw_cobblestone_tile(size, size)),
        detail(draw_roof_tile_tile(size, size)),
        detail(draw_carpet_tile(size, size)),
        detail(draw_brick_tile(size, size)),
    ]
}

/// Helper to construct the complete 2D Texture Array for wgpu.
pub fn build_pbr_texture_array(
    device: &wgpu::Device,
    queue: &wgpu::Queue,
) -> (wgpu::Texture, wgpu::TextureView, wgpu::Sampler) {
    let texture = device.create_texture(&wgpu::TextureDescriptor {
        label: Some("pbr_texture_array"),
        size: wgpu::Extent3d {
            width: TEXTURE_SIZE,
            height: TEXTURE_SIZE,
            depth_or_array_layers: LAYER_COUNT,
        },
        mip_level_count: 1,
        sample_count: 1,
        dimension: wgpu::TextureDimension::D2,
        format: wgpu::TextureFormat::Rgba8UnormSrgb,
        usage: wgpu::TextureUsages::TEXTURE_BINDING | wgpu::TextureUsages::COPY_DST,
        view_formats: &[],
    });

    let layers = texture_layers(TEXTURE_SIZE);

    for (layer_idx, data) in layers.iter().enumerate() {
        queue.write_texture(
            wgpu::TexelCopyTextureInfo {
                texture: &texture,
                mip_level: 0,
                origin: wgpu::Origin3d {
                    x: 0,
                    y: 0,
                    z: layer_idx as u32,
                },
                aspect: wgpu::TextureAspect::All,
            },
            data,
            wgpu::TexelCopyBufferLayout {
                offset: 0,
                bytes_per_row: Some(TEXTURE_SIZE * 4),
                rows_per_image: Some(TEXTURE_SIZE),
            },
            wgpu::Extent3d {
                width: TEXTURE_SIZE,
                height: TEXTURE_SIZE,
                depth_or_array_layers: 1,
            },
        );
    }

    let view = texture.create_view(&wgpu::TextureViewDescriptor {
        label: Some("pbr_texture_array_view"),
        dimension: Some(wgpu::TextureViewDimension::D2Array),
        ..Default::default()
    });

    let sampler = device.create_sampler(&wgpu::SamplerDescriptor {
        label: Some("pbr_texture_array_sampler"),
        address_mode_u: wgpu::AddressMode::Repeat,
        address_mode_v: wgpu::AddressMode::Repeat,
        address_mode_w: wgpu::AddressMode::ClampToEdge,
        mag_filter: wgpu::FilterMode::Linear,
        min_filter: wgpu::FilterMode::Linear,
        mipmap_filter: wgpu::MipmapFilterMode::Nearest,
        ..Default::default()
    });

    (texture, view, sampler)
}

/// Generates a 128x64 prefiltered equirectangular sky-to-ground environment map,
/// matching Three.js `createPropEnvironment`.
pub fn build_environment_map(
    device: &wgpu::Device,
    queue: &wgpu::Queue,
) -> (wgpu::Texture, wgpu::TextureView, wgpu::Sampler) {
    const W: u32 = 128;
    const H: u32 = 64;
    let mut data = vec![0u8; (W * H * 4) as usize];
    let sky = [0xbf as f32, 0xd4 as f32, 0xff as f32];
    let ground = [0x33 as f32, 0x30 as f32, 0x2c as f32];

    for y in 0..H {
        let t = y as f32 / (H - 1) as f32;
        let r = (sky[0] + (ground[0] - sky[0]) * t).round() as u8;
        let g = (sky[1] + (ground[1] - sky[1]) * t).round() as u8;
        let b = (sky[2] + (ground[2] - sky[2]) * t).round() as u8;
        for x in 0..W {
            let i = ((y * W + x) * 4) as usize;
            data[i] = r;
            data[i + 1] = g;
            data[i + 2] = b;
            data[i + 3] = 255;
        }
    }

    let texture = device.create_texture(&wgpu::TextureDescriptor {
        label: Some("env_map_texture"),
        size: wgpu::Extent3d {
            width: W,
            height: H,
            depth_or_array_layers: 1,
        },
        mip_level_count: 1,
        sample_count: 1,
        dimension: wgpu::TextureDimension::D2,
        format: wgpu::TextureFormat::Rgba8UnormSrgb,
        usage: wgpu::TextureUsages::TEXTURE_BINDING | wgpu::TextureUsages::COPY_DST,
        view_formats: &[],
    });

    queue.write_texture(
        wgpu::TexelCopyTextureInfo {
            texture: &texture,
            mip_level: 0,
            origin: wgpu::Origin3d::ZERO,
            aspect: wgpu::TextureAspect::All,
        },
        &data,
        wgpu::TexelCopyBufferLayout {
            offset: 0,
            bytes_per_row: Some(W * 4),
            rows_per_image: Some(H),
        },
        wgpu::Extent3d {
            width: W,
            height: H,
            depth_or_array_layers: 1,
        },
    );

    let view = texture.create_view(&wgpu::TextureViewDescriptor {
        label: Some("env_map_view"),
        ..Default::default()
    });

    let sampler = device.create_sampler(&wgpu::SamplerDescriptor {
        label: Some("env_map_sampler"),
        address_mode_u: wgpu::AddressMode::Repeat,
        address_mode_v: wgpu::AddressMode::ClampToEdge,
        address_mode_w: wgpu::AddressMode::ClampToEdge,
        mag_filter: wgpu::FilterMode::Linear,
        min_filter: wgpu::FilterMode::Linear,
        ..Default::default()
    });

    (texture, view, sampler)
}

pub struct PbrResources {
    pub layout: wgpu::BindGroupLayout,
    pub bind_group: wgpu::BindGroup,
    pub _pbr_texture: wgpu::Texture,
    pub _env_texture: wgpu::Texture,
}

impl PbrResources {
    pub fn new(device: &wgpu::Device, queue: &wgpu::Queue) -> Self {
        let layout = bind_group_layout(device);
        let (bind_group, pbr_texture, env_texture) = bind_group(device, queue, &layout);
        Self {
            layout,
            bind_group,
            _pbr_texture: pbr_texture,
            _env_texture: env_texture,
        }
    }
}

pub fn bind_group_layout(device: &wgpu::Device) -> wgpu::BindGroupLayout {
    device.create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
        label: Some("pbr-layout"),
        entries: &[
            wgpu::BindGroupLayoutEntry {
                binding: 0,
                visibility: wgpu::ShaderStages::FRAGMENT,
                ty: wgpu::BindingType::Texture {
                    sample_type: wgpu::TextureSampleType::Float { filterable: true },
                    view_dimension: wgpu::TextureViewDimension::D2Array,
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
            wgpu::BindGroupLayoutEntry {
                binding: 2,
                visibility: wgpu::ShaderStages::FRAGMENT,
                ty: wgpu::BindingType::Texture {
                    sample_type: wgpu::TextureSampleType::Float { filterable: true },
                    view_dimension: wgpu::TextureViewDimension::D2,
                    multisampled: false,
                },
                count: None,
            },
            wgpu::BindGroupLayoutEntry {
                binding: 3,
                visibility: wgpu::ShaderStages::FRAGMENT,
                ty: wgpu::BindingType::Sampler(wgpu::SamplerBindingType::Filtering),
                count: None,
            },
        ],
    })
}

pub fn bind_group(
    device: &wgpu::Device,
    queue: &wgpu::Queue,
    layout: &wgpu::BindGroupLayout,
) -> (wgpu::BindGroup, wgpu::Texture, wgpu::Texture) {
    let (pbr_tex, pbr_view, pbr_sampler) = build_pbr_texture_array(device, queue);
    let (env_tex, env_view, env_sampler) = build_environment_map(device, queue);

    let bg = device.create_bind_group(&wgpu::BindGroupDescriptor {
        label: Some("pbr-bind-group"),
        layout,
        entries: &[
            wgpu::BindGroupEntry {
                binding: 0,
                resource: wgpu::BindingResource::TextureView(&pbr_view),
            },
            wgpu::BindGroupEntry {
                binding: 1,
                resource: wgpu::BindingResource::Sampler(&pbr_sampler),
            },
            wgpu::BindGroupEntry {
                binding: 2,
                resource: wgpu::BindingResource::TextureView(&env_view),
            },
            wgpu::BindGroupEntry {
                binding: 3,
                resource: wgpu::BindingResource::Sampler(&env_sampler),
            },
        ],
    });

    (bg, pbr_tex, env_tex)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_all_procedural_textures_generate_valid_pixels() {
        let size = 64;
        let textures = [
            ("asphalt", draw_asphalt_tile(size, size)),
            ("marble", draw_marble_tile(size, size)),
            ("concrete", draw_concrete_tile(size, size)),
            ("vault_steel", draw_vault_steel_tile(size, size)),
            ("hazard", draw_hazard_tile(size, size)),
            ("wood", draw_wood_tile(size, size)),
            ("crate", draw_tactical_crate_tile(size, size)),
            ("container", draw_container_tile(size, size)),
            ("glass", draw_glass_tile(size, size)),
            ("gold", draw_gold_tile(size, size)),
            ("site_a", draw_site_decal_tile('A', size, size)),
            ("site_b", draw_site_decal_tile('B', size, size)),
        ];

        for (name, buf) in textures {
            assert_eq!(buf.len(), (size * size * 4) as usize, "{name} buffer size");
            let non_zero = buf.iter().any(|&b| b > 0);
            assert!(non_zero, "{name} must contain non-zero pixel data");
        }
    }

    const GLB_VECTORS: &str = include_str!(
        "../../../packages/core/src/modules/hassault/__tests__/glb-surface-vectors.json"
    );

    fn glb_vectors() -> serde_json::Value {
        serde_json::from_str(GLB_VECTORS).expect("glb-surface-vectors.json should parse")
    }

    #[test]
    fn glb_materials_classify_as_the_browser_does() {
        let v = glb_vectors();
        let cases = v["classify"].as_array().expect("classify");
        assert!(
            cases.len() > 100,
            "the fixture should carry every bundled map material"
        );
        for case in cases {
            let name = case["name"].as_str().unwrap();
            let want = MaterialKind::from_key(case["kind"].as_str().unwrap()).unwrap();
            assert_eq!(MaterialKind::from_name(name), want, "{name}");
        }
    }

    #[test]
    fn glb_planar_uvs_match_the_browser() {
        for case in glb_vectors()["uvs"].as_array().expect("uvs") {
            let f = |k: &str| -> Vec<f32> {
                case[k]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|x| x.as_f64().unwrap() as f32)
                    .collect()
            };
            let (p, n, uv) = (f("p"), f("n"), f("uv"));
            let got = crate::world3d::glb_planar_uv(
                [p[0], p[1], p[2]],
                [n[0], n[1], n[2]],
                case["scale"].as_f64().unwrap() as f32,
            );
            assert!(
                (got[0] - uv[0]).abs() < 1e-5 && (got[1] - uv[1]).abs() < 1e-5,
                "{case}"
            );
        }
    }

    fn linear_mean(tile: &[u8]) -> f32 {
        let n = tile.len() / 4;
        (0..n)
            .map(|i| srgb_to_linear(tile[i * 4] as f32 / 255.0))
            .sum::<f32>()
            / n as f32
    }

    #[test]
    fn multiplied_layers_are_grey_detail_at_the_shared_mean() {
        let layers = texture_layers(64);
        assert_eq!(layers.len(), LAYER_COUNT as usize);
        for (i, layer) in layers.iter().enumerate() {
            // Glass and the site decals are blended by their own colour, not multiplied.
            if matches!(i, 8 | 10 | 11) {
                continue;
            }
            let mean = linear_mean(layer);
            assert!((mean - detail_mean()).abs() < 0.03, "layer {i} mean {mean}");
            assert!(
                layer.chunks(4).all(|p| p[0] == p[1] && p[1] == p[2]),
                "layer {i} should be grey"
            );
        }
    }

    #[test]
    fn every_kind_has_a_layer_and_a_tile_scale() {
        for key in [
            "asphalt",
            "marble",
            "concrete",
            "vault_steel",
            "hazard",
            "wood",
            "crate",
            "container",
            "glass",
            "gold",
            "site_a",
            "site_b",
            "masonry",
            "plaster",
            "cobblestone",
            "roof_tile",
            "carpet",
            "brick",
        ] {
            let kind = MaterialKind::from_key(key).unwrap();
            assert!(kind.layer_index() < LAYER_COUNT, "{key}");
            assert!(kind.tile_scale() > 0.0, "{key}");
        }
        assert_eq!(MaterialKind::Brick.tile_scale(), 2.0);
    }

    #[test]
    fn test_material_kind_properties() {
        assert_eq!(MaterialKind::Asphalt.roughness(), 0.88);
        assert_eq!(MaterialKind::VaultSteel.metalness(), 0.88);
        assert_eq!(
            MaterialKind::from_name("street_ground"),
            MaterialKind::Asphalt
        );
        assert_eq!(
            MaterialKind::from_name("warehouse_steel"),
            MaterialKind::VaultSteel
        );
    }
}
