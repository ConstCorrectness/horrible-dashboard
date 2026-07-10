/**
 * Workspace persistence for the frame engine. One rule kills the old engine's
 * race guards: the store's `workspaceId` and `frame` are a single atom, and the
 * debounced autosave reads BOTH from the same snapshot at fire time — so a
 * workspace switch (which swaps the atom and flushes first) can never cross-write
 * a layout under the wrong id. The backend keeps storing layouts opaquely.
 */
import { registry } from '../registry';
import {
  createWorkspace as apiCreateWorkspace,
  deleteWorkspace as apiDeleteWorkspace,
  getWorkspaces,
  saveWorkspace,
  setActiveWorkspace,
  type Workspace as WorkspaceModel,
} from '../workspace';
import { workspaceStore } from '../workspace-store';
import { createEmptyFrame } from './model';
import { seedFromPreset, type FramePreset } from './presets';
import { regionsFor } from './controller';
import { deserialize, serialize } from './serialize';
import { layoutStore } from './store';
import type { FrameState } from './types';

const AUTOSAVE_MS = 600;

let saveTimer: ReturnType<typeof setTimeout> | null = null;
let lastSavedRevision = 0;
let autosaveBound = false;

function knownViews(): ReadonlySet<string> {
  return new Set([...registry.panels.map((p) => p.id), ...registry.widgets.map((w) => w.id)]);
}

function presetFor(id: string | null): FramePreset | undefined {
  return id ? registry.framePresets.find((p) => p.id === id) : undefined;
}

function seed(preset: FramePreset): FrameState {
  return seedFromPreset(preset, { knownViews: knownViews(), regionsFor });
}

/** A workspace's frame: its stored blob if it is one of ours, else its preset
 * seed (covers pre-frame legacy blobs — discarded by design), else empty. */
function frameOf(ws: WorkspaceModel): FrameState {
  const restored = deserialize(ws.layout, knownViews());
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

/** Boot: fetch workspaces (seeding the default preset on an empty slate) and
 * load the active one into the store. */
export async function hydrate(): Promise<void> {
  let state = await getWorkspaces();
  const defaultPreset = presetFor('dashboard') ?? registry.framePresets[0];
  if (state.workspaces.length === 0 && defaultPreset) {
    await saveWorkspace(defaultPreset.id, {
      name: defaultPreset.name,
      layout: serialize(seed(defaultPreset)),
    });
    state = await getWorkspaces();
  }
  const activeId = state.active ?? state.workspaces[0]?.id ?? null;
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

/** Prompt-free create, shared by the workspace.new command and the agent tool. */
export async function createNamedWorkspace(name: string): Promise<{ id: string; name: string }> {
  await flush();
  const ws = await apiCreateWorkspace(name);
  const state = await getWorkspaces();
  publish(state.workspaces, ws.id);
  load(ws.id, createEmptyFrame());
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
  await saveWorkspace(id, { name });
  const s = await getWorkspaces();
  publish(s.workspaces, layoutStore.getSnapshot().workspaceId);
}

export async function removeWorkspace(id: string): Promise<void> {
  if (presetFor(id)) return; // predefined layouts reset rather than delete
  const state = await apiDeleteWorkspace(id);
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
  load(id, frameState);
  await saveWorkspace(id, { layout: serialize(frameState) });
}

export async function deleteActiveWorkspace(): Promise<void> {
  const id = layoutStore.getSnapshot().workspaceId;
  if (!id || presetFor(id)) return;
  await removeWorkspace(id);
}
