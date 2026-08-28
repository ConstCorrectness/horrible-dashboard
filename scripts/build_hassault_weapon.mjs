#!/usr/bin/env node
/**
 * Turn a downloaded weapon model into a hassault weapon prop GLB.
 *
 * Both clients draw weapons as procedural boxes today (`viewmodel.ts`,
 * `viewmodel.rs`). This produces the alternative: one GLB per weapon, scaled to
 * cube units, oriented so the barrel runs down -Z, and with its PBR maps wired
 * up — the same shape `build_hassault_character.mjs` produces for the operator.
 *
 * Usage:
 *   node scripts/build_hassault_weapon.mjs \
 *     --model assets/horribleAssault/beretta-92 \
 *     --length 0.6 --forward +z \
 *     --out apps/web/public/hassault-weapon-pistol.glb
 *
 * `--inspect` measures and reports without writing anything, which is how you
 * find out what `--forward` should be.
 *
 * ## Four things this exists to get right, because each fails silently
 *
 * **Units.** There is no shared unit across these files. Measured: the Beretta's
 * bounding box is 21.4 along its longest axis for a ~21.7 cm pistol (so, cm);
 * the Remington's is 463.9 for a ~104 cm shotgun (~0.22 cm per unit); the M4A1's
 * is 20.0 for a ~84 cm carbine (~4.2 cm per unit). Nothing in the file says
 * which. So the scale is **derived from a stated real length**, exactly as the
 * character build derives its scale from `--height`: you say how long the weapon
 * is in cubes and the box is measured to get there. A converter that assumed
 * centimetres would render two of these three at the wrong size, and a weapon
 * that is wrong by 20x is not subtly wrong — it is invisible or it fills the
 * screen.
 *
 * **Which way it points.** Also not in the file, and not derivable from the
 * bounding box: a rifle pointing backwards has exactly the same box as one
 * pointing forwards. Measured, the longest axis is `z` for the Beretta and the
 * Remington and `x` for the M4A1. So `--forward` is **required** and the script
 * refuses to guess. It does report which axis is longest, so `--inspect` tells
 * you the letter and you supply the sign.
 *
 * **The maps are not in the FBX.** Measured: every one of these materials comes
 * back from FBXLoader with no maps at all except one normal map, while the PBR
 * set sits unreferenced in a sibling `textures/` directory under a different
 * naming scheme per model (`_Base_color`/`_Metallic`, `_C`/`_M`/`_R`/`_N`,
 * `D`/`Metal`/`rough`/`N`). They are matched by name here — see `MAP_PATTERNS` —
 * and a material that ends up with no base colour is **reported**, because the
 * failure is a weapon that renders flat grey and looks like a lighting bug.
 *
 * **Phong is not PBR.** FBXLoader produces `MeshPhongMaterial`; glTF's material
 * model is metallic-roughness. Exported as-is, the maps that do not exist in
 * Phong — metalness, roughness — are simply dropped, and the result is a weapon
 * with a base colour and nothing else. Every material is converted to
 * `MeshStandardMaterial` before export.
 */

import { createRequire } from 'node:module';
import { readFileSync, writeFileSync, readdirSync, existsSync, statSync, mkdirSync } from 'node:fs';
import { basename, dirname, extname, join, resolve as resolvePath } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

import {
  ShimCanvas,
  installShims,
  installTextureLoader,
  resolveFlips,
} from './lib/three-headless.mjs';

const REPO_ROOT = resolvePath(dirname(fileURLToPath(import.meta.url)), '..');

// Resolved through packages/core for the same reason the character build does:
// it guarantees the converter runs the exact three the game renders with, rather
// than a second copy that could drift a version.
const THREE_DIR = resolvePath(
  dirname(createRequire(join(REPO_ROOT, 'packages/core/package.json')).resolve('three')),
  '..',
);
const threeUrl = (rel) => pathToFileURL(join(THREE_DIR, rel)).href;

