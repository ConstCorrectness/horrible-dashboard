#!/usr/bin/env node
/**
 * Convert free-gameready-fps-female-arms FBX + texture into `apps/web/public/hassault-arms.glb`
 * for the HorribleAssault first-person viewport.
 *
 * Scales the rigged female arms to cube units (1 cube = 36 cm), attaches the PBR texture,
 * and exports a clean GLB with the skeleton and default poses.
 */

import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { dirname, join, resolve as resolvePath } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createRequire } from 'node:module';

import { installShims, installTextureLoader, ShimCanvas } from './lib/three-headless.mjs';

installShims();
globalThis.self = globalThis;

const REPO_ROOT = resolvePath(dirname(fileURLToPath(import.meta.url)), '..');
const THREE_DIR = resolvePath(
  dirname(createRequire(join(REPO_ROOT, 'packages/core/package.json')).resolve('three')),
  '..',
);
const threeUrl = (rel) => pathToFileURL(join(THREE_DIR, rel)).href;

const THREE = await import(threeUrl('build/three.module.js'));
const { FBXLoader } = await import(threeUrl('examples/jsm/loaders/FBXLoader.js'));
const { GLTFExporter } = await import(threeUrl('examples/jsm/exporters/GLTFExporter.js'));
const sharp = (await import('sharp')).default;

async function main() {
  const fbxPath = resolvePath(
    REPO_ROOT,
    'assets/horribleAssault/free-gameready-fps-female-arms/source/[FPS Female Arms].fbx',
  );
  const texPath = resolvePath(
    REPO_ROOT,
    'assets/horribleAssault/free-gameready-fps-female-arms/textures/[FPS_Female_Arms]_[FPS_Female_Arms]_Materi.png',
  );
  const outPath = resolvePath(REPO_ROOT, 'apps/web/public/hassault-arms.glb');

  if (!existsSync(fbxPath)) {
    throw new Error(`Arms FBX not found: ${fbxPath}`);
  }

  const pending = [];
  installTextureLoader(THREE, sharp, { textureSize: 1024, textureFormat: 'png' }, pending, {});

  console.log(`Loading arms FBX from ${fbxPath}...`);
  const buf = readFileSync(fbxPath);
  const ab = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
  const loader = new FBXLoader();
  const root = loader.parse(ab, dirname(fbxPath) + '/');
  await Promise.all(pending);

  let texture = null;
  if (existsSync(texPath)) {
    console.log(`Processing texture ${texPath}...`);
    const texBuffer = await sharp(texPath).resize(1024, 1024).png({ compressionLevel: 8 }).toBuffer();
    const canvas = new ShimCanvas(1024, 1024);
    canvas.__bytes = texBuffer;
    canvas.__mime = 'image/png';

    texture = new THREE.Texture();
    texture.image = canvas;
    texture.userData.mimeType = 'image/png';
    texture.needsUpdate = true;
  }

  // 1 cube = ~36 cm. Source wingspan is in cm.
  const scale = 1 / 36;
  root.scale.setScalar(scale);

  const mat = new THREE.MeshStandardMaterial({
    map: texture,
    color: 0xffffff,
    roughness: 0.65,
    metalness: 0.1,
  });

  root.traverse((c) => {
    if (c.isSkinnedMesh) {
      c.material = mat;
    }
  });

  console.log('Exporting GLB...');
  const exporter = new GLTFExporter();
  const glb = await exporter.parseAsync(root, {
    binary: true,
    animations: root.animations || [],
  });

  writeFileSync(outPath, Buffer.from(glb));
  const sizeMb = (glb.byteLength / (1024 * 1024)).toFixed(2);
  console.log(`Wrote ${outPath} (${sizeMb} MB)`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
