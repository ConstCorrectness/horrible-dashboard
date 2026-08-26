#!/usr/bin/env node
/**
 * Build the self-contained Python backend runtime that a packaged desktop app ships.
 *
 * ## Why this exists
 *
 * The desktop shell has always *supervised* the backend (`src-tauri/src/backend.rs`)
 * but never *carried* it: without a repo checkout it reported `unavailable` — "packaged
 * builds don't bundle the backend yet". Since the backend is the app's brain (agents,
 * data, every module's server side), that made a packaged install an empty shell. This
 * script produces the missing artifact.
 *
 * ## Why a real interpreter and not PyInstaller
 *
 * PyInstaller and friends freeze the import graph at build time. This backend's import
 * graph is not knowable at build time: `backend/sdk/loader.py` discovers plugins from
 * bundled dirs, `HORRIBLE_PLUGINS_DIR`, **and pip entry points**; the notebook/training
 * modules spawn kernels; `dash` execs user scripts. A frozen build breaks all of that
 * quietly — the app starts, and third-party plugins simply never appear.
 *
 * So the runtime is an ordinary relocatable CPython (python-build-standalone, the same
 * distribution `uv` manages) with the dependencies installed into its own
 * `site-packages`, and the `backend/` source tree beside it. Nothing about how imports
 * resolve changes between a checkout and a packaged install, which is the property
 * worth paying for.
 *
 * ## Layout it produces
 *
 *   <out>/
 *     python/        relocatable CPython + every dependency in its site-packages
 *     backend/       the backend source tree, verbatim
 *     pyproject.toml
 *     runtime.json   what was built, so the shell can report it
 *
 * `backend/` sits directly under `<out>` because module code navigates by
 * `Path(__file__).resolve().parents[3]` (see `backend/modules/hassault/routes.py`) and
 * expects that to be the directory *containing* `backend`. Keeping the checkout's shape
 * means those paths resolve to `<out>` instead of a repo root, rather than to something
 * that does not exist.
 *
 * Note `<out>` deliberately has **no `.git`**, so `backend/paths.py:repo_root()` returns
 * `None` and the data dir resolves to the per-OS location rather than to a `.data`
 * folder inside the installation — which on Windows is under Program Files and not
 * writable. That is not incidental; it is the reason `repo_root()` tests for `.git`
 * rather than for `pyproject.toml` alone.
 *
 * ## Usage
 *
 *   node scripts/build-backend-runtime.mjs [--out <dir>] [--clean]
 *
 * Default output is `apps/desktop/src-tauri/backend-runtime`, which is what
 * `tauri.conf.json` lists under `bundle.resources`. Git-ignored: it is a build output
 * measured in gigabytes.
 */
import { execFileSync } from 'node:child_process';
import {
  cpSync,
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
  statSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const REPO = resolve(dirname(fileURLToPath(import.meta.url)), '..');

/**
 * The interpreter the runtime ships.
 *
 * Pinned to a patch version, not a `3.12` range: the whole point of shipping an
 * interpreter is that the app runs on the Python it was tested against, and a range
 * would let two platforms in the same release carry two different ones.
 * `requires-python` in pyproject.toml is the floor this has to satisfy.
 */
const PYTHON_VERSION = '3.12.12';

/**
 * Removed after the dependencies are installed.
 *
 * Conservative on purpose — every entry is something nothing can import at runtime.
 * `include/` and `libs/` exist to *compile* C extensions, which happens at build time
 * here if at all; `Lib/test` is CPython's own test suite. Notably absent: `.dist-info`
 * directories, which look like metadata and are not — `backend/sdk/loader.py` discovers
 * backend plugins through `importlib.metadata.entry_points()`, so deleting them would
 * silently remove every pip-installed plugin from the app.
 */
const PRUNE_DIRS = ['include', 'libs', join('Lib', 'test'), join('lib', 'python3.12', 'test')];

/**
 * The committed placeholder inside the output directory.
 *
 * `bundle.resources` globs this folder, and a glob that matches nothing is a bundler
 * error — so the file has to exist even for a developer who has never built a runtime.
 * This script clears the directory first, which deletes it, so it is written back at
 * the end: without that, running the build once and then deleting the output leaves a
 * *tracked file missing* and the next `tauri build` failing for a reason that has
 * nothing to do with the change being built.
 */
const PLACEHOLDER = '.gitkeep';

/** Never copied out of the checkout: build noise, tests, and scratch work. */
const SKIP_SOURCE = new Set(['__pycache__', '.pytest_cache', 'tests', 'scratch', '.ruff_cache']);

function log(msg) {
  console.log(`[backend-runtime] ${msg}`);
}

function run(cmd, args, opts = {}) {
  return execFileSync(cmd, args, { stdio: 'inherit', cwd: REPO, ...opts });
}

function capture(cmd, args, opts = {}) {
  return execFileSync(cmd, args, { encoding: 'utf8', cwd: REPO, ...opts }).trim();
}

function parseArgs(argv) {
  const args = { out: join(REPO, 'apps', 'desktop', 'src-tauri', 'backend-runtime'), clean: false };
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === '--out') {
      args.out = resolve(argv[++i]);
    } else if (argv[i] === '--clean') {
      args.clean = true;
    } else {
      throw new Error(`unknown argument: ${argv[i]}`);
    }
  }
  return args;
}

