/**
 * Workspace persistence for the frame engine. One rule kills the old engine's
 * race guards: the store's `workspaceId` and `frame` are a single atom, and the
 * debounced autosave reads BOTH from the same snapshot at fire time — so a
 * workspace switch (which swaps the atom and flushes first) can never cross-write
 * a layout under the wrong id. The backend keeps storing layouts opaquely.
 */
import { registry } from '../registry';
import { getSetting } from '../settings';
import {
  createWorkspace as apiCreateWorkspace,
  deleteWorkspace as apiDeleteWorkspace,
  getWorkspaces,
  saveWorkspace,
  setActiveWorkspace,
  type Workspace as WorkspaceModel,
} from '../workspace';
import { workspaceStore } from '../workspace-store';
import { areaId, createEmptyFrame, listPanes, windowId } from './model';
import { closeWorkspaceSessions } from './pane-lifetime';
import { seedFromPreset, type FramePreset } from './presets';
import { isDockable, regionsFor, resolveView } from './controller';
import { deserialize, serialize } from './serialize';
import { layoutStore } from './store';
import type { FrameState, WindowState } from './types';

const AUTOSAVE_MS = 600;

/**
 * Which desktop boot opens. See {@link resolveBootWorkspace}.
 *
 * Declared in the `desktop` module's manifest; named here because `hydrate` is
 * the only thing that reads it and the two must agree on the key.
 */
export const BOOT_WORKSPACE_KEY = 'desktop.bootWorkspace';

/** The empty floating desktop — logging in should feel like logging into a machine. */
export const DEFAULT_BOOT_WORKSPACE = 'desktop';

/** The sentinel that restores whichever desktop was open last. */
export const BOOT_WORKSPACE_LAST = 'last';

let saveTimer: ReturnType<typeof setTimeout> | null = null;
let lastSavedRevision = 0;
let autosaveBound = false;

function knownViews(): ReadonlySet<string> {
  return new Set([...registry.panels.map((p) => p.id), ...registry.widgets.map((w) => w.id)]);
}

/**
 * Views that may no longer sit in a dock — see `deserialize`.
 *
 * Asked of `isDockable` rather than of the `embedded` flag alone, because
 * `embedded` is only one of the ways a view stops being dockable: a view that
 * changes `role` from `tool` to `document` (as `settings.home` did when the
 * settings page became a centre tab) is equally undockable afterwards, and its
 * saved dock entry is equally a state today's code cannot produce. Filtering on
 * the *capability* covers both without a per-view migration list.
 */
function undockableViews(): ReadonlySet<string> {
  return new Set(
    [...registry.panels, ...registry.widgets].filter((v) => !isDockable(v.id)).map((v) => v.id),
  );
}

function presetFor(id: string | null): FramePreset | undefined {
  return id ? registry.framePresets.find((p) => p.id === id) : undefined;
}

// --- Workspace ↔ agent binding -----------------------------------------------
// A preset declares the persona its workspace opens with (`FramePreset.agent`).
// Picking a different agent from the chat's dropdown is a per-workspace override
// that outlives a switch away and back — kept in localStorage rather than the
// workspace record because it is a UI preference of *this* browser, not part of
// the layout every client shares.

const AGENT_OVERRIDES_KEY = 'horrible.workspaceAgents';

