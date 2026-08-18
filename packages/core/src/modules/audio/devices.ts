/**
 * Device enumeration, and the one piece of logic that keeps a saved routing
 * working across sessions.
 *
 * Kept free of the audio graph on purpose: this is pure functions over
 * `MediaDeviceInfo`, so it is unit-testable without a DOM or a Web Audio
 * implementation, and it is where the two device gotchas live.
 */

/** Names that mean "this device is a virtual cable, not a real speaker". */
const VIRTUAL_TOKENS = [
  'voicemeeter',
  'vb-audio',
  'cable',
  'blackhole',
  'soundflower',
  'loopback',
  'virtual',
  'aggregate',
  'multi-output',
  'null sink',
  'pulseaudio',
  'pipewire',
];

/**
 * Whether a device is a virtual cable — i.e. whether sending audio to it means
 * some other application can hear it.
 *
 * A name match, which is crude, and deliberately used for **labelling only**.
 * No routing decision depends on this: a false positive would otherwise silently
 * change where audio goes, and there is no cross-platform API that answers the
 * question properly.
 */
export function isVirtualDevice(label: string): boolean {
  const lower = label.toLowerCase();
  return VIRTUAL_TOKENS.some((token) => lower.includes(token));
}

/**
 * Find the device to actually use for a saved `(id, label)` pair.
 *
 * **Why this is not just `deviceId`.** A `MediaDeviceInfo.deviceId` is a hash
 * scoped to the browser origin and the user's permission grant. It survives a
 * reboot, but it is regenerated when site data is cleared, when the user revokes
 * and re-grants microphone permission, and (in some builds) across a profile
 * change. When that happens, every saved id names nothing at all, and a mixer
 * that trusted ids alone would silently fall back to the default device on every
 * bus at once — a routing the user built, gone, with no error.
 *
 * So the label is a second key. The id is tried first because it is exact and
 * two devices can legitimately share a label (two identical USB headsets); the
 * label is the fallback that survives an id rotation.
 *
 * Returns `''` for "use the system default", which is also what an empty saved
 * id means and what a device that has genuinely been unplugged falls back to.
 */
export function resolveDeviceId(
  savedId: string,
  savedLabel: string,
  devices: MediaDeviceInfo[],
): string {
  if (!savedId && !savedLabel) return '';
  if (savedId && devices.some((d) => d.deviceId === savedId)) return savedId;
  if (savedLabel) {
    const byLabel = devices.find((d) => d.label === savedLabel);
    if (byLabel) return byLabel.deviceId;
  }
  return '';
}

/**
 * Whether the browser will tell us device *labels* yet.
 *
 * Until a media permission has been granted, `enumerateDevices()` returns the
 * right number of entries with empty labels and placeholder ids. That is not a
 * failure and not an empty list — it is the browser refusing to fingerprint the
 * machine for a page that has never asked for a microphone. A device picker
 * rendered in this state shows a list of blanks, so the UI has to prompt first.
 */
export function hasDeviceLabels(devices: MediaDeviceInfo[]): boolean {
  return devices.some((d) => d.label !== '');
}

/** Audio outputs the browser will let us target. */
export async function listOutputs(): Promise<MediaDeviceInfo[]> {
  if (!navigator.mediaDevices?.enumerateDevices) return [];
  const all = await navigator.mediaDevices.enumerateDevices();
  return all.filter((d) => d.kind === 'audiooutput');
}

/** Audio inputs (microphones). */
export async function listInputs(): Promise<MediaDeviceInfo[]> {
  if (!navigator.mediaDevices?.enumerateDevices) return [];
  const all = await navigator.mediaDevices.enumerateDevices();
  return all.filter((d) => d.kind === 'audioinput');
}

/**
 * Ask for microphone access, purely to unlock device labels.
 *
 * The track is stopped immediately — this is not the microphone the mixer uses,
 * it is the permission prompt. Leaving it open would light the recording
 * indicator for a user who only opened a settings page.
 */
export async function requestDeviceLabels(): Promise<boolean> {
  if (!navigator.mediaDevices?.getUserMedia) return false;
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    for (const track of stream.getTracks()) track.stop();
    return true;
  } catch {
    return false;
  }
}

/**
 * Whether this browser can send audio to a chosen output at all.
 *
 * `setSinkId` is Chromium-only (so: Chrome, Edge, and the WebView2/WKWebView
 * shells the desktop app runs in — but not Firefox). Without it there can be
 * exactly one output, the system default, and the mixer says so rather than
 * offering a device picker that does nothing.
 */
export function canChooseOutput(): boolean {
  return typeof HTMLMediaElement !== 'undefined' && 'setSinkId' in HTMLMediaElement.prototype;
}
