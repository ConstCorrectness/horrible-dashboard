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
      key: 'hassault.graphics',
      title: 'Graphics (pane and web)',
      description:
        'auto, low, medium or high — how much of the map’s served look this pane draws: pixel ratio, shadow resolution and softness, how many of the map’s lamps light a frame (0, 4 or 8), the sky dome, texture filtering and how far the fog reaches. Every level draws the same map, sky and lamps as the native window, only fewer of them lower down. auto picks from the GPU the browser reports. Anti-aliasing is a WebGL context flag and changes the next time the pane opens.',
      type: 'string',
      default: 'auto',
    },
    {
      key: 'hassault.video.fullscreen',
      title: 'Native client: fullscreen',
      description:
        'Whether the native client opens fullscreen. On by default — a shooter that opens in a window with a title bar is one you have to configure before it feels like a game. Borderless unless the display mode below says exclusive. Editable from the in-game menu on Escape, which is what writes this row.',
      type: 'boolean',
      default: true,
    },
    {
      key: 'hassault.video.displayMode',
      title: 'Native client: fullscreen mode',
      description:
        'borderless (the default) or exclusive. Exclusive switches the monitor to the mode below and gives the game the display outright — on some drivers that is the only way to a compositor-free present or a refresh rate the desktop is not running at — at the cost of a mode switch and a black screen at each end.',
      type: 'string',
      default: 'borderless',
    },
    {
      key: 'hassault.video.monitor',
      title: 'Native client: monitor',
      description:
        '0 for whichever monitor the window is on, or 1, 2, … for the system’s list. A monitor that is no longer connected falls back to the current one.',
      type: 'number',
      default: 0,
    },
    {
      key: 'hassault.video.exclusiveMode',
      title: 'Native client: exclusive mode',
      description:
        'WIDTHxHEIGHT@MILLIHERTZ, as the in-game menu writes it (e.g. 2560x1440@143998), or empty for the monitor’s desktop mode. Only read in exclusive fullscreen.',
      type: 'string',
      default: '',
    },
    {
      key: 'hassault.video.renderScale',
      title: 'Native client: resolution scale',
      description:
        'Fraction of the window the world is rendered at, 0.5–2. The HUD is never scaled with it, so text stays crisp at any setting. Below 1 trades sharpness for frame rate on an integrated GPU (pair it with sharpening); above 1 is supersampling, the only anti-aliasing that also smooths shader and alpha edges, at four times the pixels at 2.',
      type: 'number',
      default: 1,
    },
    {
      key: 'hassault.video.quality',
      title: 'Native client: graphics quality',
      description:
        'low, medium, high or ultra — a preset, not a ceiling. It sets the shading and fog reach itself, and writes the rows under it: anti-aliasing, texture filtering and quality, shadow quality, map lights, bloom and the sky. A row changed afterwards is honoured and the menu shows CUSTOM: the preset is applied when you pick it, never re-applied on top of a later choice. High is the browser’s picture; ultra adds bloom, every map light, 4096 shadows, 512 textures and 8× MSAA where the GPU offers it.',
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
      key: 'hassault.video.msaa',
      title: 'Native client: anti-aliasing (MSAA)',
      description:
        '1 (off), 2, 4 or 8 samples. 1 and 4 are the only counts the spec guarantees; 2 and 8 are used only where the GPU reports them, and anything it cannot do is snapped down to the nearest it can — asked for, never forced, because an unsupported count is a crash on the first frame rather than a slower one.',
      type: 'number',
      default: 1,
    },
    {
      key: 'hassault.video.antialias',
      title: 'Native client: anti-aliasing (legacy)',
      description:
        'The old on/off row, kept in step with the sample count above (on means more than 1) and read only when that row has never been saved.',
      type: 'boolean',
      default: false,
    },
    {
      key: 'hassault.video.anisotropy',
      title: 'Native client: texture filtering',
      description:
        '1, 2, 4, 8 or 16× anisotropic filtering on the modelled maps’ surfaces — what keeps a floor sharp at the grazing angle a shooter nearly always sees it from. Nearly free on a discrete GPU.',
      type: 'number',
      default: 16,
    },
    {
      key: 'hassault.video.textureQuality',
      title: 'Native client: texture quality',
      description:
        'low, medium or high: the generated surface textures at 128, 256 or 512 pixels, with a full mip chain. Built on the CPU when the map loads, so it costs load time and video memory, never frame rate.',
      type: 'string',
      default: 'medium',
    },
    {
      key: 'hassault.video.shadows',
      title: 'Native client: shadows',
      description:
        'Whether world surfaces sample the sun’s shadow map. This is a look, not a frame rate: the map and the sun are both static, so the shadow map is baked once at load and turning this off skips no pass — only the shader’s filter taps.',
      type: 'boolean',
      default: true,
    },
    {
      key: 'hassault.video.shadowQuality',
      title: 'Native client: shadow quality',
      description:
        'low (1024, 4 taps), medium (2048, 8), high (2048, 16 — the browser’s), ultra (4096, 16) or extreme (8192, 16). The map is rendered into it once, at load, so resolution costs memory and a moment rather than frames; the taps are the per-pixel cost.',
      type: 'string',
      default: 'high',
    },
    {
      key: 'hassault.video.mapLights',
      title: 'Native client: map lights',
      description:
        '0, 8, 16, 32 or 64 — how many of the map’s own lamps light a frame, nearest first. They light the operators and the weapon in your hands as well as the walls.',
      type: 'number',
      default: 8,
    },
    {
      key: 'hassault.video.bloom',
      title: 'Native client: bloom',
      description:
        '0 (off) to 1. A glow on what is already near white — lamps, screens, the sun, a muzzle flash — not a haze over the scene.',
      type: 'number',
      default: 0,
    },
    {
      key: 'hassault.video.sky',
      title: 'Native client: sky',
      description:
        'The sky dome and sun disc on maps open to the sky, from the map’s own atmosphere. Off draws the flat horizon colour instead.',
      type: 'boolean',
      default: true,
    },
    {
      key: 'hassault.video.sharpen',
      title: 'Native client: sharpening',
      description:
        '0 (off) to 1, contrast-adaptive. Gives back the edge contrast a resolution scale below 100% takes away, without ringing on detail that is already sharp.',
      type: 'number',
      default: 0,
    },
    {
      key: 'hassault.video.brightness',
      title: 'Native client: brightness',
      description:
        '0.6–1.6, a multiplier on the map’s own exposure. 1 is the picture the map was lit for.',
      type: 'number',
      default: 1,
    },
    {
      key: 'hassault.video.frameLatency',
      title: 'Native client: frame queue',
      description:
        'Frames the GPU may queue, 1–3. 1 is the lowest input latency and the default; 2 or 3 smooths frame pacing on a GPU running close to its limit, at a frame of lag each.',
      type: 'number',
      default: 1,
    },
    {
      key: 'hassault.video.adapter',
      title: 'Native client: GPU',
      description:
        'auto, or a GPU’s name as `hassault-native --list-adapters` prints it. On a hybrid laptop the client also asks NVIDIA’s and AMD’s drivers for the discrete GPU, so auto is usually right. Applies the next time the client starts; the in-game menu can restart it.',
      type: 'string',
      default: 'auto',
    },
    {
      key: 'hassault.video.backend',
      title: 'Native client: graphics API',
      description:
        'auto, dx12, vulkan, metal or gl. auto picks DirectX 12 or Vulkan on Windows, Vulkan on Linux and Metal on macOS, and never falls back to OpenGL on its own. Applies on restart.',
      type: 'string',
      default: 'auto',
    },
    {
      key: 'hassault.video.powerPreference',
      title: 'Native client: GPU preference',
      description:
        'high (performance) or low (power saving), used when the GPU is auto. Applies on restart.',
      type: 'string',
      default: 'high',
    },
    {
      key: 'hassault.video.hudScale',
      title: 'Native client: HUD scale',
      description:
        '0.75–1.5 of the HUD’s derived size. The HUD’s unit is derived from the window height in whole pixels, so 1440p and 4K can land on the same size as 1080p; this multiplies it.',
      type: 'number',
      default: 1,
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
        'On by default, which is what the client has always done when the row was unset. Off presents immediately — the lowest latency there is, and the reason the native client exists — at the cost of tearing.',
      type: 'boolean',
      default: true,
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
      // Both clients read this row, like the hitboxes one above.
      key: 'hassault.debug.grenadeArc',
      title: 'Show grenade trajectory',
      description:
        'Draw the predicted flight path while a grenade is out. Off by default, and only drawn in Training and in a match you host yourself — never in ranked or on someone else’s server.',
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
  ],
};

export { lobbyVoice } from './lobby-voice';
export { listMaps, getMapCubes, getMapInfo, type MapSummary, type MapInfo } from './api';
export { formatBytes } from './boot';
export { DEFAULT_CONTROLS, keyLabel } from './controls';
