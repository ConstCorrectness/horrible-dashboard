/**
 * The two Chromium profiles the shell runs, and what each may do.
 *
 * - **The app session** (the default session) holds the app's own page. It gets the
 *   microphone, camera and screen capture without asking — lobby voice, the Plaza,
 *   Clubhouse and the share module need them, and it is the user's own app — but
 *   only for the app's own origin.
 * - **The browser session** (`persist:browser`) holds every embedded browser pane and
 *   pop-out window: arbitrary sites. Separate cookies and storage from the app, a
 *   plain Chrome user agent (sign-in pages and bot walls refuse one that says
 *   "Electron"), and every sensitive permission is asked of the user, per site, the
 *   way a browser does — so an open tab can never listen to the room unannounced.
 */
import { app, desktopCapturer, dialog, session, type Session, type WebPreferences } from 'electron';

export const BROWSER_PARTITION = 'persist:browser';

/** Hardened preferences for every surface that shows a foreign page. */
export const WEB_PREFERENCES_BROWSER: WebPreferences = {
  sandbox: true,
  contextIsolation: true,
  nodeIntegration: false,
  webSecurity: true,
  // A background tab must not burn CPU on timers; the pane's own visibility logic
  // already hides surfaces that are off-screen.
  backgroundThrottling: true,
};

let browser: Session | null = null;

/** The embedded-browser session, configured on first use. */
export function browserSession(): Session {
  if (browser) return browser;
  const s = session.fromPartition(BROWSER_PARTITION);
  s.setUserAgent(plainChromeUserAgent(app.userAgentFallback));
  installBrowserPermissions(s);
  browser = s;
  return s;
}

/**
 * The user agent with Electron's and the app's product tokens removed — what Chrome
 * itself would send. Google sign-in, Cloudflare and most bot walls treat an
 * "Electron/x" token as an embedded browser and refuse it.
 */
export function plainChromeUserAgent(ua: string): string {
  return ua
    .split(' ')
    .filter((token) => !/^(Electron|horrible-dashboard|@horrible[^/]*)\//i.test(token))
    .join(' ');
}

/** Permissions whose grant is a privacy decision a site must ask the user for. */
const ASK: ReadonlySet<string> = new Set([
  'media',
  'geolocation',
  'notifications',
  'midi',
  'midiSysex',
  'display-capture',
  'idle-detection',
  'speaker-selection',
  'window-management',
]);

/** Harmless, or already gated by a user gesture inside the page. */
const ALLOW: ReadonlySet<string> = new Set([
  'clipboard-sanitized-write',
  'fullscreen',
  'pointerLock',
  'keyboardLock',
]);

function describe(permission: string, mediaTypes?: string[]): string {
  if (permission === 'media') {
    const wants = mediaTypes ?? [];
    if (wants.includes('video') && wants.includes('audio')) return 'use your camera and microphone';
    if (wants.includes('video')) return 'use your camera';
    return 'use your microphone';
  }
  const names: Record<string, string> = {
    geolocation: 'know your location',
    notifications: 'show notifications',
    midi: 'use your MIDI devices',
    midiSysex: 'use your MIDI devices',
    'display-capture': 'capture your screen',
    'idle-detection': 'know when you are idle',
    'speaker-selection': 'choose your audio output',
    'window-management': 'manage windows on your displays',
  };
  return names[permission] ?? permission;
}

function installBrowserPermissions(s: Session): void {
  // Decisions last for the session, keyed by site and permission — the same as a
  // browser's "allow this time", without a settings page to manage.
  const decided = new Map<string, boolean>();
  s.setPermissionCheckHandler((_wc, permission, origin) => {
    if (ALLOW.has(permission)) return true;
    return decided.get(`${origin}|${permission}`) === true;
  });
  s.setPermissionRequestHandler((wc, permission, callback, details) => {
    if (ALLOW.has(permission)) return callback(true);
    if (!ASK.has(permission)) return callback(false);
    let origin = details.requestingUrl;
    try {
      origin = new URL(details.requestingUrl).origin;
    } catch {
      // Keep the raw URL in the prompt.
    }
    const mediaTypes = 'mediaTypes' in details ? (details.mediaTypes as string[]) : undefined;
    const key = `${origin}|${permission}|${(mediaTypes ?? []).join(',')}`;
    const known = decided.get(key);
    if (known !== undefined) return callback(known);
    void dialog
      .showMessageBox({
        type: 'question',
        buttons: ['Allow', 'Block'],
        defaultId: 1,
        cancelId: 1,
        title: 'Permission request',
        message: `${origin} wants to ${describe(permission, mediaTypes)}.`,
        noLink: true,
      })
      .then(({ response }) => {
        const allowed = response === 0 && !wc.isDestroyed();
        decided.set(key, allowed);
        decided.set(`${origin}|${permission}`, allowed);
        callback(allowed);
      })
      .catch(() => callback(false));
  });
  // A site's getDisplayMedia is answered like the app's own (below), but only after
  // the user allowed that site to capture the screen.
  s.setDisplayMediaRequestHandler(
    (request, callback) => {
      const origin = request.securityOrigin;
      void dialog
        .showMessageBox({
          type: 'question',
          buttons: ['Share screen', 'Cancel'],
          defaultId: 1,
          cancelId: 1,
          title: 'Screen sharing',
          message: `${origin} wants to capture your screen.`,
          noLink: true,
        })
        .then(async ({ response }) => {
          if (response !== 0) return callback(null);
          await answerDisplayMedia(request.audioRequested, callback);
        })
        .catch(() => callback(null));
    },
    { useSystemPicker: true },
  );
}

/** How a display-media request is answered; `null` refuses it. */
type DisplayMediaCallback = (streams: Electron.Streams | null) => void;

/**
 * Hand `getDisplayMedia` the primary screen (plus system audio on Windows, the one
 * platform Chromium can loop it back on).
 *
 * Electron has no screen picker of its own: without a handler `getDisplayMedia`
 * simply rejects. On macOS 15+ `useSystemPicker` shows the OS picker instead and
 * this never runs; on Wayland the desktop portal's picker is shown by
 * `getSources` itself.
 */
async function answerDisplayMedia(
  audioRequested: boolean,
  callback: DisplayMediaCallback,
): Promise<void> {
  try {
    const [screen] = await desktopCapturer.getSources({ types: ['screen'] });
    if (!screen) return callback(null);
    callback({
      video: screen,
      ...(audioRequested && process.platform === 'win32' ? { audio: 'loopback' as const } : {}),
    });
  } catch {
    callback(null);
  }
}

/**
 * The app session: media and screen capture for the app's own origin only.
 *
 * `isAppOrigin` is main.ts's answer to "is this the app's page" — `app://horrible`
 * when packaged, the Vite dev server in development.
 */
export function configureAppSession(isAppOrigin: (url: string) => boolean): void {
  const s = session.defaultSession;
  s.setPermissionCheckHandler((_wc, _permission, origin) => isAppOrigin(origin));
  s.setPermissionRequestHandler((_wc, _permission, callback, details) => {
    callback(isAppOrigin(details.requestingUrl));
  });
  s.setDisplayMediaRequestHandler(
    (request, callback) => {
      if (!isAppOrigin(request.securityOrigin)) return callback(null);
      void answerDisplayMedia(request.audioRequested, callback);
    },
    { useSystemPicker: true },
  );
}
