/**
 * Opening an item — live in the browser, or its offline copy — and recording that
 * the user opened it (`to_read` → `reading`). Shared by the pane, the agent's
 * `kb.open` tool and search results.
 */
import { registry } from '../../registry';
import { markOpened, type Item } from './api';
import { upsert } from './store';

export function hasOffline(item: Pick<Item, 'docpage_id' | 'artifact_id'>): boolean {
  return Boolean(item.docpage_id || item.artifact_id);
}

export function openItem(item: Item, where: 'live' | 'offline' = 'live'): boolean {
  if (where === 'offline' && item.docset_id && item.docpage_id) {
    registry.openPanel('docviewer.browse', {
      params: { setId: item.docset_id, pageId: item.docpage_id },
    });
  } else if (where === 'offline' && item.artifact_id) {
    registry.openPanel('research.pageViewer', { params: { artifactId: item.artifact_id } });
  } else if (item.url) {
    registry.openPanel('browser.view', { params: { url: item.url } });
  } else {
    return false;
  }
  markOpened(item.id)
    .then(upsert)
    .catch(() => {});
  return true;
}
