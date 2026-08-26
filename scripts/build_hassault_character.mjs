#!/usr/bin/env node
/**
 * Merge a rigged character FBX plus a directory of Mixamo animation FBX clips
 * into one GLB, for `hassault`'s avatar renderer.
 *
 * The point of the merge is that the renderer does one fetch and gets a skinned
 * mesh with every clip already bound to its skeleton, instead of N FBX downloads
 * parsed in the browser. Mixamo names its bones identically across every export
 * (`mixamorig:Hips`, ...), so a clip downloaded "without skin" is pure skeleton
 * keyframes that bind to any Mixamo-rigged mesh by bone name.
 *
 * Usage:
 *   node scripts/build_hassault_character.mjs \
 *     --character assets/horribleAssault/t-pose-male-green-swat/source/rigged.fbx \
 *     --animations assets/horribleAssault/animations \
 *     --out apps/web/public/hassault-operator.glb
 *
 * Run with no --character to get a clips-only GLB (skeleton + animations, no
 * mesh) — useful for inspecting what a set of downloads actually contains.
 *
 * Three things this script exists to get right, because each fails silently:
 *
 *  - **Units.** Mixamo exports centimetres; hassault's world is in cubes and a
 *    body is `DEFAULT_HITBOX.standingHeight` (5.2) tall. The scale is applied to
 *    the root node rather than to the position tracks, so bone-local translations
 *    inherit it and clip data stays untouched.
 *  - **Root motion.** A clip downloaded without Mixamo's "In Place" checkbox
 *    translates the hips across the floor. The server owns position here, so that
 *    translation fights it and the avatar skates away from where it is. Stripped
 *    by default; the drift each clip carried is reported so you can see which
 *    downloads had it.
 *  - **Bone agreement.** A clip whose track names do not match the mesh's skeleton
 *    does not error — it just animates nothing. Every clip is checked against the
 *    character's bone list and a mismatch is a hard failure, not a warning.
 */

import { createRequire } from 'node:module';
import { readFileSync, writeFileSync, readdirSync, existsSync, statSync, mkdirSync } from 'node:fs';
import { basename, dirname, extname, join, resolve as resolvePath } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const REPO_ROOT = resolvePath(dirname(fileURLToPath(import.meta.url)), '..');

// three is a dependency of packages/core, not of the workspace root, so it is not
// resolvable from this file's own location. Resolving it through core keeps the
// script runnable as a bare `node scripts/...` from the repo root and — more to
// the point — guarantees it converts with the exact same three the game renders
// with, rather than a second copy that could drift a version.
// (`three/package.json` is not an exported subpath, so the package root is
// derived from the main entry — build/three.cjs — instead.)
const THREE_DIR = resolvePath(
  dirname(createRequire(join(REPO_ROOT, 'packages/core/package.json')).resolve('three')),
  '..',
);
const threeUrl = (rel) => pathToFileURL(join(THREE_DIR, rel)).href;

// The canonical body height, in cubes. Mirrors DEFAULT_HITBOX.standingHeight in
// packages/core/src/modules/hassault/hitbox.ts — a mesh taller than the cylinder
// it represents is a model whose head is not where it can be shot.
const DEFAULT_TARGET_HEIGHT = 5.2;

// ---------------------------------------------------------------------------
// Arguments
// ---------------------------------------------------------------------------

