import { useEffect, useState, type CSSProperties } from 'react';

import { fetchSampleObservation, type SampleObservation } from '../games-api';

/**
 * **Observation inspector** — shows a real opening position for the selected game so
 * a player can see *what they're programming against* before writing a line: the
 * per-seat observation and the legal actions the engine hands their policy each turn.
 *
 * It's the answer to "what is the observation / what are the legal moves?". Fed by the
 * cheap `GET /games/sample-observation` (no loadout, no model, no agent run — unlike the
 * dry-run's full loop), so resampling by seed is instant. For `frames` games (ViZDoom)
 * it renders the base64 frame as an image and pulls out the HUD; otherwise it shows the
 * observation JSON. See docs/modules/games.mdx (build your agent).
 */

const dim: CSSProperties = { color: 'var(--text-dim)', fontSize: '0.72rem' };
const mono: CSSProperties = { fontFamily: 'var(--font-mono, monospace)' };

/** Keys rendered specially (image / chips) and so dropped from the raw JSON dump:
 * a base64 frame is thousands of unreadable chars, and legal actions show as chips. */
const DROP_FROM_JSON = new Set(['frame', 'frames', 'legal_actions']);

function isDataUri(v: unknown): v is string {
  return typeof v === 'string' && v.startsWith('data:image');
}

export function ObservationInspector({
  gameId,
  obsKind,
}: {
  gameId: string;
  /** From the catalog entry — `frames` renders the observation as an image. */
  obsKind?: 'json' | 'frames';
}) {
  const [seed, setSeed] = useState(0);
  const [sample, setSample] = useState<SampleObservation | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchSampleObservation(gameId, seed)
      .then((s) => !cancelled && setSample(s))
      .catch(
        (e: Error) =>
          !cancelled &&
          setSample({
            ok: false,
            error: String(e.message || e),
            game_id: gameId,
            observation: {},
            legal_actions: [],
          }),
      )
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [gameId, seed]);

  const obs = sample?.observation ?? {};
  // A `frames` game's image(s): the per-seat `frame`, or the first of a `frames` array.
  const frame = isDataUri(obs.frame)
    ? obs.frame
    : Array.isArray(obs.frames) && isDataUri(obs.frames[0])
      ? (obs.frames[0] as string)
      : null;
  const hud = obs.hud;
  // The observation minus the bulky image fields, for a readable JSON view.
  const lean = Object.fromEntries(Object.entries(obs).filter(([k]) => !DROP_FROM_JSON.has(k)));

  return (
    <div
      style={{
        border: '1px solid var(--border)',
        borderRadius: 8,
        padding: '0.6rem 0.7rem',
        display: 'flex',
        flexDirection: 'column',
        gap: '0.45rem',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
        <strong style={{ fontSize: '0.82rem' }}>👁 Observation</strong>
        <span style={dim}>what the engine hands your policy each turn</span>
        <button
          type="button"
          onClick={() => setSeed((s) => s + 1)}
          disabled={loading}
          style={{ ...mono, marginLeft: 'auto', fontSize: '0.72rem', cursor: 'pointer' }}
          title="Sample another opening position"
        >
          {loading ? '…' : '⟳ resample'}
        </button>
      </div>

      {sample && !sample.ok && (
        <div style={{ color: '#e5a13f', fontSize: '0.75rem' }}>
          ⚠ {sample.error ?? 'could not sample a position'}
          {obsKind === 'frames' && (
            <div style={{ ...dim, marginTop: 4 }}>
              This game renders frames — its engine needs the native extra installed (`uv sync
              --extra games-native`).
            </div>
          )}
        </div>
      )}

      {/* Legal actions the policy must choose among. */}
      {sample?.legal_actions && sample.legal_actions.length > 0 && (
        <div>
          <div style={{ ...dim, marginBottom: 3 }}>
            legal actions · {sample.legal_actions.length}
          </div>
          <div style={{ display: 'flex', gap: '0.3rem', flexWrap: 'wrap' }}>
            {sample.legal_actions.map((a) => (
              <span key={a.id} className="games-tier-chip" title={a.id}>
                {a.label ?? a.id}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* The observation body: image for frames games, JSON otherwise. */}
      {frame && (
        <img
          src={frame}
          alt="sample frame"
          style={{
            width: '100%',
            maxWidth: 320,
            imageRendering: 'pixelated',
            borderRadius: 4,
            border: '1px solid var(--border)',
          }}
        />
      )}
      {hud != null && (
        <div style={{ ...mono, ...dim, fontSize: '0.72rem' }}>
          hud: {typeof hud === 'object' ? JSON.stringify(hud) : String(hud)}
          {typeof obs.tick === 'number' ? ` · tick ${obs.tick}` : ''}
        </div>
      )}
      {sample?.ok && Object.keys(lean).length > 0 && (
        <details {...(obsKind === 'frames' ? {} : { open: true })}>
          <summary style={dim}>observation{frame ? ' (fields)' : ''}</summary>
          <pre
            style={{
              fontSize: '0.7rem',
              maxHeight: '12rem',
              overflow: 'auto',
              margin: '0.3rem 0 0',
            }}
          >
            {JSON.stringify(lean, null, 2)}
          </pre>
        </details>
      )}
    </div>
  );
}
