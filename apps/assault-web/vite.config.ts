import react from '@vitejs/plugin-react';
import { copyFileSync, existsSync, mkdirSync, readdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { defineConfig, type Plugin } from 'vite';

// Load workspace root .env if running directly without scripts/dev.mjs wrapper
const rootEnv = resolve(__dirname, '../../.env');
if (existsSync(rootEnv)) {
  try {
    process.loadEnvFile(rootEnv);
  } catch (e) {
    console.warn('Failed to load root .env file:', e);
  }
}

/** The dashboard's public dir, which owns the game's models. Shared, not copied. */
const SHARED_PUBLIC = resolve(__dirname, '../web/public');

/**
 * Which of those files the game actually loads: the operator, arms, weapons and
 * grenades (`hassault-*.glb`), the modelled maps (`hd_*.glb`), and the icon.
 *
 * The rest of that directory is the dashboard's — its avatar, dance clips and
 * plugin runtime — and is about a fifth of it by size. A static host serves
 * every file it is given, so shipping them is bytes on a CDN for nothing.
 */
const GAME_ASSET = /^(hassault-.+\.glb|hd_.+\.glb|logo\.svg|favicon\.ico)$/;

/**
 * Copy only the game's files into the build.
 *
 * Build-only: `vite dev` serves `SHARED_PUBLIC` whole (see `publicDir` below),
 * which costs nothing locally and keeps a new model visible without a restart.
 */
function gameAssetsOnly(): Plugin {
  let outDir = '';
  return {
    name: 'assault-web:game-assets',
    apply: 'build',
    configResolved(config) {
      outDir = resolve(config.root, config.build.outDir);
    },
    writeBundle() {
      mkdirSync(outDir, { recursive: true });
      const copied = readdirSync(SHARED_PUBLIC).filter((name) => GAME_ASSET.test(name));
      for (const name of copied) {
        copyFileSync(resolve(SHARED_PUBLIC, name), resolve(outDir, name));
      }
      // A build with no models renders every map as its procedural fallback and
      // every weapon as nothing — and nothing about that fails. Fail here instead.
      if (!copied.some((name) => name.startsWith('hassault-'))) {
        throw new Error(`assault-web: no game models found in ${SHARED_PUBLIC}`);
      }
    },
  };
}

export default defineConfig(({ command }) => ({
  plugins: [react(), gameAssetsOnly()],
  publicDir: command === 'serve' ? SHARED_PUBLIC : false,
  build: {
    chunkSizeWarningLimit: 1500,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('node_modules/three') || id.includes('three/examples')) {
            return 'vendor-three';
          }
          if (id.includes('node_modules/@dimforge/rapier3d')) {
            return 'vendor-rapier';
          }
          if (id.includes('node_modules/react/') || id.includes('node_modules/react-dom/')) {
            return 'vendor-react';
          }
        },
      },
    },
  },
  server: {
    host: process.env.HORRIBLE_DEV_HOST || '127.0.0.1',
    port: Number(process.env.ASSAULT_PORT) || 5180,
    strictPort: false,
    proxy: {
      '/api': {
        target: `http://127.0.0.1:${process.env.HORRIBLE_DEV_BACKEND_PORT || '8000'}`,
        xfwd: true,
      },
      '/ws': {
        target: `ws://127.0.0.1:${process.env.HORRIBLE_DEV_BACKEND_PORT || '8000'}`,
        ws: true,
      },
      '/hassault-ws': {
        target: `ws://127.0.0.1:${process.env.HORRIBLE_GAME_SERVER_PORT || '9090'}`,
        ws: true,
      },
    },
  },
}));