function parseArgs(argv) {
  const opts = {
    character: null,
    animations: 'assets/horribleAssault/animations',
    out: 'apps/web/public/hassault-operator.glb',
    manifest: null,
    height: DEFAULT_TARGET_HEIGHT,
    rootMotion: 'strip',
    textureSize: 1024,
    textureFormat: 'webp',
    verbose: false,
    help: false,
  };
  for (let i = 2; i < argv.length; i += 1) {
    const arg = argv[i];
    const next = () => {
      const v = argv[i + 1];
      if (v === undefined) throw new Error(`${arg} needs a value`);
      i += 1;
      return v;
    };
    switch (arg) {
      case '--character':
        opts.character = next();
        break;
      case '--animations':
        opts.animations = next();
        break;
      case '--out':
        opts.out = next();
        break;
      case '--manifest':
        opts.manifest = next();
        break;
      case '--height':
        opts.height = Number(next());
        break;
      case '--root-motion':
        opts.rootMotion = next();
        break;
      case '--texture-size':
        opts.textureSize = Number(next());
        break;
      case '--texture-format':
        opts.textureFormat = next();
        break;
      case '--verbose':
      case '-v':
        opts.verbose = true;
        break;
      case '--help':
      case '-h':
        opts.help = true;
        break;
      default:
        throw new Error(`unknown argument: ${arg}`);
    }
  }
  if (!['strip', 'keep'].includes(opts.rootMotion)) {
    throw new Error(`--root-motion must be strip|keep, got ${opts.rootMotion}`);
  }
  if (!['webp', 'jpeg', 'png'].includes(opts.textureFormat)) {
    throw new Error(`--texture-format must be webp|jpeg|png, got ${opts.textureFormat}`);
  }
  return opts;
}

const HELP = `
build_hassault_character — merge a Mixamo rig + animation clips into one GLB

  --character <file>     Rigged, skinned FBX (Mixamo auto-rigger output).
                         Omit for a clips-only GLB with no mesh.
  --animations <dir>     Directory of skinless clip FBX files.
                         [assets/horribleAssault/animations]
  --out <file>           GLB to write. [apps/web/public/hassault-operator.glb]
  --manifest <file>      Also write a JSON list of clip names + durations.
  --height <cubes>       Scale the mesh to this height. [${DEFAULT_TARGET_HEIGHT}]
  --root-motion strip    Zero hip XZ translation, keeping vertical bob. [default]
  --root-motion keep     Leave translation in (only for a client that consumes it).
  --texture-size <px>    Longest edge to downscale textures to. 0 disables. [1024]
  --texture-format <f>   webp | jpeg | png. [webp]
  -v, --verbose          Per-clip track detail.
`;

// ---------------------------------------------------------------------------
// Headless shims
//
// three's FBXLoader and GLTFExporter both reach for browser image APIs. Rather
// than pulling in a full canvas implementation, these shims carry the *encoded
// bytes* straight through: an image is decoded and re-encoded once by sharp on
// the way in, and the exporter's "draw onto a canvas, then read a blob back out"
// dance just moves that buffer around. Nothing is rasterised here, so no pixel is
// resampled twice and no map is re-compressed on the way out.
// ---------------------------------------------------------------------------

/** Stands in for both OffscreenCanvas and the image drawn onto it. */
class ShimCanvas {
  constructor(width = 1, height = 1) {
    this.width = width;
    this.height = height;
    /** Encoded bytes of the image this canvas is carrying. */
    this.__bytes = null;
    this.__mime = 'image/png';
  }

  getContext() {
    return {
      drawImage: (image) => {
        this.__bytes = image?.__bytes ?? null;
        this.__mime = image?.__mime ?? 'image/png';
      },
      // The exporter uses these for the flipY and DataTexture paths. Flips are
      // already baked into the pixels by resolveFlips before the exporter runs,
      // so by the time these are called there is nothing left for them to do.
      translate: () => {},
      scale: () => {},
      putImageData: () => {
        throw new Error('DataTexture export is not supported by this headless shim');
      },
    };
  }

  async convertToBlob() {
    if (!this.__bytes) {
      throw new Error('canvas has no image bytes — an unsupported texture reached the exporter');
    }
    return new Blob([this.__bytes], { type: this.__mime });
  }

  toBlob(callback) {
    this.convertToBlob().then(callback);
  }
}

/**
 * Minimal FileReader over Node's Blob — GLTFExporter assembles the GLB through
 * one.
 *
 * The exporter calls `readAsArrayBuffer` *before* assigning `onloadend`, so the
 * callback must not fire synchronously: a shim that resolved immediately would
 * find no handler installed yet and the export would hang with no error. Going
 * through the blob's own promise puts the callback a turn later, which is the
 * ordering the real API guarantees.
 */
