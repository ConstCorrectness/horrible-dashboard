/**
 * Automatic background update checks.
 *
 * The manual "Check now" button in settings is the wrong shape for how updates
 * actually reach people: nobody opens a settings page to ask whether their app
 * is old. Every shipping desktop app checks on its own — on launch and then on a
 * timer — and *surfaces* the answer rather than waiting to be asked. This is
 * that layer, and it is deliberately nothing but scheduling and nagging policy:
 * it calls the same `checkForUpdate` the button does, and installing still goes
 * through the same signature-verified `updater_install`.
 *
 * Three decisions worth keeping:
 *
 * **The launch check is delayed.** Boot is already contending for the network
 * (settings, workspace layout, the model provider, the peer fabric) and an
 * update is never urgent to the second. `LAUNCH_DELAY_MS` keeps it out of that
 * window.
 *
 * **The interval is measured from the last check, not from process start.** A
 * desktop app is left running for weeks and also restarted twenty times an
 * afternoon; keying off process start would mean the first kind never checks
 * twice and the second kind checks on every launch. `lastCheckedAt` is persisted
 * so both converge on "about every six hours".
 *
 * **There is no silent-install mode.** `auto` as a policy value would have to
 * mean download-and-restart, because that is what the installer does — the
 * process is replaced. Restarting an app somebody is working in, to deliver
 * something that was not urgent enough to interrupt them for, is worse than
 * being a version behind. So the policy is `never` or `notify`, and the restart
 * is always a click the user made.
 */
import { checkForUpdate, updatesSupported, type UpdateInfo } from './api';

/** How long after boot the first check runs. */
export const LAUNCH_DELAY_MS = 30_000;

/** How long between checks, measured from the last completed one. */
export const CHECK_INTERVAL_MS = 6 * 60 * 60 * 1000;

/** How often the timer wakes to *consider* checking. */
const TICK_MS = 5 * 60 * 1000;

const LAST_CHECK_KEY = 'horrible.updates.lastCheckedAt';
const NOTIFIED_KEY = 'horrible.updates.notifiedVersion';

export type AutoUpdatePolicy = 'never' | 'notify';

/** Reads that must not throw: a disabled/full localStorage is not fatal here. */
function read(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* private mode, quota — the check simply repeats sooner than it needed to */
  }
}

/** When the last completed check happened, or 0 if there has never been one. */
export function lastCheckedAt(): number {
  const raw = read(LAST_CHECK_KEY);
  const parsed = raw === null ? NaN : Number(raw);
  return Number.isFinite(parsed) ? parsed : 0;
}

/**
 * Whether enough time has passed to check again.
 *
 * A `lastCheckedAt` in the future (the clock was wrong, or moved) would
 * otherwise park the app for however long that skew is, so it is treated as
 * "check now" rather than trusted.
 */
export function isCheckDue(now: number, last: number, interval = CHECK_INTERVAL_MS): boolean {
  if (last <= 0 || last > now) return true;
  return now - last >= interval;
}

/**
 * Whether this result is worth putting on screen.
 *
 * A failed check is never surfaced by the *background* path. It is a real and
 * distinct state — and the settings section reports it as such, because a user
 * who asked deserves the truth — but an offline laptop generating a toast every
 * six hours teaches people to dismiss update notices without reading them,
 * which is the one habit this whole mechanism cannot afford.
 */
export function shouldNotify(info: UpdateInfo | null, alreadyNotified: string | null): boolean {
  if (!info || !info.available || info.error) return false;
  if (!info.version) return false;
  return info.version !== alreadyNotified;
}

export interface AutoUpdateOptions {
  /** Current policy. Re-read on every tick, so toggling the setting takes effect. */
  policy: () => AutoUpdatePolicy;
  /** Current channel. Also re-read, for the same reason. */
  channel: () => string;
  /** Called at most once per version, when something newer is available. */
  onUpdate: (info: UpdateInfo) => void;
  /** Injectable for tests. */
  now?: () => number;
  check?: (channel: string) => Promise<UpdateInfo | null>;
}

/**
 * Start the background checker. Returns a disposer.
 *
 * A no-op where updates cannot be installed at all (the browser layout), rather
 * than a checker that would find a desktop release the page cannot apply.
 */
export function startAutoUpdateChecks(opts: AutoUpdateOptions): () => void {
  if (!updatesSupported()) return () => {};

  const now = opts.now ?? (() => Date.now());
  const check = opts.check ?? checkForUpdate;
  let stopped = false;
  let inFlight = false;
  // The floor the timer also respects, so a tick landing inside the launch
  // window cannot check ahead of the delayed launch check itself.
  const earliest = now() + LAUNCH_DELAY_MS;

  const tick = async (): Promise<void> => {
    if (stopped || inFlight) return;
    if (opts.policy() === 'never') return;
    const at = now();
    if (at < earliest) return;
    if (!isCheckDue(at, lastCheckedAt())) return;

    inFlight = true;
    try {
      const info = await check(opts.channel());
      if (stopped) return;
      // Stamped on completion, not on start: a check that never returned has
      // not happened, and stamping first would silence retries for six hours.
      write(LAST_CHECK_KEY, String(now()));
      if (shouldNotify(info, read(NOTIFIED_KEY)) && info?.version) {
        write(NOTIFIED_KEY, info.version);
        opts.onUpdate(info);
      }
    } catch {
      // Same reasoning as the plumbing below it: a background check that cannot
      // reach the network is not an event the user needs. It retries.
    } finally {
      inFlight = false;
    }
  };

  const timer = setInterval(() => void tick(), TICK_MS);
  const launch = setTimeout(() => void tick(), LAUNCH_DELAY_MS);

  return () => {
    stopped = true;
    clearInterval(timer);
    clearTimeout(launch);
  };
}
