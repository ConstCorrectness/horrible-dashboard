/**
 * Crossfire-Grade Procedural PBR Textures & Surfaces Engine for HorribleAssault.
 *
 * Generates high-fidelity, deterministic procedural PBR surface textures and materials
 * using pure DataTextures (compatible with both WebGL and headless Node/Vitest).
 *
 * Replaces untextured flat geometric blocks with authentic tactical shooter materials:
 * 1. Asphalt Road with road stripes and aggregate gravel.
 * 2. Polished Bank Marble Floor with elegant veining and grout lines.
 * 3. Weathered Architectural Concrete with formwork seams and tie-rod holes.
 * 4. Brushed Vault Steel with perimeter rivets and anisotropic brush grain.
 * 5. 45-Degree Industrial Hazard Warning Stripes.
 * 6. Rich Dark Mahogany Wood Planks for teller counters and executive mezzanine.
 * 7. Military Tactical Crates with metal corner reinforcements and stencils.
 * 8. Corrugated Industrial Metal for shipping containers (Junk Flea).
 * 9. Bulletproof Bank Glass (semi-transparent, specular reflection).
 * 10. Polished Gold Bullion for vault pallets.
 * 11. Tactical Bomb Site Spray Stencils ("A" & "B").
 */
import type * as THREE from 'three';

const SIZE = 256;

/** Fast deterministic pseudo-random hash */
function hash(x: number, y: number, seed = 0): number {
  const n = Math.sin(x * 127.1 + y * 311.7 + seed * 157.3) * 43758.5453123;
  return n - Math.floor(n);
}

/** 2D Smooth Value Noise */
function noise2D(x: number, y: number, period = 256): number {
  const xi = Math.floor(x);
  const yi = Math.floor(y);
  const xf = x - xi;
  const yf = y - yi;
  const u = xf * xf * (3 - 2 * xf);
  const v = yf * yf * (3 - 2 * yf);
  const w = (n: number) => ((n % period) + period) % period;

  const a = hash(w(xi), w(yi));
  const b = hash(w(xi + 1), w(yi));
  const c = hash(w(xi), w(yi + 1));
  const d = hash(w(xi + 1), w(yi + 1));

  return a + (b - a) * u + (c - a) * v + (a - b - c + d) * u * v;
}

/** Fractal Brownian Motion (fBm) multi-octave noise */
function fbm(x: number, y: number, octaves = 4): number {
  let val = 0;
  let amp = 0.5;
  let freq = 1;
  let max = 0;
  for (let i = 0; i < octaves; i++) {
    val += noise2D(x * freq, y * freq, 256 * freq) * amp;
    max += amp;
    amp *= 0.5;
    freq *= 2;
  }
  return val / max;
}

/** Helper to create a repeatable DataTexture */
function makeDataTexture(
  three: typeof THREE,
  data: Uint8Array,
  width = SIZE,
  height = SIZE,
): THREE.DataTexture {
  const tex = new three.DataTexture(data, width, height, three.RGBAFormat);
  tex.wrapS = three.RepeatWrapping;
  tex.wrapT = three.RepeatWrapping;
  tex.magFilter = three.LinearFilter;
  tex.minFilter = three.LinearMipmapLinearFilter;
  tex.generateMipmaps = true;
  tex.colorSpace = three.SRGBColorSpace;
  tex.needsUpdate = true;
  return tex;
}

/**
 * 1. Tactical Asphalt Road:
 * High-frequency aggregate gravel, bitumen tonal variation, and bright yellow/white road marking stripes.
 */
