import { useCallback, useEffect, useState } from 'react';

import { advertise, listAds, type TrainingAd } from '../api';
import { subscribeChannel } from '../../../ws';
import { revealSection } from '../../../layout/controller';

const dim = { color: 'var(--text-dim)' } as const;

type Mode = 'off' | 'offering' | 'seeking';

/** offering ↔ seeking is a match (their offer meets my seek, and vice versa). */
function isMatch(mine: Mode, theirs: TrainingAd['status']): boolean {
  if (mine === 'offering') return theirs === 'seeking';
  if (mine === 'seeking') return theirs === 'offering';
  return false;
}

function describeSpecs(specs: TrainingAd['specs']): string {
  const parts: string[] = [];
  if (specs.gpu) parts.push(`${specs.gpu}${specs.vram_gb ? ` ${specs.vram_gb}GB` : ''}`);
  else parts.push('no GPU');
  if (specs.cpu_count) parts.push(`${specs.cpu_count} CPU`);
  if (specs.ram_gb) parts.push(`${specs.ram_gb}GB RAM`);
  return parts.join(' · ');
}

/**
 * Training peer fabric: advertise this node as offering GPU / seeking help, see
 * peers' ads, and open a chat to hand off work on a match. v1 is advertise +
 * manual handoff — no remote execution.
 */
export function TrainingPeersPane() {
  const [mode, setMode] = useState<Mode>('off');
  const [note, setNote] = useState('');
  const [ads, setAds] = useState<TrainingAd[]>([]);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(() => {
    listAds()
      .then((r) => setAds(r.ads.filter((a) => a.status !== 'none')))
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    refresh();
    // Live ad updates arrive on the shared training channel.
    return subscribeChannel('training', (msg) => {
      if (msg.event === 'training_ad') refresh();
    });
  }, [refresh]);

  const apply = useCallback(
    (next: Mode) => {
      setMode(next);
      setBusy(true);
      advertise(next, note)
        .catch(() => undefined)
        .finally(() => setBusy(false));
    },
    [note],
  );

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'auto' }}>
      <div style={{ padding: '0.5rem', borderBottom: '1px solid var(--border)' }}>
        <div style={{ fontSize: '0.7rem', textTransform: 'uppercase', ...dim }}>
          Advertise this node
        </div>
        <div style={{ display: 'flex', gap: '0.4rem', marginTop: '0.35rem' }}>
          {(['off', 'offering', 'seeking'] as Mode[]).map((m) => (
            <button
              key={m}
              disabled={busy}
              onClick={() => apply(m)}
              style={{
                fontWeight: mode === m ? 700 : 400,
                borderColor: mode === m ? 'var(--accent, #539bf5)' : undefined,
              }}
            >
              {m === 'off' ? 'Off' : m === 'offering' ? 'Offer GPU' : 'Seek help'}
            </button>
          ))}
        </div>
        <input
          style={{ width: '100%', marginTop: '0.4rem', boxSizing: 'border-box' }}
          placeholder="Note (e.g. 'free evenings, 24GB 4090')"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          onBlur={() => mode !== 'off' && apply(mode)}
        />
      </div>

      <div style={{ flex: 1, padding: '0.5rem' }}>
        <div style={{ fontSize: '0.7rem', textTransform: 'uppercase', ...dim }}>Peers</div>
        {ads.length === 0 && (
          <div style={{ fontSize: '0.8rem', marginTop: '0.5rem', ...dim }}>
            No training ads from peers yet. Connect nodes in the Network panel, then offer or seek
            here.
          </div>
        )}
        <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
          {ads.map((ad) => {
            const match = isMatch(mode, ad.status);
            return (
              <li
                key={ad.node_id}
                style={{
                  padding: '0.4rem',
                  marginTop: '0.4rem',
                  border: `1px solid ${match ? 'var(--accent, #539bf5)' : 'var(--border)'}`,
                  borderRadius: 4,
                  background: match
                    ? 'color-mix(in srgb, var(--accent, #539bf5) 8%, transparent)'
                    : undefined,
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
                  <strong style={{ flex: 1, minWidth: 0, fontSize: '0.85rem' }}>
                    {ad.node_name || ad.node_id.slice(0, 10)}
                  </strong>
                  <span
                    style={{
                      fontSize: '0.68rem',
                      padding: '0.1rem 0.4rem',
                      borderRadius: 999,
                      background:
                        ad.status === 'offering'
                          ? 'var(--ok, #347d39)'
                          : 'var(--warn)',
                      color: '#fff',
                    }}
                  >
                    {ad.status === 'offering' ? 'offering GPU' : 'seeking help'}
                  </span>
                  {match && (
                    <button
                      title="Open a chat with this peer to hand off training"
                      onClick={() => revealSection('messages', 'people.home')}
                    >
                      Open chat
                    </button>
                  )}
                </div>
                <div style={{ fontSize: '0.72rem', ...dim }}>{describeSpecs(ad.specs)}</div>
                {ad.note && <div style={{ fontSize: '0.72rem' }}>{ad.note}</div>}
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}
