import { useState, useEffect, useCallback } from 'react';
import { HorribleAssaultPanel } from '@horrible/core';
import { useGuestSession } from './hooks/useGuestSession';
import { ServerBrowser } from './components/ServerBrowser';
import { InstantDeployModal } from './components/InstantDeployModal';
import { ShareRoomModal } from './components/ShareRoomModal';
import { MobileWarningBanner } from './components/MobileWarningBanner';

type AppView = 'browser' | 'instant_deploy' | 'playing';

function parseLocationParams(): { room: string | null; map: string } {
  let room: string | null = null;
  let map = 'hd_assault';

  // Check URL hash first (#room=abc&map=hd_assault)
  const hash = window.location.hash.replace(/^#\/?/, '');
  if (hash) {
    const params = new URLSearchParams(hash);
    if (params.get('room')) room = params.get('room');
    if (params.get('map')) map = params.get('map') || map;
  }

  // Fallback to query string (?room=abc&map=hd_assault)
  if (!room) {
    const search = new URLSearchParams(window.location.search);
    if (search.get('room')) room = search.get('room');
    if (search.get('map')) map = search.get('map') || map;
  }

  return { room, map };
}

export default function App() {
  const { callsign, setCallsign } = useGuestSession();
  const [view, setView] = useState<AppView>('browser');
  const [targetRoom, setTargetRoom] = useState<string | null>(null);
  const [targetMap, setTargetMap] = useState<string>('hd_assault');
  const [shareModal, setShareModal] = useState<{ room: string; map: string } | null>(null);
  const [isFullscreen, setIsFullscreen] = useState(false);

  useEffect(() => {
    const initial = parseLocationParams();
    if (initial.room) {
      setTargetRoom(initial.room);
      setTargetMap(initial.map);
      setView('instant_deploy');
    }
  }, []);

  useEffect(() => {
    const onFullscreenChange = () => {
      setIsFullscreen(Boolean(document.fullscreenElement));
    };
    document.addEventListener('fullscreenchange', onFullscreenChange);
    return () => document.removeEventListener('fullscreenchange', onFullscreenChange);
  }, []);

  const toggleFullscreen = useCallback(async () => {
    try {
      if (document.fullscreenElement) {
        await document.exitFullscreen();
      } else {
        await document.documentElement.requestFullscreen();
      }
    } catch {
      // Fullscreen permission error ignored
    }
  }, []);

  const handleJoinMatch = (room: string, map: string) => {
    setTargetRoom(room);
    setTargetMap(map);
    // Update hash for easy bookmarking and sharing
    window.location.hash = `room=${encodeURIComponent(room)}&map=${encodeURIComponent(map)}`;
    setView('playing');
  };

  const handleHostMatch = (map: string) => {
    setTargetRoom(null);
    setTargetMap(map);
    window.location.hash = `map=${encodeURIComponent(map)}`;
    setView('playing');
  };

  const handleExitMatch = () => {
    window.location.hash = '';
    setTargetRoom(null);
    setView('browser');
  };

  const handleOpenShare = (room: string, map: string) => {
    setShareModal({ room, map });
  };

  return (
    <div style={{ width: '100%', height: '100%', position: 'relative', overflow: 'hidden', backgroundColor: '#070a10' }}>
      <MobileWarningBanner />

      {view === 'browser' && (
        <ServerBrowser
          callsign={callsign}
          onCallsignChange={setCallsign}
          onJoinMatch={handleJoinMatch}
          onHostMatch={handleHostMatch}
        />
      )}

      {view === 'instant_deploy' && targetRoom && (
        <InstantDeployModal
          room={targetRoom}
          map={targetMap}
          callsign={callsign}
          onCallsignChange={setCallsign}
          onDeploy={(room, map) => {
            setTargetRoom(room);
            setTargetMap(map);
            setView('playing');
          }}
          onCancel={() => {
            window.location.hash = '';
            setTargetRoom(null);
            setView('browser');
          }}
        />
      )}

      {view === 'playing' && (
        <div style={{ width: '100%', height: '100%', position: 'relative' }}>
          {/* Quick HUD Toolbar (Fullscreen & Share) */}
          <div
            style={{
              position: 'absolute',
              top: 10,
              right: 12,
              zIndex: 50,
              display: 'flex',
              gap: '0.5rem',
              alignItems: 'center',
            }}
          >
            <button
              type="button"
              onClick={() => handleOpenShare(targetRoom || 'live-match', targetMap)}
              style={{
                backgroundColor: 'rgba(15, 23, 42, 0.75)',
                border: '1px solid rgba(56, 189, 248, 0.4)',
                color: '#38bdf8',
                borderRadius: 5,
                padding: '0.35rem 0.7rem',
                fontSize: '0.78rem',
                fontWeight: 700,
                cursor: 'pointer',
                backdropFilter: 'blur(4px)',
              }}
              title="Share match link"
            >
              🔗 Share
            </button>
            <button
              type="button"
              onClick={toggleFullscreen}
              style={{
                backgroundColor: 'rgba(15, 23, 42, 0.75)',
                border: '1px solid rgba(255, 255, 255, 0.2)',
                color: '#cbd5e1',
                borderRadius: 5,
                padding: '0.35rem 0.6rem',
                fontSize: '0.78rem',
                fontWeight: 700,
                cursor: 'pointer',
                backdropFilter: 'blur(4px)',
              }}
              title={isFullscreen ? 'Exit Fullscreen' : 'Enter Fullscreen'}
            >
              {isFullscreen ? '⤢ Window' : '⤡ Fullscreen'}
            </button>
          </div>

          <HorribleAssaultPanel
            guestCallsign={callsign}
            initialMap={targetMap}
            initialRoom={targetRoom ?? undefined}
            forceWebGl={true}
            onShareRoom={handleOpenShare}
            onToggleFullscreen={toggleFullscreen}
            isFullscreen={isFullscreen}
            onExit={handleExitMatch}
          />
        </div>
      )}

      {shareModal && (
        <ShareRoomModal
          room={shareModal.room}
          map={shareModal.map}
          onClose={() => setShareModal(null)}
        />
      )}
    </div>
  );
}