function readOverrides(): Record<string, string> {
  try {
    const raw = localStorage.getItem(AGENT_OVERRIDES_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : null;
    return parsed && typeof parsed === 'object' ? (parsed as Record<string, string>) : {};
  } catch {
    return {}; // private mode / corrupt entry — fall back to the preset's agent
  }
}

/** The roster agent a workspace's chat should open as: the user's override for it,
 * else its preset's declared agent, else the main orchestrator. */
export function agentForWorkspace(workspaceId: string | null): string {
  if (!workspaceId) return 'main';
  return readOverrides()[workspaceId] ?? presetFor(workspaceId)?.agent ?? 'main';
}

/** Remember the agent the user picked for a workspace. Choosing the workspace's
 * declared default clears the override rather than pinning it. */
export function setWorkspaceAgent(workspaceId: string | null, agentId: string): void {
  if (!workspaceId) return;
  const overrides = readOverrides();
  if (agentId === (presetFor(workspaceId)?.agent ?? 'main')) delete overrides[workspaceId];
  else overrides[workspaceId] = agentId;
  try {
    localStorage.setItem(AGENT_OVERRIDES_KEY, JSON.stringify(overrides));
  } catch {
    /* storage unavailable — the choice just doesn't persist */
  }
}

function seed(preset: FramePreset): FrameState {
  return seedFromPreset(preset, {
    knownViews: knownViews(),
    regionsFor,
    dockSizeFor: (viewId) => resolveView(viewId)?.defaultDockSize,
  });
}

/** A workspace's frame: its stored blob if it is one of ours, else its preset
 * seed (covers pre-frame legacy blobs — discarded by design), else empty. */
function frameOf(ws: WorkspaceModel): FrameState {
  const restored = deserialize(ws.layout, knownViews(), undockableViews());
  if (restored) return restored;
  const preset = presetFor(ws.id);
  return preset ? seed(preset) : createEmptyFrame();
}

function publish(list: WorkspaceModel[], activeId: string | null): void {
  workspaceStore.publish({
    workspaces: list.map((w) => ({ id: w.id, name: w.name })),
    activeId,
  });
}

function load(workspaceId: string, frameState: FrameState): void {
  layoutStore.dispatch({ type: 'LOAD_WORKSPACE', workspaceId, frame: frameState });
  lastSavedRevision = layoutStore.getSnapshot().revision;
}

async function saveSnapshotNow(): Promise<void> {
  const snap = layoutStore.getSnapshot();
  if (!snap.hydrated || !snap.workspaceId || snap.revision === lastSavedRevision) return;
  lastSavedRevision = snap.revision;
  await saveWorkspace(snap.workspaceId, { layout: serialize(snap.frame) });
}

/**
 * Add a window to a workspace that is **not** the one loaded — "send this window to
 * Desktop 2".
 *
 * It edits the destination's stored blob directly rather than going through the
 * store, because the store holds exactly one workspace at a time; loading the target
 * to edit it would yank the user's screen out from under them. Returns false if the
 * workspace is gone or its blob isn't one of ours, so the caller can leave the window
 * where it is instead of dropping it.
 *
 * The window is re-seated at ids drawn from the destination's own counter: ids are
 * unique per workspace, and carrying them across would risk colliding with whatever
 * is already there.
 */
export async function addWindowToWorkspace(
  workspaceId: string,
  win: WindowState,
): Promise<boolean> {
  const state = await getWorkspaces();
  const target = state.workspaces.find((w) => w.id === workspaceId);
  if (!target) return false;
  const frameState = frameOf(target);
  const seq = frameState.paneSeq;
  const landed: FrameState = {
    ...frameState,
    windows: [
      ...frameState.windows,
      {
        ...win,
        id: windowId(seq),
        area: { ...win.area, id: areaId(seq + 1) },
        z: frameState.windows.length + 1,
      },
    ],
    paneSeq: seq + 2,
  };
  await saveWorkspace(workspaceId, { layout: serialize(landed) });
  return true;
}

/** Persist any pending edits now (cancels the debounce). Await before switching. */
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
    if (saveTimer) clearTimeout(saveTimer);
    saveTimer = setTimeout(() => {
      saveTimer = null;
      void saveSnapshotNow();
    }, AUTOSAVE_MS);
  });
}

/**
 * The workspace boot should open, or `null` to restore whichever was last active.
 *
 * A preset that has never been visited has no workspace row yet, so this can
 * name one that must be created first — the caller does that, reusing the same
 * seed path as the empty slate.
 */
