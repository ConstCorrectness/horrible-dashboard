/**
 * Page viewer pane (`research.pageViewer`) — renders a captured self-contained
 * page from the artifact store in a **fully sandboxed** iframe: `sandbox=""`
 * (no scripts, no same-origin) on top of the byte route's `CSP: sandbox` header.
 * The archive is inert by construction — capture strips scripts and the CSP
 * blocks any un-inlined network reference, so what you see is what was saved.
 *
 * Multi-instance; params: `{ artifactId?: string; sourceId?: string }`.
 */
import { useEffect, useState } from 'react';

import { apiGet } from '../../../api';
import { usePaneParams } from '../../../panes';
import { registry } from '../../../registry';
import { toastsStore } from '../../../toasts';
import type { SourceModel } from '../../library/api';
import { artifactMeta, artifactUrl, exportToObsidian, type ArtifactModel } from '../api';

export function PageViewer() {
  const params = usePaneParams();
  const artifactIdParam = typeof params.artifactId === 'string' ? params.artifactId : null;
  const sourceId = typeof params.sourceId === 'string' ? params.sourceId : null;

  const [artifact, setArtifact] = useState<ArtifactModel | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setArtifact(null);
    setError(null);
    (async () => {
      let id = artifactIdParam;
      if (!id && sourceId) {
        const source = await apiGet<SourceModel>(`/library/sources/${sourceId}`);
        if (!source.artifact_id) throw new Error('source has no stored page');
        id = source.artifact_id;
      }
      if (!id) throw new Error('open a captured page from the library');
      const meta = await artifactMeta(id);
      if (!cancelled) setArtifact(meta);
    })().catch((err: unknown) => {
      if (!cancelled) setError(err instanceof Error ? err.message : String(err));
    });
    return () => {
      cancelled = true;
    };
  }, [artifactIdParam, sourceId]);

  const originUrl = artifact?.origin_url ?? null;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '0.5rem',
          padding: '0.25rem 0.5rem',
          borderBottom: '1px solid var(--border)',
          fontSize: '0.75rem',
          minWidth: 0,
        }}
      >
        <span
          style={{
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
            minWidth: 0,
          }}
          title={artifact?.filename}
        >
          {artifact ? String(artifact.meta.title ?? artifact.filename) : error ? '' : 'loading…'}
        </span>
        {originUrl && (
          <button
            onClick={() => registry.openPanel('browser.view', { params: { url: originUrl } })}
            title={`Open the live page: ${originUrl}`}
            style={{ marginLeft: 'auto' }}
          >
            live ↗
          </button>
        )}
        <button
          disabled={!artifact || exporting}
          style={originUrl ? undefined : { marginLeft: 'auto' }}
          onClick={() => {
            if (!artifact) return;
            setExporting(true);
            exportToObsidian(sourceId ? { source_id: sourceId } : { artifact_id: artifact.id })
              .then((res) => toastsStore.add('success', 'Exported to Obsidian', res.note_path))
              .catch((err: unknown) =>
                toastsStore.add(
                  'warning',
                  'Export failed',
                  err instanceof Error ? err.message : String(err),
                ),
              )
              .finally(() => setExporting(false));
          }}
        >
          Obsidian ⬇
        </button>
      </div>
      {error ? (
        <div style={{ padding: '1rem', color: 'var(--text-dim)' }}>{error}</div>
      ) : artifact ? (
        <iframe
          src={artifactUrl(artifact.id)}
          sandbox=""
          referrerPolicy="no-referrer"
          title={artifact.filename}
          style={{ flex: 1, border: 0, background: 'white' }}
        />
      ) : (
        <div style={{ flex: 1 }} />
      )}
    </div>
  );
}
