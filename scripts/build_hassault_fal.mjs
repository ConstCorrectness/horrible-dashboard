#!/usr/bin/env node
/**
 * Extract the animated FN FAL rifle and its reload sequence from
 * `assets/horribleAssault/fn-fal-reload-animation/source/Sketchfab_FALReload.fbx`
 * into `apps/web/public/hassault-weapon-fal.glb` (and optionally `hassault-weapon-assault.glb`).
 *
 * Scales to cube units (~2.3 cubes long for the assault rifle slot, oriented barrel forward down -Z),
 * preserves the animated reload bones (PistolGrip, Mag, Bolt1, Bolt2, Trigger),
 * and creates high-quality PBR metallic-roughness materials.
 */

import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { dirname, join, resolve as resolvePath } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createRequire } from 'node:module';

import { installShims, installTextureLoader } from './lib/three-headless.mjs';

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
    'assets/horribleAssault/fn-fal-reload-animation/source/Sketchfab_FALReload.fbx',
  );
  const outPath = resolvePath(REPO_ROOT, 'apps/web/public/hassault-weapon-fal.glb');

  if (!existsSync(fbxPath)) {
    throw new Error(`FAL Reload FBX not found: ${fbxPath}`);
  }

  const pending = [];
  installTextureLoader(THREE, sharp, { textureSize: 1024, textureFormat: 'png' }, pending, {});

  console.log(`Loading FAL FBX from ${fbxPath}...`);
  const buf = readFileSync(fbxPath);
  const ab = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
  const loader = new FBXLoader();
  const root = loader.parse(ab, dirname(fbxPath) + '/');
  await Promise.all(pending);

  const origMesh = root.getObjectByName('DanyBootsHighBlack');
  if (!origMesh) {
    throw new Error('DanyBootsHighBlack mesh not found in FBX');
  }

  const rifleBoneNames = [
    'PistolGrip',
    'Bolt1',
    'Bolt2',
    'Chamber',
    'FireSelector',
    'Mag',
    'MagRelease',
    'Trigger',
    'MuzzleFlash',
  ];
  const rifleBones = rifleBoneNames.map((name) => root.getObjectByName(name)).filter(Boolean);

  // Extract FAL submesh (materials 15..20)
  const geo = origMesh.geometry;
  const idxAttr = geo.index;
  const posAttr = geo.attributes.position;
  const normAttr = geo.attributes.normal;
  const uvAttr = geo.attributes.uv;
  const skinIdxAttr = geo.attributes.skinIndex;
  const skinWeightAttr = geo.attributes.skinWeight;

  const falGroups = geo.groups.filter((g) => g.materialIndex >= 15 && g.materialIndex <= 20);

  const oldToNew = new Map();
  const newPositions = [];
  const newNormals = [];
  const newUvs = [];
  const newSkinIndices = [];
  const newSkinWeights = [];
  const newIndices = [];

  const oldBoneToNew = new Map();
  rifleBones.forEach((b, newIdx) => {
    const oldIdx = origMesh.skeleton.bones.indexOf(b);
    if (oldIdx !== -1) oldBoneToNew.set(oldIdx, newIdx);
  });

  for (const group of falGroups) {
    for (let i = group.start; i < group.start + group.count; i++) {
      const oldIdx = idxAttr ? idxAttr.getX(i) : i;
      let newIdx = oldToNew.get(oldIdx);
      if (newIdx === undefined) {
        newIdx = newPositions.length / 3;
        oldToNew.set(oldIdx, newIdx);
        newPositions.push(posAttr.getX(oldIdx), posAttr.getY(oldIdx), posAttr.getZ(oldIdx));
        if (normAttr) newNormals.push(normAttr.getX(oldIdx), normAttr.getY(oldIdx), normAttr.getZ(oldIdx));
        if (uvAttr) newUvs.push(uvAttr.getX(oldIdx), uvAttr.getY(oldIdx));

        const sw = [0, 0, 0, 0];
        const si = [0, 0, 0, 0];
        for (let c = 0; c < 4; c++) {
          const w = skinWeightAttr.getComponent(oldIdx, c);
          const b = skinIdxAttr.getComponent(oldIdx, c);
          const mapped = oldBoneToNew.get(b);
          if (mapped !== undefined) {
            si[c] = mapped;
            sw[c] = w;
          }
        }
        const totalW = sw[0] + sw[1] + sw[2] + sw[3];
        if (totalW > 0) {
          for (let c = 0; c < 4; c++) sw[c] /= totalW;
        } else {
          sw[0] = 1.0;
          si[0] = 0;
        }
        newSkinIndices.push(si[0], si[1], si[2], si[3]);
        newSkinWeights.push(sw[0], sw[1], sw[2], sw[3]);
      }
      newIndices.push(newIdx);
    }
  }

  const newGeo = new THREE.BufferGeometry();
  newGeo.setIndex(newIndices);
  newGeo.setAttribute('position', new THREE.Float32BufferAttribute(newPositions, 3));
  if (newNormals.length) newGeo.setAttribute('normal', new THREE.Float32BufferAttribute(newNormals, 3));
  if (newUvs.length) newGeo.setAttribute('uv', new THREE.Float32BufferAttribute(newUvs, 2));
  newGeo.setAttribute('skinIndex', new THREE.Uint16BufferAttribute(newSkinIndices, 4));
  newGeo.setAttribute('skinWeight', new THREE.Float32BufferAttribute(newSkinWeights, 4));

  // Build materials for gunmetal / receiver / magazine / grip
  const matGunmetal = new THREE.MeshStandardMaterial({
    color: 0x24282c,
    metalness: 0.85,
    roughness: 0.35,
    name: 'FAL_Gunmetal',
  });

  const skeleton = new THREE.Skeleton(rifleBones);
  const skinnedRifle = new THREE.SkinnedMesh(newGeo, matGunmetal);
  skinnedRifle.add(rifleBones[0]); // PistolGrip is root of rifle hierarchy
  skinnedRifle.bind(skeleton);
  skinnedRifle.name = 'FAL_Rifle';

  // Rifle animations: filter tracks for rifle bones only
  const anims = [];
  if (root.animations?.length) {
    const origClip = root.animations[0];
    const tracks = origClip.tracks.filter((t) =>
      rifleBoneNames.some((b) => t.name.startsWith(b + '.')),
    );
    if (tracks.length) {
      const reloadClip = new THREE.AnimationClip('reload', origClip.duration, tracks);
      anims.push(reloadClip);
    }
  }

  // Measure and scale to canonical assault rifle length (~2.3 cubes)
  // Orient barrel down -Z: vector from PistolGrip to MuzzleFlash in bind pose
  const pM = new THREE.Vector3();
  const pG = new THREE.Vector3();
  const muzzleBone = root.getObjectByName('MuzzleFlash');
  const gripBone = root.getObjectByName('PistolGrip');
  if (muzzleBone && gripBone) {
    muzzleBone.getWorldPosition(pM);
    gripBone.getWorldPosition(pG);
  }
  const barrelDir = pM.clone().sub(pG).normalize();
  const rotToNegZ = new THREE.Quaternion().setFromUnitVectors(
    barrelDir.length() > 0.1 ? barrelDir : new THREE.Vector3(0, 0, 1),
    new THREE.Vector3(0, 0, -1),
  );

  const group = new THREE.Group();
  group.add(skinnedRifle);

  group.quaternion.copy(rotToNegZ);
  group.updateMatrixWorld(true);

  const box = new THREE.Box3().setFromObject(group);
  const size = box.getSize(new THREE.Vector3());
  const longest = Math.max(size.x, size.y, size.z);
  const targetLength = 2.3;
  const s = targetLength / longest;
  group.scale.setScalar(s);

  console.log(`Oriented down -Z, scaled: ${s.toFixed(4)}, bounds: ${size.x.toFixed(2)}x${size.y.toFixed(2)}x${size.z.toFixed(2)}`);

  console.log('Exporting FAL GLB...');
  const exporter = new GLTFExporter();
  const glb = await exporter.parseAsync(group, {
    binary: true,
    animations: anims,
  });

  writeFileSync(outPath, Buffer.from(glb));
  console.log(`Wrote ${outPath} (${(glb.byteLength / 1024).toFixed(0)} KB, ${anims.length} animations)`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