export function drawAsphaltTile(width = SIZE, height = SIZE): Uint8Array {
  const out = new Uint8Array(width * height * 4);
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const u = x / width;
      const v = y / height;
      const nGravel = fbm(u * 64, v * 64, 4);
      const nMacro = fbm(u * 6, v * 6, 3);
      const baseGray = Math.floor(45 + nMacro * 25 + (nGravel - 0.5) * 35);

      let r = baseGray;
      let g = baseGray;
      let b = baseGray + 2;

      // Road markings: center yellow dashed stripe (u in 0.46..0.54, dashed in v)
      if (u >= 0.47 && u <= 0.53 && (v >= 0.15 && v <= 0.85)) {
        const edge = Math.min(Math.abs(u - 0.47), Math.abs(0.53 - u)) / 0.03;
        const wear = 0.75 + 0.25 * nGravel;
        r = Math.floor(r * (1 - edge) + (220 * wear) * edge);
        g = Math.floor(g * (1 - edge) + (180 * wear) * edge);
        b = Math.floor(b * (1 - edge) + (25 * wear) * edge);
      }

      // Edge white border stripes (u near 0.04 or 0.96)
      if ((u >= 0.04 && u <= 0.08) || (u >= 0.92 && u <= 0.96)) {
        const wear = 0.70 + 0.30 * nGravel;
        r = Math.floor(Math.max(r, 190 * wear));
        g = Math.floor(Math.max(g, 190 * wear));
        b = Math.floor(Math.max(b, 195 * wear));
      }

      const idx = (y * width + x) * 4;
      out[idx] = Math.min(255, Math.max(0, r));
      out[idx + 1] = Math.min(255, Math.max(0, g));
      out[idx + 2] = Math.min(255, Math.max(0, b));
      out[idx + 3] = 255;
    }
  }
  return out;
}

/**
 * 2. Bank Grand Lobby Polished Marble Floor:
 * Quad tile grid with clean grout joints, organic veining, and high gloss finish.
 */
export function drawMarbleTile(width = SIZE, height = SIZE): Uint8Array {
  const out = new Uint8Array(width * height * 4);
  const tiles = 2; // 2x2 tile grid per texture
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const u = x / width;
      const v = y / height;

      // Grout line calculation
      const tu = (u * tiles) % 1;
      const tv = (v * tiles) % 1;
      const distGrout = Math.min(Math.min(tu, 1 - tu), Math.min(tv, 1 - tv));
      const isGrout = distGrout < 0.025;
      const groutFactor = Math.min(1, distGrout / 0.025);

      // Organic marble veining using turbulence noise
      const turb = fbm(u * 8, v * 8, 4) * 4;
      const vein = Math.sin(u * 12 + v * 12 + turb);
      const veinDark = Math.pow(Math.abs(vein), 4) * 0.35;

      // Base marble color: luxurious ivory-white with subtle warm gray tint
      let r = 230 - veinDark * 120;
      let g = 226 - veinDark * 115;
      let b = 220 - veinDark * 105;

      // Alternate checkerboard warmth
      const tileX = Math.floor(u * tiles);
      const tileY = Math.floor(v * tiles);
      if ((tileX + tileY) % 2 === 1) {
        r *= 0.94;
        g *= 0.94;
        b *= 0.95;
      }

      // Dark grout lines
      if (isGrout) {
        const darkGrout = 75;
        r = r * groutFactor + darkGrout * (1 - groutFactor);
        g = g * groutFactor + darkGrout * (1 - groutFactor);
        b = b * groutFactor + darkGrout * (1 - groutFactor);
      }

      const idx = (y * width + x) * 4;
      out[idx] = Math.min(255, Math.max(0, Math.floor(r)));
      out[idx + 1] = Math.min(255, Math.max(0, Math.floor(g)));
      out[idx + 2] = Math.min(255, Math.max(0, Math.floor(b)));
      out[idx + 3] = 255;
    }
  }
  return out;
}

/**
 * 3. Weathered Architectural Concrete Panels:
 * Structural modular concrete panels with formwork seams, tie-rod holes, and aggregate grit.
 */
