import { useCallback, useEffect, useRef, useState } from 'react';

import { useAgentContext } from '../../agent-context';
import {
  getInstallStatus,
  getMapCubes,
  getMapInfo,
  getSession,
  listInvitees,
  listMaps,
  listWeapons,
  type InstallStatus,
  type Invitee,
  type MapInfo,
  type MapSummary,
  type SessionInfo,
  type WeaponSpec,
} from './api';
import { AvatarPool } from './avatars';
import { createBackdrop, type Backdrop } from './backdrop';
import {
  EMPTY_PROGRESS,
  SIGNED_OUT,
  acceptsGameInput,
  advance,
  bootPhase,
  type BootProgress,
} from './boot';
import { BootOverlay } from './BootOverlay';
import { ShotController } from './combat';
import { EffectsPool } from './effects';
import { buildWorldMesh } from './geometry';
import type { PlayerRow } from './net';
import { createPlayer, eyeHeight, spawnAt, step, type PlayerState } from './player';
import { installReveal, type Reveal } from './reveal';
import { MatchSession, type SessionState } from './session';
import { World } from './world';

/**
 * three, imported once per page rather than once per mount.
 *
 * The panel is a singleton pane, but it can be closed and reopened, and a dynamic
 * `import()` inside the effect makes the *promise* per-mount even though the
 * module is cached. Hoisting it gives the boot sequence something to await that is
 * already resolved on a second open — a reopened pane shows no renderer stage at
 * all, which is correct: there is nothing left to load.
 */
let threeModule: Promise<typeof import('three')> | null = null;
function loadThree(): Promise<typeof import('three')> {
  if (!threeModule) threeModule = import('three');
  return threeModule;
}

const prefersReducedMotion = (): boolean =>
  typeof window.matchMedia === 'function' &&
  window.matchMedia('(prefers-reduced-motion: reduce)').matches;

/**
 * How long the world takes to assemble.
 *
 * Deliberately a duration rather than a function of load progress: the map route
 * sets `Cache-Control: max-age=3600`, so a warm reload downloads in one frame and
 * a build tied to bytes would be over before it was visible.
 */
const REVEAL_MS = 2600;

/** A signed-out `SessionInfo`, for when the backend can't be reached at all. */
const SIGNED_OUT_ACCOUNT: SessionInfo = {
  signed_in: false,
  account_id: null,
  display_name: null,
  callsign: null,
  enlisted: false,
};

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
const CONSUMED_KEYS = new Set([
  'KeyW',
  'KeyA',
  'KeyS',
  'KeyD',
  'Space',
  'ShiftLeft',
  'KeyV',
  'KeyR',
  'Digit1',
  'Digit2',
  'Digit3',
  'Digit4',
  'Digit5',
  'Tab',
  'ArrowUp',
  'ArrowDown',
  'ArrowLeft',
  'ArrowRight',
]);

const NO_CORRECTION = { x: 0, y: 0, z: 0 };
/** How long a hitmarker and a damage flash stay on screen. */
const FLASH_MS = 220;
/** Team tint used for tracers and the scoreboard: CLA sand, RVSF blue. */
const TEAM_COLORS = [0xd9a441, 0x4c8fd4];

const EMPTY_SESSION: SessionState = {
  status: 'idle',
  room: '',
  map: '',
  playerId: '',
  peers: [],
  error: '',
  rtt: 0,
  you: null,
  scores: [0, 0],
  killfeed: [],
  host: '',
  invites: [],
};

interface SceneHandle {
  setMesh: (w: World) => number;
  avatars: AvatarPool;
  reveal: Reveal;
  backdrop: Backdrop;
  camera: {
    position: { set: (x: number, y: number, z: number) => void };
    rotation: { set: (x: number, y: number, z: number, order?: string) => void };
  };
}

