import { useState } from 'react';

import { spatialVoice } from '../voice';

export function VoiceCommsPanel() {
  const [initialized, setInitialized] = useState(false);
  const [mode, setMode] = useState<'ptt' | 'vad'>('ptt');
  const [muted, setMuted] = useState(false);
  const [deafened, setDeafened] = useState(false);
  const [micVolume, setMicVolume] = useState(1.0);
  const [statusMsg, setStatusMsg] = useState<string | null>(null);

  const handleConnectVoice = async () => {
    setStatusMsg('Connecting audio capture devices…');
    const ok = await spatialVoice.init();
    if (ok) {
      setInitialized(true);
      setStatusMsg('🎙 3D Spatial Audio & Voice Comms Active');
    } else {
      setStatusMsg('Microphone access denied or unavailable.');
    }
  };

  const handleToggleMute = () => {
    const next = !muted;
    setMuted(next);
    spatialVoice.micMuted = next;
    spatialVoice.setPushToTalk(!next && spatialVoice.pttActive);
  };

  const handleToggleDeafen = () => {
    const next = !deafened;
    setDeafened(next);
    spatialVoice.deafened = next;
  };

  return (
    <div
      style={{
        background: 'var(--bg-tertiary, #161b22)',
        border: '1px solid var(--border-dim, #30363d)',
        borderRadius: 8,
        padding: '1rem',
        display: 'flex',
        flexDirection: 'column',
        gap: '0.8rem',
        color: '#ffffff',
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        {/* A heading, not a pitch. The feature-list line under it described the
            implementation to somebody who has not turned it on yet — the same
            advertising-in-the-menu problem the rest of this screen shed. */}
        <div>
          <h4 style={{ margin: 0, display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
            Voice
          </h4>
        </div>

        {!initialized ? (
          <button type="button" className="games-play-btn" onClick={handleConnectVoice}>
            Connect Voice
          </button>
        ) : (
          <div style={{ display: 'flex', gap: '0.4rem' }}>
            <button
              type="button"
              className={muted ? 'games-play-btn' : 'games-ghost-btn'}
              style={{ padding: '0.3rem 0.8rem', fontSize: '0.75rem' }}
              onClick={handleToggleMute}
            >
              {muted ? '🔇 Unmute Mic' : '🎙 Mute Mic'}
            </button>
            <button
              type="button"
              className={deafened ? 'games-play-btn' : 'games-ghost-btn'}
              style={{ padding: '0.3rem 0.8rem', fontSize: '0.75rem' }}
              onClick={handleToggleDeafen}
            >
              {deafened ? '🔕 Undeafen' : '🎧 Deafen'}
            </button>
          </div>
        )}
      </div>

      {statusMsg && (
        <div style={{ fontSize: '0.75rem', color: initialized ? '#4ade80' : '#f87171' }}>
          {statusMsg}
        </div>
      )}

      {/* Voice Controls */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '0.8rem' }}>
        <div>
          <label style={{ fontSize: '0.75rem', color: 'var(--text-dim)' }}>Activation Mode</label>
          <div style={{ display: 'flex', gap: '0.4rem', marginTop: '0.3rem' }}>
            <button
              type="button"
              className={mode === 'ptt' ? 'games-town-toggle-btn active' : 'games-town-toggle-btn'}
              onClick={() => {
                setMode('ptt');
                spatialVoice.mode = 'ptt';
              }}
            >
              Push-To-Talk [V]
            </button>
            <button
              type="button"
              className={mode === 'vad' ? 'games-town-toggle-btn active' : 'games-town-toggle-btn'}
              onClick={() => {
                setMode('vad');
                spatialVoice.mode = 'vad';
              }}
            >
              Voice Activity (VAD)
            </button>
          </div>
        </div>

        <div>
          <label style={{ fontSize: '0.75rem', color: 'var(--text-dim)' }}>
            Input Volume ({Math.round(micVolume * 100)}%)
          </label>
          <input
            type="range"
            min="0"
            max="2"
            step="0.05"
            value={micVolume}
            onChange={(e) => setMicVolume(parseFloat(e.target.value))}
            style={{ width: '100%', marginTop: '0.4rem' }}
          />
        </div>
      </div>
    </div>
  );
}