/**
 * How a texture file name maps onto a glTF material slot.
 *
 * Ordered, and the first match wins. Every pattern is anchored to the **end** of
 * the stem, so `M4_Colour_Variant_Normal.png` is a normal map rather than a
 * colour one — an unanchored matcher takes the first word it recognises, which
 * is whichever the artist happened to put first.
 *
 * The bare single-letter forms are not redundant with the underscore ones. The
 * Beretta's maps are literally `D.png`, `N.png`, `Metal.png` and `rough.png`, so
 * requiring a separator matched two of the four and silently lost its base
 * colour and its normals — which is worse than matching none, because a
 * half-wired material comes out looking deliberate.
 */
const MAP_PATTERNS = [
  // Ambient occlusion first: it is the one slot glTF does *not* have on the
  // standard material, so it is matched only to be reported and skipped rather
  // than silently landing in another slot.
  { slot: 'aoMap', tests: [/^ao$/i, /_ao$/i, /ambientocclusion$/i, /_occlusion$/i] },
  {
    slot: 'normalMap',
    tests: [/^n$/i, /_n$/i, /normal$/i, /_nrm$/i, /_norm$/i],
  },
  {
    slot: 'roughnessMap',
    tests: [/^r$/i, /_r$/i, /rough(ness)?$/i],
  },
  {
    slot: 'metalnessMap',
    tests: [/^m$/i, /_m$/i, /metal(lic|ness)?$/i],
  },
  {
    slot: 'map',
    tests: [/^[cd]$/i, /_c$/i, /_d$/i, /base_?colou?r$/i, /albedo$/i, /diffuse$/i, /_col$/i],
  },
];

/** Which axis a `--forward` string names, and which way along it. */
function parseForward(value) {
  const match = /^([+-])?([xyz])$/i.exec((value ?? '').trim());
  if (!match) {
    throw new Error(
      `--forward must be one of +x -x +y -y +z -z, got ${JSON.stringify(value)}.\n` +
        'Run with --inspect to see which axis the model is longest along; the sign\n' +
        'is the half a bounding box cannot tell you, so it has to be stated.',
    );
  }
  return { axis: match[2].toLowerCase(), sign: match[1] === '-' ? -1 : 1 };
}

function parseArgs(argv) {
  const opts = {
    model: null,
    fbx: null,
    textures: null,
    out: null,
    length: 2.5,
    forward: null,
    grip: 'rear',
    exclude: [],
    textureSize: 1024,
    textureFormat: 'webp',
    inspect: false,
    help: false,
  };
  for (let i = 2; i < argv.length; i++) {
    const arg = argv[i];
    const next = () => argv[++i];
    switch (arg) {
      case '--model':
        opts.model = next();
        break;
      case '--fbx':
        opts.fbx = next();
        break;
      case '--textures':
        opts.textures = next();
        break;
      case '--out':
        opts.out = next();
        break;
      case '--length':
        opts.length = Number(next());
        break;
      case '--forward':
        opts.forward = next();
        break;
      case '--grip':
        opts.grip = next();
        break;
      case '--exclude':
        opts.exclude.push(next());
        break;
      case '--texture-size':
        opts.textureSize = Number(next());
        break;
      case '--texture-format':
        opts.textureFormat = next();
        break;
      case '--inspect':
        opts.inspect = true;
        break;
      case '--help':
      case '-h':
        opts.help = true;
        break;
      default:
        throw new Error(`unknown argument: ${arg}`);
    }
  }
  return opts;
}

