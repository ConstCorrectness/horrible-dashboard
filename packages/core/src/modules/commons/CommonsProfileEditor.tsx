import { useEffect, useState, useSyncExternalStore } from 'react';

import { toastsStore } from '../../toasts';
import { commonsSetProfile, getCommonsState, initCommons, subscribeCommons } from './commons';

function useCommons() {
  return useSyncExternalStore(subscribeCommons, getCommonsState, getCommonsState);
}

const FIELD_STYLE = { display: 'flex', flexDirection: 'column' as const, gap: '0.2rem' };
const LABEL_STYLE = { fontSize: '0.75rem', color: 'var(--text-dim)' };

/**
 * Edit this node's commons profile (the storefront) and republish. The fields are
 * persisted as `commons.*` settings server-side and the profile is re-signed there —
 * the browser only supplies the editable text. See docs/modules/commons.mdx.
 */
export function CommonsProfileEditor() {
  const { myProfile, connected } = useCommons();
  const [headline, setHeadline] = useState('');
  const [bio, setBio] = useState('');
  const [tags, setTags] = useState('');
  const [seeking, setSeeking] = useState('');
  const [visibility, setVisibility] = useState('public');

  useEffect(() => {
    initCommons();
  }, []);

  // Prefill from the node's current profile once it arrives.
  useEffect(() => {
    if (!myProfile) return;
    setHeadline(myProfile.headline ?? '');
    setBio(myProfile.bio ?? '');
    setTags((myProfile.tags ?? []).join(', '));
    setSeeking(myProfile.seeking ?? '');
    setVisibility(myProfile.visibility ?? 'public');
  }, [myProfile]);

  return (
    <form
      className="commons-profile-editor"
      style={{
        padding: '1rem',
        display: 'flex',
        flexDirection: 'column',
        gap: '0.6rem',
        height: '100%',
        overflow: 'auto',
      }}
      onSubmit={(e) => {
        e.preventDefault();
        commonsSetProfile({ headline, bio, tags, seeking, visibility });
        toastsStore.add(
          'success',
          'Commons',
          connected ? 'Profile saved and republished.' : 'Profile saved.',
        );
      }}
    >
      <h3 style={{ margin: 0 }}>My commons profile</h3>
      <p style={{ margin: 0, fontSize: '0.75rem', color: 'var(--text-dim)' }}>
        Display name comes from your node name. Saving re-signs and republishes the profile.
      </p>

      <label style={FIELD_STYLE}>
        <span style={LABEL_STYLE}>Headline</span>
        <input
          value={headline}
          onChange={(e) => setHeadline(e.target.value)}
          placeholder="What you / your agent do"
        />
      </label>

      <label style={FIELD_STYLE}>
        <span style={LABEL_STYLE}>Bio</span>
        <textarea value={bio} onChange={(e) => setBio(e.target.value)} rows={3} />
      </label>

      <label style={FIELD_STYLE}>
        <span style={LABEL_STYLE}>Tags (comma-separated)</span>
        <input
          value={tags}
          onChange={(e) => setTags(e.target.value)}
          placeholder="rust, data-viz, trading"
        />
      </label>

      <label style={FIELD_STYLE}>
        <span style={LABEL_STYLE}>Looking for</span>
        <input value={seeking} onChange={(e) => setSeeking(e.target.value)} />
      </label>

      <label style={FIELD_STYLE}>
        <span style={LABEL_STYLE}>Visibility</span>
        <select value={visibility} onChange={(e) => setVisibility(e.target.value)}>
          <option value="public">public (listed in the directory)</option>
          <option value="unlisted">unlisted (reachable by link only)</option>
        </select>
      </label>

      <button type="submit" style={{ alignSelf: 'flex-start' }}>
        Save &amp; publish
      </button>
    </form>
  );
}
