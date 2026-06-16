import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import {
  agentModule,
  BROWSER_CAPABILITIES,
  clubhouseModule,
  dashboardModule,
  editorModule,
  filesModule,
  initAgentManifestSync,
  initApprovalListener,
  initBackendOrigin,
  initCapabilities,
  layoutsModule,
  loadPlugins,
  loadSettings,
  marketplaceModule,
  observabilityModule,
  registry,
  replModule,
  scratchModule,
  settingsModule,
  stubModule,
  terminalModule,
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
  registry.register(layoutsModule);
  registry.register(agentModule);
  registry.register(scratchModule);
  registry.register(clubhouseModule);
  registry.register(observabilityModule);
  registry.register(marketplaceModule);
  registry.register(settingsModule);
  registry.register(editorModule);
  registry.register(filesModule);
  registry.register(terminalModule);
  registry.register(replModule);
  // Dev-only agent-tool reference/validation stub (see agent-tools.md).
  if (import.meta.env.DEV) registry.register(stubModule);

  await loadPlugins();
  // After plugins register their declarations, seed the persisted overrides so
  // widgets read correct values on first render. Backend down ⇒ defaults only.
  await loadSettings();

  // Push the agent capability manifest (agent commands + widget/panel agentTools)
  // to the backend orchestrator, now and on every reconnect / registry change.
  initAgentManifestSync();
  // Listen for permission-approval prompts the gate raises during a turn.
  initApprovalListener();

  const root = document.getElementById('root');
  if (!root) throw new Error('Missing #root element');

  createRoot(root).render(
    <StrictMode>
      <AppShell appTitle="horrible-dashboard" />
    </StrictMode>,
  );
}

void boot();