/** Total bytes under `dir`, for the one number anybody actually wants at the end. */
function dirSize(dir) {
  let total = 0;
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) total += dirSize(path);
    else if (entry.isFile()) total += statSync(path).size;
  }
  return total;
}

/** Every `__pycache__` under `dir`. Written by the install, useless once relocated. */
function prunePycache(dir) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue;
    const path = join(dir, entry.name);
    if (entry.name === '__pycache__') rmSync(path, { recursive: true, force: true });
    else prunePycache(path);
  }
}

/**
 * Download a relocatable CPython into `<out>/python`.
 *
 * Installed into a scratch dir we own rather than the machine's shared uv store, then
 * copied: the shared store is where a developer's other projects point, and pruning
 * `Lib/test` out of it would be reaching into somebody else's interpreter.
 */
function installPython(outDir) {
  const staging = mkdtempSync(join(tmpdir(), 'hd-python-'));
  log(`fetching CPython ${PYTHON_VERSION}`);
  run('uv', ['python', 'install', '--managed-python', '--no-bin', PYTHON_VERSION], {
    env: { ...process.env, UV_PYTHON_INSTALL_DIR: staging },
  });

  // uv also leaves a minor-version alias (`cpython-3.12-<platform>`) beside the real
  // directory. Match on the **pinned patch version** rather than trying to tell the two
  // apart structurally: on Windows the alias is a junction, which Node reports as an
  // ordinary directory and not as a symlink, so a `!isSymbolicLink()` filter finds two
  // candidates and a `[0]` would pick whichever the filesystem listed first.
  const prefix = `cpython-${PYTHON_VERSION}-`;
  const dists = readdirSync(staging, { withFileTypes: true }).filter(
    (e) => e.isDirectory() && e.name.startsWith(prefix),
  );
  if (dists.length !== 1) {
    throw new Error(
      `expected exactly one ${prefix}* dist in ${staging}, found ${dists.length}: ` +
        readdirSync(staging).join(', '),
    );
  }

  const pythonDir = join(outDir, 'python');
  log(`copying ${dists[0].name} -> python/`);
  cpSync(join(staging, dists[0].name), pythonDir, { recursive: true, dereference: true });
  rmSync(staging, { recursive: true, force: true });

  // python-build-standalone ships PEP 668's `EXTERNALLY-MANAGED` marker, and uv
  // refuses to install into an interpreter carrying it. The marker exists to stop
  // people from installing into an interpreter *something else owns* — a distro's
  // `/usr/bin/python3`, or uv's shared managed store. This is a private copy whose
  // entire purpose is to hold these packages and which nothing else will ever see, so
  // the claim is simply false here and is removed rather than overridden with
  // `--break-system-packages`, a flag whose name would badly mislead the next reader.
  for (const marker of [
    join(pythonDir, 'Lib', 'EXTERNALLY-MANAGED'),
    join(pythonDir, 'lib', `python${PYTHON_VERSION.split('.').slice(0, 2).join('.')}`, 'EXTERNALLY-MANAGED'),
  ]) {
    rmSync(marker, { force: true });
  }
  return pythonDir;
}

/** The interpreter inside a copied dist, per platform. */
function interpreterPath(pythonDir) {
  const win = join(pythonDir, 'python.exe');
  return existsSync(win) ? win : join(pythonDir, 'bin', 'python3');
}

