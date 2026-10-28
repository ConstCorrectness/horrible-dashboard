/**
 * The Assistant Axis (`/api/interp/axis*`). Shapes mirror
 * `backend/modules/interp/axis.py` and `axis_runner.py`.
 */
import { apiGet, streamNdjson } from '../../../api';

/** Percentiles of held-out single-token projections at one layer. */
export interface Band {
  mean: number;
  p5: number;
  p25: number;
  p50: number;
  p75: number;
  p95: number;
}

export interface AxisLayer {
  layer: number;
  /** ‖mean(assistant) − mean(roles)‖ before normalising. */
  diffNorm: number;
  /** |cos| between the axis and PC1 of the role means. */
  pc1Cos: number | null;
  /** Held-out answer means: P(assistant above role). */
  auc: number | null;
  /** Held-out single tokens — what the monitor reads. */
  tokenAuc?: number | null;
  dPrime?: number;
  assistant?: Band;
  role?: Band;
}

export interface AxisCheck {
  state: 'separates' | 'weak' | 'not_checked';
  tokenAuc?: number | null;
  auc?: number | null;
  reason: string;
}

export interface AxisSummary {
  id: string;
  modelName: string;
  modelPath: string;
  modelSha: string | null;
  nLayer: number;
  nEmbd: number;
  defaultLayer: number;
  check: AxisCheck;
  cap: { layers: number[]; tau: Record<string, number>; percentile: number };
  layers: AxisLayer[];
  roles: string[];
  /** Each role's mean answer projected on the axis, per layer. */
  roleProjections: Record<string, number[]>;
  assistantProjection: number[];
  /** The first answer of each condition, to see whether a role was played. */
  examples: Record<string, string>;
  questions: string[];
  heldOut: number;
  samples: { assistant: number; role: number; heldOut: number; heldOutTokens: number };
  maxTokens: number;
  elapsed: number | null;
  createdAt: number | null;
}

export interface AxisDefaults {
  roles: { name: string; description: string }[];
  assistantPrompts: string[];
  questions: string[];
  heldOut: number;
  maxTokens: number;
}

export function getAxes(): Promise<{ axes: AxisSummary[]; defaults: AxisDefaults }> {
  return apiGet('/interp/axis');
}

export interface DriftSummary {
  mean?: number;
  min?: number;
  minAt?: number;
  slopePer100?: number;
  floor?: number;
  belowFloor?: number;
  roleMedian?: number;
  inRoleRange?: number;
}

export interface Series {
  values: (number | null)[];
  tokens: string[];
  layer: number;
  bands: { assistant?: Band; role?: Band };
  summary: DriftSummary;
}

export interface TraceSeries extends Series {
  traceId: string;
  generatedFrom: number;
  /** False for raw-text traces: not the assistant turn the bands were measured on. */
  templated: boolean;
}

export function getTraceSeries(
  axisId: string,
  traceId: string,
  layer?: number,
): Promise<TraceSeries> {
  const q = layer === undefined ? '' : `?layer=${layer}`;
  return apiGet(
    `/interp/axis/${encodeURIComponent(axisId)}/trace/${encodeURIComponent(traceId)}${q}`,
  );
}

export interface Message {
  role: 'system' | 'user' | 'assistant';
  content: string;
}

export interface MonitorResult extends Series {
  messages: { role: string; start: number; end: number }[];
  clipped: number;
  source: { kind: 'messages' | 'turn'; turnId?: string; round?: number; rounds?: number };
  /** The same numbers over the assistant's own messages only. */
  assistantSummary: DriftSummary | null;
  /** Per message, and the replies' change from the first — the reading for text
   *  the model did not write, whose level sits below bands made from its own. */
  drift: MessageDrift;
}

export interface MessageDrift {
  perMessage: { role: string; start: number; median: number }[];
  /** assistant p50 − role p50: the unit `changeInGaps` is measured in. */
  gap?: number;
  firstReply?: number;
  lastReply?: number;
  change?: number;
  /** −1 = the replies moved as far as a role sits from the assistant. */
  changeInGaps?: number;
  replies?: number;
}

export interface CapToken {
  token: string;
  generated: boolean;
  projection: Record<string, number>;
  /** Present on capped tokens: the first pass's projections and what was added. */
  uncapped?: Record<string, number>;
  pushed?: Record<string, number>;
}

export interface CapRun {
  text: string;
  tokens: CapToken[];
  promptTokens: number;
  cappedTokens: number;
}

export interface CapResult {
  default: CapRun;
  capped: CapRun;
  layers: number[];
  tau: Record<string, number>;
  check: AxisCheck;
}

export interface AxisProgress<R> {
  status?: string;
  done?: boolean;
  total?: number;
  condition?: string;
  elapsed?: number;
  run?: string;
  generated?: number;
  tokens?: number;
  result?: R;
  error?: string;
  log?: string;
}

export interface BuildRequest {
  modelPath: string;
  rolesText?: string;
  roleCount?: number;
  questions?: string[];
  heldOut?: number;
  maxTokens?: number;
}

export function buildAxis(
  req: BuildRequest,
  onEvent: (e: AxisProgress<AxisSummary>) => void,
  signal?: AbortSignal,
): Promise<void> {
  return streamNdjson(
    '/interp/axis/build',
    req,
    (o) => onEvent(o as AxisProgress<AxisSummary>),
    signal,
  );
}

export function monitorAxis(
  req: { axisId: string; layer?: number; messages?: Message[]; turnId?: string; round?: number },
  onEvent: (e: AxisProgress<MonitorResult>) => void,
): Promise<void> {
  return streamNdjson('/interp/axis/monitor', req, (o) =>
    onEvent(o as AxisProgress<MonitorResult>),
  );
}

export function capAxis(
  req: { axisId: string; system?: string; prompt?: string; maxTokens?: number },
  onEvent: (e: AxisProgress<CapResult>) => void,
): Promise<void> {
  return streamNdjson('/interp/axis/cap', req, (o) => onEvent(o as AxisProgress<CapResult>));
}
