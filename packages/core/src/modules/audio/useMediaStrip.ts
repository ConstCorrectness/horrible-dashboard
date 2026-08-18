/**
 * Route an `<audio>`/`<video>` element's sound through the mixer.
 *
 * This is the hook that makes "play a video into my microphone" possible: a
 * media element normally goes straight to the system default output and there is
 * no API to redirect it after the fact, but `createMediaElementSource` takes its
 * audio into the graph, where the routing matrix can send it anywhere — several
 * places at once, including a virtual cable another application is listening to.
 *
 * ## Three rules, all of which bite silently
 *
 * 1. **`createMediaElementSource` may be called only once per element.** A
 *    second call throws `InvalidStateError`, and in React — where an effect can
 *    re-run on any dependency change — that is easy to do by accident. The
 *    `WeakMap` below is the guard; weak so that an element that goes away is not
 *    retained by our bookkeeping.
 * 2. **Attaching *moves* the audio.** Once an element has a source node, it no
 *    longer plays to the default output on its own — it plays only through
 *    whatever the node is connected to. Attach and then fail to connect, and the
 *    video plays in total silence with no error.
 * 3. **Cross-origin media yields silence.** An element loaded from another
 *    origin without CORS produces a source node that outputs zeros rather than
 *    throwing. Everything routed through here must be same-origin (the karaoke
 *    media is served by our own backend) or CORS-enabled. This is also why an
 *    embedded YouTube *iframe* can never be routed: there is no element to
 *    attach to, which is why the karaoke path (yt-dlp to a local file) is the
 *    one that works.
 */

import { useEffect } from 'react';

import { mixer } from './engine';
import type { StripDecl } from './types';

/**
 * Elements already wired into the graph, and the node they were given.
 *
 * Weak on the element so a discarded `<video>` does not keep its source node —
 * and its decoded audio — alive.
 */
const attached = new WeakMap<HTMLMediaElement, MediaElementAudioSourceNode>();

/**
 * Attach a media element to a mixer strip for as long as the component lives.
 *
 * Pass a ref, not an element: the element does not exist on the first render,
 * and the effect re-runs when it appears.
 */
export function useMediaStrip(
  ref: React.RefObject<HTMLMediaElement | null>,
  decl: StripDecl,
): void {
  const { id, label, icon } = decl;

  useEffect(() => {
    mixer.declareStrip({ id, label, icon });
  }, [id, label, icon]);

  useEffect(() => {
    const element = ref.current;
    if (!element) return;

    const handle = mixer.connectStrip(id);
    let source = attached.get(element);
    if (!source) {
      try {
        source = handle.context.createMediaElementSource(element);
        attached.set(element, source);
      } catch {
        // Already owned by another context, or the element is in a state that
        // refuses. Leave it playing to the default output — silent audio is a
        // worse outcome than unroutable audio.
        handle.release();
        return;
      }
    }
    source.connect(handle.input);

    return () => {
      // Disconnect, never `close`: the source node stays bound to this element
      // for its lifetime (rule 1), so a remount reconnects the same node rather
      // than making a second one.
      try {
        source?.disconnect(handle.input);
      } catch {
        // Already torn down.
      }
      handle.release();
    };
  }, [ref, id]);
}
