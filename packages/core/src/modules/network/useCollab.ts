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

import {
  collabJoin,
  collabLeave,
  collabOp,
  collabShare,
  collabUnshare,
  subscribeCollab,
} from './collab';

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
  /**
   * The people this pane is shared **with**, by person rather than by machine.
   * Empty means it is syncing only between your own browser tabs — which is what
   * "shared" used to mean everywhere, while the ops went to every peer anyway.
   */
  people: { personId: string; name: string }[];
  /** Share with someone. The fabric picks whichever of their machines is up. */
  share: (personId: string) => void;
  unshare: (personId: string) => void;
  /** The last share failure, e.g. nobody of theirs is online. */
  error: string | null;
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
  const [people, setPeople] = useState<{ personId: string; name: string }[]>([]);
  const [error, setError] = useState<string | null>(null);
  // Last revision the backend acked, sent as baseRev with the next edit.
  const revRef = useRef(0);

  useEffect(() => {
    if (!shared) {
      setMembers(0);
      setPeople([]);
      return;
    }
    const unsub = subscribeCollab(paneKey, (update) => {
      if (update.kind === 'presence') {
        setMembers(update.members ?? 0);
        return;
      }
      if (update.kind === 'shared') {
        setPeople(update.people ?? []);
        return;
      }
      if (update.kind === 'error') {
        setError(update.error ?? 'share failed');
        return;
      }
      // state / op / rejected all carry the authoritative text + rev to adopt.
      // The two branches above return early for a reason: they carry no text, so
      // falling through here would blank the pane and reset its revision.
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

  const share = useCallback(
    (personId: string) => {
      setError(null);
      // Sharing with someone implies syncing: turning it on here means a pane
      // that was local can be shared in one action rather than two.
      setShared(true);
      collabShare(paneKey, personId);
    },
    [paneKey],
  );

  const unshare = useCallback(
    (personId: string) => {
      collabUnshare(paneKey, personId);
    },
    [paneKey],
  );

  return { text, setText, shared, setShared, members, people, share, unshare, error };
}
