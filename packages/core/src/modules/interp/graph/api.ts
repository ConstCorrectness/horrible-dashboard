/** Attribution graphs (`/api/interp/graphs`). Shapes mirror `backend/modules/interp/graphs.py`. */
import { apiGet, apiPost, apiPut } from '../../../api';
import type { Artifact } from '../library/api';
import type { AttributionGraph } from './model';

export interface GraphArtifact extends Omit<Artifact, 'facts' | 'compat'> {
  facts: {
    model?: string;
    prompt?: string;
    tokens?: number;
    nodes?: number;
    links?: number;
    target?: string | null;
    generator?: string | null;
    /** Neuronpedia source set: node labels live under `{layer}-{sourceSet}`. */
    sourceSet?: string | null;
  };
}

export interface GraphSummary {
  prompt?: string;
  target?: string;
  targetProb?: number;
  pinnedOnly?: boolean;
  paths?: { path: string[]; weakestLink: number }[];
  supernodes?: { label: string; members: string[] }[];
  error?: string;
}

export function listGraphs(): Promise<{ graphs: GraphArtifact[] }> {
  return apiGet('/interp/graphs');
}

export function importGraph(body: {
  ref?: string;
  json_text?: string;
  name?: string;
}): Promise<{ graph?: GraphArtifact; error?: string }> {
  return apiPost('/interp/graphs/import', body);
}

const path = (id: string) => id.split('/').map(encodeURIComponent).join('/');

export function getGraph(
  id: string,
): Promise<{ artifact?: GraphArtifact; graph?: AttributionGraph; error?: string }> {
  return apiGet(`/interp/graphs/${path(id)}`);
}

export function saveQParams(
  id: string,
  q: { pinnedIds: string[]; supernodes: string[][] },
): Promise<{ qParams: { pinnedIds: string[]; supernodes: string[][] } }> {
  return apiPut(`/interp/graphs/${path(id)}/qparams`, q);
}

export function getGraphSummary(id: string, pinnedOnly = false): Promise<GraphSummary> {
  return apiGet(`/interp/graphs/${path(id)}/summary?pinnedOnly=${pinnedOnly}`);
}