export function drawConcreteTile(width = SIZE, height = SIZE): Uint8Array {
  const out = new Uint8Array(width * height * 4);
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const u = x / width;
      const v = y / height;

      // Panel seam lines: 2 panels horizontally and vertically
      const pu = (u * 2) % 1;
      const pv = (v * 2) % 1;
      const edgeU = Math.min(pu, 1 - pu);
      const edgeV = Math.min(pv, 1 - pv);
      const isSeam = Math.min(edgeU, edgeV) < 0.018;

      // Tie-rod indentations in panel corners
      const holeU = Math.abs(pu - 0.12) < 0.03 || Math.abs(pu - 0.88) < 0.03;
      const holeV = Math.abs(pv - 0.12) < 0.03 || Math.abs(pv - 0.88) < 0.03;
      const isHole = holeU && holeV;

      // Aggregate texture
      const nFine = fbm(u * 32, v * 32, 4);
      const nMacro = fbm(u * 4, v * 4, 3);
      let val = 145 + (nMacro - 0.5) * 30 + (nFine - 0.5) * 20;

      // Vertical rain/grime streak
      const streak = noise2D(u * 16, 0.5) * (1 - v) * 25;
      val -= streak;

      if (isSeam) val *= 0.55;
      if (isHole) val *= 0.35;

      const idx = (y * width + x) * 4;
      out[idx] = Math.min(255, Math.max(0, Math.floor(val * 0.98)));
      out[idx + 1] = Math.min(255, Math.max(0, Math.floor(val * 1.00)));
      out[idx + 2] = Math.min(255, Math.max(0, Math.floor(val * 1.02)));
      out[idx + 3] = 255;
    }
  }
  return out;
}

/**
 * 4. Brushed Vault Steel Blast Door:
 * Directional anisotropic brushed finish, beveled border, and steel rivet studs.
 */
export function drawVaultSteelTile(width = SIZE, height = SIZE): Uint8Array {
  const out = new Uint8Array(width * height * 4);
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const u = x / width;
      const v = y / height;

      // Horizontal brushed metal grain
      const grain = noise2D(u * 2, v * 128) * 28;
      let val = 120 + grain;

      // Perimeter bevel border
      const edge = Math.min(Math.min(u, 1 - u), Math.min(v, 1 - v));
      if (edge < 0.06) {
        val = val * (0.6 + (edge / 0.06) * 0.4);
      }

      // Perimeter round steel rivets
      const rivetSpacing = 0.125;
      const onRivetLine = Math.abs(edge - 0.04) < 0.015;
      const rivetX = Math.abs((u % rivetSpacing) - rivetSpacing / 2) < 0.015;
      const rivetY = Math.abs((v % rivetSpacing) - rivetSpacing / 2) < 0.015;
      if (onRivetLine && (rivetX || rivetY)) {
        val = Math.min(255, val + 65); // Bright specular rivet cap
      }

      const idx = (y * width + x) * 4;
      out[idx] = Math.min(255, Math.max(0, Math.floor(val * 0.95)));
      out[idx + 1] = Math.min(255, Math.max(0, Math.floor(val * 1.00)));
      out[idx + 2] = Math.min(255, Math.max(0, Math.floor(val * 1.08)));
      out[idx + 3] = 255;
    }
  }
  return out;
}

/**
 * 5. Industrial Hazard Warning Stripes:
 * 45-degree bold diagonal yellow/black safety stripes used on vault frames & dangerous areas.
 */
export function drawHazardTile(width = SIZE, height = SIZE): Uint8Array {
  const out = new Uint8Array(width * height * 4);
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const u = x / width;
      const v = y / height;

      // 45 degree repeating diagonal bands
      const stripe = ((u + v) * 8) % 1;
      const isYellow = stripe < 0.5;

      const nGrit = (noise2D(u * 32, v * 32) - 0.5) * 25;

      let r = 0;
      let g = 0;
      let b = 0;

      if (isYellow) {
        // Bright industrial safety yellow
        r = 245 + nGrit;
        g = 185 + nGrit;
        b = 20 + nGrit;
      } else {
        // Dark weathered charcoal
        r = 35 + nGrit;
        g = 35 + nGrit;
        b = 38 + nGrit;
      }

      const idx = (y * width + x) * 4;
      out[idx] = Math.min(255, Math.max(0, Math.floor(r)));
      out[idx + 1] = Math.min(255, Math.max(0, Math.floor(g)));
      out[idx + 2] = Math.min(255, Math.max(0, Math.floor(b)));
      out[idx + 3] = 255;
    }
  }
  return out;
}

