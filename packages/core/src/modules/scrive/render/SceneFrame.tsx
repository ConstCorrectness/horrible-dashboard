/**
 * An `{r3f}` scene: a React Three Fiber component from the site's `scenes/`, run in a
 * sandbox.
 *
 * The frame is a `srcdoc` iframe with `sandbox="allow-scripts"` and nothing else, so
 * it has an opaque origin: scene code — often agent-written — cannot read the app,
 * its storage, its cookies or its session. Its CSP is `default-src 'none'` plus the
 * inline bootstrap and eval: **the frame loads nothing over the network.**
 *
 * Everything it runs is posted in by the page: first the runtime itself
 * (`/api/scrive/runtime.js`, fetched once per session and kept), which the bootstrap
 * evaluates, then the scene's source. Loading the runtime with a `<script src>` was
 * the first design; some embedders (the app's own preview browser among them) refuse
 * every subresource request from an opaque origin, and a sandbox that only works in
 * some browsers is not one to rely on. Protocol: packages/scrive-runtime/src/runtime.tsx.
 *
 * `:params:` is a JSON object of tweakable values — a number, a boolean, a colour
 * (`"#rrggbb"`), or `{ "value": 1, "min": 0, "max": 5, "step": 0.1 }`. The tweaks
 * panel re-renders the scene live without reloading it; tweaks are not written back.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { apiUrl } from '../../../origin';
import { assetUrl } from './directives';

type ParamSpec =
  | number
  | boolean
  | string
  | { value: number; min?: number; max?: number; step?: number };
type Status = { kind: 'loading' } | { kind: 'ready' } | { kind: 'error'; message: string };

/**
 * The frame's document: an empty stage and a bootstrap that waits for the runtime.
 * Nothing in it is page-supplied, so the inline script is ours alone.
 */
export function sceneSrcdoc(colorScheme = 'normal'): string {
  const csp = [
    "default-src 'none'",
    "script-src 'unsafe-inline' 'unsafe-eval'",
    "style-src 'unsafe-inline'",
    'img-src data: blob:',
    'media-src blob:',
    'worker-src blob:',
  ].join('; ');
  const bootstrap = [
    'addEventListener("message", function boot(e) {',
    '  if (e.source !== parent || !e.data || e.data.scrive !== 1 || e.data.type !== "runtime") return;',
    '  removeEventListener("message", boot);',
    '  (0, eval)(e.data.code);',
    '});',
    'parent.postMessage({ scrive: 1, type: "frame" }, "*");',
  ].join('');
  return [
    '<!doctype html><html><head><meta charset="utf-8">',
    // The page's own colour scheme: a frame whose scheme differs from its embedder's
    // is painted on an opaque backdrop (white, under a dark theme) instead of
    // showing the page through its transparent canvas.
    `<meta name="color-scheme" content="${colorScheme.replace(/[^a-z ]/g, '') || 'normal'}">`,
    `<meta http-equiv="Content-Security-Policy" content="${csp}">`,
    '<style>html,body,#scene{margin:0;width:100%;height:100%;overflow:hidden;background:transparent}</style>',
    `</head><body><div id="scene"></div><script>${bootstrap}</script></body></html>`,
  ].join('');
}

/** The runtime's source, fetched once per session for every scene on every page. */
let runtimeSource: Promise<string> | null = null;
function loadRuntime(): Promise<string> {
  runtimeSource ??= fetch(apiUrl('/api/scrive/runtime.js')).then((res) => {
    if (!res.ok) throw new Error(`scene runtime: ${res.status}`);
    return res.text();
  });
  // A failed fetch is retried by the next scene rather than remembered.
  runtimeSource.catch(() => (runtimeSource = null));
  return runtimeSource;
}

/** `:params:` → its spec (`{}` when absent; an error string when malformed). */
export function parseParams(raw: unknown): Record<string, ParamSpec> | string {
  if (raw == null || raw === '') return {};
  try {
    const value = JSON.parse(String(raw)) as unknown;
    return value && typeof value === 'object' && !Array.isArray(value)
      ? (value as Record<string, ParamSpec>)
      : ':params: must be a JSON object';
  } catch (err) {
    return `:params: is not JSON — ${err instanceof Error ? err.message : String(err)}`;
  }
}

/** The values a scene receives: a range spec contributes its `value`. */
export function paramValues(spec: Record<string, ParamSpec>): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(spec).map(([key, s]) => [key, typeof s === 'object' && s ? s.value : s]),
  );
}

export interface SceneFrameProps {
  site: string;
  pagePath: string;
  /** The scene file, relative to the page (or `/`-rooted), as written in `{r3f}`. */
  src: string;
  height: number;
  /** Raw `:params:` text. */
  params?: unknown;
  /** Show the tweak / poster / record tools (Write and Preview, not a published page). */
  tools?: boolean;
  onPoster?: (dataUrl: string) => void;
  onRecorded?: (blob: Blob) => void;
}

