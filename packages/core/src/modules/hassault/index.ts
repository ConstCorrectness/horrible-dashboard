import { registry, type ModuleManifest } from '../../registry';
import { registerNotificationAction } from '../notifications';
import { HorribleAssaultPanel } from './HorribleAssaultPanel';
import { requestJoin } from './invite-notify';

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
    const invite = item.data.invite as { room?: string; map?: string; host?: string } | undefined;
    if (!invite?.room) return;
    registry.openPanel('hassault.play');
    requestJoin({ room: invite.room, map: invite.map ?? '', host: invite.host ?? '' });
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
      icon: '🔫',
      singleton: true,
    },
  ],
  commands: [
    {
      id: 'hassault.open',
      title: 'HorribleAssault: Open',
      run: () => registry.openPanel('hassault.play'),
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
        'low, medium or high. Moves the shading (flat, a directional wash, the wash plus a rim), how far the fog reaches, and the multisample count — 1× below high, 4× at it, because those are the only counts every GPU is required to support.',
      type: 'string',
      default: 'medium',
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
        'Path to the native client executable. Blank looks in the usual build outputs under apps/native-fps — build it with `cargo build --release --manifest-path apps/native-fps/Cargo.toml`.',
      type: 'string',
      default: '',
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