/**
 * 6. Mahogany Wood Planks:
 * Rich polished dark mahogany wood with directional organic grain for bank teller counters.
 */
export function drawWoodTile(width = SIZE, height = SIZE): Uint8Array {
  const out = new Uint8Array(width * height * 4);
  const planks = 4;
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const u = x / width;
      const v = y / height;

      // Plank seams
      const pv = (v * planks) % 1;
      const isSeam = Math.min(pv, 1 - pv) < 0.02;

      // Wood grain along X
      const grain = Math.sin(u * 2 + noise2D(u * 4, v * 32) * 8);
      const intensity = 0.8 + 0.2 * grain;

      // Rich mahogany base color: warm reddish brown
      let r = 135 * intensity;
      let g = 65 * intensity;
      let b = 32 * intensity;

      if (isSeam) {
        r *= 0.4;
        g *= 0.4;
        b *= 0.4;
      }

      const idx = (y * width + x) * 4;
      out[idx] = Math.min(255, Math.max(0, Math.floor(r)));
      out[idx + 1] = Math.min(255, Math.max(0, Math.floor(g)));
      out[idx + 2] = Math.min(255, Math.max(0, Math.floor(b)));
      out[idx + 3] = 255;
    }
  }
  return out;
}

/**
 * 7. Military Tactical Supply Crate:
 * Olive-drab crate with steel corner angle brackets, rivets, and stenciled markings.
 */
export function drawTacticalCrateTile(width = SIZE, height = SIZE): Uint8Array {
  const out = new Uint8Array(width * height * 4);
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const u = x / width;
      const v = y / height;

      const edge = Math.min(Math.min(u, 1 - u), Math.min(v, 1 - v));
      const isSteelFrame = edge < 0.12;

      // Olive-drab crate wood base
      const nWood = fbm(u * 8, v * 8, 3);
      let r = 85 + (nWood - 0.5) * 20;
      let g = 95 + (nWood - 0.5) * 20;
      let b = 65 + (nWood - 0.5) * 15;

      if (isSteelFrame) {
        // Dark tactical steel reinforcement brackets
        r = 55;
        g = 58;
        b = 62;
        // Rivet in frame
        if (Math.abs(edge - 0.06) < 0.02 && (u < 0.2 || u > 0.8 || v < 0.2 || v > 0.8)) {
          r += 40;
          g += 40;
          b += 45;
        }
      }

      // Diagonal cross-brace in center
      const diag1 = Math.abs(u - v);
      const diag2 = Math.abs(u - (1 - v));
      if ((diag1 < 0.035 || diag2 < 0.035) && !isSteelFrame) {
        r *= 0.85;
        g *= 0.85;
        b *= 0.85;
      }

      const idx = (y * width + x) * 4;
      out[idx] = Math.min(255, Math.max(0, Math.floor(r)));
      out[idx + 1] = Math.min(255, Math.max(0, Math.floor(g)));
      out[idx + 2] = Math.min(255, Math.max(0, Math.floor(b)));
      out[idx + 3] = 255;
    }
  }
  return out;
}

/**
 * 8. Corrugated Metal Container (Junk Flea):
 * Industrial shipping container with horizontal ribbed corrugation and weathered paint.
 */