export function SceneFrame({
  site,
  pagePath,
  src,
  height,
  params,
  tools = true,
  onPoster,
  onRecorded,
}: SceneFrameProps) {
  const frameRef = useRef<HTMLIFrameElement>(null);
  const [status, setStatus] = useState<Status>({ kind: 'loading' });
  const [busy, setBusy] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const spec = useMemo(() => parseParams(params), [params]);
  const [values, setValues] = useState<Record<string, unknown>>(() =>
    typeof spec === 'string' ? {} : paramValues(spec),
  );
  const valuesRef = useRef(values);
  valuesRef.current = values;
  const srcdoc = useMemo(
    () =>
      sceneSrcdoc(
        typeof document === 'undefined'
          ? 'normal'
          : getComputedStyle(document.documentElement).colorScheme,
      ),
    [],
  );
  const sceneUrl = assetUrl(site, pagePath, src);

  const send = useCallback((type: string, data: Record<string, unknown> = {}) => {
    frameRef.current?.contentWindow?.postMessage({ scrive: 1, type, ...data }, '*');
  }, []);

  // A changed :params: (an edit in Source) resets the tweaks to it.
  useEffect(() => {
    if (typeof spec !== 'string') setValues(paramValues(spec));
  }, [spec]);

  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      if (event.source !== frameRef.current?.contentWindow) return;
      const msg = event.data as { scrive?: number; type?: string; [k: string]: unknown };
      if (msg?.scrive !== 1) return;
      switch (msg.type) {
        case 'frame':
          loadRuntime()
            .then((code) => send('runtime', { code }))
            .catch((err: Error) => setStatus({ kind: 'error', message: err.message }));
          break;
        case 'booted':
          setStatus({ kind: 'loading' });
          fetch(sceneUrl)
            .then((res) => {
              if (!res.ok)
                throw new Error(`${src}: ${res.status === 404 ? 'not found' : res.status}`);
              return res.text();
            })
            .then((source) => send('load', { source, params: valuesRef.current }))
            .catch((err: Error) => setStatus({ kind: 'error', message: err.message }));
          break;
        case 'ready':
          setStatus({ kind: 'ready' });
          break;
        case 'error':
          setStatus({ kind: 'error', message: String(msg.message ?? 'scene failed') });
          setBusy(null);
          break;
        case 'poster':
          setBusy(null);
          onPoster?.(String(msg.dataUrl));
          break;
        case 'recorded':
          setBusy(null);
          if (msg.blob instanceof Blob) onRecorded?.(msg.blob);
          break;
      }
    };
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [sceneUrl, src, send, onPoster, onRecorded]);

  const setValue = (key: string, value: unknown) => {
    const next = { ...valuesRef.current, [key]: value };
    setValues(next);
    send('params', { params: next });
  };

  return (
    <figure className="scrive-scene" data-status={status.kind}>
      <iframe
        ref={frameRef}
        title={`3D scene ${src}`}
        // Opaque origin: no allow-same-origin, ever. See the module comment.
        sandbox="allow-scripts"
        srcDoc={srcdoc}
        style={{ height }}
      />
      <figcaption className="scrive-scene-bar">
        <span className="scrive-scene-label">
          3D scene · <code>{src}</code>
        </span>
        <span className="scrive-scene-status" role="status">
          {status.kind === 'error' ? status.message : status.kind === 'loading' ? 'loading' : ''}
          {busy && ` ${busy}`}
        </span>
        {tools && status.kind === 'ready' && (
          <span className="scrive-scene-tools">
            {typeof spec !== 'string' && Object.keys(spec).length > 0 && (
              <button
                type="button"
                className="scrive-cell-run"
                aria-expanded={open}
                onClick={() => setOpen((o) => !o)}
              >
                Tweaks
              </button>
            )}
            {onPoster && (
              <button
                type="button"
                className="scrive-cell-run"
                title="Save the current frame as the scene's poster image"
                disabled={busy !== null}
                onClick={() => {
                  setBusy('capturing');
                  send('poster');
                }}
              >
                Poster
              </button>
            )}
            {onRecorded && (
              <button
                type="button"
                className="scrive-cell-run"
                title="Record five seconds of the scene as a WebM video"
                disabled={busy !== null}
                onClick={() => {
                  setBusy('recording 5s');
                  send('record', { seconds: 5, fps: 30 });
                }}
              >
                Record
              </button>
            )}
          </span>
        )}
      </figcaption>
      {typeof spec === 'string' && <div className="scrive-scene-error">{spec}</div>}
      {open && typeof spec !== 'string' && (
        <div className="scrive-scene-tweaks">
          {Object.entries(spec).map(([key, s]) => (
            <Tweak
              key={key}
              name={key}
              spec={s}
              value={values[key]}
              onChange={(v) => setValue(key, v)}
            />
          ))}
        </div>
      )}
    </figure>
  );
}

function Tweak({
  name,
  spec,
  value,
  onChange,
}: {
  name: string;
  spec: ParamSpec;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  if (typeof spec === 'boolean') {
    return (
      <label className="scrive-scene-tweak">
        <span>{name}</span>
        <input
          type="checkbox"
          checked={value === true}
          onChange={(e) => onChange(e.target.checked)}
        />
      </label>
    );
  }
  if (typeof spec === 'string') {
    const color = /^#[0-9a-f]{6}$/i.test(spec);
    return (
      <label className="scrive-scene-tweak">
        <span>{name}</span>
        <input
          type={color ? 'color' : 'text'}
          value={String(value ?? '')}
          onChange={(e) => onChange(e.target.value)}
          style={color ? undefined : { padding: '0 0.6rem' }}
        />
      </label>
    );
  }
  const base = typeof spec === 'number' ? spec : spec.value;
  const min = typeof spec === 'object' ? (spec.min ?? 0) : Math.min(0, base * 2);
  const max = typeof spec === 'object' ? (spec.max ?? base * 2) || 1 : Math.max(base * 2, 1);
  const step =
    typeof spec === 'object' && spec.step
      ? spec.step
      : Number.isInteger(base)
        ? 1
        : (max - min) / 100;
  return (
    <label className="scrive-scene-tweak">
      <span>{name}</span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={Number(value ?? base)}
        onChange={(e) => onChange(Number(e.target.value))}
      />
      <span className="scrive-meta">{Number(value ?? base)}</span>
    </label>
  );
}
