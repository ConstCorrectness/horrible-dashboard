#!/usr/bin/env node
/**
 * Build the native hassault client and stage it as the game server's download.
 *
 * Usage:
 *   pnpm build:hassault:download              # build, then stage
 *   pnpm build:hassault:download -- --no-build   # stage the build already there
 *
 * Writes `apps/native-fps/dist/`:
 *
 *   hAssault.exe     the release build, under the name a player sees
 *   manifest.json    { windows: { file, version, commit, sizeBytes, sha256, builtAt } }
 *
 * `deploy/game-server/Dockerfile` copies that directory into the image and
 * `backend/games_server/downloads.py` serves it at `/download/hAssault.exe`.
 * `pnpm deploy:games` runs this before `fly deploy`.
 *
 * ## Why it is built here and not in the image
 *
 * The same reason the browser game is: the image is built on a Linux builder,
 * which cannot produce a Windows executable without a cross toolchain, and the
 * machine running the deploy already has the real one. It also means the client
 * offered for download is built from the same checkout, by the same command, as
 * the server that will simulate it — the two are halves of one wire.
 *
 * ## Why a deploy from Linux or macOS still works
 *
 * There the release build is not a Windows executable, so there is nothing
 * honest to stage. The directory and a manifest are written anyway, with
 * `windows: null` and the reason: the image's COPY has something to copy,
 * `/download/info` answers `null`, and the landing page shows no button. A
 * deploy that carries no download is a smaller deploy, not a failed one. It
 * says so loudly, because the alternative is finding out from a player.
 *
 * ## The checksum is computed from the staged copy
 *
 * Not from cargo's output and not before the copy: the server repeats this
 * number to anyone who asks, so it has to describe the bytes it will serve.
 */
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(here, '..');
const crate = path.join(repoRoot, 'apps', 'native-fps');
const outDir = path.join(crate, 'dist');
const FILE = 'hAssault.exe';

const argv = process.argv.slice(2);
const skipBuild = argv.includes('--no-build');
const rel = (p) => path.relative(repoRoot, p).replaceAll('\\', '/');

function writeManifest(manifest) {
  fs.mkdirSync(outDir, { recursive: true });
  fs.writeFileSync(path.join(outDir, 'manifest.json'), `${JSON.stringify(manifest, null, 2)}\n`);
}

if (process.platform !== 'win32') {
  // Clear a client staged by an earlier run on another machine's checkout: a
  // stale executable beside a manifest that says "none" is one `--no-build`
  // away from being shipped.
  fs.rmSync(path.join(outDir, FILE), { force: true });
  writeManifest({
    windows: null,
    reason: `staged on ${process.platform}: the Windows client is built on Windows`,
  });
  console.warn(
    `hassault: WARNING — staging on ${process.platform}. This deploy will carry NO Windows ` +
      'download; run `pnpm deploy:games` from Windows to ship one.',
  );
  process.exit(0);
}

if (!skipBuild) {
  // Through the build script rather than cargo directly, so there is one
  // definition of "the release build" and the launcher's link is refreshed too.
  const built = spawnSync(process.execPath, [path.join(here, 'build-hassault.mjs'), '--release'], {
    stdio: 'inherit',
  });
  if (built.status !== 0) process.exit(built.status ?? 1);
}

const source = path.join(crate, 'target', 'release', 'hassault-native.exe');
if (!fs.existsSync(source)) {
  console.error(`hassault: ${rel(source)} is not there. Run without --no-build.`);
  process.exit(1);
}

/** `version = "…"` from the `[package]` table. The first one in the file is it. */
function crateVersion() {
  const toml = fs.readFileSync(path.join(crate, 'Cargo.toml'), 'utf8');
  return /^version\s*=\s*"([^"]+)"/m.exec(toml)?.[1] ?? '';
}

/** The commit this was built from, with `+` when the tree had changes in it. */
function commit() {
  const git = (args) => spawnSync('git', args, { cwd: repoRoot, encoding: 'utf8' });
  const head = git(['rev-parse', '--short', 'HEAD']);
  if (head.status !== 0) return '';
  // Only the client's own inputs: an unrelated edit elsewhere in the repo does
  // not make this a different client.
  const dirty = git([
    'status',
    '--porcelain',
    '--',
    'apps/native-fps',
    'apps/web/public',
    '.cargo',
  ]);
  return head.stdout.trim() + (dirty.stdout.trim() ? '+' : '');
}

fs.mkdirSync(outDir, { recursive: true });
const staged = path.join(outDir, FILE);
// Removed first: on Windows a copy over a file somebody is running fails, and
// the error should be about that rather than a half-replaced executable.
fs.rmSync(staged, { force: true });
fs.copyFileSync(source, staged);

const hash = createHash('sha256');
hash.update(fs.readFileSync(staged));
const sha256 = hash.digest('hex');
const { size } = fs.statSync(staged);

writeManifest({
  windows: {
    file: FILE,
    version: crateVersion(),
    commit: commit(),
    sizeBytes: size,
    sha256,
    builtAt: new Date().toISOString(),
  },
});

console.log(
  `hassault: staged ${rel(staged)} — ${(size / 1024 / 1024).toFixed(1)} MiB, sha256 ${sha256.slice(0, 16)}…`,
);
console.log('hassault: `fly deploy` will serve it at /download/hAssault.exe');
