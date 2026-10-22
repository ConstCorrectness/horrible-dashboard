/**
 * Persistence for the desktop. One rule kills the old engine's race guards: the
 * store's `workspaceId` and `frame` are a single atom, and the debounced autosave
 * reads BOTH from the same snapshot at fire time, so it can never write a layout
 * under the wrong id. The backend keeps storing layouts opaquely.
 *
 * There is one desktop, stored as the workspace row {@link DESKTOP_WORKSPACE}.
 * The backend still models a list of workspaces — it predates the removal of
 * tiled workspaces and presets — and rows other than this one are left alone:
 * nothing loads them, and nothing deletes what a user may still want back.
 */
import { registry } from '../registry';
import {
  getWorkspaces,
  saveWorkspace,
  saveWorkspaceOnUnload,
  setActiveWorkspace,
  type Workspace as WorkspaceModel,
  type WorkspacesState,
} from '../workspace';
import { workspaceStore } from '../workspace-store';
import { createEmptyFrame } from './model';
import { deserialize, serialize } from './serialize';
import { layoutStore } from './store';
import type { FrameState } from './types';

const AUTOSAVE_MS = 600;

/** The workspace row the desktop is stored under. */
export const DESKTOP_WORKSPACE = 'desktop';

let saveTimer: ReturnType<typeof setTimeout> | null = null;
let lastSavedRevision = 0;
let autosaveBound = false;

function knownViews(): ReadonlySet<string> {
  return new Set([...registry.panels.map((p) => p.id), ...registry.widgets.map((w) => w.id)]);
}

// --- Agent persona -------------------------------------------------------------
// Picking a different agent from the chat's dropdown is an override that outlives
// a reload — kept in localStorage rather than the layout because it is a UI
// preference of *this* browser, not part of the layout every client shares. Still
// keyed by workspace id, so an override saved before there was one desktop keeps
// applying.

const AGENT_OVERRIDES_KEY = 'horrible.workspaceAgents';

function readOverrides(): Record<string, string> {
  try {
    const raw = localStorage.getItem(AGENT_OVERRIDES_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : null;
    return parsed && typeof parsed === 'object' ? (parsed as Record<string, string>) : {};
  } catch {
    return {}; // private mode / corrupt entry — fall back to the main agent
  }
}

/** The roster agent the desktop's chat should open as: the user's override, else `main`. */
export function agentForWorkspace(workspaceId: string | null): string {
  if (!workspaceId) return 'main';
  return readOverrides()[workspaceId] ?? 'main';
}

/** Remember the agent the user picked. Choosing `main` clears the override. */
export function setWorkspaceAgent(workspaceId: string | null, agentId: string): void {
  if (!workspaceId) return;
  const overrides = readOverrides();
  if (agentId === 'main') delete overrides[workspaceId];
  else overrides[workspaceId] = agentId;
  try {
    localStorage.setItem(AGENT_OVERRIDES_KEY, JSON.stringify(overrides));
  } catch {
    /* storage unavailable — the choice just doesn't persist */
  }
}

/** The desktop's frame: its stored blob if it is one of ours, else empty. */
function frameOf(ws: WorkspaceModel): FrameState {
  return deserialize(ws.layout, knownViews()) ?? createEmptyFrame();
}

function load(workspaceId: string, frameState: FrameState): void {
  layoutStore.dispatch({ type: 'LOAD_WORKSPACE', workspaceId, frame: frameState });
  lastSavedRevision = layoutStore.getSnapshot().revision;
}

/**
 * A save in flight. Two overlapping PUTs are not merely wasteful: each serializes
 * the frame *before* awaiting, so if a slow save of revision 5 lands after a fast
 * save of revision 6, the older layout wins and the newer one is gone. Saves are
 * therefore strictly sequential, and a request arriving mid-flight waits.
 */
let saveInFlight: Promise<void> | null = null;

async function saveSnapshotNow(): Promise<void> {
  if (saveInFlight) {
    // Chain rather than skip: the in-flight save serialized an older frame, so
    // returning here would report a newer edit as saved when it was never sent.
    await saveInFlight.catch(() => {});
  }
  const snap = layoutStore.getSnapshot();
  if (!snap.hydrated || !snap.workspaceId || snap.revision === lastSavedRevision) return;
  const revision = snap.revision;
  const run = saveWorkspace(snap.workspaceId, { layout: serialize(snap.frame) }).then(() => {
    // Marked saved only once the backend has it. This used to be set *before* the
    // await, so a PUT that failed — the backend restarting under `--reload`, a
    // dropped connection — left the revision marked saved and no retry was ever
    // attempted: the edit was silently gone on the next reload. The window stayed
    // on screen, which is what made it look like the refresh had discarded it.
    lastSavedRevision = revision;
  });
  saveInFlight = run.then(
    () => {},
    () => {},
  );
  try {
    await run;
  } catch (err) {
    // Reschedule rather than swallow. The frame is a full snapshot, so one later
    // successful save carries every edit since the last one — the retry does not
    // need to remember what failed, only that something is still unsaved.
    scheduleSave();
    throw err;
  } finally {
    saveInFlight = null;
  }
}

/** Arm the debounced autosave. Shared by the store subscription and the retry. */
function scheduleSave(): void {
  if (saveTimer) clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    saveTimer = null;
    void saveSnapshotNow().catch(() => {
      /* already rescheduled above; nothing here can do better than try again */
    });
  }, AUTOSAVE_MS);
}