export function drawContainerTile(width = SIZE, height = SIZE): Uint8Array {
  const out = new Uint8Array(width * height * 4);
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const u = x / width;
      const v = y / height;

      // Horizontal corrugation ribs
      const rib = Math.sin(v * Math.PI * 16);
      const shade = 0.75 + 0.25 * rib;

      // Industrial navy blue or dark crimson container paint
      const nRust = fbm(u * 12, v * 12, 3);
      let r = 40 * shade;
      let g = 75 * shade;
      let b = 120 * shade;

      // Rust patches
      if (nRust > 0.65) {
        const rustF = (nRust - 0.65) / 0.35;
        r = r * (1 - rustF) + 140 * rustF;
        g = g * (1 - rustF) + 65 * rustF;
        b = b * (1 - rustF) + 35 * rustF;
      }

      const idx = (y * width + x) * 4;
      out[idx] = Math.min(255, Math.max(0, Math.floor(r)));
      out[idx + 1] = Math.min(255, Math.max(0, Math.floor(g)));
      out[idx + 2] = Math.min(255, Math.max(0, Math.floor(b)));
      out[idx + 3] = 255;
    }
  }
  return out;
}

/**
 * 9. Tactical Bomb Site Spray Decal ("A" or "B"):
 * Bold red/white stenciled military target tag for walls/floors.
 */
export function drawSiteDecalTile(site: 'A' | 'B', width = SIZE, height = SIZE): Uint8Array {
  const out = new Uint8Array(width * height * 4);
  const cx = width / 2;
  const cy = height / 2;
  const rOuter = width * 0.42;
  const rInner = width * 0.34;

  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const dx = x - cx;
      const dy = y - cy;
      const dist = Math.sqrt(dx * dx + dy * dy);

      // Stencil ring
      const isRing = dist <= rOuter && dist >= rInner;

      // 8x8 font representation for letter 'A' or 'B'
      const nx = (x - (cx - width * 0.25)) / (width * 0.5);
      const ny = (y - (cy - height * 0.25)) / (height * 0.5);
      let isLetter = false;

      if (nx >= 0 && nx <= 1 && ny >= 0 && ny <= 1) {
        if (site === 'A') {
          // Crossfire / CS 'A' shape
          const inLeg1 = Math.abs(ny - (1 - nx * 1.6)) < 0.12 && nx <= 0.5;
          const inLeg2 = Math.abs(ny - (1 - (1 - nx) * 1.6)) < 0.12 && nx >= 0.5;
          const inCross = Math.abs(ny - 0.55) < 0.08 && nx >= 0.25 && nx <= 0.75;
          isLetter = inLeg1 || inLeg2 || inCross;
        } else {
          // 'B' shape
          const inStem = nx <= 0.25;
          const inTopLoop = Math.abs(Math.sqrt(Math.pow(nx - 0.4, 2) + Math.pow(ny - 0.3, 2)) - 0.25) < 0.09 && nx >= 0.25;
          const inBotLoop = Math.abs(Math.sqrt(Math.pow(nx - 0.45, 2) + Math.pow(ny - 0.7, 2)) - 0.28) < 0.09 && nx >= 0.25;
          isLetter = inStem || inTopLoop || inBotLoop;
        }
      }

      const idx = (y * width + x) * 4;
      if (isRing || isLetter) {
        // Vibrant red spray paint with overspray noise
        const spray = 0.85 + 0.15 * hash(x, y);
        out[idx] = Math.floor(225 * spray);
        out[idx + 1] = Math.floor(35 * spray);
        out[idx + 2] = Math.floor(30 * spray);
        out[idx + 3] = 255;
      } else {
        // Transparent decal background
        out[idx] = 0;
        out[idx + 1] = 0;
        out[idx + 2] = 0;
        out[idx + 3] = 0;
      }
    }
  }
  return out;
}

/** Supported surface types for 3D worlds */
export type MaterialKind =
  | 'asphalt'
  | 'marble'
  | 'concrete'
  | 'vault_steel'
  | 'hazard'
  | 'wood'
  | 'crate'
  | 'container'
  | 'glass'
  | 'gold'
  | 'vehicle'
  | 'site_a'
  | 'site_b';

