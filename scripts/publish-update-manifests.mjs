#!/usr/bin/env node
/**
 * Turn one release's per-platform electron-builder manifests into the files the
 * fixed `updater` release serves — run by .github/workflows/release.yml once every
 * platform has uploaded.
 *
 *   node scripts/publish-update-manifests.mjs --tag v0.5.0 --dir <downloaded assets> --out <dir>
 *
 * Reads, from `--dir`: `latest.yml` (Windows), `latest-linux.yml`,
 * `latest-mac-<arch>.yml` (one per mac job), and each installer's `.sig`.
 * Writes, to `--out`:
 *
 * - **The Electron feeds**, one per platform, named for the channel the tag implies
 *   (`latest*.yml` for `v1.2.3`, `beta*.yml` for `v1.2.3-beta.1`). Every file URL is
 *   made absolute, pointing at the tag's own release: the feed lives on the `updater`
 *   release (see apps/desktop/src/updater.ts) and a relative name would resolve
 *   there, where the installers are not. The mac jobs' manifests are merged into one
 *   `latest-mac.yml` listing both architectures, which is what electron-updater reads.
 * - **The Tauri manifest** (`latest.json` / `beta.json`), for installs still running
 *   the Tauri shell (≤ 0.4.4): its updater reads that file, checks the minisign
 *   signature against the same key, and runs the new Windows installer or swaps the
 *   AppImage — which is how those installs move to the Electron build. macOS is left
 *   out: the Tauri updater there wants a `.app.tar.gz`, which this build does not make.
 */
import { mkdirSync, readFileSync, writeFileSync, existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const REPO = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const yaml = createRequire(join(REPO, 'apps', 'desktop', 'package.json'))('js-yaml');

const argv = process.argv.slice(2);
const option = (name) => {
  const i = argv.indexOf(name);
  if (i === -1 || !argv[i + 1]) throw new Error(`missing ${name}`);
  return argv[i + 1];
};
const tag = option('--tag');
const dir = resolve(option('--dir'));
const out = resolve(option('--out'));
const repo = process.env.GITHUB_REPOSITORY ?? 'ConstCorrectness/horrible-dashboard';
const channel = tag.includes('-beta') ? 'beta' : 'latest';
const assetUrl = (name) =>
  `https://github.com/${repo}/releases/download/${tag}/${encodeURIComponent(name)}`;

mkdirSync(out, { recursive: true });

function load(name) {
  const file = join(dir, name);
  return existsSync(file) ? yaml.load(readFileSync(file, 'utf8')) : null;
}

/** Point every file at the tag's release. */
function absolutize(manifest) {
  const files = manifest.files.map((f) => ({ ...f, url: assetUrl(f.url) }));
  return { ...manifest, files, path: files[0]?.url ?? manifest.path };
}

function write(name, manifest) {
  writeFileSync(join(out, name), yaml.dump(manifest, { lineWidth: -1 }));
  console.log(`wrote ${name}: ${manifest.files.map((f) => f.url.split('/').pop()).join(', ')}`);
}

const win = load('latest.yml');
const linux = load('latest-linux.yml');
const macs = ['x64', 'arm64'].map((arch) => load(`latest-mac-${arch}.yml`)).filter(Boolean);

if (win) write(`${channel}.yml`, absolutize(win));
if (linux) write(`${channel}-linux.yml`, absolutize(linux));
if (macs.length) {
  const merged = absolutize({ ...macs[0], files: macs.flatMap((m) => m.files) });
  write(`${channel}-mac.yml`, merged);
}

// --- the bridge for installs still on the Tauri shell -------------------------

const signatureOf = (name) => {
  const file = join(dir, `${name}.sig`);
  return existsSync(file) ? readFileSync(file, 'utf8').trim() : null;
};
const platforms = {};
const winInstaller = win?.files.find((f) => f.url.endsWith('.exe'))?.url;
if (winInstaller && signatureOf(winInstaller)) {
  platforms['windows-x86_64'] = { url: assetUrl(winInstaller), signature: signatureOf(winInstaller) };
}
const appImage = linux?.files.find((f) => f.url.endsWith('.AppImage'))?.url;
if (appImage && signatureOf(appImage)) {
  platforms['linux-x86_64'] = { url: assetUrl(appImage), signature: signatureOf(appImage) };
}
const any = win ?? linux ?? macs[0];
if (any && Object.keys(platforms).length) {
  const tauri = {
    version: any.version,
    notes: `horrible-dashboard ${tag}`,
    pub_date: any.releaseDate ?? new Date().toISOString(),
    platforms,
  };
  const name = channel === 'beta' ? 'beta.json' : 'latest.json';
  writeFileSync(join(out, name), `${JSON.stringify(tauri, null, 2)}\n`);
  console.log(`wrote ${name}: ${Object.keys(platforms).join(', ')}`);
}
