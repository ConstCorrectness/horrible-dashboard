import { useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';

import { PaneInstanceContext, useAgentContext } from '../../agent-context';
import { lockEscape, unlockEscape, useCapture } from '../../keymap';
import { setSetting, useSetting } from '../../settings';
import {
  dismissMatchSummary,
  getInstallStatus,
  getLatestMatchSummary,
  browseServers,
  getMapCubes,
  getMapInfo,
  getProcessStatus,
  getSession,
  getSkinInventory,
  launchNativeFps,
  listInvitees,
  listMaps,
  listWeapons,
  type BrowseMatch,
  type InstallStatus,
  type LaunchNativeOptions,
  type Invitee,
  type MapInfo,
  type MapSummary,
  type PostMatchSummary,
  type SessionInfo,
  type WeaponSpec,
} from './api';
import { GameAudio } from './audio';
import { AvatarPool } from './avatars';
import { createBackdrop, type Backdrop } from './backdrop';
import { MatchCompanion } from './panels/MatchCompanion';
import { PostMatchDebrief } from './panels/PostMatchDebrief';
import {
  EMPTY_PROGRESS,
  SIGNED_OUT,
  acceptsGameInput,
  advance,
  bootPhase,
  type BootProgress,
} from './boot';
import { BootOverlay } from './BootOverlay';
import { kickVector, NO_SHOT, ShotController } from './combat';
import {
  codeMap,
  describeControls,
  keyLabel,
  parseControls,
  serializeControls,
  type Bindings,
  type GameAction,
} from './controls';
import { EffectsPool } from './effects';
import { GameMenu } from './GameMenu';
import { buildWorldMesh } from './geometry';
import { MainMenu } from './MainMenu';
import {
  CONTROLS_KEY,
  CROUCH_TOGGLE_KEY,
  FOV_KEY,
  NATIVE_CLIENT_KEY,
  SENSITIVITY_KEY,
  VOLUME_KEY,
} from './menu-panels';
import type { NoiseEvent, PlayerRow, SelfState, Vec3 } from './net';
import {
  applyImpulse,
  applyLook,
  clampPitch,
  createPlayer,
  eyeHeight,
  JUMP_SPEED,
  MOVE_SPEED,
  spawnAt,
  step,
  type PlayerState,
} from './player';
import { installReveal, type Reveal } from './reveal';
import { onJoinRequested, takePendingJoin } from './invite-notify';
import { MatchSession, type SessionState } from './session';
import { TrainingRange } from './training';
import { equippedSkins, WeaponViewModel, type WeaponSkin } from './viewmodel';
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
  username: null,
  suggested_username: '',
  enlisted: false,
};

interface Hud {
  fps: number;
  triangles: number;
  x: number;
  y: number;
  z: number;
  onGround: boolean;
  /** Horizontal speed in cubes per second — the number the movement is about. */
  speed: number;
  /** 0..1 crouch, so the HUD can say so. */
  crouch: number;
  /** View yaw, so the noise ring can rotate into screen space. */
  yaw: number;
  /** Distance the last reconciliation had to correct, in cubes. */
  error: number;
}

const NO_CORRECTION = { x: 0, y: 0, z: 0 };
/** No kick this frame. Hoisted so the frame loop allocates nothing for the
 * overwhelmingly common case of not having fired. */
const NO_KICK: Vec3 = { x: 0, y: 0, z: 0 };

/**
 * Cubes of travel between the player's own footsteps.
 *
 * Mirrors `STRIDE_DISTANCE` in `noise.py`, and deliberately duplicated rather than
 * served: this drives *only* the sound of your own boots, which the server does not
 * send back (it needs no round trip, and a footstep 50 ms late does not sound like
 * one). A drift here makes your own steps land at a slightly different cadence from
 * how others hear them, which is inaudible — whereas a drift in anything the server
 * judges would not be.
 */
const OWN_STRIDE = 4.2;

