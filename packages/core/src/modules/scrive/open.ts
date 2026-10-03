/**
 * The single entry point for showing a Scrive page — the sites pane, the create
 * flow and (later) the agent all land in the same pane for the same page.
 */
import { openDocument } from '../../layout/controller';

export const PAGE_VIEW = 'scrive.page';

export function pageInstanceId(site: string, path: string): string {
  return `${PAGE_VIEW}:${site}/${path}`;
}

/**
 * Open `path` of `site`. Reopening focuses the pane that already holds it; otherwise
 * a page pane with no unsaved edits is retargeted in place (the page panel reports
 * its edits through `setPaneDirty`, which `openDocument` checks before reusing), and
 * only when every page pane is dirty does a new one appear. `title` is what the tab
 * reads, so it is the file name rather than "Page".
 */
export function openScrivePage(site: string, path: string): void {
  const title = path.split('/').pop() || path;
  openDocument(PAGE_VIEW, pageInstanceId(site, path), { site, path, title }, () => true);
}

export const PUBLISH_VIEW = 'scrive.publish';

/** The publish pane for `site` — one per site, reopened rather than duplicated. */
export function openPublish(site: string): void {
  openDocument(
    PUBLISH_VIEW,
    `${PUBLISH_VIEW}:${site}`,
    { site, title: `Publish ${site}` },
    () => true,
  );
}

export const SHARE_VIEW = 'scrive.share';
export const OUTBOX_VIEW = 'scrive.outbox';

/** A page's Share pane — its X, LinkedIn and YouTube composers. `page: ''` is the
 * site's posts that belong to no page. */
export function openShare(site: string, page: string): void {
  const name = page.split('/').pop() || page || site;
  openDocument(
    SHARE_VIEW,
    `${SHARE_VIEW}:${site}/${page}`,
    { site, path: page, title: `Share ${name}` },
    () => true,
  );
}

export const CLIP_VIEW = 'scrive.clip';

/**
 * The clip editor for `path` (an edit list, `*.clip.json`), or — with no path — the
 * site's clips and videos to pick from.
 */
export function openClip(site: string, path = ''): void {
  const name = path ? (path.split('/').pop() ?? path).replace(/\.clip\.json$/, '') : site;
  openDocument(
    CLIP_VIEW,
    `${CLIP_VIEW}:${site}/${path}`,
    { site, path, title: path ? `Clip ${name}` : `Clips ${site}` },
    () => true,
  );
}