const HELP = `
build_hassault_weapon.mjs — a downloaded weapon model into a hassault prop GLB

  --model <dir>          A Sketchfab-shaped directory: source/*.fbx + textures/
  --fbx <file>           The FBX directly, if the layout is not that
  --textures <dir>       Where the PBR maps live (default: <model>/textures)
  --out <file>           Where to write the GLB. Required unless --inspect
  --length <cubes>       How long the weapon is, longest axis (default 2.5).
                         A cube is ~36 cm, so a 90 cm rifle is about 2.5
  --forward <+-xyz>      Which way the barrel points in the source file.
                         REQUIRED: a bounding box cannot tell you the sign
  --grip <rear|centre>   Where the origin goes (default rear — roughly the grip)
  --exclude <regex>      Drop meshes whose object or material name matches.
                         Repeatable. Applied before anything is measured
  --texture-size <px>    Longest edge; 0 to leave alone (default 1024)
  --texture-format <fmt> webp | png | jpeg (default webp)
  --inspect              Measure and report, write nothing

Start with --inspect. It prints the bounding box, the longest axis, the triangle
count and which textures matched which slot, which is everything you need to
choose --length and --forward.
`;

/**
 * A trailing UDIM tile index, as Substance Painter and Mari write it.
 *
 * `1001` is the first tile and the numbering runs up from there, so a bare
 * four-digit number in that range at the end of a stem is a tile and not part of
 * the name. Anything below 1001 is left alone: `Barrel_0110` is a variant id.
 */
const UDIM_TILE = /_(1[0-9]{3}|[2-9][0-9]{3})$/;

/**
 * Every image file under a textures directory, by lowercased stem.
 *
 * The stem has any UDIM tile stripped, and that strip is the difference between
 * this model having textures and not. Every pattern in `MAP_PATTERNS` is
 * anchored to the **end** of the stem — deliberately, so that
 * `M4_Colour_Variant_Normal` is a normal map — and the M4A1's maps are named
 * `Carbine_M4A1_0110_Base_color_1001`. The tile sits *after* the map suffix, so
 * every one of its 67 files failed every anchored pattern and the whole model
 * matched **zero** sets.
 *
 * That failure is quiet in the worst way. The script reports the unmatched files
 * and carries on, the export succeeds, and the weapon renders flat grey — which
 * reads as a lighting bug in the game rather than as a converter that found no
 * textures, because a weapon with no maps still has a shape.
 */
function textureFiles(dir) {
  if (!dir || !existsSync(dir)) return [];
  return readdirSync(dir)
    .filter((name) => /\.(png|jpe?g|webp|tga|bmp)$/i.test(name))
    .map((name) => ({
      name,
      path: join(dir, name),
      stem: basename(name, extname(name)).replace(UDIM_TILE, ''),
    }));
}

/**
 * Decide which slot each texture file belongs in.
 *
 * Grouped by "texture set" — the stem with its map suffix removed — because
 * these models carry several sets (`svu_a_sniper_rifle_texset_*`,
 * `..._scope_texset_*`, `..._lens_texset_*`) and pouring all of them into one
 * material would give the rifle its own scope's paint.
 */
function classifyTextures(files) {
  const sets = new Map();
  const unmatched = [];
  for (const file of files) {
    let matched = null;
    for (const { slot, tests } of MAP_PATTERNS) {
      const test = tests.find((re) => re.test(file.stem));
      if (!test) continue;
      matched = {
        slot,
        set: file.stem
          .replace(test, '')
          .replace(/[_-]+$/, '')
          .toLowerCase(),
      };
      break;
    }
    if (!matched) {
      unmatched.push(file.name);
      continue;
    }
    const entry = sets.get(matched.set) ?? {};
    // First match wins per slot, so a directory carrying both `_C` and
    // `_BaseColor` for one set does not depend on readdir order.
    entry[matched.slot] ??= file;
    sets.set(matched.set, entry);
  }
  return { sets, unmatched };
}

/**
 * Pick the texture set for a material.
 *
 * By longest shared prefix between the material's name and the set's, because
 * neither side is authoritative: the FBX names a material `R870 Express
 * Tactical` and the files are `R870_Express_Tactical_Basecolor`, which agree on
 * everything except separators and case. Falling back to the *largest* set
 * rather than the first is what keeps a single-material model working when its
 * material is called `__DEFAULT` and shares no prefix with anything.
 */
