/**
 * Build preset for plugin authors. Produces a single ESM bundle with `react`,
 * `react/jsx-runtime`, and `@horribledashboard/sdk` left external and rewritten to the
 * host-served shim URLs (`/plugin-runtime/*.js`), so every plugin shares the
 * host's React instance and SDK runtime. Bundling your own React breaks hooks.
 *
 * Usage in a plugin's vite.config.ts:
 *
 *   import { defineConfig } from 'vite';
 *   import { horriblePluginViteConfig } from '@horribledashboard/sdk/vite';
 *   export default defineConfig(horriblePluginViteConfig({ entry: 'src/index.tsx' }));
 */

/** Structurally compatible with Vite's `UserConfig` — no vite dependency needed. */
export interface HorriblePluginViteConfig {
  build: {
    lib: {
      entry: string;
      formats: 'es'[];
      fileName: () => string;
    };
    rollupOptions: {
      external: string[];
      output: {
        paths: Record<string, string>;
      };
    };
  };
}

export function horriblePluginViteConfig(opts: { entry: string }): HorriblePluginViteConfig {
  return {
    build: {
      lib: {
        entry: opts.entry,
        formats: ['es'],
        fileName: () => 'index.js',
      },
      rollupOptions: {
        external: ['react', 'react/jsx-runtime', '@horribledashboard/sdk'],
        output: {
          paths: {
            react: '/plugin-runtime/react.js',
            'react/jsx-runtime': '/plugin-runtime/jsx-runtime.js',
            '@horribledashboard/sdk': '/plugin-runtime/sdk.js',
          },
        },
      },
    },
  };
}
