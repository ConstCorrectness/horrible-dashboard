import { registry, type ModuleManifest } from '../../registry';
import { registerNotificationAction } from '../notifications';
import { lazyPane } from '../../lazy-pane';
import { requestJoin } from './invite-notify';

// Every pane here pulls three.js (and the play pane Rapier's WASM), so none of it
// is imported until a pane opens. See `lazyPane`.
const HorribleAssaultPanel = lazyPane(
  () => import('./HorribleAssaultPanel'),
  'HorribleAssaultPanel',
);
const DeveloperConsole = lazyPane(() => import('./console'), 'DeveloperConsole');
const ModelStudioPanel = lazyPane(() => import('./studio/ModelStudioPanel'), 'ModelStudioPanel');
const ServerPanel = lazyPane(() => import('./panels/ServerPanel'), 'ServerPanel');
const StandaloneMatchCompanionPanel = lazyPane(
  () => import('./panels/MatchCompanion'),
  'StandaloneMatchCompanionPanel',
);
const StandaloneRadarPanel = lazyPane(
  () => import('./panels/StandaloneRadarPanel'),
  'StandaloneRadarPanel',
);
const VoiceCommsPanel = lazyPane(() => import('./panels/VoiceCommsPanel'), 'VoiceCommsPanel');

/**
 * What the **Join** button on an invite toast does.
 *
 * Registered here rather than in the notifications module, because joining a
 * match is this module's business and `notifications` importing it would invert
 * the dependency — the notification feed initialises at boot, and it must not
 * drag a three.js pane in with it.
 *
 * Opening the pane is deliberately *not* the same as joining it: the pane may not
 * be mounted, and a `MatchSession` only exists once it is. So this opens it and
 * parks the intent, and `HorribleAssaultPanel` acts on it when it has a session
 * and a world. Registration is module-scope so a toast is actionable even if the
 * pane has never been opened in this session — which is the entire case this
 * exists for.
 */
registerNotificationAction('hassault.joinInvite', {
  label: 'Join',
  run: (item) => {
    const invite = item.data.invite as
      | { room?: string; map?: string; host?: string; kind?: 'lobby' | 'match' }
      | undefined;
    if (!invite?.room) return;
    registry.openPanel('hassault.play');
    requestJoin({
      room: invite.room,
      map: invite.map ?? '',
      host: invite.host ?? '',
      kind: invite.kind,
    });
  },
});

/**
 * HorribleAssault, frontend side: a WebGL first-person renderer for AssaultCube
 * maps, built from the cube grid the backend serves.
 *
 * The pane is a `document` — it wants the centre area and a lot of pixels, and it
 * grabs pointer lock, which would be hostile in a narrow dock.
 *
 * See docs/modules/hassault.mdx.
 */