function chooseSet(materialName, sets) {
  if (sets.size === 0) return null;
  const normalise = (s) => s.toLowerCase().replace(/[^a-z0-9]/g, '');
  const target = normalise(materialName);
  let best = null;
  let bestScore = -1;
  for (const [name, entry] of sets) {
    const candidate = normalise(name);
    let shared = 0;
    while (
      shared < candidate.length &&
      shared < target.length &&
      candidate[shared] === target[shared]
    ) {
      shared++;
    }
    const score = shared * 100 + Object.keys(entry).length;
    if (score > bestScore) {
      bestScore = score;
      best = entry;
    }
  }
  return best;
}

/**
 * Pack separate metalness and roughness maps into the one texture glTF wants.
 *
 * glTF has no separate metalness and roughness slots: there is a single
 * `metallicRoughnessTexture` with **roughness in G and metalness in B**. Every
 * one of these models ships them as two files, so something has to combine them.
 *
 * `GLTFExporter` will do it — by drawing both onto a canvas and reading the
 * pixels back — which is the one thing the headless shims cannot do, and it
 * surfaces as `context.fillRect is not a function` well after the interesting
 * part of the conversion has already succeeded. Doing it here with sharp is both
 * the fix and the better version: the exporter's path would decode and
 * re-encode, this composites the channels directly.
 *
 * The result is assigned to **both** slots as the same texture object, which is
 * what makes the exporter take its already-packed branch instead of trying to
 * merge again.
 *
 * R carries occlusion when there is an AO map, which is where glTF's occlusion
 * conventionally lives, and white otherwise — never black, which would read as
 * "fully occluded" and render the weapon in shadow no matter where it stood.
 */
async function packMetallicRoughness(THREE, sharp, material, ao, format) {
  const rough = material.roughnessMap?.image?.__bytes;
  const metal = material.metalnessMap?.image?.__bytes;
  if (!rough && !metal) return null;

  // Sized from whichever map exists, and both resized to it: the channels have
  // to line up pixel for pixel, and these sets are not guaranteed to ship at
  // matching resolutions.
  const reference = await sharp(rough ?? metal).metadata();
  const width = reference.width;
  const height = reference.height;
  // A constant plane is filled directly rather than through sharp: `create`
  // refuses a single-channel image (it wants 3 or 4), and going via RGB only to
  // throw two channels away is a decode and an encode for a buffer of one
  // repeated byte.
  const channel = async (bytes, fallback) => {
    if (!bytes) return Buffer.alloc(width * height, fallback);
    // `.raw()` is load bearing: without it sharp hands back an *encoded* PNG,
    // and `joinChannel` reads it as raw pixels — which fails with a size
    // complaint naming two numbers that look unrelated to anything here.
    return sharp(bytes).resize(width, height, { fit: 'fill' }).greyscale().raw().toBuffer();
  };

  // 255 for a missing map, not 0. A missing roughness map means "fully rough",
  // and a missing occlusion map means "not occluded"; zero would mean mirror
  // finish and pitch dark respectively.
  const [r, g, b] = await Promise.all([
    channel(ao?.image?.__bytes ?? null, 255),
    channel(rough, 255),
    channel(metal, 0), // no metalness map means non-metal, which is 0
  ]);

  const raw = { width, height, channels: 1 };
  const packed = sharp(r, { raw }).joinChannel(g, { raw }).joinChannel(b, { raw });
  const bytes =
    format === 'png'
      ? await packed.png({ compressionLevel: 9 }).toBuffer()
      : await packed.webp({ quality: 92 }).toBuffer();

  const canvas = new ShimCanvas(width, height);
  canvas.__bytes = bytes;
  canvas.__mime = format === 'png' ? 'image/png' : 'image/webp';
  const texture = new THREE.Texture();
  texture.name = 'metallicRoughness';
  texture.image = canvas;
  texture.userData.mimeType = canvas.__mime;
  // Channel data, never colour. An sRGB decode here would bend the roughness
  // curve and look like the material is simply wrong.
  texture.colorSpace = THREE.NoColorSpace;
  texture.flipY = false;
  texture.needsUpdate = true;
  return texture;
}

