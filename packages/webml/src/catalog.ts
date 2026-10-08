/**
 * Text-generation models known to load in transformers.js on WebGPU. Sizes are the
 * weight files' bytes on the Hub (checked 2026-10-07), so the UI can say what a
 * click will download before it happens. Any other Hub id can be typed in; these
 * are only the ones we have seen work.
 *
 * Multimodal checkpoints (gemma-4-E2B, Qwen3.5) are left out: they load through a
 * processor + image-text model, not `AutoModelForCausalLM`.
 */
import type { Dtype } from './protocol';

/**
 * How a model writes tool calls. `hermes` = `<tool_call>{"name":…,"arguments":…}</tool_call>`
 * (Qwen3, SmolLM3) — the one format the backend parses. `null` = run without tools.
 */
export type ToolFormat = 'hermes' | null;

export interface CatalogModel {
  id: string;
  label: string;
  params: string;
  /** Download size per weight variant, in bytes. The first entry is the preferred one. */
  sizes: Partial<Record<Dtype, number>>;
  toolFormat: ToolFormat;
  /** Supports Qwen3's `enable_thinking` switch. */
  thinking?: boolean;
  license: string;
}

export const CATALOG: readonly CatalogModel[] = [
  {
    id: 'HuggingFaceTB/SmolLM2-360M-Instruct',
    label: 'SmolLM2 360M Instruct',
    params: '360M',
    sizes: { q4f16: 272_737_275, q4: 387_943_246 },
    toolFormat: null,
    license: 'apache-2.0',
  },
  {
    id: 'onnx-community/Qwen3-0.6B-ONNX',
    label: 'Qwen3 0.6B',
    params: '0.6B',
    sizes: { q4f16: 569_789_750, q4: 919_096_585 },
    toolFormat: 'hermes',
    thinking: true,
    license: 'apache-2.0',
  },
  {
    id: 'onnx-community/gemma-3-1b-it-ONNX',
    label: 'Gemma 3 1B IT',
    params: '1B',
    sizes: { q4f16: 763_529_245, q4: 859_454_179 },
    toolFormat: null,
    license: 'gemma',
  },
  {
    id: 'onnx-community/LFM2-1.2B-ONNX',
    label: 'LFM2 1.2B',
    params: '1.2B',
    sizes: { q4f16: 760_462_148, q4: 850_242_940 },
    toolFormat: null,
    license: 'other',
  },
  {
    id: 'onnx-community/Llama-3.2-1B-Instruct-q4f16',
    label: 'Llama 3.2 1B Instruct',
    params: '1.2B',
    sizes: { q4f16: 1_237_750_815 },
    toolFormat: null,
    license: 'llama3.2',
  },
  {
    id: 'onnx-community/Qwen3-1.7B-ONNX',
    label: 'Qwen3 1.7B',
    params: '1.7B',
    sizes: { q4f16: 1_426_069_098, q4: 2_147_212_861 },
    toolFormat: 'hermes',
    thinking: true,
    license: 'apache-2.0',
  },
  {
    id: 'HuggingFaceTB/SmolLM3-3B-ONNX',
    label: 'SmolLM3 3B',
    params: '3B',
    sizes: { q4f16: 2_124_622_302, q4: 2_842_801_143 },
    toolFormat: 'hermes',
    thinking: true,
    license: 'apache-2.0',
  },
];

export function catalogEntry(id: string): CatalogModel | undefined {
  return CATALOG.find((m) => m.id === id);
}

/**
 * The weight variant to load: the caller's choice if given, else the first variant
 * the model ships that this GPU can run (q4f16 needs `shader-f16`).
 */
export function pickDtype(model: CatalogModel | undefined, f16: boolean, wanted?: Dtype): Dtype {
  if (wanted) return wanted;
  const offered: Dtype[] = model ? (Object.keys(model.sizes) as Dtype[]) : ['q4f16', 'q4'];
  return offered.find((d) => f16 || !d.endsWith('f16')) ?? offered[0] ?? 'q4';
}

/** "570 MB", "1.4 GB". */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const i = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1000)));
  const v = bytes / 1000 ** i;
  return `${v >= 100 || i === 0 ? Math.round(v) : v.toFixed(1)} ${units[i]}`;
}

/**
 * GGUF repos to start from in the playground's GGUF picker, for the architectures
 * and quantizations the WGSL engine runs (llama, qwen3; Q4_K_M and friends).
 *
 * No sizes here, unlike the ONNX catalog: the picker lists a repo's files and
 * their sizes live from the Hub's tree API, and reads the chosen file's header
 * before anything downloads. `file` is the file to preselect when the listing
 * has it.
 */
export interface GgufSuggestion {
  repo: string;
  file: string;
  label: string;
  params: string;
  toolFormat: ToolFormat;
  thinking?: boolean;
  license: string;
}

export const GGUF_SUGGESTIONS: readonly GgufSuggestion[] = [
  {
    repo: 'bartowski/SmolLM2-360M-Instruct-GGUF',
    file: 'SmolLM2-360M-Instruct-Q8_0.gguf',
    label: 'SmolLM2 360M Instruct',
    params: '360M',
    toolFormat: null,
    license: 'apache-2.0',
  },
  {
    repo: 'unsloth/Qwen3-0.6B-GGUF',
    file: 'Qwen3-0.6B-Q4_K_M.gguf',
    label: 'Qwen3 0.6B',
    params: '0.6B',
    toolFormat: 'hermes',
    thinking: true,
    license: 'apache-2.0',
  },
  {
    repo: 'bartowski/Llama-3.2-1B-Instruct-GGUF',
    file: 'Llama-3.2-1B-Instruct-Q4_K_M.gguf',
    label: 'Llama 3.2 1B Instruct',
    params: '1.2B',
    toolFormat: null,
    license: 'llama3.2',
  },
  {
    repo: 'unsloth/Qwen3-1.7B-GGUF',
    file: 'Qwen3-1.7B-Q4_K_M.gguf',
    label: 'Qwen3 1.7B',
    params: '1.7B',
    toolFormat: 'hermes',
    thinking: true,
    license: 'apache-2.0',
  },
];

/** The suggestion a `gguf:` id came from, if any (its tool format and thinking switch). */
export function ggufSuggestion(repo: string): GgufSuggestion | undefined {
  return GGUF_SUGGESTIONS.find((s) => s.repo === repo);
}