function resolveBootWorkspace(workspaces: WorkspaceModel[]): string | null {
  const want = getSetting<string>(BOOT_WORKSPACE_KEY) ?? DEFAULT_BOOT_WORKSPACE;
  if (!want || want === BOOT_WORKSPACE_LAST) return null;
  if (workspaces.some((w) => w.id === want)) return want;
  // A preset id is a valid answer even with no row: `switchWorkspace` creates
  // those lazily too, so a setting naming one should not silently fall back.
  if (presetFor(want)) return want;
  // Names nothing this build knows — a workspace the user deleted, or a preset
  // from a plugin that is no longer installed. Falling back to the last-active
  // desktop is the one behaviour that never strands someone on an empty screen.
  return null;
}

/** Boot: fetch workspaces (seeding the default preset on an empty slate) and
 * load the active one into the store. */
export async function hydrate(): Promise<void> {
  let state = await getWorkspaces();
  // The empty floating `desktop`, not `dashboard`: a first launch should land on
  // a bare desktop the way logging into a machine does, not on a pre-arranged
  // nine-pane workspace. Only the *empty slate* is affected — an install that
  // already has workspaces keeps opening whichever one it was left on.
  const defaultPreset = presetFor('desktop') ?? presetFor('dashboard') ?? registry.framePresets[0];
  if (state.workspaces.length === 0 && defaultPreset) {
    await saveWorkspace(defaultPreset.id, {
      name: defaultPreset.name,
      layout: serialize(seed(defaultPreset)),
    });
    state = await getWorkspaces();
  }
  /**
   * Boot goes where the setting says, not where you happened to stop.
   *
   * "Whatever was open last" is the right rule for a document and the wrong one
   * for a desktop: an experiment on a tiled workspace three days ago should not
   * decide what you log into today. Nothing is lost either way — autosave has
   * already persisted that layout, and it is one Start-menu click away.
   */
  const boot = resolveBootWorkspace(state.workspaces);
  if (boot && !state.workspaces.some((w) => w.id === boot)) {
    const preset = presetFor(boot);
    // Guarded by `resolveBootWorkspace`, which only returns an unknown id when it
    // resolves to a preset.
    if (preset) {
      await saveWorkspace(preset.id, { name: preset.name, layout: serialize(seed(preset)) });
      state = await getWorkspaces();
    }
  }
  const activeId = boot ?? state.active ?? state.workspaces[0]?.id ?? null;
  publish(state.workspaces, activeId);
  const ws = state.workspaces.find((w) => w.id === activeId);
  if (ws) {
    load(ws.id, frameOf(ws));
    if (state.active !== activeId) void setActiveWorkspace(ws.id);
  }
}

/** Switch to a workspace (lazily creating a preset's stable-id workspace). */
export async function switchWorkspace(id: string): Promise<void> {
  if (layoutStore.getSnapshot().workspaceId === id) return;
  await flush();
  let state = await getWorkspaces();
  let target = state.workspaces.find((w) => w.id === id);
  if (!target) {
    const preset = presetFor(id);
    if (!preset) return;
    await saveWorkspace(preset.id, { name: preset.name });
    state = await getWorkspaces();
    target = state.workspaces.find((w) => w.id === id);
    if (!target) return;
  }
  publish(state.workspaces, id);
  load(target.id, frameOf(target));
  void setActiveWorkspace(id);
}

/**
 * Make a workspace name unique among the ones that already exist.
 *
 * Two desktops called "Workspace" are two rows in the Start menu that a user
 * cannot tell apart, and switching desktops becomes a coin flip — the launcher
 * shows the name and nothing else distinguishing. Suffixing is used rather than
 * rejecting the name, because both entry points are non-blocking (a command that
 * takes a default, and an agent tool) and failing them over a collision would be
 * worse than quietly numbering.
 *
 * Comparison is case-insensitive and trimmed: "Work" and "work " read as the
 * same name in a menu, so they collide for this purpose.
 */
export function uniqueWorkspaceName(name: string, existing: readonly string[]): string {
  const taken = new Set(existing.map((n) => n.trim().toLowerCase()));
  const base = name.trim() || 'Workspace';
  if (!taken.has(base.toLowerCase())) return base;
  for (let i = 2; ; i++) {
    const candidate = `${base} ${i}`;
    if (!taken.has(candidate.toLowerCase())) return candidate;
  }
}

