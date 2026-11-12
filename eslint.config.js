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
    // Module packages are repo-agnostic: one may be moved to its own repository and
    // come back as a git submodule with no code change. So a module reaches the host
    // only through @horrible/kernel and @horribledashboard/sdk, and another module
    // only through a service contract — never @horrible/core internals, never a
    // relative path out of its own directory. scripts/module-boundaries.mjs resolves
    // relative paths (which a glob can't) and checks the Python half the same way.
    files: ['modules/*/web/**/*.{ts,tsx}'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          patterns: [
            {
              group: ['@horrible/core', '@horrible/core/*', '@horrible/ui', '@horrible/ui/*'],
              message:
                'Import host APIs from @horrible/kernel; reach other modules through a service contract.',
            },
          ],
        },
      ],
    },
  },
  {
    // The browser agent's page scripts: each file is one function *expression*,
    // evaluated inside the page by both engines (Playwright's evaluate and the
    // native engine's CDP Runtime.evaluate), so its globals are the page's.
    files: ['backend/modules/browser/scripts/*.js'],
    languageOptions: {
      globals: {
        window: 'readonly',
        document: 'readonly',
        location: 'readonly',
        getComputedStyle: 'readonly',
        innerWidth: 'readonly',
        innerHeight: 'readonly',
        Node: 'readonly',
        NodeFilter: 'readonly',
        HTMLElement: 'readonly',
        URL: 'readonly',
      },
    },
    rules: { '@typescript-eslint/no-unused-expressions': 'off' },
  },
  {
    // Plugin-runtime shims: plain browser ESM served from public/, no build step.
    files: ['apps/*/public/plugin-runtime/**/*.js'],
    languageOptions: {
      globals: { window: 'readonly' },
    },
  },
);
