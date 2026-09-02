#!/usr/bin/env node
/**
 * Build the native hassault client and put the result where the launcher looks.
 *
 * Usage:
 *   pnpm build:hassault          # optimized, what you play
 *   pnpm build:hassault:debug    # fast to compile, slow to run
 *
 * Anything after the profile flag is passed straight to cargo, so
 * `pnpm build:hassault -- --features foo` works.
 *
 * ## Why this is a script and not `cargo build` in a package.json line
 *
 * `_local_client_candidates` in `backend/modules/hassault/routes.py` lists six
 * places a locally built client can be — `target/release`, `target/debug` and
 * `apps/native-fps/bin/` — and `pick_binary` launches **the newest of them by
 * mtime**, not the first. That rule is right (it stops a stale release build
 * shadowing the debug one you just made) but it has a corollary: whichever of
 * those paths is freshest is what starts, so a copy left behind in one of them
 * is a binary that can win a race you did not know you were running.
 *
 * So after a successful build this **hard-links** the fresh binary into
 * `apps/native-fps/bin/`, the launcher's third tier. A hard link, not a copy:
 * the two paths are then one file with one mtime, so the newest-wins rule
 * cannot pick between them and be wrong. It is relinked on every build because
 * cargo replaces its output by rename — the old link would otherwise still name
 * the *previous* build's inode, which is exactly the stale-binary failure this
 * exists to prevent.
 *
 * The debug and release links share one name (`bin/hassault`), so building one
 * profile retires the other's link rather than leaving two candidates whose
 * relative age decides what you play.
 */
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const crate = path.join(repoRoot, 'apps', 'native-fps');
const exe = process.platform === 'win32' ? '.exe' : '';

const argv = process.argv.slice(2);
const wantsDebug = argv.includes('--debug');
const passthrough = argv.filter((a) => a !== '--debug' && a !== '--release');
const profile = wantsDebug ? 'debug' : 'release';

const cargoArgs = [
  'build',
  ...(wantsDebug ? [] : ['--release']),
  '--manifest-path',
  path.join(crate, 'Cargo.toml'),
  ...passthrough,
];

console.log(`hassault: cargo ${cargoArgs.join(' ')}`);
// No `shell: true`: cargo is a real executable on every platform (unlike pnpm,
// which is a `.cmd` on Windows and does need one), and a shell here would put
// every path through another round of quoting for nothing.
const built = spawnSync('cargo', cargoArgs, { stdio: 'inherit' });
if (built.error) {
  console.error(`hassault: could not run cargo — ${built.error.message}`);
  console.error('hassault: install a Rust toolchain (https://rustup.rs) and try again.');
  process.exit(1);
}
if (built.status !== 0) {
  // cargo has already said what was wrong, in more detail than this could.
  process.exit(built.status ?? 1);
}

const source = path.join(crate, 'target', profile, `hassault-native${exe}`);
if (!fs.existsSync(source)) {
  console.error(`hassault: cargo reported success but ${source} is not there.`);
  process.exit(1);
}

const binDir = path.join(crate, 'bin');
const link = path.join(binDir, `hassault${exe}`);
fs.mkdirSync(binDir, { recursive: true });
// Removed rather than overwritten: a hard link cannot be repointed in place,
// and leaving the old one is how `bin/` ends up naming a build from last week.
fs.rmSync(link, { force: true });
let linked = 'hard link';
try {
  fs.linkSync(source, link);
} catch (e) {
  // Different volume, or a filesystem with no hard links. A copy still puts the
  // current build where the launcher looks; it just has its own mtime, so the
  // newest-wins rule is picking between two files rather than one.
  fs.copyFileSync(source, link);
  linked = `copy (hard link refused: ${e.code ?? e.message})`;
}

const rel = (p) => path.relative(repoRoot, p).replaceAll('\\', '/');
const { size, mtime } = fs.statSync(source);
console.log(
  `hassault: ${profile} build ready — ${rel(source)} ` +
    `(${(size / 1024 / 1024).toFixed(1)} MiB, ${mtime.toISOString()})`,
);
console.log(`hassault: linked into ${rel(link)} as a ${linked}`);
console.log('hassault: restart the game from the pane; the launcher runs the newest build it can find.');