export const hassaultModule: ModuleManifest = {
  id: 'hassault',
  title: 'HorribleAssault',
  category: 'play',
  // Three windows, one per job: **play** the game, **build** for it, **run a
  // server** for it. It used to be ten — the studio three more times on a
  // different tab, an armory pane duplicating the main menu's armory section, and
  // four narrow companions each claiming a dock of their own. The companions are
  // now strips on the window they accompany (`embedded`), so they open beside the
  // game rather than somewhere else in the frame. Retired ids are migrated by
  // `RENAMED_VIEWS` (saved layouts) and `VIEW_ALIASES` (the agent's vocabulary).
  panels: [
    {
      id: 'hassault.play',
      title: 'HorribleAssault',
      component: HorribleAssaultPanel,
      role: 'document',
      // A first-person shooter has no use for a taskbar in its peripheral
      // vision, and on the desktop build presenting it takes the OS window
      // fullscreen too — which is what makes pointer lock and a full field of
      // view behave the way the game assumes.
      fullscreen: true,
      icon: '⌖',
      singleton: true,
      regions: [
        { id: 'hassault.companion', label: 'Match', icon: '▤', position: 'right', defaultSize: 320 },
        { id: 'hassault.radar', label: 'Radar', icon: '⦿', position: 'right' },
        { id: 'hassault.voice', label: 'Voice', icon: '◖', position: 'right' },
        { id: 'hassault.console', label: 'Console', icon: '⌨', position: 'bottom', defaultSize: 220 },
      ],
    },
    {
      id: 'hassault.studio',
      title: 'hAssault Dev Tools',
      component: ModelStudioPanel,
      role: 'document',
      icon: '◈',
      singleton: true,
      regions: [
        { id: 'hassault.console', label: 'Console', icon: '⌨', position: 'bottom', defaultSize: 220 },
        { id: 'hassault.radar', label: 'Radar', icon: '⦿', position: 'right', defaultSize: 300 },
      ],
    },
    {
      id: 'hassault.server',
      title: 'hAssault Server',
      component: ServerPanel,
      role: 'document',
      icon: '⌬',
      singleton: true,
    },
    {
      id: 'hassault.console',
      title: 'hAssault Developer Console',
      component: DeveloperConsole,
      role: 'tool',
      icon: '⌨',
      embedded: true,
    },
    {
      id: 'hassault.companion',
      title: 'hAssault Match Companion',
      component: StandaloneMatchCompanionPanel,
      role: 'tool',
      icon: '⌖',
      embedded: true,
    },
    {
      id: 'hassault.radar',
      title: 'hAssault Tactical Radar',
      component: StandaloneRadarPanel,
      role: 'tool',
      icon: '⦿',
      embedded: true,
    },
    {
      id: 'hassault.voice',
      title: 'hAssault Voice Comms',
      component: VoiceCommsPanel,
      role: 'tool',
      icon: '🎙',
      embedded: true,
    },
  ],
  frames: [
    {
      id: 'hassault_dev',
      name: 'hAssault: Game Dev & Testing',
      icon: '⚒',
      description:
        'The game beside its dev tools: a live match on the left, the model and level studio on the right with the console strip below it.',
      frame: {
        center: {
          split: 'row',
          sizes: [0.55, 0.45],
          children: [
            { pane: 'hassault.play', headerCollapsed: true },
            { pane: 'hassault.studio', headerCollapsed: false },
          ],
        },
        docks: {},
      },
    },
    {
      id: 'hassault_play',
      name: 'hAssault: Arena Match',
      icon: '🎮',
      description:
        'Just the match, at full width. The match companion, radar, voice and console are strips on the game window — open them from its edge.',
      frame: {
        center: { pane: 'hassault.play', headerCollapsed: true },
        docks: {},
      },
    },
  ],
  // The retired openers stay as commands — keybindings and muscle memory point
  // at them — and now land on the strip or studio tab that replaced each pane.
  commands: [
    {
      id: 'hassault.open',
      title: 'HorribleAssault: Open',
      run: () => registry.openPanel('hassault.play'),
    },
    {
      id: 'hassault.openServer',
      title: 'HorribleAssault: Open Server',
      run: () => registry.openPanel('hassault.server'),
    },
    {
      id: 'hassault.openStudio',
      title: 'HorribleAssault: Open Dev Tools',
      run: () => registry.openPanel('hassault.studio'),
    },
    {
      id: 'hassault.openModelViewer',
      title: 'HorribleAssault: Dev Tools › Asset viewer',
      run: () => registry.openPanel('hassault.studio', { params: { tab: 'viewer' } }),
    },
    {
      id: 'hassault.openModelEditor',
      title: 'HorribleAssault: Dev Tools › Rig editor',
      run: () => registry.openPanel('hassault.studio', { params: { tab: 'editor' } }),
    },
    {
      id: 'hassault.openAnimEditor',
      title: 'HorribleAssault: Dev Tools › Animator',
      run: () => registry.openPanel('hassault.studio', { params: { tab: 'animator' } }),
    },
    {
      id: 'hassault.openConsole',
      title: 'HorribleAssault: Show Developer Console',
      run: () => registry.openPanel('hassault.console'),
    },
    {
      id: 'hassault.openCompanion',
      title: 'HorribleAssault: Show Match Companion',
      run: () => registry.openPanel('hassault.companion'),
    },
    {
      id: 'hassault.openRadar',
      title: 'HorribleAssault: Show Tactical Radar',
      run: () => registry.openPanel('hassault.radar'),
    },
    {
      id: 'hassault.openVoice',
      title: 'HorribleAssault: Show Voice Comms',
      run: () => registry.openPanel('hassault.voice'),
    },
  ],
  settings: [
    {
      key: 'hassault.sensitivity',
      title: 'Mouse sensitivity',
      description:
        'Multiplies how far the view turns per pixel of mouse movement, for this game only. Also adjustable from the main menu and the pause menu (Esc). The control map lives there too — it is a document rather than a value, so it has no row here.',
      type: 'number',
      default: 1,
    },
    {
      key: 'hassault.fov',
      title: 'Field of view',
      description:
        'Vertical field of view in degrees, 60–110. Wider sees more of the map and makes movement read as faster. Applies immediately, mid-match included.',
      type: 'number',
      default: 75,
    },
    {
      key: 'hassault.volume',
      title: 'Volume',
      description:
        'Footsteps, shots, jumps and landings, 0–1. Every sound is synthesized on the fly — none of it is a downloaded asset, and none of it is anybody else’s copyright. Zero never even opens an audio device.',
      type: 'number',
      default: 0.7,
    },
    {
      key: 'hassault.crouchToggle',
      title: 'Crouch toggles',
      description:
        'Off (the default) means crouch is a hold, which is what the movement rewards — you can release it the instant you need speed back. On makes it a toggle, which is easier on the hand.',
      type: 'boolean',
      default: false,
    },
    {
      key: 'hassault.voice.pushToTalk',
      title: 'Lobby voice: push to talk',
      description:
        'Off (the default) keeps your mic open while you are in lobby voice, muted only by the Mute button. On keeps it closed except while the Push to talk key is held (V unless rebound in Controls). Either way the audio goes browser to browser, never through a server.',
      type: 'boolean',
      default: false,
    },
    {
      key: 'hassault.nativeClient',
      title: 'Play in the native client',
      description:
        'On by default, and the way in: Play, Train and Host open the native window — GPU-rendered, raw mouse input, no frame cap, its own HUD, scoreboard, weapon and sound. This pane is then setup and spectating. Turning it off plays in the pane instead, which is the same game and a slower one; it is kept because the pane is also the third implementation the physics conformance fixture pins.',
      type: 'boolean',
      default: true,
    },
    {
      key: 'hassault.video.fullscreen',
      title: 'Native client: fullscreen',
      description:
        'Whether the native client opens fullscreen (borderless). On by default — a shooter that opens in a window with a title bar is one you have to configure before it feels like a game. Editable from the in-game menu on Escape, which is what writes this row.',
      type: 'boolean',
      default: true,
    },
    {
      key: 'hassault.video.renderScale',
      title: 'Native client: resolution scale',
      description:
        'Fraction of the window the world is rendered at, 0.5–1. The HUD is never scaled with it, so text stays crisp at any setting. Lower it if the frame rate is short on an integrated GPU; on a discrete card it makes no measurable difference.',
      type: 'number',
      default: 1,
    },
    {
      key: 'hassault.video.quality',
      title: 'Native client: graphics quality',
      description:
        'low, medium or high — a preset, not a ceiling. It moves the shading (flat, a directional wash, the wash plus a rim), how far the fog reaches, and it writes the anti-aliasing row below. Changing that row afterwards is honoured: the preset is applied when you pick it, never re-applied on top of a later choice.',
      type: 'string',
      default: 'medium',
    },
    {
      key: 'hassault.video.fov',
      title: 'Native client: field of view',
      description:
        'Vertical field of view in degrees, 70–120, before a scope divides it. 75 is the pane’s, so the default is the same picture in both clients. The one video setting that changes how the game plays rather than how it looks — a wider view is more of the room and a smaller enemy in it.',
      type: 'number',
      default: 75,
    },
    {
      key: 'hassault.video.antialias',
      title: 'Native client: anti-aliasing',
      description:
        '4× multisampling, on or off — there is deliberately nothing between. 1 and 4 are the only sample counts the WebGPU spec guarantees a format supports; 2× is a validation error at pipeline creation on plenty of GPUs that list it, which is a crash on the first frame rather than a slower one. Set by the quality preset and overridable from here or the in-game menu.',
      type: 'boolean',
      default: false,
    },
    {
      key: 'hassault.video.shadows',
      title: 'Native client: shadows',
      description:
        'Whether world surfaces sample the sun’s shadow map. This is a look, not a frame rate: the map and the sun are both static, so the shadow map is baked once at load and turning this off skips no pass — only the shader’s filter taps. Offered because some people prefer the flat read, not as a performance setting.',
      type: 'boolean',
      default: true,
    },
    {
      key: 'hassault.video.fpsLimit',
      title: 'Native client: frame cap',
      description:
        'Frames per second to cap at, or 0 for uncapped (the default). One of 0, 60, 120, 144, 240 or 360; anything else is snapped to the nearest. Unlike vsync this queues no frame and adds no latency, it only sleeps — it is for a laptop that would otherwise run its fan flat out drawing frames no display will show, and then thermally throttle below the cap anyway.',
      type: 'number',
      default: 0,
    },
    {
      key: 'hassault.video.vsync',
      title: 'Native client: vsync',
      description:
        'Off by default. A frame of queued latency is precisely what the native client exists to avoid — but tearing is real, and somebody who can see it should be able to say so.',
      type: 'boolean',
      default: false,
    },
    {
      key: 'hassault.crosshair.style',
      title: 'Crosshair style',
      description:
        'cross, crossDot, dot or circle. The circle draws a ring at the spread radius, which is the honest picture of a cone. Every style still opens with the weapon — a reticle that could be configured to hide the hip-fire penalty would be a setting that wins gunfights.',
      type: 'string',
      default: 'cross',
    },
    {
      key: 'hassault.crosshair.size',
      title: 'Crosshair size',
      description:
        'Arm length, 1–12. Scaled with the window, so it means the same on every monitor.',
      type: 'number',
      default: 3,
    },
    {
      key: 'hassault.crosshair.gap',
      title: 'Crosshair gap',
      description:
        "Distance from the centre to the inside of each arm, 0–20. The floor the crosshair opens from as the weapon's cone widens, never a cap on it.",
      type: 'number',
      default: 4,
    },
    {
      key: 'hassault.crosshair.thickness',
      title: 'Crosshair thickness',
      description: 'Line thickness, 0.2–3.',
      type: 'number',
      default: 0.6,
    },
    {
      key: 'hassault.crosshair.color',
      title: 'Crosshair colour',
      description:
        'white, green, cyan, amber, magenta or red. Named rather than a hex field: the reason to change it is contrast against a particular map, and all six are bright — a dark crosshair on a dark map is the one choice that would make the game worse.',
      type: 'string',
      default: 'white',
    },
    {
      // Declared here rather than only in the native client's `settings.rs`, so
      // it shows up on the Settings page and both clients read the same row —
      // which is the point: a hitbox overlay you can only turn on in one of them
      // cannot be used to compare them.
      key: 'hassault.debug.hitboxes',
      title: 'Show hitboxes',
      description:
        'Draw the exact volume a shot is resolved against around every body, with the head band marked. Off by default. It reads the served spec rather than a second copy of the numbers — which is the whole use of it: a body drawn a few percent off its hitbox costs you shots you were sure of and reads as lag rather than as a drawing bug.',
      type: 'boolean',
      default: false,
    },
    {
      key: 'hassault.nativeBinaryPath',
      title: 'Native client binary',
      description:
        'Path to the native client executable, when you want a specific one. Blank resolves three tiers in order: your own build under apps/native-fps/target, then a prebuilt client downloaded from the release matching this app version (the Install button in the game’s main menu). A local build always wins over a download — otherwise installing once would silently start running the release instead of the change you are working on.',
      type: 'string',
      default: '',
    },
    {
      // The launch path's guard against the silent failure that outlived the
      // `pick_binary` fix: the newest build on disk is still older than the
      // source the moment the client is edited, and a game that starts and runs
      // perfectly without the change in it reads as a change that did not work.
      key: 'hassault.autoBuildNative',
      title: 'Rebuild the native client before launching',
      description:
        'When the built client is older than its own source, compile it before starting it. On means a first launch after editing the client takes as long as a cargo build; off means it starts immediately and says, on the launch itself, that it predates your change. Ignored when `hassault.nativeBinaryPath` names a binary — that is you naming the build you mean — and on an install with no crate beside the binary there is nothing to be stale against.',
      type: 'boolean',
      default: true,
    },
    {
      key: 'hassault.installPath',
      title: 'AssaultCube install path',
      description:
        'Folder containing packages/maps — the game content is read from your own copy and never bundled with this app. Blank auto-detects the usual locations for your platform.',
      type: 'string',
      default: '',
    },
  ],
};

export { lobbyVoice } from './lobby-voice';
export { listMaps, getMapCubes, getMapInfo, type MapSummary, type MapInfo } from './api';
export { formatBytes } from './boot';
