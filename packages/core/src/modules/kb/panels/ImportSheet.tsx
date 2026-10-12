/**
 * Import a whole site or course: pick a profile, or paste a URL and let the
 * backend pick one (an apxml course URL resolves to that one course; an unknown
 * site gets a generic crawl of its own directory).
 */
import { useEffect, useMemo, useState } from 'react';

import { toastsStore } from '../../../toasts';
import { listProfiles, matchProfile, startImport, type Profile } from '../api';
import { CloseIcon, ImportIcon } from '../icons';

/** A classless button stays a standard 30px control (controls.css); the accent is
 * colour only. A class here would opt it out of the height rule. */
export const ACCENT_BUTTON: React.CSSProperties = {
  borderColor: 'var(--accent)',
  color: 'var(--accent)',
};

export function ImportSheet(props: {
  initialUrl?: string;
  onClose: () => void;
  onStarted: (rootItemId: string) => void;
}) {
  const [profiles, setProfiles] = useState<Profile[] | null>(null);
  const [profileId, setProfileId] = useState<string | null>(null);
  const [url, setUrl] = useState(props.initialUrl ?? '');
  const [matched, setMatched] = useState<Profile | null>(null);
  const [maxPages, setMaxPages] = useState('');
  const [collection, setCollection] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    listProfiles()
      .then((r) => setProfiles(r.profiles))
      .catch(() => setProfiles([]));
  }, []);

  // As the URL changes, show which profile it would use.
  useEffect(() => {
    const trimmed = url.trim();
    if (!/^https?:\/\//.test(trimmed)) {
      setMatched(null);
      return;
    }
    const timer = setTimeout(() => {
      matchProfile(trimmed)
        .then((r) => setMatched(r.profile))
        .catch(() => setMatched(null));
    }, 250);
    return () => clearTimeout(timer);
  }, [url]);

  const chosen = useMemo(
    () => profiles?.find((p) => p.id === profileId) ?? null,
    [profiles, profileId],
  );
  // A parameterised profile (one course) can't be started without its URL.
  const needsUrl = Boolean(chosen?.id === 'apxml-course' && !url.trim());
  const canStart = !busy && (Boolean(chosen) || /^https?:\/\//.test(url.trim())) && !needsUrl;

  const start = async () => {
    setBusy(true);
    try {
      const res = await startImport({
        profile_id: profileId ?? undefined,
        url: url.trim() || undefined,
        max_pages: maxPages ? Number(maxPages) : undefined,
        collections: collection.trim() ? [collection.trim()] : undefined,
      });
      toastsStore.add(
        'info',
        res.resumed ? 'Import resumed' : 'Import started',
        `${res.plan.label ?? res.plan.id} — pages appear as they are archived.`,
      );
      props.onStarted(res.root_item_id);
    } catch (e) {
      toastsStore.add('warning', 'Import failed', e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="kb-sheet" role="dialog" aria-label="Import a site">
      <div className="kb-sheet-card">
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            padding: 'var(--space-5) var(--space-6) var(--space-3)',
          }}
        >
          <div className="kb-title" style={{ flex: 1 }}>
            Import a site
          </div>
          <button type="button" className="btn-mini" onClick={props.onClose} title="Close">
            <CloseIcon size={12} />
          </button>
        </div>
        <div className="kb-mono" style={{ padding: '0 var(--space-6) var(--space-4)' }}>
          every page archived for offline reading · indexed for search · filed as a tree
        </div>

        <div className="kb-field">
          <input
            type="text"
            value={url}
            placeholder="https://… a page of the site or course"
            onChange={(e) => setUrl(e.target.value)}
            spellCheck={false}
          />
          <div className="kb-mono" style={{ marginTop: 'var(--space-2)', minHeight: 16 }}>
            {matched
              ? `matches profile: ${matched.label ?? matched.id}`
              : url.trim()
                ? 'no profile — a generic crawl of this directory'
                : ''}
          </div>
        </div>

        <div className="kb-label">
          Profiles <span className="kb-mono">{profiles?.length ?? '…'}</span>
        </div>
        <div style={{ overflowY: 'auto', minHeight: 0, flex: 1 }}>
          {profiles?.map((p) => (
            <button
              key={p.id}
              type="button"
              className="kb-profile"
              aria-pressed={profileId === p.id}
              onClick={() => setProfileId(profileId === p.id ? null : p.id)}
            >
              <span style={{ color: profileId === p.id ? 'var(--accent)' : 'var(--text-faint)' }}>
                <ImportIcon size={14} />
              </span>
              <span style={{ minWidth: 0 }}>
                <span style={{ display: 'block', fontSize: 'var(--fs-body)', fontWeight: 600 }}>
                  {p.label ?? p.id}
                </span>
                <span className="kb-mono" style={{ display: 'block' }}>
                  {p.description || p.id}
                  {p.builtin === false ? ' · custom' : ''}
                </span>
              </span>
            </button>
          ))}
        </div>

        <div
          className="kb-field"
          style={{
            display: 'grid',
            gridTemplateColumns: '1fr 1fr',
            gap: 'var(--space-3)',
            paddingTop: 'var(--space-4)',
          }}
        >
          <input
            type="text"
            inputMode="numeric"
            value={maxPages}
            placeholder={`page cap (${chosen?.max_pages ?? matched?.max_pages ?? 'profile default'})`}
            onChange={(e) => setMaxPages(e.target.value.replace(/[^0-9]/g, ''))}
          />
          <input
            type="text"
            value={collection}
            placeholder={`collection (${(chosen ?? matched)?.collections?.join(', ') || 'none'})`}
            onChange={(e) => setCollection(e.target.value)}
          />
        </div>
        {needsUrl && (
          <div className="kb-note">
            This profile imports one course — paste a URL from it above.
          </div>
        )}

        <div
          style={{
            display: 'flex',
            justifyContent: 'flex-end',
            gap: 'var(--space-3)',
            padding: 'var(--space-5) var(--space-6)',
          }}
        >
          <button type="button" onClick={props.onClose}>
            Cancel
          </button>
          <button type="button" style={ACCENT_BUTTON} disabled={!canStart} onClick={start}>
            <ImportIcon size={13} />
            {busy ? 'Starting…' : 'Import'}
          </button>
        </div>
      </div>
    </div>
  );
}
