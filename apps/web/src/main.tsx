import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import {
  agentModule,
  BROWSER_CAPABILITIES,
  clubhouseModule,
  codeModule,
  commonsModule,
  dashboardModule,
  DESKTOP_CAPABILITIES,
  editorModule,
  filesModule,
  flowModule,
  gamesModule,
  gitModule,
  githubModule,
  initAgentManifestSync,
  initAgentRelay,
  initApprovalListener,
  auditKeymap,
  detectPlatform,
  installGlobalShortcuts,
  setGlobalShortcuts,
  initBackendOrigin,
  initCapabilities,
  initCommons,
  explorerModule,
  initKeymapHost,
  installExternalLinkBridge,
  keymapModule,
  layoutsModule,
  libraryModule,
  loadKeymapOverrides,
  loadPlugins,
  loadSettings,
  initLobby,
  initNetwork,
  initNotifications,
  initSocial,
  notificationsModule,
  marketplaceModule,
  networkModule,
  peopleModule,
  socialModule,
  hassaultModule,
  recordsModule,
  browserModule,
  interpretabilityModule,
  observabilityModule,
  registry,
  replModule,
  researchModule,
  scratchModule,
  searchModule,
  mcpModule,
  setWindowControl,
  settingsModule,
  stubModule,
  terminalModule,
  databaseModule,
  trainingModule,
  docsModule,
  notebookModule,
  visualizerModule,
} from '@horrible/core';
import { AppShell } from '@horrible/ui';

import { isTauri, resolveBackendOrigin } from './tauriBackend';
import { createTauriGlobalShortcuts } from './tauriShortcuts';
import { createTauriWindowControl } from './tauriWindow';

// Browser layout entry: browser capability set, built-in module registration,
// then installed plugins — awaited so restored layouts find plugin panels and
// widgets already registered. Backend down means a normal, pluginless boot.
async function boot(): Promise<void> {
  // Under Tauri the shell spawns the backend and tells us its origin; in the
  // browser the origin stays null (relative paths through the Vite proxy).
  if (isTauri()) {
    initBackendOrigin(await resolveBackendOrigin());
  }

  // Same frontend, two layouts: under Tauri claim the desktop capability set
  // (native dialogs, window control, tray…) and wire the native window control;
  // the browser gets the browser set and leaves the seam null.
  // The keymap has to know its host and platform before any binding resolves:
  // `mod+1..9` is workspace switching on the desktop and unreachable browser tab
  // switching in a tab, so the two ship different defaults for the same command.
  initKeymapHost({ platform: detectPlatform(), host: isTauri() ? 'desktop' : 'browser' });

  if (isTauri()) {
    initCapabilities(DESKTOP_CAPABILITIES);
    setWindowControl(createTauriWindowControl());
    setGlobalShortcuts(createTauriGlobalShortcuts());
    // The webview can't spawn browser windows (window.open / target="_blank" are
    // silent no-ops), so route external-link clicks to the system browser.
    installExternalLinkBridge();
  } else {
    initCapabilities(BROWSER_CAPABILITIES);
  }
  registry.register(dashboardModule);
  registry.register(layoutsModule);
  registry.register(agentModule);
  registry.register(scratchModule);
  registry.register(mcpModule);
  registry.register(browserModule);
  registry.register(clubhouseModule);
  registry.register(interpretabilityModule);
  registry.register(observabilityModule);
  registry.register(marketplaceModule);
  registry.register(settingsModule);
  registry.register(keymapModule);
  registry.register(editorModule);
  // Explorer hosts the contributed browsers; the five modules that used to each
  // ship a left-dock list now contribute a section instead.
  registry.register(explorerModule);
  registry.register(filesModule);
  registry.register(codeModule);
  registry.register(gitModule);
  registry.register(githubModule);
  registry.register(terminalModule);
  registry.register(replModule);
  registry.register(databaseModule);
  registry.register(libraryModule);
  registry.register(recordsModule);
  registry.register(researchModule);
  registry.register(searchModule);
  registry.register(trainingModule);
  registry.register(docsModule);
  registry.register(notebookModule);
  registry.register(visualizerModule);
  registry.register(flowModule);
  registry.register(gamesModule);
  // network / social / commons contribute services, settings and components but
  // **no panes** — People is where all three surface.
  registry.register(networkModule);
  registry.register(socialModule);
  registry.register(notificationsModule);
  registry.register(commonsModule);
  registry.register(peopleModule);
  registry.register(hassaultModule);
  // Dev-only agent-tool reference/validation stub (see agent-tools.md).
  if (import.meta.env.DEV) registry.register(stubModule);

  await loadPlugins();
  // After plugins register their declarations, seed the persisted overrides so
  // widgets read correct values on first render. Backend down ⇒ defaults only.
  await loadSettings();
  // Same reason as settings: seed the user's overrides before the first render so
  // the palette and the Shortcuts pane show the bindings that will actually fire.
  await loadKeymapOverrides();
  // Shout in dev about any shipped binding this host will never deliver.
  if (import.meta.env.DEV) auditKeymap();
  // Push `global: true` bindings to the OS, and keep them in step with rebinds.
  // No-op without the `shortcuts.global` capability, i.e. in the browser.
  installGlobalShortcuts();

  // Push the agent capability manifest (agent commands + widget/panel agentTools)
  // to the backend orchestrator, now and on every reconnect / registry change.
  initAgentManifestSync();
  // Always-on tool-call relay: executes tools the backend relays for any chat turn
  // OR flow run (so flow Tool nodes and agent nodes can drive tools).
  initAgentRelay();
  // Subscribe to the peer fabric so presence syncs before the Peers widget opens.
  initNetwork();
  initLobby();
  initCommons();
  // The roster, and the notifications the fabric raises against it. Both belong at
  // boot rather than on a pane mount: `initSocial` used to run only when the
  // Friends tab was opened, so a friend request that arrived before you went
  // looking for one was never announced — and a watch that fires ("Andrew is
  // online") has to reach you while you are doing something else, or it is not a
  // notification.
  initSocial();
  initNotifications();
  // Listen for permission-approval prompts the gate raises during a turn.
  initApprovalListener();

  const root = document.getElementById('root');
  if (!root) throw new Error('Missing #root element');

  // A per-workspace OS window (window.perWorkspace) opens straight into a target
  // workspace instead of home. The desktop shell injects the id as a global via
  // an initialization script (window_open_workspace); the `?workspace=` query is
  // a fallback for browser/dev testing.
  const initialWorkspaceId =
    (window as { __HORRIBLE_WORKSPACE__?: string }).__HORRIBLE_WORKSPACE__ ??
    new URLSearchParams(window.location.search).get('workspace') ??
    undefined;

  createRoot(root).render(
    <StrictMode>
      <AppShell appTitle="horrible-dashboard" initialWorkspaceId={initialWorkspaceId} />
    </StrictMode>,
  );
}

// Surface a boot failure as visible text rather than a blank white window.
void boot().catch((err: unknown) => {
  console.error('[boot] failed', err);
  const root = document.getElementById('root');
  if (root) {
    const detail = err instanceof Error ? (err.stack ?? err.message) : String(err);
    root.innerHTML =
      '<pre style="padding:16px;color:#f88;white-space:pre-wrap;font:13px/1.5 monospace">' +
      `Boot failed:\n${detail.replace(/[<>&]/g, (c) => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;' })[c] ?? c)}</pre>`;
  }
});