class ShimFileReader {
  constructor() {
    this.result = null;
    this.onloadend = null;
    this.onerror = null;
  }

  #finish(promise) {
    promise
      .then((value) => {
        this.result = value;
        this.onloadend?.();
      })
      .catch((err) => {
        if (this.onerror) this.onerror(err);
        else throw err;
      });
  }

  readAsArrayBuffer(blob) {
    this.#finish(blob.arrayBuffer());
  }

  readAsDataURL(blob) {
    this.#finish(
      blob
        .arrayBuffer()
        .then(
          (ab) =>
            `data:${blob.type || 'application/octet-stream'};base64,${Buffer.from(ab).toString('base64')}`,
        ),
    );
  }
}

const blobRegistry = new Map();
let blobSeq = 0;

function installShims() {
  globalThis.OffscreenCanvas = ShimCanvas;
  globalThis.FileReader ??= ShimFileReader;
  // FBXLoader reads `window.URL.createObjectURL` for FBX-embedded textures.
  // Keeping the bytes in a Map (rather than minting a real blob: URL) lets the
  // TextureLoader shim resolve them directly, with no fetch involved.
  const createObjectURL = (blob) => {
    const id = `hdblob:${blobSeq++}`;
    blobRegistry.set(id, blob);
    return id;
  };
  const revokeObjectURL = (id) => {
    blobRegistry.delete(id);
  };
  globalThis.window = { URL: { createObjectURL, revokeObjectURL } };
}

/**
 * Replace TextureLoader with one that reads from disk (or the blob registry),
 * downscales and re-encodes with sharp, and hands back a texture whose `image`
 * is a ShimCanvas carrying the encoded bytes.
 */
function installTextureLoader(THREE, sharp, opts, pending, report) {
  THREE.TextureLoader.prototype.load = function load(url, onLoad) {
    const texture = new THREE.Texture();
    const loaderPath = this.path ?? '';
    const task = (async () => {
      let bytes;
      let label = url;
      if (blobRegistry.has(url)) {
        bytes = Buffer.from(await blobRegistry.get(url).arrayBuffer());
        label = `<embedded ${url}>`;
      } else {
        const file = resolvePath(loaderPath, url);
        if (!existsSync(file)) {
          report.missingTextures.push(url);
          return;
        }
        bytes = readFileSync(file);
        label = basename(file);
      }

      let img = sharp(bytes, { failOn: 'none' });
      const meta = await img.metadata();
      const longest = Math.max(meta.width ?? 0, meta.height ?? 0);
      if (opts.textureSize > 0 && longest > opts.textureSize) {
        img = img.resize({ width: opts.textureSize, height: opts.textureSize, fit: 'inside' });
      }
      const mime = `image/${opts.textureFormat}`;
      let encoded;
      if (opts.textureFormat === 'webp') {
        encoded = await img.webp({ quality: 88 }).toBuffer();
      } else if (opts.textureFormat === 'jpeg') {
        encoded = await img.jpeg({ quality: 90 }).toBuffer();
      } else {
        encoded = await img.png({ compressionLevel: 9 }).toBuffer();
      }
      const out = await sharp(encoded).metadata();

      const canvas = new ShimCanvas(out.width, out.height);
      canvas.__bytes = encoded;
      canvas.__mime = mime;
      texture.image = canvas;
      texture.userData.mimeType = mime;
      texture.needsUpdate = true;
      report.textures.push({
        name: label,
        from: bytes.length,
        to: encoded.length,
        size: `${out.width}x${out.height}`,
      });
      onLoad?.(texture);
    })();
    pending.push(task);
    return texture;
  };
}

