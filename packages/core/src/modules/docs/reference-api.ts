/**
 * Client for the Python reference (`/api/symdex/reference/*`).
 */
import { apiGet, apiPost } from '../../api';

export type Corpus = 'std' | 'pkg' | 'sdk';

export interface ReferenceCorpus {
  corpus: Corpus;
  source: string;
  name: string;
  version: string;
  count: number;
}

export interface ReferenceSources {
  python: string;
  interpreter: string;
  corpora: ReferenceCorpus[];
  empty: boolean;
}

export interface ReferenceMember {
  name: string;
  kind: string;
  signature: string;
  doc: string;
  members?: ReferenceMember[];
}

export interface ReferenceHit {
  name: string;
  kind: string;
  signature: string;
  doc: string;
  source: string;
  module: string;
}

export interface ReferenceDoc {
  source: string;
  module: string;
  name: string;
  signature: string;
  doc: string;
  file: string;
  line: number;
  /** Set when `module` only re-exports the name; the page was read from here. */
  definedIn?: string;
  importLine: string;
  upstream: { url: string; label: string; exact: 'anchor' | 'search' | 'release' } | null;
}

const q = encodeURIComponent;

export const getReferenceSources = () => apiGet<ReferenceSources>('/symdex/reference/sources');

export const getReferenceModules = (source: string) =>
  apiGet<{ module: string; count: number }[]>(`/symdex/reference/modules?source=${q(source)}`);

export const getReferenceMembers = (source: string, module: string) =>
  apiGet<ReferenceMember[]>(`/symdex/reference/members?source=${q(source)}&module=${q(module)}`);

export const searchReference = (text: string) =>
  apiGet<ReferenceHit[]>(`/symdex/reference/search?q=${q(text)}&limit=60`);

export const getReferenceDoc = (source: string, module: string, name: string) =>
  apiGet<ReferenceDoc>(
    `/symdex/reference/doc?source=${q(source)}&module=${q(module)}&name=${q(name)}`,
  );

/** Build the three corpora the reference browses. Detached; progress on `symdex`. */
export const buildReferenceIndex = () =>
  apiPost<{ started: boolean; reason?: string }>('/symdex/reindex', {
    kinds: ['stdlib', 'packages', 'sdk'],
  });

// The "open the reference on this symbol" seam lives in core `docs/` so the hover
// popup (core infrastructure) can use it without importing a module.
export { sendReferenceQuery, subscribeReferenceQuery, takeReferenceQuery } from '../../docs/reference-query';
