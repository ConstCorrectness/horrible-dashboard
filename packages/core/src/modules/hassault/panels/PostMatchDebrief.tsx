import type { PostMatchSummary } from '../api';

export interface PostMatchDebriefProps {
  summary: PostMatchSummary;
  onDismiss: () => void;
  onRequeue: () => void;
}

export function PostMatchDebrief({ summary, onDismiss, onRequeue }: PostMatchDebriefProps) {
  const isVictory = summary.won;
  const ratingSign = summary.ratingDelta >= 0 ? '+' : '';

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        background: 'rgba(13, 17, 23, 0.88)',
        backdropFilter: 'blur(10px)',
        zIndex: 9999,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '1rem',
        color: '#ffffff',
      }}
    >
      <div
        style={{
          background: 'var(--bg-raised, #1c2128)',
          border: `2px solid ${isVictory ? '#22c55e' : '#ef4444'}`,
          borderRadius: 12,
          padding: '1.5rem',
          maxWidth: 540,
          width: '100%',
          display: 'flex',
          flexDirection: 'column',
          gap: '1rem',
          boxShadow: `0 20px 50px ${isVictory ? 'rgba(34, 197, 94, 0.25)' : 'rgba(239, 68, 68, 0.25)'}`,
          animation: 'fadeIn 0.3s cubic-bezier(0.16, 1, 0.3, 1)',
        }}
      >
        {/* Match Result Banner */}
        <div style={{ textAlign: 'center' }}>
          <div
            style={{
              fontSize: '2rem',
              fontWeight: 900,
              letterSpacing: '2px',
              textTransform: 'uppercase',
              color: isVictory ? '#4ade80' : '#f87171',
            }}
          >
            {isVictory ? '🏆 VICTORY' : '💀 DEFEAT'}
          </div>
          <div style={{ color: 'var(--text-dim, #8b949e)', fontSize: '0.85rem', marginTop: '0.2rem' }}>
            Map: <strong>{summary.mapName}</strong> · Match Concluded
          </div>
        </div>

        {/* Stats Grid */}
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(4, 1fr)',
            gap: '0.5rem',
            background: 'var(--bg-tertiary, #161b22)',
            padding: '0.8rem',
            borderRadius: 8,
            border: '1px solid var(--border-dim, #30363d)',
            textAlign: 'center',
          }}
        >
          <div>
            <div style={{ fontSize: '1.3rem', fontWeight: 800, color: '#38bdf8' }}>{summary.kills}</div>
            <div style={{ fontSize: '0.7rem', color: 'var(--text-dim)', textTransform: 'uppercase' }}>Kills</div>
          </div>
          <div>
            <div style={{ fontSize: '1.3rem', fontWeight: 800, color: '#f43f5e' }}>{summary.deaths}</div>
            <div style={{ fontSize: '0.7rem', color: 'var(--text-dim)', textTransform: 'uppercase' }}>Deaths</div>
          </div>
          <div>
            <div style={{ fontSize: '1.3rem', fontWeight: 800, color: '#fbbf24' }}>{summary.headshotPercent}%</div>
            <div style={{ fontSize: '0.7rem', color: 'var(--text-dim)', textTransform: 'uppercase' }}>HS Acc</div>
          </div>
          <div>
            <div style={{ fontSize: '1.3rem', fontWeight: 800, color: '#a78bfa' }}>{summary.damageDealt}</div>
            <div style={{ fontSize: '0.7rem', color: 'var(--text-dim)', textTransform: 'uppercase' }}>Damage</div>
          </div>
        </div>

        {summary.isMvp && (
          <div
            style={{
              background: 'linear-gradient(90deg, #d97706 0%, #b45309 100%)',
              color: '#ffffff',
              textAlign: 'center',
              fontWeight: 800,
              fontSize: '0.8rem',
              padding: '0.35rem',
              borderRadius: 6,
              letterSpacing: '1px',
            }}
          >
            ⭐ MATCH MVP AWARD
          </div>
        )}

        {/* Rank & Rating Progression */}
        <div
          style={{
            background: 'var(--bg-tertiary, #161b22)',
            padding: '0.8rem',
            borderRadius: 8,
            border: '1px solid var(--border-dim, #30363d)',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
          }}
        >
          <div>
            <div style={{ fontSize: '0.72rem', color: 'var(--text-dim)', textTransform: 'uppercase' }}>Competitive Rank</div>
            <div style={{ fontSize: '1.1rem', fontWeight: 800, color: '#e2e8f0' }}>{summary.ratingTier}</div>
          </div>
          <div style={{ textAlign: 'right' }}>
            <div
              style={{
                fontSize: '1.2rem',
                fontWeight: 800,
                color: summary.ratingDelta >= 0 ? '#4ade80' : '#f87171',
              }}
            >
              {ratingSign}{summary.ratingDelta}
            </div>
            <div style={{ fontSize: '0.72rem', color: 'var(--text-dim)' }}>
              Rating: <strong>{summary.newRating}</strong>
            </div>
          </div>
        </div>

        {/* Level XP Progress Bar */}
        <div
          style={{
            background: 'var(--bg-tertiary, #161b22)',
            padding: '0.8rem',
            borderRadius: 8,
            border: '1px solid var(--border-dim, #30363d)',
            display: 'flex',
            flexDirection: 'column',
            gap: '0.4rem',
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.78rem' }}>
            <span>Level {summary.currentLevel} Progress (+{summary.xpGained} XP)</span>
            <strong>{summary.levelProgressPercent}%</strong>
          </div>
          <div style={{ height: 8, background: '#30363d', borderRadius: 4, overflow: 'hidden' }}>
            <div
              style={{
                height: '100%',
                width: `${summary.levelProgressPercent}%`,
                background: 'linear-gradient(90deg, #38bdf8 0%, #818cf8 100%)',
              }}
            />
          </div>
        </div>

        {/* Level-up / Match Drop Reward Showcase */}
        {summary.earnedDrop && summary.earnedDrop.definition && (
          <div
            style={{
              background: 'linear-gradient(90deg, rgba(234, 179, 8, 0.2) 0%, rgba(220, 38, 38, 0.2) 100%)',
              border: `2px solid ${summary.earnedDrop.definition.rarityColor}`,
              borderRadius: 8,
              padding: '0.8rem',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
            }}
          >
            <div>
              <div style={{ color: '#fbbf24', fontWeight: 800, fontSize: '0.75rem', textTransform: 'uppercase' }}>
                🎁 Level-Up Care Package Reward!
              </div>
              <div style={{ fontSize: '1rem', fontWeight: 700, marginTop: '0.2rem' }}>
                {summary.earnedDrop.definition.name} ({summary.earnedDrop.definition.weaponId.toUpperCase()})
              </div>
              <div style={{ fontSize: '0.72rem', color: 'var(--text-dim)' }}>
                {summary.earnedDrop.wearName} · Float: <code>{summary.earnedDrop.floatValue}</code>
              </div>
            </div>
            <span style={{ fontSize: '2rem' }}>✨</span>
          </div>
        )}

        {/* Action Buttons */}
        <div style={{ display: 'flex', gap: '0.6rem', marginTop: '0.4rem' }}>
          <button
            type="button"
            className="games-play-btn"
            style={{ flex: 1, padding: '0.6rem', fontSize: '0.9rem' }}
            onClick={onRequeue}
          >
            ⚡ Play Again / Re-Queue
          </button>
          <button
            type="button"
            className="games-ghost-btn"
            style={{ padding: '0.6rem', fontSize: '0.9rem' }}
            onClick={onDismiss}
          >
            Return to Lobby
          </button>
        </div>
      </div>
    </div>
  );
}
