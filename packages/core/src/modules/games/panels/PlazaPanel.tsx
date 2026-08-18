import { useEffect, useMemo, useState, useRef } from 'react';

import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Chip from '@mui/material/Chip';
import Typography from '@mui/material/Typography';
import Button from '@mui/material/Button';
import Dialog from '@mui/material/Dialog';
import DialogTitle from '@mui/material/DialogTitle';
import DialogContent from '@mui/material/DialogContent';
import DialogActions from '@mui/material/DialogActions';
import Avatar from '@mui/material/Avatar';
import Divider from '@mui/material/Divider';

import {
  dismissInvite,
  friendRequest,
  gamesJoinTable,
  profileGet,
  revealBoard,
  socialInvite,
  socialJoin,
  socialLeave,
  socialMove,
  socialSay,
  useGames,
} from '../game-ws';
import {
  fetchGamesCatalog,
  fetchStatus,
  fetchLeaderboard,
  type GameCatalogEntry,
} from '../games-api';
import { PlazaCanvas } from './PlazaCanvas';
import { GamesMui } from '../mui-theme';
import { toastsStore } from '../../../toasts';
import { mixer } from '../../audio/engine';
import { inputConstraints } from '../../audio/store';

const AVATARS = ['🙂', '😎', '🦸', '🧙', '🥷', '🤖', '👽', '🐱', '🦊', '🐼', '🐸', '🦄'];
const QUICK_EMOTES = ['👋', '🎉', '😂', '❤️', '👍', '🤔'];

interface SocialOccupant {
  account_id: string;
  name: string;
  avatar: string;
  x: number;
  y: number;
}

interface LeaderboardRecord {
  gameId: string;
  gameName: string;
  rating: number;
  tier: string;
  record: string;
}

interface ShopItem {
  id: string;
  name: string;
  emoji: string;
  cost: number;
  description: string;
}

const COSMETIC_ITEMS: ShopItem[] = [
  {
    id: 'crown',
    name: 'Royal Crown',
    emoji: '👑',
    cost: 100,
    description: 'Show off your supreme coding dominance.',
  },
  {
    id: 'wizard',
    name: 'Wizard Hat',
    emoji: '🧙',
    cost: 80,
    description: 'Cast compiler spells and fix bugs instantly.',
  },
  {
    id: 'goggles',
    name: 'Cyber Visor',
    emoji: '🕶️',
    cost: 50,
    description: 'A sleek, futuristic neon-lit accessory.',
  },
  {
    id: 'halo',
    name: 'Angelic Halo',
    emoji: '👼',
    cost: 120,
    description: 'An extremely rare glowing golden halo.',
  },
];

