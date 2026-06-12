import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import {
  agentModule,
  BROWSER_CAPABILITIES,
  clubhouseModule,
  dashboardModule,
  initBackendOrigin,
  initCapabilities,
  loadPlugins,
  marketplaceModule,
  observabilityModule,
  registry,
  scratchModule,
} from '@horrible/core';
import { AppShell } from '@horrible/ui';

import { isTauri, resolveBackendOrigin } from './tauriBackend';

// Browser layout entry: browser capability set, built-in module registration,
// then installed plugins — awaited so restored layouts find plugin panels and
// widgets already registered. Backend down means a normal, pluginless boot.
async function boot(): Promise<void> {
  // Under Tauri the shell spawns the backend and tells us its origin; in the
  // browser the origin stays null (relative paths through the Vite proxy).
  if (isTauri()) {
    initBackendOrigin(await resolveBackendOrigin());
  }

  initCapabilities(BROWSER_CAPABILITIES);
  registry.register(dashboardModule);
  registry.register(agentModule);
  registry.register(scratchModule);
  registry.register(clubhouseModule);
  registry.register(observabilityModule);
  registry.register(marketplaceModule);

  await loadPlugins();

  const root = document.getElementById('root');
  if (!root) throw new Error('Missing #root element');

  createRoot(root).render(
    <StrictMode>
      <AppShell appTitle="horrible-dashboard" />
    </StrictMode>,
  );
}

void boot();
