/**
 * The **workbench** — the IDE surface: a tab strip of open files over one nested
 * `BufferView`, with Explorer / Search / Source Control / Outline / Provenance on
 * the left, the agent on the right, and Terminal / Problems below.
 *
 * Those sidebars are **not rendered here.** They are `regions:` declarations in
 * this module's manifest, drawn by the ui layer's `PaneWithRegions`. That is not
 * a stylistic choice: `packages/core` cannot import `@horrible/ui` (ui depends on
 * core), so a pane living here has no way to host another module's view itself.
 * The happy consequence is that the strips get resize, per-instance persistence,
 * collapse-to-rail, tabbing and drag-out for free instead of a second docking
 * engine growing inside a pane.
 *
 * So what is left here is exactly the part the frame does not already do: which
 * files are open, which one is showing, and hosting `BufferView` as something
 * other than a pane. See docs/modules/ide.mdx.
 */
import { useContext, useEffect, useMemo, useSyncExternalStore } from 'react';

import { PaneInstanceContext, SectionInstanceContext, useAgentContext } from '../../agent-context';
import { dialogs } from '../../dialogs';
import { registerCloseGuard } from '../../layout/close-guards';
import { closePaneSession, paneSessionKey } from '../../layout/pane-lifetime';
import { findPaneAnywhere } from '../../layout/model';
import { layoutStore } from '../../layout/store';
import { PaneParamsContext } from '../../panes';
import { BufferView } from '../editor/BufferView';
import {
  forgetUnsaved,
  isSourceDirty,
  readUnsaved,
  saveSource,
  setBufferHost,
  sourceTitle,
  subscribeUnsaved,
  type BufferHost,
} from '../editor';
import { getBuffer } from '../editor/buffers';
import { retainDiagnostics } from '../editor/lsp-registry';
import { IdeTabStrip } from './IdeTabStrip';
import { activateTab, closeTab, openTab, readTabs, renameTab } from './openBuffers';
import './ide.css';

/**
 * The instance id handed to the nested `BufferView`. Synthetic and per-source,
 * mirroring the convention `Region.tsx` already uses for a strip's content
 * (`<host>:<position>:<view>`) — it is a view the host renders, not a pane the
 * layout owns.
 */
export function tabInstanceId(hostInstanceId: string, uri: string): string {
  return `${hostInstanceId}:buffer:${uri}`;
}

/** The live workbench, if one is open — what `openInWorkbench` and the agent
 * tools resolve against. Set by the mounted component. */
let liveHostId: string | null = null;

/** The workbench instance currently rendering, or null when none is open. */
export function workbenchInstanceId(): string | null {
  return liveHostId;
}

