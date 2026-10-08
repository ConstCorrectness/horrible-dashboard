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
  model: string;
  dtype: Dtype;
  device: Device;
}

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

export type StopReason = 'eos' | 'length' | 'interrupt';

export interface Usage {
  promptTokens: number;
  completionTokens: number;
}

export type WorkerEvent =
  | { type: 'progress'; file: string; loaded: number; total: number; status: string }
  | { type: 'ready'; model: string; dtype: Dtype; device: Device; loadMs: number }
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
