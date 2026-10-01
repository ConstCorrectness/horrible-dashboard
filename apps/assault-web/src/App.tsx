import { lazy, Suspense, useCallback, useEffect, useState } from 'react';
import { useGuestSession } from './hooks/useGuestSession';
import { accountName, applySocketIdentity, useAuth } from './auth';
import { Landing } from './components/Landing';
import { DeploySplash } from './components/DeploySplash';
import { CreateMatchDialog } from './components/CreateMatchDialog';
import { SignInDialog } from './components/SignInDialog';
import { ShareRoomModal } from './components/ShareRoomModal';
import { MobileWarningBanner } from './components/MobileWarningBanner';

type AppView = 'browser' | 'deploy' | 'playing';

/**
 * The game itself: three.js, the Rapier physics wasm and the renderer — about
 * 4 MB of script, which the landing page needs none of.
 *
 * Loaded lazily so the landing page arrives in a fraction of that, and then
 * **prefetched** once the page is idle (below), so pressing Quick Play does not
 * start the download — by then it has usually finished.
 */
const loadPanel = () => import('@horrible/core/hassault-panel');
const HorribleAssaultPanel = lazy(() =>
  loadPanel().then((m) => ({ default: m.HorribleAssaultPanel })),
);

const DEFAULT_MAP = 'hd_dust2';

function parseLocationParams(): { room: string | null; map: string } {
  let room: string | null = null;
  let map = DEFAULT_MAP;

  // Check URL hash first (#room=abc&map=hd_dust2)
  const hash = window.location.hash.replace(/^#\/?/, '');
  if (hash) {
    const params = new URLSearchParams(hash);
    if (params.get('room')) room = params.get('room');
    if (params.get('map')) map = params.get('map') || map;
  }

  // Fallback to query string (?room=abc&map=hd_dust2)
  if (!room) {
    const search = new URLSearchParams(window.location.search);
    if (search.get('room')) room = search.get('room');
    if (search.get('map')) map = search.get('map') || map;
  }

  return { room, map };
}

export default function App() {
  const { callsign, setCallsign } = useGuestSession();
  const auth = useAuth();
  const [view, setView] = useState<AppView>('browser');
  const [target, setTarget] = useState<{ room: string | null; map: string }>({
    room: null,
    map: DEFAULT_MAP,
  });
  const [dialog, setDialog] = useState<
    | { kind: 'create' }
    | { kind: 'signin'; step: 'in' | 'handle' }
    | { kind: 'share'; room: string; map: string }
    | null
  >(null);
  const [isFullscreen, setIsFullscreen] = useState(false);

  // Signed in with a username → the socket carries the token; otherwise the
  // guest callsign. Re-applied whenever either changes.
  const handle = accountName(auth.account);
  useEffect(() => {
    applySocketIdentity(callsign);
  }, [auth.token, handle, callsign]);

  useEffect(() => {
    // `requestIdleCallback` is missing on Safari; a short timeout is the same
    // intent — after first paint, before anyone has had time to click.
    const prefetch = () => void loadPanel().catch(() => {});
    if ('requestIdleCallback' in window) {
      const id = window.requestIdleCallback(prefetch, { timeout: 2000 });
      return () => window.cancelIdleCallback(id);
    }
    const id = globalThis.setTimeout(prefetch, 500);
    return () => globalThis.clearTimeout(id);
  }, []);

  // A shared link lands on the deploy screen for its room.
  useEffect(() => {
    const initial = parseLocationParams();
    if (initial.room) {
      setTarget({ room: initial.room, map: initial.map });
      setView('deploy');
    }
  }, []);

  useEffect(() => {
    const onFullscreenChange = () => setIsFullscreen(Boolean(document.fullscreenElement));
    document.addEventListener('fullscreenchange', onFullscreenChange);
    return () => document.removeEventListener('fullscreenchange', onFullscreenChange);
  }, []);

  const toggleFullscreen = useCallback(async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await document.documentElement.requestFullscreen();
    } catch {
      // Fullscreen refused (permissions policy, iframe) — the button just does nothing.
    }
  }, []);

  const join = (room: string, map: string) => {
    setTarget({ room, map });
    // Bookmarkable and shareable from the moment you pick it.
    window.location.hash = `room=${encodeURIComponent(room)}&map=${encodeURIComponent(map)}`;
    setView('deploy');
  };

  const host = (map: string) => {
    setDialog(null);
    setTarget({ room: null, map });
    window.location.hash = `map=${encodeURIComponent(map)}`;
    setView('deploy');
  };

  const backToBrowser = () => {
    window.location.hash = '';
    setTarget((t) => ({ ...t, room: null }));
    setView('browser');
  };

  return (
    <div style={{ width: '100%', height: '100%', position: 'relative', overflow: 'hidden' }}>
      <MobileWarningBanner />

      {view === 'browser' && (
        <Landing
          callsign={callsign}
          onCallsignChange={setCallsign}
          onJoin={join}
          onHost={host}
          onCreate={() => setDialog({ kind: 'create' })}
          onSignIn={() => setDialog({ kind: 'signin', step: 'in' })}
          onChooseHandle={() => setDialog({ kind: 'signin', step: 'handle' })}
        />
      )}

      {view === 'deploy' && (
        <DeploySplash
          room={target.room}
          map={target.map}
          callsign={callsign}
          onCallsignChange={setCallsign}
          onDeploy={() => setView('playing')}
          onCancel={backToBrowser}
        />
      )}

      {view === 'playing' && (
        <div style={{ width: '100%', height: '100%', position: 'relative' }}>
          <div className="match-tools">
            <button
              type="button"
              className="btn btn-sm"
              onClick={() =>
                setDialog({ kind: 'share', room: target.room || 'live-match', map: target.map })
              }
              title="Share match link"
            >
              Invite
            </button>
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => void toggleFullscreen()}
              title={isFullscreen ? 'Exit fullscreen' : 'Enter fullscreen'}
            >
              {isFullscreen ? 'Windowed' : 'Fullscreen'}
            </button>
          </div>

          <Suspense fallback={<LoadingGame />}>
            <HorribleAssaultPanel
              guestCallsign={handle ?? callsign}
              initialMap={target.map}
              initialRoom={target.room ?? undefined}
              forceWebGl={true}
              standalone={true}
              onShareRoom={(room, map) => setDialog({ kind: 'share', room, map })}
              onToggleFullscreen={() => void toggleFullscreen()}
              isFullscreen={isFullscreen}
              onExit={backToBrowser}
            />
          </Suspense>
        </div>
      )}

      {dialog?.kind === 'create' && (
        <CreateMatchDialog onLaunch={host} onClose={() => setDialog(null)} />
      )}
      {dialog?.kind === 'signin' && (
        <SignInDialog initialStep={dialog.step} onClose={() => setDialog(null)} />
      )}
      {dialog?.kind === 'share' && (
        <ShareRoomModal room={dialog.room} map={dialog.map} onClose={() => setDialog(null)} />
      )}
    </div>
  );
}

/** Shown only if Deploy is pressed before the prefetch above has finished. */
function LoadingGame() {
  return (
    <div
      className="mono"
      style={{
        position: 'absolute',
        inset: 0,
        display: 'grid',
        placeItems: 'center',
        fontSize: 13,
        letterSpacing: '0.14em',
        textTransform: 'uppercase',
      }}
    >
      Loading game engine…
    </div>
  );
}
