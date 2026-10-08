/**
 * A GGUF header → the model the engine would build, or the reasons it won't.
 *
 * The check runs on the header alone (see parse.ts), so the playground can refuse
 * a file before downloading it. Every refusal names what is unsupported; a file is
 * never half-run on a guess — including a file with tensors the engine would not
 * use (attention biases on a `llama` file, say), which would otherwise run quietly
 * without them.
 *
 * Supported, with F32 / F16 / Q8_0 / Q4_0 / Q5_0 / Q4_K / Q5_K / Q6_K weights:
 *
 *  - `llama` (Llama 2/3, SmolLM2): NORM rope, Llama 3's `rope_freqs`.
 *  - `qwen2` (Qwen2 / 2.5, DeepSeek-R1's Qwen distills): NEOX rope, biases on Q, K
 *    and V.
 *  - `qwen3`: NEOX rope, per-head RMS norms on Q and K.
 *  - `gemma3`: Qwen3's Q/K norms, plus an embedding scaled by √embd, RMS norms on the
 *    attention and feed-forward outputs before each residual add, a GELU gate, and
 *    sliding-window attention on five layers in six, with their own rope base.
 *
 * Later stages widen this table, not the engine's tolerance.
 */
import { ggmlTypeName } from './ggml';
import type { GgufHeader, GgufTensor, GgufValue } from './parse';
import { SUPPORTED_TYPES } from './quant';

export type RopeKind = 'norm' | 'neox';

export interface ModelConfig {
  arch: string;
  layers: number;
  /** Residual width. */
  embd: number;
  ffn: number;
  heads: number;
  kvHeads: number;
  headDim: number;
  /** Dimensions per head that are rotated (`rope.dimension_count`). */
  ropeDims: number;
  ropeBase: number;
  rope: RopeKind;
  /** `rope_freqs.weight`: Llama 3's per-dimension frequency divisors. */
  ropeFreqs: boolean;
  /** Per-head RMS norms on Q and K before rope (Qwen3, Gemma 3). */
  qkNorm: boolean;
  /** Biases on the Q, K and V projections (Qwen2). */
  qkvBias: boolean;
  /** RMS norms on the attention and feed-forward outputs, before the residual adds (Gemma 3). */
  postNorms: boolean;
  /** The feed-forward gate's activation: SiLU, or GELU's tanh approximation (Gemma). */
  ffnAct: 'silu' | 'gelu';
  /** The embedding rows are multiplied by this: 1, or √embd (Gemma). */
  embedScale: number;
  /**
   * Keys a windowed layer's query sees, itself included (`attention.sliding_window`);
   * 0 when no layer is windowed. See `layerWindow`.
   */
  slidingWindow: number;
  /** Of every `swaPattern` layers, all but the last are windowed. */
  swaPattern: number;
  /** The rope base of windowed layers (`rope.freq_base_swa`). */
  ropeBaseSwa: number;
  /** Multiplier on Q·K: `attention.scale`, else 1/√headDim. */
  attentionScale: number;
  rmsEps: number;
  contextLength: number;
  vocab: number;
  /** No `output.weight`: the LM head is the embedding matrix. */
  tiedEmbeddings: boolean;
}

export type Support = { ok: true; config: ModelConfig } | { ok: false; reasons: string[] };

interface ArchSpec {
  rope: RopeKind;
  qkNorm: boolean;
  /** `rope_freqs.weight` may be present. */
  ropeFreqs: boolean;
  /** llama.cpp asserts every head dimension is rotated. */
  fullRope: boolean;
  qkvBias?: boolean;
  /** Gemma 3's post-norms, GELU gate, √embd embedding scale and sliding window. */
  gemma?: boolean;
}

const ARCHS: Record<string, ArchSpec> = {
  llama: { rope: 'norm', qkNorm: false, ropeFreqs: true, fullRope: false },
  qwen2: { rope: 'neox', qkNorm: false, ropeFreqs: false, fullRope: false, qkvBias: true },
  qwen3: { rope: 'neox', qkNorm: true, ropeFreqs: false, fullRope: true },
  gemma3: { rope: 'neox', qkNorm: true, ropeFreqs: false, fullRope: false, gemma: true },
};

/** Gemma 3's layers come in sixes, five windowed and one global, unless the file says. */
const GEMMA3_SWA_PATTERN = 6;

/**
 * The sliding window of layer `l`: how many keys, its own included, each query
 * sees; 0 for a layer that sees every earlier position. llama.cpp's
 * `set_swa_pattern`: of every `swaPattern` layers, all but the last are windowed.
 */
