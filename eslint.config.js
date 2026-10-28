import js from '@eslint/js';
import tseslint from 'typescript-eslint';

export default tseslint.config(
  {
    ignores: [
      '**/dist/**',
      '**/node_modules/**',
      'apps/desktop/release/**', // electron-builder output (unpacked app, installers)
      '**/.venv/**',
      '.data/**', // runtime data dir (e.g. the notebook module's managed kernel venv)
      '.data-*/**', // sibling data dirs (e.g. .data-peer2/ for the peer fabric)
      '.claude/**', // agent worktrees (each with its own venv/node_modules) live here
      'website/build/**',
      'website/.docusaurus/**',
    ],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ['scripts/**/*.mjs'],
    languageOptions: {
      globals: {
        console: 'readonly',
        process: 'readonly',
        URL: 'readonly',
        Blob: 'readonly',
        Buffer: 'readonly',
      },
    },
  },
  {
    // These drive the desktop app: Node 22's timers, fetch and WebSocket in the script,
    // and the page's own globals inside the functions handed to the page to run.
    files: ['scripts/desktop-smoke.mjs', 'scripts/desktop-bench.mjs'],
    languageOptions: {
      globals: {
        fetch: 'readonly',
        WebSocket: 'readonly',
        setTimeout: 'readonly',
        clearTimeout: 'readonly',
        window: 'readonly',
        document: 'readonly',
        location: 'readonly',
      },
    },
  },
  {
    // Plugin-runtime shims: plain browser ESM served from public/, no build step.
    files: ['apps/*/public/plugin-runtime/**/*.js'],
    languageOptions: {
      globals: { window: 'readonly' },
    },
  },
);
