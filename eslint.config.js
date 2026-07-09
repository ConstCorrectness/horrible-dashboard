import js from '@eslint/js';
import tseslint from 'typescript-eslint';

export default tseslint.config(
  {
    ignores: [
      '**/dist/**',
      '**/node_modules/**',
      '**/src-tauri/**',
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
      globals: { console: 'readonly', process: 'readonly', URL: 'readonly' },
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
