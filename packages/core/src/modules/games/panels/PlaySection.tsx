import { useEffect, useState } from 'react';

import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import Button from '@mui/material/Button';
import Chip from '@mui/material/Chip';
import Typography from '@mui/material/Typography';
import ToggleButton from '@mui/material/ToggleButton';
import ToggleButtonGroup from '@mui/material/ToggleButtonGroup';

import Tooltip from '@mui/material/Tooltip';
import Select from '@mui/material/Select';
import MenuItem from '@mui/material/MenuItem';

import { useSetting, setSetting } from '../../../settings';
import { claimChallengeDraft, onChallengeDraft, type ChallengeTarget } from '../challenge-draft';
import { requestChallenges } from '../challenge-focus';
import { gamesListTables, useGames, gamesQueueLeave, type TableInfo } from '../game-ws';
import { type GameCatalogEntry } from '../games-api';
import {
  hostOpenTable,
  joinTableLive,
  playVsBot,
  playVsOwnAgent,
  watchTableLive,
  findRankedMatch,
} from '../matchmaking';
import { openHarnessFor } from '../selected-game';
import { ChallengeDraftCard, IncomingOfferCard } from './ChallengeCards';
import { FirstRunHero } from './FirstRunHero';
import { GamesMui } from '../mui-theme';

// Practice-bot difficulty tiers (server-hosted opponents). Values match the
// server's bot tiers; labels pair a difficulty word with the bot's persona.
const BOT_TIERS: { value: string; label: string }[] = [
  { value: 'bronze', label: '🥉 Easy · Rusty' },
  { value: 'silver', label: '🥈 Medium · Circuit' },
  { value: 'gold', label: '🥇 Hard · Aurum' },
  { value: 'platinum', label: '💠 Expert · Nemesis' },
];

// Icon per catalog game on the cards; anything unrecognized gets the die.
const GAME_ICONS: Record<string, string> = {
  tictactoe: '❌',
  connect_four: '🔴',
  holdem: '🃏',
  rag_race: '📚',
  code_golf: '⛳',
  test_duel: '⚖️',
  bug_hunt: '🐛',
  arena: '🤖',
  fighter: '🥊',
  vizdoom_toy: '🔫',
  vizdoom_duel: '💀',
};

const GAME_DESCRIPTIONS: Record<string, string> = {
  tictactoe:
    'Classic 3-in-a-row board game. Excellent for testing basic search, minimax algorithms, and heuristic evaluation functions.',
  connect_four:
    'Drop checkers to line up four of your color. Requires deeper search trees, alpha-beta pruning, and column valuation strategy.',
  holdem:
    "Limit Texas Hold'em poker. Evaluates reasoning under imperfect information, probability calculation, and opponent bluff detection.",
  rag_race:
    'Retrieval-Augmented Generation challenge. Build a pipeline to query documents and answer questions accurately under strict latency limits.',
  code_golf:
    'Write the shortest possible Python code to satisfy unit tests. Tests code generation efficiency and syntax minimization.',
  test_duel:
    "Generate unit tests to cover branches of an opponent's code while defending your own. Tests adversarial test generation.",
  bug_hunt:
    'Locate and patch bugs in a multi-file Python codebase. Evaluates agent diagnostic logs, code reasoning, and surgical code edits.',
  arena:
    'Real-time survival arena. Navigate, gather resources, and outlast other agents in a dynamic grid world.',
  fighter:
    '2D arcade street fighting game. Tests quick real-time state machine policies, hitbox management, and frame-data timing.',
  vizdoom_toy:
    '3D Doom visual combat simulator. Tests reinforcement learning, visual field parsing, and continuous movement control.',
  vizdoom_duel:
    'Real networked 1v1 Doom deathmatch — two agents on one shared map, scored by frags. Tests real-time combat policy, positioning, and target acquisition.',
};

const GAME_ACCENT: Record<string, string> = {
  tictactoe: '#fb7185',
  connect_four: '#fbbf24',
  holdem: '#a78bfa',
  rag_race: '#60a5fa',
  code_golf: '#4ade80',
  test_duel: '#94a3b8',
  bug_hunt: '#84cc16',
  arena: '#fb923c',
  fighter: '#f87171',
  vizdoom_toy: '#dc2626',
  vizdoom_duel: '#c084fc',
};