export function IdeWorkbench() {
  const hostId = useContext(PaneInstanceContext);
  const { tabs, active } = useSyncExternalStore(
    layoutStore.subscribe,
    () => tabsSnapshot(hostId),
    () => tabsSnapshot(hostId),
  );

  // Dirty dots come from the unsaved cache, not from `buffers.ts`: that registry
  // is mount-scoped and only the active tab is mounted, so every background tab
  // would show clean no matter what you had typed in it.
  const dirtyVersion = useSyncExternalStore(subscribeUnsaved, unsavedVersion, unsavedVersion);
  const dirty = useMemo(
    () => new Set(tabs.filter((uri) => isSourceDirty(uri))),
    // `dirtyVersion` is the subscription's change signal; the Set is derived
    // from the cache, which the version stands in for.
    [tabs, dirtyVersion],
  );

  // Claim every `openBuffer` while this workbench is on screen. Without it a file
  // clicked in the workbench's own Explorer strip splits off a second
  // `editor.buffer` pane holding the same URI — two CodeMirror views registered
  // under one source, where saving is last-writer-wins.
  useEffect(() => {
    if (!hostId) return;
    liveHostId = hostId;
    const host: BufferHost = {
      open(source) {
        openTab(hostId, source);
        return true;
      },
      adopt(instanceId, uri) {
        const from = sourceOfTabInstance(hostId, instanceId);
        return from !== null && renameTab(hostId, from, uri);
      },
    };
    setBufferHost(host);
    return () => {
      setBufferHost(null);
      if (liveHostId === hostId) liveHostId = null;
    };
  }, [hostId]);

  // Keep diagnostics for every open tab, not just the mounted one. The LSP
  // registry drops a buffer's diagnostics when its CodeMirror view is destroyed,
  // which happens on every tab switch — so without this the Problems panel would
  // list only the file already showing its errors in the gutter.
  useEffect(() => {
    const releases = tabs.map((uri) => retainDiagnostics(uri));
    return () => releases.forEach((release) => release());
  }, [tabs]);

  // The nested BufferView registers a close guard under its synthetic id, which
  // no close path knows about — so the workbench owns the real one and answers
  // for every tab at once, VS Code's "save all / don't save / cancel".
  useEffect(() => {
    if (!hostId) return;
    return registerCloseGuard(hostId, async () => {
      const proceed = await confirmDiscardAll(hostId);
      if (proceed) releaseRegionSessions(hostId);
      return proceed;
    });
  }, [hostId]);

  // No section id: this is the *pane's* context — the open files and which one
  // is showing. The nested buffer registers under the `editor` section instead
  // (see the provider below), so the two merge rather than overwrite.
  useAgentContext(() => ({
    openFiles: tabs.map((uri) => ({ uri, title: sourceTitle(uri), dirty: dirty.has(uri) })),
    activeFile: active,
  }));

  const onClose = async (uri: string) => {
    if (!hostId) return;
    if (isSourceDirty(uri) && !(await confirmDiscard(uri))) return;
    closeTab(hostId, uri);
  };

  return (
    <div className="ide-workbench">
      <IdeTabStrip
        tabs={tabs}
        active={active}
        dirty={dirty}
        onSelect={(uri) => hostId && activateTab(hostId, uri)}
        onClose={onClose}
      />
      <div className="ide-editor">
        {active && hostId ? (
          <PaneInstanceContext.Provider value={tabInstanceId(hostId, active)}>
            {/* The buffer registers its agent context as a *section* of this
                pane. Without this provider it would register at the pane level
                — the same slot the workbench uses above — and the two would
                silently overwrite each other, last mount winning. As a section
                they merge instead. */}
            <SectionInstanceContext.Provider value="editor">
              <PaneParamsContext.Provider value={paramsFor(active)}>
                <BufferView key={active} />
              </PaneParamsContext.Provider>
            </SectionInstanceContext.Provider>
          </PaneInstanceContext.Provider>
        ) : (
          <IdeEmpty />
        )}
      </div>
    </div>
  );
}

function IdeEmpty() {
  return (
    <div className="ide-empty">
      <p className="ide-empty-title">No file open</p>
      <p className="ide-empty-hint">
        Pick one from the Explorer, or search the workspace with Search.
      </p>
    </div>
  );
}

/** The params a real `editor.buffer` pane would have been opened with. */
function paramsFor(uri: string): Record<string, unknown> {
  // `panelId` because `PaneHost` always sets it and legacy panes read it.
  return { panelId: 'editor.buffer', source: uri, title: sourceTitle(uri) };
}

/** Which open source a synthetic tab instance id refers to. */
function sourceOfTabInstance(hostId: string, instanceId: string): string | null {
  const prefix = `${hostId}:buffer:`;
  return instanceId.startsWith(prefix) ? instanceId.slice(prefix.length) : null;
}

// `useSyncExternalStore` compares snapshots by identity, so a fresh object each
// call would loop forever. Cache until the underlying tabs actually change.
let tabsCache: { key: string; value: ReturnType<typeof readTabs> } | null = null;

function tabsSnapshot(hostId: string | null) {
  if (!hostId) return EMPTY_TABS;
  const next = readTabs(hostId);
  // JSON, not a joined string: a delimiter has to be a character that cannot
  // appear in a path, and the only one that fits (NUL) makes this source file
  // binary — invisible to grep, to ripgrep, and to this app's own text search.
  const key = JSON.stringify([hostId, next.tabs, next.active]);
  if (!tabsCache || tabsCache.key !== key) tabsCache = { key, value: next };
  return tabsCache.value;
}

