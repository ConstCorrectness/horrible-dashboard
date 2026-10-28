/**
 * What the interpretability suite is looking at, as facts an agent can read.
 *
 * Before this, every interpretability pane gave the agent nothing: asked "what am
 * I looking at?", it got `{section, hasPayload: false}` and answered from whatever
 * else was on screen. This module composes the stores the suite's tools already
 * share — the model and its per-layer attention layout, the model locus every tool
 * follows, the latest captured agent turn, the window's lens run — into one digest.
 *
 * It owns no state. The stores are module-level (they outlive any pane), so the
 * digest reads the same whether the section being asked about is mounted, hidden
 * behind another, or not open at all.
 *
 * Two sizes. `compact` rides along with every chat turn while the suite is on
 * screen (the workspace-context budget is ~1.5k characters per pane), so it is a
 * handful of strings. `full` is what `interp.state` returns when asked.
 */
import { getModelLocus, type ModelLocus } from '../../model-locus';
import {
  interpretabilityStore,
  type AttentionLayout,
  type LayerAttention,
  type ModelArchitecture,
  type TurnSnapshot,
} from '../interpretability/store';
import { lensRuns, type LensRun } from '../webml/lens-run';
import { fmtBytes, fmtContext, savings } from './attention/model';
import { headsDigest } from './heads/state';
import { libraryDigest } from './library/state';

export interface DigestInputs {
  arch: ModelArchitecture | null;
  attention: AttentionLayout | null;
  locus: ModelLocus;
  turns: readonly TurnSnapshot[];
  lensRun: LensRun | null;
  /** The Heads section's one-line summary (`heads/state.ts`), if it has a trace. */
  heads?: string | null;
  /** The Library's: artifacts, what fits, what the worker holds (`library/state.ts`). */
  library?: string | null;
}

export function readDigestInputs(): DigestInputs {
  return {
    arch: interpretabilityStore.getArchitecture(),
    attention: interpretabilityStore.getAttention(),
    locus: getModelLocus(),
    turns: interpretabilityStore.getSnapshot(),
    lensRun: lensRuns.get(),
    heads: headsDigest(),
    library: libraryDigest(),
  };
}

/** One layer as a sentence: "L17 sliding, window 512, 8q/1kv × 256, KV from L13". */
export function describeLayer(layer: LayerAttention): string {
  const parts = [`L${layer.layer} ${layer.kind}`];
  if (layer.window) parts.push(`window ${layer.window}`);
  if (layer.heads)
    parts.push(`${layer.heads}q/${layer.kvHeads ?? '?'}kv × ${layer.headDimK ?? '?'}`);
  if (layer.kvSourceLayer !== null) parts.push(`KV from L${layer.kvSourceLayer}`);
  if (layer.kEqV) parts.push('K=V');
  if (layer.outputGate) parts.push('gated output');
  if (layer.nope) parts.push('no RoPE');
  if (layer.mtp) parts.push('MTP head');
  parts.push(`(${layer.basis})`);
  return parts.join(', ');
}

function fileName(path: string): string {
  return path.split(/[\\/]/).pop() ?? path;
}

/** The KV cache at 32k (or the largest context under it), as a sentence. */
function kvLine(layout: AttentionLayout): string | null {
  const points = layout.kvCache;
  if (!points.length) return null;
  const point = [...points].reverse().find((p) => p.context <= 32768) ?? points[0];
  const ratio = savings(point);
  return (
    `${fmtBytes(point.bytes)} fp16 KV at ${fmtContext(point.context)}` +
    (ratio && ratio > 1.05 ? ` (${ratio.toFixed(1)}× smaller than all-global)` : '')
  );
}

export interface SuiteDigest {
  model?: string;
  shape?: string;
  attention?: string;
  kvCache?: string;
  locus?: string;
  atLocus?: string;
  lastTurn?: string;
  windowLens?: string;
  heads?: string;
  library?: string;
  caveats?: string[];
  /** Full only. */
  layers?: string[];
  notes?: string[];
}

/**
 * The digest. Every field is optional and omitted rather than guessed: a model the
 * node could not resolve says so in `model`, and a layout that failed is a caveat,
 * not an empty pattern.
 */
