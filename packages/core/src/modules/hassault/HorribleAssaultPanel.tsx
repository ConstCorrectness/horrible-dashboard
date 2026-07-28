import { useCallback, useEffect, useRef, useState } from 'react';

import { useAgentContext } from '../../agent-context';
import {
  getInstallStatus,
  getMapCubes,
  getMapInfo,
  listInvitees,
  listMaps,
  type InstallStatus,
  type Invitee,
  type MapInfo,
  type MapSummary,
} from './api';
import { AvatarPool } from './avatars';
import { buildWorldMesh } from './geometry';
import type { PlayerRow } from './net';
import { createPlayer, eyeHeight, spawnAt, step, type PlayerState } from './player';
import { MatchSession, type SessionState } from './session';
import { World } from './world';

interface Hud {
  fps: number;
  triangles: number;
  x: number;
  y: number;
  z: number;
  onGround: boolean;
  /** Distance the last reconciliation had to correct, in cubes. */
  error: number;
}

/** Keys we consume, so the pane never swallows the app's own shortcuts. */
const MOVEMENT_KEYS = new Set([
  'KeyW',
  'KeyA',
  'KeyS',
  'KeyD',
  'Space',
  'ShiftLeft',
  'KeyV',
  'ArrowUp',
  'ArrowDown',
  'ArrowLeft',
  'ArrowRight',
]);

const NAME_KEY = 'hassault.playerName';
const NO_CORRECTION = { x: 0, y: 0, z: 0 };

interface SceneHandle {
  setMesh: (w: World) => number;
  avatars: AvatarPool;
  camera: {
    position: { set: (x: number, y: number, z: number) => void };
    rotation: { set: (x: number, y: number, z: number, order?: string) => void };
  };
}

/**
 * HorribleAssault: walk around a real AssaultCube map, rendered in WebGL from the
 * cube grid the backend serves — alone, or in a match against other people on the
 * fabric.
 *
 * three is lazy-loaded on first render, matching `Avatar3D` — it is a large
 * dependency and most sessions never open this pane.
 *
 * See docs/modules/hassault.mdx.
 */
