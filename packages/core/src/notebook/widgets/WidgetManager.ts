/**
 * A lightweight ipywidgets manager: one per kernel session. It speaks the standard
 * Jupyter **comm** protocol — `comm_open` carries a widget model's initial state,
 * `comm_msg` carries incremental state updates — but renders the common core widgets
 * with native controls (see WidgetView) instead of the heavy `@jupyter-widgets`
 * view stack. Standard `ipywidgets` slider/button/text/etc. code works unchanged;
 * arbitrary third-party / anywidget rendering is a later phase.
 *
 * `comm_id` === the widget `model_id` in ipywidgets, so a widget-view output's
 * `model_id` resolves directly to a comm here.
 */
import { onKernelEvent, sendCommMsg, type CommContent, type CommSnapshot } from '../kernelClient';

export type WidgetState = Record<string, unknown>;

/** An inbound custom comm message (`model.send` on the kernel side; anywidget's
 * `msg:custom`). Buffers arrive base64-decoded. */
export type CustomMsg = { content: unknown; buffers: ArrayBuffer[] };

const b64ToBuf = (b64: string): ArrayBuffer => {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes.buffer;
};

export class WidgetManager {
  private models = new Map<string, WidgetState>();
  private listeners = new Map<string, Set<() => void>>();
  private customListeners = new Map<string, Set<(m: CustomMsg) => void>>();

  constructor(
    readonly channel: string,
    readonly sessionKey: string,
  ) {}

  // --- comm ingestion -------------------------------------------------------

  onOpen(comm: CommContent): void {
    const state = (comm.data?.state ?? {}) as WidgetState;
    this.models.set(comm.comm_id, { ...state });
    this.notify(comm.comm_id);
  }

  onMsg(comm: CommContent, buffers: string[] = []): void {
    const method = comm.data?.method;
    if (method === 'update' || method === 'echo_update') {
      const patch = (comm.data?.state ?? {}) as WidgetState;
      const cur = this.models.get(comm.comm_id) ?? {};
      this.models.set(comm.comm_id, { ...cur, ...patch });
      this.notify(comm.comm_id);
    } else if (method === 'custom') {
      this.customListeners
        .get(comm.comm_id)
        ?.forEach((cb) => cb({ content: comm.data?.content, buffers: buffers.map(b64ToBuf) }));
    }
  }

  onClose(comm: CommContent): void {
    this.models.delete(comm.comm_id);
    this.notify(comm.comm_id);
  }

  /** Re-hydrate models from an `opened` snapshot (reattach-resync). Only fills
   * gaps, so it never clobbers a live model another pane is already driving. */
  seed(comms: CommSnapshot[] | undefined): void {
    for (const c of comms ?? []) {
      if (!this.models.has(c.comm_id)) {
        this.models.set(c.comm_id, { ...c.state });
        this.notify(c.comm_id);
      }
    }
  }

  // --- view API -------------------------------------------------------------

  getState(modelId: string): WidgetState | undefined {
    return this.models.get(modelId);
  }

  subscribe(modelId: string, cb: () => void): () => void {
    let set = this.listeners.get(modelId);
    if (!set) {
      set = new Set();
      this.listeners.set(modelId, set);
    }
    set.add(cb);
    return () => set.delete(cb);
  }

  /** Subscribe to inbound custom messages for a model (anywidget `msg:custom`). */
  subscribeCustom(modelId: string, cb: (m: CustomMsg) => void): () => void {
    let set = this.customListeners.get(modelId);
    if (!set) {
      set = new Set();
      this.customListeners.set(modelId, set);
    }
    set.add(cb);
    return () => set.delete(cb);
  }

  /** Update a model's state locally (optimistic) without touching the kernel —
   * anywidget's `model.set` stages here; `save_changes` flushes via `commit`. */
  setLocal(modelId: string, patch: WidgetState): void {
    const cur = this.models.get(modelId);
    if (!cur) return;
    this.models.set(modelId, { ...cur, ...patch });
    this.notify(modelId);
  }

  /** Send a state patch to the kernel comm (no local mutation). */
  commit(modelId: string, patch: WidgetState): void {
    sendCommMsg(this.channel, this.sessionKey, modelId, { method: 'update', state: patch });
  }

  /** UI changed a widget value: update locally (optimistic) and tell the kernel. */
  setState(modelId: string, patch: WidgetState): void {
    if (!this.models.has(modelId)) return;
    this.setLocal(modelId, patch);
    this.commit(modelId, patch);
  }

  /** A button/custom event (e.g. Button.on_click, anywidget `model.send`). */
  sendCustom(modelId: string, content: Record<string, unknown>): void {
    sendCommMsg(this.channel, this.sessionKey, modelId, { method: 'custom', content });
  }

  private notify(modelId: string): void {
    this.listeners.get(modelId)?.forEach((l) => l());
  }
}

const managers = new Map<string, WidgetManager>();
const wired = new Set<string>();

function wire(channel: string): void {
  if (wired.has(channel)) return;
  wired.add(channel);
  onKernelEvent(channel, 'comm_open', (d) => managers.get(d.sessionKey)?.onOpen(d.comm));
  onKernelEvent(channel, 'comm_msg', (d) => managers.get(d.sessionKey)?.onMsg(d.comm, d.buffers));
  onKernelEvent(channel, 'comm_close', (d) => managers.get(d.sessionKey)?.onClose(d.comm));
}

/** Get (or create) the widget manager for a session. */
export function widgetManagerFor(channel: string, sessionKey: string): WidgetManager {
  wire(channel);
  let m = managers.get(sessionKey);
  if (!m) {
    m = new WidgetManager(channel, sessionKey);
    managers.set(sessionKey, m);
  }
  return m;
}