/**
 * Bake out every `flipY` before the exporter sees it.
 *
 * FBX and glTF disagree about which way up a texture's V axis runs, so
 * FBXLoader hands back textures marked `flipY = true` and GLTFExporter honours
 * that by transforming the canvas it draws onto. Our canvas is a passthrough and
 * cannot flip, so the flip happens here instead — in sharp, on the real pixels —
 * and the flag is cleared so the exporter has nothing left to do.
 *
 * A texture carrying no bytes is a hard failure rather than a skip: it would
 * come out the right way up only by accident, and an upside-down character is
 * the kind of thing that gets blamed on the UVs for an hour.
 */
async function resolveFlips(root, sharp) {
  const seen = new Set();
  const jobs = [];
  root.traverse((obj) => {
    const materials = Array.isArray(obj.material)
      ? obj.material
      : obj.material
        ? [obj.material]
        : [];
    for (const mat of materials) {
      for (const value of Object.values(mat)) {
        if (!value?.isTexture || value.flipY !== true || seen.has(value)) continue;
        seen.add(value);
        const canvas = value.image;
        if (!canvas?.__bytes) {
          throw new Error(
            `texture "${value.name || '<unnamed>'}" is marked flipY but carries no image data`,
          );
        }
        jobs.push(
          sharp(canvas.__bytes)
            .flip()
            .toBuffer()
            .then((bytes) => {
              canvas.__bytes = bytes;
              value.flipY = false;
            }),
        );
      }
    }
  });
  await Promise.all(jobs);
  return jobs.length;
}

/**
 * Fold each material's opacity map into its base colour's alpha channel, and set
 * the transparency mode from what the pixels actually contain.
 *
 * glTF has no separate alpha-map slot — alpha lives in the base colour texture —
 * so an FBX `map_d` reaches the exporter as a `alphaMap` it simply drops. That
 * loses the Fuse body mask, which is what stops bare torso geometry poking
 * through the clothing that covers it.
 *
 * The mode is measured rather than assumed, because FBXLoader marks every
 * material with an opacity map `transparent = true` and four of these seven maps
 * are solid white. Blending an opaque body costs a depth-sorted draw and buys a
 * class of see-through-yourself artefact for nothing:
 *
 *   fully opaque (min 255)      -> OPAQUE, map dropped entirely
 *   effectively binary          -> MASK with a 0.5 cutoff (the body mask)
 *   genuine partial coverage    -> BLEND (tinted eyewear lenses)
 */
async function resolveAlpha(root, sharp, textureFormat) {
  const seen = new Set();
  const decided = [];
  const materials = [];
  root.traverse((obj) => {
    for (const mat of Array.isArray(obj.material)
      ? obj.material
      : obj.material
        ? [obj.material]
        : []) {
      if (!seen.has(mat)) {
        seen.add(mat);
        materials.push(mat);
      }
    }
  });

  for (const mat of materials) {
    const alpha = mat.alphaMap;
    if (!alpha?.image?.__bytes) {
      // No opacity map at all: whatever `transparent` FBXLoader guessed, there is
      // nothing to blend, so say so explicitly rather than leaving it to default.
      mat.transparent = false;
      mat.alphaTest = 0;
      continue;
    }

    const stats = await sharp(alpha.image.__bytes).greyscale().stats();
    const { min, max } = stats.channels[0];
    mat.alphaMap = null;

    if (min === 255) {
      mat.transparent = false;
      mat.alphaTest = 0;
      decided.push({ name: mat.name, mode: 'OPAQUE' });
      continue;
    }

    // How much of the map sits in the mid-tones decides mask vs blend: a cutout
    // mask is almost all 0 or 255, a tinted lens is not.
    const hist = stats.channels[0];
    const mid = (hist.mean - min) / Math.max(1, max - min);
    const binary = mid > 0.9 || mid < 0.1;

    const base = mat.map?.image;
    if (base?.__bytes) {
      const baseMeta = await sharp(base.__bytes).metadata();
      const alphaRaw = await sharp(alpha.image.__bytes)
        .greyscale()
        .resize(baseMeta.width, baseMeta.height, { fit: 'fill' })
        .raw()
        .toBuffer();
      // jpeg cannot carry alpha, so a map that needs it is promoted to webp.
      const mime = textureFormat === 'jpeg' ? 'image/webp' : `image/${textureFormat}`;
      const merged = sharp(await sharp(base.__bytes).removeAlpha().toBuffer()).joinChannel(
        alphaRaw,
        { raw: { width: baseMeta.width, height: baseMeta.height, channels: 1 } },
      );
      base.__bytes =
        mime === 'image/png'
          ? await merged.png({ compressionLevel: 9 }).toBuffer()
          : await merged.webp({ quality: 88, alphaQuality: 100 }).toBuffer();
      base.__mime = mime;
      mat.map.userData.mimeType = mime;
    }

    if (binary) {
      mat.transparent = false;
      mat.alphaTest = 0.5;
      decided.push({ name: mat.name, mode: 'MASK' });
    } else {
      mat.transparent = true;
      mat.alphaTest = 0;
      decided.push({ name: mat.name, mode: 'BLEND' });
    }
  }
  return decided;
}

