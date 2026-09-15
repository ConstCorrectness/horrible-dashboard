import { useSyncExternalStore } from 'react';

import { IconStop } from '../../../glyphs';
import { registry } from '../../../registry';
import { getStreamState, stopStream, subscribeStream } from '../stream';
import { getShareSnapshot, subscribeShare } from '../ws';

import './live-indicator.css';

/**
 * "You are broadcasting", in the shell chrome, with the Share pane closed.
 *
 * The browser's own sharing bar says a capture is running but not who is on the
 * other end, and it is gone entirely inside the desktop shell. The pane says
 * everything, but only while it is open — and a share outlives the pane that
 * started it by design. So this is the one surface that is on screen whenever
 * something is going out, and it is quiet (renders nothing) the rest of the time.
 *
 * Two states, because they are two different exposures: a live **capture** is
 * pixels leaving right now; an open **link** with no capture is a URL somebody
 * could still be holding. Both deserve a readout; only the first gets Stop.
 */
export function ShareLiveIndicator() {
  const stream = useSyncExternalStore(subscribeStream, getStreamState, getStreamState);
  const share = useSyncExternalStore(subscribeShare, getShareSnapshot, getShareSnapshot);
  const hosting = share.hosting;
  const linkOpen = Boolean(hosting?.link);
  if (!stream.live && !linkOpen) return null;

  const guests = hosting?.participants.filter((p) => p.role === 'guest').length ?? 0;
  const meta: string[] = [];
  if (stream.live) {
    meta.push(`${guests} guest${guests === 1 ? '' : 's'}`);
    // The relay's own count. Only meaningful while publishing — the node polls it
    // then — so it is omitted for a link with nothing behind it rather than
    // shown as a confident zero.
    if (linkOpen) meta.push(`${stream.relayViewers} via link`);
  }

  return (
    <div
      className={`share-live${stream.live ? ' share-live--on' : ''}`}
      role="status"
      aria-live="polite"
    >
      <button
        type="button"
        className="share-live__open"
        title="Open the Share panel"
        onClick={() => registry.openPanel('share.session')}
      >
        <span className="share-live__dot" aria-hidden="true" />
        <span className="share-live__label">{stream.live ? 'Live' : 'Link open'}</span>
        {meta.length > 0 && <span className="share-live__meta">{meta.join(' · ')}</span>}
      </button>
      {stream.live && (
        <button
          type="button"
          className="share-live__stop"
          aria-label="Stop sharing screen"
          title="Stop sharing screen"
          onClick={() => void stopStream()}
        >
          <IconStop />
        </button>
      )}
    </div>
  );
}
