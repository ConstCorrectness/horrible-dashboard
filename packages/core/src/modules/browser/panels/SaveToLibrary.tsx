/**
 * "Save to Library" popover — the human-facing half of the capture path the agent
 * drives with `browser.save` (both go through `../capture`, so a page saved by hand
 * and one saved by the agent are the same source).
 *
 * The media list deliberately shows *undescribed* assets greyed out rather than
 * hiding them: an image with no alt text can't be embedded (the app's embedder is
 * text-only), and silently dropping it would look like a bug. Showing it, disabled,
 * with the reason, is the honest version.
 */
import { useCallback, useEffect, useState } from 'react';

import { clipStatus } from '../../library/api';
import { captureAllMedia, capturePage, captureMedia, isDescribed, pageMedia } from '../capture';
import type { MediaItem, PageMedia } from '../session';

type Status =
  | { kind: 'idle' }
  | { kind: 'busy' }
  | { kind: 'done'; msg: string }
  | { kind: 'error'; msg: string };

const row: React.CSSProperties = {
  display: 'flex',
  alignItems: 'center',
  gap: '0.5rem',
  padding: '0.3rem 0.4rem',
  borderRadius: 4,
};

export function SaveToLibrary({ library, onClose }: { library: string; onClose: () => void }) {
  const [media, setMedia] = useState<PageMedia | null>(null);
  const [status, setStatus] = useState<Status>({ kind: 'idle' });
  const [saved, setSaved] = useState<ReadonlySet<string>>(new Set());
  // With CLIP on, an undescribed image is indexable by appearance, so the
  // "no description" block lifts entirely.
  const [visual, setVisual] = useState(false);

  useEffect(() => {
    pageMedia()
      .then(setMedia)
      .catch((e: Error) => setStatus({ kind: 'error', msg: e.message }));
    clipStatus()
      .then((s) => setVisual(s.enabled))
      .catch(() => setVisual(false));
  }, []);

  const run = useCallback(async (fn: () => Promise<string>) => {
    setStatus({ kind: 'busy' });
    try {
      setStatus({ kind: 'done', msg: await fn() });
    } catch (e) {
      setStatus({ kind: 'error', msg: (e as Error).message });
    }
  }, []);

  const savePage = () =>
    run(async () => {
      const s = await capturePage({ library });
      return `Saved “${s.title}” — embedding in the background.`;
    });

  const saveAll = () =>
    run(async () => {
      const { saved: list, skipped } = await captureAllMedia({ library });
      setSaved((prev) => new Set([...prev, ...list.map((s) => s.asset?.src ?? '')]));
      return skipped
        ? `Saved ${list.length}; skipped ${skipped} with no description.`
        : `Saved ${list.length} item(s).`;
    });

  const saveOne = (item: MediaItem) =>
    run(async () => {
      const s = await captureMedia(item, media!.url, { library });
      setSaved((prev) => new Set([...prev, item.src]));
      return `Saved “${s.title}”.`;
    });

  const items = media ? [...media.images, ...media.videos] : [];
  const savable = (item: MediaItem) => visual || isDescribed(item);
  const savableCount = items.filter(savable).length;
  const busy = status.kind === 'busy';

  return (
    <div
      style={{
        position: 'absolute',
        top: '2.4rem',
        right: '0.5rem',
        zIndex: 20,
        width: 380,
        maxHeight: '70%',
        display: 'flex',
        flexDirection: 'column',
        background: 'var(--bg-elevated, #24242a)',
        border: '1px solid var(--border)',
        borderRadius: 6,
        boxShadow: '0 8px 24px rgb(0 0 0 / 45%)',
      }}
    >
      <div
        style={{
          ...row,
          justifyContent: 'space-between',
          borderBottom: '1px solid var(--border)',
        }}
      >
        <strong style={{ fontSize: '0.85rem' }}>Save to “{library}”</strong>
        <button
          type="button"
          onClick={onClose}
          style={{ background: 'none', border: 'none', cursor: 'pointer' }}
        >
          ×
        </button>
      </div>

      <div style={{ ...row, gap: '0.4rem', borderBottom: '1px solid var(--border)' }}>
        <button type="button" onClick={savePage} disabled={busy}>
          Save page text
        </button>
        <button type="button" onClick={saveAll} disabled={busy || savableCount === 0}>
          Save all media ({savableCount})
        </button>
        {visual && (
          <span style={{ fontSize: '0.68rem', color: 'var(--text-dim)' }} title="CLIP is on">
            👁 visual
          </span>
        )}
      </div>

      {status.kind !== 'idle' && status.kind !== 'busy' && (
        <div
          style={{
            ...row,
            fontSize: '0.75rem',
            color: status.kind === 'error' ? '#f08c8c' : '#2ed573',
          }}
        >
          {status.msg}
        </div>
      )}

      <div style={{ overflowY: 'auto', padding: '0.2rem' }}>
        {media == null && <div className="dashboard-hint">Reading page media…</div>}
        {media != null && items.length === 0 && (
          <div className="dashboard-hint">No images or videos on this page.</div>
        )}
        {items.map((item) => {
          const described = isDescribed(item);
          const canSave = savable(item);
          const already = saved.has(item.src);
          const label = item.alt || item.context?.[0] || item.title;
          return (
            <div key={item.src} style={{ ...row, opacity: canSave ? 1 : 0.5 }}>
              {item.kind === 'image' ? (
                <img
                  src={item.src}
                  alt=""
                  style={{
                    width: 44,
                    height: 44,
                    objectFit: 'cover',
                    borderRadius: 3,
                    flex: '0 0 auto',
                  }}
                />
              ) : (
                <div style={{ width: 44, textAlign: 'center', flex: '0 0 auto' }}>▶</div>
              )}
              <div style={{ minWidth: 0, flex: 1 }}>
                <div
                  style={{
                    fontSize: '0.75rem',
                    whiteSpace: 'nowrap',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                  }}
                  title={label || item.src}
                >
                  {label || <em style={{ color: 'var(--text-dim)' }}>no description</em>}
                </div>
                <div style={{ fontSize: '0.68rem', color: 'var(--text-dim)' }}>
                  {canSave
                    ? [
                        item.kind,
                        item.width && item.height ? `${item.width}×${item.height}` : null,
                        !described ? 'indexed by appearance' : null,
                      ]
                        .filter(Boolean)
                        .join(' · ')
                    : 'No alt text or caption — nothing to embed'}
                </div>
              </div>
              <button
                type="button"
                disabled={!canSave || busy || already}
                title={
                  canSave
                    ? 'Save to library'
                    : 'No describing text to embed. Enable CLIP visual search to index it by appearance.'
                }
                onClick={() => saveOne(item)}
              >
                {already ? '✓' : 'Save'}
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}