/**
 * Rebind every skinned mesh onto one shared skeleton, matched by bone name.
 *
 * FBXLoader gives each skinned mesh its own `Bone` objects even when the FBX
 * describes a single skeleton, so a Mixamo character arrives as nine meshes
 * bound to nine partial copies of the same rig — 87 bone objects for 34 distinct
 * names. An animation track names a bone, and both the exporter and the runtime
 * mixer resolve that name through `PropertyBinding.findNode`, which returns the
 * **first depth-first match**. So the clips drive one copy and eight meshes are
 * bound to copies nothing animates.
 *
 * The failure is entirely silent and looks like a modelling mistake: the shirt
 * walks while the body, head and boots stand in a T-pose. Canonicalising on the
 * first depth-first match is what makes the skins agree with the tracks, because
 * it is the same rule `findNode` applies.
 *
 * Bind poses are checked rather than assumed — a duplicate sitting at a
 * different point in the hierarchy would deform the mesh instead of animating
 * it, which is worse than the bug being fixed.
 */
function unifySkeletons(THREE, root) {
  root.updateMatrixWorld(true);

  const canonical = new Map();
  root.traverse((o) => {
    if (o.isBone && !canonical.has(o.name)) canonical.set(o.name, o);
  });

  const meshes = [];
  root.traverse((o) => {
    if (o.isSkinnedMesh) meshes.push(o);
  });

  const drift = [];
  let rebound = 0;
  for (const mesh of meshes) {
    const old = mesh.skeleton;
    const bones = old.bones.map((b) => canonical.get(b.name) ?? b);
    if (bones.every((b, i) => b === old.bones[i])) continue;

    for (let i = 0; i < bones.length; i += 1) {
      if (bones[i] === old.bones[i]) continue;
      const a = new THREE.Vector3().setFromMatrixPosition(bones[i].matrixWorld);
      const b = new THREE.Vector3().setFromMatrixPosition(old.bones[i].matrixWorld);
      const d = a.distanceTo(b);
      if (d > 1e-3) drift.push(`${old.bones[i].name} (${d.toFixed(3)} units apart)`);
    }

    mesh.bind(new THREE.Skeleton(bones, old.boneInverses), mesh.bindMatrix);
    rebound += 1;
  }

  if (drift.length) {
    throw new Error(
      'duplicate bones do not share a bind pose, so rebinding would deform the mesh:\n  ' +
        [...new Set(drift)].join('\n  '),
    );
  }
  return { rebound, meshes: meshes.length, bones: canonical.size };
}

// ---------------------------------------------------------------------------
// Clip handling
// ---------------------------------------------------------------------------