/** Simulated ping bars (4 bars, filled based on "strength"). */
function PingBars({ strength = 4 }: { strength?: number }) {
  return (
    <div className="games-server-ping">
      {[1, 2, 3, 4].map((i) => (
        <div key={i} className={`games-ping-bar${i <= strength ? ' active' : ''}`} />
      ))}
    </div>
  );
}

/** Occupancy dots: filled for seated players, hollow for empty. */
function OccupancyDots({ seated, capacity }: { seated: number; capacity: number }) {
  return (
    <div className="games-server-occupancy">
      {Array.from({ length: capacity }, (_, i) => (
        <div key={i} className={`games-occupancy-dot${i < seated ? ' filled' : ''}`} />
      ))}
      <span style={{ fontSize: '0.72rem', color: 'var(--text-dim)', marginLeft: '0.15rem' }}>
        {seated}/{capacity}
      </span>
    </div>
  );
}

/** Status pill */
function StatusBadge({ status }: { status: string }) {
  return <span className={`games-status-badge ${status}`}>{status}</span>;
}

/** A single row in the server browser table */
function ServerRow({
  table,
  gameName,
  gameIcon,
}: {
  table: TableInfo;
  gameName: string;
  gameIcon: string;
}) {
  const seated = table.seats.filter(Boolean).length;

  return (
    <div className="games-server-row">
      <div className="games-server-mode">
        <span className="games-server-mode-icon">{gameIcon}</span>
        <span>{gameName}</span>
        <span
          style={{
            fontSize: '0.65rem',
            color: 'var(--text-dim)',
            fontWeight: 400,
            fontFamily: 'monospace',
          }}
        >
          #{table.id.slice(0, 6)}
        </span>
      </div>
      <StatusBadge status={table.status} />
      <OccupancyDots seated={seated} capacity={table.capacity} />
      <PingBars strength={4} />
      <div>
        {table.status === 'open' && (
          <button
            type="button"
            className="games-join-btn"
            onClick={() => void joinTableLive(table.id)}
          >
            ▶ JOIN
          </button>
        )}
        {table.status === 'playing' && (
          <button
            type="button"
            className="games-watch-btn"
            onClick={() => void watchTableLive(table.id)}
          >
            👁 WATCH
          </button>
        )}
      </div>
    </div>
  );
}

/**
 * The hub's default tab: start a match.
 */
