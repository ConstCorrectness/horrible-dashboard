/**
 * Typed client for `/api/scrive/*`. Mirrors backend/modules/scrive/models.py.
 *
 * Pages travel as their exact on-disk text (line endings included) with a
 * `revision` — a hash of those bytes. A save names the revision it was based on; a
 * stale one comes back as `{ conflict }` holding the page as it is now, so the
 * editor can reconcile instead of overwriting someone else's change.
 */
import { ApiError, apiDelete, apiGet, apiPost, apiPut } from '../../api';
import type { NbOutput } from '../../notebook/types';
import { apiUrl } from '../../origin';

export type PageKind = 'post' | 'page' | 'notebook';

export interface SiteMeta {
  id: string;
  title: string;
  theme: string;
  root: string;
  pages: number;
}

export interface SiteConfig {
  version: number;
  title: string;
  theme: string;
  targets: Record<string, unknown>;
  defaults: Record<string, unknown>;
}

export interface PageMeta {
  path: string;
  kind: PageKind;
  title: string;
  status: string;
  date: string;
  tags: string[];
  description: string;
  updated_at: number;
  revision: string;
}

export interface Page {
  meta: PageMeta;
  content: string;
}

/** `scrive` channel, event `page.changed`. Includes the echo of our own saves. */
export interface PageChanged {
  site: string;
  path: string;
  change: 'added' | 'modified' | 'deleted';
  revision: string;
  /** `agent` when a Scrive agent tool wrote this revision: the page shows it as a
   * reviewable agent edit. */
  origin?: '' | 'agent';
}

/** `scrive` channel, event `outline.changed`. */
export interface OutlineChanged {
  site: string;
  id: string;
  status: OutlineStatus;
}

export const SCRIVE_CHANNEL = 'scrive';

const q = (path: string) => `path=${encodeURIComponent(path)}`;

export function listSites(): Promise<SiteMeta[]> {
  return apiGet('/scrive/sites');
}

export function createSite(id: string, title: string): Promise<SiteMeta> {
  return apiPost('/scrive/sites', { id, title });
}

// ── web apps (`apps/<name>/`, run on their own origin) ─────────────────────────

export interface AppInfo {
  name: string;
  title: string;
  hasIndex: boolean;
  files: number;
  bytes: number;
  /** `SPACE.json` of an imported Space. */
  source: {
    space: string;
    revision: string;
    license: string;
    url: string;
    skipped: { path: string; size: number; reason: string }[];
  } | null;
  skipped: { path: string; size: number; reason: string }[];
}

export interface AppsOrigin {
  origin: string | null;
  reason: string;
}

let appsOriginPromise: Promise<AppsOrigin> | null = null;

/** Where this backend serves web apps (`http://scrive-apps.localhost:<port>`), once. */
export function getAppsOrigin(): Promise<AppsOrigin> {
  appsOriginPromise ??= apiGet<AppsOrigin>('/scrive/apps-origin');
  appsOriginPromise.catch(() => (appsOriginPromise = null));
  return appsOriginPromise;
}

export function listApps(site: string): Promise<AppInfo[]> {
  return apiGet(`/scrive/sites/${site}/apps`);
}

export function listAppTemplates(): Promise<string[]> {
  return apiGet('/scrive/app-templates');
}

export function createApp(site: string, name: string, template: string): Promise<AppInfo> {
  return apiPost(`/scrive/sites/${site}/apps`, { name, template });
}

export function importSpaceApp(site: string, space: string, name?: string): Promise<AppInfo> {
  return apiPost(`/scrive/sites/${site}/apps/import`, { space, name: name || null });
}

export function getSite(site: string): Promise<{ site: SiteMeta; config: SiteConfig }> {
  return apiGet(`/scrive/sites/${site}`);
}

export function listPages(site: string): Promise<PageMeta[]> {
  return apiGet(`/scrive/sites/${site}/pages`);
}

