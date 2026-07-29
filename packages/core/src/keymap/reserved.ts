/**
 * Chords the **host** eats before our keydown handler ever runs.
 *
 * This is the difference between a shortcut that works and one that silently
 * does nothing. `mod+1..9` (workspace switch) is the live example: Chrome's tab
 * switching is not cancellable, so in the browser layout those bindings have
 * never fired — and nothing told anyone. The table below is data, consumed by
 * the Shortcuts UI (a live badge), a dev-mode boot check, and the `keymap.*`
 * agent tools so the agent refuses to hand the user a dead key.
 *
 * `preventable: true` means the host *would* act but `preventDefault()` stops it
 * (browser back on `alt+left`); `false` means the keystroke never reaches the
 * page at all. Only the latter is truly unusable. See
 * docs/architecture/keybindings.mdx.
 */
import type { KeyContext } from './context';
import { type Chord, type KeySpec, type KeyPlatform, tryParseSpec } from './spec';

export type KeyHost = 'browser' | 'desktop';

export interface ReservedEntry {
  /** Key spec in the ordinary grammar; `mod` resolves per platform. */
  key: string;
  /** Hosts this applies to. Omit for all. */
  hosts?: KeyHost[];
  /** Platforms this applies to. Omit for all. */
  platforms?: KeyPlatform[];
  /** Who takes it, shown verbatim to the user. */
  owner: string;
  /** Can `preventDefault()` reclaim it? */
  preventable: boolean;
}

const digits = (n: number) => Array.from({ length: n }, (_, i) => String(i + 1));

export const RESERVED: ReservedEntry[] = [
  // --- Browser: tab and window management (never reaches the page) -----------
  ...digits(9).map(
    (d): ReservedEntry => ({
      key: `mod+${d}`,
      hosts: ['browser'],
      owner: 'Browser (switch to tab)',
      preventable: false,
    }),
  ),
  { key: 'mod+w', hosts: ['browser'], owner: 'Browser (close tab)', preventable: false },
  { key: 'mod+t', hosts: ['browser'], owner: 'Browser (new tab)', preventable: false },
  { key: 'mod+n', hosts: ['browser'], owner: 'Browser (new window)', preventable: false },
  {
    key: 'mod+shift+w',
    hosts: ['browser'],
    owner: 'Browser (close window)',
    preventable: false,
  },
  {
    key: 'mod+shift+t',
    hosts: ['browser'],
    owner: 'Browser (reopen closed tab)',
    preventable: false,
  },
  {
    key: 'mod+shift+n',
    hosts: ['browser'],
    owner: 'Browser (incognito window)',
    preventable: false,
  },
  { key: 'ctrl+tab', hosts: ['browser'], owner: 'Browser (next tab)', preventable: false },
  {
    key: 'ctrl+shift+tab',
    hosts: ['browser'],
    owner: 'Browser (previous tab)',
    preventable: false,
  },

  // --- Browser: function keys ----------------------------------------------
  { key: 'f3', hosts: ['browser'], owner: 'Browser (find next)', preventable: false },
  { key: 'f5', hosts: ['browser'], owner: 'Browser (reload)', preventable: false },
  { key: 'f6', hosts: ['browser'], owner: 'Browser (focus address bar)', preventable: false },
  { key: 'f11', hosts: ['browser'], owner: 'Browser (fullscreen)', preventable: false },
  { key: 'f12', hosts: ['browser'], owner: 'Browser (developer tools)', preventable: false },

  // --- Browser: preventable, but we'd be fighting a strong habit -------------
  {
    key: 'alt+left',
    hosts: ['browser'],
    platforms: ['win', 'linux'],
    owner: 'Browser (back)',
    preventable: true,
  },
  {
    key: 'alt+right',
    hosts: ['browser'],
    platforms: ['win', 'linux'],
    owner: 'Browser (forward)',
    preventable: true,
  },
  { key: 'mod+p', hosts: ['browser'], owner: 'Browser (print)', preventable: true },
  { key: 'mod+s', hosts: ['browser'], owner: 'Browser (save page)', preventable: true },
  { key: 'mod+f', hosts: ['browser'], owner: 'Browser (find in page)', preventable: true },

  // --- OS level: no host escapes these -------------------------------------
  { key: 'meta+q', platforms: ['mac'], owner: 'macOS (quit app)', preventable: false },
  { key: 'meta+m', platforms: ['mac'], owner: 'macOS (minimize)', preventable: false },
  { key: 'meta+h', platforms: ['mac'], owner: 'macOS (hide app)', preventable: false },
  { key: 'meta+space', platforms: ['mac'], owner: 'macOS (Spotlight)', preventable: false },
  {
    key: 'ctrl+space',
    platforms: ['mac'],
    owner: 'macOS (switch input source)',
    preventable: false,
  },
  {
    key: 'ctrl+space',
    platforms: ['win'],
    owner: 'Windows IME (toggle input method)',
    preventable: false,
  },
  { key: 'alt+f4', platforms: ['win', 'linux'], owner: 'OS (close window)', preventable: false },
  { key: 'alt+tab', platforms: ['win', 'linux'], owner: 'OS (switch window)', preventable: false },
];

/** `mod` is not a real modifier — resolve it before comparing two specs. */
function normalize(spec: KeySpec, platform: KeyPlatform): KeySpec {
  if (!spec.mod) return spec;
  return platform === 'mac'
    ? { ...spec, mod: false, meta: true }
    : { ...spec, mod: false, ctrl: true };
}

/**
 * A comparable key identity, so `mod+1` and `mod+code:Digit1` are recognized as
 * the same physical chord. `KeyW`/`Digit1` strip to `w`/`1`; anything else keeps
 * its own spelling.
 */
function canonicalKey(spec: KeySpec): string {
  if (spec.kind !== 'code') return spec.value;
  const stripped = spec.value.replace(/^(Key|Digit)/, '').toLowerCase();
  return stripped || spec.value.toLowerCase();
}

function sameStroke(a: KeySpec, b: KeySpec, platform: KeyPlatform): boolean {
  const x = normalize(a, platform);
  const y = normalize(b, platform);
  return (
    x.ctrl === y.ctrl &&
    x.meta === y.meta &&
    x.alt === y.alt &&
    x.shift === y.shift &&
    canonicalKey(x) === canonicalKey(y)
  );
}

export interface ReservedHit {
  owner: string;
  preventable: boolean;
}

/**
 * Is this chord claimed by the host? Only the **first** stroke can be stolen —
 * once a sequence has started we already own the keyboard. Returns the strongest
 * hit (an unpreventable one outranks a preventable one).
 */
export function checkReserved(
  chord: Chord,
  ctx: Pick<KeyContext, 'platform' | 'host'>,
): ReservedHit | null {
  const first = chord[0];
  if (!first) return null;
  let best: ReservedHit | null = null;
  for (const entry of RESERVED) {
    if (entry.hosts && !entry.hosts.includes(ctx.host)) continue;
    if (entry.platforms && !entry.platforms.includes(ctx.platform)) continue;
    const parsed = tryParseSpec(entry.key);
    if (!parsed || !sameStroke(first, parsed[0], ctx.platform)) continue;
    if (!best || (!entry.preventable && best.preventable)) {
      best = { owner: entry.owner, preventable: entry.preventable };
    }
  }
  return best;
}

/** Convenience for string specs (the Shortcuts UI and the agent tools). */
export function checkReservedSpec(
  spec: string,
  ctx: Pick<KeyContext, 'platform' | 'host'>,
): ReservedHit | null {
  const chord = tryParseSpec(spec);
  return chord ? checkReserved(chord, ctx) : null;
}
