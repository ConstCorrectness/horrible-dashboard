/**
 * The model worker's message protocol. One worker holds at most one loaded model;
 * every request is answered with events, and a generation's events all carry its `id`.
 *
 *   host → worker   load · generate · interrupt · unload
 *   worker → host   progress* · ready | error
 *                   delta* · step* · done | error        (per generation)
 *                   unloaded
 *
 * `step` events are only sent when the generation asked for them (`topK > 0`): each one
 * is the distribution the token was sampled from — after the model's own logits
 * processors (repetition penalty, temperature, top-k) — so it shows what the sampler
 * actually saw, not the raw logits.
 */

export type Role = 'system' | 'user' | 'assistant' | 'tool';

export interface ChatMessage {
  role: Role;
  content: string;
  /** An assistant turn's tool calls (arguments as objects), for templates that render them. */
  tool_calls?: { type: 'function'; function: { name: string; arguments: unknown } }[];
  /** A `tool` turn's tool name. */
  name?: string;
}

/** `webgpu` is the point; `wasm` exists so a machine without a GPU can still run a tiny model. */
export type Device = 'webgpu' | 'wasm';

/** The ONNX weight variants the catalog knows about. `q4f16` needs the `shader-f16` feature. */
export type Dtype = 'q4f16' | 'q4' | 'fp16' | 'fp32';

export interface LoadRequest {
  type: 'load';
  /**
   * A Hub model id (ONNX, transformers.js), or `gguf:<owner>/<repo>/<file.gguf>`
   * for a GGUF file run by our own WGSL engine (gguf.worker.ts), or
   * `gguf-node:<path>` for a GGUF this node already has.
   */
  model: string;
  /**
   * `gguf-node:` only: where to Range-read the file (the node's
   * `/api/llamacpp/models/file`). The worker cannot know the backend's origin, so
   * the window resolves it (`WebmlEngine`'s `sourceUrl`).
   */
  url?: string;
  /** ONNX weight variant. A GGUF's quantization is in the file; this is echoed back. */
  dtype: Dtype;
  device: Device;
  /** GGUF only: KV-cache positions to allocate (default 4096, capped by the model). */
  contextLength?: number;
}

/** Which worker runs a model. */
export type EngineKind = 'onnx' | 'gguf';

export interface GenerateRequest {
  type: 'generate';
  id: string;
  messages: ChatMessage[];
  /** JSON-schema tool definitions handed to the chat template (models that support it). */
  tools?: unknown[];
  maxNewTokens?: number;
  /** 0 = greedy. */
  temperature?: number;
  /** Record this many alternatives per token as `step` events; 0 / absent = off. */
  topK?: number;
  /**
   * GGUF only: read out the logit lens at every generated token — each layer's
   * residual norm and what the LM head would say from it — into the `step` events.
   * Costs one more pass over the LM head's weights per token. Ignored by ONNX models.
   */
  lens?: boolean;
  /** Extra variables for the chat template (`enable_thinking` for Qwen3, …). */
  templateKwargs?: Record<string, unknown>;
}

export type WorkerRequest =
  | LoadRequest
  | GenerateRequest
  | { type: 'interrupt' }
  | { type: 'unload' };

export interface Alternative {
  token: string;
  p: number;
}

/** The logit lens at one layer, for the position a token was chosen at. */
export interface LayerLens {
  /** L2 norm of the residual stream after the layer. */
  norm: number;
  /** Entropy, in bits, of the LM head's distribution over that residual. */
  entropy: number;
  /** That distribution's most likely tokens, best first. */
  top: Alternative[];
}

export type StopReason = 'eos' | 'length' | 'interrupt';

export interface Usage {
  promptTokens: number;
  completionTokens: number;
  /**
   * GGUF: prompt tokens whose KV entries were kept from the previous reply (the
   * shared prefix), so not prefilled again. Included in `promptTokens`.
   */
  cachedTokens?: number;
}

export type WorkerEvent =
  | { type: 'progress'; file: string; loaded: number; total: number; status: string }
  | {
      type: 'ready';
      model: string;
      dtype: Dtype;
      device: Device;
      loadMs: number;
      engine?: EngineKind;
      /** GGUF: the weight type holding most of the file ("Q8_0"). */
      quant?: string;
      /** GGUF: the KV-cache positions allocated. */
      contextLength?: number;
      /** GGUF: the chat template takes `enable_thinking`. */
      thinking?: boolean;
    }
  | { type: 'error'; id?: string; message: string }
  | { type: 'delta'; id: string; text: string }
  | {
      type: 'step';
      id: string;
      /** Index of the generated token, from 0. */
      index: number;
      token: string;
      /** Probability of the chosen token. */
      p: number;
      /** Shannon entropy of the distribution, in bits. */
      entropy: number;
      topk: Alternative[];
      /** With `lens`: one entry per layer, first to last. */
      layers?: LayerLens[];
    }
  | {
      type: 'done';
      id: string;
      text: string;
      stop: StopReason;
      usage: Usage;
      /** Time to first token (ms), from the request arriving in the worker. */
      ttftMs: number;
      /** Decode speed: tokens after the first over the time after the first. */
      tokensPerSecond: number;
    }
  | { type: 'unloaded' };
