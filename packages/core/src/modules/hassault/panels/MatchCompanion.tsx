export interface MatchCompanionProps {
  mapName: string;
  room: string;
  pid?: number;
  onExitMatch: () => void;
}

export function MatchCompanion({ mapName, room, pid, onExitMatch }: MatchCompanionProps) {
  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        height: '100%',
        padding: '2rem',
        textAlign: 'center',
        background: 'radial-gradient(circle at center, rgba(56, 189, 248, 0.08) 0%, rgba(13, 17, 23, 0.95) 70%)',
        color: '#ffffff',
        gap: '1.2rem',
      }}
    >
      <div
        style={{
          width: 72,
          height: 72,
          borderRadius: '50%',
          border: '3px solid #38bdf8',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontSize: '2rem',
          boxShadow: '0 0 30px rgba(56, 189, 248, 0.4)',
          animation: 'pulse 2s infinite ease-in-out',
        }}
      >
        🎮
      </div>

      <div>
        <div style={{ fontSize: '0.75rem', textTransform: 'uppercase', letterSpacing: '2px', color: '#38bdf8', fontWeight: 800 }}>
          Live Match in Progress
        </div>
        <h2 style={{ margin: '0.3rem 0 0 0', fontSize: '1.6rem', fontWeight: 800 }}>
          {mapName}
        </h2>
        <div style={{ fontSize: '0.8rem', color: 'var(--text-dim, #8b949e)', marginTop: '0.2rem' }}>
          Room: <code>{room}</code> {pid ? `· Native Engine PID: ${pid}` : ''} · Sub-Tick UDP 128Hz
        </div>
      </div>

      <div
        style={{
          maxWidth: 420,
          background: 'var(--bg-tertiary, #161b22)',
          padding: '0.8rem 1.2rem',
          borderRadius: 8,
          border: '1px solid var(--border-dim, #30363d)',
          fontSize: '0.8rem',
          color: 'var(--text-dim, #8b949e)',
          lineHeight: '1.4',
        }}
      >
        The native high-performance FPS client is running in focus with <strong>1,000Hz+ Raw Mouse Input</strong> and uncapped framerate.
        The dashboard is maintaining your live session companion.
      </div>

      <div style={{ display: 'flex', gap: '0.8rem' }}>
        <button
          type="button"
          className="games-ghost-btn"
          style={{ padding: '0.5rem 1.2rem', fontSize: '0.85rem', color: '#f87171', borderColor: '#f87171' }}
          onClick={onExitMatch}
        >
          Leave Match
        </button>
      </div>
    </div>
  );
}
