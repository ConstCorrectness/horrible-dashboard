/**
 * `WebmlEngine`: the UI-thread side of one model worker. It owns the worker,
 * mirrors its state as a subscribable snapshot (shaped for `useSyncExternalStore`)
 * and turns the event protocol into promises.
 *
 * The worker is created on the first `load`, so constructing an engine is free.
 */
import { isGgufModelId } from './gguf/store';
import type {
  Device,
  Dtype,
  EngineKind,
  GenerateRequest,
  StopReason,
  Usage,
  WorkerEvent,
  WorkerRequest,
} from './protocol';

export interface FileProgress {
  loaded: number;
  total: number;
  done: boolean;
}

export type EngineState =
  | { kind: 'idle' }
  | {
      kind: 'loading';
      model: string;
      dtype: Dtype;
      /** `download` while files arrive, `warmup` while shaders compile. */
      phase: 'download' | 'warmup';
      files: Record<string, FileProgress>;
    }
  | {
      kind: 'ready';
      model: string;
      dtype: Dtype;
      device: Device;
      loadMs: number;
      engine: EngineKind;
      /** GGUF: the dominant weight type ("Q8_0"). */
      quant?: string;
      /** GGUF: KV-cache positions allocated. */
      contextLength?: number;
      /** GGUF: the chat template takes `enable_thinking`. */
      thinking?: boolean;
    }
  | { kind: 'error'; message: string; model?: string };

export interface GenerationResult {
  text: string;
  stop: StopReason;
  usage: Usage;
  ttftMs: number;
  tokensPerSecond: number;
}

export type StepEvent = Extract<WorkerEvent, { type: 'step' }>;

export interface GenerateHandlers {
  onDelta?: (text: string) => void;
  onStep?: (step: StepEvent) => void;
  /** Aborting interrupts the generation; the promise still resolves with what was produced. */
  signal?: AbortSignal;
}

export type GenerateOptions = Omit<GenerateRequest, 'type' | 'id'>;

interface Pending {
  handlers: GenerateHandlers;
  resolve: (result: GenerationResult) => void;
  reject: (err: Error) => void;
}

export function defaultWorker(): Worker {
  return new Worker(new URL('./engine/transformers.worker.ts', import.meta.url), {
    type: 'module',
    name: 'webml',
  });
}

export function ggufWorker(): Worker {
  return new Worker(new URL('./engine/gguf.worker.ts', import.meta.url), {
    type: 'module',
    name: 'webml-gguf',
  });
}

/** The worker for each engine: transformers.js for ONNX, our WGSL engine for GGUF. */
export function workerFor(kind: EngineKind): Worker {
  return kind === 'gguf' ? ggufWorker() : defaultWorker();
}

/** Which engine runs `model`: `gguf:` ids are GGUF files, anything else an ONNX repo. */
export function engineFor(model: string): EngineKind {
  return isGgufModelId(model) ? 'gguf' : 'onnx';
}

let seq = 0;

export class WebmlEngine {
  private worker: Worker | null = null;
  /** The engine the current (or next) worker runs. */
  private kind: EngineKind = 'onnx';
  private state: EngineState = { kind: 'idle' };
  private readonly listeners = new Set<() => void>();
  private loadWaiter: { resolve: () => void; reject: (err: Error) => void } | null = null;
  private readonly pending = new Map<string, Pending>();

  constructor(private readonly createWorker: (kind: EngineKind) => Worker = workerFor) {}

  getState = (): EngineState => this.state;

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  /** A generation is running. */
  get busy(): boolean {
    return this.pending.size > 0;
  }

  /**
   * Load a model (resolves when it is warmed up). Loading the loaded model is a
   * no-op. A model for the other engine replaces the worker: terminating it is the
   * only way to give its GPU memory back.
   */
  load(
    model: string,
    dtype: Dtype,
    device: Device = 'webgpu',
    options: { contextLength?: number } = {},
  ): Promise<void> {
    const s = this.state;
    if (s.kind === 'ready' && s.model === model && s.dtype === dtype && s.device === device) {
      return Promise.resolve();
    }
    const kind = engineFor(model);
    if (this.worker && kind !== this.kind) {
      this.worker.terminate();
      this.worker = null;
      for (const p of this.pending.values())
        p.reject(new Error('superseded by loading another model'));
      this.pending.clear();
    }
    this.kind = kind;
    this.loadWaiter?.reject(new Error('superseded by another load'));
    this.set({ kind: 'loading', model, dtype, phase: 'download', files: {} });
    return new Promise<void>((resolve, reject) => {
      this.loadWaiter = { resolve, reject };
      this.send({ type: 'load', model, dtype, device, contextLength: options.contextLength });
    });
  }

