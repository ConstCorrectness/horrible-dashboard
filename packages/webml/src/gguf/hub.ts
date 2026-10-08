/**
 * The Hub, before anything large downloads: which GGUF files a repo has (the
 * tree API, as the backend's `list_repo_ggufs` does), and what one of them is —
 * read from its header over Range requests, a few MB at most.
 */
import { ggufModelId } from './store';
import { readGgufHeader } from './parse';
import { dominantQuant, preflight } from './session';
import { HttpSource, hfResolveUrl } from './source';

export interface HubGguf {
  /** Path in the repo. */
  path: string;
  size: number;
  /** `mmproj-*`: a vision projector, not a language model. */
  isProjector: boolean;
}

/** The `.gguf` files in a Hub model repo, by path. */
export async function listRepoGgufs(
  repo: string,
  fetcher: typeof fetch = fetch,
): Promise<HubGguf[]> {
  const res = await fetcher(
    `https://huggingface.co/api/models/${repo.split('/').map(encodeURIComponent).join('/')}/tree/main?recursive=1`,
  );
  if (!res.ok) throw new Error(`${repo}: HTTP ${res.status}`);
  const entries: unknown = await res.json();
  const out: HubGguf[] = [];
  for (const e of Array.isArray(entries) ? entries : []) {
    const entry = e as { type?: string; path?: string; size?: number; lfs?: { size?: number } };
    if (entry.type !== 'file' || !entry.path?.toLowerCase().endsWith('.gguf')) continue;
    out.push({
      path: entry.path,
      size: entry.lfs?.size ?? entry.size ?? 0,
      isProjector: (entry.path.split('/').pop() ?? '').startsWith('mmproj-'),
    });
  }
  return out.sort((a, b) => a.path.localeCompare(b.path));
}

export interface GgufInspection {
  id: string;
  ok: boolean;
  /** Why it cannot run here; empty when it can. */
  reasons: string[];
  arch: string;
  name: string | null;
  quant: string;
  contextLength: number | null;
  /** File size, when the server said. */
  size: number | null;
}

/** Read one Hub GGUF's header and say whether this engine can run it. */
export async function inspectHubGguf(
  repo: string,
  file: string,
  fetcher?: typeof fetch,
): Promise<GgufInspection> {
  const source = new HttpSource(hfResolveUrl(repo, file), { fetch: fetcher });
  const header = await readGgufHeader(source);
  const meta = header.metadata;
  const arch = typeof meta['general.architecture'] === 'string' ? meta['general.architecture'] : '';
  const ctx = meta[`${arch}.context_length`];
  const check = preflight(header);
  return {
    id: ggufModelId(repo, file),
    ok: check.ok,
    reasons: check.ok ? [] : check.reasons,
    arch,
    name: typeof meta['general.name'] === 'string' ? meta['general.name'] : null,
    quant: dominantQuant(header),
    contextLength: typeof ctx === 'number' ? ctx : null,
    size: source.size,
  };
}