/**
 * Prompt-free create, shared by the workspace.new commands and the agent tool.
 *
 * `mode` is the desktop's paradigm and belongs to the workspace, not to a global
 * switch: choosing it here is what makes "documents tiled on one desktop,
 * everything loose on another" the normal way to have both, instead of converting
 * one desktop back and forth through a lossy flip.
 *
 * `fromCurrent` seeds the new workspace with the arrangement on screen right now —
 * "save this as a desktop" — rather than an empty frame. The current frame is
 * copied *before* the switch, since `load` replaces it.
 */
export async function createNamedWorkspace(
  name: string,
  { mode, fromCurrent = false }: { mode?: FrameState['mode']; fromCurrent?: boolean } = {},
): Promise<{ id: string; name: string }> {
  const current = layoutStore.getSnapshot().frame;
  await flush();
  const existing = await getWorkspaces();
  const ws = await apiCreateWorkspace(
    uniqueWorkspaceName(
      name,
      existing.workspaces.map((w) => w.name),
    ),
  );
  const state = await getWorkspaces();
  publish(state.workspaces, ws.id);
  const seed = fromCurrent
    ? structuredClone(current)
    : { ...createEmptyFrame(), mode: mode ?? 'tiling' };
  load(ws.id, mode ? { ...seed, mode } : seed);
  void setActiveWorkspace(ws.id);
  return { id: ws.id, name: ws.name };
}

export async function listWorkspaces(): Promise<{
  active: string | null;
  workspaces: { id: string; name: string }[];
}> {
  const s = await getWorkspaces();
  return { active: s.active, workspaces: s.workspaces.map((w) => ({ id: w.id, name: w.name })) };
}

export async function renameWorkspace(id: string, name: string): Promise<void> {
  const state = await getWorkspaces();
  // Its own current name is not a collision with itself.
  const others = state.workspaces.filter((w) => w.id !== id).map((w) => w.name);
  await saveWorkspace(id, { name: uniqueWorkspaceName(name, others) });
  const s = await getWorkspaces();
  publish(s.workspaces, layoutStore.getSnapshot().workspaceId);
}

export async function removeWorkspace(id: string): Promise<void> {
  if (presetFor(id)) return; // predefined layouts reset rather than delete
  const state = await apiDeleteWorkspace(id);
  // Deleting the workspace closes every pane in it, including the ones that were
  // unmounted at the time — those still hold a PTY / browser session.
  closeWorkspaceSessions(id);
  const activeId = layoutStore.getSnapshot().workspaceId;
  if (activeId === id) {
    const next = state.workspaces.find((w) => w.id === state.active) ?? state.workspaces[0] ?? null;
    publish(state.workspaces, next?.id ?? null);
    if (next) {
      load(next.id, frameOf(next));
      void setActiveWorkspace(next.id);
    } else {
      load('', createEmptyFrame());
    }
  } else {
    publish(state.workspaces, activeId);
  }
}

/** Re-seed the active workflow layout from its preset (discarding tweaks). */
export async function resetLayout(): Promise<void> {
  const id = layoutStore.getSnapshot().workspaceId;
  const preset = presetFor(id);
  if (!id || !preset) return;
  if (saveTimer) {
    clearTimeout(saveTimer);
    saveTimer = null;
  }
  const frameState = seed(preset);
  // A reseed drops whatever the user had added. Those panes are closed, not
  // merely unmounted, so their sessions go with them — but the ones the preset
  // still declares keep theirs.
  closeWorkspaceSessions(id, new Set(listPanes(frameState).map((p) => p.pane.instanceId)));
  load(id, frameState);
  await saveWorkspace(id, { layout: serialize(frameState) });
}

export async function deleteActiveWorkspace(): Promise<void> {
  const id = layoutStore.getSnapshot().workspaceId;
  if (!id || presetFor(id)) return;
  await removeWorkspace(id);
}
