/**
 * The catalog of shells this node can launch, from `GET /api/terminal/shells`.
 *
 * Fetched once and shared: every terminal pane wants the same list, and the backend
 * probe behind it shells out to `wsl.exe` on its first call. A pane that cannot get
 * the list still works — it just spawns the platform default, which is what it did
 * before there was a picker.
 *
 * See docs/modules/terminal.mdx.
 */
import { useEffect, useState } from 'react';

import { apiGet, apiPost } from '../../api';

export interface ShellInfo {
  id: string;
  label: string;
  kind: string;
  /** Display only — `start` carries the id. See `shells.resolve` on the backend. */
  path: string;
  note: string | null;
}

export interface ShellCatalog {
  shells: ShellInfo[];
  /** The id a session with no `shell` gets, or null when it is unnamed. */
  default: string | null;
}

const EMPTY: ShellCatalog = { shells: [], default: null };

let cache: Promise<ShellCatalog> | null = null;

export function loadShells(refresh = false): Promise<ShellCatalog> {
  if (refresh) cache = null;
  cache ??= (
    refresh
      ? apiPost<ShellCatalog>('/terminal/shells/refresh', {})
      : apiGet<ShellCatalog>('/terminal/shells')
  ).catch(() => EMPTY);
  return cache;
}

/** An entry that can actually be launched — the "could not ask" rows carry no path. */
export function isLaunchable(shell: ShellInfo): boolean {
  return shell.path !== '';
}

export function useShells(): ShellCatalog {
  const [catalog, setCatalog] = useState<ShellCatalog>(EMPTY);
  useEffect(() => {
    let live = true;
    void loadShells().then((c) => {
      if (live) setCatalog(c);
    });
    return () => {
      live = false;
    };
  }, []);
  return catalog;
}

/** The label for an id, falling back to the id so an unknown one is still legible. */
export function shellLabel(catalog: ShellCatalog, id: string | null): string {
  if (!id) return 'Default shell';
  return catalog.shells.find((s) => s.id === id)?.label ?? id;
}
