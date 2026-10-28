/**
 * Steering a GGUF with an SAE feature (`POST /api/interp/steer`). Shapes mirror
 * `backend/modules/interp/steer.py`.
 */
import { streamNdjson } from '../../../api';

export interface SteerRequest {
  artifactId: string;
  feature: number;
  /** In units of the feature's own peak on the prompt: 1 = as strong as it fires. */
  strength: number;
  /** The trace whose model (and, without `prompt`, whose prompt) to steer. */
  traceId?: string;
  prompt?: string;
  modelPath?: string;
  maxTokens?: number;
}

export interface Mover {
  id: number;
  token: string;
  default: number;
  steered: number;
  deltaLogProb: number;
}

export interface SteerResult {
  defaultTraceId: string;
  steeredTraceId: string;
  feature: number;
  layer: number;
  strength: number;
  /** The feature's peak the strength is measured in. */
  unit: number;
  /** strength × unit: the vector's length added to the residual. */
  scale: number;
  default: { text: string; tokens: string[] };
  steered: { text: string; tokens: string[] };
  /** First generated token where the two runs differ; null when identical. */
  diverges: number | null;
  arrived: {
    state: 'arrived' | 'did_not_arrive' | 'nothing_to_suppress' | 'no_push';
    defaultMean: number;
    steeredMean: number;
    defaultActiveShare: number;
    steeredActiveShare: number;
  };
  nextToken: {
    defaultTop: Mover;
    steeredTop: Mover;
    gained: Mover[];
    lost: Mover[];
    kl: number;
  } | null;
}

export interface SteerProgress {
  phase?: 'default' | 'steered';
  status?: string;
  done?: boolean;
  result?: SteerResult;
  error?: string;
}

export function runSteer(
  req: SteerRequest,
  onProgress: (p: SteerProgress) => void,
  signal?: AbortSignal,
): Promise<void> {
  return streamNdjson('/interp/steer', req, (obj) => onProgress(obj as SteerProgress), signal);
}