const EMPTY_TABS = { tabs: [] as string[], active: null as string | null };

// A monotonic counter standing in for "the unsaved cache changed" — the cache is
// a Map, which has no snapshot identity to compare.
let unsavedTick = 0;
subscribeUnsaved(() => {
  unsavedTick += 1;
});
function unsavedVersion(): number {
  return unsavedTick;
}

/** VS Code's three-way prompt for one file. */
async function confirmDiscard(uri: string): Promise<boolean> {
  const choice = await dialogs.choice({
    title: `Do you want to save the changes you made to ${sourceTitle(uri)}?`,
    message: "Your changes will be lost if you don't save.",
    buttons: [
      { label: 'Save', value: 'save', primary: true },
      { label: "Don't Save", value: 'dontSave' },
      { label: 'Cancel', value: 'cancel' },
    ],
    cancelValue: 'cancel',
  });
  if (choice === 'save') return saveUnmounted(uri);
  if (choice === 'dontSave') {
    // Drop the content as well as the tab. The cache is keyed by source and
    // outlives both, so leaving it hands the declined changes straight back the
    // next time the file is opened.
    forgetUnsaved(uri);
    return true;
  }
  return false;
}

/** The same prompt for every dirty tab at once, when the pane itself closes. */
async function confirmDiscardAll(hostId: string): Promise<boolean> {
  const pending = readTabs(hostId).tabs.filter((uri) => isSourceDirty(uri));
  if (pending.length === 0) return true;
  const choice = await dialogs.choice({
    title:
      pending.length === 1
        ? `Do you want to save the changes you made to ${sourceTitle(pending[0])}?`
        : `Do you want to save the changes to ${pending.length} files?`,
    message: "Your changes will be lost if you don't save.",
    buttons: [
      { label: pending.length === 1 ? 'Save' : 'Save All', value: 'save', primary: true },
      { label: "Don't Save", value: 'dontSave' },
      { label: 'Cancel', value: 'cancel' },
    ],
    cancelValue: 'cancel',
  });
  if (choice === 'save') {
    const results = await Promise.all(pending.map(saveUnmounted));
    return results.every(Boolean);
  }
  if (choice === 'dontSave') {
    pending.forEach(forgetUnsaved);
    return true;
  }
  return false;
}

/**
 * Save a file whose editor may not be mounted. The mounted one saves through its
 * controller (which knows the revision it loaded); the rest write the cached
 * content straight to the source model, because there is no view to ask.
 */
async function saveUnmounted(uri: string): Promise<boolean> {
  const controller = getBuffer(uri);
  if (controller) {
    // The controller's `save` resolves either way; a failure leaves the source
    // dirty, which is the honest answer to "did this close succeed".
    await controller.save();
    return !isSourceDirty(uri);
  }
  const cached = readUnsaved(uri);
  if (!cached) return true;
  try {
    await saveSource(uri, cached.content);
    forgetUnsaved(uri);
    return true;
  } catch {
    return false;
  }
}

/**
 * Dispose the sessions the workbench's region views own — a terminal's PTY above
 * all. A region's content takes its `paneSession` under the synthetic
 * `<host>:<position>:<view>` key, which no close path passes to
 * `closePaneSession`, so without this the shell outlives the pane that opened it.
 * Written generically so it covers any future resource-owning strip.
 */
function releaseRegionSessions(hostId: string): void {
  const { frame, workspaceId } = layoutStore.getSnapshot();
  const pane = findPaneAnywhere(frame, hostId)?.pane;
  if (!pane?.regions) return;
  for (const position of ['left', 'right', 'bottom'] as const) {
    for (const view of pane.regions[position]?.views ?? []) {
      closePaneSession(paneSessionKey(workspaceId, `${hostId}:${position}:${view}`));
    }
  }
}
