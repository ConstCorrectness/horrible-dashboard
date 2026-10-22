/**
 * Installing the desktop engine into the shell: the LayoutController the agent's
 * tools drive, the debounced autosave, the `frame` module's commands and
 * keybindings, and hydrating the desktop. Run once from `AppShell`, before
 * anything that might open a pane.
 */
import {
  executeTool,
  framePersistence,
  installFrameController,
  layoutStore,
  minibuffer,
  openFramePane,
  presentFocusedPane,
  registry,
  toggleRegion,
  type OpenPaneOptions,
  type RegionPosition,
} from '@horrible/core';

let installed = false;

/** The pane region keys act on: the focused window's showing tab. */
function focusedPaneId(): string | null {
  const f = layoutStore.getSnapshot().frame;
  const win = f.windows.find((w) => w.id === f.focusedWindowId);
  return win?.area.tabs[win.area.activeTab]?.instanceId ?? f.focusedInstanceId;
}

/** Idempotent, and safe to call before anything renders. */
export function installFrameShell(): void {
  if (installed) return;
  installed = true;
  installFrameController();
  framePersistence.bindAutosave();
  if (import.meta.env.DEV) {
    // Dev/E2E handle. `exec` runs a relayed tool by name — the same entry point
    // agent/REPL calls hit — so layout verbs can be exercised from the console.
    (
      window as Window & {
        __horribleFrame?: {
          store: typeof layoutStore;
          registry: typeof registry;
          exec: typeof executeTool;
        };
      }
    ).__horribleFrame = { store: layoutStore, registry, exec: executeTool };
  }
  const POSITIONS: readonly RegionPosition[] = ['left', 'right', 'bottom'];
  registry.register({
    id: 'frame',
    title: 'Frame',
    commands: [
      {
        // Kept under its old id: saved keymap overrides name it.
        id: 'area.fullscreen',
        title: 'Toggle fullscreen for the focused pane',
        run: () => presentFocusedPane(),
      },
      {
        id: 'minibuffer.open',
        title: 'Minibuffer: Run a command (M-x)',
        run: () => minibuffer.open('/'),
      },
      ...POSITIONS.map((position) => ({
        id: `region.toggle:${position}`,
        title: `Region: Toggle ${position}`,
        run: () => {
          const instanceId = focusedPaneId();
          if (instanceId) void toggleRegion(instanceId, position);
        },
      })),
    ],
    keybindings: [
      // ctrl+space is the IME toggle on Windows and the input-source switch on
      // macOS — neither reaches the page — so those two get an alternative.
      { key: 'ctrl+space', command: 'area.fullscreen', platforms: ['linux'] },
      { key: 'mod+alt+f', command: 'area.fullscreen', platforms: ['mac', 'win'] },
      // `override` so a focused editor pane can't shadow it — the minibuffer
      // is the escape hatch and has to be reachable from anywhere.
      { key: 'alt+x', command: 'minibuffer.open', override: true },
      { key: 't', command: 'region.toggle:left' },
      { key: 'n', command: 'region.toggle:right' },
      { key: 'b', command: 'region.toggle:bottom' },
    ],
  });
  void framePersistence
    .hydrate()
    .catch((err: unknown) => {
      // A boot that could not read the layout must not look like a boot that read
      // an empty one. `hydrate` already retried; reaching here means the backend is
      // genuinely not answering, and the user's desktop is still safely on disk.
      //
      // The store stays un-hydrated, so autosave is off and nothing can overwrite
      // that layout with the blank frame on screen — but the screen is blank, and
      // saying nothing makes a refresh look like it discarded every window. So say
      // it, and offer the only action that helps.
      console.error('Could not load your desktop layout', err);
      reportLayoutLoadFailure(err);
    })
    .finally(() => {
      hydrated = true;
      const queued = pending.splice(0);
      for (const run of queued) run();
    });
}

/**
 * Surface a failed layout load. Kept as a plain DOM banner rather than a React
 * toast because it has to work when the shell may not have mounted, and its only
 * job is to make an empty desktop legible plus offer a reload.
 */
function reportLayoutLoadFailure(err: unknown): void {
  if (typeof document === 'undefined' || document.getElementById('layout-load-error')) return;
  const bar = document.createElement('div');
  bar.id = 'layout-load-error';
  bar.className = 'layout-load-error';
  bar.setAttribute('role', 'alert');
  const text = document.createElement('span');
  text.textContent =
    'Could not load your desktop — the backend did not answer. Your windows are saved; nothing has been lost.';
  const retry = document.createElement('button');
  retry.type = 'button';
  retry.textContent = 'Retry';
  retry.onclick = () => window.location.reload();
  const detail = document.createElement('code');
  detail.textContent = err instanceof Error ? err.message : String(err);
  bar.append(text, detail, retry);
  document.body.appendChild(bar);
}

/**
 * Work requested before the desktop finished loading.
 *
 * A boot-time `registry.openPanel` — a deep link, a plugin opening its pane on
 * registration — arrives while the frame is still the empty seed. Running it then
 * puts the pane into a frame that `LOAD_WORKSPACE` is about to replace wholesale,
 * so it simply vanishes with no error anywhere.
 */
let hydrated = false;
const pending: Array<() => void> = [];

function whenHydrated(run: () => void): void {
  if (hydrated) run();
  else pending.push(run);
}

/** `registry.openPanel`'s target: open the pane in a window, once hydrated. */
export function openPaneWhenReady(viewId: string, opts?: OpenPaneOptions): void {
  whenHydrated(() => openFramePane(viewId, opts));
}