export function HorribleAssaultPanel() {
  const mountRef = useRef<HTMLDivElement | null>(null);
  const [status, setStatus] = useState<InstallStatus | null>(null);
  const [maps, setMaps] = useState<MapSummary[]>([]);
  const [mapName, setMapName] = useState<string>('');
  const [info, setInfo] = useState<MapInfo | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [locked, setLocked] = useState(false);
  const [playerName, setPlayerName] = useState<string>(
    () => localStorage.getItem(NAME_KEY) || 'player',
  );
  const [hud, setHud] = useState<Hud>({
    fps: 0,
    triangles: 0,
    x: 0,
    y: 0,
    z: 0,
    onGround: false,
    error: 0,
  });
  const [net, setNet] = useState<SessionState>({
    status: 'idle',
    room: '',
    map: '',
    playerId: '',
    peers: [],
    error: '',
    rtt: 0,
    host: '',
    invites: [],
  });
  const [invitees, setInvitees] = useState<Invitee[]>([]);
  const [inviteWho, setInviteWho] = useState('');

  // Mutable simulation state, kept out of React: this updates every frame and
  // re-rendering the component 60 times a second would be absurd.
  const worldRef = useRef<World | null>(null);
  const playerRef = useRef<PlayerState>(createPlayer(0, 0, 0));
  const keysRef = useRef<Set<string>>(new Set());
  const noclipRef = useRef(false);
  const sceneRef = useRef<SceneHandle | null>(null);
  const sessionRef = useRef<MatchSession | null>(null);
  if (sessionRef.current === null) sessionRef.current = new MatchSession();

  useEffect(() => {
    const session = sessionRef.current;
    if (!session) return;
    session.onChange = setNet;
    // Invitations may have arrived while this pane was closed, so ask rather
    // than only listening.
    session.refreshInvites();
    return () => {
      session.disconnect();
    };
  }, []);

  // Who could be invited. Refreshed on a timer because presence changes without
  // anything in this pane happening — a friend closing their laptop is not an
  // event we would otherwise hear about.
  useEffect(() => {
    let cancelled = false;
    const load = () => {
      void listInvitees()
        .then((list) => {
          if (!cancelled) setInvitees(list);
        })
        .catch(() => {
          /* the roster is optional here; an empty invite list is a fine answer */
        });
    };
    load();
    const timer = window.setInterval(load, 15_000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  // ---- discover the install and its maps ------------------------------------------

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const st = await getInstallStatus();
        if (cancelled) return;
        setStatus(st);
        if (!st.found) return;
        const list = await listMaps();
        if (cancelled) return;
        setMaps(list);
        // ac_desert is a good default: small, open, and obviously recognisable.
        const preferred = list.find((m) => m.name === 'ac_desert') ?? list[0];
        if (preferred) setMapName(preferred.name);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // ---- the three.js scene ---------------------------------------------------------

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;
    let disposed = false;
    let cleanup: (() => void) | undefined;

    void import('three').then((THREE) => {
      if (disposed || !mountRef.current) return;

      const scene = new THREE.Scene();
      scene.background = new THREE.Color(0x0d1117);
      // Fog hides the far clip plane and, on a 256-cube map, is a big win: it
      // stops the whole world reading as flat untextured colour at distance.
      scene.fog = new THREE.Fog(0x0d1117, 60, 320);

      const camera = new THREE.PerspectiveCamera(75, 1, 0.1, 600);
      const renderer = new THREE.WebGLRenderer({ antialias: true });
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
      mountRef.current.appendChild(renderer.domElement);
      renderer.domElement.style.display = 'block';
      renderer.domElement.style.width = '100%';
      renderer.domElement.style.height = '100%';
      renderer.domElement.style.cursor = 'crosshair';

      // Hemisphere light alone reads flat; the directional adds enough gradient
      // to tell walls from floors before real textures exist.
      scene.add(new THREE.HemisphereLight(0xbfd4ff, 0x33302c, 2.0));
      const sun = new THREE.DirectionalLight(0xffffff, 1.1);
      sun.position.set(0.6, 1, 0.35);
      scene.add(sun);

      let mesh: import('three').Mesh | null = null;
      const material = new THREE.MeshLambertMaterial({ vertexColors: true });
      const avatars = new AvatarPool(THREE, scene);

      const setMesh = (world: World): number => {
        if (mesh) {
          scene.remove(mesh);
          mesh.geometry.dispose();
        }
        const data = buildWorldMesh(world);
        const geo = new THREE.BufferGeometry();
        geo.setAttribute('position', new THREE.BufferAttribute(data.positions, 3));
        geo.setAttribute('normal', new THREE.BufferAttribute(data.normals, 3));
        geo.setAttribute('color', new THREE.BufferAttribute(data.colors, 3));
        geo.computeBoundingSphere();
        mesh = new THREE.Mesh(geo, material);
        scene.add(mesh);
        return data.triangles;
      };

      const resize = () => {
        const el = mountRef.current;
        if (!el) return;
        const w = el.clientWidth || 1;
        const h = el.clientHeight || 1;
        renderer.setSize(w, h, false);
        camera.aspect = w / h;
        camera.updateProjectionMatrix();
      };
      resize();
      const observer = new ResizeObserver(resize);
      observer.observe(mountRef.current);

      let raf = 0;
      let last = performance.now();
      let fpsAccum = 0;
      let fpsFrames = 0;
      let hudAccum = 0;
      let remote: PlayerRow[] = [];

      const frame = (now: number) => {
        raf = requestAnimationFrame(frame);
        const dt = Math.max(0, (now - last) / 1000);
        last = now;

        const world = worldRef.current;
        const player = playerRef.current;
        const session = sessionRef.current;
        const online = session != null && session.state.status === 'joined';

        if (world) {
          const keys = keysRef.current;
          const forward =
            (keys.has('KeyW') || keys.has('ArrowUp') ? 1 : 0) -
            (keys.has('KeyS') || keys.has('ArrowDown') ? 1 : 0);
          const strafe =
            (keys.has('KeyD') || keys.has('ArrowRight') ? 1 : 0) -
            (keys.has('KeyA') || keys.has('ArrowLeft') ? 1 : 0);
          const input = {
            forward,
            strafe,
            jump: keys.has('Space'),
            // Noclip is a local sightseeing tool. The server has no such move, so
            // in a match it would desync on the very first frame.
            noclip: !online && noclipRef.current,
          };

          if (online && session) {
            // Correct against the newest snapshot *before* predicting this
            // frame, so the frame we are about to draw is built on the
            // authoritative state rather than on top of a stale error.
            const correction = session.pendingCorrection;
            if (correction) {
              session.predictor.reconcile(world, player, correction.row, correction.ack);
              session.pendingCorrection = null;
            }
            session.queue(session.predictor.record(world, player, input, dt));
            session.predictor.decay(dt);
          } else {
            step(world, player, input, dt);
          }
        }

        if (session) {
          session.pump(now);
          remote = online ? session.snapshots.sample(now, session.state.playerId) : [];
          avatars.sync(remote);
        }

        // Cube (x, y, height) → three (x, height, z). The correction offset is
        // visual only: the simulation stays exactly where the server says.
        const c = online && session ? session.predictor.correction : NO_CORRECTION;
        camera.position.set(player.x + c.x, eyeHeight(player) + c.z, player.y + c.y);
        // YXZ so yaw is applied before pitch; the default XYZ order rolls the
        // camera as you look around.
        camera.rotation.set(player.pitch, -player.yaw - Math.PI / 2, 0, 'YXZ');
        renderer.render(scene, camera);

        fpsAccum += dt;
        fpsFrames += 1;
        hudAccum += dt;
        if (hudAccum >= 0.25) {
          const fps = fpsFrames / Math.max(fpsAccum, 1e-6);
          setHud((h) => ({
            ...h,
            fps: Math.round(fps),
            x: player.x,
            y: player.y,
            z: player.z,
            onGround: player.onGround,
            error: session ? session.predictor.lastError : 0,
          }));
          fpsAccum = 0;
          fpsFrames = 0;
          hudAccum = 0;
        }
      };
      raf = requestAnimationFrame(frame);

      sceneRef.current = { setMesh, avatars, camera: camera as never };

      cleanup = () => {
        cancelAnimationFrame(raf);
        observer.disconnect();
        avatars.dispose();
        if (mesh) mesh.geometry.dispose();
        material.dispose();
        renderer.dispose();
        renderer.domElement.remove();
        sceneRef.current = null;
      };
    });

    return () => {
      disposed = true;
      cleanup?.();
    };
  }, []);

  // ---- load the selected map ------------------------------------------------------

  useEffect(() => {
    if (!mapName) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    void (async () => {
      try {
        const [mapInfo, cubes] = await Promise.all([getMapInfo(mapName), getMapCubes(mapName)]);
        if (cancelled) return;
        const world = new World(mapInfo, cubes);
        worldRef.current = world;
        setInfo(mapInfo);

        const spawn = world.spawns()[0];
        playerRef.current = spawn
          ? spawnAt(world, spawn)
          : createPlayer(world.ssize / 2, world.ssize / 2, 0);

        // The scene may still be lazy-loading three; retry briefly rather than
        // dropping the mesh on the floor.
        const attach = (tries: number) => {
          const s = sceneRef.current;
          if (!s) {
            if (tries > 0) window.setTimeout(() => attach(tries - 1), 100);
            return;
          }
          const triangles = s.setMesh(world);
          setHud((h) => ({ ...h, triangles }));
          setLoading(false);
        };
        attach(30);
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : String(e));
          setLoading(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [mapName]);

  // Changing map while in a match would put us in a room simulating a different
  // world, so the match is left rather than silently desynced.
  useEffect(() => {
    const session = sessionRef.current;
    if (session && session.state.status === 'joined' && session.state.map !== mapName) {
      session.leave();
    }
  }, [mapName]);

  // ---- input ----------------------------------------------------------------------

  const onCanvasClick = useCallback(() => {
    mountRef.current?.querySelector('canvas')?.requestPointerLock();
  }, []);

  useEffect(() => {
    const el = mountRef.current;
    if (!el) return;

    const onPointerLockChange = () => {
      const canvas = el.querySelector('canvas');
      setLocked(document.pointerLockElement === canvas);
    };
    const onMouseMove = (e: MouseEvent) => {
      const canvas = el.querySelector('canvas');
      if (document.pointerLockElement !== canvas) return;
      const p = playerRef.current;
      p.yaw -= e.movementX * 0.0022;
      p.pitch -= e.movementY * 0.0022;
      // Just under a right angle: exactly ±90° makes the view flip over.
      const limit = Math.PI / 2 - 0.001;
      p.pitch = Math.max(-limit, Math.min(limit, p.pitch));
    };
    const onKeyDown = (e: KeyboardEvent) => {
      const canvas = el.querySelector('canvas');
      if (document.pointerLockElement !== canvas) return;
      if (!MOVEMENT_KEYS.has(e.code)) return;
      // Only swallow keys while the pointer is locked, so the command palette
      // and every other shortcut keep working when it isn't.
      e.preventDefault();
      if (e.code === 'KeyV') noclipRef.current = !noclipRef.current;
      keysRef.current.add(e.code);
    };
    const onKeyUp = (e: KeyboardEvent) => keysRef.current.delete(e.code);
    const onBlur = () => keysRef.current.clear();

    document.addEventListener('pointerlockchange', onPointerLockChange);
    document.addEventListener('mousemove', onMouseMove);
    window.addEventListener('keydown', onKeyDown);
    window.addEventListener('keyup', onKeyUp);
    window.addEventListener('blur', onBlur);
    return () => {
      document.removeEventListener('pointerlockchange', onPointerLockChange);
      document.removeEventListener('mousemove', onMouseMove);
      window.removeEventListener('keydown', onKeyDown);
      window.removeEventListener('keyup', onKeyUp);
      window.removeEventListener('blur', onBlur);
    };
  }, []);

  const respawn = useCallback(() => {
    const session = sessionRef.current;
    // In a match the server owns spawn points; asking it keeps everyone's idea
    // of where we are in agreement.
    if (session && session.state.status === 'joined') {
      session.respawn();
      return;
    }
    const world = worldRef.current;
    if (!world) return;
    const spawns = world.spawns();
    const spawn = spawns[Math.floor(Math.random() * spawns.length)];
    if (spawn) playerRef.current = spawnAt(world, spawn);
  }, []);

  const toggleMatch = useCallback(() => {
    const session = sessionRef.current;
    if (!session || !mapName) return;
    if (session.state.status === 'joined' || session.state.status === 'joining') {
      session.leave();
    } else {
      localStorage.setItem(NAME_KEY, playerName);
      session.join(mapName, playerName);
    }
  }, [mapName, playerName]);

  const sendInvite = useCallback(() => {
    const session = sessionRef.current;
    if (session && inviteWho) session.invite(inviteWho);
  }, [inviteWho]);

  const acceptInvite = useCallback(
    (room: string, map: string, host: string) => {
      const session = sessionRef.current;
      if (!session) return;
      localStorage.setItem(NAME_KEY, playerName);
      // Load their map before joining: the snapshots are positions in *that*
      // world, and rendering them against a different one is nonsense.
      setMapName(map);
      session.join(map, playerName, room, host);
    },
    [playerName],
  );

  // Let the agent see where it is, what map is loaded, and who else is here.
  useAgentContext(() => ({
    map: info ? { name: info.name, title: info.title, size: info.ssize } : null,
    position: { x: Math.round(hud.x), y: Math.round(hud.y), z: Math.round(hud.z) },
    onGround: hud.onGround,
    triangles: hud.triangles,
    installed: status?.found ?? false,
    match:
      net.status === 'joined'
        ? {
            room: net.room,
            rtt: Math.round(net.rtt),
            players: net.peers.map((p) => ({ name: p.name, team: p.team, stale: p.stale })),
          }
        : null,
  }));

  // ---- render ---------------------------------------------------------------------

  if (status && !status.found) {
    return (
      <div style={{ padding: '1rem', color: 'var(--text-dim)', fontSize: '0.85rem' }}>
        <h3 style={{ margin: '0 0 0.5rem', color: 'var(--text)' }}>No AssaultCube install</h3>
        <p>{status.message}</p>
        <p>
          Set <code>hassault.installPath</code> in Settings to the folder containing{' '}
          <code>packages/maps</code>. Game content is read from your own copy and is never bundled
          with this app.
        </p>
      </div>
    );
  }

  const online = net.status === 'joined';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '0.5rem',
          padding: '0.4rem 0.6rem',
          borderBottom: '1px solid var(--border, #2a2a2a)',
          fontSize: '0.8rem',
          flexShrink: 0,
        }}
      >
        <select
          value={mapName}
          onChange={(e) => setMapName(e.target.value)}
          style={{ maxWidth: 160 }}
        >
          {maps.map((m) => (
            <option key={m.name} value={m.name}>
              {m.name}
            </option>
          ))}
        </select>
        <button onClick={respawn} disabled={!info}>
          Respawn
        </button>
        <input
          value={playerName}
          onChange={(e) => setPlayerName(e.target.value.slice(0, 24))}
          disabled={online}
          aria-label="Player name"
          style={{ width: 110 }}
        />
        <button onClick={toggleMatch} disabled={!info}>
          {online ? 'Leave match' : net.status === 'joining' ? 'Joining…' : 'Join match'}
        </button>
        {online && !net.host && invitees.length > 0 && (
          <>
            <select
              value={inviteWho}
              onChange={(e) => setInviteWho(e.target.value)}
              aria-label="Invite a friend"
              style={{ maxWidth: 130 }}
            >
              <option value="">Invite…</option>
              {invitees.map((f) => (
                <option key={f.person_id} value={f.friend_code} disabled={!f.can_play}>
                  {f.name}
                  {f.can_play ? '' : ' (no match support)'}
                </option>
              ))}
            </select>
            <button onClick={sendInvite} disabled={!inviteWho}>
              Send
            </button>
          </>
        )}
        {online && (
          <span style={{ color: 'var(--text-dim)', whiteSpace: 'nowrap' }}>
            {net.room} · {net.peers.length} in · {Math.round(net.rtt)} ms
            {net.host ? ` · guest on ${net.host.slice(0, 8)}` : ''}
          </span>
        )}
        {net.status === 'error' && <span style={{ color: '#f85149' }}>{net.error}</span>}
        {info && !online && (
          <span
            style={{
              color: 'var(--text-dim)',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {info.title}
          </span>
        )}
        <span style={{ marginLeft: 'auto', color: 'var(--text-dim)', whiteSpace: 'nowrap' }}>
          {hud.fps} fps · {(hud.triangles / 1000).toFixed(0)}k tris
        </span>
      </div>

      <div style={{ position: 'relative', flex: 1, minHeight: 0 }}>
        <div ref={mountRef} onClick={onCanvasClick} style={{ position: 'absolute', inset: 0 }} />

        {net.invites.length > 0 && (
          <div
            style={{
              position: 'absolute',
              left: 8,
              top: 8,
              display: 'flex',
              flexDirection: 'column',
              gap: '0.35rem',
              zIndex: 2,
            }}
          >
            {net.invites.map((invite) => (
              <div
                key={invite.room}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '0.5rem',
                  background: 'rgba(13,17,23,0.9)',
                  border: '1px solid var(--border, #2a2a2a)',
                  borderRadius: 6,
                  padding: '0.4rem 0.6rem',
                  fontSize: '0.78rem',
                  color: 'var(--text)',
                }}
              >
                <span>
                  <strong>{invite.hostName}</strong> invited you to <code>{invite.map}</code>
                </span>
                <button onClick={() => acceptInvite(invite.room, invite.map, invite.host)}>
                  Join
                </button>
                <button onClick={() => sessionRef.current?.dismissInvite(invite.room)}>
                  Dismiss
                </button>
              </div>
            ))}
          </div>
        )}

        {online && net.peers.length > 0 && (
          <div
            style={{
              position: 'absolute',
              right: 8,
              top: 8,
              pointerEvents: 'none',
              fontFamily: 'monospace',
              fontSize: '0.7rem',
              color: 'rgba(255,255,255,0.8)',
              background: 'rgba(13,17,23,0.55)',
              borderRadius: 4,
              padding: '0.3rem 0.5rem',
              lineHeight: 1.5,
            }}
          >
            {net.peers.map((p) => (
              <div key={p.id} style={{ opacity: p.stale ? 0.45 : 1 }}>
                <span style={{ color: p.team === 1 ? '#7fb2e5' : '#e0b96a' }}>●</span> {p.name}
                {p.id === net.playerId ? ' (you)' : ''} · {Math.round(p.rtt)} ms
                {p.stale ? ' · lagging' : ''}
              </div>
            ))}
          </div>
        )}

        {!locked && (
          <div
            style={{
              position: 'absolute',
              inset: 0,
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              justifyContent: 'center',
              gap: '0.4rem',
              background: 'rgba(13,17,23,0.72)',
              pointerEvents: 'none',
              color: 'var(--text)',
              fontSize: '0.85rem',
              textAlign: 'center',
            }}
          >
            {loading ? (
              <strong>Loading {mapName}…</strong>
            ) : error ? (
              <strong style={{ color: '#f85149' }}>{error}</strong>
            ) : (
              <>
                <strong>Click to play</strong>
                <span style={{ color: 'var(--text-dim)' }}>
                  WASD move · mouse look · Space jump{online ? '' : ' · V noclip'} · Esc release
                </span>
                {online && (
                  <span style={{ color: 'var(--text-dim)' }}>
                    In match {net.room} — noclip is disabled while the server is simulating you.
                  </span>
                )}
              </>
            )}
          </div>
        )}

        {locked && (
          <>
            <div
              style={{
                position: 'absolute',
                left: '50%',
                top: '50%',
                width: 3,
                height: 3,
                marginLeft: -1.5,
                marginTop: -1.5,
                borderRadius: '50%',
                background: '#fff',
                opacity: 0.75,
                pointerEvents: 'none',
              }}
            />
            <div
              style={{
                position: 'absolute',
                left: 8,
                bottom: 8,
                pointerEvents: 'none',
                fontFamily: 'monospace',
                fontSize: '0.7rem',
                color: 'rgba(255,255,255,0.7)',
              }}
            >
              x {hud.x.toFixed(1)} y {hud.y.toFixed(1)} z {hud.z.toFixed(1)}
              {hud.onGround ? '' : ' · airborne'}
              {!online && noclipRef.current ? ' · noclip' : ''}
              {online ? ` · ${Math.round(net.rtt)} ms · err ${hud.error.toFixed(2)}` : ''}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
