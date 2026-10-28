/**
 * Install a downloaded Jacobian / J++ lens for a local GGUF. The lens slot is per
 * GGUF file, and only GGUFs the lens matches are offered: a lens file states no
 * width, so an unmatched model is merely not contradicted, not a fit.
 */
import { useEffect, useState, type CSSProperties } from 'react';

import { revealSection } from '../../../layout/controller';
import { setSubSection } from '../SubSections';
import { SUITE_SECTIONS, SUITE_VIEW } from '../ids';
import { S } from '../styles';
import { getLensTargets, installLens, type LensTarget } from './api';

export function LensInstall({ artifactId }: { artifactId: string }) {
  const [targets, setTargets] = useState<LensTarget[] | null>(null);
  const [unmatched, setUnmatched] = useState(0);
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const load = () =>
    getLensTargets(artifactId)
      .then((r) => {
        setTargets(r.targets);
        setUnmatched(r.unmatched);
      })
      .catch((err: unknown) => setNote(err instanceof Error ? err.message : String(err)));

  useEffect(() => {
    void load();
  }, [artifactId]);

  const install = async (t: LensTarget) => {
    setBusy(t.modelPath);
    setNote(null);
    try {
      const r = await installLens(artifactId, t.modelPath);
      setNote(
        r.error ??
          `Installed for ${t.name}: ${r.installed?.map((x) => `${x.lensId} (${x.layers.length} layers)`).join(', ')}`,
      );
      await load();
    } finally {
      setBusy(null);
    }
  };

  return (
    <div style={I.box}>
      <span style={S.heading}>Use as a lens</span>
      {targets === null ? (
        <span style={S.mono}>finding matching GGUFs…</span>
      ) : targets.length === 0 ? (
        <span style={S.mono}>
          No local GGUF matches this lens ({unmatched} checked). Download the model it was fitted on
          in the llama.cpp pane.
        </span>
      ) : (
        targets.map((t) => (
          <div key={t.modelPath} style={I.row}>
            <span style={I.name} title={t.modelPath}>
              {t.name}
            </span>
            <span style={S.mono}>{t.compat.reasons[0] ?? t.compat.state}</span>
            {t.installed.length ? (
              <button
                type="button"
                className="btn-mini"
                onClick={() => {
                  setSubSection(SUITE_SECTIONS.lens, 'lenses');
                  void revealSection(SUITE_SECTIONS.lens, SUITE_VIEW);
                }}
              >
                Compare lenses
              </button>
            ) : (
              <button
                type="button"
                className="btn-mini"
                disabled={busy !== null}
                title="Convert in the worker (torch, weights only) into the llama.cpp lens slot for this GGUF"
                onClick={() => void install(t)}
              >
                {busy === t.modelPath ? 'Installing…' : 'Install'}
              </button>
            )}
          </div>
        ))
      )}
      {targets !== null && targets.length > 0 && unmatched > 0 && (
        <span style={S.mono}>{unmatched} other GGUFs do not match and are not offered.</span>
      )}
      {note && <span style={S.mono}>{note}</span>}
    </div>
  );
}

const I = {
  box: {
    display: 'grid',
    gap: 4,
    paddingTop: 6,
    borderTop: '1px solid var(--border)',
  } satisfies CSSProperties,
  row: {
    display: 'grid',
    gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr) auto',
    gap: 10,
    alignItems: 'center',
  } satisfies CSSProperties,
  name: {
    fontFamily: 'var(--font-mono)',
    fontSize: 12,
    fontWeight: 700,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  } satisfies CSSProperties,
};
