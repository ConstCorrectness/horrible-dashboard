/**
 * Live co-editing's provider, over an in-memory relay shaped like the backend's (each
 * frame to every other member). The things that must hold: concurrent edits merge
 * instead of one winning; someone joining late receives the whole document from a
 * single state-vector exchange; a frame survives base64 on the JSON socket; and a
 * person leaving disappears from everyone's presence.
 */
import { describe, expect, it } from 'vitest';
import * as Y from 'yjs';

import { fromBase64, LiveProvider, toBase64, type LiveTransport } from '../live/provider';

class Relay {
  private members: { handler: (frame: Uint8Array) => void; open: () => void }[] = [];
  /** Frames are delivered later, like a socket, unless flushed. */
  private queue: (() => void)[] = [];

  transport(): LiveTransport {
    const member = { handler: ((): void => {}) as (frame: Uint8Array) => void, open: () => {} };
    this.members.push(member);
    return {
      send: (frame) => {
        // Through base64, as the real channel carries it.
        const wire = fromBase64(toBase64(frame));
        for (const other of this.members)
          if (other !== member) this.queue.push(() => other.handler(wire));
      },
      onFrame: (handler) => {
        member.handler = handler;
        return () => {
          this.members = this.members.filter((m) => m !== member);
        };
      },
      onOpen: (handler) => {
        member.open = handler;
        this.queue.push(handler); // "the socket opened"
        return () => {};
      },
    };
  }

  flush(): void {
    while (this.queue.length) this.queue.shift()!();
  }
}

function peer(relay: Relay, seed?: string) {
  const doc = new Y.Doc();
  const text = doc.getText('source');
  if (seed !== undefined) text.insert(0, seed);
  const provider = new LiveProvider(doc, relay.transport());
  return { doc, text, provider };
}

describe('LiveProvider', () => {
  it('gives a late joiner the whole document and marks it synced', () => {
    const relay = new Relay();
    const host = peer(relay, '# Priors\n\nA paragraph.\n');
    relay.flush();
    const guest = peer(relay);
    expect(guest.provider.synced).toBe(false);
    relay.flush();
    expect(guest.text.toString()).toBe('# Priors\n\nA paragraph.\n');
    expect(guest.provider.synced).toBe(true);
    host.provider.destroy();
  });

  it('merges two people typing at once, in different places and the same one', () => {
    const relay = new Relay();
    const host = peer(relay, 'one two\n');
    relay.flush();
    const guest = peer(relay);
    relay.flush();
    // Both edit before either hears of the other's change.
    host.text.insert(0, 'HOST ');
    guest.text.insert(guest.text.length, 'GUEST\n');
    host.text.insert(host.text.toString().indexOf('two'), 'and ');
    guest.text.insert(guest.text.toString().indexOf('two'), 'or ');
    relay.flush();
    expect(host.text.toString()).toBe(guest.text.toString());
    const merged = host.text.toString();
    for (const piece of ['HOST ', 'GUEST', 'and ', 'or ', 'one ', 'two'])
      expect(merged).toContain(piece);
  });

  it('shows who is here, and forgets someone who leaves', () => {
    const relay = new Relay();
    const host = peer(relay, 'x');
    const guest = peer(relay);
    host.provider.awareness.setLocalStateField('user', { name: 'Ada', color: 'cursor-a' });
    guest.provider.awareness.setLocalStateField('user', { name: 'Grace', color: 'cursor-b' });
    relay.flush();
    const names = (p: LiveProvider) =>
      [...p.awareness.getStates().values()]
        .map((s) => (s as { user?: { name: string } }).user?.name)
        .filter(Boolean)
        .sort();
    expect(names(host.provider)).toEqual(['Ada', 'Grace']);
    guest.provider.destroy();
    relay.flush();
    expect(names(host.provider)).toEqual(['Ada']);
  });

  it('shows someone who was already here to whoever joins later', () => {
    const relay = new Relay();
    const host = peer(relay, 'x');
    host.provider.awareness.setLocalStateField('user', { name: 'Ada', color: 'cursor-a' });
    relay.flush();
    const late = peer(relay);
    late.provider.awareness.setLocalStateField('user', { name: 'Grace', color: 'cursor-b' });
    relay.flush();
    const names = [...late.provider.awareness.getStates().values()]
      .map((s) => (s as { user?: { name: string } }).user?.name)
      .filter(Boolean)
      .sort();
    expect(names).toEqual(['Ada', 'Grace']);
  });

  it('carries large frames through base64 intact', () => {
    const bytes = new Uint8Array(200_000).map((_, i) => (i * 31) % 256);
    expect(fromBase64(toBase64(bytes))).toEqual(bytes);
  });
});