export function buildDigest(
  inputs: DigestInputs,
  size: 'compact' | 'full' = 'compact',
): SuiteDigest {
  const { arch, attention, locus, turns, lensRun } = inputs;
  const out: SuiteDigest = {};
  const caveats: string[] = [];

  if (arch && !arch.error) {
    const source =
      arch.source === 'gguf' || arch.source === 'ollama'
        ? `${arch.source === 'gguf' ? 'GGUF header' : 'Ollama /api/show'}`
        : `repo ${arch.sourceDetail} (describes the repo, not necessarily these weights)`;
    out.model = `${arch.model}${arch.family ? ` (${arch.family})` : ''} — from ${source}`;
    const shape = [
      arch.layers != null ? `${arch.layers} blocks` : '',
      arch.hiddenSize != null ? `d_model ${arch.hiddenSize}` : '',
      arch.attention?.heads != null
        ? `${arch.attention.heads} heads / ${arch.attention.kvHeads ?? '?'} KV (headline)`
        : '',
      arch.vocabSize != null ? `vocab ${arch.vocabSize}` : '',
      arch.moe?.experts ? `MoE top-${arch.moe.expertsPerToken} of ${arch.moe.experts}` : '',
    ].filter(Boolean);
    if (shape.length) out.shape = shape.join(' · ');
  } else {
    out.model = arch?.error ? `unresolved: ${arch.error}` : 'not loaded yet';
  }

  if (attention && !attention.error && attention.layers.length) {
    out.attention = `${attention.pattern} (${fileName(attention.path)})`;
    const kv = kvLine(attention);
    if (kv) out.kvCache = kv;
    if (attention.conflicts.length) caveats.push(...attention.conflicts);
    if (attention.layers.some((l) => l.basis === 'rule')) {
      caveats.push('Some layer kinds come from the family rule, not per-layer metadata.');
    }
  } else if (attention?.error) {
    caveats.push(`No attention layout: ${attention.error}`);
  }

  const where: string[] = [];
  if (typeof locus.layer === 'number')
    where.push(locus.layer === -1 ? 'embedding' : `layer ${locus.layer}`);
  if (typeof locus.position === 'number') where.push(`position ${locus.position}`);
  if (locus.stage) where.push(locus.stage);
  if (locus.traceId) where.push(`trace ${locus.traceId}`);
  if (where.length) out.locus = where.join(', ');
  if (typeof locus.layer === 'number' && attention?.layers[locus.layer]) {
    out.atLocus = describeLayer(attention.layers[locus.layer]);
  }

  const turn = turns[0];
  if (turn) {
    const round = turn.rounds[turn.rounds.length - 1];
    out.lastTurn =
      `${turn.agentName || turn.agentId} on ${turn.model || 'unknown model'}: ` +
      `${turn.rounds.length} round${turn.rounds.length === 1 ? '' : 's'}` +
      (round
        ? `, ${round.totalTokens} tokens${round.toolsTruncated ? ', tools DROPPED by budget' : ''}`
        : '') +
      (turn.exact ? '' : ' (token counts estimated)');
  }

  if (inputs.heads) out.heads = inputs.heads;
  if (inputs.library) out.library = inputs.library;

  if (lensRun && lensRun.tokens.length) {
    out.windowLens =
      `${lensRun.model}: ${lensRun.tokens.length} tokens × ${lensRun.layers} layers` +
      (lensRun.live ? ' (still generating)' : '');
  }

  if (caveats.length) out.caveats = size === 'full' ? caveats : caveats.slice(0, 2);

  if (size === 'full' && attention && !attention.error) {
    out.layers = attention.layers.map(describeLayer);
    out.notes = attention.notes;
  }
  return out;
}

/** The digest of what the suite is showing now. */
export function suiteDigest(size: 'compact' | 'full' = 'compact'): SuiteDigest {
  return buildDigest(readDigestInputs(), size);
}

/**
 * Make sure the digest's sources are loaded — for a read that arrives while the
 * suite has never been opened (an agent tool runs whether or not the pane does).
 */
export async function primeDigest(): Promise<void> {
  if (!interpretabilityStore.getArchitecture()) await interpretabilityStore.refresh();
  // Not the tensor inventory: the digest never reads it, and it is the large one.
  await interpretabilityStore.ensureAttention();
}
