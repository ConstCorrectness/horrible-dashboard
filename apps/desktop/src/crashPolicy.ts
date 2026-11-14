/**
 * What to do when the UI's renderer process dies.
 *
 * The shell used to reload once and, if the page crashed again within ten seconds,
 * do nothing at all: the window stayed open, dead and frameless, with no way to
 * tell a crash from a freeze — and the crash reason was never written anywhere.
 * Now every crash is logged with its reason and exit code, a crash is retried with
 * a growing delay, and a page that keeps crashing gets an error page that says so
 * and offers a reload, instead of a blank window.
 *
 * No `electron` import, so the policy is testable in plain Node.
 */

/** Crashes older than this no longer count against the page. */
export const CRASH_WINDOW_MS = 60_000;
/** This many crashes inside the window and the shell stops reloading. */
export const MAX_CRASHES = 4;
/** Wait before each successive reload: at once, then 2 s, then 5 s. */
const RELOAD_DELAYS_MS = [0, 2_000, 5_000];

export type CrashDecision = { action: 'reload'; delayMs: number } | { action: 'give-up' };

export class CrashPolicy {
  private crashes: number[] = [];

  /** Record a crash at `now` and say what to do about it. */
  onCrash(now: number): CrashDecision {
    this.crashes = this.crashes.filter((t) => now - t < CRASH_WINDOW_MS);
    this.crashes.push(now);
    const n = this.crashes.length;
    if (n >= MAX_CRASHES) return { action: 'give-up' };
    return {
      action: 'reload',
      delayMs: RELOAD_DELAYS_MS[Math.min(n - 1, RELOAD_DELAYS_MS.length - 1)],
    };
  }

  /** Forget the history, after the user reloads by hand. */
  reset(): void {
    this.crashes = [];
  }
}

/** One line for the terminal and the log. */
export function describeCrash(details: { reason: string; exitCode: number }): string {
  return `renderer process gone: reason=${details.reason} exitCode=${details.exitCode}`;
}

/** The page shown once the shell stops reloading. `reloadUrl` is the UI's own URL. */
export function crashPageHtml(
  details: { reason: string; exitCode: number },
  reloadUrl: string,
): string {
  const esc = (s: string) => s.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
  return `<!doctype html><meta charset="utf-8"><title>horrible-dashboard stopped</title>
<body style="margin:0;background:#111;color:#ddd;font:12.5px Geist,'Segoe UI',system-ui,sans-serif;display:grid;place-items:center;height:100vh">
<div style="max-width:32rem;padding:1.5rem;border-top:2px solid #e5484d;background:#1a1a1a">
<h1 style="font-size:19px;font-weight:700;line-height:1.2;letter-spacing:.14em;text-transform:uppercase;margin:0 0 .75rem">The window kept crashing</h1>
<p style="margin:0 0 1rem;color:#aaa">The UI's process stopped ${MAX_CRASHES} times in a minute, so it was not restarted again.</p>
<p style="margin:0 0 1rem;font-family:ui-monospace,monospace;color:#999">reason: ${esc(details.reason)} · exit code: ${details.exitCode}</p>
<a href="${esc(reloadUrl)}" style="color:#fff">Reload the app</a>
</div></body>`;
}
