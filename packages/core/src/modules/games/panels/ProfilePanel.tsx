import React, { useEffect, useState } from 'react';

import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Chip from '@mui/material/Chip';
import Typography from '@mui/material/Typography';
import LinearProgress from '@mui/material/LinearProgress';
import Button from '@mui/material/Button';
import TextField from '@mui/material/TextField';
import Dialog from '@mui/material/Dialog';
import DialogTitle from '@mui/material/DialogTitle';
import DialogContent from '@mui/material/DialogContent';
import DialogActions from '@mui/material/DialogActions';
import List from '@mui/material/List';
import ListItem from '@mui/material/ListItem';
import ListItemAvatar from '@mui/material/ListItemAvatar';
import Avatar from '@mui/material/Avatar';
import IconButton from '@mui/material/IconButton';
import MenuItem from '@mui/material/MenuItem';
import Select from '@mui/material/Select';

import { apiGet } from '../../../api';
import {
  profileGet,
  profileSet,
  friendList,
  friendRequest,
  friendAccept,
  friendRemove,
  socialInvite,
  useGames,
} from '../game-ws';
import { fetchGamesCatalog, fetchLeaderboard, type GameCatalogEntry } from '../games-api';
import {
  backgroundCss,
  mediaUrl,
  patchProfile,
  uploadProfileImage,
  type Showcase,
} from '../profile-api';
import { invalidateProfileCards } from '../../people/profile-cards';
import { openReplay } from '../replay-focus';
import { openGamesSection } from '../hub-section';
import { GamesMui } from '../mui-theme';
import { ProfileComments } from './ProfileComments';
import { ProfileCustomize } from './ProfileCustomize';

interface MatchEntry {
  ts: number;
  game_id: string;
  result: string;
  loadout_version: string | null;
  model_label: string | null;
  replay_id: string | null;
  rating_delta: number | null;
  tier?: string | null;
}

interface TierCard {
  game_id: string;
  name: string;
  tier: string | null;
  rating: number | null;
  record: string;
}

