/**
 * Full-engine viewport: paints the backend's server-rendered Chromium frames onto a
 * `<canvas>` and relays pointer/keyboard input back over the `browser` `/ws` channel
 * (the vizdoom/visualizer pattern). Navigation/back/forward/reload are issued by the
 * parent toolbar through the engine ops; this component owns only the live frame,
 * input capture and viewport sizing, and reports the live URL/title upward via `onMeta`.
 *
 * **Frames are stills, not video.** CDP's `Page.startScreencast` emits JPEG frames —
 * there is no H.264/VP8 elementary stream, so `VideoDecoder` has nothing to accept.
 * The applicable WebCodecs API is `ImageDecoder`, with `createImageBitmap` as the
 * fallback. Both decode **off the main thread** and yield something `drawImage` can
 * blit ~free, which is why this doesn't need an OffscreenCanvas worker: the expensive
 * half (JPEG decode) is already off-thread, and the cheap half is a GPU blit.
 *
 * Two rules the pipeline depends on:
 * - **Only the newest frame matters.** A frame arriving mid-decode replaces the
 *   pending one rather than queueing, so a slow decode drops frames instead of
 *   accumulating latency.
 * - **Every decoded frame must be closed.** `ImageBitmap`/`VideoFrame` hold GPU
 *   memory that GC does not promptly reclaim; leaking one per frame at 20fps
 *   exhausts it in minutes.
 *
 * Sizing is driven by the pane, not hardcoded: a `ResizeObserver` pushes the pane's
 * real size to `page.set_viewport_size` (debounced), and click mapping uses the
 * per-frame `deviceWidth`/`deviceHeight` CDP reports rather than a constant.
 */
import {
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type MouseEvent,
  type WheelEvent,
} from 'react';

import { PaneInstanceContext } from '../../../agent-context';
import { useCapture } from '../../../keymap';
import {
  engine,
  sendInput,
  startSession,
  subscribeFrames,
  subscribeErrors,
  type BrowserFrame,
} from '../session';

// Fallback viewport, used only until the first resize lands and for mapping clicks
// on fallback-poll frames (which carry no metadata). Matches _DEFAULT_VIEWPORT in
// backend/modules/browser/session.py.
const DEFAULT_VW = 1280;
const DEFAULT_VH = 800;

// Resize debounce. A pane drag fires ResizeObserver continuously, and every resize
// costs a real Chromium relayout plus a screencast restart — settle first.
const RESIZE_DEBOUNCE_MS = 150;

// Keys we forward as a named press (everything else of length 1 is inserted as text).
const NAMED_KEYS = new Set([
  'Enter',
  'Backspace',
  'Tab',
  'Escape',
  'Delete',
  'ArrowUp',
  'ArrowDown',
  'ArrowLeft',
  'ArrowRight',
  'Home',
  'End',
  'PageUp',
  'PageDown',
]);

/** Anything `drawImage` accepts that also owns releasable memory. */
type DecodedFrame = ImageBitmap | VideoFrame;

