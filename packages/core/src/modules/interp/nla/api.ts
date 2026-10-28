/**
 * Natural Language Autoencoders (`/api/interp/nla*`). Shapes mirror
 * `backend/modules/interp/nla.py` and `nla_runner.py`.
 */
import { apiGet, streamNdjson } from '../../../api';
import type { Message } from '../axis/api';

export type Part = 'base' | 'av' | 'ar';

export interface NlaPair {
  id: string;
  label: string;
  source: string;
  baseRepo: string;
  layer: number;
  nEmbd: number;
  injectionScale: number;
  fveNrm: number;
  /** GGUFs found on this node for each part. */
  files: Record<Part, string[]>;
  ready: boolean;
}

export interface NlaDefaults {
  limit: number;
  samples: number;
  maxTokens: number;
  temperature: number;
  goodCos: number;
  weakCos: number;
  minLift: number;
  earlyPositions: number;
  nullExplanation: string;
}

export type Verdict = 'carried' | 'partial' | 'drifted' | 'not_checked';

export interface Sample {
  raw: string;
  explanation: string;
  /** False when the generation stopped before `</explanation>`. */
  closed: boolean;
  tokens: number;
  cos: number | null;
  mse: number | null;
  /** The neutral sentence's reconstruction against the same vector — the floor. */
  nullCos: number | null;
}

export interface ExplainedPosition {
  position: number;
  token: string;
  norm: number;
  flags: ('early' | 'high_norm')[];
  samples: Sample[];
  best: Sample | null;
  verdict: Verdict;
}

export interface ReadoutSummary {
  positions: number;
  meanCos: number | null;
  meanNullCos: number | null;
  carried: number;
  partial: number;
  drifted: number;
  flagged: number;
}

export interface Readout {
  id: string;
  pairId: string;
  layer: number;
  source: { kind: 'text' | 'messages' | 'turn' | 'trace'; traceId?: string; turnId?: string };
  tokens: string[];
  norms: number[];
  positions: ExplainedPosition[];
  summary: ReadoutSummary;
  timing: { base?: number; av: number; ar: number; total: number };
  avTokensPerSecond: number | null;
  settings: { samples: number; maxTokens: number; temperature: number; seed: number | null };
  createdAt: number;
}

export interface ReadoutRow {
  id: string;
  pairId: string;
  source: Readout['source'];
  positions: number;
  meanCos: number | null;
  createdAt: number;
}

export function getNla(): Promise<{
  pairs: NlaPair[];
  readouts: ReadoutRow[];
  defaults: NlaDefaults;
}> {
  return apiGet('/interp/nla');
}

export function getReadout(id: string): Promise<Readout> {
  return apiGet(`/interp/nla/readouts/${encodeURIComponent(id)}`);
}

export interface NlaProgress<R> {
  status?: string;
  model?: string;
  done?: boolean | number;
  total?: number;
  tokens?: number;
  position?: number;
  explanation?: string;
  result?: R;
  error?: string;
}

export interface ExplainRequest {
  pairId: string;
  text?: string;
  messages?: Message[];
  turnId?: string;
  traceId?: string;
  positions?: number[];
  limit?: number;
  samples?: number;
  maxTokens?: number;
  temperature?: number;
  seed?: number | null;
}

export function explain(
  req: ExplainRequest,
  onEvent: (e: NlaProgress<Readout>) => void,
): Promise<void> {
  return streamNdjson('/interp/nla/explain', req, (o) => onEvent(o as NlaProgress<Readout>));
}

export interface ScoreResult {
  explanation: string;
  cos: number | null;
  mse: number | null;
  nullCos: number | null;
  verdict: Verdict;
  position: number;
  readoutId: string;
}

export function scoreExplanation(
  req: { readoutId: string; position: number; explanation: string },
  onEvent: (e: NlaProgress<ScoreResult>) => void,
): Promise<void> {
  return streamNdjson('/interp/nla/score', req, (o) => onEvent(o as NlaProgress<ScoreResult>));
}
