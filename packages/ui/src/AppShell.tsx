import { useEffect, useState, useSyncExternalStore } from 'react';
import {
  closeTransientChrome,
  DEFAULT_ESCAPE_HOLD_MS,
  dialogsStore,
  isPresenting,
  presentPane,
  getSetting,
  hasCapability,
  installKeymap,
  installUpdate,
  layoutStore,
  onExternalOpenFailed,
  startAutoUpdateChecks,
  toggleAppFullscreen,
  registry,
  setBackdrop,
  setDesktopMode,
  setSetting,
  setShellView,
  SymbolSearchModal,
  toastsStore,
  windowControl,
  type ShellView,
} from '@horrible/core';

import { ApprovalPrompts } from './ApprovalPrompts';
import { ConnectorDialog } from './home/ConnectorDialog';
import { NAME_SETTING_KEY, SETUP_DISMISSED_KEY } from './home/constants';
import { CaptureHud } from './CaptureHud';
import { Spotlight } from './Spotlight';
import { ContextMenuLayer } from './overlay/ContextMenu';
import { Dialogs } from './Dialogs';
import { Toasts } from './Toasts';
import { Desktop } from './desktop/Desktop';
import { Oobe } from './desktop/Oobe';
import { OOBE_COMPLETE_KEY } from './desktop/constants';
import { Taskbar } from './desktop/Taskbar';
import { registerDesktopModule } from './desktop/module';
import { WindowLayer } from './desktop/WindowLayer';
import { Frame } from './layout/Frame';
import { Minibuffer } from './layout/Minibuffer';
import { installFrameShell, openPaneWhenReady, switchWorkspaceWhenReady } from './layout/install';
import { WindowResizeHandles } from './layout/WindowChrome';
import { DetachedTitlebar, WorkspaceTabs } from './layout/WorkspaceTabs';
import './styles.css';
import './desktop/desktop.css';
import './desktop/backdrops/backdrops.css';
import './desktop/taskbar/taskbar.css';
import './desktop/oobe.css';
import './overlay/docs.css';

/**
 * The shell: the workspace tab strip on top (always visible, Blender-style),
 * then either the `desktop` (a backdrop, with windows over it) or the frame (the
 * tiling workspace engine — activity rail, docks, center area grid). Global
 * chrome (palette, toasts, dialogs, keybinding dispatch) lives here.
 *
 * The old `home` view is gone: it was a second top-level surface doing the
 * desktop's job, and it survives as the `splash` backdrop instead. See
 * docs/architecture/layout-shell.mdx and docs/architecture/desktop-shell.mdx.
 */
