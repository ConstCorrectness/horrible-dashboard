import js from '@eslint/js';
import tseslint from 'typescript-eslint';

export default tseslint.config(
  {
    ignores: [
      '**/dist/**',
      '**/node_modules/**',
      '**/src-tauri/**',
      '.venv/**',
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