/** Persist any pending edits now (cancels the debounce). */
export async function flush(): Promise<void> {
  if (saveTimer) {
    clearTimeout(saveTimer);
    saveTimer = null;
  }
  await saveSnapshotNow();
}

/** Subscribe the debounced autosave to the store. Idempotent. */
export function bindAutosave(): void {
  if (autosaveBound) return;
  autosaveBound = true;
  layoutStore.subscribe(() => {
    const snap = layoutStore.getSnapshot();
    if (!snap.hydrated || snap.revision === lastSavedRevision) return;
    scheduleSave();
  });
  bindUnloadFlush();
}

/**
 * Save the pending edit when the page goes away.
 *
 * Without this the 600ms debounce is a window in which every edit is lost: open a
 * window and hit refresh, and the layout that comes back is the one from before —
 * which reads as "refreshing reset my desktop", because the pane really was open
 * a moment ago.
 *
 * `pagehide` rather than `beforeunload`: it fires on mobile and on tab discard,
 * where `beforeunload` does not, and it is the event that is actually guaranteed
 * before a page is thrown away. `visibilitychange` to hidden covers the tab being
 * backgrounded and never coming back.
 *
 * The request is `keepalive` (see `saveWorkspaceOnUnload`), because a page being
 * unloaded is not allowed to await anything: a normal `fetch` started here is
 * cancelled with the document. It cannot report failure, so the debounced save
 * stays the primary path — this is the backstop for the edit not yet written.
 */
function bindUnloadFlush(): void {
  if (typeof window === 'undefined') return;
  const flushBeacon = (): void => {
    const snap = layoutStore.getSnapshot();
    if (!snap.hydrated || !snap.workspaceId || snap.revision === lastSavedRevision) return;
    if (saveWorkspaceOnUnload(snap.workspaceId, serialize(snap.frame)))
      lastSavedRevision = snap.revision;
  };
  window.addEventListener('pagehide', flushBeacon);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') flushBeacon();
  });
}

/**
 * Read the workspace list, retrying a failure before giving up.
 *
 * Boot is the one moment this call is *expected* to fail: the Tauri shell spawns
 * the backend and loads the UI concurrently, `pnpm dev` restarts the backend on
 * every save, and either way the first request can land on a port that is not
 * serving yet. A single attempt turns a half-second startup race into an empty
 * desktop with the user's real layout still sitting on disk — which is precisely
 * what "refreshing reset all my windows" looks like from the outside.
 *
 * Retries only the *read*. There is nothing to lose by asking again, and the
 * alternative — presenting an empty desktop — is the outcome worth avoiding.
 */
async function getWorkspacesWithRetry(attempts = 4): Promise<WorkspacesState> {
  let lastError: unknown;
  for (let i = 0; i < attempts; i++) {
    try {
      return await getWorkspaces();
    } catch (err) {
      lastError = err;
      // 250ms, 500ms, 1s — about two seconds in total, which covers a backend
      // reload without making a genuinely absent backend feel hung.
      if (i < attempts - 1) await new Promise((r) => setTimeout(r, 250 * 2 ** i));
    }
  }
  throw lastError;
}

/**
 * Boot: load the desktop into the store, creating its row on a clean install.
 *
 * Rejects if the layout could not be read. The caller must **not** treat that as
 * a loaded-but-empty desktop: the store stays un-hydrated on purpose, which keeps
 * autosave switched off so a blank screen can never be written over the layout
 * that is still safely on disk.
 */
export async function hydrate(): Promise<void> {
  let state = await getWorkspacesWithRetry();
  let ws = state.workspaces.find((w) => w.id === DESKTOP_WORKSPACE);
  if (!ws) {
    await saveWorkspace(DESKTOP_WORKSPACE, {
      name: 'Desktop',
      layout: serialize(createEmptyFrame()),
    });
    state = await getWorkspaces();
    ws = state.workspaces.find((w) => w.id === DESKTOP_WORKSPACE);
    if (!ws) throw new Error('the desktop workspace could not be created');
  }
  workspaceStore.publish({ workspaces: [{ id: ws.id, name: ws.name }], activeId: ws.id });
  load(ws.id, frameOf(ws));
  if (state.active !== ws.id) void setActiveWorkspace(ws.id);
}
