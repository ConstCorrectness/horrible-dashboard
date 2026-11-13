import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import {
  agentModule,
  BROWSER_CAPABILITIES,
  clubhouseModule,
  codeModule,
  commonsModule,
  dashboardModule,
  desktopCapabilities,
  isDesktopShell,
  editorModule,
  filesModule,
  flowModule,
  gamesModule,
  gitModule,
  githubModule,
  ideModule,
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
  musicModule,
  initMusic,
  keymapModule,
  libraryModule,
  docviewerModule,
  kbModule,
  loadKeymapOverrides,
  loadModulePackages,
  loadPlugins,
  loadSettings,
  initLobby,
  initNetwork,
  initNotifications,
  initRecordsWatch,
  initSocial,
  initTheme,
  initGraphicsQuality,
  bootFailed,
  bootReady,
  bootStep,
  briefingModule,
  notificationsModule,
  marketplaceModule,
  networkModule,
  peopleModule,
  socialModule,
  shareModule,
  initShare,
  hassaultModule,
  hostedModule,
  HubLogin,
  initWsOrigin,
  isHosted,
  probeHub,
  reportActivity,
  startActivityReporter,
  toastsStore,
  waitForInstance,
  recordsModule,
  browserModule,
  audioModule,
  initAudio,
  hardwareModule,
  storageModule,
  updatesModule,
  interpretabilityModule,
  interpSuiteModule,
  evalsModule,
  agentpediaModule,
  trajectoriesModule,
  labModule,
  discoverModule,
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
  terminalModule,
  databaseModule,
  trainingModule,
  datasetsModule,
  localtrackModule,
  docsModule,
  notebookModule,
  scriveModule,
  webmlModule,
  initWebmlRelay,
  visualizerModule,
} from '@horrible/core';
import { AppRoot } from '@horrible/ui';
import { modules as modulePackages } from 'virtual:horrible-modules';

import { resolveBackendOrigin } from './desktopBackend';
import { createDesktopGlobalShortcuts } from './desktopShortcuts';
import { createDesktopWindowControl } from './desktopWindow';

