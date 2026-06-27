import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import {
  agentModule,
  BROWSER_CAPABILITIES,
  clubhouseModule,
  dashboardModule,
  editorModule,
  filesModule,
  flowModule,
  initAgentManifestSync,
  initAgentRelay,
  initApprovalListener,
  initBackendOrigin,
  initCapabilities,
  layoutsModule,
  loadPlugins,
  loadSettings,
  initNetwork,
  marketplaceModule,
  networkModule,
  observabilityModule,
  registry,
  replModule,
  scratchModule,
  settingsModule,
  stubModule,
  terminalModule,
  vectordbModule,
  visualizerModule,
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
  registry.register(vectordbModule);
  registry.register(visualizerModule);
<<<<<<< HEAD
  registry.register(flowModule);
=======
  registry.register(networkModule);
>>>>>>> 3f35996b5155259eab463daeb38527646abf178d
  // Dev-only agent-tool reference/validation stub (see agent-tools.md).
  if (import.meta.env.DEV) registry.register(stubModule);

  await loadPlugins();
  // After plugins register their declarations, seed the persisted overrides so
  // widgets read correct values on first render. Backend down ⇒ defaults only.
  await loadSettings();

  // Push the agent capability manifest (agent commands + widget/panel agentTools)
  // to the backend orchestrator, now and on every reconnect / registry change.
  initAgentManifestSync();
<<<<<<< HEAD
  // Always-on tool-call relay: executes tools the backend relays for any chat turn
  // OR flow run (so flow Tool nodes and agent nodes can drive tools).
  initAgentRelay();
=======
  // Subscribe to the peer fabric so presence syncs before the Peers widget opens.
  initNetwork();
>>>>>>> 3f35996b5155259eab463daeb38527646abf178d
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
