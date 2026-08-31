import { useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';

import { PaneInstanceContext, useAgentContext } from '../../agent-context';
import { lockEscape, unlockEscape, useCapture } from '../../keymap';
import { setSetting, useSetting } from '../../settings';
import {
  dismissMatchSummary,
  getInstallStatus,
  getLatestMatchSummary,
  getRankedMaps,
  browseServers,
  getMapCubes,
  getMapInfo,
  getProcessStatus,
  getSession,
  getSkinInventory,
  listInvitees,
  listMaps,
  getThrowPhysics,
  listTacticals,
  getItems,
  listWeapons,
  type TacticalSpec,
  type ThrowPhysics,
  type BrowseMatch,
  type InstallStatus,
  type LaunchNativeOptions,
  type LaunchNativeResult,
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
import { useNativeLaunch } from './native-launch';
import { FlashOverlay, NadeTray, Radar } from './panels/Radar';
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
  NADE_ACTIONS,
  describeControls,
  keyLabel,
  parseControls,
  serializeControls,
  type Bindings,
  type GameAction,
} from './controls';
import { DeveloperConsole, NetGraphHUD } from './console';
import { ArcLine } from './arcline';
import { simulateThrow, throwOrigin, throwVelocity } from './arc';
import { DecalPool } from './decals';
import { EffectsPool } from './effects';
import { GameMenu } from './GameMenu';
import { buildWorldMesh } from './geometry';
import { MainMenu } from './MainMenu';
import {
  CONTROLS_KEY,
  CROUCH_TOGGLE_KEY,
  FOV_KEY,
  NATIVE_CLIENT_KEY,
  SHOW_HITBOXES_KEY,
  SENSITIVITY_KEY,
  VOLUME_KEY,
} from './menu-panels';
import type { ItemsResponse } from './api';
import type { NoiseEvent, PickedItem, PlayerRow, SelfState, Vec3 } from './net';
import {
  applyImpulse,
  applyLook,
  clampPitch,
  inWater,
  submerged,
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
import { createDetailTexture, DETAIL_NEUTRAL } from './surfaces';
import { createLadders } from './ladders';
import { ItemPool } from './items';
import { NadePool } from './nades';
import { createWater } from './water';
import { GrenadeController } from './utility';
import { TrainingRange } from './training';
import { equippedSkins, WeaponViewModel, type WeaponSkin } from './viewmodel';
import { createPropEnvironment } from './models/weapons';
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
/**
 * What to put under the Play buttons for a launch in whatever state it is in.
 *
 * The node's own message wins whenever it has one — it names the pid, the build
 * and its age. This only fills the gap while a job is still running, and the two
 * running phases deliberately read differently: a build is minutes and a start is
 * a moment, and one word for both is what made a compile look like a hang.
 */
function launchMessage(res: LaunchNativeResult): string {
  if (res.message) return res.message;
  if (res.phase === 'building') return 'Compiling the native client — this takes minutes…';
  if (res.phase === 'starting') return 'Starting the native client…';
  return res.launched ? 'Launched' : 'It did not start';
}

/** How long a hitmarker and a damage flash stay on screen. */
/** Stable empties: a fresh literal every render re-runs the radar's effect on
 * frames where nothing actually changed. */
const EMPTY_SPOTTED: readonly string[] = [];
const EMPTY_COUNTS: Readonly<Record<string, number>> = {};

const FLASH_MS = 220;
/** How long a pickup line stays on screen. */
const PICKUP_TTL_MS = 2200;

/**
 * One pickup, in words.
 *
 * Reads the *applied* amounts off the wire rather than the item's spec, so the
 * line and the numbers beside it can never disagree — a health pack taken at 90
 * says "+10 hp".
 */
function describePickup(item: PickedItem): string {
  const parts: string[] = [];
  if (item.health) parts.push(`+${item.health} hp`);
  if (item.armour) parts.push(`+${item.armour} ar`);
  if (item.rounds) parts.push(`+${item.rounds} rounds`);
  if (item.nade) parts.push(`+1 ${item.nade}`);
  return parts.join('  ');
}
/** Team tint used for tracers and the scoreboard: CLA sand, RVSF blue. */
const TEAM_COLORS = [0xd9a441, 0x4c8fd4];

const EMPTY_SESSION: SessionState = {
  status: 'idle',
  room: '',
  ranked: false,
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
  items: [],
  itemsOut: [],
};

interface SceneHandle {
  setMesh: (w: World) => number;
  /** Vertical field of view in degrees. A setting, so it has to reach the camera
   * after construction rather than only at it. */
  setFov: (degrees: number) => void;
  /** Park the render loop, scheduling nothing. Used while the native client owns
   * the GPU; the scene stays resident so `resume` costs one frame. */
  suspend: () => void;
  /** Re-arm the loop, discarding the time spent parked. */
  resume: () => void;
  avatars: AvatarPool;
  /** The gun in your hands. Exposed so the key handler can start an inspect: it
   * is a *local* animation with no command behind it, so there is nothing on the
   * frame loop's side to ask for it. */
  weapon: WeaponViewModel;
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
  const [itemTable, setItemTable] = useState<ItemsResponse | null>(null);
  /**
   * Whether our own eye is under the water plane.
   *
   * Its own state rather than a field on `hud`, which updates at 4 Hz: this is
   * the same line the simulation reads to take the jump away, so the screen has
   * to say so on the frame it happens, not a quarter of a second later. Set only
   * on a crossing, so it costs one render per dive rather than one per frame.
   */
  const [underwater, setUnderwater] = useState(false);
  /** The grenades, in slot order. Served for the same reason the weapons are. */
  const [tacticals, setTacticals] = useState<TacticalSpec[]>([]);
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
  const [consoleOpen, setConsoleOpen] = useState(false);
  const consoleOpenRef = useRef(false);
  consoleOpenRef.current = consoleOpen;
  const sensitivity = useSetting<number>(SENSITIVITY_KEY) ?? 1;
  const fov = useSetting<number>(FOV_KEY) ?? 75;
  const volume = useSetting<number>(VOLUME_KEY) ?? 0.7;
  const crouchToggle = useSetting<boolean>(CROUCH_TOGGLE_KEY) ?? false;
  /** Whether Play, Train and Host open the native window rather than this pane. */
  const nativeClient = useSetting<boolean>(NATIVE_CLIENT_KEY) ?? true;
  const showHitboxes = useSetting<boolean>(SHOW_HITBOXES_KEY) ?? false;
  const storedControls = useSetting<string>(CONTROLS_KEY);
  const controls = useMemo(() => parseControls(storedControls), [storedControls]);
  const codes = useMemo(() => codeMap(controls), [controls]);
  /** Recent noises, for the direction ring. */
  const [heard, setHeard] = useState<{ id: number; at: number; event: NoiseEvent }[]>([]);

  // ---- Native process lifecycle bridge ---------------------------------------
  /** Maps the game server will adjudicate. Empty until fetched, or if it is down. */
  const [rankedMaps, setRankedMaps] = useState<string[]>([]);
  const [nativeRunning, setNativeRunning] = useState(false);
  /** The render loop's copy: it runs outside React and cannot read state. */
  const nativeRunningRef = useRef(false);
  nativeRunningRef.current = nativeRunning;
  const [nativePid, setNativePid] = useState<number | undefined>();
  /** What the last native launch said, shown in the menu next to the buttons.
   * Cleared when that process exits — see the status poll below. */
  const [nativeStatus, setNativeStatus] = useState<string | null>(null);
  /** Whether the previous poll saw a live process, so the exit can be told from
   * the steady state of there never having been one. Without it a failed
   * launch's error message would be wiped by the very next poll. */
  const wasRunning = useRef(false);
  /** The node's launch job — see `native-launch.ts`. Survives this pane. */
  const launcher = useNativeLaunch();
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
   *
   * **Baselined from the server on mount, not from `0`.** A ref starting at zero
   * means "no match has ever been closed", so *any* undismissed row — one left
   * behind by a failed dismiss, or simply the last match of a previous session —
   * reopened the card the moment the pane was mounted. Docking, undocking or
   * switching workspaces was enough. What the pane actually wants to show is a
   * card earned *while it was open*, so it asks once what is already there and
   * treats all of it as seen.
   *
   * Not clock arithmetic: `played_at` is the backend's `time.time()` and has no
   * relationship to this browser's clock, so "newer than now" would either
   * swallow a real card or replay an old one depending on which way the two
   * machines were skewed.
   */
  const dismissedSummaryAt = useRef(0);
  /**
   * Whether that baseline has landed. The poll is suppressed until it has —
   * otherwise the first interval can win the race against the baseline request
   * and show the stale card exactly once, which is the whole bug wearing a
   * 1.5-second delay.
   */
  const summaryBaselined = useRef(false);

  const closeSummary = useCallback((summary: PostMatchSummary) => {
    dismissedSummaryAt.current = Math.max(dismissedSummaryAt.current, summary.timestamp);
    setPostMatchSummary(null);
    void dismissMatchSummary();
  }, []);

  useEffect(() => {
    let active = true;
    // Once, not on a poll: the server's bundled map list changes on a deploy, and
    // a menu that re-asked every few seconds would be spending a round trip to
    // hear the same three names.
    void getRankedMaps()
      .then((names) => {
        if (active) setRankedMaps(names);
      })
      .catch(() => {
        // Unreachable: Ranked greys out, which is the honest signal — better than
        // a button that fails at the socket after the map has loaded.
      });
    return () => {
      active = false;
    };
  }, []);

  /**
   * Park the pane's render loop for the duration of a native match.
   *
   * The frame callback already declined to *draw* while the native client was
   * up, but it kept re-arming itself, so a webview compositor woke at display
   * rate on the same GPU the game wants exclusively. Parking schedules nothing
   * at all. The scene is not torn down — `resume` is one frame, and the map is
   * back the instant the native window exits, which was the reason the loop was
   * left running in the first place.
   *
   * Keyed on `nativeRunning` rather than done inside `launchNative`, because the
   * flag is also cleared by the status poll below (the process exiting on its
   * own) and by the companion's Exit button — three call sites, one effect.
   */
  useEffect(() => {
    const scene = sceneRef.current;
    if (!scene) return;
    if (nativeRunning) scene.suspend();
    else scene.resume();
    // On unmount the scene effect's own cleanup cancels the frame, so there is
    // nothing to undo here: resuming a loop that is being torn down would only
    // schedule one more callback into a disposed renderer.
  }, [nativeRunning]);

  useEffect(() => {
    let active = true;
    // Everything the server is already holding counts as seen — see
    // `dismissedSummaryAt`. A failure leaves the pane unbaselined and the poll
    // parked, which is the safe direction: no card at all beats somebody else's.
    void getLatestMatchSummary()
      .then((sum) => {
        if (!active) return;
        dismissedSummaryAt.current = sum?.timestamp ?? 0;
        summaryBaselined.current = true;
      })
      .catch(() => {
        if (active) summaryBaselined.current = true;
      });
    const interval = setInterval(async () => {
      try {
        const [proc, sum] = await Promise.all([getProcessStatus(), getLatestMatchSummary()]);
        if (!active) return;
        // **The launch message belongs to a process that is running.** It used to
        // be set once and never cleared, so "Launched native FPS client (PID:
        // 55508)" sat under the Play buttons for the rest of the session — long
        // after that pid had exited, in green, next to buttons that would launch
        // a different one. The poll already knows the process is gone; this is
        // it acting on that.
        if (wasRunning.current && !proc.running) setNativeStatus(null);
        wasRunning.current = proc.running;
        setNativeRunning(proc.running);
        setNativePid(proc.pid);
        if (!sum) {
          // Cleared server-side — by this pane, another one, or a restart.
          setPostMatchSummary(null);
          return;
        }
        // Gated on the *summary* half only, not on the whole poll: the native
        // process status above has nothing to do with the debrief and should
        // keep updating while the baseline request is in flight.
        if (!summaryBaselined.current) return;
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
  /**
   * The interpolated rows the last frame drew, for the radar.
   *
   * A ref rather than state: these change sixty times a second and the radar is
   * a canvas, so pushing them through React would re-render the whole pane for a
   * blip that moved two pixels. The radar's effect re-runs on `hud` — which does
   * update per frame — and reads the newest rows from here.
   */
  const remoteRowsRef = useRef<PlayerRow[]>([]);
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
  /** Grenade selection and the throw edge. See `utility.ts`. */
  const nadesRef = useRef<GrenadeController | null>(null);
  /** Offline stand-in for everything a match server would own. See `training.ts`. */
  const rangeRef = useRef<TrainingRange | null>(null);
  /** Last pushed training state, so the frame loop can tell what changed. */
  const localYouRef = useRef<SelfState | null>(null);
  /** Read by the view model, which runs in the same loop and cannot await React. */
  const localReloadingRef = useRef(false);
  /**
   * Whether a grenade was in hand on the previous frame.
   *
   * An edge, not a level: `setBlocked` and `holster` are both things to do *on
   * the change*, and calling `holster` every frame would restart the stow
   * animation sixty times a second and leave the weapon permanently down.
   */
  const nadeHeldRef = useRef(false);
  /**
   * The served throw constants, or `null` while they are in flight or absent.
   *
   * `null` draws no arc at all rather than integrating with zeros — which would
   * be a straight line into the floor, i.e. an aiming aid that is confidently
   * wrong. A peer's node too old to serve `/throw` is a real case.
   */
  const throwPhysicsRef = useRef<ThrowPhysics | null>(null);
  /** Last zoom step pushed to React, so the loop only pushes transitions. */
  const scopedRef = useRef(0);
  const audioRef = useRef<GameAudio | null>(null);
  /** Bots the main menu asked for, waiting for the room to exist. `add_bot` needs a
   * room id, and the room is only ours once the welcome lands. */
  const pendingBotsRef = useRef<{ count: number; skill: string } | null>(null);
  if (sessionRef.current === null) sessionRef.current = new MatchSession();
  if (shotsRef.current === null) shotsRef.current = new ShotController();
  if (nadesRef.current === null) nadesRef.current = new GrenadeController();
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
  // By ref like the rest: the frame loop is built once and cannot read React
  // state, and a toggle has to reach it without tearing the scene down.
  const showHitboxesRef = useRef(showHitboxes);
  showHitboxesRef.current = showHitboxes;
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
    // The item table, for the same reason the loadout is fetched: Train resolves
    // its own ammunition pickups, and a copy of `respawn`/`mags`/`radius` here
    // would be a range whose items behave differently from a match's. A failure
    // is not surfaced — unlike an empty loadout, which leaves the trigger doing
    // nothing, an absent item table simply means no items on the range.
    void getItems()
      .then((table) => {
        if (cancelled) return;
        setItemTable(table);
      })
      .catch(() => {});

    return () => {
      cancelled = true;
    };
  }, []);

  // The grenades, fetched for the same reason the weapons are: the HUD shows a
  // carry count and the renderer draws a cloud at the served radius, and a
  // hardcoded copy of either is a smoke drawn a different size from the one
  // actually blocking sight on the server.
  useEffect(() => {
    let cancelled = false;
    void listTacticals()
      .then((specs) => {
        if (cancelled) return;
        setTacticals(specs);
        nadesRef.current?.setSpecs(specs);
        // The wire carries a *slot index*, so the served order is load-bearing:
        // a reordering on the server would silently turn every smoke key into an
        // HE. Checked rather than trusted, because the failure is invisible.
        const drifted = NADE_ACTIONS.filter(
          (entry, i) => specs[i] !== undefined && specs[i].id !== entry.id,
        );
        if (drifted.length > 0) {
          console.warn(
            '[hassault] grenade slots disagree with the server:',
            specs.map((spec) => spec.id).join(','),
            'expected',
            NADE_ACTIONS.map((n) => n.id).join(','),
          );
        }
      })
      .catch(() => {
        // Deliberately quiet, unlike the loadout: a backend older than this
        // route leaves you with no grenades, which is a smaller game rather than
        // a broken one — the trigger still works.
        if (!cancelled) setTacticals([]);
      });
    // The constants a throw is integrated with, on a route of their own — see
    // `ThrowPhysics` for why they are not a field on `/tacticals`. Failure
    // leaves the ref `null`, which draws no arc rather than a straight line into
    // the floor: an aiming aid that is confidently wrong is worse than none.
    void getThrowPhysics()
      .then((physics) => {
        if (!cancelled) throwPhysicsRef.current = physics;
      })
      .catch(() => {
        // A node too old to serve it. Quiet for the same reason as above.
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

  // Pickup feedback: the line that says what you just ran over, and its sound.
  //
  // Driven off `you.picked`, which the server drains: an item is reported once,
  // and the amounts in it are the ones that actually applied — a health pack
  // taken at 90 says "+10 hp", not "+25". The sound is synthesised locally
  // rather than waiting for the server to echo it back, exactly like our own
  // footsteps: a pickup noise arriving 50 ms after the pickup sounds like
  // somebody else's.
  const [picked, setPicked] = useState<{ at: number; text: string }[]>([]);
  useEffect(() => {
    const items = net.you?.picked;
    if (!items || items.length === 0) return;
    const at = Date.now();
    audioRef.current?.own('pickup', 0.6);
    setPicked((prev) => [
      ...prev.filter((p) => at - p.at < PICKUP_TTL_MS),
      ...items.map((item) => ({ at, text: describePickup(item) })),
    ]);
  }, [net.you]);

  // Same wall-clock problem the hit flashes have: nothing re-renders when a
  // pickup line simply gets old.
  useEffect(() => {
    if (picked.length === 0) return;
    const remaining = picked[picked.length - 1].at + PICKUP_TTL_MS + 20 - Date.now();
    if (remaining <= 0) {
      setPicked([]);
      return;
    }
    const timer = window.setTimeout(() => setPicked((p) => p.slice(0, 0)), remaining);
    return () => window.clearTimeout(timer);
  }, [picked]);

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
      // Slightly blue and slightly lifted off black. Pure `#0d1117` made the fog
      // read as the world dissolving into the panel's background rather than
      // into air, and gave a distant wall nothing to sit against.
      const HORIZON = 0x11161f;
      scene.background = new THREE.Color(HORIZON);
      // Fog hides the far clip plane and, on a 256-cube map, is a big win: it
      // stops the whole world reading as flat untextured colour at distance.
      // Exponential rather than linear — linear fog has a visible start plane
      // that sweeps across walls as you walk toward them, and `Exp2` is what
      // reads as air.
      scene.fog = new THREE.FogExp2(HORIZON, 0.0055);

      const camera = new THREE.PerspectiveCamera(75, 1, 0.1, 600);
      const renderer = new THREE.WebGLRenderer({ antialias: true });
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
      // ACES rather than the default clip: the sun plus a hemisphere light puts
      // lit floors above 1.0, and `NoToneMapping` flattens everything past that
      // into the same white — which is exactly where a bright surface loses the
      // grain the detail texture was added to give it.
      renderer.toneMapping = THREE.ACESFilmicToneMapping;
      renderer.toneMappingExposure = 1.15;
      // Static geometry and a static sun, so the shadow map is rendered **once
      // per map** rather than once per frame (see `setMesh`). That is the whole
      // reason real shadows are affordable here at all.
      renderer.shadowMap.enabled = true;
      renderer.shadowMap.type = THREE.PCFShadowMap;
      renderer.shadowMap.autoUpdate = false;
      mountRef.current.appendChild(renderer.domElement);
      renderer.domElement.style.display = 'block';
      renderer.domElement.style.width = '100%';
      renderer.domElement.style.height = '100%';
      renderer.domElement.style.cursor = 'crosshair';

      // Hemisphere light alone reads flat; the directional adds enough gradient
      // to tell walls from floors before real textures exist.
      scene.add(new THREE.HemisphereLight(0xbfd4ff, 0x33302c, 1.55));
      const sun = new THREE.DirectionalLight(0xfff2dd, 1.75);
      sun.castShadow = true;
      sun.shadow.mapSize.set(2048, 2048);
      // Normal bias, not just a constant one: these surfaces are large flat
      // quads at every angle to the sun, and a constant bias big enough to stop
      // acne on the floors detaches the shadows from the foot of every wall.
      sun.shadow.bias = -0.0004;
      sun.shadow.normalBias = 0.08;
      scene.add(sun);
      scene.add(sun.target);
      // A cool fill from behind, at a fraction of the sun's strength. Without
      // it every surface facing away from the sun is lit only by the hemisphere
      // and comes out the same value, which is what makes an unlit wall read as
      // a hole rather than as a wall in shade.
      const fill = new THREE.DirectionalLight(0x9fb6ff, 0.45);
      fill.position.set(-0.5, 0.35, -0.7);
      scene.add(fill);

      let mesh: import('three').Mesh | null = null;
      const detail = createDetailTexture(THREE, renderer.capabilities.getMaxAnisotropy());
      const material = new THREE.MeshLambertMaterial({
        vertexColors: true,
        map: detail,
        // The reciprocal of the tile's neutral value, so a pixel with no grain
        // leaves the surface exactly as `geometry.ts` coloured it. Without this
        // the detail map would darken the entire world by a quarter.
        color: new THREE.Color().setScalar(1 / DETAIL_NEUTRAL),
      });
      // **The single line that makes sun shadows possible in a Cube world.**
      //
      // three defaults `shadowSide` to `BackSide` for a front-sided material, to
      // hide the gap between a shadow and the object casting it. Here that is
      // fatal: a Cube 1 map is a sealed box, every open cell emits a *ceiling*
      // quad facing down, and rendering back faces into the shadow map means the
      // sky lid catches all the light and the entire level sits in shadow. It
      // reads as the lighting simply being broken.
      //
      // Front faces only means a surface casts when it faces the sun. Ceilings
      // face away and drop out; walls and floors cast exactly as they should.
      material.shadowSide = THREE.FrontSide;
      // Patched, not replaced: the build animation runs through the same lit
      // material the finished world uses, so nothing pops when it ends.
      const reveal = installReveal(material);
      const backdrop = createBackdrop(THREE, scene);
      const avatars = new AvatarPool(THREE, scene);
      const effects = new EffectsPool(THREE, scene);
      const decals = new DecalPool(THREE, scene);
      const arcLine = new ArcLine(THREE, scene);
      // Grenades in the air and the smoke/fire they leave. A renderer only: what
      // it draws is what the snapshot said, never anything it worked out.
      const nadePool = new NadePool(THREE, scene);
      // Items on the floor. Placements come once with the welcome and never
      // move; which of them are currently gone rides in every snapshot.
      const itemPool = new ItemPool(THREE, scene);
      let placedForRoom = '';
      // Whether we were in water last frame, so *entering* can be told from
      // *being in*: only the crossing makes a sound.
      let wasWet = false;
      let wasUnder = false;
      // The map's water plane and its ladders. Both are static — water is one
      // global height in Cube 1 and a ladder's span never changes — so they are
      // built once from the world and only disposed. An invisible water plane
      // would be the worst kind of bug now that it decides how a body moves.
      let water: ReturnType<typeof createWater> = null;
      let ladders: ReturnType<typeof createLadders> = null;
      // The gun in your hands. Parented to the camera by the constructor, which
      // is also what puts the camera in the scene graph.
      const viewmodel = new WeaponViewModel(THREE, scene, camera);
      // The weapon props are physically-based and metallic; the world is not.
      // Confined to the view model rather than set as `scene.environment`
      // precisely so the map's Lambert surfaces and the operator keep the look
      // the shared light rig gives them — this exists to stop the gun in your
      // hands rendering as a black silhouette, not to relight the game.
      const propEnvironment = createPropEnvironment(THREE, renderer);
      viewmodel.setEnvironment(propEnvironment);

      const setMesh = (world: World): number => {
        if (mesh) {
          scene.remove(mesh);
          mesh.geometry.dispose();
        }
        // Rebuilt with the mesh rather than beside it: both describe this map,
        // and a water plane left over from the previous one would hang in the
        // air at whatever height that map's was.
        water?.dispose();
        ladders?.dispose();
        water = createWater(THREE, scene, world);
        ladders = createLadders(THREE, scene, world);
        const data = buildWorldMesh(world);
        const geo = new THREE.BufferGeometry();
        geo.setAttribute('position', new THREE.BufferAttribute(data.positions, 3));
        geo.setAttribute('normal', new THREE.BufferAttribute(data.normals, 3));
        geo.setAttribute('color', new THREE.BufferAttribute(data.colors, 3));
        geo.setAttribute('uv', new THREE.BufferAttribute(data.uvs, 2));
        geo.computeBoundingSphere();
        mesh = new THREE.Mesh(geo, material);
        // Both, and both matter: a wall has to cast onto the floor beside it and
        // receive from the wall opposite. One-sided geometry means there are no
        // back faces to produce the peter-panning a single-sided caster usually
        // does.
        mesh.castShadow = true;
        mesh.receiveShadow = true;
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

        // Aim the sun at *this* map and re-render the shadow map once. The
        // frustum is fitted to the geometry's own bounds rather than to `ssize`
        // for the same reason the camera is: a map's grid is mostly empty
        // border, so a grid-sized shadow camera spends most of its texels on
        // nothing and leaves the level itself blocky.
        // Roughly 50 degrees up: high enough that a room is not half in shade,
        // low enough that a wall throws a shadow long enough to see. A sun
        // directly overhead casts almost nothing on a map made of vertical
        // walls, which is the failure mode this angle is chosen against.
        const reach = extent * 2;
        sun.position.set(cx + reach * 0.55, reach * 0.82, cz + reach * 0.36);
        sun.target.position.set(cx, 0, cz);
        sun.target.updateMatrixWorld();
        const cam = sun.shadow.camera;
        cam.left = -extent * 1.1;
        cam.right = extent * 1.1;
        cam.top = extent * 1.1;
        cam.bottom = -extent * 1.1;
        // Fitted around the map rather than left at 1..far: the depth range is
        // what the shadow's precision is spent on, and a near plane at 1 for a
        // light 170 units away throws most of it away.
        const distance = sun.position.distanceTo(sun.target.position);
        cam.near = Math.max(1, distance - extent * 1.4);
        cam.far = distance + extent * 1.4;
        cam.updateProjectionMatrix();
        // The world is static and so is the sun, so this is the only frame that
        // pays for shadows. `autoUpdate` stays off; anything that moves — the
        // avatars, the weapon in your hands — deliberately does not cast, since
        // a moving caster would need a map that is never rebuilt.
        renderer.shadowMap.needsUpdate = true;
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
      // Whether the loop has been parked (the native client is playing). Parked
      // means *nothing scheduled* — see `suspend` below.
      let suspended = false;
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

        // Belt and braces for the frame or two between the native client
        // starting and the effect below parking the loop. The real saving is
        // `suspend`, not this: returning here still leaves a callback firing at
        // display rate. `last` is updated above, so this frame's time is spent
        // rather than accumulated.
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
            // Resolved every frame, not only when a key was pressed: the
            // controller also adopts the server's carry counts here, so a throw
            // the server refused puts the number back on the HUD rather than
            // leaving it one short until the next respawn.
            const thrown = nadesRef.current?.frame(now, session.state.you ?? null);
            session.queue(session.predictor.record(world, player, input, dt, intent, kick, thrown));
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
            // Training has no server to throw at, so the intent is drained and
            // dropped. Draining it matters anyway: without it the press stays
            // queued and the grenade comes out on the frame you deploy into a
            // real match.
            nadesRef.current?.frame(now, self);
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
                // The range resolves its own shots, so its faces come from
                // `trace.ts` rather than off the wire — the same numbers, pinned
                // against the server's by `physics-vectors.json`. A range that
                // left no marks would be the one place you cannot see your own
                // spray pattern, which is what it is for.
                for (let i = 0; i < shot.ends.length; i++) {
                  decals.mark(shot.ends[i], shot.faces[i] ?? -1);
                }
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
            // Also after the step, for the same reason the server collects in
            // `_movement_consequences`: you pick something up by having moved
            // onto it, so reading the position from before the step would take
            // an item a frame early and miss one you ran straight through.
            range?.collect(player.x, player.y, player.z);
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
            // Breaking the surface, in either direction. Synthesised locally like
            // every other sound we make: the server does not send our own noises
            // back, because one arriving half a round trip late does not sound
            // like the thing that made it.
            const nowWet = inWater(world, player);
            if (nowWet !== wasWet) {
              wasWet = nowWet;
              audio.own('splash', 0.8);
            }
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
          // Set before the sync rather than in an effect of its own: the pool
          // builds and frees its wireframes inside `sync`, so the flag has to be
          // in force by the time it runs or the toggle lands a frame late.
          avatars.setHitboxes(showHitboxesRef.current);
          avatars.sync(remote, dt);
          remoteRowsRef.current = remote;
          // Straight off the newest snapshot rather than the interpolated
          // sample: a grenade is not a player, it has no prediction to reconcile
          // with, and `NadePool` does its own smoothing toward the last position
          // it was told about.
          const latest = session.snapshots.latest;
          nadePool.sync(latest?.nades, latest?.zones);
          // Placed from the render loop rather than from the welcome handler:
          // the pool needs the scene, which lives here, and a socket callback is
          // the wrong place to be building geometry. Keyed on the room so
          // rejoining rebuilds and a re-render does not.
          //
          // Offline the range holds the same placements (the server resolved
          // them either way — see `MapInfo.items`), so the pool takes one code
          // path and Train's items sit exactly where a match's do.
          const trainingRange = online ? null : rangeRef.current;
          const itemsKey = online ? `room:${session.state.room}` : 'training';
          if (placedForRoom !== itemsKey) {
            placedForRoom = itemsKey;
            itemPool.place(online ? session.state.items : (trainingRange?.placements() ?? []));
          }
          itemPool.sync(online ? latest?.itemsOut : trainingRange?.takenIds());
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
              // One mark per pellet that stopped on a surface. `faces` is
              // optional on the wire — a fabric peer may be running an older
              // backend — and an absent list means "no marks", never "mark
              // everything": `-1` is refused by `mark` itself.
              for (let i = 0; i < fx.ends.length; i++) {
                decals.mark(fx.ends[i], fx.faces?.[i] ?? -1);
              }
              // Flash and kick the shooter's own avatar. Our body is not drawn
              // (we are inside it), so this only ever lands on someone else.
              avatars.fired(fx.id);
            }
            session.pendingShots = [];
          }
          if (session.pendingBlasts.length > 0) {
            const audio = audioRef.current;
            for (const blast of session.pendingBlasts) {
              effects.blast(blast.at, blast.radius, blast.nade);
              // Played from here rather than through the noise envelope, because
              // a detonation is not a noise you might not hear: it is a thing
              // that visibly happened in front of you, and the envelope's job is
              // deciding audibility for things you cannot see.
              audio?.own(blast.nade === 'he' ? 'explosion' : `nade_${blast.nade}`, 1);
            }
            session.pendingBlasts = [];
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
        decals.update(dt);
        nadePool.update(dt);
        itemPool.update(dt);

        const elapsed = (now - started) / 1000;
        backdrop.update(elapsed);
        water?.update(elapsed);
        // Outside the audio block on purpose: the tint is not a sound, and a
        // player with the volume at zero still has to be told their head is under.
        if (worldRef.current) {
          const nowUnder = submerged(worldRef.current, playerRef.current);
          if (nowUnder !== wasUnder) {
            wasUnder = nowUnder;
            setUnderwater(nowUnder);
          }
        }

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
          // **What is in your hand this frame.** The trigger is blocked and the
          // weapon stowed while a grenade is up, and both come back the instant
          // it leaves — a throw is one action, not a mode you have to leave.
          //
          // Cosmetic on the wire, deliberately: the alternative (`shots.select`
          // to some other slot) puts `weapon: n` on the next command, and
          // `_handle_combat` cancels an in-flight reload on a switch. Equipping
          // a grenade would then silently abort a reload.
          const nades = nadesRef.current;
          const holdingNade = nades?.equipped ?? false;
          if (holdingNade !== nadeHeldRef.current) {
            nadeHeldRef.current = holdingNade;
            shots?.setBlocked(holdingNade);
            if (holdingNade) viewmodel.holster();
          }
          // Pull the pin, wind up, release. Played on the frame the grenade
          // actually left — `justThrew` rather than the key press, so a throw
          // the cooldown refused does not animate one that never happened.
          if (nades?.justThrew) viewmodel.throwNade();
          // **The predicted arc.** Drawn from the *locally predicted* velocity,
          // not from the last snapshot's: the whole reason it exists is to make
          // `THROW_INHERIT` visible — running and jumping feed the throw — and a
          // velocity half a round trip old would lag exactly the movement it is
          // meant to be showing.
          const throwPhysics = throwPhysicsRef.current;
          if (holdingNade && throwPhysics && world) {
            const eyeZ = eyeHeight(player);
            const arc = simulateThrow(
              world,
              throwOrigin(player.x, player.y, eyeZ, player.yaw, player.pitch, throwPhysics),
              throwVelocity(
                player.yaw,
                player.pitch,
                // The right button is the lob, and the preview has to show the
                // one the button under your finger would throw — but nothing is
                // held down while aiming, so the arc shows the full throw and
                // the lob is read off it as "much shorter than that".
                false,
                [player.velX ?? 0, player.velY ?? 0, player.velZ ?? 0],
                throwPhysics,
              ),
              throwPhysics,
            );
            arcLine.show(arc);
          } else {
            arcLine.hide();
          }
          const heldWeapon = shots?.weapon?.id ?? '';
          viewmodel.setWeapon(heldWeapon, skinsRef.current[heldWeapon] ?? null);
          // The reload's *progress*, from the two served numbers: how long it
          // takes (`reloadTime`, on the weapon) and how much is left
          // (`reloadIn`, on the snapshot). `null` when there is no length to
          // measure against, which is a different fact from "just started" — see
          // `ViewModelFrame.reloadProgress`. The range fills both, so a reload
          // dips identically in Train and in a match with no second code path.
          const reloadTime = shots?.weapon?.reloadTime ?? 0;
          const reloadLeft = online
            ? (session?.state.you?.reloadIn ?? 0)
            : (rangeRef.current?.selfState().reloadIn ?? 0);
          viewmodel.update(dt, {
            speed: moving ? MOVE_SPEED : 0,
            onGround: player.onGround,
            reloading: online
              ? (session?.state.you?.reloading ?? false)
              : localReloadingRef.current,
            reloadProgress:
              reloadTime > 0 ? Math.max(0, Math.min(1, 1 - reloadLeft / reloadTime)) : null,
            yaw: player.yaw,
            pitch: player.pitch,
            visible: alive,
            // For the landing dip. A *duration*, which is also what the server
            // sends (`you.move.sinceLanded`) and for the same reason: the two
            // simulated clocks are unrelated, so a timestamp from one measured
            // against the other means nothing.
            sinceLanded: Math.max(0, player.t - player.landedAt),
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

      /**
       * Park the render loop entirely.
       *
       * Not the same as skipping the draw. While the native client is playing
       * this scene is invisible behind the companion overlay, but a scheduled
       * `requestAnimationFrame` still wakes the webview's compositor at display
       * rate on the very GPU the game is trying to have to itself. The scene,
       * its geometry and the pools all stay resident, so `resume` puts the map
       * back on the next frame rather than rebuilding it.
       */
      const suspend = () => {
        if (suspended) return;
        suspended = true;
        cancelAnimationFrame(raf);
        raf = 0;
      };

      const resume = () => {
        if (!suspended) return;
        suspended = false;
        // Before re-arming, not after: `now - last` would otherwise be the whole
        // length of the native match. `step` clamps its own `dt`, but the FPS
        // accumulator and `backdrop.update(elapsed)` do not.
        last = performance.now();
        raf = requestAnimationFrame(frame);
      };

      const setFov = (degrees: number) => {
        camera.fov = degrees;
        camera.updateProjectionMatrix();
      };

      sceneRef.current = {
        setMesh,
        setFov,
        suspend,
        resume,
        avatars,
        weapon: viewmodel,
        reveal,
        backdrop,
        camera: camera as never,
      };
      // If a native match is already in flight, this loop is born parked. The
      // effect keyed on `nativeRunning` cannot do it: it ran before this scene
      // existed, so without this a scene rebuilt mid-match would come up armed
      // and stay that way until the native window exited.
      if (nativeRunningRef.current) suspend();

      // Unblocks the map load, which has been waiting rather than polling.
      sceneReadyRef.current?.resolve();

      cleanup = () => {
        cancelAnimationFrame(raf);
        observer.disconnect();
        avatars.dispose();
        effects.dispose();
        decals.dispose();
        arcLine.dispose();
        nadePool.dispose();
        itemPool.dispose();
        water?.dispose();
        ladders?.dispose();
        viewmodel.dispose();
        backdrop.dispose();
        if (mesh) mesh.geometry.dispose();
        material.dispose();
        detail.dispose();
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
      // **With a grenade in hand the mouse means throw and toss.** Left is the
      // full overhand throw, right is the short underhand lob — the two the
      // server has always known about, now on the two buttons a hand is already
      // on rather than on `G` and `H`.
      //
      // This is why selecting a grenade *equips* it rather than merely readying
      // one: a global right-click toss would take the scope away from the
      // sniper, whose whole identity is that scope.
      if (nadesRef.current?.equipped) {
        if (e.button !== 0 && e.button !== 2) return;
        e.preventDefault();
        nadesRef.current.press(e.button === 2);
        return;
      }
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
      // Backquote (`) or Tilde (~) opens/closes the developer console
      if (e.code === 'Backquote' || (e.key === '`' && !e.ctrlKey && !e.altKey)) {
        e.preventDefault();
        if (consoleOpenRef.current) {
          setConsoleOpen(false);
          if (!menuOpenRef.current) grabInput();
        } else {
          if (document.pointerLockElement) document.exitPointerLock?.();
          setConsoleOpen(true);
        }
        return;
      }
      if (consoleOpenRef.current) {
        if (e.key === 'Escape') {
          e.preventDefault();
          setConsoleOpen(false);
          if (!menuOpenRef.current) grabInput();
        }
        return;
      }

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
      // Purely local — see `WeaponViewModel.inspect`. It is never a command, so
      // it needs no server, works in Train, and costs the wire nothing.
      if (action === 'inspect') sceneRef.current?.weapon.inspect();
      if (action === 'scores') setShowScores(true);
      // Two modes, one ref. In hold mode the key up clears it; in toggle mode
      // nothing does but the next press, which is exactly why crouch cannot live
      // in `keysRef` with the rest of the movement keys.
      if (action === 'crouch') {
        crouchRef.current = crouchToggleRef.current ? !crouchRef.current : true;
      }
      if (action.startsWith('weapon')) {
        // A weapon key puts the grenade away — the counterpart of a number key
        // taking one out. Purely cosmetic on the wire: `holster` sets no slot.
        nadesRef.current?.holster();
        shotsRef.current?.select(Number(action.slice(6)) - 1);
      }
      // Selecting **equips**: the weapon goes down and the two mouse buttons
      // become throw and toss. Picking a grenade and choosing the moment are
      // still two decisions — the second one is now a click rather than a
      // second key on the other side of the keyboard.
      const nadeSlot = NADE_ACTIONS.findIndex((n) => n.action === action);
      if (nadeSlot >= 0) nadesRef.current?.equip(nadeSlot);
      // Edge-triggered here, at the key, and not read from `keysRef` in the
      // frame loop: `throw` rides on a movement command, so a held key read as a
      // level would set the flag sixty times a second. `e.repeat` is already
      // filtered above, which is what makes this one press.
      // Still bound, and still working: a player who has rebound the mouse, or
      // simply learned these, should not lose them because the default moved.
      if (action === 'throw') nadesRef.current?.press(false);
      if (action === 'lob') nadesRef.current?.press(true);
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
    (opts: Omit<LaunchNativeOptions, 'map_name'> & { map_name?: string }) => {
      sessionRef.current?.leave();
      // Fire-and-watch, not await. When the client has been edited since it was
      // last built the node compiles it first, which is minutes — and this pane
      // is unmounted the instant its tab loses focus, so a promise awaited here
      // was simply dropped and the launch looked like it had stopped. The job
      // lives on the node now, and `useNativeLaunch` reads it back on mount.
      void launcher.launch({ map_name: mapName, max_fps: 240, ...opts });
    },
    [launcher, mapName],
  );

  /**
   * The launch job's own report, folded into the pane's native-process state.
   *
   * Kept as an effect rather than done inside `launchNative` for the reason the
   * hook exists at all: a launch that finishes while this pane is unmounted is
   * adopted when it comes back, and there is no press to hang that on.
   */
  useEffect(() => {
    const res = launcher.result;
    if (!res) return;
    setNativeStatus(launchMessage(res));
    if (res.launched) {
      setNativeRunning(true);
      setNativePid(res.pid);
      // Armed here rather than waiting for the poll to notice: a client that
      // exits within the poll interval would otherwise never be seen running at
      // all, and its launch message would stay up for the session.
      wasRunning.current = true;
    }
  }, [launcher.result]);

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
      // The map's own items, exactly where a match would put them: the server
      // resolved these heights, so the range never has to.
      if (itemTable && info) {
        range.placeItems(info.items, itemTable.kinds, itemTable.reach);
      }
    }
    deploy();
  }, [deploy, weapons, nativeClient, launchNative, itemTable, info]);

  /**
   * Open the map designer, seeded from the selected map or (`blank`) from solid
   * rock.
   *
   * Native-only: there is no in-pane fallback, unlike Train and Host — the
   * visual half of the editor (`apps/native-fps/src/editor.rs`) exists only in
   * the native client (real lighting, raw mouse input; see
   * docs/modules/hassault.mdx#the-map-designer). `MainMenu` disables the row
   * when `nativeClient` is off rather than this silently doing nothing.
   */
  const editMap = useCallback(
    (blank: boolean) => {
      if (!nativeClient) return;
      void launchNative({ mode: 'edit', blank });
    },
    [launchNative, nativeClient],
  );

  /**
   * Play a match the **game server** adjudicates.
   *
   * The only difference from `host` is the flag: the node opens the room
   * somewhere else and proxies for us, and every event after the join is the
   * wire this pane already reads. No bots — a match whose roster a player can
   * reshape is not one their result should count for, and the server refuses
   * them anyway.
   */
  const ranked = useCallback(() => {
    if (nativeClient) {
      void launchNative({ mode: 'ranked' });
      return;
    }
    const session = sessionRef.current;
    if (!session || !mapName) return;
    shotsRef.current?.reset();
    session.join(mapName, playerName, undefined, undefined, true);
    deploy();
  }, [mapName, playerName, deploy, nativeClient, launchNative]);

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
  // Our own team, read off the roster rather than tracked separately — the row
  // is already in `peers`, and a second copy is a copy that can be stale.
  const myTeam = net.peers.find((p) => p.id === net.playerId)?.team ?? 0;
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
                  {describeControls(controls)} · Esc menu · ` console
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

        {/* CS:GO Style NetGraph HUD */}
        {phase === 'playing' && <NetGraphHUD rttMs={net.rtt} />}

        {/* Developer Console (Overlay) */}
        <DeveloperConsole
          isOpen={consoleOpen}
          onClose={() => {
            setConsoleOpen(false);
            if (!menuOpen) grabInput();
          }}
          roomId={net.room}
          mapName={mapName}
          rttMs={net.rtt}
        />

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
                onEditMap={editMap}
                onQuickPlay={quickPlay}
                onRanked={ranked}
                rankedMaps={rankedMaps}
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
                  {/* Armour beside health rather than as its own bar: it is a
                      second pool of the same resource, and reading "how much can
                      I take" off two widgets in two places is the thing a HUD is
                      supposed to save you. Hidden at zero — a permanent 0 next
                      to the health is noise for the whole first minute of every
                      round. */}
                  {!!you.armour && (
                    <span style={{ color: '#6f97c4' }}>
                      {' '}
                      {you.armour}
                      <span style={{ fontSize: '0.7rem', opacity: 0.6 }}> ar</span>
                    </span>
                  )}
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

            {/* Pickup lines, above the bottom-left readout they are about. */}
            {picked.length > 0 && (
              <div
                style={{
                  position: 'absolute',
                  left: 8,
                  bottom: 96,
                  pointerEvents: 'none',
                  fontFamily: 'monospace',
                  fontSize: '0.78rem',
                  letterSpacing: '0.06em',
                  color: '#7ee787',
                  textShadow: '0 1px 2px rgba(0,0,0,0.8)',
                }}
              >
                {picked.map((line) => (
                  <div key={`${line.at}-${line.text}`}>{line.text}</div>
                ))}
              </div>
            )}

            {/* Underwater. A tint rather than a post-process: it has to read as
                "your head is under", and the cheapest honest way to say that is
                to put water between the player and everything else. Drawn below
                the crosshair so aiming still works — swimming is a bad place to
                fight, not a blindfold. */}
            {underwater && (
              <div
                style={{
                  position: 'absolute',
                  inset: 0,
                  pointerEvents: 'none',
                  background:
                    'radial-gradient(ellipse at center, rgba(24,86,120,0.28) 0%, rgba(12,46,68,0.62) 100%)',
                }}
              />
            )}

            <NoiseRing heard={heard} yaw={hud.yaw} />

            {/* The radar. Which enemies are on it is `you.spotted`, decided by
                the server — see `MatchRoom.spotted_by`. */}
            {online && (
              <Radar
                world={worldRef.current}
                me={{ x: hud.x, y: hud.y, yaw: hud.yaw }}
                myId={net.playerId}
                myTeam={myTeam}
                rows={remoteRowsRef.current}
                spotted={you?.spotted ?? EMPTY_SPOTTED}
              />
            )}

            <NadeTray
              specs={tacticals}
              counts={nadesRef.current?.carried ?? EMPTY_COUNTS}
              selected={nadesRef.current?.selected ?? 0}
            />

            {/* Drawn last, over everything including the crosshair — a flash you
                could aim through would not be a flash. */}
            <FlashOverlay strength={you?.flash ?? 0} />

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