export function layerWindow(config: ModelConfig, l: number): number {
  if (!config.slidingWindow) return 0;
  return l % config.swaPattern < config.swaPattern - 1 ? config.slidingWindow : 0;
}

export const SUPPORTED_ARCHS = Object.keys(ARCHS);

function num(meta: Record<string, GgufValue>, key: string): number | undefined {
  const v = meta[key];
  return typeof v === 'number' ? v : undefined;
}

/** "a, b, c and 4 more" */
function some(names: string[]): string {
  return `${names.slice(0, 5).join(', ')}${names.length > 5 ? ` and ${names.length - 5} more` : ''}`;
}

export function checkSupport(header: GgufHeader): Support {
  const meta = header.metadata;
  const reasons: string[] = [];
  const arch = typeof meta['general.architecture'] === 'string' ? meta['general.architecture'] : '';
  const spec = ARCHS[arch];
  if (!spec) {
    return {
      ok: false,
      reasons: [
        `architecture "${arch || 'unknown'}" is not supported (supported: ${SUPPORTED_ARCHS.join(', ')})`,
      ],
    };
  }

  const need = (key: string): number => {
    const v = num(meta, `${arch}.${key}`);
    if (v === undefined) reasons.push(`missing ${arch}.${key}`);
    return v ?? 0;
  };
  const layers = need('block_count');
  const embd = need('embedding_length');
  const ffn = need('feed_forward_length');
  const heads = need('attention.head_count');
  const kvHeads = num(meta, `${arch}.attention.head_count_kv`) ?? heads;
  const headDim = num(meta, `${arch}.attention.key_length`) ?? (heads ? embd / heads : 0);
  const valueDim = num(meta, `${arch}.attention.value_length`) ?? headDim;
  const ropeDims = num(meta, `${arch}.rope.dimension_count`) ?? headDim;

  if (valueDim !== headDim)
    reasons.push(`value_length ${valueDim} differs from key_length ${headDim}`);
  if (heads && kvHeads && heads % kvHeads) {
    reasons.push(`${heads} heads do not divide into ${kvHeads} KV heads`);
  }
  if (!Number.isInteger(headDim) || headDim <= 0)
    reasons.push(`head size ${headDim} is not a whole number`);
  if (ropeDims % 2 || ropeDims > headDim || (spec.fullRope && ropeDims !== headDim))
    reasons.push(`rope.dimension_count ${ropeDims} does not fit head size ${headDim}`);
  const scaling = meta[`${arch}.rope.scaling.type`];
  if (typeof scaling === 'string' && scaling !== 'none')
    reasons.push(`rope scaling "${scaling}" is not supported yet`);
  for (const key of ['final_logit_softcapping', 'attn_logit_softcapping']) {
    if (num(meta, `${arch}.${key}`)) reasons.push(`${key} is not supported yet`);
  }
  let slidingWindow = 0;
  let swaPattern = 1;
  if (spec.gemma) {
    slidingWindow = num(meta, `${arch}.attention.sliding_window`) ?? 0;
    const pattern = meta[`${arch}.attention.sliding_window_pattern`];
    if (pattern === undefined) swaPattern = GEMMA3_SWA_PATTERN;
    else if (typeof pattern === 'number' && pattern >= 1) swaPattern = pattern;
    else reasons.push('a per-layer attention.sliding_window_pattern is not supported yet');
  }

  const byName = new Map(header.tensors.map((t) => [t.name, t]));
  const embedding = byName.get('token_embd.weight');
  const vocab = embedding?.shape[1] ?? 0;
  const q = heads * headDim;
  const kv = kvHeads * headDim;

  // Every tensor the engine reads, with the shape the metadata implies.
  const expect = new Map<string, number[]>([
    ['token_embd.weight', [embd, vocab]],
    ['output_norm.weight', [embd]],
  ]);
  for (let l = 0; l < layers; l++) {
    const p = `blk.${l}.`;
    expect.set(`${p}attn_norm.weight`, [embd]);
    expect.set(`${p}attn_q.weight`, [embd, q]);
    expect.set(`${p}attn_k.weight`, [embd, kv]);
    expect.set(`${p}attn_v.weight`, [embd, kv]);
    expect.set(`${p}attn_output.weight`, [q, embd]);
    expect.set(`${p}ffn_norm.weight`, [embd]);
    expect.set(`${p}ffn_gate.weight`, [embd, ffn]);
    expect.set(`${p}ffn_up.weight`, [embd, ffn]);
    expect.set(`${p}ffn_down.weight`, [ffn, embd]);
    if (spec.qkNorm) {
      expect.set(`${p}attn_q_norm.weight`, [headDim]);
      expect.set(`${p}attn_k_norm.weight`, [headDim]);
    }
    if (spec.qkvBias) {
      expect.set(`${p}attn_q.bias`, [q]);
      expect.set(`${p}attn_k.bias`, [kv]);
      expect.set(`${p}attn_v.bias`, [kv]);
    }
    if (spec.gemma) {
      expect.set(`${p}post_attention_norm.weight`, [embd]);
      expect.set(`${p}post_ffw_norm.weight`, [embd]);
    }
  }
  const optional = new Map<string, number[]>([['output.weight', [embd, vocab]]]);
  if (spec.ropeFreqs) optional.set('rope_freqs.weight', [ropeDims / 2]);

  const missing = [...expect.keys()].filter((n) => !byName.has(n));
  if (missing.length) reasons.push(`missing tensors: ${some(missing)}`);
  const unused = header.tensors
    .map((t) => t.name)
    .filter((n) => !expect.has(n) && !optional.has(n));
  if (unused.length) {
    reasons.push(
      `tensors this engine would ignore (so it would compute the wrong thing): ${some(unused)}`,
    );
  }
  if (!missing.length && embedding && Number.isInteger(headDim)) {
    // Shapes must agree with the metadata: a kernel told the wrong row count reads
    // past its buffer, which WGSL clamps silently into garbage, not an error.
    for (const [name, shape] of [...expect, ...optional]) {
      const got = byName.get(name)?.shape;
      if (got && got.join('×') !== shape.join('×')) {
        reasons.push(`${name} is ${got.join('×')}, but the metadata implies ${shape.join('×')}`);
      }
    }
  }
  for (const t of header.tensors) {
    if (!SUPPORTED_TYPES.has(t.type))
      reasons.push(`${t.name} is ${ggmlTypeName(t.type)}, which is not supported yet`);
  }

  if (reasons.length) return { ok: false, reasons: dedupeTypeReasons(reasons) };
  const ropeBase = num(meta, `${arch}.rope.freq_base`) ?? 10000;
  // llama.cpp computes Gemma 3's scale rather than reading it: 1/√headDim, except
  // 27B (62 layers), whose queries are scaled by its width per head.
  const attentionScale = spec.gemma
    ? 1 / Math.sqrt(layers === 62 ? Math.floor(embd / heads) : headDim)
    : num(meta, `${arch}.attention.scale`) || 1 / Math.sqrt(headDim);
  return {
    ok: true,
    config: {
      arch,
      layers,
      embd,
      ffn,
      heads,
      kvHeads,
      headDim,
      ropeDims,
      ropeBase,
      rope: spec.rope,
      ropeFreqs: byName.has('rope_freqs.weight'),
      qkNorm: spec.qkNorm,
      qkvBias: !!spec.qkvBias,
      postNorms: !!spec.gemma,
      ffnAct: spec.gemma ? 'gelu' : 'silu',
      embedScale: spec.gemma ? Math.sqrt(embd) : 1,
      slidingWindow,
      swaPattern,
      // llama.cpp's default for Gemma 3's windowed layers is 10000, not the global base.
      ropeBaseSwa: num(meta, `${arch}.rope.freq_base_swa`) ?? 10000,
      attentionScale,
      rmsEps: num(meta, `${arch}.attention.layer_norm_rms_epsilon`) ?? 1e-5,
      contextLength: num(meta, `${arch}.context_length`) ?? 2048,
      vocab,
      tiedEmbeddings: !byName.has('output.weight'),
    },
  };
}

/** One line per unsupported type instead of one per tensor (a Q4_K file has hundreds). */
function dedupeTypeReasons(reasons: string[]): string[] {
  const out: string[] = [];
  const types = new Map<string, number>();
  for (const r of reasons) {
    const m = / is (\S+), which is not supported yet$/.exec(r);
    if (!m) out.push(r);
    else types.set(m[1], (types.get(m[1]) ?? 0) + 1);
  }
  for (const [type, n] of types)
    out.push(`${n} tensor${n === 1 ? '' : 's'} use ${type}, which is not supported yet`);
  return out;
}

/** The tensor named `name`; throws if absent (callers ran `checkSupport` first). */
export function tensor(header: GgufHeader, name: string): GgufTensor {
  const t = header.tensors.find((x) => x.name === name);
  if (!t) throw new Error(`missing tensor ${name}`);
  return t;
}
