/**
 * A GGUF header → the model the engine would build, or the reasons it won't.
 *
 * The check runs on the header alone (see parse.ts), so the playground can refuse
 * a file before downloading it. Every refusal names what is unsupported; a file is
 * never half-run on a guess.
 *
 * 6.1 supports the `llama` architecture with F32 / F16 / Q8_0 / Q4_0 weights and no
 * rope scaling. Later stages widen this table, not the engine's tolerance.
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
  rmsEps: number;
  contextLength: number;
  vocab: number;
  /** No `output.weight`: the LM head is the embedding matrix. */
  tiedEmbeddings: boolean;
}

export type Support = { ok: true; config: ModelConfig } | { ok: false; reasons: string[] };

const SUPPORTED_ARCHS: Record<string, RopeKind> = { llama: 'norm' };

function num(meta: Record<string, GgufValue>, key: string): number | undefined {
  const v = meta[key];
  return typeof v === 'number' ? v : undefined;
}

/** Tensor names one llama-family block must have. */
const BLOCK_TENSORS = [
  'attn_norm',
  'attn_q',
  'attn_k',
  'attn_v',
  'attn_output',
  'ffn_norm',
  'ffn_gate',
  'ffn_up',
  'ffn_down',
];

export function checkSupport(header: GgufHeader): Support {
  const meta = header.metadata;
  const reasons: string[] = [];
  const arch = typeof meta['general.architecture'] === 'string' ? meta['general.architecture'] : '';
  const rope = SUPPORTED_ARCHS[arch];
  if (!rope) {
    return {
      ok: false,
      reasons: [
        `architecture "${arch || 'unknown'}" is not supported (supported: ${Object.keys(SUPPORTED_ARCHS).join(', ')})`,
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
  if (ropeDims % 2 || ropeDims > headDim)
    reasons.push(`rope.dimension_count ${ropeDims} does not fit head size ${headDim}`);
  const scaling = meta[`${arch}.rope.scaling.type`];
  if (typeof scaling === 'string' && scaling !== 'none')
    reasons.push(`rope scaling "${scaling}" is not supported yet`);

  const byName = new Map(header.tensors.map((t) => [t.name, t]));
  if (byName.has('rope_freqs.weight'))
    reasons.push('per-dimension rope frequencies (rope_freqs) are not supported yet');
  const embedding = byName.get('token_embd.weight');
  const required = ['token_embd.weight', 'output_norm.weight'];
  for (let l = 0; l < layers; l++)
    for (const t of BLOCK_TENSORS) required.push(`blk.${l}.${t}.weight`);
  const missing = required.filter((n) => !byName.has(n));
  if (missing.length) {
    reasons.push(
      `missing tensors: ${missing.slice(0, 5).join(', ')}${missing.length > 5 ? ` and ${missing.length - 5} more` : ''}`,
    );
  } else if (embedding && Number.isInteger(headDim)) {
    // Shapes must agree with the metadata: a kernel told the wrong row count reads
    // past its buffer, which WGSL clamps silently into garbage, not an error.
    const vocab = embedding.shape[1] ?? 0;
    const q = heads * headDim;
    const kv = kvHeads * headDim;
    const expect: [string, number[]][] = [
      ['token_embd.weight', [embd, vocab]],
      ['output_norm.weight', [embd]],
    ];
    if (byName.has('output.weight')) expect.push(['output.weight', [embd, vocab]]);
    for (let l = 0; l < layers; l++) {
      const p = `blk.${l}.`;
      expect.push(
        [`${p}attn_norm.weight`, [embd]],
        [`${p}attn_q.weight`, [embd, q]],
        [`${p}attn_k.weight`, [embd, kv]],
        [`${p}attn_v.weight`, [embd, kv]],
        [`${p}attn_output.weight`, [q, embd]],
        [`${p}ffn_norm.weight`, [embd]],
        [`${p}ffn_gate.weight`, [embd, ffn]],
        [`${p}ffn_up.weight`, [embd, ffn]],
        [`${p}ffn_down.weight`, [ffn, embd]],
      );
    }
    for (const [name, shape] of expect) {
      const got = byName.get(name)!.shape;
      if (got.join('×') !== shape.join('×')) {
        reasons.push(`${name} is ${got.join('×')}, but the metadata implies ${shape.join('×')}`);
      }
    }
  }
  for (const t of header.tensors) {
    if (!SUPPORTED_TYPES.has(t.type))
      reasons.push(`${t.name} is ${ggmlTypeName(t.type)}, which is not supported yet`);
  }

  if (reasons.length) return { ok: false, reasons: dedupeTypeReasons(reasons) };
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
      ropeBase: num(meta, `${arch}.rope.freq_base`) ?? 10000,
      rope,
      rmsEps: num(meta, `${arch}.attention.layer_norm_rms_epsilon`) ?? 1e-5,
      contextLength: num(meta, `${arch}.context_length`) ?? 2048,
      vocab: embedding!.shape[1] ?? 0,
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