export function createPage(
  site: string,
  body: {
    kind: 'post' | 'page';
    title: string;
    slug?: string;
    template?: string;
    inputs?: Record<string, string>;
  },
): Promise<Page> {
  return apiPost(`/scrive/sites/${site}/pages`, body);
}

export function readPage(site: string, path: string): Promise<Page> {
  return apiGet(`/scrive/sites/${site}/page?${q(path)}`);
}

export type SaveResult = { page: Page } | { conflict: Page };

/**
 * Save against `baseRevision`. A 409 is answered with the page as it is now. The
 * shared client keeps only an error's `detail`, so the current page is re-read
 * rather than taken from the 409 body — one extra request, on a path that only runs
 * when two writers actually collided.
 */
export async function savePage(
  site: string,
  path: string,
  content: string,
  baseRevision: string,
): Promise<SaveResult> {
  try {
    const page = await apiPut<Page>(`/scrive/sites/${site}/page?${q(path)}`, {
      content,
      base_revision: baseRevision,
    });
    return { page };
  } catch (err) {
    if (err instanceof ApiError && err.status === 409) {
      return { conflict: await readPage(site, path) };
    }
    throw err;
  }
}

export function deletePage(site: string, path: string): Promise<{ ok: boolean }> {
  return apiDelete(`/scrive/sites/${site}/page?${q(path)}`);
}

/**
 * Store a pasted or dropped file in the site (`media/` unless `folder` says
 * otherwise) and answer its site-relative path. Never overwrites: a taken name comes
 * back with `-2`, `-3`…
 */
export async function uploadAsset(site: string, file: File, folder = 'media'): Promise<string> {
  const form = new FormData();
  form.append('file', file, file.name);
  form.append('folder', folder);
  const res = await fetch(apiUrl(`/api/scrive/sites/${site}/assets`), {
    method: 'POST',
    body: form,
  });
  if (!res.ok) {
    let detail = `upload failed: ${res.status}`;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === 'string') detail = body.detail;
    } catch {
      // non-JSON error body — keep the status message
    }
    throw new ApiError(detail, res.status);
  }
  return ((await res.json()) as { path: string }).path;
}

// ── templates ────────────────────────────────────────────────────────────────

export interface TemplateInput {
  description: string;
  required: boolean;
  default: string;
}

export interface TemplateInfo {
  id: string;
  name: string;
  description: string;
  kind: 'post' | 'page';
  inputs: Record<string, TemplateInput>;
  source: 'builtin' | 'site';
  sections: string[];
}

/** Built-in post templates, plus the site's own (which win on a shared id). */
export function listTemplates(site?: string | null): Promise<TemplateInfo[]> {
  return apiGet(`/scrive/templates${site ? `?site=${encodeURIComponent(site)}` : ''}`);
}

export function saveAsTemplate(
  site: string,
  body: { path: string; id: string; name: string; description?: string },
): Promise<TemplateInfo> {
  return apiPost(`/scrive/sites/${site}/templates`, body);
}

// ── outlines ─────────────────────────────────────────────────────────────────

export type OutlineStatus = 'proposed' | 'approved' | 'discarded';

export interface OutlineSection {
  heading: string;
  intent: string;
  level: number;
  figure: string;
}

/** A page an agent proposes to write; it becomes a page only when approved. */
export interface Outline {
  id: string;
  site: string;
  title: string;
  kind: 'post' | 'page';
  template: string;
  inputs: Record<string, string>;
  prompt: string;
  lead: string;
  description: string;
  tags: string[];
  sections: OutlineSection[];
  status: OutlineStatus;
  page: string;
  created_at: number;
  updated_at: number;
}

export type OutlineEdit = Partial<
  Pick<Outline, 'title' | 'lead' | 'description' | 'tags' | 'sections'>
>;

export function listOutlines(status?: OutlineStatus, site?: string): Promise<Outline[]> {
  const params = new URLSearchParams();
  if (status) params.set('status', status);
  if (site) params.set('site', site);
  const query = params.toString();
  return apiGet(`/scrive/outlines${query ? `?${query}` : ''}`);
}

