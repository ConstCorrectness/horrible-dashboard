import { useEffect, useState } from 'react';

import { registry } from '../../../registry';
import { consoleRegistry } from '../console/registry';
import {
  dispatchConsoleCommand,
  getLatestMatchTelemetry,
  onMatchTelemetry,
  requestMapEditorInspect,
  type MatchTelemetry,
} from '../workspace-bus';

export interface MatchCompanionProps {
  mapName?: string;
  room?: string;
  pid?: number;
  onExitMatch?: () => void;
}

export function MatchCompanion({
  mapName: propMapName,
  room: propRoom,
  pid,
  onExitMatch,
}: MatchCompanionProps) {
  const [telemetry, setTelemetry] = useState<MatchTelemetry | null>(getLatestMatchTelemetry);
  const [godMode, setGodMode] = useState<boolean>(consoleRegistry.getBool('player.god'));
  const [noclip, setNoclip] = useState<boolean>(consoleRegistry.getBool('player.noclip'));
  const [hitboxes, setHitboxes] = useState<boolean>(consoleRegistry.getBool('draw.hitboxes'));
  const [wireframe, setWireframe] = useState<boolean>(consoleRegistry.getBool('draw.wireframe'));

  useEffect(() => {
    const unsubTelemetry = onMatchTelemetry((data) => {
      setTelemetry(data);
    });

    const unsubCvar = consoleRegistry.subscribe((name, val) => {
      if (name === 'player.god') setGodMode(Boolean(val));
      if (name === 'player.noclip') setNoclip(Boolean(val));
      if (name === 'draw.hitboxes') setHitboxes(Boolean(val));
      if (name === 'draw.wireframe') setWireframe(Boolean(val));
    });

    return () => {
      unsubTelemetry();
      unsubCvar();
    };
  }, []);

  const currentMap = propMapName || telemetry?.mapName || 'No Map Active';
  const currentRoom = propRoom || telemetry?.room || 'local';
  const isPlaying = Boolean(propMapName || telemetry?.phase === 'playing');
  const player = telemetry?.player;

  const toggleGodMode = () => {
    const next = !godMode;
    consoleRegistry.set('player.god', next ? '1' : '0');
    setGodMode(next);
    dispatchConsoleCommand({
      command: `player.god ${next ? 1 : 0}`,
      source: 'panel',
    });
  };

  const toggleNoclip = () => {
    const next = !noclip;
    consoleRegistry.set('player.noclip', next ? '1' : '0');
    setNoclip(next);
    dispatchConsoleCommand({
      command: `player.noclip ${next ? 1 : 0}`,
      source: 'panel',
    });
  };

  const toggleHitboxes = () => {
    const next = !hitboxes;
    consoleRegistry.set('draw.hitboxes', next ? '1' : '0');
    setHitboxes(next);
    dispatchConsoleCommand({
      command: `draw.hitboxes ${next ? 1 : 0}`,
      source: 'panel',
    });
  };

  const toggleWireframe = () => {
    const next = !wireframe;
    consoleRegistry.set('draw.wireframe', next ? '1' : '0');
    setWireframe(next);
    dispatchConsoleCommand({
      command: `draw.wireframe ${next ? 1 : 0}`,
      source: 'panel',
    });
  };

  const handleOpenStudio = () => {
    if (player && currentMap) {
      requestMapEditorInspect({
        mapName: currentMap,
        camera: { x: player.x, y: player.y, z: player.z, yaw: player.yaw },
        source: 'companion',
      });
    }
    registry.openPanel('hassault.studio');
  };

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        padding: '1.2rem',
        background: 'radial-gradient(circle at 50% 20%, rgba(56, 189, 248, 0.08) 0%, rgba(13, 17, 23, 0.96) 80%)',
        color: 'rgb(241, 245, 249)',
        gap: '1rem',
        overflowY: 'auto',
      }}
    >
      {/* Header Bar */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          borderBottom: '1px solid rgba(255, 255, 255, 0.1)',
          paddingBottom: '0.8rem',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.8rem' }}>
          <div
            style={{
              width: 44,
              height: 44,
              borderRadius: '50%',
              border: '2px solid rgb(56, 189, 248)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: '1.4rem',
              boxShadow: '0 0 16px rgba(56, 189, 248, 0.35)',
            }}
          >
            🎮
          </div>
          <div>
            <div
              style={{
                fontSize: '0.68rem',
                textTransform: 'uppercase',
                letterSpacing: '1.5px',
                color: 'rgb(56, 189, 248)',
                fontWeight: 800,
              }}
            >
              {isPlaying ? 'Match Companion • Live' : 'Match Companion • Standby'}
            </div>
            <h3 style={{ margin: '0.1rem 0 0 0', fontSize: '1.15rem', fontWeight: 800 }}>
              {currentMap}
            </h3>
          </div>
        </div>

        <div style={{ display: 'flex', gap: '0.4rem', alignItems: 'center' }}>
          <span
            style={{
              fontSize: '0.68rem',
              padding: '2px 8px',
              borderRadius: 4,
              background: isPlaying ? 'rgba(34, 197, 94, 0.2)' : 'rgba(148, 163, 184, 0.2)',
              border: `1px solid ${isPlaying ? 'rgb(34, 197, 94)' : 'rgb(148, 163, 184)'}`,
              color: isPlaying ? 'rgb(34, 197, 94)' : 'rgb(148, 163, 184)',
              fontWeight: 700,
            }}
          >
            {pid ? `NATIVE PID ${pid}` : isPlaying ? 'SUB-TICK 128Hz' : 'IDLE'}
          </span>
          {onExitMatch && (
            <button
              type="button"
              className="games-ghost-btn"
              style={{
                padding: '0.3rem 0.8rem',
                fontSize: '0.75rem',
                color: 'rgb(248, 113, 113)',
                borderColor: 'rgb(248, 113, 113)',
              }}
              onClick={onExitMatch}
            >
              Leave Match
            </button>
          )}
        </div>
      </div>

      {/* Live Vitals Gauge (if active player) */}
      {player && (
        <div
          style={{
            background: 'rgba(22, 27, 34, 0.8)',
            border: '1px solid rgba(255, 255, 255, 0.1)',
            borderRadius: 8,
            padding: '0.8rem',
            display: 'flex',
            flexDirection: 'column',
            gap: '0.6rem',
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span style={{ fontSize: '0.72rem', fontWeight: 700, color: 'rgb(148, 163, 184)' }}>
              PLAYER VITALS
            </span>
            <div style={{ display: 'flex', gap: '0.3rem' }}>
              {godMode && (
                <span
                  style={{
                    fontSize: '0.62rem',
                    fontWeight: 800,
                    padding: '1px 5px',
                    borderRadius: 3,
                    background: 'rgba(234, 179, 8, 0.25)',
                    color: 'rgb(234, 179, 8)',
                    border: '1px solid rgb(234, 179, 8)',
                  }}
                >
                  GODMODE
                </span>
              )}
              {noclip && (
                <span
                  style={{
                    fontSize: '0.62rem',
                    fontWeight: 800,
                    padding: '1px 5px',
                    borderRadius: 3,
                    background: 'rgba(168, 85, 247, 0.25)',
                    color: 'rgb(168, 85, 247)',
                    border: '1px solid rgb(168, 85, 247)',
                  }}
                >
                  NOCLIP
                </span>
              )}
            </div>
          </div>

          {/* Health & Armor Bars */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.6rem' }}>
            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.7rem' }}>
                <span>❤️ Health</span>
                <span style={{ fontWeight: 800 }}>{player.health} HP</span>
              </div>
              <div
                style={{
                  height: 6,
                  background: 'rgba(255, 255, 255, 0.1)',
                  borderRadius: 3,
                  marginTop: '0.2rem',
                  overflow: 'hidden',
                }}
              >
                <div
                  style={{
                    height: '100%',
                    width: `${Math.max(0, Math.min(100, player.health))}%`,
                    background:
                      player.health > 50
                        ? 'rgb(34, 197, 94)'
                        : player.health > 20
                        ? 'rgb(234, 179, 8)'
                        : 'rgb(239, 68, 68)',
                    transition: 'width 0.2s ease',
                  }}
                />
              </div>
            </div>

            <div>
              <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.7rem' }}>
                <span>🛡️ Armor</span>
                <span style={{ fontWeight: 800 }}>{player.armor}%</span>
              </div>
              <div
                style={{
                  height: 6,
                  background: 'rgba(255, 255, 255, 0.1)',
                  borderRadius: 3,
                  marginTop: '0.2rem',
                  overflow: 'hidden',
                }}
              >
                <div
                  style={{
                    height: '100%',
                    width: `${Math.max(0, Math.min(100, player.armor))}%`,
                    background: 'rgb(56, 189, 248)',
                    transition: 'width 0.2s ease',
                  }}
                />
              </div>
            </div>
          </div>

          {/* Active Weapon & Position */}
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
              paddingTop: '0.4rem',
              borderTop: '1px solid rgba(255, 255, 255, 0.06)',
              fontSize: '0.72rem',
            }}
          >
            <div>
              <span style={{ color: 'rgb(148, 163, 184)' }}>Weapon: </span>
              <strong style={{ color: 'rgb(255, 255, 255)' }}>
                {player.weapon.toUpperCase()}
              </strong>{' '}
              <span style={{ color: 'rgb(56, 189, 248)', fontWeight: 700 }}>
                ({player.ammo} / {player.carried})
              </span>
            </div>
            <div style={{ color: 'rgb(148, 163, 184)', fontFamily: 'monospace' }}>
              pos: {player.x.toFixed(1)}, {player.y.toFixed(1)}, {player.z.toFixed(1)}
            </div>
          </div>
        </div>
      )}

      {/* Developer & Cheat Quick Controls */}
      <div
        style={{
          background: 'rgba(22, 27, 34, 0.8)',
          border: '1px solid rgba(255, 255, 255, 0.1)',
          borderRadius: 8,
          padding: '0.8rem',
          display: 'flex',
          flexDirection: 'column',
          gap: '0.5rem',
        }}
      >
        <div style={{ fontSize: '0.72rem', fontWeight: 700, color: 'rgb(148, 163, 184)' }}>
          LIVE DEV & CHEAT TOGGLES
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '0.4rem' }}>
          <button
            type="button"
            className={godMode ? 'games-play-btn' : 'games-ghost-btn'}
            style={{ fontSize: '0.72rem', padding: '0.35rem 0.5rem' }}
            onClick={toggleGodMode}
          >
            🛡️ Godmode: {godMode ? 'ON' : 'OFF'}
          </button>
          <button
            type="button"
            className={noclip ? 'games-play-btn' : 'games-ghost-btn'}
            style={{ fontSize: '0.72rem', padding: '0.35rem 0.5rem' }}
            onClick={toggleNoclip}
          >
            👻 Noclip: {noclip ? 'ON' : 'OFF'}
          </button>
          <button
            type="button"
            className={hitboxes ? 'games-play-btn' : 'games-ghost-btn'}
            style={{ fontSize: '0.72rem', padding: '0.35rem 0.5rem' }}
            onClick={toggleHitboxes}
          >
            📦 Hitboxes: {hitboxes ? 'ON' : 'OFF'}
          </button>
          <button
            type="button"
            className={wireframe ? 'games-play-btn' : 'games-ghost-btn'}
            style={{ fontSize: '0.72rem', padding: '0.35rem 0.5rem' }}
            onClick={toggleWireframe}
          >
            🕸️ Wireframe: {wireframe ? 'ON' : 'OFF'}
          </button>
        </div>
      </div>

      {/* Cross-Pane Seamless Navigation Bar */}
      <div
        style={{
          background: 'rgba(22, 27, 34, 0.8)',
          border: '1px solid rgba(255, 255, 255, 0.1)',
          borderRadius: 8,
          padding: '0.8rem',
          display: 'flex',
          flexDirection: 'column',
          gap: '0.5rem',
        }}
      >
        <div style={{ fontSize: '0.72rem', fontWeight: 700, color: 'rgb(148, 163, 184)' }}>
          CONNECTED WORKSPACE PANES
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '0.4rem' }}>
          <button
            type="button"
            className="games-ghost-btn"
            style={{ fontSize: '0.72rem', padding: '0.35rem 0.5rem', textAlign: 'left' }}
            onClick={() => registry.openPanel('hassault.play')}
          >
            🎮 Play Arena
          </button>
          <button
            type="button"
            className="games-ghost-btn"
            style={{ fontSize: '0.72rem', padding: '0.35rem 0.5rem', textAlign: 'left' }}
            onClick={handleOpenStudio}
          >
            ◈ 3D Level Studio
          </button>
          <button
            type="button"
            className="games-ghost-btn"
            style={{ fontSize: '0.72rem', padding: '0.35rem 0.5rem', textAlign: 'left' }}
            onClick={() => registry.openPanel('hassault.armory')}
          >
            ⚔ Armory & Skins
          </button>
          <button
            type="button"
            className="games-ghost-btn"
            style={{ fontSize: '0.72rem', padding: '0.35rem 0.5rem', textAlign: 'left' }}
            onClick={() => registry.openPanel('hassault.console')}
          >
            ⌨ Dev Console
          </button>
          <button
            type="button"
            className="games-ghost-btn"
            style={{ fontSize: '0.72rem', padding: '0.35rem 0.5rem', textAlign: 'left' }}
            onClick={() => registry.openPanel('hassault.radar')}
          >
            ⦿ Tactical Radar
          </button>
          <button
            type="button"
            className="games-ghost-btn"
            style={{ fontSize: '0.72rem', padding: '0.35rem 0.5rem', textAlign: 'left' }}
            onClick={() => registry.openPanel('hassault.voice')}
          >
            🎙 Voice Comms
          </button>
        </div>
      </div>

      {/* Match Score & Roster Summary */}
      {telemetry && (
        <div
          style={{
            background: 'rgba(22, 27, 34, 0.8)',
            border: '1px solid rgba(255, 255, 255, 0.1)',
            borderRadius: 8,
            padding: '0.8rem',
            display: 'flex',
            flexDirection: 'column',
            gap: '0.5rem',
            fontSize: '0.75rem',
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span style={{ fontWeight: 700, color: 'rgb(148, 163, 184)' }}>MATCH SCORE</span>
            <div style={{ fontWeight: 800, fontSize: '0.9rem' }}>
              <span style={{ color: 'rgb(96, 165, 250)' }}>CT {telemetry.score.ct}</span>
              {' : '}
              <span style={{ color: 'rgb(248, 113, 113)' }}>{telemetry.score.t} T</span>
            </div>
          </div>
          <div style={{ color: 'rgb(148, 163, 184)', fontSize: '0.7rem' }}>
            Room: <code>{currentRoom}</code> · Active Players: {telemetry.roster.length} · Round
            Time: {Math.floor(telemetry.roundTime)}s
          </div>
        </div>
      )}
    </div>
  );
}

/** First-class dockable panel wrapper */
export function StandaloneMatchCompanionPanel() {
  return <MatchCompanion />;
}
