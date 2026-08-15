import { useEffect, useRef, useState } from 'react';
import {
  closeTransientChrome,
  DEFAULT_ESCAPE_HOLD_MS,
  dialogsStore,
  fullscreenArea,
  getSetting,
  hasCapability,
  installKeymap,
  installUpdate,
  layoutStore,
  onExternalOpenFailed,
  startAutoUpdateChecks,
  registry,
  setSetting,
  setShellView,
  SymbolSearchModal,
  toastsStore,
  windowControl,
  type OpenPaneOptions,
  type ShellView,
} from '@horrible/core';

import { ApprovalPrompts } from './ApprovalPrompts';
import { SETUP_DISMISSED_KEY } from './home/constants';
import { CaptureHud } from './CaptureHud';
import { CommandPalette } from './CommandPalette';
import { ContextMenuLayer } from './overlay/ContextMenu';
import { Dialogs } from './Dialogs';
import { Toasts } from './Toasts';
import { HomeView } from './HomeView';
import { Frame } from './layout/Frame';
import { WindowResizeHandles } from './layout/WindowChrome';
import { DetachedTitlebar, WorkspaceTabs } from './layout/WorkspaceTabs';
import './styles.css';
import './overlay/docs.css';

/**
 * The shell: the workspace tab strip on top (always visible, Blender-style),
 * then either the `home` view or the frame (the workspace engine — activity
 * rail, docks, center area grid). Global chrome (palette, toasts, dialogs,
 * keybinding dispatch) lives here. See docs/architecture/layout-shell.mdx.
 */
export function AppShell({
  appTitle,
  initialWorkspaceId,
}: {
  appTitle: string;
  /** When set (a per-workspace OS window), boot into this workspace, not home. */
  initialWorkspaceId?: string;
}) {
  const [view, setView] = useState<ShellView>(initialWorkspaceId ? 'workspace' : 'home');
  const [paletteOpen, setPaletteOpen] = useState(false);

  // Bumped each time a panel should open, so the Frame reacts even to repeats.
  const [pendingOpen, setPendingOpen] = useState<{
    panelId: string;
    opts?: OpenPaneOptions;
    nonce: number;
  }>();
  // Same pattern for switching to a named workspace tab.
  const [pendingWorkspace, setPendingWorkspace] = useState<{
    workspaceId: string;
    nonce: number;
  }>();
  const nonce = useRef(0);

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
    // OS-window fullscreen is a phase-2 native-shell capability (F11); distinct
    // from the frame's in-window `area.fullscreen` (ctrl+space). Only register
    // it where a native shell grants the capability, so the browser keeps its
    // own native F11 and the palette stays clean.
    const nativeFullscreen = hasCapability('window.fullscreen');
    registry.register({
      id: 'shell',
      title: 'Shell',
      commands: [
        {
          id: 'shell.commandPalette',
          title: 'Open command palette',
          run: () => setPaletteOpen(true),
        },
        { id: 'shell.home', title: 'Go home', run: () => setView('home') },
        { id: 'shell.workspace', title: 'Go to workspace', run: () => setView('workspace') },
        {
          id: 'shell.setup',
          title: 'Show setup (model, account, tools)',
          run: async () => {
            await setSetting(SETUP_DISMISSED_KEY, false);
            setView('home');
          },
        },
        ...(nativeFullscreen
          ? [
              {
                id: 'shell.toggleFullscreen',
                title: 'Window: Toggle fullscreen',
                run: async () => {
                  const on = await windowControl()?.toggleFullscreen();
                  if (on !== undefined) {
                    toastsStore.add(
                      'info',
                      on ? 'Fullscreen' : 'Windowed',
                      on ? 'Press F11 to exit.' : 'Press F11 for fullscreen.',
                      1500,
                    );
                  }
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
        ...(nativeFullscreen ? [{ key: 'f11', command: 'shell.toggleFullscreen' }] : []),
      ],
      settings: [
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
    // Opening any panel switches to the workspace and tells it which to open.
    registry.setPanelOpener((panelId, opts) => {
      setView('workspace');
      setPendingOpen({ panelId, opts, nonce: nonce.current++ });
    });
    // Switching workspaces also enters the workspace view, then signals the frame.
    registry.setWorkspaceSwitcher((workspaceId) => {
      setView('workspace');
      setPendingWorkspace({ workspaceId, nonce: nonce.current++ });
    });
    // Per-workspace OS window: open straight into the requested workspace once
    // the frame is mounted (it replays this pending switch after hydration).
    if (initialWorkspaceId) {
      setPendingWorkspace({ workspaceId: initialWorkspaceId, nonce: nonce.current++ });
    }
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
          if (!layoutStore.getSnapshot().frame.fullscreenAreaId) return false;
          fullscreenArea(null);
          return true;
        },
        closeTransient: () => closeTransientChrome(),
        setPendingChord: setChordHint,
        escapeHoldMs: () => Number(getSetting('keymap.escapeHoldMs') ?? DEFAULT_ESCAPE_HOLD_MS),
      }),
    [],
  );

  // The keymap's `shellView` context key, so a binding can say `shellView == 'home'`.
  useEffect(() => setShellView(view), [view]);

  // Mount the Frame the first time it's shown, then keep it alive across views
  // so pane state (terminals, editors) survives visiting home.
  const [workspaceMounted, setWorkspaceMounted] = useState(false);
  useEffect(() => {
    if (view === 'workspace') setWorkspaceMounted(true);
  }, [view]);

  // A per-workspace OS window is "detached": it drops the workspace-switcher
  // strip and home view, showing only a slim titlebar over that one workspace's
  // frame (rail + docks + center).
  const detached = !!initialWorkspaceId;

  return (
    <div className={`shell shell--frame${detached ? ' shell--detached' : ''}`}>
      {hasCapability('chrome.workspaceTabs') && <WindowResizeHandles />}
      {detached ? <DetachedTitlebar /> : <WorkspaceTabs />}
      <div className="shell-content">
        {!detached && (
          <div hidden={view !== 'home'} className="shell-view">
            <HomeView />
          </div>
        )}
        {(detached || workspaceMounted) && (
          <div hidden={!detached && view !== 'workspace'} className="shell-view">
            <Frame pendingOpen={pendingOpen} pendingWorkspace={pendingWorkspace} />
          </div>
        )}
      </div>
      {/* Half-typed chord (mod+k …) — without this the keyboard just goes quiet
          for a second and the user has no idea the shell is waiting. */}
      {chordHint && <div className="shell-chord-hint">{chordHint}&nbsp;…</div>}
      <CaptureHud />
      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
      <SymbolSearchModal />
      <ApprovalPrompts />
      <Toasts />
      <Dialogs />
      {/* Last, so the menu paints over every other layer; it renders into a portal
          on document.body anyway, but source order settles ties at equal z-index. */}
      <ContextMenuLayer />
    </div>
  );
}