export function getOutline(site: string, id: string): Promise<Outline> {
  return apiGet(`/scrive/sites/${site}/outlines/${id}`);
}

export function updateOutline(site: string, id: string, edit: OutlineEdit): Promise<Outline> {
  return apiPut(`/scrive/sites/${site}/outlines/${id}`, edit);
}

/** Write the page the outline describes; the caller then asks the agent to fill it. */
export function approveOutline(
  site: string,
  id: string,
  edit: OutlineEdit,
): Promise<{ outline: Outline; page: Page }> {
  return apiPost(`/scrive/sites/${site}/outlines/${id}/approve`, edit);
}

export function discardOutline(site: string, id: string): Promise<Outline> {
  return apiPost(`/scrive/sites/${site}/outlines/${id}/discard`, {});
}

// ── review ───────────────────────────────────────────────────────────────────

export interface Finding {
  line: number;
  severity: 'error' | 'warning' | 'info';
  rule: string;
  message: string;
}

/** The page's review findings — the same list the agent's `scrive.critiquePage` sees. */
export function critiquePage(site: string, path: string): Promise<Finding[]> {
  return apiGet(`/scrive/sites/${site}/critique?${q(path)}`);
}

// --- themes and publishing (Phase 5) ------------------------------------------------

export interface ThemeInfo {
  id: string;
  name: string;
  description: string;
  /** One of the four built-in layouts (`site/layouts.tsx`). */
  layout: string;
  source: 'builtin' | 'site';
  /** The theme's CSS custom properties, inlined into the published stylesheet. */
  tokens: string;
  /** THEME.md: the brand and voice guide. */
  guide: string;
  /** A site theme's own layouts (`layouts/<name>.html`), by name: `page`, `home`, `list`,
   * `base`. Each replaces the built-in layout for that kind of file (`site/template.ts`). */
  templates: Record<string, string>;
}

export function listThemes(site?: string | null): Promise<ThemeInfo[]> {
  return apiGet(`/scrive/themes${site ? `?site=${encodeURIComponent(site)}` : ''}`);
}

export type PagesMode = 'static' | 'jupyter-book';

/** `scrive.yml` → `targets.pages`. */
export interface PagesConfig {
  repo: string;
  mode: PagesMode;
  cname: string;
}

export function updateSiteConfig(
  site: string,
  edit: { title?: string; theme?: string; pages?: PagesConfig },
): Promise<SiteConfig> {
  return apiPut(`/scrive/sites/${site}/config`, edit);
}

export interface PublishRecord {
  mode: PagesMode;
  owner: string;
  repo: string;
  branch: string;
  url: string;
  commit: string;
  files: string[];
  pages: number;
  published_at: number;
}

export interface PublishState {
  config: PagesConfig;
  record: PublishRecord | null;
  available: boolean;
  reason: string;
  /** The local build's folder, when there is one. */
  built: string;
}

export function getPublishState(site: string): Promise<PublishState> {
  return apiGet(`/scrive/sites/${site}/publish`);
}

/** What preflight found in the files about to leave. */
export interface PublishFinding {
  /** Stops a publish until acknowledged. */
  blocking: boolean;
  severity: 'secret' | 'warning' | 'info';
  rule: string;
  message: string;
  file: string;
}

/** The client-built static site (see `site/build.tsx`). */
export interface SiteBundle {
  files: Record<string, string>;
  assets: string[];
  /** Scene sources the pages embed (site paths). */
  scenes?: string[];
  /** Web apps the pages embed, by name. */
  apps?: string[];
  cards: { path: string; title: string; description: string; kicker: string }[];
  pages: string[];
}

export interface BuildOutcome {
  path: string;
  files: number;
  findings: PublishFinding[];
  note: string;
}

export interface PublishOutcome {
  published: boolean;
  findings: PublishFinding[];
  record: PublishRecord | null;
  note: string;
}

export function buildSiteLocally(site: string, bundle: SiteBundle): Promise<BuildOutcome> {
  return apiPost(`/scrive/sites/${site}/build`, bundle);
}