export function PlazaPanel() {
  const { social, accountId } = useGames();
  const [avatar, setAvatar] = useState(AVATARS[0]);
  const [text, setText] = useState('');
  const [games, setGames] = useState<GameCatalogEntry[]>([]);
  const [challengeGame, setChallengeGame] = useState('tictactoe');
  const [whoami, setWhoami] = useState<string | null>(null);

  // Selected player overlay state
  const [selectedPlayer, setSelectedPlayer] = useState<SocialOccupant | null>(null);
  const [actionMenuOpen, setActionMenuOpen] = useState(false);

  // Selected player's profile stats states
  const [showProfileCard, setShowProfileCard] = useState(false);
  const [playerTiers, setPlayerTiers] = useState<LeaderboardRecord[]>([]);
  const [loadingTiers, setLoadingTiers] = useState(false);

  // ── Wardrobe Shop States ──
  const [shopOpen, setShopOpen] = useState(false);
  const [tokens, setTokens] = useState<number>(() => {
    const saved = localStorage.getItem('arcade_tokens');
    return saved ? parseInt(saved, 10) : 350; // default starter tokens
  });
  const [unlockedCosmetics, setUnlockedCosmetics] = useState<string[]>(() => {
    const saved = localStorage.getItem('unlocked_cosmetics');
    return saved ? JSON.parse(saved) : [];
  });
  const [equippedAccessories, setEquippedAccessories] = useState<Record<string, string>>(() => {
    const saved = localStorage.getItem('equipped_accessories');
    return saved ? JSON.parse(saved) : {};
  });

  // ── Microphone states ──
  const [micActive, setMicActive] = useState(false);
  const [speakingPlayers, setSpeakingPlayers] = useState<Record<string, boolean>>({});
  const micStreamRef = useRef<MediaStream | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const animationRef = useRef<number>(0);
  const micCanvasRef = useRef<HTMLCanvasElement | null>(null);

  // Auto assign random accessories to other occupants for visual variety
  useEffect(() => {
    if (social.occupants.length > 0 && accountId) {
      setEquippedAccessories((prev) => {
        const next = { ...prev };
        social.occupants.forEach((o) => {
          if (o.account_id !== accountId && !next[o.account_id]) {
            // 30% chance to wear a random cosmetic
            if (Math.random() < 0.4) {
              const items = ['crown', 'wizard', 'goggles', 'halo'];
              next[o.account_id] = items[Math.floor(Math.random() * items.length)];
            }
          }
        });
        return next;
      });
    }
  }, [social.occupants, accountId]);

  useEffect(() => {
    fetchGamesCatalog().then((g) => {
      setGames(g);
      if (g[0]) setChallengeGame(g[0].id);
    });
    fetchStatus()
      .then((s) => setWhoami(s.signed_in ? s.display_name : null))
      .catch(() => setWhoami(null));
  }, []);

  useEffect(() => {
    if (social.profile?.avatar) setAvatar(social.profile.avatar);
    if (!social.joined) profileGet();
  }, [social.profile?.avatar, social.joined]);

  const others = useMemo(
    () => social.occupants.filter((o) => o.account_id !== accountId),
    [social.occupants, accountId],
  );

  // Load selected player's MMR stats
  const loadPlayerStats = async (player: SocialOccupant) => {
    setLoadingTiers(true);
    setPlayerTiers([]);
    try {
      const records: LeaderboardRecord[] = [];
      for (const g of games) {
        try {
          const lb = await fetchLeaderboard(g.id);
          const entry = lb.entries.find((e) => e.account_id === player.account_id);
          if (entry) {
            records.push({
              gameId: g.id,
              gameName: g.name,
              rating: entry.rating ?? 1200,
              tier: entry.tier || 'Gold',
              record: `${entry.wins}W/${entry.losses}L/${entry.draws}D`,
            });
          }
        } catch {
          // ignore
        }
      }
      setPlayerTiers(records);
    } catch (e) {
      console.error(e);
    } finally {
      setLoadingTiers(false);
    }
  };

  const send = () => {
    const t = text.trim();
    if (t) {
      socialSay(t);
      setText('');
    }
  };

  const acceptInvite = () => {
    if (social.invite) {
      gamesJoinTable(social.invite.table_id);
      revealBoard();
      dismissInvite();
    }
  };

  const handleSelectPlayer = (player: SocialOccupant) => {
    setSelectedPlayer(player);
    setActionMenuOpen(true);
  };

  const handleDirectMessage = () => {
    if (selectedPlayer) {
      setText(`@${selectedPlayer.name} `);
      setActionMenuOpen(false);
      const chatInput = document.getElementById('plaza-chat-input');
      if (chatInput) chatInput.focus();
    }
  };

  const handleSendFriendRequest = () => {
    if (selectedPlayer) {
      friendRequest(selectedPlayer.account_id);
      toastsStore.add('success', 'Central Plaza', `Friend request sent to ${selectedPlayer.name}!`);
      setActionMenuOpen(false);
    }
  };

  const handleChallengePlayer = () => {
    if (selectedPlayer && challengeGame) {
      socialInvite(selectedPlayer.account_id, challengeGame);
      revealBoard();
      toastsStore.add(
        'info',
        'Central Plaza',
        `Challenged ${selectedPlayer.name} to ${games.find((g) => g.id === challengeGame)?.name}!`,
      );
      setActionMenuOpen(false);
    }
  };

  const handleViewProfile = () => {
    if (selectedPlayer) {
      setActionMenuOpen(false);
      setShowProfileCard(true);
      loadPlayerStats(selectedPlayer);
    }
  };

  // ── Wardrobe Shop purchasing & equipping ──
  const handlePurchase = (item: ShopItem) => {
    if (tokens < item.cost) {
      toastsStore.add('error', 'Wardrobe Shop', 'Insufficient Arcade Tokens!');
      return;
    }
    const newTokens = tokens - item.cost;
    const newUnlocked = [...unlockedCosmetics, item.id];
    setTokens(newTokens);
    setUnlockedCosmetics(newUnlocked);
    localStorage.setItem('arcade_tokens', newTokens.toString());
    localStorage.setItem('unlocked_cosmetics', JSON.stringify(newUnlocked));
    toastsStore.add('success', 'Wardrobe Shop', `Successfully purchased ${item.name}!`);
  };

  const handleEquip = (itemId: string) => {
    if (!accountId) return;
    const nextAccessories = { ...equippedAccessories };
    if (nextAccessories[accountId] === itemId) {
      delete nextAccessories[accountId]; // unequip
      toastsStore.add('info', 'Wardrobe Shop', 'Unequipped accessory.');
    } else {
      nextAccessories[accountId] = itemId; // equip
      toastsStore.add('success', 'Wardrobe Shop', 'Accessory equipped!');
    }
    setEquippedAccessories(nextAccessories);
    localStorage.setItem('equipped_accessories', JSON.stringify(nextAccessories));
  };

  // ── Microphone audio analysis ──
  const toggleMicrophone = async () => {
    if (micActive) {
      // Disconnect microphone
      setMicActive(false);
      if (micStreamRef.current) {
        micStreamRef.current.getTracks().forEach((track) => track.stop());
        micStreamRef.current = null;
      }
      // Dropped, never closed: this is the mixer's shared context now, and
      // closing it would silence every other sound in the app the moment
      // somebody turned their microphone off in the Plaza.
      audioCtxRef.current = null;
      analyserRef.current = null;
      cancelAnimationFrame(animationRef.current);
      setSpeakingPlayers((prev) => {
        const next = { ...prev };
        if (accountId) delete next[accountId];
        return next;
      });
      toastsStore.add('info', 'Voice Channel', 'Microphone disabled.');
    } else {
      // Enable microphone
      try {
        // Through the mixer so the microphone the user chose is the one used.
        const stream = await navigator.mediaDevices.getUserMedia({ audio: inputConstraints() });
        micStreamRef.current = stream;
        setMicActive(true);

        // The mixer's shared context. This graph only drives a level meter — it
        // plays nothing — so it takes the context without claiming a strip.
        const audioCtx = mixer.getContext();
        audioCtxRef.current = audioCtx;

        const source = audioCtx.createMediaStreamSource(stream);
        const analyser = audioCtx.createAnalyser();
        analyser.fftSize = 64;
        analyserRef.current = analyser;
        source.connect(analyser);

        const bufferLength = analyser.frequencyBinCount;
        const dataArray = new Uint8Array(bufferLength);

        toastsStore.add('success', 'Voice Channel', 'Microphone connected!');

        // Render waveform & check speaking volume levels. Both teardown paths
        // (toggling the mic off, and unmount) cancelAnimationFrame this loop.
        const drawWaveform = () => {
          animationRef.current = requestAnimationFrame(drawWaveform);

          analyser.getByteFrequencyData(dataArray);

          // Calculate average volume
          let sum = 0;
          for (let i = 0; i < bufferLength; i++) {
            sum += dataArray[i];
          }
          const averageVol = sum / bufferLength;
          const isSpeaking = averageVol > 22; // speaking threshold

          if (accountId) {
            setSpeakingPlayers((prev) => {
              if (prev[accountId] === isSpeaking) return prev;
              return { ...prev, [accountId]: isSpeaking };
            });
          }

          // Draw live visualizer on offscreen canvas
          const canvas = micCanvasRef.current;
          if (canvas) {
            const ctx = canvas.getContext('2d')!;
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            ctx.strokeStyle = '#2ed573';
            ctx.lineWidth = 2.5;
            ctx.beginPath();
            const sliceWidth = canvas.width / bufferLength;
            let x = 0;
            for (let i = 0; i < bufferLength; i++) {
              const v = dataArray[i] / 255.0;
              const y = canvas.height / 2 + (v - 0.5) * canvas.height * 0.8;
              if (i === 0) {
                ctx.moveTo(x, y);
              } else {
                ctx.lineTo(x, y);
              }
              x += sliceWidth;
            }
            ctx.stroke();
          }
        };

        drawWaveform();
      } catch (err) {
        console.error(err);
        toastsStore.add('error', 'Voice Channel', 'Microphone access denied or unavailable.');
      }
    }
  };

  useEffect(() => {
    return () => {
      // Cleanup audio context on unmount
      if (micStreamRef.current) {
        micStreamRef.current.getTracks().forEach((track) => track.stop());
      }
      cancelAnimationFrame(animationRef.current);
    };
  }, []);

  const renderPlayerAvatar = (avatarStr: string, size = '3.5rem') => {
    if (
      avatarStr.startsWith('data:image/') ||
      avatarStr.startsWith('http://') ||
      avatarStr.startsWith('https://')
    ) {
      return (
        <img
          src={avatarStr}
          alt="Avatar"
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
          fontSize: size === '3.5rem' ? '1.8rem' : '1.2rem',
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
      <div className="games-plaza">
        {/* Header bar */}
        <div
          className="games-plaza-topbar"
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '0.6rem 0.8rem',
          }}
        >
          <strong style={{ fontSize: '0.95rem' }}>🌐 Central Plaza (3D WebGL)</strong>
          <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
            <Button
              size="small"
              variant="outlined"
              sx={{ textTransform: 'none', py: 0.1, px: 1, fontSize: '0.72rem' }}
              onClick={() => setShopOpen(true)}
            >
              🛍️ Wardrobe Shop
            </Button>
            <span style={{ color: 'var(--text-dim)', fontSize: '0.72rem' }}>
              {social.roster.length} online
            </span>
            {social.joined && (
              <button
                type="button"
                onClick={() => socialLeave()}
                style={{ padding: '0.2rem 0.6rem', fontSize: '0.75rem' }}
              >
                Leave
              </button>
            )}
          </div>
        </div>

        {!social.joined ? (
          <form
            className="games-plaza-join"
            onSubmit={(e) => {
              e.preventDefault();
              socialJoin(avatar);
            }}
          >
            <div style={{ color: 'var(--text-dim)', fontSize: '0.78rem' }}>
              {whoami ? (
                <>
                  Step into the lobby as <strong>{whoami}</strong> — pick an avatar and say hi. Real
                  people, live rooms, speech bubbles.
                </>
              ) : (
                <>
                  Step into the lobby under your account name — pick an avatar and say hi. Real
                  people, live rooms, speech bubbles. (Sign in in Games to use your GitHub/Google
                  username.)
                </>
              )}
            </div>
            <div
              style={{ display: 'flex', gap: '0.35rem', alignItems: 'center', flexWrap: 'wrap' }}
            >
              <div className="games-avatar-picker">
                {AVATARS.map((a) => (
                  <button
                    key={a}
                    type="button"
                    className={`games-avatar-opt ${a === avatar ? 'active' : ''}`}
                    onClick={() => setAvatar(a)}
                  >
                    {a}
                  </button>
                ))}
              </div>
              <button type="submit">Enter the Plaza →</button>
            </div>
          </form>
        ) : (
          <>
            {social.invite && (
              <div className="games-plaza-invite">
                <span>
                  ⚔️ <strong>{social.invite.from_name}</strong> challenged you to{' '}
                  <strong>{social.invite.game_name}</strong>
                </span>
                <span style={{ marginLeft: 'auto', display: 'flex', gap: '0.35rem' }}>
                  <button type="button" className="games-play-btn" onClick={acceptInvite}>
                    Join
                  </button>
                  <button type="button" onClick={() => dismissInvite()}>
                    Dismiss
                  </button>
                </span>
              </div>
            )}

            {/* 3D Canvas wrapper */}
            <div
              className="games-plaza-floor"
              style={{
                height: '360px',
                overflow: 'hidden',
                background: '#101216',
                borderBottom: '1px solid var(--border)',
              }}
            >
              <PlazaCanvas
                occupants={social.occupants}
                bubbles={social.bubbles}
                accountId={accountId}
                roomName="Central Plaza"
                onMove={socialMove}
                onSelectPlayer={handleSelectPlayer}
                playerAccessories={equippedAccessories}
                speakingPlayers={speakingPlayers}
              />
            </div>

            {/* Say + Mic + Quick Emotes */}
            <div
              className="games-plaza-saybar"
              style={{
                display: 'flex',
                flexDirection: 'column',
                gap: '0.5rem',
                padding: '0.6rem 0.8rem',
              }}
            >
              <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', width: '100%' }}>
                {/* Voice Mic Toggle */}
                <button
                  type="button"
                  onClick={toggleMicrophone}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    background: micActive ? '#2e7d32' : 'rgba(255,255,255,0.06)',
                    color: '#fff',
                    border: 'none',
                    borderRadius: '4px',
                    padding: '0.4rem 0.8rem',
                    cursor: 'pointer',
                    fontSize: '0.78rem',
                    gap: '0.3rem',
                  }}
                  title={micActive ? 'Mute Microphone' : 'Enable Microphone (Voice Chat)'}
                >
                  {micActive ? '🎙️ Mic: ON' : '🎤 Mic: OFF'}
                </button>

                {micActive && (
                  <canvas
                    ref={micCanvasRef}
                    width={80}
                    height={24}
                    style={{
                      border: '1px solid rgba(255,255,255,0.1)',
                      borderRadius: '4px',
                      background: 'rgba(0,0,0,0.2)',
                    }}
                  />
                )}

                <form
                  style={{ display: 'flex', gap: '0.35rem', flex: 1 }}
                  onSubmit={(e) => {
                    e.preventDefault();
                    send();
                  }}
                >
                  <input
                    id="plaza-chat-input"
                    value={text}
                    onChange={(e) => setText(e.target.value)}
                    placeholder="Say something in Central Plaza… (Double-click floor to Run)"
                    style={{ flex: 1 }}
                  />
                  <button type="submit" disabled={!text.trim()}>
                    💬 Say
                  </button>
                </form>
              </div>

              <div
                style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}
              >
                <div className="games-plaza-emotes" style={{ margin: 0 }}>
                  {QUICK_EMOTES.map((em) => (
                    <button
                      key={em}
                      type="button"
                      onClick={() => socialSay(em, true)}
                      title="emote"
                    >
                      {em}
                    </button>
                  ))}
                </div>
                {accountId && equippedAccessories[accountId] && (
                  <Typography variant="caption" color="text.secondary">
                    Equipped cosmetic:{' '}
                    <span style={{ fontWeight: 700 }}>
                      {COSMETIC_ITEMS.find((i) => i.id === equippedAccessories[accountId])?.name}
                    </span>
                  </Typography>
                )}
              </div>
            </div>

            {/* Who's in this room + quick actions */}
            <div className="games-plaza-here" style={{ flex: 1, overflowY: 'auto' }}>
              <div className="games-plaza-here-head">
                <span style={{ color: 'var(--text-dim)' }}>In the Plaza</span>
                <label
                  style={{ marginLeft: 'auto', fontSize: '0.72rem', color: 'var(--text-dim)' }}
                >
                  challenge game:{' '}
                  <select value={challengeGame} onChange={(e) => setChallengeGame(e.target.value)}>
                    {games.map((g) => (
                      <option key={g.id} value={g.id}>
                        {g.name}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              {others.length === 0 ? (
                <div style={{ color: 'var(--text-dim)', fontSize: '0.78rem', padding: '0.5rem 0' }}>
                  Nobody else here yet. Wait for other developers to join!
                </div>
              ) : (
                <ul className="games-plaza-people">
                  {others.map((o) => (
                    <li
                      key={o.account_id}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        padding: '0.3rem 0.6rem',
                        borderBottom: '1px solid var(--border)',
                      }}
                    >
                      <span
                        className="games-plaza-person"
                        style={{
                          cursor: 'pointer',
                          display: 'flex',
                          alignItems: 'center',
                          gap: '0.4rem',
                        }}
                        onClick={() => handleSelectPlayer(o)}
                      >
                        {renderPlayerAvatar(o.avatar, '1.6rem')}
                        <strong>{o.name}</strong>
                        {equippedAccessories[o.account_id] && (
                          <span>
                            {
                              COSMETIC_ITEMS.find((i) => i.id === equippedAccessories[o.account_id])
                                ?.emoji
                            }
                          </span>
                        )}
                        {speakingPlayers[o.account_id] && (
                          <span
                            style={{ color: '#2ed573', fontSize: '0.75rem', marginLeft: '0.2rem' }}
                          >
                            🔊
                          </span>
                        )}
                      </span>
                      <div style={{ display: 'flex', gap: '0.3rem' }}>
                        <Button
                          size="small"
                          variant="outlined"
                          sx={{ textTransform: 'none', py: 0.1, px: 0.8, fontSize: '0.72rem' }}
                          onClick={() => handleSelectPlayer(o)}
                        >
                          Interact
                        </Button>
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </>
        )}

        {/* ── Wardrobe Shop Modal ── */}
        <Dialog open={shopOpen} onClose={() => setShopOpen(false)} maxWidth="sm" fullWidth>
          <DialogTitle
            sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}
          >
            <span style={{ fontWeight: 800 }}>🛍️ Wardrobe & Cosmetic Shop</span>
            <Chip label={`🪙 ${tokens} Tokens`} color="primary" sx={{ fontWeight: 800 }} />
          </DialogTitle>
          <Divider />
          <DialogContent sx={{ py: 3 }}>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
              Earn Arcade Tokens by competing in games and writing top-tier agents. Spend them below
              to buy exclusive cosmetics:
            </Typography>
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))',
                gap: '1rem',
              }}
            >
              {COSMETIC_ITEMS.map((item) => {
                const isUnlocked = unlockedCosmetics.includes(item.id);
                const isEquipped = accountId && equippedAccessories[accountId] === item.id;
                return (
                  <Card
                    variant="outlined"
                    sx={{
                      height: '100%',
                      display: 'flex',
                      flexDirection: 'column',
                      justifyContent: 'space-between',
                    }}
                  >
                    <CardContent sx={{ pb: 1 }}>
                      <div
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'space-between',
                          marginBottom: '0.5rem',
                        }}
                      >
                        <Typography variant="subtitle1" sx={{ fontWeight: 800 }}>
                          {item.emoji} {item.name}
                        </Typography>
                        {!isUnlocked && (
                          <Chip
                            size="small"
                            label={`🪙 ${item.cost}`}
                            color="secondary"
                            sx={{ fontWeight: 700 }}
                          />
                        )}
                      </div>
                      <Typography
                        variant="body2"
                        color="text.secondary"
                        sx={{ fontSize: '0.75rem', lineHeight: 1.4 }}
                      >
                        {item.description}
                      </Typography>
                    </CardContent>
                    <Divider />
                    <div
                      style={{
                        padding: '0.6rem 0.8rem',
                        display: 'flex',
                        justifyContent: 'flex-end',
                        background: 'rgba(255,255,255,0.01)',
                      }}
                    >
                      {isUnlocked ? (
                        <Button
                          size="small"
                          variant={isEquipped ? 'contained' : 'outlined'}
                          color={isEquipped ? 'success' : 'primary'}
                          onClick={() => handleEquip(item.id)}
                          fullWidth
                        >
                          {isEquipped ? 'Equipped 👼' : 'Equip Accessory'}
                        </Button>
                      ) : (
                        <Button
                          size="small"
                          variant="contained"
                          color="primary"
                          onClick={() => handlePurchase(item)}
                          disabled={tokens < item.cost}
                          fullWidth
                        >
                          Buy Item
                        </Button>
                      )}
                    </div>
                  </Card>
                );
              })}
            </div>
          </DialogContent>
          <DialogActions>
            <Button onClick={() => setShopOpen(false)} color="inherit">
              Close Shop
            </Button>
          </DialogActions>
        </Dialog>

        {/* ── Interactive Player Actions Modal ── */}
        <Dialog open={actionMenuOpen} onClose={() => setActionMenuOpen(false)}>
          {selectedPlayer && (
            <>
              <DialogTitle
                sx={{ fontWeight: 800, display: 'flex', alignItems: 'center', gap: '0.8rem' }}
              >
                {renderPlayerAvatar(selectedPlayer.avatar, '2.4rem')}
                <div>
                  <Typography variant="subtitle1" sx={{ fontWeight: 800 }}>
                    {selectedPlayer.name}
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    ID: {selectedPlayer.account_id}
                  </Typography>
                </div>
              </DialogTitle>
              <Divider />
              <DialogContent
                sx={{
                  minWidth: '280px',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '0.8rem',
                  py: 2,
                }}
              >
                <Button
                  variant="contained"
                  color="primary"
                  onClick={handleChallengePlayer}
                  fullWidth
                >
                  ⚔️ Challenge to {games.find((g) => g.id === challengeGame)?.name ?? challengeGame}
                </Button>
                <Button variant="outlined" color="primary" onClick={handleViewProfile} fullWidth>
                  👤 View Profile Card
                </Button>
                <Button
                  variant="outlined"
                  color="inherit"
                  onClick={handleSendFriendRequest}
                  fullWidth
                >
                  🤝 Send Friend Request
                </Button>
                <Button variant="outlined" color="inherit" onClick={handleDirectMessage} fullWidth>
                  💬 Message (Mention in Chat)
                </Button>
              </DialogContent>
              <DialogActions>
                <Button onClick={() => setActionMenuOpen(false)} color="inherit">
                  Close
                </Button>
              </DialogActions>
            </>
          )}
        </Dialog>

        {/* ── Player Profile Card Modal ── */}
        <Dialog
          open={showProfileCard}
          onClose={() => setShowProfileCard(false)}
          maxWidth="sm"
          fullWidth
        >
          {selectedPlayer && (
            <>
              <DialogTitle
                sx={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '1rem',
                  background:
                    'linear-gradient(135deg, rgba(38,42,50,0.9) 0%, rgba(20,22,26,0.9) 100%)',
                  p: 3,
                }}
              >
                {renderPlayerAvatar(selectedPlayer.avatar, '3.5rem')}
                <div>
                  <Typography variant="h6" sx={{ fontWeight: 800, color: '#fff' }}>
                    {selectedPlayer.name}
                  </Typography>
                  <Typography variant="caption" sx={{ color: 'rgba(255,255,255,0.6)' }}>
                    Arcade Developer Profile
                  </Typography>
                </div>
              </DialogTitle>
              <DialogContent
                sx={{ py: 3, px: 3, display: 'flex', flexDirection: 'column', gap: '1.2rem' }}
              >
                <div>
                  <Typography
                    variant="caption"
                    sx={{ display: 'block', mb: 0.5, fontWeight: 700, color: 'text.secondary' }}
                  >
                    ABOUT DEVELOPER
                  </Typography>
                  <Typography
                    variant="body2"
                    sx={{ fontStyle: 'italic', color: 'text.secondary', lineHeight: 1.5 }}
                  >
                    "A fellow software engineer and game agent creator hanging out in the Central
                    Plaza. Let's play a game!"
                  </Typography>
                </div>

                <Divider />

                <div>
                  <Typography
                    variant="caption"
                    sx={{ display: 'block', mb: 0.8, fontWeight: 700, color: 'text.secondary' }}
                  >
                    🏆 RANKED SKILL TIERS
                  </Typography>

                  {loadingTiers ? (
                    <Typography variant="body2" color="text.secondary">
                      Loading ratings from leaderboards…
                    </Typography>
                  ) : playerTiers.length === 0 ? (
                    <Typography variant="body2" color="text.secondary">
                      No rated games recorded in leaderboards.
                    </Typography>
                  ) : (
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.6rem' }}>
                      {playerTiers.map((tier) => (
                        <div
                          key={tier.gameId}
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'space-between',
                            background: 'rgba(255,255,255,0.02)',
                            border: '1px solid var(--border)',
                            borderRadius: '4px',
                            padding: '0.4rem 0.6rem',
                          }}
                        >
                          <div>
                            <Typography sx={{ fontWeight: 800, fontSize: '0.75rem' }}>
                              {tier.gameName}
                            </Typography>
                            <Typography
                              variant="caption"
                              color="text.secondary"
                              sx={{ fontSize: '0.68rem' }}
                            >
                              Rating: {tier.rating}
                            </Typography>
                          </div>
                          <Chip
                            size="small"
                            color="primary"
                            variant="outlined"
                            label={tier.tier}
                            sx={{ height: 18, fontSize: '0.65rem', fontWeight: 700 }}
                          />
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </DialogContent>
              <DialogActions sx={{ p: 2 }}>
                <Button onClick={() => setShowProfileCard(false)} color="inherit">
                  Close
                </Button>
              </DialogActions>
            </>
          )}
        </Dialog>
      </div>
    </GamesMui>
  );
}
