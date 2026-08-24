/**
 * The host side of the semantic mirror: watch this workspace, redact it, publish
 * it.
 *
 * Runs only while this node is hosting a session, and stops the moment it is not
 * — a subscription to the layout store that outlived the session would keep
 * projecting a workspace nobody is watching, and would start publishing again the
 * instant a socket reconnected.
 *
 * Two things keep the traffic sane, and both compare the **projection** rather
 * than the frame:
 *
 * - Nothing is sent when the redacted view of the workspace is unchanged. The
 *   common case — the host typing inside a pane, or working entirely in panes the
 *   guest cannot see — produces no traffic at all.
 * - A change is coalesced to at most one send per `MIN_INTERVAL_MS`. The layout
 *   store fires on every pixel of a drag, and a guest does not need 60 of those a
 *   second to know a split moved.
 */
import { registry } from '../../registry';
import { layoutStore } from '../../layout/store';
import {
  mirrorChanged,
  redactFrame,
  summarize,
  type MirrorFrame,
  type ViewShareInfo,
} from './mirror';
import { getShareSnapshot, sendMirror, subscribeShare } from './ws';

/** Floor on how often a projection goes out, in ms. */
const MIN_INTERVAL_MS = 400;

function lookup(viewId: string): ViewShareInfo | undefined {
  const decl = registry.view(viewId);
  if (!decl) return undefined;
  return { title: decl.title, share: decl.share };
}

let unsubscribe: (() => void) | null = null;
let last: MirrorFrame | null = null;
let timer: ReturnType<typeof setTimeout> | null = null;
let pending = false;

function flush(): void {
  timer = null;
  if (!pending) return;
  pending = false;
  const frame = redactFrame(layoutStore.getSnapshot().frame, lookup);
  if (!mirrorChanged(last, frame)) return;
  last = frame;
  sendMirror(frame, summarize(frame));
}

function onLayoutChange(): void {
  pending = true;
  // Leading-edge send, then a trailing one for whatever the drag settles on.
  // A purely trailing throttle makes every change feel 400ms late; a purely
  // leading one drops the final position of a drag, which is the one that matters.
  if (timer === null) {
    flush();
    timer = setTimeout(flush, MIN_INTERVAL_MS);
  }
}

/** Begin projecting this workspace to the session's guests. */
export function startPublishing(): void {
  if (unsubscribe) return;
  // `last` is cleared rather than kept: a new session has new guests, and they
  // must receive a full projection rather than nothing-has-changed.
  last = null;
  unsubscribe = layoutStore.subscribe(onLayoutChange);
  pending = true;
  flush();
}

/** Stop. Safe to call when not publishing. */
export function stopPublishing(): void {
  unsubscribe?.();
  unsubscribe = null;
  if (timer !== null) clearTimeout(timer);
  timer = null;
  pending = false;
  last = null;
}

export function isPublishing(): boolean {
  return unsubscribe !== null;
}

/**
 * Re-send the current projection even if it has not changed.
 *
 * For a guest who has just joined: they need the whole picture, and the host's
 * layout may not change again for minutes.
 */
export function republish(): void {
  if (!unsubscribe) return;
  last = null;
  pending = true;
  flush();
}


/**
 * Tie publishing to the session's lifetime.
 *
 * Publishing must start and stop with the *session*, not with a pane: the mirror
 * pane is the guest's surface, and a host who never opens it is still hosting.
 * Driving it from the session snapshot also means a session started by the agent,
 * by a command, or from another tab all begin projecting with no extra wiring.
 */
export function bindPublishing(): () => void {
  let guests = 0;
  return subscribeShare(() => {
    const hosting = getShareSnapshot().hosting;
    if (!hosting) {
      guests = 0;
      stopPublishing();
      return;
    }
    startPublishing();
    // Somebody new arrived. The backend hands a joiner whatever projection it
    // holds, but that one is only as fresh as the host's last layout change —
    // republishing on a join means the first thing they see is current.
    const next = hosting.participants.length;
    if (next > guests) republish();
    guests = next;
  });
}
