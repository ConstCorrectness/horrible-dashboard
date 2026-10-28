import { defineConfig } from 'vitest/config';

// Unit tests cover the shell's Electron-free modules (backend supervisor logic, the
// CDP allowlist, accelerator spelling, minisign); everything that needs a running
// Electron is exercised by scripts/desktop-smoke.mjs against the real app.
export default defineConfig({
  test: { include: ['test/**/*.test.ts'], environment: 'node' },
});
