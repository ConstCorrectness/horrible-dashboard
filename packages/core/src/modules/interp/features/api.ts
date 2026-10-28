/**
 * SAE features on llama.cpp traces (`/api/interp/features`). Shapes mirror
 * `backend/modules/interp/routes.py` and `sae.py`.
 */
import { apiGet, apiPost } from '../../../api';
import type { Artifact, Compat } from '../library/api';

export interface NeuronpediaSource {
  model: string;
  source: string;
}

export interface FeatureTrace {
  traceId: string;
  modelName: string | null;
  prompt: string;
  createdAt: number | null;
  /** Installed SAEs that may apply: compatible or unknown, never incompatible. */
  fits: { artifactId: string; state: Compat['state'] }[];
}

export interface SaeRow extends Omit<Artifact, 'compat'> {
  compat: Omit<Compat, 'target' | 'source'>;
  neuronpedia: NeuronpediaSource | null;
}

export interface ReconstructionCheck {
  state: 'verified' | 'drifted' | 'not_checked';
  fvu?: number;
  cosine?: number;
  threshold?: number;
  tokens: number;
  skipped?: number;
  l0?: number | null;
  expectedL0?: number | null;
  reason?: string;
}

export interface TopFeature {
  index: number;
  peak: number;
  position: number;
  /** Tokens it is active on. */
  tokens: number;
  /** Peaks on the first token after BOS — real, but outside Neuronpedia's examples. */
  firstToken: boolean;
}

export interface FeatureEncoding {
  traceId: string;
  model: string | null;
  artifactId: string;
  arch: string;
  site: string;
  layer: number;
  width: number;
  fidelity: string | null;
  tokens: { position: number; text: string; generated: boolean }[];
  firstPosition: number;
  perToken: { position: number; active: number; top: { index: number; act: number }[] }[];
  top: TopFeature[];
  check: ReconstructionCheck;
  compat: Omit<Compat, 'target' | 'source'>;
  neuronpedia: NeuronpediaSource | null;
  error?: string;
}

export interface FeatureValues {
  index: number;
  firstPosition: number;
  values: number[];
  threshold: number | null;
  error?: string;
}

export interface LabelCheck {
  state: 'verified' | 'drifted' | 'failed' | 'not_checked';
  expected?: number;
  ours?: number;
  ratio?: number | null;
  token?: string | null;
  tolerance?: [number, number];
  reason?: string;
}

export function getFeatureTraces(): Promise<{ traces: FeatureTrace[] }> {
  return apiGet('/interp/features/traces');
}

export function getSaes(traceId: string): Promise<{ saes: SaeRow[] }> {
  return apiGet(`/interp/features/saes?traceId=${encodeURIComponent(traceId)}`);
}

export function getFeatures(traceId: string, artifactId: string): Promise<FeatureEncoding> {
  return apiGet(
    `/interp/features/${encodeURIComponent(traceId)}?artifactId=${encodeURIComponent(artifactId)}`,
  );
}

export function getFeatureValues(
  traceId: string,
  artifactId: string,
  index: number,
): Promise<FeatureValues> {
  return apiGet(
    `/interp/features/${encodeURIComponent(traceId)}/feature/${index}` +
      `?artifactId=${encodeURIComponent(artifactId)}`,
  );
}

/** Trace Neuronpedia's top example for `index` through the trace's model and
 *  compare activations. Sends nothing of the user's. */
export function labelCheck(
  traceId: string,
  artifactId: string,
  index: number,
): Promise<LabelCheck> {
  return apiPost('/interp/features/label-check', { traceId, artifactId, index });
}
