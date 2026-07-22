/**
 * The browser → library bridge: turn what's on screen into vector-store sources.
 *
 * This is the seam the two modules meet at. The browser knows the *live DOM* — the
 * only place an image's alt text, its figcaption, and the heading above it exist
 * together. The library knows how to chunk, embed, and store. Neither should learn
 * the other's internals, so the translation lives here: `MediaItem` (DOM shape) →
 * `MediaAsset` (storage shape).
 *
 * Why capture media by *reference and description* rather than by bytes: the app's
 * embedder is text-only (backend/modules/database/embeddings.py), so an image is
 * searchable through the words around it. That means the harvest has to happen while
 * the page is still open — by the time a URL reaches the ingest pipeline the context
 * is gone. See docs/modules/browser.mdx#saving-to-the-library.
 */
import { clipStatus, ingestSource, type MediaAsset, type SourceModel } from '../library/api';
import { engine, type MediaItem, type PageMedia } from './session';

/** Where a capture goes and how it's labelled. */
export interface CaptureOptions {
  library?: string;
  tags?: string[];
}

/**
 * DOM shape → storage shape.
 *
 * `context[0]` is the figcaption when the harvester found one (it pushes the caption
 * before the heading), so it's promoted to `caption` and the rest stays as context —
 * a caption is a real description of the asset, whereas a heading is only proximity.
 */
function toAsset(item: MediaItem, pageUrl: string): MediaAsset {
  const [caption, ...rest] = item.context ?? [];
  return {
    src: item.src,
    kind: item.kind,
    page_url: pageUrl,
    alt: item.alt || null,
    caption: caption ?? null,
    context: rest,
    width: item.width,
    height: item.height,
    duration: item.duration ?? null,
    poster: item.poster ?? null,
  };
}

/** True when an asset has *any* text to embed. */
export function isDescribed(item: MediaItem): boolean {
  return Boolean(item.alt?.trim() || item.title?.trim() || item.context?.some((c) => c.trim()));
}

// Whether the library can index an image by its *appearance*. Cached per session:
// the extra and the setting don't flip mid-session in practice, and this is consulted
// once per media item when saving a whole page.
let clipProbe: Promise<boolean> | null = null;
function clipEnabled(): Promise<boolean> {
  if (!clipProbe) {
    clipProbe = clipStatus()
      .then((s) => s.enabled)
      .catch(() => false);
  }
  return clipProbe;
}

/**
 * Whether this asset can be saved at all.
 *
 * Text-only, an undescribed image is genuinely unfindable — the best index it could
 * get is a filename like `IMG_4821.png`, which no realistic query matches — so we
 * refuse rather than bury a row nothing can reach. CLIP removes that constraint
 * entirely: the pixels become the index, and description turns into a bonus.
 */
export async function isSavable(item: MediaItem): Promise<boolean> {
  return isDescribed(item) || (await clipEnabled());
}

/**
 * Save the page currently open in the engine.
 *
 * Preferred path: the `capture` op archives the live post-JS DOM as one
 * self-contained HTML artifact (stored server-side), and the source is filed as
 * a `page` pointing at it — openable offline in the Page Viewer. If capture
 * fails (older backend, mid-navigation), fall back to the original text-only
 * `blog`-by-URL ingest so saving still degrades to something useful.
 */
export async function capturePage(opts: CaptureOptions = {}): Promise<SourceModel> {
  try {
    const cap = await engine.capture();
    return await ingestSource({
      type: 'page',
      url: cap.url,
      title: cap.title,
      author: cap.author ?? undefined,
      artifact_id: cap.artifact_id,
      library: opts.library,
      tags: opts.tags,
    });
  } catch {
    const content = await engine.content();
    return ingestSource({
      type: 'blog',
      url: content.url,
      title: content.title,
      author: content.author ?? undefined,
      library: opts.library,
      tags: opts.tags,
    });
  }
}

/** List the media on the live page, with the text that describes each item. */
export function pageMedia(): Promise<PageMedia> {
  return engine.media();
}

/** Save one image/video from the live page. */
export function captureMedia(
  item: MediaItem,
  pageUrl: string,
  opts: CaptureOptions = {},
): Promise<SourceModel> {
  return ingestSource({
    type: item.kind === 'image' ? 'image' : 'video',
    title: item.alt || item.title || undefined,
    asset: toAsset(item, pageUrl),
    library: opts.library,
    tags: opts.tags,
  });
}

export interface CaptureAllResult {
  saved: SourceModel[];
  /** Assets skipped as unindexable — no describing text, and no visual index either. */
  skipped: number;
}

/**
 * Save every savable image/video on the live page.
 *
 * Sequential on purpose: each source is embedded on ingest, and a page with 60
 * images would otherwise fire 60 concurrent embed calls at a local Ollama — or 60
 * CLIP inferences at a single-worker executor.
 */
export async function captureAllMedia(opts: CaptureOptions = {}): Promise<CaptureAllResult> {
  const media = await pageMedia();
  const items = [...media.images, ...media.videos];
  const withClip = await clipEnabled();
  const saved: SourceModel[] = [];
  let skipped = 0;
  for (const item of items) {
    if (!isDescribed(item) && !withClip) {
      skipped += 1;
      continue;
    }
    saved.push(await captureMedia(item, media.url, opts));
  }
  return { saved, skipped };
}
