/**
 * A web app from the site (`{app} name` → `apps/<name>/`), running on the apps
 * origin the backend serves (`http://scrive-apps.localhost:<port>`; see
 * backend/modules/scrive/apps.py).
 *
 * Not the `{r3f}` sandbox: an app is a whole website and the ones worth embedding
 * keep model weights in Cache Storage, which an opaque origin throws on. A separate
 * real origin gives it storage and WebGPU while still sharing nothing with the app.
 *
 * It reloads when any of its files changes on disk (`app.changed` from the watcher),
 * and the console shim the apps origin injects posts its errors here, where they show
 * under the frame — its own DevTools console belongs to a cross-origin frame.
 */
import { useEffect, useRef, useState } from 'react';

import { openExternal } from '../../../external';
import { subscribeChannel } from '../../../ws';
import { getAppsOrigin, SCRIVE_CHANNEL, type AppsOrigin } from '../api';

export interface ConsoleLine {
  level: 'error' | 'warn' | 'log' | 'info';
  text: string;
}

/** `name`, `apps/name`, `/apps/name/` → the app name; null when it is not one. */
export function parseAppRef(arg: unknown): string | null {
  const raw =
    typeof arg === 'string'
      ? arg
          .trim()
          .replace(/^\/?apps\//, '')
          .replace(/\/+$/, '')
      : '';
  return /^[A-Za-z0-9][\w.-]{0,63}$/.test(raw) ? raw : null;
}

export function useAppsOrigin(): AppsOrigin | null {
  const [origin, setOrigin] = useState<AppsOrigin | null>(null);
  useEffect(() => {
    let live = true;
    getAppsOrigin().then(
      (o) => live && setOrigin(o),
      (e: unknown) =>
        live && setOrigin({ origin: null, reason: e instanceof Error ? e.message : String(e) }),
    );
    return () => {
      live = false;
    };
  }, []);
  return origin;
}

/** The app's URL on the apps origin, or null. */
export function appUrl(
  origin: string | null,
  site: string,
  name: string,
  version = 0,
): string | null {
  return origin
    ? `${origin}/${encodeURIComponent(site)}/${encodeURIComponent(name)}/?v=${version}`
    : null;
}

/**
 * A frame running one app, plus what it says on its console. Shared by the `{app}`
 * block and the app preview pane, which sets `width` for its viewport presets.
 */
export function useAppFrame(site: string, name: string) {
  const origin = useAppsOrigin();
  const frame = useRef<HTMLIFrameElement>(null);
  const [version, setVersion] = useState(0);
  const [lines, setLines] = useState<ConsoleLine[]>([]);

  useEffect(
    () =>
      subscribeChannel(SCRIVE_CHANNEL, (msg) => {
        if (msg.event !== 'app.changed') return;
        const data = (msg.data ?? {}) as { site?: string; app?: string };
        if (data.site === site && data.app === name) setVersion((v) => v + 1);
      }),
    [site, name],
  );

  useEffect(() => {
    const onMessage = (e: MessageEvent) => {
      if (!frame.current || e.source !== frame.current.contentWindow) return;
      const data = e.data as { scriveApp?: number; level?: string; text?: unknown };
      if (data?.scriveApp !== 1) return;
      const level =
        (['error', 'warn', 'log', 'info'] as const).find((l) => l === data.level) ?? 'log';
      setLines((prev) => [...prev.slice(-199), { level, text: String(data.text ?? '') }]);
    };
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, []);

  // A reload starts a fresh console.
  useEffect(() => setLines([]), [version]);

  return {
    origin,
    frame,
    url: appUrl(origin?.origin ?? null, site, name, version),
    lines,
    reload: () => setVersion((v) => v + 1),
    clear: () => setLines([]),
  };
}

export interface AppFrameProps {
  site: string;
  name: unknown;
  height: number;
  /** Open the app preview pane (Write and Preview offer it). */
  onPreview?: () => void;
}

export function AppFrame({ site, name: arg, height, onPreview }: AppFrameProps) {
  const name = parseAppRef(arg);
  const app = useAppFrame(site, name ?? '');
  const [showConsole, setShowConsole] = useState(false);
  const errors = app.lines.filter((l) => l.level === 'error').length;

  if (!name) {
    return (
      <figure className="scrive-app" data-status="error">
        <figcaption className="scrive-space-bar">
          <span className="scrive-space-error">
            {'{app}'} needs the name of a folder in apps/ — got {JSON.stringify(String(arg ?? ''))}
          </span>
        </figcaption>
      </figure>
    );
  }

  return (
    <figure className="scrive-app" data-status={errors ? 'error' : 'ready'}>
      <figcaption className="scrive-space-bar">
        <span className="scrive-space-title">apps/{name}</span>
        <span className="scrive-space-meta">
          {app.origin === null
            ? 'starting…'
            : app.origin.origin
              ? 'own origin · live reload'
              : 'no preview here'}
        </span>
        <span className="scrive-app-tools">
          <button
            type="button"
            className="scrive-app-tool"
            onClick={app.reload}
            disabled={!app.url}
          >
            Reload
          </button>
          {onPreview && (
            <button type="button" className="scrive-app-tool" onClick={onPreview}>
              Preview pane
            </button>
          )}
          <button
            type="button"
            className="scrive-app-tool"
            onClick={() => app.url && void openExternal(app.url)}
            disabled={!app.url}
          >
            Open in browser
          </button>
          <button
            type="button"
            className={`scrive-app-tool${errors ? ' is-bad' : ''}`}
            onClick={() => setShowConsole((v) => !v)}
            aria-expanded={showConsole}
          >
            Console{app.lines.length ? ` · ${app.lines.length}` : ''}
            {errors ? ` · ${errors} error${errors === 1 ? '' : 's'}` : ''}
          </button>
        </span>
      </figcaption>
      {app.url ? (
        <iframe
          ref={app.frame}
          src={app.url}
          title={`Web app ${name}`}
          style={{ height }}
          // Its own origin (scrive-apps.localhost), never the app UI's: same-origin
          // here is the app's own storage, which is the point.
          sandbox="allow-scripts allow-same-origin allow-forms allow-downloads allow-popups allow-modals"
          allow="cross-origin-isolated; fullscreen; clipboard-write"
        />
      ) : (
        app.origin && <div className="scrive-space-note scrive-space-pad">{app.origin.reason}</div>
      )}
      {showConsole && <ConsoleList lines={app.lines} onClear={app.clear} />}
    </figure>
  );
}

export function ConsoleList({ lines, onClear }: { lines: ConsoleLine[]; onClear: () => void }) {
  return (
    <div className="scrive-app-console" role="log" aria-label="App console">
      {lines.length === 0 ? (
        <div className="scrive-space-note">Nothing logged yet.</div>
      ) : (
        lines.map((l, i) => (
          <div key={i} className={`scrive-app-line is-${l.level}`}>
            <span className="scrive-app-level">{l.level}</span>
            <span>{l.text}</span>
          </div>
        ))
      )}
      {lines.length > 0 && (
        <button type="button" className="scrive-app-tool" onClick={onClear}>
          Clear
        </button>
      )}
    </div>
  );
}
