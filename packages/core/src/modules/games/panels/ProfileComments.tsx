/**
 * A profile's comment wall.
 *
 * Comments live on the **game server**, not on either node, and that is the whole
 * point: it is the only always-on machine, so you can write on someone's profile
 * while every one of their computers is off, and they read it whenever they next
 * sign in. A peer-to-peer comment would only be deliverable when both people
 * happen to be online — which is a message, not a wall.
 *
 * Authorship is by account, so a comment survives the commenter going away.
 * Hiding is the profile owner's (or the author's) call and is a soft hide, not a
 * delete — `hidden` on the row, so a moderation decision is reversible.
 */
import { useCallback, useEffect, useState } from 'react';

import { Avatar } from '../../people/Avatar';
import { addComment, fetchComments, hideComment, type ProfileComment } from '../profile-api';

export interface ProfileCommentsProps {
  handle: string;
  /** The signed-in account, when there is one. Absent ⇒ read-only. */
  viewerAccountId?: string | null;
  /** True when this is the viewer's own profile: they may hide anything on it. */
  isOwner?: boolean;
}

function when(ts: number): string {
  const seconds = Date.now() / 1000 - ts;
  if (seconds < 60) return 'just now';
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return new Date(ts * 1000).toLocaleDateString();
}

export function ProfileComments({ handle, viewerAccountId, isOwner }: ProfileCommentsProps) {
  const [comments, setComments] = useState<ProfileComment[]>([]);
  const [draft, setDraft] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(() => {
    setLoading(true);
    fetchComments(handle)
      .then(setComments)
      .catch(() => setComments([]))
      .finally(() => setLoading(false));
  }, [handle]);

  useEffect(reload, [reload]);

  const post = useCallback(async () => {
    const body = draft.trim();
    if (!body) return;
    setBusy(true);
    setError(null);
    try {
      const comment = await addComment(handle, body);
      setComments((prev) => [comment, ...prev]);
      setDraft('');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }, [draft, handle]);

  const hide = useCallback(async (id: string) => {
    try {
      await hideComment(id);
      setComments((prev) => prev.filter((c) => c.id !== id));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  return (
    <div className="profile-comments">
      <h4 className="profile-comments-title">Comments ({comments.length})</h4>

      {viewerAccountId ? (
        <form
          className="profile-comment-form"
          onSubmit={(e) => {
            e.preventDefault();
            void post();
          }}
        >
          <textarea
            value={draft}
            rows={2}
            maxLength={1000}
            placeholder={isOwner ? 'Write on your own wall…' : `Leave a comment for @${handle}…`}
            onChange={(e) => setDraft(e.target.value)}
          />
          <button type="submit" disabled={busy || !draft.trim()}>
            {busy ? 'Posting…' : 'Post'}
          </button>
        </form>
      ) : (
        <p className="profile-comments-empty">Sign in to leave a comment.</p>
      )}

      {error && <p className="profile-comments-error">{error}</p>}

      {loading ? (
        <p className="profile-comments-empty">Loading…</p>
      ) : comments.length === 0 ? (
        <p className="profile-comments-empty">
          Nothing here yet{isOwner ? '.' : ' — be the first to say something.'}
        </p>
      ) : (
        <ul className="profile-comment-list">
          {comments.map((c) => (
            <li key={c.id} className="profile-comment">
              <Avatar
                name={c.author_name ?? c.author_id}
                emoji={c.author_avatar}
                imageRef={c.author_avatar_url}
                size={28}
              />
              <div className="profile-comment-body">
                <div className="profile-comment-meta">
                  <strong>{c.author_name ?? c.author_handle ?? 'someone'}</strong>
                  <span>{when(c.created_at)}</span>
                  {(isOwner || c.author_id === viewerAccountId) && (
                    <button
                      type="button"
                      className="profile-comment-hide"
                      title="Hide this comment"
                      onClick={() => void hide(c.id)}
                    >
                      ✕
                    </button>
                  )}
                </div>
                <p>{c.body}</p>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
