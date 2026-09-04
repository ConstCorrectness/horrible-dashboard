#!/usr/bin/env node

/**
 * HorribleAssault 3D Model Asset Sync & Cache Utility
 *
 * Commands:
 *   node scripts/assets.mjs check   - Validate integrity of all local models against manifest
 *   node scripts/assets.mjs pull    - Download missing or corrupted models from remote host into local cache
 *   node scripts/assets.mjs pack    - Refresh manifest SHA-256 hashes from public/ directory
 */

import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, writeFileSync, copyFileSync, statSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const REPO_ROOT = resolve(__dirname, '..');

const MANIFEST_PATH = resolve(REPO_ROOT, 'packages/core/src/modules/hassault/assets.manifest.json');
const ROOT_MANIFEST_PATH = resolve(REPO_ROOT, 'assets/manifest.json');
const CACHE_DIR = resolve(REPO_ROOT, '.cache/assets');
const PUBLIC_DIR = resolve(REPO_ROOT, 'apps/web/public');

function loadManifest() {
  const content = readFileSync(MANIFEST_PATH, 'utf-8');
  return JSON.parse(content);
}

function sha256File(path) {
  if (!existsSync(path)) return null;
  const buffer = readFileSync(path);
  return createHash('sha256').update(buffer).digest('hex');
}

async function downloadFile(url, destPath) {
  console.log(`  Downloading ${url} -> ${destPath}...`);
  mkdirSync(dirname(destPath), { recursive: true });

  const res = await fetch(url);
  if (!res.ok) {
    throw new Error(`HTTP ${res.status} ${res.statusText} fetching ${url}`);
  }

  const arrayBuffer = await res.arrayBuffer();
  writeFileSync(destPath, Buffer.from(arrayBuffer));
}

async function cmdCheck() {
  const manifest = loadManifest();
  console.log(`Checking ${Object.keys(manifest.assets).length} HorribleAssault 3D assets...`);

  let allOk = true;
  for (const [key, asset] of Object.entries(manifest.assets)) {
    const destPath = resolve(REPO_ROOT, asset.destination);
    if (!existsSync(destPath)) {
      console.log(`❌ [MISSING]  ${asset.filename} (expected at ${asset.destination})`);
      allOk = false;
      continue;
    }

    const currentHash = sha256File(destPath);
    if (currentHash !== asset.sha256) {
      console.log(`⚠️  [HASH MISMATCH] ${asset.filename}`);
      console.log(`     Expected: ${asset.sha256}`);
      console.log(`     Found:    ${currentHash}`);
      allOk = false;
    } else {
      const size = (asset.size / (1024 * 1024)).toFixed(2);
      console.log(`✅ [OK]       ${asset.filename.padEnd(28)} (${size} MB)`);
    }
  }

  if (!allOk) {
    console.log('\nSome assets are missing or modified. Run `pnpm assets:pull` to download them.');
    process.exit(1);
  } else {
    console.log('\nAll assets are verified and intact.');
  }
}

async function cmdPull() {
  const manifest = loadManifest();
  const baseUrl = process.env.HORRIBLE_ASSETS_BASE_URL || manifest.baseUrl;
  mkdirSync(CACHE_DIR, { recursive: true });
  mkdirSync(PUBLIC_DIR, { recursive: true });

  console.log(`Syncing assets from ${baseUrl}...`);

  for (const [key, asset] of Object.entries(manifest.assets)) {
    const cacheFile = join(CACHE_DIR, asset.filename);
    const destFile = resolve(REPO_ROOT, asset.destination);

    let needsFetch = true;

    // 1. Check if public file matches hash
    if (existsSync(destFile)) {
      const destHash = sha256File(destFile);
      if (destHash === asset.sha256) {
        console.log(`⚡ [CACHED]   ${asset.filename} matches manifest hash.`);
        continue;
      }
    }

    // 2. Check if cache file matches hash
    if (existsSync(cacheFile)) {
      const cacheHash = sha256File(cacheFile);
      if (cacheHash === asset.sha256) {
        console.log(`📦 [RESTORE]  Copying ${asset.filename} from local cache...`);
        copyFileSync(cacheFile, destFile);
        continue;
      }
    }

    // 3. Download from remote host
    const remoteUrl = `${baseUrl}/${asset.filename}`;
    try {
      await downloadFile(remoteUrl, cacheFile);
      const downloadedHash = sha256File(cacheFile);
      if (downloadedHash !== asset.sha256) {
        console.warn(`⚠️ Warning: Downloaded hash mismatch for ${asset.filename}!`);
      }
      copyFileSync(cacheFile, destFile);
      console.log(`✅ [PULLED]   ${asset.filename}`);
    } catch (err) {
      console.error(`❌ [FAILED]   Could not download ${asset.filename}: ${err.message}`);
    }
  }
}

async function cmdPack() {
  const manifest = loadManifest();
  console.log('Scanning apps/web/public for model hashes...');

  let changed = false;
  for (const [key, asset] of Object.entries(manifest.assets)) {
    const destFile = resolve(REPO_ROOT, asset.destination);
    if (existsSync(destFile)) {
      const hash = sha256File(destFile);
      const size = statSync(destFile).size;
      if (manifest.assets[key].sha256 !== hash || manifest.assets[key].size !== size) {
        manifest.assets[key].sha256 = hash;
        manifest.assets[key].size = size;
        changed = true;
        console.log(`🔄 Updated hash for ${asset.filename}: ${hash} (${size} bytes)`);
      }
    }
  }

  if (changed) {
    const jsonStr = JSON.stringify(manifest, null, 2) + '\n';
    writeFileSync(MANIFEST_PATH, jsonStr);
    writeFileSync(ROOT_MANIFEST_PATH, jsonStr);
    console.log('✅ assets.manifest.json updated.');
  } else {
    console.log('No hash changes detected.');
  }
}

const action = process.argv[2] || 'check';
switch (action) {
  case 'check':
    cmdCheck();
    break;
  case 'pull':
    cmdPull();
    break;
  case 'pack':
    cmdPack();
    break;
  default:
    console.log('Usage: node scripts/assets.mjs [check|pull|pack]');
    process.exit(1);
}