export function publishSite(
  site: string,
  request: { bundle?: SiteBundle; acknowledged?: boolean },
): Promise<PublishOutcome> {
  return apiPost(`/scrive/sites/${site}/publish`, request);
}

/** A file of the local build, served sandboxed by the backend. */
export function builtUrl(site: string, path = ''): string {
  return apiUrl(`/api/scrive/sites/${site}/built/${path}`);
}

/** `scrive` channel, event `site.published`. */
export interface SitePublished {
  site: string;
  url: string;
  commit: string;
}

export interface CachedCell {
  id: string;
  source: string;
  outputs: NbOutput[];
  execution_count: number | null;
}

export function cachedCells(site: string, path: string): Promise<{ cells: CachedCell[] }> {
  return apiGet(`/scrive/sites/${site}/cells?${q(path)}`);
}

// --- the outbox (Phase 6) ------------------------------------------------------------

export type OutboxTarget = 'x' | 'linkedin' | 'youtube' | 'devto' | 'hashnode';
/** The targets that take the whole article rather than a post about it. */
export const ARTICLE_TARGETS: readonly OutboxTarget[] = ['devto', 'hashnode'];
export type OutboxStatus = 'draft' | 'approved' | 'scheduled' | 'sending' | 'sent' | 'failed';

export interface XPostDraft {
  text: string;
  media: string[];
}

export interface XPayload {
  posts: XPostDraft[];
  link: string;
  link_in_reply: boolean | null;
}

export interface LinkedInPayload {
  text: string;
  link: string;
  title: string;
  description: string;
  thumbnail: string;
}

export interface YouTubePayload {
  video: string;
  title: string;
  description: string;
  tags: string[];
  privacy: 'public' | 'unlisted' | 'private';
  publish_at: string;
  category: string;
  made_for_kids: boolean;
  synthetic: boolean;
}

/** A whole article for dev.to: the page converted to its Markdown (backend `crosspost.py`). */
export interface DevtoPayload {
  title: string;
  body: string;
  description: string;
  tags: string[];
  /** A site path or an https URL. */
  cover: string;
  canonical_url: string;
  /** false: a dev.to draft, finished there. */
  published: boolean;
  series: string;
}

export interface HashnodePayload {
  title: string;
  subtitle: string;
  body: string;
  tags: string[];
  cover: string;
  canonical_url: string;
}

export type OutboxPayload =
  | XPayload
  | LinkedInPayload
  | YouTubePayload
  | DevtoPayload
  | HashnodePayload;

export interface OutboxItem {
  id: string;
  site: string;
  page: string;
  target: OutboxTarget;
  payload: OutboxPayload;
  status: OutboxStatus;
  created_by: 'person' | 'agent';
  note: string;
  run_at: number | null;
  findings: PublishFinding[];
  steps: Record<string, { status: string; [key: string]: unknown }>;
  attempts: number;
  next_attempt_at: number | null;
  error: string;
  remote_id: string;
  url: string;
  created_at: number;
  updated_at: number;
  approved_at: number | null;
  sent_at: number | null;
}

/** `scrive` channel, event `outbox.changed`. */
export interface OutboxChanged {
  id: string;
  site: string;
  page: string;
  target: OutboxTarget;
  status: OutboxStatus;
  label?: string;
  progress?: number | null;
  deleted?: boolean;
}

/** `page: ''` lists the rows that belong to no page (a clip shared on its own). */
export function listOutbox(filter: { site?: string; page?: string; status?: OutboxStatus } = {}) {
  const query = new URLSearchParams(
    Object.entries(filter).filter(
      (e): e is [string, string] => typeof e[1] === 'string' && (e[1] !== '' || e[0] === 'page'),
    ),
  ).toString();
  return apiGet<OutboxItem[]>(`/scrive/outbox${query ? `?${query}` : ''}`);
}

export function createOutbox(
  site: string,
  page: string,
  target: OutboxTarget,
  payload?: OutboxPayload,
): Promise<OutboxItem> {
  return apiPost('/scrive/outbox', { site, page, target, payload });
}