/**
 * Install every runtime dependency into the shipped interpreter.
 *
 * Resolved from `uv.lock` via `uv export` rather than from `pyproject.toml` directly, so
 * the runtime carries the exact versions the lockfile pins and the exact set CI tested
 * — `--frozen` fails rather than silently re-resolving if the lockfile is stale.
 *
 * `--no-dev` and no extras: extras are opt-in by design (torch alone is ~460 MB), and a
 * packaged app that shipped all of them would be several gigabytes for features most
 * installs never touch. Every extra is lazy-imported and answers with an install hint.
 */
function installDependencies(python) {
  const staging = mkdtempSync(join(tmpdir(), 'hd-reqs-'));
  const requirements = join(staging, 'requirements.txt');
  log('exporting locked requirements');
  const exported = capture('uv', [
    'export',
    '--frozen',
    '--no-dev',
    '--no-emit-project',
    '--format',
    'requirements-txt',
  ]);
  writeFileSync(requirements, exported, 'utf8');

  log('installing dependencies into the shipped interpreter (this takes a few minutes)');
  run('uv', ['pip', 'install', '--python', python, '--requirement', requirements]);
  rmSync(staging, { recursive: true, force: true });
}

/** Copy the backend source tree and the manifest that sits beside it. */
function copySource(outDir) {
  log('copying backend/');
  cpSync(join(REPO, 'backend'), join(outDir, 'backend'), {
    recursive: true,
    filter: (src) => {
      const name = src.split(/[\\/]/).pop();
      return !SKIP_SOURCE.has(name) && !name.endsWith('.pyc');
    },
  });
  // Read by `backend/version.py` — which is what keys the hassault client download, so
  // a runtime without it would resolve every install against the fallback literal.
  cpSync(join(REPO, 'pyproject.toml'), join(outDir, 'pyproject.toml'));
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  const outDir = args.out;

  // Read the committed placeholder *before* clearing, so it can be written back
  // verbatim afterwards and stays a single source of truth rather than a second copy
  // of its own explanation living in this script.
  const placeholderPath = join(outDir, PLACEHOLDER);
  const placeholderText = existsSync(placeholderPath)
    ? readFileSync(placeholderPath, 'utf8')
    : "Placeholder so tauri.conf.json's bundle.resources glob always matches a file.\n";

  if (args.clean || existsSync(outDir)) {
    log(`clearing ${outDir}`);
    rmSync(outDir, { recursive: true, force: true });
  }
  mkdirSync(outDir, { recursive: true });

  const pythonDir = installPython(outDir);
  const python = interpreterPath(pythonDir);
  installDependencies(python);
  copySource(outDir);

  log('pruning');
  for (const relative of PRUNE_DIRS) {
    rmSync(join(pythonDir, relative), { recursive: true, force: true });
  }
  prunePycache(outDir);

  // **The smoke test, and the manifest, in one step.** Asking the shipped interpreter
  // for the app version imports `backend.version` *through the relocated tree* — so a
  // runtime that cannot import its own backend fails here, at build time, instead of
  // shipping and reporting `unavailable` on somebody's machine. It also proves the
  // version resolves to the real one rather than to `FALLBACK_VERSION`, which is what
  // every prebuilt-client download is keyed on.
  log('verifying the relocated runtime can import the backend');
  const appVersion = capture(python, ['-c', 'from backend.version import app_version; print(app_version())'], {
    cwd: outDir,
  });
  if (!/^\d+\.\d+\.\d+/.test(appVersion)) {
    throw new Error(`the runtime reported an implausible app version: ${JSON.stringify(appVersion)}`);
  }

  writeFileSync(
    join(outDir, 'runtime.json'),
    `${JSON.stringify(
      {
        pythonVersion: PYTHON_VERSION,
        appVersion,
        builtAt: new Date().toISOString(),
        platform: `${process.platform}-${process.arch}`,
      },
      null,
      2,
    )}\n`,
    'utf8',
  );

  // Restore what the initial clear removed. See PLACEHOLDER.
  writeFileSync(join(outDir, PLACEHOLDER), placeholderText, 'utf8');

  const gb = dirSize(outDir) / 1024 ** 3;
  log(`done: ${outDir} (${gb.toFixed(2)} GB)`);
}

main();