// Browser layout entry: browser capability set, built-in module registration,
// then installed plugins — awaited so restored layouts find plugin panels and
// widgets already registered. Backend down means a normal, pluginless boot.
async function boot(): Promise<void> {
  const root = document.getElementById('root');
  if (!root) throw new Error('Missing #root element');

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
  const desktop = isDesktopShell();

  // Before the hub probe too: hosted, the wait for a stopped instance to wake is
  // the longest step of the whole boot (tens of seconds on a cold machine), and
  // it used to run with nothing mounted at all — a black window until it ended.
  reactRoot.render(
    <StrictMode>
      <AppRoot appTitle="horrible-dashboard" />
    </StrictMode>,
  );

  // Behind the hosted hub (backend/hub/), signed out means every /api call 401s:
  // show the sign-in screen instead of booting over the user's real settings.
  // A plain backend has no /hub/me, so this is a no-op for `pnpm dev` and desktop.
  if (!desktop) {
    initWsOrigin((import.meta.env.VITE_WS_ORIGIN as string | undefined) ?? null);
    // probeHub never rejects (unreachable = local), so `undefined` cannot happen;
    // treating it as local keeps that true if it ever does.
    const hub = await bootStep('hub', 'Checking sign-in', () => probeHub());
    if (hub?.mode === 'signedOut') {
      reactRoot.render(
        <StrictMode>
          <HubLogin />
        </StrictMode>,
      );
      return;
    }
    if (hub?.mode === 'signedIn') {
      // Opening the page is input: report it first, so an instance the hub stopped
      // for idleness starts now. Then wait for it before loading anything from it.
      const up = await bootStep(
        'instance',
        'Starting your workspace',
        async () => {
          await reportActivity();
          return waitForInstance();
        },
        { hint: 'A workspace that has been idle can take up to a minute to wake.' },
      );
      if (!up) throw new Error('Your dashboard instance did not start. Reload to retry.');
    }
  }

  // Under the desktop shell it spawns the backend and tells us its origin; in the
  // browser the origin stays null (relative paths through the Vite proxy).
  if (desktop) {
    const origin = await bootStep('backend', 'Locating the backend', resolveBackendOrigin);
    if (origin) initBackendOrigin(origin);
  }

  // Same frontend, two layouts: under the shell claim the desktop capability set
  // (native dialogs, window control, tray…) and wire the native window control;
  // the browser gets the browser set and leaves the seam null.
  // The keymap has to know its host and platform before any binding resolves:
  // some chords the native shell can claim are browser-owned in a tab, so the two
  // ship different defaults for the same command.
  const platform = detectPlatform();
  initKeymapHost({ platform, host: desktop ? 'desktop' : 'browser' });

  if (desktop) {
    initCapabilities(desktopCapabilities());
    const control = createDesktopWindowControl();
    setWindowControl(control);
    // A reload wiped every pane session that owned a native browser overlay, but
    // the OS window (and its child webviews) survived it. Sweep before any pane
    // mounts: whatever is left over has no owner, and a pane in the restored
    // layout re-creates its surface for itself.
    void control.browserWebview?.closeAll().catch(() => {
      // Nothing to sweep, or the shell predates the command — not worth a toast.
    });
    setGlobalShortcuts(createDesktopGlobalShortcuts());
    // The app page opens no windows of its own (the shell refuses window.open and
    // target="_blank"), so route external-link clicks to the system browser.
    installExternalLinkBridge();
  } else {
    initCapabilities(BROWSER_CAPABILITIES);
  }
  await bootStep('modules', 'Registering modules', () => {
    registry.register(dashboardModule);
    registry.register(agentModule);
    registry.register(scratchModule);
    registry.register(mcpModule);
    registry.register(skillsModule);
    registry.register(browserModule);
    // A hosted instance does not serve Clubhouse (its sign-in drives a .NET helper
    // on the user's own machine) — see backend/hosted.py.
    if (!isHosted()) registry.register(clubhouseModule);
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
    registry.register(labModule);
    registry.register(discoverModule);
    registry.register(evalsModule);
    // After evals: an eval case runs through the same orchestrator loop, so a
    // trajectory is what an eval result is a grade *of*.
    registry.register(trajectoriesModule);
    // After trajectories: agentpedia owns no store and joins that one, plus
    // `agent_turns` and the telemetry ring. Registering it earlier would put a
    // reader of three modules ahead of the modules it reads.
    registry.register(agentpediaModule);
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
    // After the five views it hosts in its region strips — a region naming an
    // unregistered view renders as "Unknown pane".
    registry.register(ideModule);
    registry.register(replModule);
    registry.register(databaseModule);
    registry.register(libraryModule);
    registry.register(docviewerModule);
    registry.register(kbModule);
    registry.register(musicModule);
    registry.register(recordsModule);
    registry.register(researchModule);
    registry.register(searchModule);
    registry.register(trainingModule);
    registry.register(datasetsModule);
    registry.register(localtrackModule);
    registry.register(docsModule);
    registry.register(notebookModule);
    registry.register(scriveModule);
    registry.register(webmlModule);
    // After every module whose pane folded into it (interpretability, agentpedia,
    // llama.cpp, webml): the suite hosts their bodies as its rail sections.
    registry.register(interpSuiteModule);
    registry.register(visualizerModule);
    registry.register(flowModule);
    registry.register(gamesModule);
    // network / social / commons contribute services, settings and components but
    // **no panes** — People is where all three surface.
    registry.register(networkModule);
    registry.register(socialModule);
    // Share: sessions over the fabric. After social — a session invite is
    // addressed to a person, so the roster has to be registered first.
    registry.register(shareModule);
    registry.register(notificationsModule);
    // Spotlight's empty state: papers, headlines, quick settings. No pane.
    registry.register(briefingModule);
    registry.register(commonsModule);
    registry.register(peopleModule);
    registry.register(hassaultModule);
    if (isHosted()) registry.register(hostedModule);
  });

  // The saved settings and keybindings are two requests that depend on nothing
  // above and on neither loader below, so they go out now and are awaited where
  // their values are first needed. In sequence, boot waited for four round trips
  // one after another; the overrides are a plain key→value bag, so it does not
  // matter whether a plugin's declarations arrive before or after them.
  const settingsLoaded = bootStep('settings', 'Loading settings', loadSettings);
  const keymapLoaded = bootStep('keymap', 'Loading keybindings', loadKeymapOverrides);

  // Module packages (modules/<id>): registered after the built-ins above, enabled
  // ones only, dependencies first — and from here on they follow the backend, so a
  // module disabled from the marketplace leaves without a reload.
  await bootStep('module-packages', 'Loading module packages', () =>
    loadModulePackages(modulePackages),
  );
  await bootStep('plugins', 'Loading plugins', loadPlugins);
  // Seed the persisted overrides before the first render so widgets read correct
  // values. Backend down ⇒ defaults only.
  await settingsLoaded;
  // Straight after the overrides land and before the SHELL's first render: the
  // theme is a `data-theme` attribute on <html>, so applying it here means the
  // shell paints already themed rather than flashing the default and correcting
  // itself. (The boot splash above renders earlier and may correct once.)
  initTheme();
  initGraphicsQuality();
  // Same reason as settings: seed the user's overrides before the first render so
  // the palette and the Shortcuts pane show the bindings that will actually fire.
  await keymapLoaded;
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
    // The window's half of the `browser` chat provider: in-browser model rounds
    // relayed from the backend, plus what this window's GPU can run.
    initWebmlRelay();
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
    // Same reason as initSocial: an invitation to somebody's session arrives while
    // you are doing something else by definition, and a channel that only starts
    // when the share pane mounts would drop it on the floor.
    initShare();
    // The saved audio routing. Without it the mixer has no buses until its pane is
    // opened, and anything routed through it (a karaoke song) has nowhere to play.
    initAudio();
    // Music plays app-wide, not in a pane: start the player with the shell so an
    // agent's "play something" is audible before anyone opens the music pane.
    initMusic();
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

  // Behind the hub, input is what keeps this person's instance running (an open
  // tab alone is not), and what wakes it after the hub stopped it for idleness.
  if (isHosted()) {
    startActivityReporter(() =>
      toastsStore.add(
        'info',
        'Resuming',
        'Your workspace was paused while idle and is starting again.',
      ),
    );
  }
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
  // index.html's static splash counts as empty: it is only ever there when React
  // never rendered, and left alone it would animate forever over a dead boot.
  if (root && (!root.firstChild || root.querySelector('[data-static-splash]'))) {
    const detail = detailText;
    root.innerHTML =
      '<pre style="padding:16px;color:#f88;white-space:pre-wrap;font:13px/1.5 monospace">' +
      `Boot failed:\n${detail.replace(/[<>&]/g, (c) => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;' })[c] ?? c)}</pre>`;
  }
});
