/**
 * A Yjs document kept in step with the other people editing it live.
 *
 * The wire is y-websocket's: each message is one y-protocols frame — `0` sync (state
 * vector, missing updates, or an update), `1` awareness (who is here, their cursor) —
 * carried base64 on the `/ws` `scrive-live` channel and relayed by the backend
 * (`backend/modules/scrive/live.py`), which never reads them. On (re)joining, the
 * provider sends its state vector; whoever has what it lacks answers with exactly
 * that, so a guest arriving late, or a host whose socket dropped, converges without
 * anyone resending the whole page.
 *
 * The transport is a parameter so tests can wire two providers to each other.
 */
import * as decoding from 'lib0/decoding';
import * as encoding from 'lib0/encoding';
import {
  applyAwarenessUpdate,
  Awareness,
  encodeAwarenessUpdate,
  removeAwarenessStates,
} from 'y-protocols/awareness';
import { readSyncMessage, writeSyncStep1, writeUpdate } from 'y-protocols/sync';
import type * as Y from 'yjs';

const MESSAGE_SYNC = 0;
const MESSAGE_AWARENESS = 1;

/** Where a provider's frames go, and where the others' arrive. */
export interface LiveTransport {
  send(frame: Uint8Array): void;
  /** Answers an unsubscribe. */
  onFrame(handler: (frame: Uint8Array) => void): () => void;
  /** Called on every (re)connect, so the provider can resync. */
  onOpen(handler: () => void): () => void;
}

export class LiveProvider {
  readonly awareness: Awareness;
  /** True once another member has answered our state vector. */
  synced = false;
  private readonly offs: (() => void)[] = [];
  private readonly syncListeners = new Set<() => void>();

  constructor(
    readonly doc: Y.Doc,
    private readonly transport: LiveTransport,
    awareness?: Awareness,
  ) {
    this.awareness = awareness ?? new Awareness(doc);
    const onUpdate = (update: Uint8Array, origin: unknown) => {
      if (origin === this) return; // came from the wire: do not echo it
      const encoder = encoding.createEncoder();
      encoding.writeVarUint(encoder, MESSAGE_SYNC);
      writeUpdate(encoder, update);
      this.transport.send(encoding.toUint8Array(encoder));
    };
    doc.on('update', onUpdate);
    this.offs.push(() => doc.off('update', onUpdate));

    const onAwareness = (
      { added, updated, removed }: { added: number[]; updated: number[]; removed: number[] },
      origin: unknown,
    ) => {
      if (origin === this) return;
      this.sendAwareness([...added, ...updated, ...removed]);
    };
    this.awareness.on('update', onAwareness);
    this.offs.push(() => this.awareness.off('update', onAwareness));

    this.offs.push(transport.onFrame((frame) => this.receive(frame)));
    this.offs.push(transport.onOpen(() => this.resync()));
  }

  /** Ask the others for what we lack, and say who we are. */
  resync(): void {
    const encoder = encoding.createEncoder();
    encoding.writeVarUint(encoder, MESSAGE_SYNC);
    writeSyncStep1(encoder, this.doc);
    this.transport.send(encoding.toUint8Array(encoder));
    this.sendAwareness([this.doc.clientID]);
  }

  onSynced(listener: () => void): () => void {
    this.syncListeners.add(listener);
    return () => this.syncListeners.delete(listener);
  }

  private sendAwareness(clients: number[]): void {
    const encoder = encoding.createEncoder();
    encoding.writeVarUint(encoder, MESSAGE_AWARENESS);
    encoding.writeVarUint8Array(encoder, encodeAwarenessUpdate(this.awareness, clients));
    this.transport.send(encoding.toUint8Array(encoder));
  }

  private receive(frame: Uint8Array): void {
    const decoder = decoding.createDecoder(frame);
    const type = decoding.readVarUint(decoder);
    if (type === MESSAGE_SYNC) {
      const encoder = encoding.createEncoder();
      encoding.writeVarUint(encoder, MESSAGE_SYNC);
      const kind = readSyncMessage(decoder, encoder, this.doc, this);
      // A state vector arrived: answer it with what they are missing.
      if (encoding.length(encoder) > 1) this.transport.send(encoding.toUint8Array(encoder));
      // Step 1 (`0`) is someone (re)joining: say who we are too, or they would not see
      // us until awareness next renews itself, some fifteen seconds on.
      if (kind === 0) this.sendAwareness([this.doc.clientID]);
      // Step 2 (`1`) is someone's answer to ours: from here we hold the shared doc.
      if (kind === 1 && !this.synced) {
        this.synced = true;
        this.syncListeners.forEach((l) => l());
      }
    } else if (type === MESSAGE_AWARENESS) {
      applyAwarenessUpdate(this.awareness, decoding.readVarUint8Array(decoder), this);
    }
  }

  destroy(): void {
    removeAwarenessStates(this.awareness, [this.doc.clientID], 'left');
    this.offs.splice(0).forEach((off) => off());
    this.syncListeners.clear();
  }
}

// --- base64, for frames on a JSON socket ----------------------------------------------

export function toBase64(bytes: Uint8Array): string {
  let binary = '';
  const CHUNK = 0x8000;
  for (let i = 0; i < bytes.length; i += CHUNK)
    binary += String.fromCharCode(...bytes.subarray(i, i + CHUNK));
  return btoa(binary);
}

export function fromBase64(text: string): Uint8Array {
  const binary = atob(text);
  const out = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) out[i] = binary.charCodeAt(i);
  return out;
}
