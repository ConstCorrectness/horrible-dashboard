import { useCallback, useState, type ChangeEvent } from 'react';

import {
  BACKGROUND_PRESETS,
  mediaUrl,
  patchProfile,
  uploadProfileImage,
  type PlayerProfile,
  type Showcase,
} from '../profile-api';

export interface ProfileCustomizeProps {
  profile: PlayerProfile;
  /** What could be pinned — the player's ranked tiers, typically. */
  showcaseOptions: Showcase[];
  /** Re-read the live copy so every surface sees the change. */
  onChanged: () => void;
}

const MAX_SHOWCASES = 3;

export function ProfileCustomize({ profile, showcaseOptions, onChanged }: ProfileCustomizeProps) {
  const [status, setStatus] = useState(profile.status_text ?? '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const apply = useCallback(
    async (patch: Parameters<typeof patchProfile>[0]) => {
      setBusy(true);
      setError(null);
      try {
        await patchProfile(patch);
        onChanged();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setBusy(false);
      }
    },
    [onChanged],
  );

  const uploadBackground = useCallback(
    async (e: ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (!file) return;
      setBusy(true);
      setError(null);
      try {
        const { url } = await uploadProfileImage(file, 'background');
        await patchProfile({ background_url: url });
        onChanged();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setBusy(false);
        e.target.value = '';
      }
    },
    [onChanged],
  );

  const pinned = profile.showcase ?? [];
  const isPinned = (option: Showcase) =>
    pinned.some((s) => s.kind === option.kind && s.value === option.value);

  const togglePin = (option: Showcase) => {
    const next = isPinned(option)
      ? pinned.filter((s) => !(s.kind === option.kind && s.value === option.value))
      : [...pinned, option].slice(-MAX_SHOWCASES);
    void apply({ showcase: next });
  };

  const uploaded = mediaUrl(profile.background_url);

  return (
    <div className="profile-editor">
      <label className="people-label">Status & Username Tagline</label>
      <form
        style={{ display: 'flex', gap: '0.4rem' }}
        onSubmit={(e) => {
          e.preventDefault();
          void apply({ status_text: status.trim() });
        }}
      >
        <input
          value={status}
          maxLength={80}
          placeholder="afk til 6 · training harness in AgentTown · open to challenges"
          onChange={(e) => setStatus(e.target.value)}
          style={{ flex: 1 }}
        />
        <button type="submit" disabled={busy || status === (profile.status_text ?? '')}>
          Save
        </button>
      </form>

      <label className="people-label">Profile Banner / Background</label>
      <div className="profile-bg-picker">
        {BACKGROUND_PRESETS.map((preset) => (
          <button
            key={preset.id}
            type="button"
            className="profile-bg-swatch"
            style={{ background: preset.css }}
            title={preset.label}
            disabled={busy}
            data-selected={!uploaded && profile.background_id === preset.id ? 'true' : 'false'}
            onClick={() =>
              void apply({ background_id: preset.id, background_url: '' })
            }
          />
        ))}
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
        <input
          type="file"
          accept="image/png,image/jpeg,image/webp,image/gif"
          disabled={busy}
          onChange={(e) => void uploadBackground(e)}
          style={{ fontSize: '0.75rem' }}
        />
        {uploaded && (
          <button type="button" disabled={busy} onClick={() => void apply({ background_url: '' })}>
            Remove image
          </button>
        )}
      </div>

      {showcaseOptions.length > 0 && (
        <>
          <label className="people-label">Showcase Pinned Badges (up to {MAX_SHOWCASES})</label>
          <div className="profile-bg-picker">
            {showcaseOptions.map((option) => (
              <button
                key={`${option.kind}:${String(option.value)}`}
                type="button"
                disabled={busy}
                style={{
                  fontSize: '0.75rem',
                  borderColor: isPinned(option) ? 'var(--accent, #6ea8fe)' : undefined,
                }}
                onClick={() => togglePin(option)}
              >
                {isPinned(option) ? '★' : '☆'} {String(option.label ?? option.value)}
              </button>
            ))}
          </div>
        </>
      )}

      {error && <p className="profile-comments-error">{error}</p>}
    </div>
  );
}
