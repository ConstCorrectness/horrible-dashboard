/**
 * Module-level Scrive state shared by the panes and the commands.
 *
 * - The **current site** — what the sites pane shows and where "New post" lands.
 *   Remembered per browser (localStorage, guarded: it can throw in a sandboxed or
 *   private context, and losing the remembered choice is harmless).
 * - The **open page controllers**, by pane instance — so `scrive.save` (bound to
 *   mod+s while a page pane has focus) reaches the page that is focused, the way
 *   `editor.save` reaches the active buffer.
 */
import { useSyncExternalStore } from 'react';

const SITE_KEY = 'scrive.currentSite';

let currentSite: string | null = readSite();
const siteListeners = new Set<() => void>();

function readSite(): string | null {
  try {
    return globalThis.localStorage?.getItem(SITE_KEY) ?? null;
  } catch {
    return null;
  }
}

export function getCurrentSite(): string | null {
  return currentSite;
}

export function setCurrentSite(site: string | null): void {
  if (site === currentSite) return;
  currentSite = site;
  try {
    if (site) globalThis.localStorage?.setItem(SITE_KEY, site);
    else globalThis.localStorage?.removeItem(SITE_KEY);
  } catch {
    // Remembering the choice is a convenience; the in-memory value still holds.
  }
  for (const listener of siteListeners) listener();
}

export function useCurrentSite(): string | null {
  return useSyncExternalStore(
    (listener) => {
      siteListeners.add(listener);
      return () => siteListeners.delete(listener);
    },
    () => currentSite,
  );
}

// "Generate a page" asked for from outside the sites pane (the command palette):
// the pane opens its Generate form when it hears this.
const generateListeners = new Set<() => void>();
let generateWanted = false;

export function requestGenerate(): void {
  generateWanted = true;
  for (const listener of generateListeners) listener();
}

/** The sites pane takes a request made before it mounted (once). */
export function claimGenerate(): boolean {
  const wanted = generateWanted;
  generateWanted = false;
  return wanted;
}

export function onGenerate(listener: () => void): () => void {
  generateListeners.add(listener);
  return () => generateListeners.delete(listener);
}

export type PageMode = 'write' | 'source' | 'split' | 'preview';

export interface PageController {
  save(): Promise<void>;
  setMode(mode: PageMode): void;
  /** Ask for a name and write the page as a site template. */
  saveAsTemplate(): Promise<void>;
}

const controllers = new Map<string, PageController>();

export function registerPageController(instanceId: string, controller: PageController): () => void {
  controllers.set(instanceId, controller);
  return () => {
    if (controllers.get(instanceId) === controller) controllers.delete(instanceId);
  };
}

export function getPageController(instanceId: string | null): PageController | undefined {
  return instanceId ? controllers.get(instanceId) : undefined;
}
