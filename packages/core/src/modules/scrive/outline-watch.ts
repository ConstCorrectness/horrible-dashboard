/**
 * Brings the agent's drafts in front of the person, wherever they are in the app.
 *
 * - An outline the agent proposes (`outline.changed`, status `proposed`) opens in
 *   the outline pane at once: the agent has stopped and is waiting on a review.
 * - A page the agent creates directly (`page.changed`, `added`, origin `agent`)
 *   opens in a page pane, where its writes show as reviewable edits.
 * - The outlines still awaiting review are kept here for the shell indicator.
 *
 * Module-level and started once: the shell may mount its indicator slot in more than
 * one chrome surface, and a pane must open once per event, not once per mount.
 */
import { useSyncExternalStore } from 'react';

import { openDocument } from '../../layout/controller';
import { subscribeChannel } from '../../ws';
import {
  listOutlines,
  SCRIVE_CHANNEL,
  type Outline,
  type OutlineChanged,
  type PageChanged,
} from './api';
import { openScrivePage } from './open';

export const OUTLINE_VIEW = 'scrive.outline';

export function openOutline(site: string, id: string, title = 'Outline'): void {
  openDocument(OUTLINE_VIEW, `${OUTLINE_VIEW}:${site}/${id}`, { site, id, title }, () => true);
}

let proposed: Outline[] = [];
const listeners = new Set<() => void>();
let started = false;

function emit(): void {
  for (const listener of listeners) listener();
}

async function refresh(): Promise<void> {
  try {
    proposed = await listOutlines('proposed');
  } catch {
    // Backend not up yet; the next event refreshes.
    return;
  }
  emit();
}

export function startOutlineWatch(): void {
  if (started) return;
  started = true;
  void refresh();
  subscribeChannel(SCRIVE_CHANNEL, (msg) => {
    if (msg.event === 'outline.changed') {
      const event = msg.data as OutlineChanged;
      void refresh();
      if (event.status === 'proposed') openOutline(event.site, event.id);
      return;
    }
    if (msg.event === 'page.changed') {
      const event = msg.data as PageChanged;
      if (event.change === 'added' && event.origin === 'agent') {
        openScrivePage(event.site, event.path);
      }
    }
  });
}

export function useProposedOutlines(): Outline[] {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => proposed,
    () => proposed,
  );
}
