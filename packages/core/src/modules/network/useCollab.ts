/**
 * `useCollab` — the host hook a **network-aware pane** uses to sync its text-ish
 * state across nodes over the `/ws` `collab` channel. It encapsulates everything
 * the scratch panel used to hand-wire: joining/leaving the room, tracking the
 * authoritative revision, rebasing on a rejected (stale) op, and surfacing live
 * peer presence. Any pane that declares `collab` in its manifest drives it through
 * this hook instead of touching the channel primitives directly.
 *
 * See docs/modules/network.mdx (collab) and the `CollabDecl` contract in
 * `@horribledashboard/sdk`.
 */
import { useCallback, useEffect, useRef, useState } from 'react';

import { collabJoin, collabLeave, collabOp, subscribeCollab } from './collab';

export interface CollabPane {
  /** The current text (authoritative while shared, local otherwise). */
  text: string;
  /** Edit the text. When shared, the edit is pushed to the room as an op. */
  setText: (next: string) => void;
  /** Whether this pane is currently syncing with the room. */
  shared: boolean;
  /** Toggle sharing on/off. */
  setShared: (on: boolean) => void;
  /** Live count of browsers in the room (this node + peers' members). */
  members: number;
}

export interface UseCollabOptions {
  /** Seed text used before the room sends its authoritative state. */
  initialText?: string;
  /** Start shared immediately (from a manifest's `collab.autoShare`). */
  autoShare?: boolean;
}

/**
 * Drive a collaborative pane keyed by `paneKey`. Returns the pane's text, an
 * editor, the share toggle, and a live presence count.
 */
export function useCollab(paneKey: string, opts: UseCollabOptions = {}): CollabPane {
  const [text, setTextState] = useState(opts.initialText ?? '');
  const [shared, setShared] = useState(Boolean(opts.autoShare));
  const [members, setMembers] = useState(0);
  // Last revision the backend acked, sent as baseRev with the next edit.
  const revRef = useRef(0);

  useEffect(() => {
    if (!shared) {
      setMembers(0);
      return;
    }
    const unsub = subscribeCollab(paneKey, (update) => {
      if (update.kind === 'presence') {
        setMembers(update.members ?? 0);
        return;
      }
      // state / op / rejected all carry the authoritative text + rev to adopt.
      revRef.current = update.rev;
      setTextState(update.text);
      if (update.members !== undefined) setMembers(update.members);
    });
    collabJoin(paneKey);
    return () => {
      collabLeave(paneKey);
      unsub();
    };
  }, [shared, paneKey]);

  const setText = useCallback(
    (next: string) => {
      setTextState(next);
      if (shared) collabOp(paneKey, revRef.current, next);
    },
    [shared, paneKey],
  );

  return { text, setText, shared, setShared, members };
}