async function main() {
  const opts = parseArgs(process.argv);
  if (opts.help || (!opts.model && !opts.fbx)) {
    console.log(HELP);
    return;
  }

  installShims();

  const THREE = await import(threeUrl('build/three.module.js'));
  const { FBXLoader } = await import(threeUrl('examples/jsm/loaders/FBXLoader.js'));
  const { GLTFExporter } = await import(threeUrl('examples/jsm/exporters/GLTFExporter.js'));
  const sharp = (await import('sharp')).default;

  // -- locate the source ----------------------------------------------------

  let fbxFile = opts.fbx ? resolvePath(REPO_ROOT, opts.fbx) : null;
  let textureDir = opts.textures ? resolvePath(REPO_ROOT, opts.textures) : null;

  if (opts.model) {
    const modelDir = resolvePath(REPO_ROOT, opts.model);
    if (!existsSync(modelDir)) throw new Error(`no such model directory: ${modelDir}`);
    const sourceDir = join(modelDir, 'source');
    const candidates = existsSync(sourceDir)
      ? readdirSync(sourceDir).filter((n) => n.toLowerCase().endsWith('.fbx'))
      : [];
    if (candidates.length === 0) {
      // Naming the archives explicitly, because this is the common case: several
      // of these downloads ship the mesh inside a rar or zip and the directory
      // looks complete until you go looking for the FBX.
      const archives = existsSync(sourceDir)
        ? readdirSync(sourceDir).filter((n) => /\.(zip|rar|7z)$/i.test(n))
        : [];
      throw new Error(
        `no .fbx in ${sourceDir}` +
          (archives.length
            ? `\n\nThe mesh is still inside ${archives.join(', ')} — extract it there first.`
            : ''),
      );
    }
    fbxFile ??= join(sourceDir, candidates[0]);
    textureDir ??= join(modelDir, 'textures');
  }

  if (!fbxFile || !existsSync(fbxFile)) throw new Error(`no such FBX: ${fbxFile}`);

  // -- load -----------------------------------------------------------------

  const pending = [];
  const report = { textures: [], missingTextures: [] };
  installTextureLoader(THREE, sharp, opts, pending, report);

  const buf = readFileSync(fbxFile);
  const ab = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
  const root = new FBXLoader().parse(ab, `${dirname(fbxFile)}/`);
  await Promise.all(pending);

  // -- prune ----------------------------------------------------------------
  //
  // Before the measure, not after, and that ordering is the whole point of the
  // flag. `--length` is derived from the longest side of the bounding box and
  // the muzzle is read back off its front face (`fitWeaponModel` takes the
  // model's `min.z`), so one stray mesh sitting outside the weapon corrupts
  // *both*: the M4A1 carries a loose cartridge floating past its flash hider,
  // which made the rifle itself 6% shorter than the stated length and put the
  // muzzle flash out in front of the barrel, in mid-air.
  //
  // Matched against the object name *and* the material name because these
  // downloads name their objects nothing at all — the M4A1's are
  // `mesh1.dat.desirefx.me_out` — while their materials are descriptive
  // (`Carbine_M4A1_Bullet_01`). Anchor with `$` when a name is a prefix of its
  // siblings: `Bullet_01$` is the loose round, `Bullet_01` is also the four in
  // the magazine.
  if (opts.exclude.length) {
    const patterns = opts.exclude.map((p) => new RegExp(p, 'i'));
    const matches = (name) => !!name && patterns.some((re) => re.test(name));
    const dropped = [];
    root.traverse((obj) => {
      if (!obj.isMesh) return;
      const names = [obj.name, ...(Array.isArray(obj.material) ? obj.material : [obj.material]).map((m) => m?.name)];
      if (names.some(matches)) dropped.push(obj);
    });
    for (const obj of dropped) obj.removeFromParent();
    // Reported by count *and* by name, and a pattern that matched nothing is an
    // error rather than a shrug: an exclusion is stated because something is
    // known to be in the way, so a typo that silently excludes nothing would
    // hand back the exact model the flag was passed to avoid.
    for (const re of patterns) {
      if (!dropped.some((o) => matches(o.name) || (Array.isArray(o.material) ? o.material : [o.material]).some((m) => re.test(m?.name ?? '')))) {
        throw new Error(
          `--exclude ${re.source} matched no mesh.
` +
            'Run with --inspect to list the materials this file actually carries.',
        );
      }
    }
    console.log(
      `excluded   ${dropped.length} mesh(es): ` +
        dropped.map((o) => (Array.isArray(o.material) ? o.material[0]?.name : o.material?.name) || o.name).join(', '),
    );
  }

  // -- measure --------------------------------------------------------------

  const box = new THREE.Box3().setFromObject(root);
  const size = box.getSize(new THREE.Vector3());
  const longest = ['x', 'y', 'z'].reduce((a, b) => (size[a] >= size[b] ? a : b));

  let triangles = 0;
  const meshes = [];
  const materials = new Set();
  root.traverse((obj) => {
    if (!obj.isMesh) return;
    meshes.push(obj);
    triangles += (obj.geometry?.attributes?.position?.count ?? 0) / 3;
    for (const m of Array.isArray(obj.material) ? obj.material : [obj.material]) {
      if (m) materials.add(m);
    }
  });

  const files = textureFiles(textureDir);
  const { sets, unmatched } = classifyTextures(files);

  console.log(`\nsource     ${fbxFile.replace(`${REPO_ROOT}\\`, '').replace(`${REPO_ROOT}/`, '')}`);
  console.log(
    `bbox       ${size.x.toFixed(2)} x ${size.y.toFixed(2)} x ${size.z.toFixed(2)}   longest: ${longest}`,
  );
  console.log(`geometry   ${meshes.length} meshes, ${Math.round(triangles)} triangles`);
  console.log(`materials  ${materials.size}`);
  console.log(`textures   ${files.length} files, ${sets.size} sets`);
  for (const [name, entry] of sets) {
    console.log(`   set ${name || '(root)'}: ${Object.keys(entry).sort().join(', ')}`);
  }
  if (unmatched.length) console.log(`   unmatched: ${unmatched.join(', ')}`);

  // A viewmodel is drawn every frame at arm's length; the whole native world is
  // around 32k triangles. Said as a warning rather than a refusal, because what
  // counts as too many is a judgement about the target machine and this script
  // has no business making it — but a 687k-triangle carbine is not a viewmodel,
  // and finding that out after wiring it into two renderers is worse.
  if (triangles > 40000) {
    console.log(
      `\n! ${Math.round(triangles)} triangles is a lot for a first-person prop.\n` +
        '  For scale: the entire hd_atrium world draws about 32k. Decimate in\n' +
        '  Blender before converting — three has no decimator worth using at\n' +
        '  this ratio.',
    );
  }

  if (opts.inspect) {
    console.log('\n--inspect: nothing written.');
    return;
  }
  if (!opts.out) throw new Error('--out is required (or use --inspect)');
  if (!opts.forward) {
    throw new Error(
      `--forward is required.\n\n` +
        `This model is longest along ${longest}, but a bounding box cannot tell a\n` +
        `barrel pointing +${longest} from one pointing -${longest} — they are the same box.\n` +
        `Open it once, look, and pass --forward +${longest} or -${longest}.`,
    );
  }

  // -- materials ------------------------------------------------------------

  const wired = [];
  const missingBaseColor = [];
  // Collected rather than packed inline: packing has to happen after
  // `resolveFlips`, and the AO map is dropped from the material before then.
  const packing = [];
  for (const mat of materials) {
    const entry = chooseSet(mat.name ?? '', sets);
    // Phong to metallic-roughness. Exporting the Phong material directly drops
    // metalness and roughness silently — glTF has no slot for them on a material
    // it does not recognise — and the weapon comes out with a base colour and no
    // surface response at all.
    const standard = new THREE.MeshStandardMaterial({
      name: mat.name,
      color: mat.color?.clone?.() ?? new THREE.Color(0xffffff),
      metalness: 1,
      roughness: 1,
    });
    const slots = [];
    let aoTexture = null;
    if (entry) {
      const loader = new THREE.TextureLoader();
      for (const [slot, file] of Object.entries(entry)) {
        const texture = loader.load(file.path);
        if (slot === 'aoMap') {
          // Held back rather than assigned: glTF's occlusion lives in the R
          // channel of the same packed texture as metalness and roughness, so
          // this is picked up by `packMetallicRoughness` below instead.
          texture.name = file.name;
          aoTexture = texture;
          slots.push('ao(packed)');
          continue;
        }
        texture.name = file.name;
        // Only the base colour is colour data. A normal or roughness map read as
        // sRGB is decoded on sampling and comes out subtly, unfixably wrong —
        // and it looks like the model is bad rather than the pipeline.
        texture.colorSpace = slot === 'map' ? THREE.SRGBColorSpace : THREE.NoColorSpace;
        standard[slot] = texture;
        slots.push(slot);
      }
    }
    // The normal map the FBX did carry, if the directory had none.
    if (!standard.normalMap && mat.normalMap) {
      standard.normalMap = mat.normalMap;
      slots.push('normalMap(fbx)');
    }
    if (!standard.map) {
      // Deliberately **not** `report.missingTextures`. That list belongs to the
      // shared texture loader and means "the FBX referenced a file that is not
      // on disk", which is routine here — these FBXs name their maps with spaces
      // (`R870 Express Tactical Normal.png`) while the directory beside them uses
      // underscores, and we wire from the directory anyway. Reusing the list
      // printed *file* names under a heading about *materials*, which is a worse
      // failure than the one it was meant to report.
      missingBaseColor.push(mat.name || '(unnamed)');
    }
    wired.push({ name: mat.name || '(unnamed)', slots });
    if (standard.metalnessMap || standard.roughnessMap) {
      packing.push({ material: standard, ao: aoTexture });
    }
    // Swap it in wherever it was used.
    for (const mesh of meshes) {
      if (Array.isArray(mesh.material)) {
        mesh.material = mesh.material.map((m) => (m === mat ? standard : m));
      } else if (mesh.material === mat) {
        mesh.material = standard;
      }
    }
  }
  await Promise.all(pending);
  await resolveFlips(root, sharp);

  // After the flips, so the packed channels inherit pixels that are already the
  // right way up — packing first would bake a flip into two of the three
  // channels and leave the third alone, which is not a mistake anything downstream
  // could detect.
  for (const { material, ao } of packing) {
    const packed = await packMetallicRoughness(THREE, sharp, material, ao, opts.textureFormat);
    if (!packed) continue;
    // The same object in both slots: that is what makes the exporter use it as
    // already-packed rather than trying to merge two maps on a canvas it cannot
    // draw to.
    material.metalnessMap = packed;
    material.roughnessMap = packed;
    if (ao) material.aoMap = packed;
  }

  console.log('');
  for (const { name, slots } of wired) {
    console.log(`material   ${name}: ${slots.length ? slots.sort().join(', ') : 'NO MAPS'}`);
  }
  if (missingBaseColor.length) {
    console.log(
      `\n! no base colour for material: ${missingBaseColor.join(', ')}\n` +
        '  Those surfaces render flat, which reads as a lighting bug rather than\n' +
        '  a missing texture. Check --textures points at the right directory.',
    );
  }
  if (report.missingTextures.length) {
    // Informational, not a warning: every one of these downloads names its maps
    // differently inside the FBX from the files sitting beside it, and the
    // directory is what we wire from.
    console.log(
      `\n  (the FBX also referenced ${report.missingTextures.length} texture path(s) ` +
        'not on disk; wired from --textures instead)',
    );
  }

  // -- transform ------------------------------------------------------------
  //
  // Applied to a wrapper rather than to the geometry: the source keeps whatever
  // pivot and rotation it had, and everything below is one node's transform that
  // can be read back out of the GLB and checked.

  const forward = parseForward(opts.forward);
  const scale = opts.length / size[longest];

  // A weapon prop is geometry and nothing else. The Remington's FBX carries a
  // light, and an exported prop that lights the room it is carried through is a
  // bug nobody would go looking for in a *model* — it reads as the renderer's
  // lighting being wrong. Cameras go for the same reason: an artist's viewport
  // is not part of the weapon.
  const strays = [];
  root.traverse((obj) => {
    if (obj.isLight || obj.isCamera) strays.push(obj);
  });
  for (const stray of strays) stray.removeFromParent();
  if (strays.length) {
    console.log(`stripped   ${strays.length} light/camera node(s) the source carried`);
  }

  const oriented = new THREE.Group();
  oriented.name = 'weapon';
  oriented.add(root);

  // Rotate so the stated forward axis becomes -Z, which is where a camera looks
  // and therefore where the barrel has to point for the view model's `HOME`
  // offsets to mean anything.
  const from = new THREE.Vector3(
    forward.axis === 'x' ? forward.sign : 0,
    forward.axis === 'y' ? forward.sign : 0,
    forward.axis === 'z' ? forward.sign : 0,
  );
  const to = new THREE.Vector3(0, 0, -1);
  root.quaternion.setFromUnitVectors(from, to);
  root.scale.setScalar(scale);
  root.updateMatrixWorld(true);

  // Re-measure *after* the rotation and scale: the origin has to be placed in
  // the frame the weapon ends up in, and moving it first puts the grip wherever
  // the source happened to have its pivot.
  const finalBox = new THREE.Box3().setFromObject(root);
  const finalSize = finalBox.getSize(new THREE.Vector3());
  const centre = finalBox.getCenter(new THREE.Vector3());
  const origin =
    opts.grip === 'centre'
      ? centre
      : // The rear of the weapon, at the bottom — roughly where a hand is, and
        // the point the view model's `HOME` is expressed relative to. Not a real
        // grip detection: nothing in the file marks one, and a heuristic that
        // was wrong would be wrong differently per model.
        new THREE.Vector3(centre.x, finalBox.min.y, finalBox.max.z);
  root.position.sub(origin);

  console.log(
    `\nscaled     x${scale.toPrecision(4)} -> ${finalSize.x.toFixed(2)} x ` +
      `${finalSize.y.toFixed(2)} x ${finalSize.z.toFixed(2)} cubes`,
  );
  console.log(`oriented   ${opts.forward} -> -z, origin at ${opts.grip}`);

  // -- export ---------------------------------------------------------------

  const outFile = resolvePath(REPO_ROOT, opts.out);
  mkdirSync(dirname(outFile), { recursive: true });
  const exporter = new GLTFExporter();
  const glb = await exporter.parseAsync(oriented, {
    binary: true,
    onlyVisible: false,
    maxTextureSize: Infinity, // sharp already resized; this stops a second clamp
  });
  writeFileSync(outFile, Buffer.from(glb));
  const kb = statSync(outFile).size / 1024;
  console.log(
    `\nwrote      ${opts.out}  (${kb > 1024 ? `${(kb / 1024).toFixed(1)} MB` : `${kb.toFixed(0)} KB`})`,
  );
}

main().catch((err) => {
  console.error(`\n${err.message}\n`);
  process.exitCode = 1;
});