export function editOutbox(id: string, payload: OutboxPayload): Promise<OutboxItem> {
  return apiPut(`/scrive/outbox/${id}`, { payload });
}

export function deleteOutbox(id: string): Promise<{ ok: boolean }> {
  return apiDelete(`/scrive/outbox/${id}`);
}

export function checkOutbox(id: string): Promise<PublishFinding[]> {
  return apiPost(`/scrive/outbox/${id}/check`, {});
}

export function approveOutbox(
  id: string,
  acknowledged = false,
): Promise<{ item: OutboxItem; approved: boolean }> {
  return apiPost(`/scrive/outbox/${id}/approve`, { acknowledged });
}

export function sendOutbox(id: string): Promise<OutboxItem> {
  return apiPost(`/scrive/outbox/${id}/send`, {});
}

export function scheduleOutbox(id: string, runAt: number): Promise<OutboxItem> {
  return apiPost(`/scrive/outbox/${id}/schedule`, { run_at: runAt });
}

export function unscheduleOutbox(id: string): Promise<OutboxItem> {
  return apiPost(`/scrive/outbox/${id}/unschedule`, {});
}

export function retryOutbox(id: string): Promise<OutboxItem> {
  return apiPost(`/scrive/outbox/${id}/retry`, {});
}

/** Which targets are connected (from `/api/connectors`). */
export async function connectedTargets(): Promise<Record<OutboxTarget, boolean>> {
  const { connectors } = await apiGet<{ connectors: { id: string; connected: boolean }[] }>(
    '/connectors',
  );
  const on = (id: string) => connectors.some((c) => c.id === id && c.connected);
  return {
    x: on('x'),
    linkedin: on('linkedin'),
    youtube: on('youtube'),
    devto: on('devto'),
    hashnode: on('hashnode'),
  };
}

// ── PDF export ───────────────────────────────────────────────────────────────
// Mirrors backend/modules/scrive/exports.py.

export type PdfEngine = 'auto' | 'print' | 'typst';

export interface ExportStatus {
  myst: boolean;
  typst: boolean;
  auto: 'print' | 'typst';
}

export interface ExportOutcome {
  /** Site-relative: `_build/exports/<page>.pdf`. */
  path: string;
  engine: 'print' | 'typst';
  bytes: number;
  seconds: number;
  findings: PublishFinding[];
  note: string;
}

export function exportStatus(): Promise<ExportStatus> {
  return apiGet('/scrive/export/status');
}

export function exportPage(
  site: string,
  page: string,
  engine: PdfEngine,
  paper: 'a4' | 'letter',
  bundle: SiteBundle,
): Promise<ExportOutcome> {
  return apiPost(`/scrive/sites/${site}/export`, { page, engine, paper, bundle });
}

/** The exported file, as a download. */
export function exportFileUrl(site: string, path: string): string {
  return apiUrl(`/scrive/sites/${site}/export-file?path=${encodeURIComponent(path)}`);
}

// ── clips ────────────────────────────────────────────────────────────────────
// Mirrors backend/modules/scrive/clips.py and media.py. Segment times are source
// seconds; caption and overlay times are output seconds.

export type ClipPreset = 'x' | 'youtube' | 'web' | 'gif';
export type CropAspect = 'source' | '16:9' | '9:16' | '1:1' | '4:5';

export interface ClipSegment {
  in: number;
  out: number;
}

export interface ClipCue {
  t0: number;
  t1: number;
  text: string;
}

export interface ClipOverlay extends ClipCue {
  pos: 'top' | 'center' | 'bottom';
}

export interface EditList {
  version: number;
  source: string;
  page: string;
  segments: ClipSegment[];
  crop: { aspect: CropAspect; x: number; y: number };
  speed: number;
  captions: ClipCue[];
  overlays: ClipOverlay[];
  audio: { replace: string; gain: number; mute: boolean };
  output: { preset: ClipPreset; gif_fps: number; gif_width: number; burn_captions: boolean };
}

export interface MediaProbe {
  duration: number;
  width: number;
  height: number;
  fps: number;
  has_audio: boolean;
}

