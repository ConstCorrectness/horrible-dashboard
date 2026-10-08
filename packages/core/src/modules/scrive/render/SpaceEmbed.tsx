/**
 * A Hugging Face Space in a page (`{space}`; see `space.ts`).
 *
 * Never mounted on arrival: many Spaces start a multi-gigabyte model download the
 * moment they load (the WebGPU ones always do), so the frame waits behind a
 * `<details>` the reader opens. In the app the iframe is only created once it is
 * opened; a published page has no script, so it ships the iframe inside the closed
 * `<details>` with `loading="lazy"` — the browser does not load what it does not
 * render.
 */
import { useEffect, useState } from 'react';

import {
  fetchSpaceInfo,
  parseSpaceRef,
  spaceEmbedUrl,
  spacePageUrl,
  type SpaceInfo,
} from './space';

export interface SpaceEmbedProps {
  arg: unknown;
  options: Record<string, unknown>;
  /** A published page: no Hub API call, and the frame is in the markup up front. */
  published?: boolean;
}

export function SpaceEmbed({ arg, options, published = false }: SpaceEmbedProps) {
  const ref = parseSpaceRef(arg);
  const [info, setInfo] = useState<SpaceInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (published || !ref) return;
    let live = true;
    fetchSpaceInfo(ref.id).then(
      (i) => live && setInfo(i),
      (e: unknown) => live && setError(e instanceof Error ? e.message : String(e)),
    );
    return () => {
      live = false;
    };
  }, [published, ref?.id]);

  if (!ref) {
    return (
      <figure className="scrive-space" data-status="error">
        <figcaption className="scrive-space-bar">
          <span className="scrive-space-error">
            {'{space}'} needs a Space id (owner/name) or its huggingface.co URL — got{' '}
            {JSON.stringify(String(arg ?? ''))}
          </span>
        </figcaption>
      </figure>
    );
  }

  const host = spaceEmbedUrl(options.host) ?? info?.host ?? null;
  const height = Number(options.height) || 640;
  const title = info ? `${info.emoji ? `${info.emoji} ` : ''}${info.title}` : ref.id;

  return (
    <figure className="scrive-space" data-status={error ? 'error' : 'ready'}>
      <figcaption className="scrive-space-bar">
        <span className="scrive-space-title">{title}</span>
        <a
          className="scrive-space-link"
          href={spacePageUrl(ref.id)}
          target="_blank"
          rel="noreferrer"
        >
          huggingface.co/spaces/{ref.id}
        </a>
        {info && (
          <span className="scrive-space-meta">
            {[info.sdk, info.stage.toLowerCase(), info.likes ? `${info.likes} likes` : '']
              .filter(Boolean)
              .join(' · ')}
          </span>
        )}
      </figcaption>
      {error && !host && <div className="scrive-space-error">{error}</div>}
      {host ? (
        <details className="scrive-run" onToggle={(e) => setOpen(e.currentTarget.open)}>
          <summary>
            Run it here
            <span className="scrive-space-note">
              {' '}
              — the Space runs in your browser and may download model weights first
            </span>
          </summary>
          {(open || published) && (
            <iframe
              src={host}
              title={`Hugging Face Space ${ref.id}`}
              loading="lazy"
              style={{ height }}
              referrerPolicy="strict-origin-when-cross-origin"
              // Its own origin (hf.space), never ours: scripts and storage there are
              // what a WebGPU Space needs to run and to cache its weights.
              sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox allow-downloads"
              allow="cross-origin-isolated; fullscreen; clipboard-write"
            />
          )}
        </details>
      ) : (
        !error && <div className="scrive-space-note scrive-space-pad">Looking up the Space…</div>
      )}
    </figure>
  );
}
