/**
 * Tabs for the native browser pane.
 *
 * Each tab is its own native webview (`${paneId}:${tabId}` to the shell), so a tab
 * keeps its page, scroll, form state and login while another is in front. The tab
 * list lives in the pane session ({@link NativeTabsHeld}), not in React state, for
 * the reason everything native here does: a workspace switch unmounts the pane but
 * must not close its tabs (see layout/pane-lifetime, "unmount is not close").
 *
 * The shell reports what pages do on their own (`browser-webview` events): loads
 * keep the URL bar honest when a link is clicked *inside* the page, titles label
 * the tabs, `target=_blank` opens a tab here instead of a stray OS window, and
 * downloads surface in the pane. Back/forward read the tab's real history over CDP
 * where the host has it (Windows); elsewhere a per-tab stack built from loads
 * stands in.
 */
import { useCallback, useEffect, useReducer, useState } from 'react';

import { hasCapability } from '../../../capabilities';
import { windowControl, type BrowserWebviewEvent } from '../../../window';
import {
  goHistory,
  nativeEngine,
  navigationHistory,
  registerNativeTarget,
  touchNativeTarget,
} from '../native-engine';

export interface NativeTab {
  id: string;
  /** The shell's id for this tab's webview. */
  webviewId: string;
  /** Where the tab was last *asked* to go (URL bar, bookmark, agent). */
  target: string;
  /** Bumped to re-issue navigation to `target` — also how reload works. */
  seq: number;
  /** Where the page actually is, as the shell reports it. */
  liveUrl: string;
  title: string;
  loading: boolean;
  canBack: boolean;
  canForward: boolean;
  /** The webview exists — a remount attaches to it instead of re-creating it. */
  created: boolean;
  /** Fallback history (hosts without CDP): URLs seen, and where we are in them. */
  stack: string[];
  idx: number;
  /** The next load is a back/forward we issued, so it must not push. */
  replaying: boolean;
}

export interface DownloadStatus {
  url: string;
  path: string | null;
  state: 'started' | 'done' | 'failed';
}

/** The pane-session half: everything a remount must find where it left it. */
export interface NativeTabsHeld {
  tabs: NativeTab[];
  active: string | null;
  next: number;
  /** Agent-target releases, one per live webview. */
  targets: Map<string, () => void>;
}

export function newNativeTabsHeld(): NativeTabsHeld {
  return { tabs: [], active: null, next: 1, targets: new Map() };
}

/** Close every webview the pane holds — the pane itself is closing. */
export function disposeNativeTabs(held: NativeTabsHeld): void {
  const control = windowControl()?.browserWebview;
  for (const tab of held.tabs) {
    held.targets.get(tab.webviewId)?.();
    control?.close(tab.webviewId).catch(() => {
      // Shutdown races (window already gone) are not worth surfacing.
    });
  }
  held.targets.clear();
  held.tabs = [];
  held.active = null;
}

/** How long a finished download stays in the pane's status line. */
const DOWNLOAD_NOTICE_MS = 8000;