export interface ClipFinding {
  severity: 'info' | 'warning' | 'error';
  rule: string;
  message: string;
}

export interface ClipDoc {
  path: string;
  revision: string;
  edit: EditList;
  probe: MediaProbe | null;
  findings: ClipFinding[];
  output: string;
  rendered: boolean;
}

export interface MediaFile {
  path: string;
  size: number;
  updated_at: number;
}

export interface MediaListing {
  clips: MediaFile[];
  videos: MediaFile[];
  audio: MediaFile[];
}

export type ClipJobStatus = 'running' | 'done' | 'failed' | 'cancelled';

/** Also the `scrive` channel's `clip.job` event. */
export interface ClipJob {
  id: string;
  kind: 'render' | 'captions';
  site: string;
  clip: string;
  status: ClipJobStatus;
  progress: number;
  label: string;
  output: string;
  cues: ClipCue[];
  findings: ClipFinding[];
  error: string;
  started_at: number;
  finished_at: number | null;
}

export interface ExtraState {
  available: boolean;
  state: 'installed' | 'absent' | 'unknown';
  reason: string;
  install: string;
}

export function mediaStatus(): Promise<{ ffmpeg: ExtraState; voice: ExtraState }> {
  return apiGet('/scrive/media/status');
}

export function listMedia(site: string): Promise<MediaListing> {
  return apiGet(`/scrive/sites/${site}/media`);
}

export function createClip(
  site: string,
  source: string,
  opts: { name?: string; page?: string } = {},
): Promise<ClipDoc> {
  return apiPost(`/scrive/sites/${site}/clips`, { source, ...opts });
}

export function readClip(site: string, path: string): Promise<ClipDoc> {
  return apiGet(`/scrive/sites/${site}/clip?${q(path)}`);
}

export type ClipSaveResult = { doc: ClipDoc } | { conflict: ClipDoc };

/** Save against `baseRevision`; a 409 re-reads the clip, as `savePage` does. */
export async function saveClip(
  site: string,
  path: string,
  edit: EditList,
  baseRevision: string,
): Promise<ClipSaveResult> {
  try {
    const doc = await apiPut<ClipDoc>(`/scrive/sites/${site}/clip?${q(path)}`, {
      edit,
      base_revision: baseRevision,
    });
    return { doc };
  } catch (err) {
    if (err instanceof ApiError && err.status === 409) {
      return { conflict: await readClip(site, path) };
    }
    throw err;
  }
}

export function renderClip(site: string, path: string): Promise<ClipJob> {
  return apiPost(`/scrive/sites/${site}/clip/render?${q(path)}`, {});
}

export function captionClip(site: string, path: string): Promise<ClipJob> {
  return apiPost(`/scrive/sites/${site}/clip/captions?${q(path)}`, {});
}

export function listClipJobs(site: string, clip?: string): Promise<ClipJob[]> {
  const query = new URLSearchParams({ site, ...(clip ? { clip } : {}) }).toString();
  return apiGet(`/scrive/clip-jobs?${query}`);
}

export function cancelClipJob(id: string): Promise<ClipJob> {
  return apiPost(`/scrive/clip-jobs/${id}/cancel`, {});
}

/** An X or YouTube outbox **draft** carrying the clip's render. */
export function draftFromClip(
  site: string,
  path: string,
  target: 'x' | 'youtube',
): Promise<OutboxItem> {
  return apiPost(`/scrive/sites/${site}/clip/draft`, { path, target });
}

/** The site file at `path` (site-relative), as the asset route serves it. */
export function siteFileUrl(site: string, path: string, bust?: string | number): string {
  const v = bust === undefined ? '' : `&v=${encodeURIComponent(String(bust))}`;
  return apiUrl(`/api/scrive/sites/${site}/asset?${q(path)}${v}`);
}

export function filmstripUrl(site: string, path: string, count: number): string {
  return apiUrl(`/api/scrive/sites/${site}/media/filmstrip?${q(path)}&count=${count}`);
}