/** How long a heard noise stays on the direction ring. */
const NOISE_TTL_MS = 900;
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
  /** Vertical field of view in degrees. A setting, so it has to reach the camera
   * after construction rather than only at it. */
  setFov: (degrees: number) => void;
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
  // Identity is the account's username. There is deliberately no name input any
  // more: the backend ignores a client-supplied name outright (see
  // `channel._signed_in_username`), so offering one would only be a lie.
  const playerName = account?.username ?? '';
  const [hud, setHud] = useState<Hud>({
    fps: 0,
    triangles: 0,
    x: 0,
    y: 0,
    z: 0,
    onGround: false,
    speed: 0,
    crouch: 0,
    yaw: 0,
    error: 0,
  });
  const [net, setNet] = useState<SessionState>(EMPTY_SESSION);
  const [invitees, setInvitees] = useState<Invitee[]>([]);
  const [weapons, setWeapons] = useState<WeaponSpec[]>([]);
  /**
   * Why there is no loadout, when there is no loadout.
   *
   * This used to be a swallowed `catch`, on the reasoning that movement and the
   * map still work without weapons. They do — but an empty loadout also means
   * `ShotController` bails on every frame, so the game silently becomes one
   * where the trigger does nothing, in a *match*, with nothing anywhere saying
   * why. A gunless game is never a state to enter quietly.
   */
  const [loadoutError, setLoadoutError] = useState('');
  const [botSkill, setBotSkill] = useState('normal');
  const [showScores, setShowScores] = useState(false);
  /** Timestamps, compared against `Date.now()` so a stale one simply expires. */
  const [flash, setFlash] = useState({ hit: 0, killed: 0, hurt: 0 });
  /**
   * Our own state while training, in the same shape a snapshot delivers.
   *
   * Pushed from the frame loop only when one of its fields actually changes, not
   * every frame: the magazine moves a dozen times a second at most, and a
   * `setState` per frame would re-render the whole panel sixty times a second to
   * redraw a number that did not move.
   */
  const [localYou, setLocalYou] = useState<SelfState | null>(null);
  /**
   * Current zoom step, mirrored out of `ShotController` for the view.
   *
   * The controller owns it — it is read every frame to build the command — but
   * the FOV, the sensitivity divisor and the scope overlay are all React's
   * business, and this is the one line between them.
   */
  const [scoped, setScoped] = useState(0);

  // ---- the pause menu and the preferences it edits --------------------------
  const [menuOpen, setMenuOpen] = useState(false);
  const sensitivity = useSetting<number>(SENSITIVITY_KEY) ?? 1;
  const fov = useSetting<number>(FOV_KEY) ?? 75;
  const volume = useSetting<number>(VOLUME_KEY) ?? 0.7;
  const crouchToggle = useSetting<boolean>(CROUCH_TOGGLE_KEY) ?? false;
  /** Whether Play, Train and Host open the native window rather than this pane. */
  const nativeClient = useSetting<boolean>(NATIVE_CLIENT_KEY) ?? true;
  const storedControls = useSetting<string>(CONTROLS_KEY);
  const controls = useMemo(() => parseControls(storedControls), [storedControls]);
  const codes = useMemo(() => codeMap(controls), [controls]);
  /** Recent noises, for the direction ring. */
  const [heard, setHeard] = useState<{ id: number; at: number; event: NoiseEvent }[]>([]);

  // ---- Native process lifecycle bridge ---------------------------------------
  const [nativeRunning, setNativeRunning] = useState(false);
  /** The render loop's copy: it runs outside React and cannot read state. */
  const nativeRunningRef = useRef(false);
  nativeRunningRef.current = nativeRunning;
  const [nativePid, setNativePid] = useState<number | undefined>();
  /** What the last native launch said, shown in the menu next to the buttons. */
  const [nativeStatus, setNativeStatus] = useState<string | null>(null);
  const [postMatchSummary, setPostMatchSummary] = useState<PostMatchSummary | null>(null);

  /**
   * The equipped skin for each weapon, by weapon id.
   *
   * A ref rather than state because the render loop reads it every frame and
   * nothing in React's tree depends on it — and it is a **ref the loop reads
   * live**, not a value captured when the scene was built, so equipping
   * something in the armoury and coming back applies without a remount.
   */
  const skinsRef = useRef<Record<string, WeaponSkin>>({});

  /**
   * Load the equipped skins.
   *
   * Called on mount and again on every deploy: the armoury is a different pane,
   * so the moment a skin can *change* without this pane knowing is the moment
   * between opening it and pressing Play.
   */
  const refreshSkins = useCallback(async () => {
    try {
      const inventory = await getSkinInventory();
      skinsRef.current = equippedSkins(inventory);
    } catch {
      // A missing armoury is a weapon in its default colours, not a broken
      // match: skins are cosmetic, and failing to play over one would not be.
    }
  }, []);

  useEffect(() => {
    void refreshSkins();
  }, [refreshSkins]);

  /**
   * The `timestamp` of the last debrief this player closed.
   *
   * **A dismissal has to stick locally**, not only on the server. This poll runs
   * every 1.5s and its request is usually already in flight when the button is
   * pressed, so clearing the state alone lets the older response put the same
   * card straight back — and if the `dismiss` POST fails, or the pane is
   * remounted by a workspace switch, the server still has the summary and hands
   * it over again. Every route to "the victory screen will not go away" runs
   * through trusting the server's copy to be gone; keying on the timestamp we
   * closed means the same match can never be shown twice.
   */
  const dismissedSummaryAt = useRef(0);

  const closeSummary = useCallback((summary: PostMatchSummary) => {
    dismissedSummaryAt.current = Math.max(dismissedSummaryAt.current, summary.timestamp);
    setPostMatchSummary(null);
    void dismissMatchSummary();
  }, []);

  useEffect(() => {
    let active = true;
    const interval = setInterval(async () => {
      try {
        const [proc, sum] = await Promise.all([getProcessStatus(), getLatestMatchSummary()]);
        if (!active) return;
        setNativeRunning(proc.running);
        setNativePid(proc.pid);
        if (!sum) {
          // Cleared server-side — by this pane, another one, or a restart.
          setPostMatchSummary(null);
          return;
        }
        if (!proc.running && sum.timestamp > dismissedSummaryAt.current) {
          setPostMatchSummary(sum);
        }
      } catch {
        // ignore background poll failures
      }
    }, 1500);
    return () => {
      active = false;
      clearInterval(interval);
    };
  }, []);

  // Mutable simulation state, kept out of React: this updates every frame and
  // re-rendering the component 60 times a second would be absurd.
  const worldRef = useRef<World | null>(null);
  const playerRef = useRef<PlayerState>(createPlayer(0, 0, 0));
  /** Held *actions*, not codes: the key handler resolves the binding once, and the
   * frame loop then never has to know which key produced a movement. */
  const keysRef = useRef<Set<GameAction>>(new Set());
  const noclipRef = useRef(false);
  /** Crouch is its own ref rather than an entry in `keysRef`, because in toggle
   * mode it outlives the keypress that set it. */
  const crouchRef = useRef(false);
  /** Distance covered since our own last footstep sound. */
  const strideRef = useRef(0);
  const sceneRef = useRef<SceneHandle | null>(null);
  const sessionRef = useRef<MatchSession | null>(null);
  const shotsRef = useRef<ShotController | null>(null);
  /** Offline stand-in for everything a match server would own. See `training.ts`. */
  const rangeRef = useRef<TrainingRange | null>(null);
  /** Last pushed training state, so the frame loop can tell what changed. */
  const localYouRef = useRef<SelfState | null>(null);
  /** Read by the view model, which runs in the same loop and cannot await React. */
  const localReloadingRef = useRef(false);
  /** Last zoom step pushed to React, so the loop only pushes transitions. */
  const scopedRef = useRef(0);
  const audioRef = useRef<GameAudio | null>(null);
  /** Bots the main menu asked for, waiting for the room to exist. `add_bot` needs a
   * room id, and the room is only ours once the welcome lands. */
  const pendingBotsRef = useRef<{ count: number; skill: string } | null>(null);
  if (sessionRef.current === null) sessionRef.current = new MatchSession();
  if (shotsRef.current === null) shotsRef.current = new ShotController();
  if (rangeRef.current === null) rangeRef.current = new TrainingRange();
  if (audioRef.current === null) audioRef.current = new GameAudio();

  // The frame loop is built once and never re-created, so anything it needs to
  // read per-frame from React state has to arrive by ref.
  const phaseRef = useRef(phase);
  phaseRef.current = phase;
  // Same for the input handlers, which are installed once: a rebind or a new
  // sensitivity has to reach them without tearing down pointer lock to do it.
  const sensitivityRef = useRef(sensitivity);
  // Divided by the same magnification the FOV is, so a given mouse movement
  // sweeps the same *distance on screen* whatever the zoom. Without this, 4×
  // multiplies every twitch by four and the scope is unusable at exactly the
  // range it exists for. Recomputed on render, and a zoom change is a render.
  sensitivityRef.current = sensitivity / (shotsRef.current?.magnification() ?? 1);
  const codesRef = useRef(codes);
  codesRef.current = codes;
  const crouchToggleRef = useRef(crouchToggle);
  crouchToggleRef.current = crouchToggle;
  /** Pointer-lock state as the *handlers* see it: `document.pointerLockElement`
   * has already been cleared by the time an Escape that released it reaches us. */
  const lockedRef = useRef(false);
  const menuOpenRef = useRef(false);
  menuOpenRef.current = menuOpen;

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

  // Volume reaches the synth without touching the render loop; at zero it never
  // even builds an AudioContext.
  useEffect(() => {
    audioRef.current?.setVolume(volume);
  }, [volume]);

  useEffect(() => {
    const audio = audioRef.current;
    return () => audio?.dispose();
  }, []);

  // Field of view. Applied through the scene handle rather than at construction,
  // because it is a setting and can change while a match is running. The scene may
  // not exist on the first pass — the renderer loads lazily — so this re-runs when
  // it arrives.
  useEffect(() => {
    // Divided by the magnification, which *is* the zoom: a scope narrows the
    // field of view, it does not enlarge anything. `scoped` is in the deps
    // because the magnification is read off the controller, which React does not
    // otherwise watch.
    sceneRef.current?.setFov(fov / (shotsRef.current?.magnification() ?? 1));
  }, [fov, scoped, progress.renderer]);

  // The loadout, fetched rather than hardcoded: the client needs each weapon's
  // fire interval so it does not send input the server would only discard, and a
  // second copy of those numbers here is a drift trap.
  useEffect(() => {
    let cancelled = false;
    void listWeapons()
      .then((specs) => {
        if (cancelled) return;
        setLoadoutError(specs.length === 0 ? 'The server returned an empty loadout.' : '');
        setWeapons(specs);
        shotsRef.current?.setWeapons(specs, Math.min(2, specs.length - 1));
        rangeRef.current?.setWeapons(specs, Math.min(2, specs.length - 1));
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        // Surfaced rather than swallowed. The overwhelmingly likely cause is a
        // backend older than the weapons route, which 404s here and then shows
        // up as a game where the trigger does nothing — a symptom that points
        // nowhere near its cause unless something says this happened.
        setLoadoutError(err instanceof Error ? err.message : String(err));
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

  // Bots the menu asked for, sent once the room is actually ours. Keyed off the
  // room id rather than the status, so a *new* room gets its own bots and rejoining
  // the same one does not silently double them.
  useEffect(() => {
    const wanted = pendingBotsRef.current;
    if (!wanted || net.status !== 'joined' || net.host) return;
    pendingBotsRef.current = null;
    sessionRef.current?.addBots(wanted.count, wanted.skill);
  }, [net.status, net.room, net.host]);

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
      // The gun in your hands. Parented to the camera by the constructor, which
      // is also what puts the camera in the scene graph.
      const viewmodel = new WeaponViewModel(THREE, scene, camera);

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

        // **Nothing is drawn while the native client is playing.** The pane is
        // covered by the companion overlay, so this scene is invisible — and it
        // is a full WebGL render, in a compositing webview, on the same GPU the
        // game is trying to have to itself. The loop is kept alive rather than
        // cancelled so the map is back the instant the native window exits, and
        // `last` is updated above so the first frame back is not a two-minute
        // `dt`.
        if (nativeRunningRef.current) return;

        const world = worldRef.current;
        const player = playerRef.current;
        const session = sessionRef.current;
        const shots = shotsRef.current;
        const online = session != null && session.state.status === 'joined';
        const alive = !online || (session?.state.you?.alive ?? true);

        let moving = false;
        /** Whether a shot left the barrel this frame, for the view model's kick. */
        let fired = false;

        if (world) {
          const keys = keysRef.current;
          // A dead player's input is discarded server-side, so predicting
          // movement from it would only be a correction waiting to happen.
          const forward = alive ? (keys.has('forward') ? 1 : 0) - (keys.has('back') ? 1 : 0) : 0;
          const strafe = alive ? (keys.has('right') ? 1 : 0) - (keys.has('left') ? 1 : 0) : 0;
          moving = forward !== 0 || strafe !== 0;
          const input = {
            forward,
            strafe,
            jump: alive && keys.has('jump'),
            crouch: alive && crouchRef.current,
            // Noclip is a local sightseeing tool. The server has no such move, so
            // in a match it would desync on the very first frame.
            noclip: !online && noclipRef.current,
          };
          const wasOnGround = player.onGround;

          if (online && session && shots) {
            // Correct against the newest snapshot *before* predicting this
            // frame, so the frame we are about to draw is built on the
            // authoritative state rather than on top of a stale error.
            const correction = session.pendingCorrection;
            if (correction) {
              session.predictor.reconcile(
                world,
                player,
                correction.row,
                correction.move,
                correction.ack,
              );
              session.pendingCorrection = null;
            }
            // Recoil is a local move on the camera, exactly like the mouse: the
            // server reads whatever angles the resulting command carries.
            const climb = shots.recoil(dt);
            player.yaw += climb.yaw;
            player.pitch = clampPitch(player.pitch + climb.pitch);
            // The instant we are *rendering*, which is what the server rewinds a
            // shot to. Not sent until the buffer has an offset to derive it from.
            const renderT = session.snapshots.renderTime(now);
            const intent = shots.frame(
              now,
              Number.isFinite(renderT) ? renderT : 0,
              session.state.you,
            );
            fired = intent.fire;
            // The shove a shot puts on the shooter, from the *served* kickback
            // number so the client cannot disagree with the server about it.
            // Handed to `record`, which applies it after the step — the same
            // order `match._fire` does, and the order a replay has to repeat.
            const kick = fired
              ? kickVector(shots.weapon, player.yaw, player.pitch, player.crouch > 0.5)
              : NO_KICK;
            session.queue(session.predictor.record(world, player, input, dt, intent, kick));
            session.predictor.decay(dt);
          } else {
            // Offline the training range plays the part of the server: it owns
            // ammo, reloads and the dummies, and hands back the same `SelfState`
            // a snapshot would have carried. `ShotController` therefore takes the
            // identical path it takes in a match and needs no offline branch —
            // which is the point, because a trigger that behaves differently in
            // training is a trigger training cannot teach you.
            const range = rangeRef.current;
            const climb = shots?.recoil(dt) ?? { yaw: 0, pitch: 0 };
            player.yaw += climb.yaw;
            player.pitch = clampPitch(player.pitch + climb.pitch);
            range?.update(dt);
            // Read once and shared: `selfState` drains hitmarkers, so calling it
            // again for the HUD would consume the markers the controller was
            // handed and show none of them.
            const self = range?.selfState() ?? null;
            const intent = shots?.frame(now, 0, self) ?? NO_SHOT;
            if (intent.reload) range?.requestReload();
            if (intent.weapon >= 0) range?.select(intent.weapon);
            fired = intent.fire;
            localReloadingRef.current = self?.reloading ?? false;
            // Only when something the HUD draws actually moved.
            const prev = localYouRef.current;
            if (
              self &&
              (prev === null ||
                prev.ammo !== self.ammo ||
                prev.reserve !== self.reserve ||
                prev.reloading !== self.reloading ||
                prev.weapon !== self.weapon)
            ) {
              localYouRef.current = self;
              setLocalYou(self);
            }
            if (fired && range && shots) {
              const shot = range.fire(
                world,
                player.x,
                player.y,
                player.z,
                // The eye as an *offset* from the feet, which is what a muzzle
                // position is built from — and which crouching lowers.
                eyeHeight(player) - player.z,
                player.yaw,
                player.pitch,
                intent.scoped,
              );
              if (shot) {
                effects.shot(shot.origin, shot.ends, TEAM_COLORS[0] ?? 0xffffff, true);
                if (shot.hits.length > 0) {
                  const killed = shot.hits.some((h) => h.killed);
                  setFlash((f) => ({
                    ...f,
                    hit: Date.now(),
                    killed: killed ? Date.now() : f.killed,
                  }));
                }
              }
            }
            step(world, player, input, dt);
            // After the step, matching the order `Predictor.record` uses online
            // and the order the match server fires in. Applied before it, a
            // shoot-jump would land somewhere training never taught you.
            if (fired && shots) {
              const kick = kickVector(shots.weapon, player.yaw, player.pitch, player.crouch > 0.5);
              applyImpulse(player, kick.x, kick.y, kick.z);
            }
          }

          // Our own sounds, made here rather than waited for: the server does not
          // send them back, because a footstep that arrives half a round trip late
          // does not sound like a footstep. Cosmetic only — nothing here is input.
          const audio = audioRef.current;
          if (audio && acceptsGameInput(phaseRef.current) && alive) {
            if (player.onGround && player.crouch <= 0.5) {
              strideRef.current += Math.hypot(player.velX, player.velY) * dt;
              if (strideRef.current >= OWN_STRIDE) {
                strideRef.current = 0;
                audio.own('step', 0.45);
              }
            } else {
              strideRef.current = 0;
            }
            if (wasOnGround && !player.onGround && player.velZ > 0) audio.own('jump', 0.5);
            if (player.fallSpeed > 0) {
              // Louder the harder the landing, which is the audible half of the
              // fall-damage rule: you hear that a drop was expensive.
              audio.own('land', Math.min(1, 0.35 + player.fallSpeed / (JUMP_SPEED * 2)));
            }
            // Your own gun, in its own voice — and locally, because a shot that
            // waited for the server to describe it would arrive after the recoil.
            if (fired) audio.own('shot', 0.55, shotsRef.current?.weapon);
          }
        }

        // One line out of the controller, and only on a change: the zoom step
        // drives the FOV, the look sensitivity and the scope overlay, all of
        // which are React's.
        if (shots && shots.scoped !== scopedRef.current) {
          scopedRef.current = shots.scoped;
          setScoped(shots.scoped);
        }

        if (session) {
          session.pump(now);
          // Offline the bodies on the map are the training dummies. They are
          // `PlayerRow`s so they go through the same avatar pool as everybody
          // else — a target that rendered differently from a player would be
          // practice against something the match does not contain.
          remote = online
            ? session.snapshots.sample(now, session.state.playerId)
            : (rangeRef.current?.rows() ?? []);
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
          if (session.pendingNoise.length > 0) {
            const audio = audioRef.current;
            const listenerYaw = playerRef.current.yaw;
            const loadout = shotsRef.current?.weapons ?? [];
            for (const event of session.pendingNoise) {
              audio?.heard(event, listenerYaw, loadout);
            }
            // Also shown, not only played: a bearing is exactly what the direction
            // ring draws, and a player on headphones and a player on laptop
            // speakers should not be playing different games.
            const at = Date.now();
            const batch = session.pendingNoise.map((event, i) => ({
              id: at * 100 + i,
              at,
              event,
            }));
            setHeard((prev) => [...prev.filter((h) => at - h.at < NOISE_TTL_MS), ...batch]);
            session.pendingNoise = [];
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
          // The weapon rides the camera, so it is updated with it: what it needs
          // is what the camera just did (angles) and what the player just did
          // (moved, fired, reloading).
          if (fired) viewmodel.fire();
          const heldWeapon = shots?.weapon?.id ?? '';
          viewmodel.setWeapon(heldWeapon, skinsRef.current[heldWeapon] ?? null);
          viewmodel.update(dt, {
            speed: moving ? MOVE_SPEED : 0,
            onGround: player.onGround,
            reloading: online
              ? (session?.state.you?.reloading ?? false)
              : localReloadingRef.current,
            yaw: player.yaw,
            pitch: player.pitch,
            visible: alive,
          });
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
          // Nobody is holding it yet: the cinematic camera is not a player.
          viewmodel.update(dt, {
            speed: 0,
            onGround: true,
            reloading: false,
            yaw: 0,
            pitch: 0,
            visible: false,
          });
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
            speed: Math.hypot(player.velX, player.velY),
            crouch: player.crouch,
            yaw: player.yaw,
            error: session ? session.predictor.lastError : 0,
          }));
          fpsAccum = 0;
          fpsFrames = 0;
          hudAccum = 0;
        }
      };
      raf = requestAnimationFrame(frame);

      const setFov = (degrees: number) => {
        camera.fov = degrees;
        camera.updateProjectionMatrix();
      };

      sceneRef.current = {
        setMesh,
        setFov,
        avatars,
        reveal,
        backdrop,
        camera: camera as never,
      };
      // Unblocks the map load, which has been waiting rather than polling.
      sceneReadyRef.current?.resolve();

      cleanup = () => {
        cancelAnimationFrame(raf);
        observer.disconnect();
        avatars.dispose();
        effects.dispose();
        viewmodel.dispose();
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
  // Capture is the shell's, not ours: while it is held the keymap resolves only
  // this view's own bindings, so `t`/`n`/`b`/`mod+1..9` stop reaching the frame
  // mid-match. Releasing runs `onRelease` exactly once no matter who triggered it
  // — the Escape ladder, focus moving to another pane, or unmount.
  const capture = useCapture({
    mode: 'full',
    // Escape belongs to the game (its own menu); holding it gives the mouse back
    // where the host allows it. The HUD says which of the two is actually live.
    escape: 'passthrough',
    instanceId: useContext(PaneInstanceContext),
    viewId: 'hassault.play',
    onRelease: () => {
      unlockEscape();
      if (document.pointerLockElement) document.exitPointerLock();
    },
  });
  const requestCapture = capture.request;
  const releaseCapture = capture.release;

  /** Take the mouse and the keyboard. The one path into playing, from a click on
   * the canvas and from the pause menu's Resume alike. */
  const grabInput = useCallback(() => {
    if (!acceptsGameInput(phaseRef.current)) return;
    mountRef.current?.querySelector('canvas')?.requestPointerLock();
    requestCapture();
    // Keyboard Lock needs document fullscreen; without it Escape releases pointer
    // lock outright and the hold gesture degrades (see canHoldEscape).
    void lockEscape();
  }, [requestCapture]);

  const onCanvasClick = useCallback(() => {
    // A click while the menu is up is a click *on* the menu that fell through
    // somewhere it shouldn't have; it must not silently re-grab the pointer.
    if (menuOpenRef.current) return;
    grabInput();
  }, [grabInput]);

  /**
   * Open the pause menu, giving the mouse and keyboard back so it can be used.
   *
   * Called from Escape, which arrives by one of two routes depending on the host:
   * with Keyboard Lock the shell's ladder hands the tap to the pane, and without
   * it the ladder releases capture instead and the browser drops pointer lock on
   * its own. Both end here, so the menu opens either way — see `keymap/dispatch`.
   */
  const openMenu = useCallback(() => {
    setMenuOpen(true);
    keysRef.current.clear();
    shotsRef.current?.release();
    // Coming back from the menu at 4× with no memory of having scoped is a
    // disorienting way to resume, and the FOV is the one piece of state here
    // whose cause is invisible.
    shotsRef.current?.unscope();
    setShowScores(false);
    if (document.pointerLockElement) document.exitPointerLock();
    releaseCapture();
  }, [releaseCapture]);

  const resumeGame = useCallback(() => {
    setMenuOpen(false);
    grabInput();
  }, [grabInput]);

  useEffect(() => {
    const el = mountRef.current;
    if (!el) return;
    const isLocked = () => document.pointerLockElement === el.querySelector('canvas');

    const onPointerLockChange = () => {
      const held = isLocked();
      setLocked(held);
      lockedRef.current = held;
      // Releasing the pointer must release the trigger too, or a weapon left
      // firing keeps firing into whatever you tabbed away to.
      if (!held) {
        shotsRef.current?.release();
        keysRef.current.clear();
        if (!crouchToggleRef.current) crouchRef.current = false;
        setShowScores(false);
        // The browser can drop pointer lock on its own (alt-tab, Escape where
        // Keyboard Lock is unavailable); keep the shell's capture in step.
        releaseCapture();
      }
    };
    const onMouseMove = (e: MouseEvent) => {
      if (!isLocked()) return;
      // Mouse right turns right. The sign lives in `applyLook` with the reasoning
      // for it and a test — it was inverted here, which is subtle enough to have
      // shipped: the camera's yaw is about cube +x, but the renderer maps cube y
      // onto three's z, and that reflection is exactly one sign.
      applyLook(playerRef.current, e.movementX, e.movementY, sensitivityRef.current);
    };
    const onMouseDown = (e: MouseEvent) => {
      if (!isLocked()) return;
      // Right is the scope, left is the trigger. A weapon with no scope ignores
      // the right button entirely rather than consuming it.
      if (e.button === 2) {
        e.preventDefault();
        shotsRef.current?.cycleScope();
        return;
      }
      if (e.button !== 0) return;
      e.preventDefault();
      shotsRef.current?.press();
    };
    const onMouseUp = (e: MouseEvent) => {
      if (e.button === 0) shotsRef.current?.release();
    };
    // Pointer lock suppresses the context menu in most browsers, but not all and
    // not on every platform — and one that opens mid-firefight steals the
    // pointer. Cheap insurance for a button the game now uses.
    const onContextMenu = (e: MouseEvent) => {
      if (isLocked()) e.preventDefault();
    };
    const onWheel = (e: WheelEvent) => {
      if (!isLocked()) return;
      e.preventDefault();
      shotsRef.current?.cycle(e.deltaY > 0 ? 1 : -1);
    };
    const onKeyDown = (e: KeyboardEvent) => {
      // Escape is the pause menu, and it is the one key handled while *not*
      // locked — because by the time it reaches us the shell's ladder may already
      // have released the lock (see `openMenu`). `lockedRef` is the state before
      // that release, which is why it exists.
      if (e.code === 'Escape') {
        if (menuOpenRef.current) resumeGame();
        else if (lockedRef.current) openMenu();
        return;
      }
      if (!isLocked()) return;
      const action = codesRef.current.get(e.code);
      // Only swallow keys the game is actually bound to, so the command palette
      // and every other shortcut keep working when the pointer isn't locked — and
      // an unbound key keeps working even when it is.
      if (!action) return;
      e.preventDefault();
      if (e.repeat) return;
      if (action === 'noclip') noclipRef.current = !noclipRef.current;
      if (action === 'reload') shotsRef.current?.requestReload();
      if (action === 'scores') setShowScores(true);
      // Two modes, one ref. In hold mode the key up clears it; in toggle mode
      // nothing does but the next press, which is exactly why crouch cannot live
      // in `keysRef` with the rest of the movement keys.
      if (action === 'crouch') {
        crouchRef.current = crouchToggleRef.current ? !crouchRef.current : true;
      }
      if (action.startsWith('weapon')) {
        shotsRef.current?.select(Number(action.slice(6)) - 1);
      }
      keysRef.current.add(action);
    };
    const onKeyUp = (e: KeyboardEvent) => {
      // Resolved through the *current* map on the way up too, and tolerant of a
      // rebind mid-press: the action a key added is the action it removes, and a
      // key rebound while held simply leaves its old action stuck until the next
      // release — which `clear()` on unlock and on blur already covers.
      const action = codesRef.current.get(e.code);
      if (!action) return;
      if (action === 'scores') setShowScores(false);
      if (action === 'crouch' && !crouchToggleRef.current) crouchRef.current = false;
      keysRef.current.delete(action);
    };
    const onBlur = () => {
      keysRef.current.clear();
      shotsRef.current?.release();
      // Standing up on blur, in hold mode only: a toggled crouch is a deliberate
      // state and losing it because the window lost focus would be a surprise.
      if (!crouchToggleRef.current) crouchRef.current = false;
    };

    document.addEventListener('pointerlockchange', onPointerLockChange);
    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mousedown', onMouseDown);
    document.addEventListener('mouseup', onMouseUp);
    document.addEventListener('contextmenu', onContextMenu);
    el.addEventListener('wheel', onWheel, { passive: false });
    // Capture phase, deliberately. The shell's dispatcher is also on `window` in
    // the capture phase, and when its Escape ladder consumes the key it calls
    // `stopPropagation` — which stops the event reaching any *other* node, and so
    // would stop a bubble-phase listener here from ever seeing Escape. Two
    // listeners on the same node in the same phase both run, so this one does.
    window.addEventListener('keydown', onKeyDown, true);
    window.addEventListener('keyup', onKeyUp);
    window.addEventListener('blur', onBlur);
    return () => {
      document.removeEventListener('pointerlockchange', onPointerLockChange);
      document.removeEventListener('mousemove', onMouseMove);
      document.removeEventListener('mousedown', onMouseDown);
      document.removeEventListener('mouseup', onMouseUp);
      document.removeEventListener('contextmenu', onContextMenu);
      el.removeEventListener('wheel', onWheel);
      window.removeEventListener('keydown', onKeyDown, true);
      window.removeEventListener('keyup', onKeyUp);
      window.removeEventListener('blur', onBlur);
    };
  }, [releaseCapture, openMenu, resumeGame]);

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

  /** Enter the world. The one path from any menu to actually playing. */
  const deploy = useCallback(() => {
    setDeployed(true);
    setMenuOpen(false);
    // The armoury is another pane; this is the last moment before the gun is in
    // your hands, so it is the right one to ask what you are carrying.
    void refreshSkins();
  }, [refreshSkins]);

  /**
   * Enter the world alone, on the loaded map.
   *
   * Not a match: no server, and noclip available. This is where the movement is
   * learnable — the chained-jump timing and the shoot-jump are exactly the sort
   * of thing to practise before somebody is aiming at you, and the shoot-jump in
   * particular cannot be practised without a working gun, which is why the range
   * exists (`training.ts`). The dummies stand on the map's own spawn points and
   * do not shoot back.
   */
  /**
   * Hand the press to the native client instead of playing it in here.
   *
   * The mode travels with it, which is the whole of B4: without one, every launch
   * was "a match on this map, or open one", so Train dropped a learner into
   * whatever firefight was already running and the bot count the menu had just
   * collected went nowhere.
   *
   * The pane does not go dark waiting for the 1.5s process poll to notice — the
   * route has already spawned the process and returned its pid by the time this
   * resolves, and a menu that sits there for a second and a half after a
   * successful launch reads as a button that did nothing.
   */
  const launchNative = useCallback(
    async (opts: Omit<LaunchNativeOptions, 'map_name'> & { map_name?: string }) => {
      sessionRef.current?.leave();
      setNativeStatus('Starting the native client…');
      try {
        const res = await launchNativeFps({ map_name: mapName, max_fps: 240, ...opts });
        setNativeStatus(res.message ?? (res.launched ? 'Launched' : 'It did not start'));
        if (res.launched) {
          setNativeRunning(true);
          setNativePid(res.pid);
        }
      } catch (err) {
        setNativeStatus(err instanceof Error ? err.message : 'Could not reach this node');
      }
    },
    [mapName],
  );

  const train = useCallback(() => {
    if (nativeClient) {
      void launchNative({ mode: 'train' });
      return;
    }
    sessionRef.current?.leave();
    shotsRef.current?.reset();
    const range = rangeRef.current;
    const world = worldRef.current;
    if (range) {
      range.reset();
      range.setWeapons(weapons, Math.min(2, weapons.length - 1));
      // Placed relative to where we are about to stand, so the nearest dummies
      // are the ones in front of you rather than the ones the map happens to
      // list first.
      if (world) range.place(world, playerRef.current.x, playerRef.current.y);
    }
    deploy();
  }, [deploy, weapons, nativeClient, launchNative]);

  /** Host a match here on the loaded map and enter it, with bots if asked. */
  const host = useCallback(
    (bots: number) => {
      if (nativeClient) {
        // `bot_skill` rides along rather than being read from the setting on the
        // backend: the menu's skill select is the live value, and the setting is
        // only where it happens to be stored.
        void launchNative({ mode: 'host', bots, bot_skill: botSkill });
        return;
      }
      const session = sessionRef.current;
      if (!session || !mapName) return;
      shotsRef.current?.reset();
      // The name is sent for the wire's sake; the backend takes the username from
      // the account and ignores this entirely.
      session.join(mapName, playerName);
      // Queued behind the join rather than sent with it: `add_bot` needs a room to
      // add them to, and the room is only ours once the welcome lands. A retry loop
      // would be the alternative, and this is one message either way.
      if (bots > 0) pendingBotsRef.current = { count: bots, skill: botSkill };
      deploy();
    },
    [mapName, playerName, botSkill, deploy, nativeClient, launchNative],
  );

  /**
   * Invite somebody, starting a match first if there isn't one.
   *
   * The Invite button used to be disabled unless you were already hosting, with a
   * tooltip explaining that an invite is to a room you are running. True, and
   * beside the point: "invite Rob" is a complete intent, and making the person
   * infer the missing precondition, go and satisfy it, then come back is why the
   * Friends panel read as broken to anyone who opened it first.
   *
   * The bots count is deliberately zero here. You are inviting a human; filling
   * the room with three bots on their behalf is a decision nobody made.
   */
  const inviteFriend = useCallback(
    (friendCode: string) => {
      const session = sessionRef.current;
      if (!session) return;
      // "Hosting" is: in a match, and it is ours rather than one we joined on
      // somebody else's node — inviting people to *their* room is not ours to do.
      // Read off `net` directly because the `online` binding is declared further
      // down, with the render.
      const hosting = net.status === 'joined' && !net.host;
      if (!hosting) host(0);
      // Sent after the join is requested rather than awaited on the welcome:
      // `invite` reads `this.state.room`, which the welcome fills in, so the
      // session queues this the same way `add_bot` is queued.
      session.invite(friendCode);
    },
    [net.status, net.host, host],
  );

  /**
   * Join a specific room, wherever it is running.
   *
   * The one path for every way of arriving at one: an invitation a friend pushed, a
   * row picked out of the server browser, and the main menu's own list. `host` empty
   * means it is a match on this node, which is the same thing `join` understands.
   */
  const joinRoom = useCallback(
    (room: string, map: string, host: string) => {
      const session = sessionRef.current;
      if (!session) return;
      // Load their map before joining: the snapshots are positions in *that*
      // world, and rendering them against a different one is nonsense.
      setMapName(map);
      shotsRef.current?.reset();
      session.join(map, playerName, room, host);
      deploy();
    },
    [playerName, deploy],
  );

  /**
   * Start playing with the least ceremony possible.
   *
   * Joins the fullest joinable match a friend or the LAN is running — fullest
   * because a match with people in it is the one worth joining, and an empty room
   * somebody left open is not — and hosts one only when there is nothing to join.
   * A map this node cannot load is skipped rather than offered and then failed on.
   *
   * Composed entirely from things that already exist: it is `browseServers`,
   * `joinRoom` and `host` in a sensible order, with no new endpoint behind it.
   */
  const quickPlay = useCallback(
    async (bots: number) => {
      let best: BrowseMatch | null = null;
      try {
        const data = await browseServers();
        const known = new Set(maps.map((m) => m.name));
        for (const m of data.matches) {
          if (!known.has(m.map) || m.players >= m.maxPlayers) continue;
          if (m.host === '' && m.id === net.room) continue;
          if (best === null || m.players > best.players) best = m;
        }
      } catch {
        /* No browse, no candidates — hosting is still a perfectly good answer. */
      }
      if (best) {
        if (nativeClient) {
          void launchNative({
            mode: 'join',
            room_id: best.id,
            map_name: best.map,
            host: best.host,
          });
        } else {
          joinRoom(best.id, best.map, best.host);
        }
      } else {
        // `host` branches on `nativeClient` itself, so quick play does not need
        // to: one place decides what hosting means.
        host(bots);
      }
    },
    [maps, net.room, joinRoom, host, nativeClient, launchNative],
  );

  /**
   * Act on an invite accepted from *outside* the game.
   *
   * The Join button on a shell toast can be pressed with this pane closed, which
   * is the whole point of putting invites on the shell's notification channel —
   * the pane not being mounted was exactly what made them invisible. The action
   * opens the pane and parks the intent; this consumes it once there is a session
   * and a world to join into.
   *
   * Gated on `info` rather than run on mount: joining before the map is loaded
   * would render another world's snapshots against no geometry. The subscription
   * covers the other order — pane already open, toast pressed — where nothing new
   * mounts and only the listener fires.
   */
  useEffect(() => {
    if (info == null) return;
    const act = (join: { room: string; map: string; host: string }) => {
      joinRoom(join.room, join.map, join.host);
    };
    const parked = takePendingJoin();
    if (parked) act(parked);
    return onJoinRequested((join) => {
      takePendingJoin();
      act(join);
    });
  }, [info, joinRoom]);

  /**
   * Leave the world and go back to the main menu.
   *
   * Leaves the match on the way out, deliberately: the main menu is not somewhere
   * you can stand while a server is still simulating your body. It also gives the
   * pointer back, because the menu is a DOM overlay and needs a real mouse.
   */
  const exitToMenu = useCallback(() => {
    sessionRef.current?.leave();
    shotsRef.current?.reset();
    keysRef.current.clear();
    crouchRef.current = false;
    setMenuOpen(false);
    setShowScores(false);
    setDeployed(false);
    if (document.pointerLockElement) document.exitPointerLock();
    releaseCapture();
  }, [releaseCapture]);

  // The control map, written straight through to settings so a rebind survives a
  // reload. The scalar preferences are edited by `SettingsPanel` directly; this one
  // is a JSON document, which is why the menus are its only editor.
  const setControls = useCallback((next: Bindings) => {
    void setSetting(CONTROLS_KEY, serializeControls(next));
  }, []);

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
  // The socket's word in a match, the range's own in training. Same shape either
  // way, so everything downstream — the ammo counter, the reload line, the
  // weapon name — is written once rather than once per mode.
  const you = online ? net.you : localYou;
  const weapon = you ? weapons[you.weapon] : undefined;
  const now = Date.now();
  const showHit = now - flash.hit < FLASH_MS;
  const showKilled = now - flash.killed < FLASH_MS * 2;
  const showHurt = now - flash.hurt < FLASH_MS * 2;
  const crosshairGap = shotsRef.current?.crosshairSpread() ?? 4;
  const magnification = shotsRef.current?.magnification() ?? 1;

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
        {/* The toolbar is *status*, not setup. Choosing a map, hosting, adding
            bots and inviting people all live in the main menu now — a game that has
            a front door should not also have half of one bolted to its chrome, and
            two ways to start a match is two things to keep in step. */}
        <button onClick={exitToMenu} disabled={phase !== 'playing'} title="Back to the main menu">
          ☰ Menu
        </button>
        {/* The username, shown not typed: it comes from the account, and the
            backend refuses any name the client supplies. Renaming happens on the
            enlist screen, which owns the uniqueness check. */}
        <span
          title="Your username — change it from the sign-in screen"
          style={{
            fontFamily: 'var(--font-mono, monospace)',
            color: 'var(--accent, #6ea8fe)',
            padding: '0 0.2rem',
          }}
        >
          {playerName || '—'}
        </span>
        {info && (
          <span
            style={{
              color: 'var(--text-dim)',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            <code>{info.name}</code> · {info.title}
          </span>
        )}
        {!online && phase === 'playing' && (
          <>
            <span style={{ color: 'var(--text-dim)' }}>training</span>
            <button onClick={respawn} disabled={!info} title="Back to a spawn point">
              Respawn
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

        {/* Invites, drawn **over the canvas**.
            Not a duplicate of the shell toast: while pointer lock is held the
            shell's chrome is not on screen at all, so the toast that would
            normally carry this is invisible — the same reason Steam draws its
            overlay notification inside the game rather than on the desktop.
            The pointer is captured too, so the buttons here are only reachable
            once Escape gives it back; that is what the hint says, rather than
            offering a button the mouse cannot reach. */}
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
                  {invite.hostDevice && (
                    <span style={{ color: 'var(--text-dim)' }}> · on {invite.hostDevice}</span>
                  )}
                </span>
                {phase === 'playing' ? (
                  <span style={{ color: 'var(--text-dim)' }}>Esc to answer</span>
                ) : (
                  <>
                    <button onClick={() => joinRoom(invite.room, invite.map, invite.host)}>
                      Join
                    </button>
                    <button onClick={() => sessionRef.current?.dismissInvite(invite.room)}>
                      Dismiss
                    </button>
                  </>
                )}
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
        {phase === 'playing' && !locked && !menuOpen && (
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
                {/* The player's own keys, not the shipped ones: this line is a
                    lie the moment anything is rebound. */}
                <span style={{ color: 'var(--text-dim)' }}>
                  {describeControls(controls)} · Esc menu
                </span>
                <span style={{ color: 'var(--text-dim)' }}>
                  Left fire · right scope · wheel or {keyLabel(controls.weapon1[0] ?? '1')}–
                  {keyLabel(controls.weapon5[0] ?? '5')} weapon ·{' '}
                  {keyLabel(controls.reload[0] ?? 'R')} reload
                  {online ? <> · {keyLabel(controls.scores[0] ?? 'Tab')} scores</> : null}
                </span>
                {!online && (
                  <span style={{ color: 'var(--text-dim)' }}>
                    Training — the dummies don&rsquo;t shoot back. Host a match from the menu to
                    fight.
                  </span>
                )}
                {loadoutError && (
                  <span style={{ color: '#f85149' }}>
                    No loadout — the trigger will do nothing. {loadoutError}
                  </span>
                )}
              </>
            )}
          </div>
        )}

        {/* The pause menu. Deployed players only: before that the boot overlay owns
            the pane, and Escape there is the shell's business, not the game's. */}
        {phase === 'playing' && menuOpen && (
          <GameMenu
            online={online}
            hosting={online && !net.host}
            room={net.room}
            maps={maps}
            peers={net.peers}
            playerId={net.playerId}
            invitees={invitees}
            invites={net.invites}
            controls={controls}
            onControls={setControls}
            onJoin={(room, map, host) => {
              joinRoom(room, map, host);
              // Picking a match is a decision to play, so it puts you back in the
              // world rather than leaving you looking at the list you just used.
              resumeGame();
            }}
            onLeave={() => sessionRef.current?.leave()}
            onInvite={inviteFriend}
            onDismissInvite={(room) => sessionRef.current?.dismissInvite(room)}
            onResume={resumeGame}
            onExitToMenu={exitToMenu}
          />
        )}

        {nativeRunning && (
          <div style={{ position: 'absolute', inset: 0, zIndex: 100 }}>
            <MatchCompanion
              mapName={mapName}
              room={net.room || 'match_live'}
              pid={nativePid}
              onExitMatch={() => setNativeRunning(false)}
            />
          </div>
        )}

        {/* Never over live gameplay. A summary is only ever produced when a
            *native* match exits, so the pane behind this is a menu — but the
            card outlives the match that made it, and one left over from an
            earlier native game would otherwise sit on top of a browser match
            that is still being played. */}
        {postMatchSummary && phase !== 'playing' && (
          <PostMatchDebrief
            summary={postMatchSummary}
            onDismiss={() => closeSummary(postMatchSummary)}
            onRequeue={() => {
              closeSummary(postMatchSummary);
              host(3);
            }}
          />
        )}

        {phase !== 'playing' && !nativeRunning && (
          <BootOverlay
            phase={phase}
            progress={progress}
            bytes={bytes}
            mapName={mapName}
            error={error}
            account={account}
            onSignedIn={() => refreshAccount(true)}
            menu={
              <MainMenu
                account={account}
                maps={maps}
                mapName={mapName}
                onMapName={setMapName}
                controls={controls}
                onControls={setControls}
                peers={net.peers}
                playerId={net.playerId}
                room={net.room}
                hosting={online && !net.host}
                online={online}
                invitees={invitees}
                invites={net.invites}
                botSkill={botSkill}
                onBotSkill={setBotSkill}
                onTrain={train}
                onHost={host}
                onQuickPlay={quickPlay}
                onJoin={joinRoom}
                onInvite={inviteFriend}
                onDismissInvite={(room) => sessionRef.current?.dismissInvite(room)}
                ready={info != null}
                error={error}
                loadoutError={loadoutError}
                nativeClient={nativeClient}
                nativeStatus={nativeStatus}
              />
            }
          />
        )}

        {locked && (
          <>
            {scoped > 0 ? (
              <ScopeOverlay magnification={magnification} hit={showHit} killed={showKilled} />
            ) : (
              <Crosshair gap={crosshairGap} hit={showHit} killed={showKilled} />
            )}
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
              {/* Speed, not just position. It is the number the movement is *about*
                  — the chained jump is only learnable if you can see that it worked,
                  and 27.5 against a 22 cap is the whole feedback loop. */}
              <div style={{ fontSize: '0.78rem' }}>
                <span
                  style={{
                    color: hud.speed > MOVE_SPEED + 0.5 ? '#7ee787' : 'rgba(255,255,255,0.7)',
                  }}
                >
                  {hud.speed.toFixed(1)}
                </span>
                <span style={{ opacity: 0.5 }}> / {MOVE_SPEED} c/s</span>
                {hud.crouch > 0.5 && <span style={{ color: '#8ab4f8' }}> · crouched</span>}
                {hud.onGround ? '' : ' · airborne'}
              </div>
              x {hud.x.toFixed(1)} y {hud.y.toFixed(1)} z {hud.z.toFixed(1)}
              {!online && noclipRef.current ? ' · noclip' : ''}
              {online ? ` · ${Math.round(net.rtt)} ms · err ${hud.error.toFixed(2)}` : ''}
            </div>

            <NoiseRing heard={heard} yaw={hud.yaw} />

            {online && you && (you.fell ?? 0) > 0 && (
              <div
                style={{
                  position: 'absolute',
                  left: '50%',
                  top: '58%',
                  transform: 'translateX(-50%)',
                  pointerEvents: 'none',
                  fontFamily: 'monospace',
                  fontSize: '0.8rem',
                  color: '#ff9d94',
                }}
              >
                −{you.fell} fall
              </div>
            )}

            {you && weapon && (
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

/**
 * Where the sounds are coming from.
 *
 * A ring of ticks around the crosshair, one per noise heard, at the bearing the
 * server reported and the opacity of its volume. This is not decoration and it is
 * not an accessibility fallback: the noise system is *information* (see
 * `backend/modules/hassault/noise.py`), and information that only exists in the
 * audio mix is information a player on laptop speakers does not have. Showing it
 * means the two are playing the same game.
 *
 * Deliberately coarse — a direction and a loudness, which is exactly what the wire
 * carries. There is no distance in it because there is no distance on the wire.
 */
function NoiseRing({
  heard,
  yaw,
}: {
  heard: { id: number; at: number; event: NoiseEvent }[];
  yaw: number;
}) {
  const now = Date.now();
  const live = heard.filter((h) => now - h.at < NOISE_TTL_MS);
  if (live.length === 0) return null;
  return (
    <div
      style={{
        position: 'absolute',
        left: '50%',
        top: '50%',
        width: 0,
        height: 0,
        pointerEvents: 'none',
      }}
    >
      {live.map(({ id, at, event }) => {
        const age = (now - at) / NOISE_TTL_MS;
        // Bearing relative to where we are facing, then into screen space: a sound
        // dead ahead has to sit at the top of the ring and swing round as we turn.
        const relative = event.bearing - yaw;
        const radius = 78;
        const x = Math.sin(relative) * radius;
        const y = -Math.cos(relative) * radius;
        return (
          <div
            key={id}
            style={{
              position: 'absolute',
              left: x - 3,
              top: y - 3,
              width: 6,
              height: 6,
              borderRadius: '50%',
              // Shots are the loud, urgent ones and read differently on purpose.
              background: event.kind === 'shot' ? '#ffb86b' : '#cfd8ff',
              opacity: Math.max(0, (1 - age) * Math.min(1, event.volume * 1.6)),
              // Above and below get an outline rather than a position: the ring is
              // a compass, and there is no vertical axis on a compass.
              boxShadow: event.up !== 0 ? '0 0 0 2px rgba(255,255,255,0.35)' : undefined,
            }}
          />
        );
      })}
    </div>
  );
}

/**
 * Four ticks around a centre dot, opening with the weapon's spread.
 *
 * The gap is the honest thing to show: it is the cone the server will actually
 * roll pellets inside, so a shotgun looks like a shotgun without anyone having
 * to read the numbers.
 */
/**
 * The sniper's sight picture, drawn in CSS rather than as a texture.
 *
 * A texture would be an asset, and assets here are either someone else's
 * copyright or something to draw by hand — the same rule the maps and the audio
 * follow. Two radial gradients and a few hairlines is all a scope actually is.
 *
 * The vignette is the mechanical half, not decoration: it is what a scope
 * *costs*. Trading peripheral vision for magnification is the decision the
 * weapon is built around, and a zoom with a clear view all round would be a free
 * upgrade rather than a choice.
 */
function ScopeOverlay({
  magnification,
  hit,
  killed,
}: {
  magnification: number;
  hit: boolean;
  killed: boolean;
}) {
  const color = killed ? '#ff6b6b' : hit ? '#ffd166' : 'rgba(220,255,220,0.85)';
  return (
    <div style={{ position: 'absolute', inset: 0, pointerEvents: 'none' }}>
      {/* The blacked-out surround. `min(...)` on both axes keeps the sight
          circular in a pane of any shape rather than stretching to an ellipse. */}
      <div
        style={{
          position: 'absolute',
          inset: 0,
          background:
            'radial-gradient(circle at 50% 50%, rgba(0,0,0,0) min(31vh, 31vw), ' +
            'rgba(0,0,0,0.55) min(33vh, 33vw), rgba(0,0,0,0.97) min(36vh, 36vw))',
        }}
      />
      <div
        style={{
          position: 'absolute',
          left: '50%',
          top: '50%',
          width: 'min(66vh, 66vw)',
          height: 'min(66vh, 66vw)',
          transform: 'translate(-50%, -50%)',
          borderRadius: '50%',
          border: '1px solid rgba(0,0,0,0.85)',
          boxShadow: 'inset 0 0 60px rgba(0,0,0,0.55)',
        }}
      />
      {/* Crosshairs: full-width hairlines with a gap at the centre, so the thing
          being aimed at is never behind the reticle drawing it. */}
      {[
        { left: '50%', top: 0, width: 1, height: '100%', translate: '-50%, 0' },
        { left: 0, top: '50%', width: '100%', height: 1, translate: '0, -50%' },
      ].map((line, i) => (
        <div
          key={i}
          style={{
            position: 'absolute',
            left: line.left,
            top: line.top,
            width: line.width,
            height: line.height,
            transform: `translate(${line.translate})`,
            background: color,
            opacity: 0.5,
            // The gap. Cut from the middle of the line itself rather than drawn
            // as two elements per axis.
            WebkitMaskImage:
              i === 0
                ? 'linear-gradient(to bottom, #000 44%, transparent 44%, transparent 56%, #000 56%)'
                : 'linear-gradient(to right, #000 44%, transparent 44%, transparent 56%, #000 56%)',
            maskImage:
              i === 0
                ? 'linear-gradient(to bottom, #000 44%, transparent 44%, transparent 56%, #000 56%)'
                : 'linear-gradient(to right, #000 44%, transparent 44%, transparent 56%, #000 56%)',
          }}
        />
      ))}
      {/* The centre dot, which is where the shot goes. */}
      <div
        style={{
          position: 'absolute',
          left: '50%',
          top: '50%',
          width: 3,
          height: 3,
          transform: hit ? 'translate(-50%, -50%) rotate(45deg)' : 'translate(-50%, -50%)',
          borderRadius: '50%',
          background: color,
        }}
      />
      <div
        style={{
          position: 'absolute',
          left: '50%',
          top: 'calc(50% + min(23vh, 23vw))',
          transform: 'translateX(-50%)',
          fontFamily: 'monospace',
          fontSize: '0.7rem',
          color: 'rgba(220,255,220,0.65)',
        }}
      >
        {magnification}×
      </div>
    </div>
  );
}

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