export function useNativeTabs(held: NativeTabsHeld | null, paneId: string, initialUrl: string) {
  const [, rerender] = useReducer((n: number) => n + 1, 0);
  const [download, setDownload] = useState<DownloadStatus | null>(null);
  const canCdp = hasCapability('browser.nativeCdp');

  /** Replace one tab (immutably, so React sees it) and repaint. */
  const update = useCallback(
    (tabId: string, patch: (tab: NativeTab) => Partial<NativeTab>) => {
      if (!held) return;
      held.tabs = held.tabs.map((t) => (t.id === tabId ? { ...t, ...patch(t) } : t));
      rerender();
    },
    [held],
  );

  const open = useCallback(
    (url: string, activate = true): string | null => {
      if (!held) return null;
      const id = `t${held.next++}`;
      held.tabs = [
        ...held.tabs,
        {
          id,
          webviewId: `${paneId}:${id}`,
          target: url,
          seq: 0,
          liveUrl: '',
          title: '',
          loading: Boolean(url),
          canBack: false,
          canForward: false,
          created: false,
          stack: [],
          idx: -1,
          replaying: false,
        },
      ];
      if (activate || !held.active) held.active = id;
      rerender();
      return id;
    },
    [held, paneId],
  );

  // A pane opened with a URL starts on it.
  useEffect(() => {
    if (held && held.tabs.length === 0 && initialUrl) open(initialUrl);
  }, [held, initialUrl, open]);

  const activeTab = held?.tabs.find((t) => t.id === held.active) ?? null;

  const activate = useCallback(
    (tabId: string) => {
      if (!held) return;
      held.active = tabId;
      const tab = held.tabs.find((t) => t.id === tabId);
      if (tab) touchNativeTarget(tab.webviewId);
      rerender();
    },
    [held],
  );

  const close = useCallback(
    (tabId: string) => {
      if (!held) return;
      const index = held.tabs.findIndex((t) => t.id === tabId);
      const tab = held.tabs[index];
      if (!tab) return;
      held.targets.get(tab.webviewId)?.();
      held.targets.delete(tab.webviewId);
      windowControl()
        ?.browserWebview?.close(tab.webviewId)
        .catch(() => {});
      held.tabs = held.tabs.filter((t) => t.id !== tabId);
      if (held.active === tabId) {
        // The neighbour to the right takes its place, as in every browser.
        held.active = (held.tabs[index] ?? held.tabs[index - 1])?.id ?? null;
      }
      rerender();
    },
    [held],
  );

  /** Point the active tab at `url` (opening the first tab if there is none). */
  const navigate = useCallback(
    (url: string) => {
      if (!held) return;
      if (!activeTab) {
        open(url);
        return;
      }
      update(activeTab.id, (t) => ({ target: url, seq: t.seq + 1, loading: true }));
      touchNativeTarget(activeTab.webviewId);
    },
    [held, activeTab, open, update],
  );

  const reload = useCallback(() => {
    if (!activeTab) return;
    const url = activeTab.liveUrl || activeTab.target;
    update(activeTab.id, (t) => ({ target: url, seq: t.seq + 1, loading: true, replaying: true }));
  }, [activeTab, update]);

  const step = useCallback(
    (delta: number) => {
      if (!activeTab) return;
      if (canCdp && activeTab.created) {
        goHistory(activeTab.webviewId, delta).catch(() => {});
        return;
      }
      const idx = activeTab.idx + delta;
      const url = activeTab.stack[idx];
      if (!url) return;
      update(activeTab.id, (t) => ({ idx, target: url, seq: t.seq + 1, replaying: true }));
    },
    [activeTab, canCdp, update],
  );

  /** A tab's webview now exists: make it drivable by the agent. */
  const onCreated = useCallback(
    (webviewId: string) => {
      if (!held) return;
      const tab = held.tabs.find((t) => t.webviewId === webviewId);
      if (!tab) return;
      if (!held.targets.has(webviewId)) {
        held.targets.set(webviewId, registerNativeTarget(webviewId, paneId));
      }
      if (held.active === tab.id) touchNativeTarget(webviewId);
      update(tab.id, () => ({ created: true }));
    },
    [held, paneId, update],
  );

  // --- what the pages report ---------------------------------------------
  useEffect(() => {
    const control = windowControl()?.browserWebview;
    if (!held || !control) return;
    const prefix = `${paneId}:`;
    let noticeTimer = 0;

    const refreshHistory = (tab: NativeTab) => {
      if (!canCdp) return;
      navigationHistory(tab.webviewId)
        .then((h) =>
          update(tab.id, () => ({
            canBack: h.currentIndex > 0,
            canForward: h.currentIndex < h.entries.length - 1,
          })),
        )
        .catch(() => {});
    };

    const off = control.onEvent((event: BrowserWebviewEvent) => {
      if (!event.id.startsWith(prefix)) return;
      const tab = held.tabs.find((t) => t.webviewId === event.id);
      if (!tab) return;
      switch (event.kind) {
        case 'load':
          if (event.loading) {
            update(tab.id, () => ({ loading: true, liveUrl: event.url }));
            return;
          }
          update(tab.id, (t) => {
            if (canCdp || t.replaying || t.stack[t.idx] === event.url) {
              return { loading: false, liveUrl: event.url, replaying: false };
            }
            const stack = [...t.stack.slice(0, t.idx + 1), event.url];
            return {
              loading: false,
              liveUrl: event.url,
              stack,
              idx: stack.length - 1,
              canBack: stack.length > 1,
              canForward: false,
            };
          });
          refreshHistory(tab);
          return;
        case 'title':
          update(tab.id, () => ({ title: event.title }));
          return;
        case 'newTab':
          open(event.url);
          return;
        case 'download':
          setDownload({ url: event.url, path: event.path, state: event.state });
          clearTimeout(noticeTimer);
          if (event.state !== 'started') {
            noticeTimer = window.setTimeout(() => setDownload(null), DOWNLOAD_NOTICE_MS);
          }
          return;
      }
    });

    // Events that fired while this pane was unmounted (another workspace in front)
    // were missed; ask the live pages where they are now.
    if (canCdp) {
      for (const tab of held.tabs) {
        if (!tab.created) continue;
        nativeEngine(tab.webviewId)
          .info()
          .then((page) => update(tab.id, () => ({ liveUrl: page.url, title: page.title })))
          .catch(() => {});
        refreshHistory(tab);
      }
    }

    return () => {
      off();
      clearTimeout(noticeTimer);
    };
  }, [held, paneId, canCdp, open, update]);

  return {
    tabs: held?.tabs ?? [],
    activeTab,
    download,
    dismissDownload: () => setDownload(null),
    open,
    close,
    activate,
    navigate,
    reload,
    back: () => step(-1),
    forward: () => step(1),
    onCreated,
  };
}