/** "Rifle Crouch Walk To Kneel.fbx" -> "rifle_crouch_walk_to_kneel" */
function clipNameFor(file) {
  return basename(file, extname(file))
    .replace(/[^A-Za-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .toLowerCase();
}

/** Bone a track addresses, e.g. "mixamorig:Hips.position" -> "mixamorig:Hips". */
function trackTarget(trackName) {
  const dot = trackName.lastIndexOf('.');
  return dot === -1 ? trackName : trackName.slice(0, dot);
}

/**
 * Zero the horizontal component of the root bone's position track.
 *
 * Vertical motion is kept: that is the body's bob and the rise of a jump, which
 * belong to the animation. Horizontal motion is the character walking across the
 * floor, which here belongs to the server. Returns the horizontal distance the
 * clip would have travelled, so the caller can report which downloads were not
 * fetched with Mixamo's "In Place" box ticked.
 */
function stripRootMotion(clip, rootBone, apply) {
  const track = clip.tracks.find((t) => t.name === `${rootBone}.position`);
  if (!track) return 0;
  const v = track.values;
  let minX = Infinity;
  let maxX = -Infinity;
  let minZ = Infinity;
  let maxZ = -Infinity;
  for (let i = 0; i < v.length; i += 3) {
    minX = Math.min(minX, v[i]);
    maxX = Math.max(maxX, v[i]);
    minZ = Math.min(minZ, v[i + 2]);
    maxZ = Math.max(maxZ, v[i + 2]);
  }
  const drift = Math.hypot(maxX - minX, maxZ - minZ);
  if (apply && v.length >= 3) {
    const baseX = v[0];
    const baseZ = v[2];
    for (let i = 0; i < v.length; i += 3) {
      v[i] = baseX;
      v[i + 2] = baseZ;
    }
  }
  return drift;
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

async function main() {
  const opts = parseArgs(process.argv);
  if (opts.help) {
    console.log(HELP);
    return;
  }

  installShims();

  const THREE = await import(threeUrl('build/three.module.js'));
  const { FBXLoader } = await import(threeUrl('examples/jsm/loaders/FBXLoader.js'));
  const { GLTFExporter } = await import(threeUrl('examples/jsm/exporters/GLTFExporter.js'));
  const sharp = (await import('sharp')).default;

  const pending = [];
  const report = { textures: [], missingTextures: [] };
  installTextureLoader(THREE, sharp, opts, pending, report);

  const loadFbx = (file) => {
    const buf = readFileSync(file);
    const ab = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
    return new FBXLoader().parse(ab, `${dirname(file)}/`);
  };

  // -- character ------------------------------------------------------------

  let root = null;
  let skeletonBones = null;

  if (opts.character) {
    const file = resolvePath(REPO_ROOT, opts.character);
    if (!existsSync(file)) {
      // Overwhelmingly this is the auto-rig step not having happened yet rather
      // than a typo, so say so: the rigged FBX is produced by hand on mixamo.com
      // and nothing in the repo can generate it.
      throw new Error(
        `character not found: ${file}\n\n` +
          'If you have not auto-rigged the mesh yet, that is the missing step — the\n' +
          'avatars in assets/horribleAssault/ are unrigged Sketchfab OBJs and no clip\n' +
          'can bind to them. On mixamo.com: Upload Character -> the OBJ (or its .zip\n' +
          'as-is) -> place the chin/wrist/elbow/knee/groin markers -> download as\n' +
          'FBX Binary, T-Pose, *with* skin, and point --character at that file.\n\n' +
          'To check what a set of clips contains meanwhile, drop --character for a\n' +
          'clips-only GLB.',
      );
    }
    if (extname(file).toLowerCase() !== '.fbx') {
      throw new Error(
        `--character must be an FBX, got ${extname(file)}.\n` +
          'An OBJ carries no skeleton and no skin weights, so no clip can bind to it —\n' +
          'run the mesh through the Mixamo auto-rigger first.',
      );
    }
    console.log(`character  ${opts.character}`);
    root = loadFbx(file);
    await Promise.all(pending.splice(0));

    const skinned = [];
    root.traverse((o) => {
      if (o.isSkinnedMesh) skinned.push(o);
    });
    if (!skinned.length) {
      throw new Error(
        `${basename(file)} contains no SkinnedMesh — it is a static mesh, not a rigged character.\n` +
          'Upload it to the Mixamo auto-rigger (mixamo.com -> Upload Character) and use the FBX it returns.',
      );
    }
    const unified = unifySkeletons(THREE, root);
    skeletonBones = new Set(skinned.flatMap((m) => m.skeleton.bones.map((b) => b.name)));
    console.log(
      `           ${unified.meshes} skinned mesh(es), ${unified.bones} bones` +
        (unified.rebound ? `, ${unified.rebound} rebound onto the shared skeleton` : ''),
    );

    const flipped = await resolveFlips(root, sharp);
    if (flipped) console.log(`           flipped ${flipped} texture(s) to glTF's V orientation`);
    const alphaModes = await resolveAlpha(root, sharp, opts.textureFormat);
    if (alphaModes.length) {
      const grouped = alphaModes.reduce((acc, d) => {
        (acc[d.mode] ??= []).push(d.name.replace(/mat$/, ''));
        return acc;
      }, {});
      for (const [mode, names] of Object.entries(grouped)) {
        console.log(`           ${mode.padEnd(6)} ${names.join(', ')}`);
      }
    }

    // Scale to the canonical body height. Applied to the root node so that bone
    // translations in every clip inherit it — scaling the tracks instead would
    // mean every future clip had to be scaled the same way to stay in agreement.
    const box = new THREE.Box3().setFromObject(root);
    const measured = box.max.y - box.min.y;
    if (measured > 0 && Number.isFinite(opts.height) && opts.height > 0) {
      const s = opts.height / measured;
      root.scale.setScalar(s);
      console.log(
        `           height ${measured.toFixed(1)} source units -> ${opts.height} cubes (x${s.toFixed(5)})`,
      );
    }
    // Drop whatever animation the character file shipped with (a bind pose).
    root.animations = [];
  } else {
    root = new THREE.Group();
    root.name = 'ClipsOnly';
    console.log('character  (none — clips-only GLB)');
  }

  // -- clips ----------------------------------------------------------------

  const animDir = resolvePath(REPO_ROOT, opts.animations);
  if (!existsSync(animDir) || !statSync(animDir).isDirectory()) {
    throw new Error(`animations directory not found: ${animDir}`);
  }
  const files = readdirSync(animDir)
    .filter((f) => f.toLowerCase().endsWith('.fbx'))
    .sort();
  if (!files.length) throw new Error(`no .fbx files in ${animDir}`);

  console.log(`\nclips      ${files.length} from ${opts.animations}\n`);

  const clips = [];
  const rows = [];
  const mismatches = [];
  let skeletonAdopted = false;

  for (const f of files) {
    const path = join(animDir, f);
    const group = loadFbx(path);
    await Promise.all(pending.splice(0));

    if (!group.animations?.length) {
      rows.push({ name: clipNameFor(f), note: 'NO ANIMATION — skipped' });
      continue;
    }
    const clip = group.animations[0];
    clip.name = clipNameFor(f);

    const targets = new Set(clip.tracks.map((t) => trackTarget(t.name)));

    // With no character, the first clip's own skeleton becomes the export's —
    // that is what makes a clips-only GLB loadable at all.
    if (!skeletonBones && !skeletonAdopted) {
      skeletonAdopted = true;
      root.add(group);
      skeletonBones = new Set();
      group.traverse((o) => {
        if (o.isBone) skeletonBones.add(o.name);
      });
    }

    const missing = [...targets].filter((t) => !skeletonBones.has(t));
    if (missing.length) mismatches.push({ clip: clip.name, missing });

    const rootBone = [...targets].find((t) => /Hips$/.test(t)) ?? [...targets][0];
    const drift = stripRootMotion(clip, rootBone, opts.rootMotion === 'strip');

    clips.push(clip);
    rows.push({
      name: clip.name,
      duration: clip.duration,
      tracks: clip.tracks.length,
      bones: targets.size,
      drift,
    });
    if (opts.verbose) {
      console.log(`  ${clip.name}: ${clip.tracks.length} tracks over ${targets.size} bones`);
    }
  }

  // A clip whose bones the mesh does not have animates nothing at all, with no
  // error at runtime. That is the failure this check exists to make loud.
  if (mismatches.length) {
    const detail = mismatches
      .map(
        (m) =>
          `  ${m.clip}: ${m.missing.slice(0, 6).join(', ')}${m.missing.length > 6 ? ` (+${m.missing.length - 6} more)` : ''}`,
      )
      .join('\n');
    throw new Error(
      `these clips address bones the character does not have:\n${detail}\n\n` +
        'Both sides must carry the same rig. A clip bound to a skeleton that lacks its\n' +
        'bones plays as a T-pose, silently.',
    );
  }

  // -- report ---------------------------------------------------------------

  const pad = (s, n) => String(s).padEnd(n);
  console.log(pad('CLIP', 34) + pad('SECONDS', 10) + pad('TRACKS', 9) + 'ROOT DRIFT');
  for (const r of rows) {
    if (r.note) {
      console.log(pad(r.name, 34) + r.note);
      continue;
    }
    const drift =
      r.drift > 1
        ? `${r.drift.toFixed(0)} units ${opts.rootMotion === 'strip' ? '(stripped)' : '(KEPT)'}`
        : '—';
    console.log(pad(r.name, 34) + pad(r.duration.toFixed(2), 10) + pad(r.tracks, 9) + drift);
  }

  if (report.textures.length) {
    const from = report.textures.reduce((a, t) => a + t.from, 0);
    const to = report.textures.reduce((a, t) => a + t.to, 0);
    console.log(
      `\ntextures   ${report.textures.length} maps, ${(from / 1e6).toFixed(1)} MB -> ${(to / 1e6).toFixed(1)} MB` +
        ` (${opts.textureFormat}, max ${opts.textureSize || 'native'}px)`,
    );
  }
  if (report.missingTextures.length) {
    console.log(
      `\n  WARNING  ${report.missingTextures.length} texture(s) referenced but not found on disk:`,
    );
    for (const t of new Set(report.missingTextures)) console.log(`           ${t}`);
    console.log(
      '           The FBX points at files beside it; keep its textures/ folder alongside.',
    );
  }

  // -- export ---------------------------------------------------------------

  const outFile = resolvePath(REPO_ROOT, opts.out);
  mkdirSync(dirname(outFile), { recursive: true });

  const exporter = new GLTFExporter();
  const glb = await exporter.parseAsync(root, {
    binary: true,
    animations: clips,
    onlyVisible: false,
    maxTextureSize: Infinity, // sharp already resized; this stops a second clamp
  });

  writeFileSync(outFile, Buffer.from(glb));
  const kb = statSync(outFile).size / 1024;
  console.log(
    `\nwrote      ${opts.out}  (${kb > 1024 ? `${(kb / 1024).toFixed(1)} MB` : `${kb.toFixed(0)} KB`}, ${clips.length} clips)`,
  );

  if (opts.manifest) {
    const manFile = resolvePath(REPO_ROOT, opts.manifest);
    mkdirSync(dirname(manFile), { recursive: true });
    const payload = {
      source: opts.out,
      generatedFrom: opts.animations,
      targetHeight: opts.height,
      rootMotion: opts.rootMotion,
      clips: rows
        .filter((r) => !r.note)
        .map((r) => ({ name: r.name, duration: Number(r.duration.toFixed(4)) })),
    };
    writeFileSync(manFile, `${JSON.stringify(payload, null, 2)}\n`);
    console.log(`           ${opts.manifest}`);
  }
}

main().catch((err) => {
  console.error(`\nbuild_hassault_character: ${err.message}\n`);
  process.exit(1);
});