export function ProfilePanel() {
  const { social, accountId } = useGames();
  const [cards, setCards] = useState<TierCard[]>([]);
  const [log, setLog] = useState<MatchEntry[]>([]);
  const [socialMe, setSocialMe] = useState<{ code?: string; person_id?: string; display_name?: string } | null>(null);
  const [copiedCode, setCopiedCode] = useState(false);

  // Custom states for editing
  const [editingAvatar, setEditingAvatar] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [editingBio, setEditingBio] = useState(false);
  const [bioInput, setBioInput] = useState('');
  const [friendIdInput, setFriendIdInput] = useState('');
  const [customizing, setCustomizing] = useState(false);

  // Challenge modal states
  const [challengeTarget, setChallengeTarget] = useState<string | null>(null);
  const [challengeTargetName, setChallengeTargetName] = useState<string>('');
  const [challengeGameId, setChallengeGameId] = useState<string>('tictactoe');
  const [catalogGames, setCatalogGames] = useState<GameCatalogEntry[]>([]);

  useEffect(() => {
    profileGet();
    friendList();
    fetchGamesCatalog().then(setCatalogGames);
    apiGet<{ entries: MatchEntry[] }>('/games/match-log?limit=30')
      .then((r) => setLog(r.entries))
      .catch(() => setLog([]));
    apiGet<{ code?: string; person_id?: string; display_name?: string }>('/social/me')
      .then(setSocialMe)
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!accountId) return;
    fetchGamesCatalog().then(async (games: GameCatalogEntry[]) => {
      const rows = await Promise.all(
        games.map(async (g) => {
          try {
            const lb = await fetchLeaderboard(g.id);
            const me = lb.entries.find((e) => e.account_id === accountId);
            if (!me) return null;
            return {
              game_id: g.id,
              name: g.name,
              tier: me.tier ?? null,
              rating: me.rating,
              record: `${me.wins}W/${me.losses}L/${me.draws}D`,
            };
          } catch {
            return null;
          }
        }),
      );
      setCards(rows.filter((r): r is TierCard => r !== null));
    });
  }, [accountId]);

  const profile = social.profile;

  // Sync bio input when profile loads
  useEffect(() => {
    if (profile) {
      setBioInput(profile.bio || '');
    }
  }, [profile]);

  const pct =
    profile && profile.next_level_xp !== null
      ? Math.min(
          100,
          Math.round(
            ((profile.xp - profile.level_floor) / (profile.next_level_xp - profile.level_floor)) *
              100,
          ),
        )
      : 100;

  /**
   * Upload a profile picture.
   *
   * This used to `readAsDataURL` the file and hand the base64 string to
   * `profileSet` as the **avatar emoji** — a column the server caps at 8
   * characters. Every upload was therefore silently truncated to eight bytes of
   * base64 and no picture ever appeared, with no error anywhere. The file goes to
   * the media endpoint now and the profile stores a reference to it.
   */
  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploadError(null);
    setUploading(true);
    try {
      const { url } = await uploadProfileImage(file, 'avatar');
      await patchProfile({ avatar_url: url });
      profileGet(); // re-read the live copy so every surface sees the new artwork
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : String(err));
    } finally {
      setUploading(false);
      // Let the same file be chosen again after a failure.
      e.target.value = '';
    }
  };

  const handleSaveBio = () => {
    profileSet(profile?.avatar, bioInput);
    setEditingBio(false);
  };

  const handleSendFriendRequest = (e: React.FormEvent) => {
    e.preventDefault();
    if (friendIdInput.trim()) {
      friendRequest(friendIdInput.trim());
      setFriendIdInput('');
    }
  };

  const handleLaunchChallenge = () => {
    if (challengeTarget && challengeGameId) {
      socialInvite(challengeTarget, challengeGameId);
      setChallengeTarget(null);
    }
  };

  /**
   * The avatar, preferring an uploaded image over the emoji fallback.
   *
   * `imageRef` is a stored media reference (`/media/<sha>`); `avatarStr` is the
   * emoji that always exists. Passing a data URL still renders — an in-flight
   * preview does that — but nothing writes one to the server any more.
   */
  const renderAvatar = (avatarStr: string, size = '4.5rem', imageRef?: string | null) => {
    const src = mediaUrl(imageRef) ?? (avatarStr.startsWith('data:image/') ? avatarStr : null);
    if (src) {
      return (
        <img
          src={src}
          alt="Profile Avatar"
          style={{
            width: size,
            height: size,
            borderRadius: '50%',
            objectFit: 'cover',
            border: '2px solid var(--accent, #6ea8fe)',
          }}
        />
      );
    }
    return (
      <Avatar
        sx={{
          width: size,
          height: size,
          fontSize: size === '4.5rem' ? '2.2rem' : '1.5rem',
          bgcolor: 'rgba(110, 168, 254, 0.1)',
          border: '2px solid var(--accent, #6ea8fe)',
          color: 'var(--accent, #6ea8fe)',
        }}
      >
        {avatarStr}
      </Avatar>
    );
  };

  return (
    <GamesMui>
      <div
        style={{
          padding: '1rem',
          fontSize: '0.85rem',
          height: '100%',
          overflowY: 'auto',
          color: 'var(--text)',
          boxSizing: 'border-box',
        }}
      >
        {!profile ? (
          <div style={{ color: 'var(--text-dim)', textAlign: 'center', marginTop: '2rem' }}>
            Connect to the game server to load your profile.
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem' }}>
            {/* ── Steam-Style Header ──
                The header *is* the banner: an uploaded image if there is one, else
                the chosen preset gradient, else the neutral wash it always had. */}
            <Card
              sx={{
                background: mediaUrl(profile.background_url)
                  ? `url(${mediaUrl(profile.background_url)}) center/cover`
                  : (backgroundCss(profile.background_id) ??
                    'linear-gradient(135deg, rgba(38,42,50,0.9) 0%, rgba(20,22,26,0.9) 100%)'),
                borderColor: 'divider',
              }}
            >
              <CardContent
                sx={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '1.2rem',
                  flexWrap: 'wrap',
                  position: 'relative',
                  p: '1.5rem',
                }}
              >
                <div style={{ position: 'relative' }}>
                  {renderAvatar(profile.avatar, '4.5rem', profile.avatar_url)}
                  <button
                    type="button"
                    style={{
                      position: 'absolute',
                      bottom: 0,
                      right: 0,
                      background: 'var(--accent, #6ea8fe)',
                      color: '#000',
                      border: 'none',
                      borderRadius: '50%',
                      width: '1.8rem',
                      height: '1.8rem',
                      fontSize: '0.9rem',
                      cursor: 'pointer',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      boxShadow: '0 2px 4px rgba(0,0,0,0.3)',
                    }}
                    title="Change picture / avatar"
                    onClick={() => setEditingAvatar((v) => !v)}
                  >
                    ✏️
                  </button>
                </div>

                <div style={{ flex: 1, minWidth: '200px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
                    <Typography variant="h5" sx={{ fontWeight: 800 }}>
                      {profile.display_name || profile.handle || profile.account_id}
                    </Typography>
                    {profile.handle && (
                      <Chip
                        size="small"
                        label={`@${profile.handle}`}
                        sx={{ fontWeight: 700, bgcolor: 'rgba(56, 189, 248, 0.15)', color: '#38bdf8' }}
                      />
                    )}
                    {socialMe?.code && (
                      <Chip
                        size="small"
                        label={copiedCode ? '✓ Copied' : `Friend Code: ${socialMe.code}`}
                        onClick={() => {
                          if (socialMe.code) {
                            navigator.clipboard.writeText(socialMe.code);
                            setCopiedCode(true);
                            setTimeout(() => setCopiedCode(false), 2000);
                          }
                        }}
                        title="Click to copy your self-certifying friend code"
                        sx={{ cursor: 'pointer', borderColor: 'rgba(255,255,255,0.2)' }}
                        variant="outlined"
                      />
                    )}
                  </div>
                  <Typography variant="body2" sx={{ color: 'text.secondary', fontSize: '0.78rem', mt: 0.2 }}>
                    ID: {profile.account_id}
                  </Typography>
                  {profile.status_text && (
                    <Typography
                      variant="body2"
                      sx={{ fontStyle: 'italic', color: 'text.secondary', mt: 0.4 }}
                    >
                      {profile.status_text}
                    </Typography>
                  )}
                  <div
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: '0.6rem',
                      marginTop: '0.6rem',
                    }}
                  >
                    <Typography
                      variant="subtitle2"
                      sx={{ fontWeight: 700, color: 'var(--accent, #6ea8fe)' }}
                    >
                      Level {profile.level}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      {profile.xp} XP
                    </Typography>
                  </div>
                  <LinearProgress
                    variant="determinate"
                    value={Math.max(4, pct)}
                    sx={{ width: '100%', maxWidth: '280px', mt: 0.5, height: 6, borderRadius: 999 }}
                  />
                </div>

                <Button size="small" onClick={() => setCustomizing((v) => !v)}>
                  {customizing ? 'Done' : 'Customize'}
                </Button>

                {customizing && (
                  <div style={{ width: '100%', marginTop: '0.8rem' }}>
                    <ProfileCustomize
                      profile={profile}
                      // The tiers you've actually earned are the only honest thing
                      // to pin: a showcase you can pick without having done it is
                      // decoration, not a record.
                      showcaseOptions={cards.map(
                        (c): Showcase => ({
                          kind: 'tier',
                          value: c.game_id,
                          label: `${c.name}${c.tier ? ` · ${c.tier}` : ''}`,
                        }),
                      )}
                      onChanged={() => {
                        profileGet();
                        // Your new picture has to reach the friends list too, which
                        // caches cards for the session.
                        invalidateProfileCards();
                      }}
                    />
                  </div>
                )}

                {editingAvatar && (
                  <div
                    style={{
                      width: '100%',
                      marginTop: '1rem',
                      paddingTop: '1rem',
                      borderTop: '1px solid var(--border)',
                      display: 'flex',
                      flexDirection: 'column',
                      gap: '0.8rem',
                    }}
                  >
                    <div>
                      <Typography
                        variant="caption"
                        sx={{ display: 'block', mb: 0.5, fontWeight: 700, color: 'text.secondary' }}
                      >
                        UPLOAD PICTURE
                      </Typography>
                      <input
                        type="file"
                        accept="image/png,image/jpeg,image/webp,image/gif"
                        disabled={uploading}
                        onChange={(e) => void handleFileUpload(e)}
                        style={{ fontSize: '0.8rem' }}
                      />
                      {uploading && (
                        <Typography variant="caption" sx={{ display: 'block', mt: 0.5 }}>
                          Uploading…
                        </Typography>
                      )}
                      {/* Surfaced, not swallowed: the server rejects oversize files,
                          unsupported formats and mislabelled ones, and a silent
                          failure here is exactly what hid the old truncation bug. */}
                      {uploadError && (
                        <Typography
                          variant="caption"
                          sx={{ display: 'block', mt: 0.5, color: '#f85149' }}
                        >
                          {uploadError}
                        </Typography>
                      )}
                      {profile.avatar_url && (
                        <Button
                          size="small"
                          sx={{ mt: 0.5 }}
                          disabled={uploading}
                          onClick={() => {
                            // An explicit empty string clears it; `undefined` would
                            // mean "leave it alone" to the patch endpoint.
                            void patchProfile({ avatar_url: '' }).then(() => profileGet());
                          }}
                        >
                          Remove picture
                        </Button>
                      )}
                    </div>
                    <div>
                      <Typography
                        variant="caption"
                        sx={{ display: 'block', mb: 0.5, fontWeight: 700, color: 'text.secondary' }}
                      >
                        OR SELECT EMOJI
                      </Typography>
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.4rem' }}>
                        {[
                          '🤖',
                          '🦾',
                          '🧠',
                          '👾',
                          '🐙',
                          '🦊',
                          '🐲',
                          '⚡',
                          '🛠',
                          '🎯',
                          '🃏',
                          '🚀',
                          '👑',
                          '🧙',
                          '🐯',
                          '🐼',
                        ].map((a) => (
                          <button
                            key={a}
                            type="button"
                            style={{
                              background: 'rgba(255,255,255,0.05)',
                              border: 'none',
                              borderRadius: '4px',
                              padding: '0.4rem',
                              fontSize: '1.2rem',
                              cursor: 'pointer',
                            }}
                            onClick={() => {
                              profileSet(a, profile?.bio);
                              setEditingAvatar(false);
                            }}
                          >
                            {a}
                          </button>
                        ))}
                      </div>
                    </div>
                  </div>
                )}
              </CardContent>
            </Card>

            {/* ── Two-Column Steam Layout ── */}
            <div style={{ display: 'flex', gap: '1.5rem', flexWrap: 'wrap' }}>
              {/* Left Column (Bio & Replays) */}
              <div
                style={{
                  flex: '2 1 500px',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '1.5rem',
                }}
              >
                {/* Biography / Description */}
                <Card sx={{ borderColor: 'divider' }}>
                  <CardContent sx={{ display: 'flex', flexDirection: 'column', gap: '0.6rem' }}>
                    <div
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                      }}
                    >
                      <Typography
                        variant="subtitle1"
                        sx={{ fontWeight: 800, color: 'text.secondary' }}
                      >
                        📝 Profile Description
                      </Typography>
                      {!editingBio && (
                        <Button size="small" onClick={() => setEditingBio(true)}>
                          Edit Bio
                        </Button>
                      )}
                    </div>

                    {editingBio ? (
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem' }}>
                        <TextField
                          multiline
                          rows={3}
                          fullWidth
                          variant="outlined"
                          size="small"
                          placeholder="Tell people about yourself or your agent's strategy..."
                          value={bioInput}
                          onChange={(e) => setBioInput(e.target.value)}
                        />
                        <div style={{ display: 'flex', gap: '0.5rem', justifyContent: 'flex-end' }}>
                          <Button
                            size="small"
                            variant="outlined"
                            color="inherit"
                            onClick={() => setEditingBio(false)}
                          >
                            Cancel
                          </Button>
                          <Button
                            size="small"
                            variant="contained"
                            color="primary"
                            onClick={handleSaveBio}
                          >
                            Save
                          </Button>
                        </div>
                      </div>
                    ) : (
                      <Typography
                        variant="body2"
                        sx={{
                          fontStyle: profile.bio ? 'normal' : 'italic',
                          color: profile.bio ? 'text.primary' : 'text.secondary',
                          whiteSpace: 'pre-wrap',
                          lineHeight: 1.5,
                        }}
                      >
                        {profile.bio ||
                          'No profile description set yet. Click Edit Bio to write one!'}
                      </Typography>
                    )}
                  </CardContent>
                </Card>

                {/* Your own wall. Readable while every one of your machines is
                    off, because it lives on the game server rather than on a node
                    — which is the whole reason comments are not a peer message. */}
                {profile.handle && (
                  <Card sx={{ borderColor: 'divider' }}>
                    <CardContent>
                      <ProfileComments
                        handle={profile.handle}
                        viewerAccountId={accountId}
                        isOwner
                      />
                    </CardContent>
                  </Card>
                )}

                {/* AgentTown Resident Presence */}
                <Card sx={{ borderColor: 'divider' }}>
                  <CardContent sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '0.8rem' }}>
                    <div>
                      <Typography variant="subtitle1" sx={{ fontWeight: 800 }}>
                        🏛 AgentTown Resident Presence
                      </Typography>
                      <Typography variant="body2" color="text.secondary" sx={{ fontSize: '0.8rem', mt: 0.3 }}>
                        Your autonomous agent lives, works, and socializes in AgentTown. Observe its activity, wanderings, and whisper task directives into its next tick.
                      </Typography>
                    </div>
                    <Button size="small" variant="contained" onClick={() => openGamesSection('social')}>
                      Observe in AgentTown →
                    </Button>
                  </CardContent>
                </Card>

                {/* Recent Matches */}
                <Card sx={{ borderColor: 'divider' }}>
                  <CardContent sx={{ display: 'flex', flexDirection: 'column', gap: '0.6rem' }}>
                    <Typography
                      variant="subtitle1"
                      sx={{ fontWeight: 800, color: 'text.secondary', mb: 0.5 }}
                    >
                      📼 Recent Match Replays
                    </Typography>
                    {log.length === 0 ? (
                      <Typography
                        variant="body2"
                        color="text.secondary"
                        sx={{ py: 1, textAlign: 'center' }}
                      >
                        No matches recorded yet. Complete a casual or ranked game to see replays
                        here.
                      </Typography>
                    ) : (
                      <div style={{ overflowX: 'auto' }}>
                        <table
                          style={{ borderCollapse: 'collapse', width: '100%', fontSize: '0.8rem' }}
                        >
                          <thead>
                            <tr
                              style={{
                                borderBottom: '1px solid var(--border)',
                                textAlign: 'left',
                                color: 'var(--text-dim)',
                              }}
                            >
                              <th style={{ padding: '0.4rem' }}>Result</th>
                              <th style={{ padding: '0.4rem' }}>Game</th>
                              <th style={{ padding: '0.4rem' }}>Harness Details</th>
                              <th style={{ padding: '0.4rem' }}>Rating Change</th>
                              <th style={{ padding: '0.4rem' }}>Date</th>
                              <th style={{ padding: '0.4rem', textAlign: 'center' }}>Replay</th>
                            </tr>
                          </thead>
                          <tbody>
                            {log.map((e, i) => (
                              <tr key={i} className="games-match-log-row">
                                <td style={{ padding: '0.4rem', fontWeight: 700 }}>
                                  {e.result === 'win' ? (
                                    <span style={{ color: '#3fb950' }}>🏆 Win</span>
                                  ) : e.result === 'loss' ? (
                                    <span style={{ color: '#e5534b' }}>💥 Loss</span>
                                  ) : (
                                    <span style={{ color: 'var(--text-dim)' }}>🤝 Draw</span>
                                  )}
                                </td>
                                <td style={{ padding: '0.4rem', textTransform: 'capitalize' }}>
                                  {e.game_id.replace('_', ' ')}
                                </td>
                                <td style={{ padding: '0.4rem', color: 'var(--text-dim)' }}>
                                  {e.loadout_version ?? 'v1'}
                                  {e.model_label ? ` (${e.model_label})` : ''}
                                </td>
                                <td style={{ padding: '0.4rem' }}>
                                  {e.rating_delta !== null ? (
                                    <span
                                      style={{
                                        color: e.rating_delta >= 0 ? '#3fb950' : '#e5534b',
                                        fontWeight: 700,
                                      }}
                                    >
                                      {e.rating_delta >= 0 ? `+${e.rating_delta}` : e.rating_delta}
                                    </span>
                                  ) : (
                                    <span style={{ color: 'var(--text-dim)' }}>—</span>
                                  )}
                                </td>
                                <td style={{ padding: '0.4rem', color: 'var(--text-dim)' }}>
                                  {new Date(e.ts * 1000).toLocaleDateString()}
                                </td>
                                <td style={{ padding: '0.4rem', textAlign: 'center' }}>
                                  {e.replay_id ? (
                                    <Button
                                      size="small"
                                      variant="outlined"
                                      sx={{ minWidth: 0, py: 0.1, px: 1, fontSize: '0.72rem' }}
                                      onClick={() => openReplay(e.replay_id!)}
                                    >
                                      📼 Play
                                    </Button>
                                  ) : (
                                    '—'
                                  )}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </CardContent>
                </Card>
              </div>

              {/* Right Column (Tiers & Friends) */}
              <div
                style={{
                  flex: '1 1 320px',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '1.5rem',
                }}
              >
                {/* Friends List Card */}
                <Card sx={{ borderColor: 'divider' }}>
                  <CardContent sx={{ display: 'flex', flexDirection: 'column', gap: '0.8rem' }}>
                    <div
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                      }}
                    >
                      <Typography
                        variant="subtitle1"
                        sx={{ fontWeight: 800, color: 'text.secondary' }}
                      >
                        👥 Friends List ({social.friends.length})
                      </Typography>
                    </div>

                    {/* Friend Add Input */}
                    <form
                      onSubmit={handleSendFriendRequest}
                      style={{ display: 'flex', gap: '0.4rem' }}
                    >
                      <TextField
                        size="small"
                        placeholder="Add friend by Account ID..."
                        variant="outlined"
                        value={friendIdInput}
                        onChange={(e) => setFriendIdInput(e.target.value)}
                        fullWidth
                        sx={{ input: { fontSize: '0.75rem', py: 0.6 } }}
                      />
                      <Button
                        type="submit"
                        variant="contained"
                        size="small"
                        sx={{ fontSize: '0.7rem' }}
                      >
                        Request
                      </Button>
                    </form>

                    {/* Pending incoming requests */}
                    {social.pending.length > 0 && (
                      <div
                        style={{
                          background: 'rgba(110, 168, 254, 0.05)',
                          borderRadius: '4px',
                          padding: '0.5rem',
                        }}
                      >
                        <Typography
                          variant="caption"
                          sx={{ display: 'block', mb: 0.5, fontWeight: 700, color: 'primary.main' }}
                        >
                          📥 INCOMING FRIEND REQUESTS ({social.pending.length})
                        </Typography>
                        <List dense sx={{ p: 0 }}>
                          {social.pending.map((req) => (
                            <ListItem
                              key={req.account_id}
                              sx={{
                                px: 0.4,
                                py: 0.2,
                                display: 'flex',
                                alignItems: 'center',
                                gap: '0.5rem',
                              }}
                            >
                              <ListItemAvatar sx={{ minWidth: 0 }}>
                                {renderAvatar(req.avatar, '1.8rem')}
                              </ListItemAvatar>
                              <div style={{ flex: 1, minWidth: 0 }}>
                                <div style={{ fontSize: '0.78rem', fontWeight: 700 }}>
                                  {req.display_name}
                                </div>
                              </div>
                              <div style={{ display: 'flex', gap: '0.2rem' }}>
                                <Button
                                  size="small"
                                  variant="contained"
                                  color="success"
                                  sx={{ minWidth: 0, py: 0.1, px: 0.8, fontSize: '0.65rem' }}
                                  onClick={() => friendAccept(req.account_id)}
                                >
                                  Accept
                                </Button>
                                <Button
                                  size="small"
                                  variant="outlined"
                                  color="error"
                                  sx={{ minWidth: 0, py: 0.1, px: 0.8, fontSize: '0.65rem' }}
                                  onClick={() => friendRemove(req.account_id)}
                                >
                                  Decline
                                </Button>
                              </div>
                            </ListItem>
                          ))}
                        </List>
                      </div>
                    )}

                    {/* Friends Roster */}
                    {social.friends.length === 0 ? (
                      <Typography
                        variant="body2"
                        color="text.secondary"
                        sx={{ py: 1, textAlign: 'center' }}
                      >
                        No friends added yet. Send requests using their account ID above.
                      </Typography>
                    ) : (
                      <List dense sx={{ p: 0 }}>
                        {social.friends.map((friend) => (
                          <ListItem
                            key={friend.account_id}
                            secondaryAction={
                              <div style={{ display: 'flex', gap: '0.2rem', alignItems: 'center' }}>
                                {friend.online && (
                                  <Button
                                    size="small"
                                    variant="outlined"
                                    color="primary"
                                    sx={{
                                      py: 0.1,
                                      px: 0.8,
                                      fontSize: '0.65rem',
                                      textTransform: 'none',
                                    }}
                                    onClick={() => {
                                      setChallengeTarget(friend.account_id);
                                      setChallengeTargetName(friend.display_name);
                                    }}
                                  >
                                    Challenge
                                  </Button>
                                )}
                                <IconButton
                                  size="small"
                                  color="error"
                                  onClick={() => friendRemove(friend.account_id)}
                                  title="Remove friend"
                                >
                                  🗑
                                </IconButton>
                              </div>
                            }
                            sx={{ px: 0, py: 0.4 }}
                          >
                            <ListItemAvatar
                              sx={{ minWidth: 0, mr: '0.6rem', position: 'relative' }}
                            >
                              {renderAvatar(friend.avatar, '2.2rem')}
                              <div
                                style={{
                                  position: 'absolute',
                                  bottom: 0,
                                  right: 0,
                                  width: '0.65rem',
                                  height: '0.65rem',
                                  borderRadius: '50%',
                                  border: '2px solid var(--bg-raised, #1d2026)',
                                  background: friend.online ? '#3fb950' : '#8a909c',
                                }}
                                title={friend.online ? 'Online' : 'Offline'}
                              />
                            </ListItemAvatar>
                            <div style={{ flex: 1, minWidth: 0 }}>
                              <div style={{ fontSize: '0.82rem', fontWeight: 700 }}>
                                {friend.display_name}
                              </div>
                              <div style={{ fontSize: '0.72rem', color: 'var(--text-dim)' }}>
                                Lv {friend.level} · {friend.online ? 'Online' : 'Offline'}
                              </div>
                            </div>
                          </ListItem>
                        ))}
                      </List>
                    )}
                  </CardContent>
                </Card>

                {/* Ranked Game Tiers */}
                <Card sx={{ borderColor: 'divider' }}>
                  <CardContent sx={{ display: 'flex', flexDirection: 'column', gap: '0.6rem' }}>
                    <Typography
                      variant="subtitle1"
                      sx={{ fontWeight: 800, color: 'text.secondary' }}
                    >
                      🏆 Ranked Skill Tiers
                    </Typography>
                    {cards.length === 0 ? (
                      <Typography
                        variant="body2"
                        color="text.secondary"
                        sx={{ py: 1, textAlign: 'center' }}
                      >
                        No ranked game tiers records yet. Complete ranked games in the lobby to earn
                        ratings.
                      </Typography>
                    ) : (
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem' }}>
                        {cards.map((c) => (
                          <div
                            key={c.game_id}
                            style={{
                              display: 'flex',
                              alignItems: 'center',
                              justifyContent: 'space-between',
                              background: 'rgba(255,255,255,0.02)',
                              border: '1px solid var(--border)',
                              borderRadius: '4px',
                              padding: '0.5rem 0.7rem',
                            }}
                          >
                            <div>
                              <Typography sx={{ fontWeight: 800, fontSize: '0.8rem' }}>
                                {c.name}
                              </Typography>
                              <Typography variant="caption" color="text.secondary">
                                Rating: {c.rating ?? '···'} · {c.record}
                              </Typography>
                            </div>
                            <Chip
                              size="small"
                              color={c.tier ? 'primary' : 'default'}
                              variant="outlined"
                              label={c.tier ?? '—'}
                              sx={{ height: 20, fontSize: '0.7rem', fontWeight: 700 }}
                            />
                          </div>
                        ))}
                      </div>
                    )}
                  </CardContent>
                </Card>
              </div>
            </div>

            {/* ── Challenge Invitation Dialog ── */}
            <Dialog open={challengeTarget !== null} onClose={() => setChallengeTarget(null)}>
              <DialogTitle sx={{ fontWeight: 800, fontSize: '1rem' }}>
                ⚔️ Challenge {challengeTargetName}
              </DialogTitle>
              <DialogContent sx={{ minWidth: '280px', py: 1 }}>
                <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
                  Select the game you would like to challenge them to play:
                </Typography>
                <Select
                  value={challengeGameId}
                  onChange={(e) => setChallengeGameId(e.target.value)}
                  fullWidth
                  size="small"
                >
                  {catalogGames.map((g) => (
                    <MenuItem key={g.id} value={g.id}>
                      {g.name}
                    </MenuItem>
                  ))}
                </Select>
              </DialogContent>
              <DialogActions sx={{ p: 2 }}>
                <Button onClick={() => setChallengeTarget(null)} color="inherit">
                  Cancel
                </Button>
                <Button onClick={handleLaunchChallenge} variant="contained" color="primary">
                  Send Invite
                </Button>
              </DialogActions>
            </Dialog>
          </div>
        )}
      </div>
    </GamesMui>
  );
}