export function AppShell({
  appTitle,
  initialWorkspaceId,
}: {
  appTitle: string;
  /** When set (a per-workspace OS window), boot into this workspace, not home. */
  initialWorkspaceId?: string;
}) {
  // First run goes through setup before the desktop. Read once, at mount, from
  // the settings already loaded by `boot()` — reading it reactively would yank
  // the user back into the wizard the moment the settings store republished.
  const [view, setView] = useState<ShellView>(() =>
    !initialWorkspaceId && getSetting<boolean>(OOBE_COMPLETE_KEY) !== true ? 'oobe' : 'desktop',
  );
  const [paletteOpen, setPaletteOpen] = useState(false);

  useEffect(() => {
    document.title = appTitle;
  }, [appTitle]);

  // A link the app cancelled and then failed to open itself. Nothing is on the
  // user's screen at this point and no error was raised anywhere, so say so —
  // and hand over the URL rather than another link, since links are what just
  // failed. Persistent (duration 0): it carries the only copy of the address.
  useEffect(
    () =>
      onExternalOpenFailed((url) => {
        toastsStore.add(
          'warning',
          "Couldn't open that link",
          'Your browser or the desktop shell refused to open it. Copy the address and paste it into a browser.',
          0,
          { copyUrl: url },
        );
      }),
    [],
  );

  // Background update checks. A no-op in the browser layout, which has nothing
  // to install. The toast is persistent (duration 0) and carries the install
  // action: an update notice that expires after four seconds is one the user
  // will only ever catch by accident, and dismissing it is how they say no.
  //
  // `policy`/`channel` are read through getSetting on every tick rather than
  // captured, so changing either in settings takes effect without a restart —
  // which is why this effect has no dependency on them and mounts once.
  useEffect(
    () =>
      startAutoUpdateChecks({
        policy: () => (getSetting<string>('app.autoUpdate') === 'never' ? 'never' : 'notify'),
        channel: () => getSetting<string>('app.releaseChannel') ?? 'stable',
        onUpdate: (info) => {
          toastsStore.add(
            'info',
            `Version ${info.version ?? ''} is available`,
            `You are running ${info.currentVersion} on the ${info.channel} channel. Installing replaces the app and restarts it; your data is untouched.`,
            0,
            {
              action: {
                label: 'Install and restart',
                run: () => {
                  // Nothing follows on success — the process is replaced. A
                  // failure is worth a word, since the user asked for this one.
                  void installUpdate(info.channel).catch((exc: unknown) => {
                    toastsStore.add('error', "Couldn't install the update", String(exc), 0);
                  });
                },
              },
            },
          );
        },
      }),
    [],
  );

  useEffect(() => {
    // App-window fullscreen, distinct from the frame's in-window
    // `area.fullscreen` and from a presented pane. Granted in BOTH layouts now:
    // the browser has no OS window but the DOM Fullscreen API covers the same
    // ground, and `toggleAppFullscreen` picks the mechanism.
    const canFullscreen = hasCapability('window.fullscreen');
    // The KEY, however, stays native-only. `keymap/reserved.ts` declares `f11`
    // owned by the browser with `preventable: false`, so binding it in the
    // browser layout would produce a binding that silently never fires.
    const nativeFullscreenKey = canFullscreen && windowControl() !== null;
    // First: the layout engine's controller, commands and hydration. The
    // desktop is the landing surface, so the tiling Frame may never mount —
    // and everything from the agent's layout tools to the backdrop commands
    // routes through the controller this installs.
    installFrameShell();
    registerDesktopModule();
    registry.register({
      id: 'shell',
      title: 'Shell',
      commands: [
        {
          id: 'shell.commandPalette',
          title: 'Open command palette',
          run: () => setPaletteOpen(true),
        },
        // Kept under its old id: the logo button and any saved keymap override
        // name it, and "home" is still what the destination means — it is now
        // the desktop rather than a separate avatar screen.
        // Both kept under their old ids: the logo button and any saved keymap
        // override name them. What they mean has changed, because there is one
        // view now — "home" is the desktop, and "the workspace" is what this
        // desktop looks like when it is tiling.
        { id: 'shell.home', title: 'Go to the desktop', run: () => setView('desktop') },
        {
          id: 'shell.workspace',
          title: 'Desktop: Switch to the tiling workspace',
          run: () => {
            setView('desktop');
            setDesktopMode('tiling');
          },
        },
        {
          id: 'shell.setup',
          title: 'Show setup (model, account, tools)',
          run: async () => {
            await setSetting(SETUP_DISMISSED_KEY, false);
            // The setup flow lives on the splash surface, which is now a
            // backdrop rather than a view — so put it on and go there. Without
            // this the command clears the flag and appears to do nothing on a
            // desktop showing any other backdrop.
            setBackdrop({ id: 'splash' });
            setView('desktop');
          },
        },
        {
          // Re-enter the first-run wizard without a restart. `desktop.oobeComplete`
          // is read once at mount (deliberately — reading it reactively yanks the
          // user back into the wizard the moment the settings store republishes),
          // so clearing the setting alone did nothing until the next launch, which
          // is what the setting's own description had to admit. This command lives
          // here because `setView` is only in scope here.
          id: 'shell.oobe',
          title: 'Show the first-run setup wizard',
          run: async () => {
            await setSetting(OOBE_COMPLETE_KEY, false);
            setView('oobe');
          },
        },
        ...(canFullscreen
          ? [
              {
                id: 'shell.toggleFullscreen',
                title: 'Window: Toggle fullscreen',
                run: async () => {
                  const on = await toggleAppFullscreen();
                  // The hint names the key only where the key exists; in the
                  // browser F11 belongs to the browser and telling the user to
                  // press it would be advice for a different application.
                  const exit = nativeFullscreenKey ? 'Press F11 to exit.' : 'Press Escape to exit.';
                  toastsStore.add(
                    'info',
                    on ? 'Fullscreen' : 'Windowed',
                    on ? exit : 'The app is back in a window.',
                    1500,
                  );
                },
              },
            ]
          : []),
      ],
      keybindings: [
        // Deliberately NOT `override`. The old service's comments claimed the
        // palette was override-global, but the terminal's scoped `mod+k` →
        // `terminal.clear` shadow is intentional and documented (the iTerm/VS
        // Code convention) — the comment was stale, not the behavior. `alt+x`
        // (minibuffer) is the binding that genuinely must never be shadowed.
        { key: 'mod+k', command: 'shell.commandPalette' },
        ...(nativeFullscreenKey ? [{ key: 'f11', command: 'shell.toggleFullscreen' }] : []),
      ],
      settings: [
        {
          // Declared here so the name first-run setup asks for is an ordinary
          // setting: editable afterwards, and resettable to blank like any
          // other override. It used to be localStorage, which is why the
          // wizard asked again on every fresh browser profile.
          key: NAME_SETTING_KEY,
          title: 'What the app calls you',
          description:
            'Used in the greeting on the desktop backdrop. Leave it blank for no name.',
          type: 'string',
          default: '',
        },
        {
          key: SETUP_DISMISSED_KEY,
          title: 'Hide setup on the home page',
          description:
            'Hides the "Get set up" flow (local model, account, connectors) even when steps are outstanding. Reopen it with the "Show setup" command.',
          type: 'boolean',
          default: false,
        },
      ],
    });
    // Opening a pane and switching desktops no longer change the shell view —
    // there is only one — so both go straight to the engine. `installFrameShell`
    // queues either until hydration finishes, which is what the nonce/replay
    // dance in the Frame used to be for.
    registry.setPanelOpener(openPaneWhenReady);
    registry.setWorkspaceSwitcher(switchWorkspaceWhenReady);
    if (initialWorkspaceId) switchWorkspaceWhenReady(initialWorkspaceId);
  }, []);

  // One capture-phase handler owns the keyboard, including the Escape ladder —
  // dialogs, capture release, area fullscreen and transient chrome used to each
  // grab Escape independently, so two of them could fire on one press.
  const [chordHint, setChordHint] = useState<string | null>(null);
  useEffect(
    () =>
      installKeymap({
        runCommand: (id) => {
          void registry.runCommand(id).catch((err) => {
            toastsStore.add('error', 'Command failed', String(err), 4000);
          });
        },
        dismissDialog: () => dialogsStore.dismissActive(),
        exitFullscreen: () => {
          // Both mechanisms, one rung. `presentPane(null)` clears whichever is
          // live — the tiling area or a presented window — and unwinds the OS
          // fullscreen it may have escalated to. Checking only
          // `fullscreenAreaId` here left a presented pane with no Escape at all.
          if (!isPresenting()) return false;
          presentPane(null);
          return true;
        },
        closeTransient: () => closeTransientChrome(),
        setPendingChord: setChordHint,
        escapeHoldMs: () => Number(getSetting('keymap.escapeHoldMs') ?? DEFAULT_ESCAPE_HOLD_MS),
      }),
    [],
  );

  // The keymap's `shellView` context key, so a binding can say
  // `shellView == 'desktop'`.
  useEffect(() => setShellView(view), [view]);

  // Which paradigm THIS desktop runs. Not a shell view and not a setting: it is
  // a property of the workspace, so switching desktops can switch paradigm.
  const { frame } = useSyncExternalStore(layoutStore.subscribe, layoutStore.getSnapshot);
  const tiling = frame.mode === 'tiling';

  // A per-workspace OS window is "detached": it drops the workspace-switcher
  // strip, showing only a slim titlebar over that one desktop.
  const detached = !!initialWorkspaceId;

  return (
    <div className={`shell shell--frame${detached ? ' shell--detached' : ''}`}>
      {hasCapability('chrome.workspaceTabs') && <WindowResizeHandles />}
      {detached ? <DetachedTitlebar /> : <WorkspaceTabs />}
      <div className="shell-content">
        {/* One view, two paradigms. The desktop (backdrop) is always mounted and
            always underneath; the tiling frame — rails, docks, centre grid —
            renders opaquely over it only while this desktop is in tiling mode.
            They are not two shell views: a desktop IS a workspace, and its mode
            decides what it looks like, so flipping is instant and never a
            navigation. */}
        {/* The wizard is a view, not a modal: it owns the screen until it is
            finished or skipped, and a dismissible overlay over a half-configured
            desktop is exactly the "did I finish that?" state it exists to avoid. */}
        {view === 'oobe' && <Oobe onDone={() => setView('desktop')} />}
        <div hidden={view !== 'desktop'} className="shell-view os-shell-view">
          <Desktop />
          {tiling && <Frame />}
        </div>
        {/* Windows float over BOTH views, so this is a sibling of them rather than
            a child: `.shell-view[hidden]` is `display:none`, and a window layer
            nested inside a view would vanish the moment you went home. It is
            inside `.shell-content` (already `position: relative`) so its
            coordinate space stops below the workspace strip — which is what keeps
            a window's titlebar from sliding under the Tauri drag region. */}
        <WindowLayer />
      </div>
      {/* Outside `.shell-content`, so it takes its own strip of the shell rather
          than overlapping the desktop — the window layer's coordinate space
          then stops at the taskbar, and a maximized window cannot cover it. A
          detached per-workspace window has no taskbar: it holds one desktop and
          has nothing to switch between. */}
      {/* The minibuffer on a floating desktop, where the Frame that normally
          hosts it is not mounted. Without this `alt+x` flipped a store nothing
          rendered, and — worse — a `dialogs.prompt()` waited forever on an
          answer the user was never shown. It renders nothing when idle; the
          taskbar's `mx` zone is the desktop's status line. */}
      {view === 'desktop' && !tiling && <Minibuffer variant="overlay" />}
      {!detached && view === 'desktop' && <Taskbar />}
      {/* Half-typed chord (mod+k …) — without this the keyboard just goes quiet
          for a second and the user has no idea the shell is waiting. */}
      {chordHint && <div className="shell-chord-hint">{chordHint}&nbsp;…</div>}
      <CaptureHud />
      <Spotlight open={paletteOpen} onClose={() => setPaletteOpen(false)} />
      <SymbolSearchModal />
      <ApprovalPrompts />
      <Toasts />
      <Dialogs />
      {/* The connect flow, reachable from any surface. Mounted at the shell so a
          `requestConnect` from a pane, the clock flyout or a Start-menu entry never
          has to navigate to the home tile row to find the UI. */}
      <ConnectorDialog />
      {/* Last, so the menu paints over every other layer; it renders into a portal
          on document.body anyway, but source order settles ties at equal z-index. */}
      <ContextMenuLayer />
    </div>
  );
}