export interface PBRMaterialLibrary {
  materials: Record<MaterialKind, THREE.Material>;
  dispose(): void;
}

/**
 * Builds the complete Crossfire PBR Material Library.
 */
export function createPBRMaterialLibrary(three: typeof THREE): PBRMaterialLibrary {
  const asphaltTex = makeDataTexture(three, drawAsphaltTile());
  const marbleTex = makeDataTexture(three, drawMarbleTile());
  const concreteTex = makeDataTexture(three, drawConcreteTile());
  const vaultSteelTex = makeDataTexture(three, drawVaultSteelTile());
  const hazardTex = makeDataTexture(three, drawHazardTile());
  const woodTex = makeDataTexture(three, drawWoodTile());
  const crateTex = makeDataTexture(three, drawTacticalCrateTile());
  const containerTex = makeDataTexture(three, drawContainerTile());
  const siteATex = makeDataTexture(three, drawSiteDecalTile('A'));
  const siteBTex = makeDataTexture(three, drawSiteDecalTile('B'));

  const textures: THREE.Texture[] = [
    asphaltTex,
    marbleTex,
    concreteTex,
    vaultSteelTex,
    hazardTex,
    woodTex,
    crateTex,
    containerTex,
    siteATex,
    siteBTex,
  ];

  const materials: Record<MaterialKind, THREE.Material> = {
    asphalt: new three.MeshStandardMaterial({
      map: asphaltTex,
      roughness: 0.88,
      metalness: 0.05,
      name: 'mat_asphalt',
    }),
    marble: new three.MeshStandardMaterial({
      map: marbleTex,
      roughness: 0.16, // High gloss polished marble!
      metalness: 0.06,
      name: 'mat_marble',
    }),
    concrete: new three.MeshStandardMaterial({
      map: concreteTex,
      roughness: 0.75,
      metalness: 0.08,
      name: 'mat_concrete',
    }),
    vault_steel: new three.MeshStandardMaterial({
      map: vaultSteelTex,
      roughness: 0.32,
      metalness: 0.88, // Highly metallic brushed steel!
      name: 'mat_vault_steel',
    }),
    hazard: new three.MeshStandardMaterial({
      map: hazardTex,
      roughness: 0.45,
      metalness: 0.12,
      name: 'mat_hazard',
    }),
    wood: new three.MeshStandardMaterial({
      map: woodTex,
      roughness: 0.32,
      metalness: 0.04,
      name: 'mat_wood',
    }),
    crate: new three.MeshStandardMaterial({
      map: crateTex,
      roughness: 0.65,
      metalness: 0.20,
      name: 'mat_crate',
    }),
    container: new three.MeshStandardMaterial({
      map: containerTex,
      roughness: 0.58,
      metalness: 0.45,
      name: 'mat_container',
    }),
    glass: new three.MeshStandardMaterial({
      color: 0xc4e2f8,
      roughness: 0.06,
      metalness: 0.12,
      transparent: true,
      opacity: 0.38,
      depthWrite: false,
      name: 'mat_glass',
    }),
    gold: new three.MeshStandardMaterial({
      color: 0xffd700,
      roughness: 0.20,
      metalness: 0.95,
      name: 'mat_gold',
    }),
    vehicle: new three.MeshStandardMaterial({
      color: 0x182438,
      roughness: 0.30,
      metalness: 0.75,
      name: 'mat_vehicle',
    }),
    site_a: new three.MeshStandardMaterial({
      map: siteATex,
      transparent: true,
      roughness: 0.7,
      name: 'mat_site_a',
    }),
    site_b: new three.MeshStandardMaterial({
      map: siteBTex,
      transparent: true,
      roughness: 0.7,
      name: 'mat_site_b',
    }),
  };

  return {
    materials,
    dispose() {
      for (const t of textures) t.dispose();
      for (const m of Object.values(materials)) m.dispose();
    },
  };
}
