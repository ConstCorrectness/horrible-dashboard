/**
 * `{space}`: a Hugging Face Space, embedded live.
 *
 *   ```{space} webml-community/smollm-webgpu
 *   :height: 640
 *   :host: webml-community-smollm-webgpu.static.hf.space
 *   ```
 *
 * The argument is the Space id, or its huggingface.co page URL. The embed host is
 * **not** derivable from the id alone: a static Space is served from
 * `<subdomain>.static.hf.space` and its plain `<subdomain>.hf.space` answers 404,
 * while a Gradio or Docker Space is the other way round. So the editor asks the Hub
 * API once (`/api/spaces/<id>` → `host`) and writes the answer into `:host:`; a
 * published page, which makes no API calls, embeds whatever is written there.
 */

export interface SpaceRef {
  /** `owner/name`. */
  id: string;
}

export interface SpaceInfo {
  id: string;
  /** The embed origin, e.g. `https://owner-name.static.hf.space`. */
  host: string;
  sdk: string;
  title: string;
  emoji: string;
  likes: number;
  stage: string;
  license: string;
}

const ID = /^([A-Za-z0-9][\w.-]*)\/([\w.-]+)$/;

/** `owner/name`, or a huggingface.co/spaces/… URL, → the Space id; null otherwise. */
export function parseSpaceRef(arg: unknown): SpaceRef | null {
  const raw = typeof arg === 'string' ? arg.trim() : '';
  if (!raw) return null;
  const direct = ID.exec(raw);
  if (direct) return { id: `${direct[1]}/${direct[2]}` };
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return null;
  }
  if (!/^(?:www\.)?huggingface\.co$/i.test(url.hostname)) return null;
  const m = /^\/spaces\/([^/]+)\/([^/?#]+)/.exec(url.pathname);
  return m && ID.test(`${m[1]}/${m[2]}`) ? { id: `${m[1]}/${m[2]}` } : null;
}

/**
 * A `:host:` value made safe to put in an iframe: an `*.hf.space` host (with or
 * without scheme), always served over https. Anything else is refused — a page is
 * often agent-written, and `:host:` must not become a way to embed arbitrary sites.
 */
export function spaceEmbedUrl(host: unknown): string | null {
  const raw =
    typeof host === 'string'
      ? host
          .trim()
          .replace(/^https:\/\//i, '')
          .replace(/\/+$/, '')
      : '';
  return /^[a-z0-9-]+(?:\.static)?\.hf\.space$/i.test(raw) ? `https://${raw.toLowerCase()}` : null;
}

export function spacePageUrl(id: string): string {
  return `https://huggingface.co/spaces/${id}`;
}

const cache = new Map<string, Promise<SpaceInfo>>();

/** The Space's card and embed host, from the Hub API (cached per session). */
export function fetchSpaceInfo(id: string, fetcher: typeof fetch = fetch): Promise<SpaceInfo> {
  let pending = cache.get(id);
  if (!pending) {
    pending = fetcher(`https://huggingface.co/api/spaces/${id}`).then(async (res) => {
      if (!res.ok)
        throw new Error(res.status === 404 ? `no Space ${id} on the Hub` : `Hub API ${res.status}`);
      const d = (await res.json()) as Record<string, unknown>;
      const card = (d.cardData ?? {}) as Record<string, unknown>;
      const runtime = (d.runtime ?? {}) as Record<string, unknown>;
      const host = spaceEmbedUrl(d.host) ?? spaceEmbedUrl(`${String(d.subdomain ?? '')}.hf.space`);
      if (!host) throw new Error(`the Hub reported no embed host for ${id}`);
      return {
        id: String(d.id ?? id),
        host,
        sdk: String(d.sdk ?? ''),
        title: String(card.title ?? id.split('/')[1]),
        emoji: String(card.emoji ?? ''),
        likes: Number(d.likes ?? 0),
        stage: String(runtime.stage ?? ''),
        license: String(card.license ?? ''),
      };
    });
    pending.catch(() => cache.delete(id));
    cache.set(id, pending);
  }
  return pending;
}