  generate(options: GenerateOptions, handlers: GenerateHandlers = {}): Promise<GenerationResult> {
    if (this.state.kind !== 'ready') return Promise.reject(new Error('no model loaded'));
    if (this.busy) return Promise.reject(new Error('the model is busy with another reply'));
    const id = `g${++seq}`;
    return new Promise<GenerationResult>((resolve, reject) => {
      this.pending.set(id, { handlers, resolve, reject });
      if (handlers.signal) {
        if (handlers.signal.aborted) queueMicrotask(() => this.interrupt());
        else handlers.signal.addEventListener('abort', () => this.interrupt(), { once: true });
      }
      this.send({ type: 'generate', id, ...options });
    });
  }

  interrupt(): void {
    this.worker?.postMessage({ type: 'interrupt' } satisfies WorkerRequest);
  }

  /** Free the model's GPU memory; the worker stays for the next load. */
  unload(): void {
    if (!this.worker) return;
    this.send({ type: 'unload' });
  }

  /** Kill the worker outright — the only way to recover a wedged GPU device. */
  terminate(): void {
    this.worker?.terminate();
    this.worker = null;
    this.loadWaiter?.reject(new Error('engine terminated'));
    this.loadWaiter = null;
    for (const p of this.pending.values()) p.reject(new Error('engine terminated'));
    this.pending.clear();
    this.set({ kind: 'idle' });
  }

  private send(req: WorkerRequest): void {
    if (!this.worker) {
      this.worker = this.createWorker(this.kind);
      this.worker.addEventListener('message', (e: MessageEvent<WorkerEvent>) =>
        this.onEvent(e.data),
      );
      this.worker.addEventListener('error', (e) =>
        this.onCrash(e.message || 'the model worker crashed'),
      );
    }
    this.worker.postMessage(req);
  }

  private onCrash(message: string): void {
    this.worker?.terminate();
    this.worker = null;
    this.loadWaiter?.reject(new Error(message));
    this.loadWaiter = null;
    for (const p of this.pending.values()) p.reject(new Error(message));
    this.pending.clear();
    this.set({ kind: 'error', message });
  }

  private onEvent(event: WorkerEvent): void {
    switch (event.type) {
      case 'progress': {
        const s = this.state;
        if (s.kind !== 'loading') return;
        if (event.status === 'warmup') {
          this.set({ ...s, phase: 'warmup' });
          return;
        }
        const prev = s.files[event.file] ?? { loaded: 0, total: 0, done: false };
        const done = event.status === 'done';
        this.set({
          ...s,
          files: {
            ...s.files,
            [event.file]: {
              loaded: done ? Math.max(prev.loaded, prev.total) : event.loaded || prev.loaded,
              total: event.total || prev.total,
              done,
            },
          },
        });
        return;
      }
      case 'ready':
        this.set({
          kind: 'ready',
          model: event.model,
          dtype: event.dtype,
          device: event.device,
          loadMs: event.loadMs,
          engine: event.engine ?? 'onnx',
          quant: event.quant,
          contextLength: event.contextLength,
          thinking: event.thinking,
        });
        this.loadWaiter?.resolve();
        this.loadWaiter = null;
        return;
      case 'unloaded':
        this.set({ kind: 'idle' });
        return;
      case 'error': {
        if (event.id) {
          const p = this.pending.get(event.id);
          this.pending.delete(event.id);
          p?.reject(new Error(event.message));
          return;
        }
        const model = this.state.kind === 'loading' ? this.state.model : undefined;
        this.set({ kind: 'error', message: event.message, model });
        this.loadWaiter?.reject(new Error(event.message));
        this.loadWaiter = null;
        return;
      }
      case 'delta':
        this.pending.get(event.id)?.handlers.onDelta?.(event.text);
        return;
      case 'step':
        this.pending.get(event.id)?.handlers.onStep?.(event);
        return;
      case 'done': {
        const p = this.pending.get(event.id);
        this.pending.delete(event.id);
        p?.resolve({
          text: event.text,
          stop: event.stop,
          usage: event.usage,
          ttftMs: event.ttftMs,
          tokensPerSecond: event.tokensPerSecond,
        });
        this.emit();
        return;
      }
    }
  }

  private set(state: EngineState): void {
    this.state = state;
    this.emit();
  }

  private emit(): void {
    for (const l of this.listeners) l();
  }
}

/** Bytes downloaded and expected across a load's files (files with unknown size count as 0). */
export function loadTotals(files: Record<string, FileProgress>): { loaded: number; total: number } {
  let loaded = 0;
  let total = 0;
  for (const f of Object.values(files)) {
    loaded += f.loaded;
    total += f.total;
  }
  return { loaded, total };
}