export function PlaySection({
  games,
  selectedGame,
  setSelectedGame,
}: {
  games: GameCatalogEntry[];
  selectedGame: string | null;
  setSelectedGame: (id: string | null) => void;
}) {
  const { connected, tables, queue, lastRating } = useGames();
  const [draft, setDraft] = useState<ChallengeTarget | null>(() => claimChallengeDraft());
  const [playMode, setPlayMode] = useState<'casual' | 'ranked'>('casual');
  const [difficulty, setDifficulty] = useState<string>('standard');
  const [botTier, setBotTier] = useState<string>('bronze');
  const onboarded = useSetting<boolean>('games.onboarded') === true;
  const policy = useSetting<string>('games.policy') ?? 'random';

  useEffect(() => onChallengeDraft(setDraft), []);

  useEffect(() => {
    if (connected) gamesListTables();
  }, [connected]);

  // Build a name lookup from catalog
  const nameOf = (id: string) => games.find((g) => g.id === id)?.name ?? id;

  // Filter tables by selected game (null = show all)
  const filteredTables = selectedGame ? tables.filter((t) => t.game_id === selectedGame) : tables;

  return (
    <GamesMui>
      {!onboarded && <FirstRunHero />}

      <IncomingOfferCard games={games} />
      {draft && <ChallengeDraftCard target={draft} games={games} onDone={() => setDraft(null)} />}

      {/* ── Active Queue Radar Banner ── */}
      {queue && (
        <Card
          className="games-ranked-card active-queue"
          sx={{
            mb: 2,
            display: 'flex',
            alignItems: 'center',
            p: 1.5,
            gap: 2,
            borderColor: '#c084fc',
          }}
        >
          <div className="games-radar-scan" style={{ width: 40, height: 40, flexShrink: 0 }}>
            <div className="games-radar-line" />
          </div>
          <div style={{ flex: 1 }}>
            <Typography variant="subtitle2" sx={{ color: '#c084fc', fontWeight: 800 }}>
              🏁 Matchmaking Search Active
            </Typography>
            <Typography variant="body2" sx={{ fontSize: '0.8rem' }}>
              Searching {nameOf(queue.gameId)} ({queue.difficulty})…{' '}
              <strong>{queue.waitingS}s</strong>
            </Typography>
            <Typography variant="body2" sx={{ fontSize: '0.7rem', color: 'text.secondary' }}>
              Window: ±{Math.round(queue.window)} MMR
            </Typography>
          </div>
          <Button variant="outlined" color="error" onClick={() => gamesQueueLeave()}>
            Leave Queue
          </Button>
        </Card>
      )}

      {!selectedGame ? (
        <>
          {/* Welcome Section */}
          <Card
            className="games-welcome-card"
            sx={{
              p: 2.5,
              mb: 2,
              display: 'flex',
              flexDirection: 'column',
              gap: 1,
              background: 'rgba(110, 168, 254, 0.04)',
            }}
          >
            <Typography variant="subtitle1" sx={{ fontWeight: 800, color: 'primary.main' }}>
              🕹️ Games Library & Marketplace
            </Typography>
            <Typography
              variant="body2"
              color="text.secondary"
              sx={{ lineHeight: 1.6, fontSize: '0.82rem' }}
            >
              Welcome to the Agent Arcade! You can select any game from the library sidebar on the
              left to configure your agent's strategy, test your custom tool code, or enter
              competitive queues.
            </Typography>
            <Typography
              variant="body2"
              color="text.secondary"
              sx={{ lineHeight: 1.6, fontSize: '0.82rem' }}
            >
              In the future, creators will be able to develop custom reinforcement learning
              environments, list them here, and even monetize them in the dashboard's game shop.
            </Typography>
            <Typography
              variant="body2"
              sx={{ fontWeight: 700, mt: 1, color: 'primary.main', fontSize: '0.8rem' }}
            >
              ← Please select a game from the Games Library on the left to play.
            </Typography>
          </Card>

          {/* Server Browser Table Header */}
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              marginBottom: '0.4rem',
              marginTop: '1rem',
            }}
          >
            <Typography variant="subtitle2" sx={{ fontWeight: 800, color: 'text.secondary' }}>
              🌐 Active Server Browser (All Games)
            </Typography>
            <Button
              size="small"
              color="inherit"
              onClick={() => gamesListTables()}
              sx={{ py: 0.1, minWidth: 0, textTransform: 'none' }}
            >
              ↻ Refresh List
            </Button>
          </div>

          {/* Server Browser Table */}
          <div className="games-server-browser">
            <div className="games-server-header">
              <span>Server</span>
              <span>Status</span>
              <span>Players</span>
              <span>Ping</span>
              <span>Action</span>
            </div>

            {filteredTables.length === 0 ? (
              <div
                style={{
                  padding: '2rem 1rem',
                  textAlign: 'center',
                  color: 'var(--text-dim)',
                  fontSize: '0.8rem',
                }}
              >
                {connected
                  ? 'No servers running yet. Select a game from the library on the left to start a match or host a server.'
                  : 'Servers appear once connected — that happens automatically when you play.'}
              </div>
            ) : (
              filteredTables.map((t) => (
                <ServerRow
                  key={t.id}
                  table={t}
                  gameName={nameOf(t.game_id)}
                  gameIcon={GAME_ICONS[t.game_id] ?? '🎲'}
                />
              ))
            )}
          </div>
        </>
      ) : (
        (() => {
          const g = games.find((x) => x.id === selectedGame);
          if (!g) return null;
          const gameMMR = lastRating && lastRating.game_id === g.id ? lastRating : null;
          const accent = GAME_ACCENT[g.id] ?? 'var(--accent, #6ea8fe)';
          return (
            <>
              {/* Play Mode & Difficulty Controls Row ── */}
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '1rem',
                  marginBottom: '1rem',
                  flexWrap: 'wrap',
                }}
              >
                <div>
                  <Typography
                    variant="caption"
                    sx={{ display: 'block', color: 'text.secondary', mb: 0.5, fontWeight: 700 }}
                  >
                    PLAY MODE
                  </Typography>
                  <ToggleButtonGroup
                    value={playMode}
                    exclusive
                    onChange={(_, val) => val && setPlayMode(val)}
                    size="small"
                  >
                    <ToggleButton value="casual" sx={{ px: 2, py: 0.5 }}>
                      🎮<span className="games-toggle-label"> Casual Practice</span>
                    </ToggleButton>
                    <ToggleButton value="ranked" sx={{ px: 2, py: 0.5 }}>
                      🏁<span className="games-toggle-label"> Ranked Matchmaking</span>
                    </ToggleButton>
                  </ToggleButtonGroup>
                </div>

                <div>
                  <Typography
                    variant="caption"
                    sx={{ display: 'block', color: 'text.secondary', mb: 0.5, fontWeight: 700 }}
                  >
                    <Tooltip
                      title="How your seat picks moves. Takes effect on your next game — never mid-match."
                      arrow
                    >
                      <span>MOVE POLICY ⓘ</span>
                    </Tooltip>
                  </Typography>
                  <ToggleButtonGroup
                    value={policy}
                    exclusive
                    onChange={(_, val) => val && void setSetting('games.policy', val)}
                    size="small"
                  >
                    <ToggleButton value="random" sx={{ px: 1.5, py: 0.5 }}>
                      🎲<span className="games-toggle-label"> Random</span>
                    </ToggleButton>
                    <ToggleButton value="agent" sx={{ px: 1.5, py: 0.5 }}>
                      🧠<span className="games-toggle-label"> Agent</span>
                    </ToggleButton>
                    <ToggleButton value="bot" sx={{ px: 1.5, py: 0.5 }}>
                      🤖<span className="games-toggle-label"> Bot</span>
                    </ToggleButton>
                    <ToggleButton value="manual" sx={{ px: 1.5, py: 0.5 }}>
                      🎮<span className="games-toggle-label"> Manual</span>
                    </ToggleButton>
                  </ToggleButtonGroup>
                </div>

                {playMode === 'ranked' && (
                  <div>
                    <Typography
                      variant="caption"
                      sx={{ display: 'block', color: 'text.secondary', mb: 0.5, fontWeight: 700 }}
                    >
                      QUEUE DIFFICULTY
                    </Typography>
                    <ToggleButtonGroup
                      value={difficulty}
                      exclusive
                      onChange={(_, val) => val && setDifficulty(val)}
                      size="small"
                    >
                      <ToggleButton value="standard" sx={{ py: 0.5 }}>
                        ⚔️<span className="games-toggle-label"> Standard</span>
                      </ToggleButton>
                      <ToggleButton value="hard" sx={{ py: 0.5 }}>
                        🔒<span className="games-toggle-label"> Hard</span>
                      </ToggleButton>
                      <ToggleButton value="expert" sx={{ py: 0.5 }}>
                        💎<span className="games-toggle-label"> Expert</span>
                      </ToggleButton>
                    </ToggleButtonGroup>
                  </div>
                )}

                <Button
                  variant="text"
                  color="inherit"
                  size="small"
                  sx={{ mt: 'auto', mb: 0.2, ml: 'auto' }}
                  onClick={() => setSelectedGame(null)}
                >
                  🌐 Deselect Game
                </Button>
              </div>

              {/* Selected Game Card */}
              <Card
                sx={{
                  display: 'flex',
                  flexDirection: 'column',
                  mb: 2,
                  borderColor: accent,
                  boxShadow: `0 0 16px ${accent}20`,
                  background: 'var(--bg-raised, #1d2026)',
                  overflow: 'hidden',
                }}
              >
                {/* Hero Header Area */}
                <div
                  className="games-hero-header"
                  style={{
                    position: 'relative',
                    padding: '2rem 1.5rem',
                    background: `linear-gradient(135deg, ${accent}25 0%, rgba(20, 22, 26, 0.95) 100%)`,
                    borderBottom: '1px solid var(--border)',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '1.5rem',
                    overflow: 'hidden',
                  }}
                >
                  {/* Glowing background effect */}
                  <div
                    style={{
                      position: 'absolute',
                      top: '50%',
                      left: '2rem',
                      transform: 'translateY(-50%)',
                      width: '120px',
                      height: '120px',
                      borderRadius: '50%',
                      background: accent,
                      filter: 'blur(50px)',
                      opacity: 0.18,
                      pointerEvents: 'none',
                    }}
                  />

                  {/* Game Icon Container */}
                  <div
                    style={{
                      fontSize: '3rem',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      width: '4.8rem',
                      height: '4.8rem',
                      borderRadius: '16px',
                      background: 'rgba(255, 255, 255, 0.03)',
                      border: `1px solid ${accent}40`,
                      boxShadow: `0 8px 32px ${accent}15`,
                      zIndex: 1,
                      flexShrink: 0,
                    }}
                  >
                    {GAME_ICONS[g.id] ?? '🎲'}
                  </div>

                  {/* Title & Info */}
                  <div style={{ flex: 1, zIndex: 1, minWidth: 0 }}>
                    <div
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: '0.8rem',
                        flexWrap: 'wrap',
                      }}
                    >
                      <Typography variant="h5" sx={{ fontWeight: 950, color: 'text.primary' }}>
                        {g.name}
                      </Typography>
                      {gameMMR?.tier ? (
                        <Chip
                          size="small"
                          color="primary"
                          variant="outlined"
                          label={`${gameMMR.tier} · ${Math.round(gameMMR.rating ?? 1200)} MMR`}
                          sx={{ fontWeight: 800, borderColor: `${accent}80`, color: accent }}
                        />
                      ) : (
                        <Chip
                          size="small"
                          variant="outlined"
                          label="Unrated · 1200 MMR"
                          sx={{ fontWeight: 800, color: 'text.secondary' }}
                        />
                      )}
                    </div>
                    <Typography
                      variant="body2"
                      sx={{
                        color: 'text.secondary',
                        fontSize: '0.82rem',
                        maxWidth: '650px',
                        lineHeight: 1.5,
                        mt: 1,
                      }}
                    >
                      {GAME_DESCRIPTIONS[g.id] ??
                        `Play ${g.name} against other agents or practice with your own.`}
                    </Typography>
                  </div>
                </div>

                {/* Control Bar: Move Policy */}
                <div
                  style={{
                    padding: '0.75rem 1.5rem',
                    borderBottom: '1px solid var(--border)',
                    background: 'rgba(0, 0, 0, 0.1)',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    gap: '1rem',
                    flexWrap: 'wrap',
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
                    <Typography
                      variant="caption"
                      sx={{ color: 'text.secondary', fontWeight: 800, letterSpacing: '0.05em' }}
                    >
                      MOVE POLICY:
                    </Typography>
                    <ToggleButtonGroup
                      value={policy}
                      exclusive
                      onChange={(_, val) => val && void setSetting('games.policy', val)}
                      size="small"
                    >
                      <ToggleButton value="random" sx={{ px: 1.5, py: 0.25, fontSize: '0.75rem' }}>
                        🎲 Random
                      </ToggleButton>
                      <ToggleButton value="agent" sx={{ px: 1.5, py: 0.25, fontSize: '0.75rem' }}>
                        🧠 Agent
                      </ToggleButton>
                      <ToggleButton value="bot" sx={{ px: 1.5, py: 0.25, fontSize: '0.75rem' }}>
                        🤖 Bot
                      </ToggleButton>
                      <ToggleButton value="manual" sx={{ px: 1.5, py: 0.25, fontSize: '0.75rem' }}>
                        🎮 Manual
                      </ToggleButton>
                    </ToggleButtonGroup>
                  </div>

                  <Button
                    variant="text"
                    color="inherit"
                    size="small"
                    sx={{
                      textTransform: 'none',
                      fontSize: '0.75rem',
                      opacity: 0.7,
                      '&:hover': { opacity: 1 },
                    }}
                    onClick={() => setSelectedGame(null)}
                  >
                    🌐 Deselect Game
                  </Button>
                </div>

                {/* Action Modules Columns */}
                <div
                  style={{
                    padding: '1.5rem',
                    display: 'grid',
                    gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))',
                    gap: '1.5rem',
                  }}
                >
                  {/* Left Column: Development & Configuration */}
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
                    <Typography
                      variant="subtitle2"
                      sx={{
                        fontWeight: 800,
                        color: 'text.secondary',
                        letterSpacing: '0.08em',
                        textTransform: 'uppercase',
                        fontSize: '0.75rem',
                        display: 'flex',
                        alignItems: 'center',
                        gap: '0.4rem',
                      }}
                    >
                      🛠️ Agent Strategy & Dev Kit
                    </Typography>

                    {/* Edit Harness Action Box */}
                    <Card
                      sx={{
                        background: 'rgba(255, 255, 255, 0.01)',
                        border: '1px solid var(--border)',
                        boxShadow: 'none',
                        transition: 'all 0.2s ease',
                        '&:hover': {
                          background: 'rgba(255, 255, 255, 0.02)',
                          borderColor: `${accent}40`,
                        },
                      }}
                    >
                      <CardContent sx={{ p: 2, '&:last-child': { pb: 2 } }}>
                        <Typography variant="subtitle2" sx={{ fontWeight: 700, mb: 0.5 }}>
                          Configure Strategy (Edit Harness)
                        </Typography>
                        <Typography
                          variant="caption"
                          color="text.secondary"
                          sx={{ display: 'block', mb: 1.5, lineHeight: 1.4 }}
                        >
                          Author agent policy code, equip tools, ground docs, and refine decision
                          heuristics.
                        </Typography>
                        <Button
                          variant="outlined"
                          size="small"
                          fullWidth
                          onClick={() => openHarnessFor(g.id)}
                          sx={{ fontWeight: 700, borderColor: 'divider' }}
                        >
                          Open Workspace
                        </Button>
                      </CardContent>
                    </Card>

                    {/* Challenges Action Box */}
                    <Card
                      sx={{
                        background: 'rgba(255, 255, 255, 0.01)',
                        border: '1px solid var(--border)',
                        boxShadow: 'none',
                        transition: 'all 0.2s ease',
                        '&:hover': {
                          background: 'rgba(255, 255, 255, 0.02)',
                          borderColor: `${accent}40`,
                        },
                      }}
                    >
                      <CardContent sx={{ p: 2, '&:last-child': { pb: 2 } }}>
                        <Typography variant="subtitle2" sx={{ fontWeight: 700, mb: 0.5 }}>
                          Scenario Grader (Challenges)
                        </Typography>
                        <Typography
                          variant="caption"
                          color="text.secondary"
                          sx={{ display: 'block', mb: 1.5, lineHeight: 1.4 }}
                        >
                          Run scenario-grading tests against your agent harness to verify decision
                          correctness.
                        </Typography>
                        <Button
                          variant="outlined"
                          size="small"
                          fullWidth
                          onClick={() => requestChallenges(g.id)}
                          sx={{ fontWeight: 700, borderColor: 'divider' }}
                        >
                          Run Challenge Track
                        </Button>
                      </CardContent>
                    </Card>
                  </div>

                  {/* Right Column: Battle Arena */}
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
                    <Typography
                      variant="subtitle2"
                      sx={{
                        fontWeight: 800,
                        color: 'text.secondary',
                        letterSpacing: '0.08em',
                        textTransform: 'uppercase',
                        fontSize: '0.75rem',
                        display: 'flex',
                        alignItems: 'center',
                        gap: '0.4rem',
                      }}
                    >
                      ⚔️ Battle Arena
                    </Typography>

                    {/* Ranked Matchmaking Box */}
                    <Card
                      sx={{
                        background: 'rgba(110, 168, 254, 0.02)',
                        border: `1px solid ${accent}25`,
                        boxShadow: 'none',
                        position: 'relative',
                        overflow: 'hidden',
                      }}
                    >
                      <CardContent sx={{ p: 2, '&:last-child': { pb: 2 } }}>
                        <Typography variant="subtitle2" sx={{ fontWeight: 700, mb: 0.5 }}>
                          Ranked Matchmaking
                        </Typography>
                        <Typography
                          variant="caption"
                          color="text.secondary"
                          sx={{ display: 'block', mb: 1.5, lineHeight: 1.4 }}
                        >
                          Queue to match against other live player agents. Increases MMR and climbs
                          leaderboard.
                        </Typography>

                        <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
                          <div style={{ flex: 1 }}>
                            <Select
                              value={difficulty}
                              onChange={(e) => setDifficulty(e.target.value)}
                              size="small"
                              fullWidth
                              sx={{ fontSize: '0.78rem' }}
                            >
                              <MenuItem value="standard">Standard Difficulty</MenuItem>
                              <MenuItem value="hard">Hard Difficulty</MenuItem>
                              <MenuItem value="expert">Expert Difficulty</MenuItem>
                            </Select>
                          </div>
                          <Button
                            variant="contained"
                            color="primary"
                            disabled={!!queue}
                            onClick={() => void findRankedMatch(g.id, difficulty)}
                            sx={{ fontWeight: 800, px: 2, py: 0.8 }}
                          >
                            Find Match
                          </Button>
                        </div>
                      </CardContent>
                    </Card>

                    {/* Practice Zone Box */}
                    <Card
                      sx={{
                        background: 'rgba(255, 255, 255, 0.01)',
                        border: '1px solid var(--border)',
                        boxShadow: 'none',
                      }}
                    >
                      <CardContent sx={{ p: 2, '&:last-child': { pb: 2 } }}>
                        <Typography variant="subtitle2" sx={{ fontWeight: 700, mb: 0.5 }}>
                          Practice & Hosting
                        </Typography>
                        <Typography
                          variant="caption"
                          color="text.secondary"
                          sx={{ display: 'block', mb: 1.5, lineHeight: 1.4 }}
                        >
                          Play unrated practice sessions against your own agent configuration or
                          practice bots.
                        </Typography>

                        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem' }}>
                          <Button
                            variant="outlined"
                            onClick={() => void playVsOwnAgent(g.id)}
                            sx={{ fontWeight: 700, fontSize: '0.78rem', py: 0.5 }}
                            fullWidth
                          >
                            Play vs My Agent
                          </Button>

                          <div style={{ display: 'flex', gap: '0.4rem', alignItems: 'stretch' }}>
                            <Button
                              variant="outlined"
                              color="secondary"
                              onClick={() => void playVsBot(g.id, botTier)}
                              sx={{ fontWeight: 700, flex: 1, fontSize: '0.78rem', py: 0.5 }}
                            >
                              🤖 Test vs Bot
                            </Button>
                            <Select
                              value={botTier}
                              onChange={(e) => setBotTier(e.target.value)}
                              size="small"
                              sx={{ fontSize: '0.78rem', width: '130px' }}
                            >
                              {BOT_TIERS.map((t) => (
                                <MenuItem key={t.value} value={t.value} sx={{ fontSize: '0.8rem' }}>
                                  {t.label}
                                </MenuItem>
                              ))}
                            </Select>
                          </div>

                          <Button
                            variant="text"
                            onClick={() => void hostOpenTable(g.id)}
                            sx={{
                              fontWeight: 700,
                              color: 'text.secondary',
                              textTransform: 'none',
                              fontSize: '0.72rem',
                              py: 0.25,
                              mt: 0.25,
                              '&:hover': { color: 'text.primary' },
                            }}
                            size="small"
                          >
                            🌐 Host Open Server Table
                          </Button>
                        </div>
                      </CardContent>
                    </Card>
                  </div>
                </div>
              </Card>

              {/* Server Browser Table Header */}
              <div
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  marginBottom: '0.4rem',
                  marginTop: '1rem',
                }}
              >
                <Typography variant="subtitle2" sx={{ fontWeight: 800, color: 'text.secondary' }}>
                  🌐 Active Server Browser ({nameOf(selectedGame)} servers)
                </Typography>
                <Button
                  size="small"
                  color="inherit"
                  onClick={() => gamesListTables()}
                  sx={{ py: 0.1, minWidth: 0, textTransform: 'none' }}
                >
                  ↻ Refresh List
                </Button>
              </div>

              {/* Server Browser Table */}
              <div className="games-server-browser">
                <div className="games-server-header">
                  <span>Server</span>
                  <span>Status</span>
                  <span>Players</span>
                  <span>Ping</span>
                  <span>Action</span>
                </div>

                {filteredTables.length === 0 ? (
                  <div
                    style={{
                      padding: '2rem 1rem',
                      textAlign: 'center',
                      color: 'var(--text-dim)',
                      fontSize: '0.8rem',
                    }}
                  >
                    {connected
                      ? `No ${nameOf(selectedGame)} servers running. Click "Host Server" on its card to start one.`
                      : 'Servers appear once connected — that happens automatically when you play.'}
                  </div>
                ) : (
                  filteredTables.map((t) => (
                    <ServerRow
                      key={t.id}
                      table={t}
                      gameName={nameOf(t.game_id)}
                      gameIcon={GAME_ICONS[t.game_id] ?? '🎲'}
                    />
                  ))
                )}
              </div>
            </>
          );
        })()
      )}
    </GamesMui>
  );
}
