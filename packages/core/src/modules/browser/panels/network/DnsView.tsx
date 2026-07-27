/**
 * The delegation ladder: where a name actually comes from.
 *
 * A recursive resolver does this walk once and then hides it behind a cache
 * forever, so most people never see that resolving `docs.python.org` means asking a
 * root server who runs `.org`, asking *those* servers who runs `python.org`, and
 * only then getting an answer — which turns out to be a CNAME, so the whole walk
 * starts again in another zone.
 *
 * Each rung shows who answered, how long it took, and whether the parent signed the
 * delegation (the DNSSEC chain of trust). Glue records get their own line because
 * they're the answer to a genuinely puzzling question: how do you look up
 * `ns1.python.org` if you need `python.org`'s nameservers to do it?
 */
import { useState } from 'react';

import { probeDns, type DnsChain, type DnsHop } from './api';

const LEVEL_LABEL: Record<string, string> = {
  root: 'Root servers',
  tld: 'Top-level domain',
  authoritative: 'Authoritative',
  answer: 'Answer',
};

function HopRung({ hop, last }: { hop: DnsHop; last: boolean }) {
  return (
    <div style={{ display: 'flex', gap: '0.5rem', fontSize: '0.75rem' }}>
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          width: 14,
        }}
      >
        <span style={{ color: hop.signed ? '#4ade80' : 'var(--text-dim)' }}>
          {hop.error ? '✗' : '●'}
        </span>
        {!last && <span style={{ flex: 1, borderLeft: '1px solid var(--border)' }} />}
      </div>

      <div style={{ flex: 1, paddingBottom: '0.55rem' }}>
        <div style={{ display: 'flex', gap: '0.4rem' }}>
          <strong>{LEVEL_LABEL[hop.level] ?? hop.level}</strong>
          <span style={{ color: 'var(--text-dim)' }}>{hop.zone}</span>
          <span style={{ marginLeft: 'auto', color: 'var(--text-dim)' }}>
            {hop.rtt_ms != null ? `${hop.rtt_ms}ms` : ''}
          </span>
        </div>
        <div style={{ color: 'var(--text-dim)' }}>
          asked {hop.server || '—'} about {hop.query}
          {hop.signed && ' · signed (DS present)'}
        </div>

        {hop.referral.length > 0 && (
          <div style={{ color: 'var(--text-dim)' }}>
            → delegated to {hop.referral.slice(0, 3).join(', ')}
            {hop.referral.length > 3 ? ` +${hop.referral.length - 3}` : ''}
          </div>
        )}
        {Object.keys(hop.glue).length > 0 && (
          <div
            style={{ color: 'var(--text-dim)' }}
            title="Addresses shipped with the referral. Without them, looking up a nameserver that lives inside the zone it serves would be circular."
          >
            glue: {Object.values(hop.glue).flat().slice(0, 3).join(', ')}
          </div>
        )}
        {hop.cname && <div>alias → {hop.cname}</div>}
        {hop.answers.length > 0 && <div style={{ fontWeight: 600 }}>{hop.answers.join(', ')}</div>}
        {hop.error && <div style={{ color: 'var(--danger, #d66)' }}>{hop.error}</div>}
      </div>
    </div>
  );
}

export function DnsView({ initialTarget }: { initialTarget?: string }) {
  const [target, setTarget] = useState(initialTarget ?? '');
  const [chain, setChain] = useState<DnsChain | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = () => {
    const host = target.trim();
    if (!host || busy) return;
    setBusy(true);
    setError(null);
    probeDns(host)
      .then(setChain)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)))
      .finally(() => setBusy(false));
  };

  return (
    <div style={{ padding: '0.5rem', fontSize: '0.78rem' }}>
      <div style={{ display: 'flex', gap: '0.4rem', marginBottom: '0.5rem' }}>
        <input
          style={{ flex: 1 }}
          placeholder="host or URL, e.g. docs.python.org"
          value={target}
          onChange={(e) => setTarget(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && run()}
        />
        <button type="button" onClick={run} disabled={busy || !target.trim()}>
          {busy ? '…' : 'Walk'}
        </button>
      </div>

      {error && <div style={{ color: 'var(--danger, #d66)' }}>{error}</div>}

      {!chain && !error && (
        <div className="dashboard-hint">
          Resolve a name the long way — root servers first, then the TLD, then the domain&apos;s own
          nameservers. This is what your resolver does once and then caches. Plain UDP port 53; no
          special privileges needed.
        </div>
      )}

      {chain && (
        <>
          <div style={{ marginBottom: '0.5rem', color: 'var(--text-dim)' }}>
            {chain.addresses.length > 0 ? (
              <>
                <strong style={{ color: 'var(--text)' }}>{chain.addresses.join(', ')}</strong> in{' '}
                {chain.elapsed_ms}ms · {chain.hops.length} step(s)
                {chain.dnssec ? ' · DNSSEC-signed end to end' : ''}
              </>
            ) : (
              `no address resolved (${chain.elapsed_ms}ms)`
            )}
          </div>

          {chain.hops.map((hop, i) => (
            <HopRung
              key={`${hop.server}-${hop.zone}-${i}`}
              hop={hop}
              last={i === chain.hops.length - 1}
            />
          ))}

          {chain.notes.map((note) => (
            <div key={note} className="dashboard-hint" style={{ marginTop: '0.3rem' }}>
              {note}
            </div>
          ))}
        </>
      )}
    </div>
  );
}