/**
 * HorribleAssault: walk around a real AssaultCube map, rendered in WebGL from the
 * cube grid the backend serves — alone, or in a match against other people on the
 * fabric and against bots.
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
  const [error, setError] = useState<string | null>(null);
  const [locked, setLocked] = useState(false);

  // ---- the boot sequence ----------------------------------------------------
  //
  // `progress` is real work completed (see boot.ts), `account` is who this node
  // plays as, and `deployed` is the player having chosen to enter. Together they
  // decide which screen the pane shows.
  const [progress, setProgress] = useState<BootProgress>(EMPTY_PROGRESS);
  const [bytes, setBytes] = useState<{ loaded: number; total: number | null }>({
    loaded: 0,
    total: null,
  });
  const [account, setAccount] = useState<SessionInfo | null>(null);
  const [deployed, setDeployed] = useState(false);

  const phase = bootPhase(progress, account ?? SIGNED_OUT, deployed);
  // Identity is the account's callsign. There is deliberately no name input any
  // more: the backend ignores a client-supplied name outright (see
  // `channel._signed_in_callsign`), so offering one would only be a lie.
  const playerName = account?.callsign ?? '';
  const [hud, setHud] = useState<Hud>({
    fps: 0,
    triangles: 0,
    x: 0,
    y: 0,
    z: 0,
    onGround: false,
    error: 0,
  });
  const [net, setNet] = useState<SessionState>(EMPTY_SESSION);
  const [invitees, setInvitees] = useState<Invitee[]>([]);
  const [inviteWho, setInviteWho] = useState('');
  const [weapons, setWeapons] = useState<WeaponSpec[]>([]);
  const [botSkill, setBotSkill] = useState('normal');
  const [showScores, setShowScores] = useState(false);
  /** Timestamps, compared against `Date.now()` so a stale one simply expires. */
  const [flash, setFlash] = useState({ hit: 0, killed: 0, hurt: 0 });

  // Mutable simulation state, kept out of React: this updates every frame and
  // re-rendering the component 60 times a second would be absurd.
  const worldRef = useRef<World | null>(null);
  const playerRef = useRef<PlayerState>(createPlayer(0, 0, 0));
  const keysRef = useRef<Set<string>>(new Set());
  const noclipRef = useRef(false);
  const sceneRef = useRef<SceneHandle | null>(null);
  const sessionRef = useRef<MatchSession | null>(null);
  const shotsRef = useRef<ShotController | null>(null);
  if (sessionRef.current === null) sessionRef.current = new MatchSession();
  if (shotsRef.current === null) shotsRef.current = new ShotController();

  // The frame loop is built once and never re-created, so anything it needs to
  // read per-frame from React state has to arrive by ref.
  const phaseRef = useRef(phase);
  phaseRef.current = phase;

  // Resolved when the scene exists. Replaces polling for `sceneRef.current`: the
  // map load and the renderer load are genuinely concurrent, and awaiting is both
  // exact and instant, where a retry loop was neither.
  const sceneReadyRef = useRef<{ promise: Promise<void>; resolve: () => void } | null>(null);
  if (sceneReadyRef.current === null) {
    let resolve!: () => void;
    const promise = new Promise<void>((r) => {
      resolve = r;
    });
    sceneReadyRef.current = { promise, resolve };
  }

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

  /** Re-read who we are. Called on mount and after every sign-in or rename. */
  const refreshAccount = useCallback(async (fromServer = false): Promise<SessionInfo> => {
    const info = await getSession(fromServer);
    setAccount(info);
    return info;
  }, []);

  useEffect(() => {
    // A failure here is signed-out, not an error banner: the sign-in screen is
    // already the right answer, and a backend that can't say is not signed in.
    void refreshAccount().catch(() => setAccount(SIGNED_OUT_ACCOUNT));
  }, [refreshAccount]);

  // The loadout, fetched rather than hardcoded: the client needs each weapon's
  // fire interval so it does not send input the server would only discard, and a
  // second copy of those numbers here is a drift trap.
  useEffect(() => {
    let cancelled = false;
    void listWeapons()
      .then((specs) => {
        if (cancelled) return;
        setWeapons(specs);
        shotsRef.current?.setWeapons(specs, Math.min(2, specs.length - 1));
      })
      .catch(() => {
        /* no loadout means no shooting; movement and the map still work */
      });
    return () => {
      cancelled = true;
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

  // Hit feedback. Driven off the authoritative `you`, not off pulling the
  // trigger: a hitmarker that appears because you fired is a lie.
  const lastHpRef = useRef(100);
  const [, forceTick] = useState(0);
  useEffect(() => {
    const you = net.you;
    if (!you) return;
    const hit = you.hits.length > 0;
    const killed = you.hits.some((h) => h.killed);
    const hurt = you.hp < lastHpRef.current;
    lastHpRef.current = you.hp;
    // Only on an actual event: this effect runs on every emitted snapshot, and
    // setting state unconditionally would re-render the pane for nothing.
    if (!hit && !killed && !hurt) return;
    const at = Date.now();
    setFlash((f) => ({
      hit: hit ? at : f.hit,
      killed: killed ? at : f.killed,
      hurt: hurt ? at : f.hurt,
    }));
  }, [net.you]);

  // Flashes expire by wall clock, so something has to re-render when they do —
  // otherwise the crosshair keeps its hitmarker until the next snapshot that
  // happens to change something else.
  useEffect(() => {
    const newest = Math.max(flash.hit, flash.killed, flash.hurt);
    const remaining = newest + FLASH_MS * 2 + 20 - Date.now();
    if (newest === 0 || remaining <= 0) return;
    const timer = window.setTimeout(() => forceTick((n) => n + 1), remaining);
    return () => window.clearTimeout(timer);
  }, [flash]);

  // ---- discover the install and its maps ------------------------------------------

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const st = await getInstallStatus();
        if (cancelled) return;
        setStatus(st);
        const list = await listMaps();
        if (cancelled) return;
        setMaps(list);
        setProgress((p) => advance(p, { install: 1 }));
        // Default to a map that ships with the app, so the first thing anyone
        // sees exists on every machine. Falling back to whatever is first keeps
        // the panel usable if the bundled maps somehow failed to build.
        const preferred =
          list.find((m) => m.name === 'hd_atrium') ??
          list.find((m) => m.source === 'bundled') ??
          list[0];
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
    // What the cinematic camera orbits: the loaded map's real extent, filled in
    // by `setMesh`. A sane default so the first frames before any map aren't NaN.
    const bounds = { current: { cx: 64, cz: 64, extent: 64 } };

    void loadThree().then((THREE) => {
      if (disposed || !mountRef.current) return;
      setProgress((p) => advance(p, { renderer: 1 }));

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
      // Patched, not replaced: the build animation runs through the same lit
      // material the finished world uses, so nothing pops when it ends.
      const reveal = installReveal(material);
      const backdrop = createBackdrop(THREE, scene);
      const avatars = new AvatarPool(THREE, scene);
      const effects = new EffectsPool(THREE, scene);

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

        // Frame on the geometry's own bounds, not on `ssize`. A map's grid is
        // mostly empty border — ac_desert's buildings occupy a fraction of its
        // 128 cubes — so orbiting the grid centre at a grid-sized radius puts the
        // level in the far distance, small and off to one side.
        const sphere = geo.boundingSphere;
        const cx = sphere ? sphere.center.x : world.ssize / 2;
        const cz = sphere ? sphere.center.z : world.ssize / 2;
        const extent = Math.max(sphere ? sphere.radius : world.ssize / 2, 8);
        bounds.current = { cx, cz, extent };

        // Aim the build at *this* map. The material outlives a map change, so a
        // reveal left completed would show the next map already assembled.
        reveal.fit([cx, cz], extent * 1.05, Math.max(extent * 0.6, 1));
        backdrop.fit([cx, cz], extent * 2);
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
      const started = last;
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
        const shots = shotsRef.current;
        const online = session != null && session.state.status === 'joined';
        const alive = !online || (session?.state.you?.alive ?? true);

        if (world) {
          const keys = keysRef.current;
          // A dead player's input is discarded server-side, so predicting
          // movement from it would only be a correction waiting to happen.
          const forward = alive
            ? (keys.has('KeyW') || keys.has('ArrowUp') ? 1 : 0) -
              (keys.has('KeyS') || keys.has('ArrowDown') ? 1 : 0)
            : 0;
          const strafe = alive
            ? (keys.has('KeyD') || keys.has('ArrowRight') ? 1 : 0) -
              (keys.has('KeyA') || keys.has('ArrowLeft') ? 1 : 0)
            : 0;
          const input = {
            forward,
            strafe,
            jump: alive && keys.has('Space'),
            // Noclip is a local sightseeing tool. The server has no such move, so
            // in a match it would desync on the very first frame.
            noclip: !online && noclipRef.current,
          };

          if (online && session && shots) {
            // Correct against the newest snapshot *before* predicting this
            // frame, so the frame we are about to draw is built on the
            // authoritative state rather than on top of a stale error.
            const correction = session.pendingCorrection;
            if (correction) {
              session.predictor.reconcile(world, player, correction.row, correction.ack);
              session.pendingCorrection = null;
            }
            // Recoil is a local move on the camera, exactly like the mouse: the
            // server reads whatever angles the resulting command carries.
            const kick = shots.recoil(dt);
            player.yaw += kick.yaw;
            player.pitch = clampPitch(player.pitch + kick.pitch);
            // The instant we are *rendering*, which is what the server rewinds a
            // shot to. Not sent until the buffer has an offset to derive it from.
            const renderT = session.snapshots.renderTime(now);
            const intent = shots.frame(
              now,
              Number.isFinite(renderT) ? renderT : 0,
              session.state.you,
            );
            session.queue(session.predictor.record(world, player, input, dt, intent));
            session.predictor.decay(dt);
          } else {
            shots?.frame(now, 0, null);
            step(world, player, input, dt);
          }
        }

        if (session) {
          session.pump(now);
          remote = online ? session.snapshots.sample(now, session.state.playerId) : [];
          avatars.sync(remote);
          if (session.pendingShots.length > 0) {
            // Teams come from the roster, not from `remote` — that one excludes
            // us, and our own tracer needs a colour too.
            const teamOf = new Map(session.state.peers.map((p) => [p.id, p.team]));
            for (const fx of session.pendingShots) {
              effects.shot(
                fx.origin,
                fx.ends,
                TEAM_COLORS[teamOf.get(fx.id) ?? 0] ?? 0xffffff,
                fx.id === session.state.playerId,
              );
            }
            session.pendingShots = [];
          }
        }
        effects.update(dt);

        const elapsed = (now - started) / 1000;
        backdrop.update(elapsed);

        if (acceptsGameInput(phaseRef.current)) {
          backdrop.setOpacity(0);
          // Cube (x, y, height) → three (x, height, z). The correction offset is
          // visual only: the simulation stays exactly where the server says.
          const c = online && session ? session.predictor.correction : NO_CORRECTION;
          camera.position.set(player.x + c.x, eyeHeight(player) + c.z, player.y + c.y);
          // YXZ so yaw is applied before pitch; the default XYZ order rolls the
          // camera as you look around.
          camera.rotation.set(player.pitch, -player.yaw - Math.PI / 2, 0, 'YXZ');
        } else {
          // Before you deploy the camera flies the map rather than standing in
          // it: a slow orbit is what makes the sign-in screen read as a game's
          // front door instead of a form over a frozen screenshot.
          backdrop.setOpacity(1);
          // Close and low: a distant top-down orbit reads as a minimap, not as a
          // place. This sits about level with the rooftops and drifts.
          const { cx, cz, extent } = bounds.current;
          const radius = extent * 1.25;
          const angle = elapsed * 0.055;
          camera.position.set(
            cx + Math.cos(angle) * radius,
            extent * 0.45 + Math.sin(elapsed * 0.19) * extent * 0.06,
            cz + Math.sin(angle) * radius,
          );
          camera.lookAt(cx, extent * 0.1, cz);
        }
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

      sceneRef.current = { setMesh, avatars, reveal, backdrop, camera: camera as never };
      // Unblocks the map load, which has been waiting rather than polling.
      sceneReadyRef.current?.resolve();

      cleanup = () => {
        cancelAnimationFrame(raf);
        observer.disconnect();
        avatars.dispose();
        effects.dispose();
        backdrop.dispose();
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
    let revealRaf = 0;
    setError(null);
    // A new map re-runs the download, mesh and build stages. `renderer` and
    // `install` stay done — they are not per-map work.
    setProgress((p) => ({ ...p, map: 0, mesh: 0, reveal: 0 }));
    setBytes({ loaded: 0, total: null });

    void (async () => {
      try {
        // Sequential, not `Promise.all`: the metadata is cheap and tells us how
        // big the grid should be, which is what makes the byte counter meaningful
        // before the first chunk lands.
        const mapInfo = await getMapInfo(mapName);
        if (cancelled) return;
        const expected = mapInfo.ssize * mapInfo.ssize * 9;
        setBytes({ loaded: 0, total: expected });

        const cubes = await getMapCubes(mapName, (loaded, total) => {
          if (cancelled) return;
          const size = total ?? expected;
          setBytes({ loaded, total: size });
          setProgress((p) => advance(p, { map: size > 0 ? loaded / size : 1 }));
        });
        if (cancelled) return;
        setProgress((p) => advance(p, { map: 1 }));

        const world = new World(mapInfo, cubes);
        worldRef.current = world;
        setInfo(mapInfo);

        const spawn = world.spawns()[0];
        playerRef.current = spawn
          ? spawnAt(world, spawn)
          : createPlayer(world.ssize / 2, world.ssize / 2, 0);

        // Await the renderer rather than poll for it. The two loads are genuinely
        // concurrent, and the retry loop this replaces would silently drop the
        // mesh on a machine slow enough to exhaust its attempts.
        await sceneReadyRef.current?.promise;
        if (cancelled) return;
        const scene = sceneRef.current;
        if (!scene) return;

        const triangles = scene.setMesh(world);
        setHud((h) => ({ ...h, triangles }));
        setProgress((p) => advance(p, { mesh: 1 }));

        // The build. Runs on its own clock rather than on load progress: the map
        // is already here by now, and the point of the animation is to show the
        // world arriving, not to stall until it has.
        if (prefersReducedMotion()) {
          scene.reveal.complete();
          setProgress((p) => advance(p, { reveal: 1 }));
          return;
        }
        const startedAt = performance.now();
        const tick = (now: number) => {
          if (cancelled) return;
          const t = Math.min(1, (now - startedAt) / REVEAL_MS);
          scene.reveal.set(t);
          setProgress((p) => advance(p, { reveal: t }));
          if (t < 1) revealRaf = requestAnimationFrame(tick);
        };
        revealRaf = requestAnimationFrame(tick);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => {
      cancelled = true;
      cancelAnimationFrame(revealRaf);
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

  // Guarded on the phase, not on the overlay's visibility. The boot overlay has
  // real inputs in it, and the pre-existing "click to play" layer is deliberately
  // `pointerEvents: none` so clicks fall through to this handler — so without the
  // guard, clicking an email field would grab the pointer instead of focusing it.
  // It must be the handler and not the markup, because Esc during play returns to
  // the unlocked state and re-arms this exact path.
  const onCanvasClick = useCallback(() => {
    if (!acceptsGameInput(phaseRef.current)) return;
    mountRef.current?.querySelector('canvas')?.requestPointerLock();
  }, []);

  useEffect(() => {
    const el = mountRef.current;
    if (!el) return;
    const isLocked = () => document.pointerLockElement === el.querySelector('canvas');

    const onPointerLockChange = () => {
      const held = isLocked();
      setLocked(held);
      // Releasing the pointer must release the trigger too, or a weapon left
      // firing keeps firing into whatever you tabbed away to.
      if (!held) {
        shotsRef.current?.release();
        keysRef.current.clear();
        setShowScores(false);
      }
    };
    const onMouseMove = (e: MouseEvent) => {
      if (!isLocked()) return;
      const p = playerRef.current;
      p.yaw -= e.movementX * 0.0022;
      p.pitch = clampPitch(p.pitch - e.movementY * 0.0022);
    };
    const onMouseDown = (e: MouseEvent) => {
      if (!isLocked() || e.button !== 0) return;
      e.preventDefault();
      shotsRef.current?.press();
    };
    const onMouseUp = (e: MouseEvent) => {
      if (e.button === 0) shotsRef.current?.release();
    };
    const onWheel = (e: WheelEvent) => {
      if (!isLocked()) return;
      e.preventDefault();
      shotsRef.current?.cycle(e.deltaY > 0 ? 1 : -1);
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (!isLocked()) return;
      if (!CONSUMED_KEYS.has(e.code)) return;
      // Only swallow keys while the pointer is locked, so the command palette
      // and every other shortcut keep working when it isn't.
      e.preventDefault();
      if (e.repeat) return;
      if (e.code === 'KeyV') noclipRef.current = !noclipRef.current;
      if (e.code === 'KeyR') shotsRef.current?.requestReload();
      if (e.code === 'Tab') setShowScores(true);
      if (e.code.startsWith('Digit')) {
        shotsRef.current?.select(Number(e.code.slice(5)) - 1);
      }
      keysRef.current.add(e.code);
    };
    const onKeyUp = (e: KeyboardEvent) => {
      if (e.code === 'Tab') setShowScores(false);
      keysRef.current.delete(e.code);
    };
    const onBlur = () => {
      keysRef.current.clear();
      shotsRef.current?.release();
    };

    document.addEventListener('pointerlockchange', onPointerLockChange);
    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mousedown', onMouseDown);
    document.addEventListener('mouseup', onMouseUp);
    el.addEventListener('wheel', onWheel, { passive: false });
    window.addEventListener('keydown', onKeyDown);
    window.addEventListener('keyup', onKeyUp);
    window.addEventListener('blur', onBlur);
    return () => {
      document.removeEventListener('pointerlockchange', onPointerLockChange);
      document.removeEventListener('mousemove', onMouseMove);
      document.removeEventListener('mousedown', onMouseDown);
      document.removeEventListener('mouseup', onMouseUp);
      el.removeEventListener('wheel', onWheel);
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
      shotsRef.current?.reset();
      // The name is sent for the wire's sake; the backend takes the callsign from
      // the account and ignores this entirely.
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
      // Load their map before joining: the snapshots are positions in *that*
      // world, and rendering them against a different one is nonsense.
      setMapName(map);
      shotsRef.current?.reset();
      session.join(map, playerName, room, host);
    },
    [playerName],
  );

  // Let the agent see where it is, what map is loaded, who else is here, and how
  // the fight is going.
  useAgentContext(() => ({
    map: info ? { name: info.name, title: info.title, size: info.ssize } : null,
    position: { x: Math.round(hud.x), y: Math.round(hud.y), z: Math.round(hud.z) },
    onGround: hud.onGround,
    triangles: hud.triangles,
    // Spelled out rather than a bare `installed`, which an agent would read as
    // "cannot play" — the bundled maps play with no install at all.
    mapCount: status?.map_count ?? 0,
    assaultCubeInstalled: status?.found ?? false,
    match:
      net.status === 'joined'
        ? {
            room: net.room,
            rtt: Math.round(net.rtt),
            scores: { CLA: net.scores[0], RVSF: net.scores[1] },
            you: net.you
              ? {
                  health: net.you.hp,
                  alive: net.you.alive,
                  weapon: weapons[net.you.weapon]?.name ?? '',
                  ammo: net.you.ammo,
                  kills: net.you.kills,
                  deaths: net.you.deaths,
                }
              : null,
            players: net.peers.map((p) => ({
              name: p.name,
              team: p.team,
              stale: p.stale,
              bot: p.bot,
              kills: p.kills,
              deaths: p.deaths,
              alive: p.alive,
            })),
          }
        : null,
  }));

  // ---- render ---------------------------------------------------------------------

  // Gated on having a map to play, *not* on having an AssaultCube install: the
  // bundled maps ship with the app, so a missing install is only ever a smaller
  // map list. This is reachable at all in case the bundled maps fail to build.
  if (status && status.map_count === 0) {
    return (
      <div style={{ padding: '1rem', color: 'var(--text-dim)', fontSize: '0.85rem' }}>
        <h3 style={{ margin: '0 0 0.5rem', color: 'var(--text)' }}>No maps available</h3>
        <p>{status.message}</p>
        <p>
          Set <code>hassault.installPath</code> in Settings to the folder containing{' '}
          <code>packages/maps</code>. AssaultCube content is read from your own copy and is never
          bundled with this app.
        </p>
      </div>
    );
  }

  const online = net.status === 'joined';
  const you = net.you;
  const weapon = you ? weapons[you.weapon] : undefined;
  const now = Date.now();
  const showHit = now - flash.hit < FLASH_MS;
  const showKilled = now - flash.killed < FLASH_MS * 2;
  const showHurt = now - flash.hurt < FLASH_MS * 2;
  const crosshairGap = shotsRef.current?.crosshairSpread() ?? 4;

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
          {/* Grouped so it is obvious which maps ship with the app and which
              came from your own AssaultCube — they are different in kind, not
              just in name. The second group is absent without an install. */}
          <optgroup label="Bundled">
            {maps
              .filter((m) => m.source === 'bundled')
              .map((m) => (
                <option key={m.name} value={m.name}>
                  {m.name}
                </option>
              ))}
          </optgroup>
          {maps.some((m) => m.source !== 'bundled') && (
            <optgroup label="AssaultCube">
              {maps
                .filter((m) => m.source !== 'bundled')
                .map((m) => (
                  <option key={m.name} value={m.name}>
                    {m.name}
                  </option>
                ))}
            </optgroup>
          )}
        </select>
        <button onClick={respawn} disabled={!info}>
          Respawn
        </button>
        {/* The callsign, shown not typed: it comes from the account, and the
            backend refuses any name the client supplies. Renaming happens on the
            enlist screen, which owns the uniqueness check. */}
        <span
          title="Your callsign — change it from the sign-in screen"
          style={{
            fontFamily: 'var(--font-mono, monospace)',
            color: 'var(--accent, #6ea8fe)',
            padding: '0 0.2rem',
          }}
        >
          {playerName || '—'}
        </span>
        <button onClick={toggleMatch} disabled={!info}>
          {online ? 'Leave match' : net.status === 'joining' ? 'Joining…' : 'Join match'}
        </button>
        {online && !net.host && (
          <>
            <select
              value={botSkill}
              onChange={(e) => setBotSkill(e.target.value)}
              aria-label="Bot skill"
              style={{ width: 82 }}
            >
              <option value="easy">easy</option>
              <option value="normal">normal</option>
              <option value="hard">hard</option>
            </select>
            <button onClick={() => sessionRef.current?.addBots(1, botSkill)}>+ Bot</button>
            <button
              onClick={() => sessionRef.current?.removeBots(1)}
              disabled={!net.peers.some((p) => p.bot)}
            >
              − Bot
            </button>
          </>
        )}
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
            <span style={{ color: '#d9a441' }}>{net.scores[0]}</span>
            {' · '}
            <span style={{ color: '#4c8fd4' }}>{net.scores[1]}</span>
            {' · '}
            {net.peers.length} in · {Math.round(net.rtt)} ms
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

        {showHurt && (
          <div
            style={{
              position: 'absolute',
              inset: 0,
              pointerEvents: 'none',
              // A vignette rather than a full wash: it says "you are being shot"
              // without hiding the person shooting you.
              boxShadow: 'inset 0 0 120px 30px rgba(220,40,40,0.55)',
            }}
          />
        )}

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

        {online && net.killfeed.length > 0 && (
          <div
            style={{
              position: 'absolute',
              right: 8,
              top: 8,
              pointerEvents: 'none',
              fontFamily: 'monospace',
              fontSize: '0.72rem',
              textAlign: 'right',
              lineHeight: 1.6,
            }}
          >
            {net.killfeed.map((k) => (
              <div
                key={k.id}
                style={{
                  color: k.mine ? '#f0d48a' : 'rgba(255,255,255,0.75)',
                  background: 'rgba(13,17,23,0.55)',
                  borderRadius: 3,
                  padding: '0 0.35rem',
                  display: 'inline-block',
                  marginBottom: 2,
                }}
              >
                {k.text}
              </div>
            ))}
          </div>
        )}

        {online && showScores && (
          <div
            style={{
              position: 'absolute',
              left: '50%',
              top: '12%',
              transform: 'translateX(-50%)',
              minWidth: 340,
              pointerEvents: 'none',
              fontFamily: 'monospace',
              fontSize: '0.75rem',
              color: 'rgba(255,255,255,0.9)',
              background: 'rgba(13,17,23,0.85)',
              border: '1px solid var(--border, #2a2a2a)',
              borderRadius: 6,
              padding: '0.5rem 0.7rem',
            }}
          >
            <div style={{ marginBottom: '0.35rem', opacity: 0.7 }}>
              CLA {net.scores[0]} — {net.scores[1]} RVSF
            </div>
            {[...net.peers]
              .sort((a, b) => b.kills - a.kills || a.deaths - b.deaths)
              .map((p) => (
                <div
                  key={p.id}
                  style={{
                    display: 'flex',
                    gap: '0.5rem',
                    opacity: p.alive ? 1 : 0.45,
                  }}
                >
                  <span style={{ color: p.team === 1 ? '#7fb2e5' : '#e0b96a' }}>●</span>
                  <span style={{ flex: 1 }}>
                    {p.name}
                    {p.id === net.playerId ? ' (you)' : ''}
                  </span>
                  <span style={{ width: 28, textAlign: 'right' }}>{p.kills}</span>
                  <span style={{ width: 28, textAlign: 'right', opacity: 0.6 }}>{p.deaths}</span>
                  <span style={{ width: 52, textAlign: 'right', opacity: 0.6 }}>
                    {p.bot ? 'bot' : `${Math.round(p.rtt)} ms`}
                  </span>
                </div>
              ))}
          </div>
        )}

        {online && you && !you.alive && (
          <div
            style={{
              position: 'absolute',
              inset: 0,
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              justifyContent: 'center',
              pointerEvents: 'none',
              background: 'rgba(60,10,10,0.35)',
              color: '#fff',
            }}
          >
            <strong style={{ fontSize: '1.4rem', letterSpacing: '0.08em' }}>DOWN</strong>
            <span style={{ opacity: 0.8, fontSize: '0.85rem' }}>
              respawning in {Math.max(0, Math.ceil(you.respawnIn))}s
            </span>
          </div>
        )}

        {/* Only once deployed. Before that the boot overlay owns this space, and
            two full-bleed layers would fight — this one is `pointerEvents: none`
            by design, so it would sit invisibly over the sign-in form's buttons
            while still dimming them. */}
        {phase === 'playing' && !locked && (
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
            {error ? (
              <strong style={{ color: '#f85149' }}>{error}</strong>
            ) : (
              <>
                <strong>Click to play</strong>
                <span style={{ color: 'var(--text-dim)' }}>
                  WASD move · mouse look · Space jump{online ? '' : ' · V noclip'} · Esc release
                </span>
                {online ? (
                  <span style={{ color: 'var(--text-dim)' }}>
                    Fire · 1–5 or wheel weapon · R reload · Tab scores
                  </span>
                ) : (
                  <span style={{ color: 'var(--text-dim)' }}>
                    Join a match to shoot — then add bots if nobody else is about.
                  </span>
                )}
              </>
            )}
          </div>
        )}

        {phase !== 'playing' && (
          <BootOverlay
            phase={phase}
            progress={progress}
            bytes={bytes}
            mapName={mapName}
            error={error}
            account={account}
            onSignedIn={() => refreshAccount(true)}
            onDeploy={() => setDeployed(true)}
          />
        )}

        {locked && (
          <>
            <Crosshair gap={crosshairGap} hit={showHit} killed={showKilled} />
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
              {online && you && (
                <div
                  style={{
                    fontSize: '1.5rem',
                    lineHeight: 1.1,
                    color: you.hp > 30 ? '#fff' : '#f85149',
                  }}
                >
                  {you.hp}
                  <span style={{ fontSize: '0.7rem', opacity: 0.6 }}> hp</span>
                  {you.protected && (
                    <span style={{ fontSize: '0.6rem', opacity: 0.7 }}> · spawn shield</span>
                  )}
                </div>
              )}
              x {hud.x.toFixed(1)} y {hud.y.toFixed(1)} z {hud.z.toFixed(1)}
              {hud.onGround ? '' : ' · airborne'}
              {!online && noclipRef.current ? ' · noclip' : ''}
              {online ? ` · ${Math.round(net.rtt)} ms · err ${hud.error.toFixed(2)}` : ''}
            </div>

            {online && you && weapon && (
              <div
                style={{
                  position: 'absolute',
                  right: 12,
                  bottom: 8,
                  pointerEvents: 'none',
                  fontFamily: 'monospace',
                  textAlign: 'right',
                  color: 'rgba(255,255,255,0.85)',
                }}
              >
                <div style={{ fontSize: '0.7rem', opacity: 0.6 }}>{weapon.name}</div>
                <div style={{ fontSize: '1.5rem', lineHeight: 1.1 }}>
                  {weapon.mag > 0 ? you.ammo : '∞'}
                  <span style={{ fontSize: '0.8rem', opacity: 0.55 }}>
                    {weapon.mag > 0 ? ` / ${you.reserve < 0 ? '∞' : you.reserve}` : ''}
                  </span>
                </div>
                {you.reloading && (
                  <div style={{ fontSize: '0.7rem', color: '#f0d48a' }}>reloading…</div>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

/** Just under a right angle: exactly ±90° makes the view flip over. */
function clampPitch(pitch: number): number {
  const limit = Math.PI / 2 - 0.001;
  return Math.max(-limit, Math.min(limit, pitch));
}

/**
 * Four ticks around a centre dot, opening with the weapon's spread.
 *
 * The gap is the honest thing to show: it is the cone the server will actually
 * roll pellets inside, so a shotgun looks like a shotgun without anyone having
 * to read the numbers.
 */
function Crosshair({ gap, hit, killed }: { gap: number; hit: boolean; killed: boolean }) {
  const color = killed ? '#ff6b6b' : hit ? '#ffd166' : 'rgba(255,255,255,0.8)';
  const arm = 6;
  const ticks = [
    { left: -gap - arm, top: -1, width: arm, height: 2 },
    { left: gap, top: -1, width: arm, height: 2 },
    { left: -1, top: -gap - arm, width: 2, height: arm },
    { left: -1, top: gap, width: 2, height: arm },
  ];
  return (
    <div
      style={{
        position: 'absolute',
        left: '50%',
        top: '50%',
        pointerEvents: 'none',
        // Hitmarkers rotate the ticks into an X, which reads instantly and needs
        // no second element to fade in and out.
        transform: hit ? 'rotate(45deg)' : undefined,
      }}
    >
      <div
        style={{
          position: 'absolute',
          left: -1.5,
          top: -1.5,
          width: 3,
          height: 3,
          borderRadius: '50%',
          background: color,
        }}
      />
      {ticks.map((t, i) => (
        <div key={i} style={{ position: 'absolute', background: color, ...t }} />
      ))}
    </div>
  );
}
