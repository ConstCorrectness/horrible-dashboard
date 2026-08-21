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
  initKeymapPreset,
  installExternalLinkBridge,
  karaokeModule,
  keymapModule,
  layoutsModule,
  libraryModule,
  loadKeymapOverrides,
  loadPlugins,
  loadSettings,
  initLobby,
  initNetwork,
  initNotifications,
  initRecordsWatch,
  initSocial,
  initTheme,
  bootFailed,
  bootReady,
  bootStep,
  notificationsModule,
  marketplaceModule,
  networkModule,
  peopleModule,
  socialModule,
  hassaultModule,
  recordsModule,
  browserModule,
  audioModule,
  hardwareModule,
  storageModule,
  updatesModule,
  interpretabilityModule,
  evalsModule,
  trajectoriesModule,
  labModule,
  llamacppModule,
  observabilityModule,
  registry,
  replModule,
  researchModule,
  scratchModule,
  searchModule,
  mcpModule,
  skillsModule,
  setWindowControl,
  settingsModule,
  stubModule,
  terminalModule,
  databaseModule,
  trainingModule,
  localtrackModule,
  docsModule,
  notebookModule,
  visualizerModule,
} from '@horrible/core';
import { AppRoot } from '@horrible/ui';

import { isTauri, resolveBackendOrigin } from './tauriBackend';
import { createTauriGlobalShortcuts } from './tauriShortcuts';
import { createTauriWindowControl } from './tauriWindow';

// Browser layout entry: browser capability set, built-in module registration,
// then installed plugins — awaited so restored layouts find plugin panels and
// widgets already registered. Backend down means a normal, pluginless boot.
async function boot(): Promise<void> {
  const root = document.getElementById('root');
  if (!root) throw new Error('Missing #root element');

  // A per-workspace OS window (window.perWorkspace) opens straight into a target
  // workspace instead of the desktop. The desktop shell injects the id as a
  // global via an initialization script (window_open_workspace); the
  // `?workspace=` query is a fallback for browser/dev testing.
  const initialWorkspaceId =
    (window as { __HORRIBLE_WORKSPACE__?: string }).__HORRIBLE_WORKSPACE__ ??
    new URLSearchParams(window.location.search).get('workspace') ??
    undefined;

  // Rendered FIRST, so the boot splash can narrate the steps below rather than
  // the user staring at a white window while plugins load and the backend is
  // probed. `AppRoot` shows the splash until `bootReady()`; the shell itself
  // still mounts only after everything here has run, which is what keeps
  // workspace hydration from pruning panes whose modules are not registered yet.
  //
  // The splash therefore paints before `initTheme()` and may correct its colours
  // once settings land. That is a deliberately smaller cost than a blank window,
  // and the rule the theme comment below protects — that the *shell's* first
  // paint is already themed — is untouched.
  const reactRoot = createRoot(root);
  reactRoot.render(
    <StrictMode>
      <AppRoot appTitle="horrible-dashboard" initialWorkspaceId={initialWorkspaceId} />
    </StrictMode>,
  );

  // Under Tauri the shell spawns the backend and tells us its origin; in the
  // browser the origin stays null (relative paths through the Vite proxy).
  if (isTauri()) {
    const origin = await bootStep('backend', 'Locating the backend', resolveBackendOrigin);
    if (origin) initBackendOrigin(origin);
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
  await bootStep('modules', 'Registering modules', () => {
    registry.register(dashboardModule);
    registry.register(layoutsModule);
    registry.register(agentModule);
    registry.register(scratchModule);
    registry.register(mcpModule);
    registry.register(skillsModule);
    registry.register(browserModule);
    registry.register(clubhouseModule);
    // Before the modules whose defaults it decides (llama.cpp's build and offload,
    // the tracer's cap, what the training surface recommends).
    registry.register(hardwareModule);
    // Before every module that makes a sound: those register a strip on the
    // mixer at import, and a strip declared before the mixer exists is a strip
    // the routing matrix never shows.
    registry.register(audioModule);
    // Beside hardware: both are readings of the machine rather than panes, and the
    // two questions ("what is this box" / "where do my files go") are asked together.
    registry.register(storageModule);
    registry.register(updatesModule);
    registry.register(interpretabilityModule);
    // After interpretability so the Lab workspace tab sits beside it, and because the
    // Lab frame composes its panes more heavily than any other module's.
    registry.register(labModule);
    registry.register(evalsModule);
    // After evals: an eval case runs through the same orchestrator loop, so a
    // trajectory is what an eval result is a grade *of*.
    registry.register(trajectoriesModule);
    // The Lab is where you look at a model; this is where the node runs one.
    registry.register(llamacppModule);
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
    registry.register(karaokeModule);
    registry.register(recordsModule);
    registry.register(researchModule);
    registry.register(searchModule);
    registry.register(trainingModule);
    registry.register(localtrackModule);
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
  });

  await bootStep('plugins', 'Loading plugins', loadPlugins);
  // After plugins register their declarations, seed the persisted overrides so
  // widgets read correct values on first render. Backend down ⇒ defaults only.
  await bootStep('settings', 'Loading settings', loadSettings);
  // Straight after the overrides land and before the SHELL's first render: the
  // theme is a `data-theme` attribute on <html>, so applying it here means the
  // shell paints already themed rather than flashing the default and correcting
  // itself. (The boot splash above renders earlier and may correct once.)
  initTheme();
  // Same reason as settings: seed the user's overrides before the first render so
  // the palette and the Shortcuts pane show the bindings that will actually fire.
  await bootStep('keymap', 'Loading keybindings', loadKeymapOverrides);
  // After the overrides load, and subscribed: `keymap.preset` layers a named set
  // (i3) between the shipped defaults and the user's own rebinds.
  initKeymapPreset();
  // Shout in dev about any shipped binding this host will never deliver.
  if (import.meta.env.DEV) auditKeymap();
  // Push `global: true` bindings to the OS, and keep them in step with rebinds.
  // No-op without the `shortcuts.global` capability, i.e. in the browser.
  installGlobalShortcuts();

  // Everything below subscribes rather than fetching, so it is one step: none of
  // it blocks, and listing seven instant lines on the splash would imply a cost
  // that is not there.
  await bootStep('services', 'Connecting services', () => {
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
    // Same reason as initSocial, and the same bug it fixed: the records watch used to
    // start only when a records pane mounted, so an agent proposal filed while you
    // were looking at anything else was received by nobody and showed up on no
    // counter. Propose is explicitly the "safe to run unattended" write path, which
    // makes "you were elsewhere" its normal case rather than its edge case.
    initRecordsWatch();
    // Listen for permission-approval prompts the gate raises during a turn.
    initApprovalListener();
  });

  bootReady();
}

// Surface a boot failure as visible text rather than a blank white window.
//
// Two layers on purpose: `bootFailed` puts it on the splash (already mounted,
// themed, and showing which step got that far), and the raw innerHTML fallback
// stays for the case where React itself never got off the ground — the splash
// cannot report a failure that stopped it from existing.
void boot().catch((err: unknown) => {
  console.error('[boot] failed', err);
  const detailText = err instanceof Error ? (err.stack ?? err.message) : String(err);
  bootFailed(detailText);
  const root = document.getElementById('root');
  if (root && !root.firstChild) {
    const detail = detailText;
    root.innerHTML =
      '<pre style="padding:16px;color:#f88;white-space:pre-wrap;font:13px/1.5 monospace">' +
      `Boot failed:\n${detail.replace(/[<>&]/g, (c) => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;' })[c] ?? c)}</pre>`;
  }
});
