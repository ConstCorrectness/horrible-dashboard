/**
 * Full-engine viewport: renders the backend's server-rendered Chromium frame in an
 * `<img>` and relays pointer/keyboard input back over the `browser` `/ws` channel
 * (the vizdoom/visualizer pattern). Coordinates are scaled from the displayed image
 * to the real 1280×800 viewport the backend drives. Navigation/back/forward/reload are
 * issued by the parent toolbar through the engine ops; this component owns only the
 * live frame + input capture, and reports the live URL/title upward via `onMeta`.
 */
import {
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
  sendInput,
  startSession,
  subscribeFrames,
  subscribeErrors,
  type BrowserFrame,
} from '../session';

// Must match _VIEWPORT in backend/modules/browser/session.py.
const VW = 1280;
const VH = 800;

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
  const [frame, setFrame] = useState<BrowserFrame | null>(null);
  const [error, setError] = useState<string | null>(null);
  const imgRef = useRef<HTMLImageElement>(null);

  // Start the session once; re-navigate whenever the parent bumps navSeq.
  useEffect(() => {
    startSession();
  }, []);

  useEffect(() => {
    if (url) sendInput('navigate', { url });
  }, [url, navSeq]);

  useEffect(() => {
    const unsubFrames = subscribeFrames((f) => {
      setFrame(f);
      setError(null); // Clear error if we got a frame
      onMeta({ url: f.url, title: f.title });
    });
    const unsubErrors = subscribeErrors((err) => {
      setError(err);
    });
    return () => {
      unsubFrames();
      unsubErrors();
    };
  }, [onMeta]);

  // Map a client coordinate on the <img> to the backend viewport space.
  const toViewport = (clientX: number, clientY: number): { x: number; y: number } | null => {
    const el = imgRef.current;
    if (!el) return null;
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return null;
    const x = ((clientX - rect.left) / rect.width) * VW;
    const y = ((clientY - rect.top) / rect.height) * VH;
    return { x: Math.max(0, Math.min(VW, x)), y: Math.max(0, Math.min(VH, y)) };
  };

  const onClick = (e: MouseEvent) => {
    const p = toViewport(e.clientX, e.clientY);
    if (p) sendInput('click', p);
    imgRef.current?.parentElement?.focus();
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
      ) : frame ? (
        <img
          ref={imgRef}
          src={frame.frame}
          alt={frame.title || 'page'}
          draggable={false}
          onClick={onClick}
          style={{
            width: '100%',
            height: 'auto',
            maxHeight: '100%',
            objectFit: 'contain',
            cursor: 'default',
            userSelect: 'none',
          }}
        />
      ) : (
        <div style={{ padding: '2rem', color: 'var(--text-dim)' }}>Starting browser engine…</div>
      )}
    </div>
  );
}