/** `data:image/jpeg;base64,…` → raw bytes, without a fetch() round trip. */
function dataUriToBytes(uri: string): Uint8Array {
  const comma = uri.indexOf(',');
  const binary = atob(comma >= 0 ? uri.slice(comma + 1) : uri);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

/**
 * Decode one JPEG frame off the main thread.
 *
 * `ImageDecoder` is preferred where present (Chromium, so: the desktop webview and
 * most browsers running this app) — it takes the bytes directly. `createImageBitmap`
 * is the portable fallback and costs one extra Blob wrap.
 */
async function decodeFrame(uri: string): Promise<DecodedFrame> {
  const bytes = dataUriToBytes(uri);
  const Decoder = (globalThis as { ImageDecoder?: typeof ImageDecoder }).ImageDecoder;
  if (Decoder) {
    const decoder = new Decoder({ data: bytes, type: 'image/jpeg' });
    try {
      const { image } = await decoder.decode();
      return image;
    } finally {
      // The decoder holds its own buffers; the decoded VideoFrame is independent
      // and is closed by the paint loop once it has been drawn.
      decoder.close();
    }
  }
  return createImageBitmap(new Blob([bytes as BlobPart], { type: 'image/jpeg' }));
}

export function FullBrowserView({
  url,
  navSeq,
  onMeta,
}: {
  url: string;
  /** Bumped by the parent to (re)issue navigation to `url` (also covers reload). */
  navSeq: number;
  onMeta: (meta: { url: string; title: string }) => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const [hasFrame, setHasFrame] = useState(false);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const hostRef = useRef<HTMLDivElement>(null);

  // The page-space size the last frame covered — what a click is scaled against.
  // A ref, not state: it's read by event handlers, never rendered.
  const viewportRef = useRef({ width: DEFAULT_VW, height: DEFAULT_VH });

  // Decode pipeline state. `pending` holds at most one undecoded frame (newest
  // wins); `decoding` guards against running two decodes concurrently.
  const pendingRef = useRef<string | null>(null);
  const decodingRef = useRef(false);
  // Decoded-but-not-yet-painted frame, handed to the rAF loop.
  const readyRef = useRef<DecodedFrame | null>(null);
  const rafRef = useRef<number | null>(null);

  // Start the session once; re-navigate whenever the parent bumps navSeq.
  useEffect(() => {
    startSession();
  }, []);

  useEffect(() => {
    if (url) sendInput('navigate', { url });
  }, [url, navSeq]);

  // --- paint loop ----------------------------------------------------------
  // Draw whatever has finished decoding, on the compositor's schedule. Scheduled
  // only when there's something to show, so an idle page costs nothing.
  const paint = useCallback(() => {
    rafRef.current = null;
    const frame = readyRef.current;
    const canvas = canvasRef.current;
    if (!frame) return;
    readyRef.current = null;
    if (canvas) {
      const w = 'displayWidth' in frame ? frame.displayWidth : frame.width;
      const h = 'displayHeight' in frame ? frame.displayHeight : frame.height;
      // Resizing the canvas clears it, so only touch it on a genuine size change.
      if (canvas.width !== w || canvas.height !== h) {
        canvas.width = w;
        canvas.height = h;
      }
      const ctx = canvas.getContext('2d');
      if (ctx) ctx.drawImage(frame, 0, 0);
    }
    frame.close();
  }, []);

  const schedulePaint = useCallback(() => {
    if (rafRef.current == null) rafRef.current = requestAnimationFrame(paint);
  }, [paint]);

  // Drain the pending frame, one decode at a time. Re-entrant by design: a frame
  // that arrived during the last decode is picked up on the way out.
  const drain = useCallback(async () => {
    if (decodingRef.current) return;
    decodingRef.current = true;
    try {
      while (pendingRef.current) {
        const uri = pendingRef.current;
        pendingRef.current = null;
        let decoded: DecodedFrame;
        try {
          decoded = await decodeFrame(uri);
        } catch {
          continue; // a corrupt frame is not fatal; the next one repaints
        }
        // A frame decoded while an earlier one still awaits paint supersedes it —
        // release the old one rather than leaking it.
        readyRef.current?.close();
        readyRef.current = decoded;
        schedulePaint();
      }
    } finally {
      decodingRef.current = false;
    }
  }, [schedulePaint]);

  useEffect(() => {
    const unsubFrames = subscribeFrames((f: BrowserFrame) => {
      const meta = f.metadata;
      if (meta?.deviceWidth && meta.deviceHeight) {
        viewportRef.current = { width: meta.deviceWidth, height: meta.deviceHeight };
      }
      pendingRef.current = f.frame;
      setHasFrame(true);
      setError(null); // a frame means the engine is alive
      onMeta({ url: f.url, title: f.title });
      void drain();
    });
    const unsubErrors = subscribeErrors((err) => setError(err));
    return () => {
      unsubFrames();
      unsubErrors();
    };
  }, [onMeta, drain]);

  // Release GPU memory held by frames that never got painted.
  useEffect(
    () => () => {
      if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
      readyRef.current?.close();
      readyRef.current = null;
    },
    [],
  );

  // --- viewport sync -------------------------------------------------------
  // Drive the real Chromium viewport from the pane's size, so the page lays out for
  // the space it's shown in instead of being scaled from a fixed 1280×800.
  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let last = '';
    const push = () => {
      const rect = host.getBoundingClientRect();
      const w = Math.round(rect.width);
      const h = Math.round(rect.height);
      // A hidden pane measures 0×0 — resizing to that would reflow the page for
      // nothing and come back clamped.
      if (w < 1 || h < 1) return;
      const key = `${w}x${h}`;
      if (key === last) return;
      last = key;
      engine
        .resize(w, h)
        .then((applied) => {
          viewportRef.current = applied;
        })
        .catch(() => {
          // Older backend without the op, or engine still starting: keep the last
          // known viewport — clicks stay usable, just scaled against it.
        });
    };
    const observer = new ResizeObserver(() => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(push, RESIZE_DEBOUNCE_MS);
    });
    observer.observe(host);
    push();
    return () => {
      observer.disconnect();
      if (timer) clearTimeout(timer);
    };
  }, []);

  // Map a client coordinate on the canvas to page space. Scales against the size the
  // *frame* reports, so it stays correct through a resize even before the next frame
  // lands, and while the canvas is letterboxed by `object-fit: contain`.
  const toViewport = (clientX: number, clientY: number): { x: number; y: number } | null => {
    const el = canvasRef.current;
    if (!el) return null;
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return null;
    const { width: vw, height: vh } = viewportRef.current;
    const x = ((clientX - rect.left) / rect.width) * vw;
    const y = ((clientY - rect.top) / rect.height) * vh;
    return { x: Math.max(0, Math.min(vw, x)), y: Math.max(0, Math.min(vh, y)) };
  };

  const onClick = (e: MouseEvent) => {
    const p = toViewport(e.clientX, e.clientY);
    if (p) sendInput('click', p);
    hostRef.current?.focus();
  };

  // `keyboard` capture, not `full`: unmodified keys go to the remote page (this
  // is a browser — `t` must type a `t`), while `mod+`/`alt+` chords still reach
  // the shell so the palette and pane verbs keep working. Escape is the remote
  // page's (dismissing its dialogs), and releasing capture is just a matter of
  // clicking elsewhere, so a hold gesture would be overkill here.
  const capture = useCapture({
    mode: 'keyboard',
    escape: 'passthrough',
    instanceId: useContext(PaneInstanceContext),
    viewId: 'browser.view',
  });

  const onWheel = (e: WheelEvent) => {
    sendInput('scroll', { dx: e.deltaX, dy: e.deltaY });
  };

  const onKeyDown = (e: KeyboardEvent) => {
    if (e.metaKey || e.ctrlKey || e.altKey) return; // leave app shortcuts alone
    if (NAMED_KEYS.has(e.key)) {
      e.preventDefault();
      sendInput('key', { key: e.key });
    } else if (e.key.length === 1) {
      e.preventDefault();
      sendInput('type', { text: e.key });
    }
  };

  return (
    <div
      ref={hostRef}
      tabIndex={0}
      onFocus={capture.request}
      onBlur={capture.release}
      onWheel={onWheel}
      onKeyDown={onKeyDown}
      style={{
        width: '100%',
        height: '100%',
        outline: 'none',
        background: '#111',
        overflow: 'hidden',
        display: 'flex',
        alignItems: 'flex-start',
        justifyContent: 'center',
      }}
    >
      {error ? (
        <div style={{ padding: '2rem', color: 'red', textAlign: 'center', maxWidth: '80%' }}>
          <h4 style={{ margin: '0 0 1rem 0' }}>Browser Engine Error</h4>
          <p style={{ fontSize: '0.9rem', color: 'var(--text-dim)', wordBreak: 'break-word' }}>
            {error}
          </p>
        </div>
      ) : (
        <>
          <canvas
            ref={canvasRef}
            onClick={onClick}
            style={{
              display: hasFrame ? 'block' : 'none',
              width: '100%',
              height: '100%',
              objectFit: 'contain',
              cursor: 'default',
              userSelect: 'none',
            }}
          />
          {!hasFrame && (
            <div style={{ padding: '2rem', color: 'var(--text-dim)' }}>
              Starting browser engine…
            </div>
          )}
        </>
      )}
    </div>
  );
}
