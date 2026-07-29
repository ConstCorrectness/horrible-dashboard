/**
 * Key **specs**: parsing, matching and display formatting.
 *
 * A spec is one or more `+`-separated chords separated by spaces — `mod+k`,
 * `alt+shift+left`, `mod+k mod+s` (a two-stroke sequence). Modifier tokens are
 * `mod` (ctrl or cmd), `ctrl`, `meta`, `alt`, `shift`; the last token is the key.
 *
 * A key token carries one of **two identities**, and the spec chooses:
 *
 *   - bare (`w`, `escape`, `left`) — the *character* the key produces, compared
 *     against `e.key`. What every existing binding uses.
 *   - `code:` prefixed (`code:KeyW`) — the *physical position*, compared against
 *     `e.code`. Survives non-US layouts, which is what game movement needs.
 *
 * One promotion happens automatically: a digit or ASCII-punctuation key combined
 * with `shift` resolves positionally, because `e.key` reports the **shifted
 * glyph** (`shift+1` arrives as `!`), so a character-identity spec for it could
 * never match. See docs/architecture/keybindings.mdx.
 */

/** Modifiers match EXACTLY: a spec that omits `alt` rejects an alt-chord. */
export interface KeySpec {
  /** ctrl-or-cmd. Mutually exclusive with explicit `ctrl`/`meta`. */
  mod: boolean;
  ctrl: boolean;
  meta: boolean;
  alt: boolean;
  shift: boolean;
  /** Which event field `value` is compared against. */
  kind: 'key' | 'code';
  /** Lowercased `e.key` for `kind: 'key'`, exact `e.code` for `kind: 'code'`. */
  value: string;
}

/** A binding's trigger: one stroke, or a sequence the user types in order. */
export type Chord = KeySpec[];

/** `e.key` aliases so specs can say `alt+left` / `ctrl+space` naturally. */
const KEY_ALIASES: Record<string, string> = {
  left: 'arrowleft',
  right: 'arrowright',
  up: 'arrowup',
  down: 'arrowdown',
  space: ' ',
  esc: 'escape',
};

/** Reverse of KEY_ALIASES, for rendering a spec back to its friendly spelling. */
const KEY_LABELS: Record<string, string> = {
  arrowleft: '←',
  arrowright: '→',
  arrowup: '↑',
  arrowdown: '↓',
  ' ': 'Space',
  escape: 'Esc',
  enter: 'Enter',
  tab: 'Tab',
  backspace: 'Backspace',
  delete: 'Delete',
};

/**
 * Digits and ASCII punctuation on a US layout. These are the keys whose `e.key`
 * changes under shift, so a `shift+<one of these>` spec has to match positionally
 * or it can never fire.
 */
const SHIFT_UNSTABLE = /^[0-9`\-=[\]\\;',./]$/;

/** `e.code` for the shift-unstable keys, so the promotion has somewhere to go. */
const CODE_FOR_KEY: Record<string, string> = {
  '0': 'Digit0',
  '1': 'Digit1',
  '2': 'Digit2',
  '3': 'Digit3',
  '4': 'Digit4',
  '5': 'Digit5',
  '6': 'Digit6',
  '7': 'Digit7',
  '8': 'Digit8',
  '9': 'Digit9',
  '`': 'Backquote',
  '-': 'Minus',
  '=': 'Equal',
  '[': 'BracketLeft',
  ']': 'BracketRight',
  '\\': 'Backslash',
  ';': 'Semicolon',
  "'": 'Quote',
  ',': 'Comma',
  '.': 'Period',
  '/': 'Slash',
};

/** The character a shift-unstable `e.code` produces unshifted, for labels. */
const KEY_FOR_CODE: Record<string, string> = Object.fromEntries(
  Object.entries(CODE_FOR_KEY).map(([key, code]) => [code, key]),
);

export class KeySpecError extends Error {}

/** Parse one `+`-separated stroke. Throws `KeySpecError` on a malformed spec. */
function parseStroke(stroke: string): KeySpec {
  // A trailing `+` means the key IS `+` (`mod++`, or a bare `+`). Peel it off
  // before splitting, or the split leaves empty tokens that read as modifiers.
  const plusKey = stroke.endsWith('+');
  const tokens = (plusKey ? stroke.slice(0, -1) : stroke).split('+');
  const plain = plusKey ? '+' : (tokens.pop() ?? '');
  // Slicing the key off leaves the separator's empty tail behind; drop it so it
  // isn't read as a modifier.
  if (plusKey) tokens.pop();
  if (plain === '') throw new KeySpecError(`Empty key spec: "${stroke}"`);

  const spec: KeySpec = {
    mod: false,
    ctrl: false,
    meta: false,
    alt: false,
    shift: false,
    kind: 'key',
    value: '',
  };
  for (const raw of tokens) {
    switch (raw.toLowerCase()) {
      case 'mod':
        spec.mod = true;
        break;
      case 'ctrl':
      case 'control':
        spec.ctrl = true;
        break;
      case 'meta':
      case 'cmd':
      case 'command':
      case 'super':
        spec.meta = true;
        break;
      case 'alt':
      case 'option':
        spec.alt = true;
        break;
      case 'shift':
        spec.shift = true;
        break;
      default:
        throw new KeySpecError(`Unknown modifier "${raw}" in "${stroke}"`);
    }
  }
  if (spec.mod && (spec.ctrl || spec.meta)) {
    throw new KeySpecError(`"mod" already means ctrl-or-cmd, drop the explicit one: "${stroke}"`);
  }

  if (/^code:/i.test(plain)) {
    spec.kind = 'code';
    spec.value = plain.slice('code:'.length);
    if (!spec.value) throw new KeySpecError(`Empty code in "${stroke}"`);
    return spec;
  }

  const lowered = plain.toLowerCase();
  const key = KEY_ALIASES[lowered] ?? lowered;
  // The promotion: shift + a key whose character changes under shift.
  if (spec.shift && SHIFT_UNSTABLE.test(key) && CODE_FOR_KEY[key]) {
    spec.kind = 'code';
    spec.value = CODE_FOR_KEY[key];
    return spec;
  }
  spec.kind = 'key';
  spec.value = key;
  return spec;
}

/**
 * Parse a full spec into its chord sequence. Strokes are space-separated, so
 * `mod+k mod+s` is two strokes and `ctrl+space` is one (the key is spelled
 * `space`, never a literal blank).
 */
export function parseSpec(spec: string): Chord {
  const strokes = spec.trim().split(/\s+/).filter(Boolean);
  if (strokes.length === 0) throw new KeySpecError('Empty key spec');
  return strokes.map(parseStroke);
}

/** Parse, returning null instead of throwing — for user input and stored maps. */
export function tryParseSpec(spec: string): Chord | null {
  try {
    return parseSpec(spec);
  } catch {
    return null;
  }
}

/** Modifier keys never resolve a binding on their own. */
const MODIFIER_KEYS = new Set(['control', 'shift', 'alt', 'meta', 'os', 'altgraph']);

export function isModifierEvent(e: KeyboardEvent): boolean {
  return MODIFIER_KEYS.has(e.key.toLowerCase());
}

/** Does a keyboard event match a single parsed stroke? */
export function matchesSpec(e: KeyboardEvent, spec: KeySpec): boolean {
  if (spec.mod) {
    if (!e.ctrlKey && !e.metaKey) return false;
  } else {
    if (e.ctrlKey !== spec.ctrl) return false;
    if (e.metaKey !== spec.meta) return false;
  }
  if (e.altKey !== spec.alt) return false;
  if (e.shiftKey !== spec.shift) return false;

  return spec.kind === 'code' ? e.code === spec.value : e.key.toLowerCase() === spec.value;
}

/**
 * Does an event match a key spec string? Kept as the single-stroke convenience
 * the old `matchesKeySpec` provided; multi-stroke specs match their first stroke
 * only (sequence tracking is the resolver's job).
 */
export function matchesKeySpec(e: KeyboardEvent, key: string): boolean {
  const chord = tryParseSpec(key);
  return !!chord && matchesSpec(e, chord[0]);
}

/** Serialize a parsed spec back to canonical text (round-trips through parse). */
export function formatSpec(chord: Chord): string {
  return chord.map(formatStroke).join(' ');
}

function formatStroke(spec: KeySpec): string {
  const parts: string[] = [];
  if (spec.mod) parts.push('mod');
  if (spec.ctrl) parts.push('ctrl');
  if (spec.meta) parts.push('meta');
  if (spec.alt) parts.push('alt');
  if (spec.shift) parts.push('shift');
  // The space key's `e.key` is a literal ' ', which cannot be written into a
  // spec — strokes are space-separated, so `ctrl+ ` re-parses as `ctrl++`. Emit
  // the alias, or a stored override for ctrl+space corrupts on reload.
  const key =
    spec.kind === 'code' ? `code:${spec.value}` : spec.value === ' ' ? 'space' : spec.value;
  parts.push(key);
  return parts.join('+');
}

export type KeyPlatform = 'mac' | 'win' | 'linux';

export interface LabelOptions {
  platform: KeyPlatform;
  /**
   * `navigator.keyboard.getLayoutMap()` result, so a `code:` spec is labelled
   * with what is actually printed on this user's keyboard. Optional — without it
   * a `code:` spec falls back to its US-layout character.
   */
  layoutMap?: ReadonlyMap<string, string>;
}

/** Human-facing label — `⌘K` on mac, `Ctrl+K` elsewhere. */
export function labelSpec(chord: Chord, opts: LabelOptions): string {
  return chord.map((s) => labelStroke(s, opts)).join(' ');
}

function labelStroke(spec: KeySpec, opts: LabelOptions): string {
  const mac = opts.platform === 'mac';
  const parts: string[] = [];
  if (spec.mod) parts.push(mac ? '⌘' : 'Ctrl');
  if (spec.ctrl) parts.push(mac ? '⌃' : 'Ctrl');
  if (spec.meta && !spec.mod) parts.push(mac ? '⌘' : 'Win');
  if (spec.alt) parts.push(mac ? '⌥' : 'Alt');
  if (spec.shift) parts.push(mac ? '⇧' : 'Shift');
  parts.push(labelKey(spec, opts));
  return mac ? parts.join('') : parts.join('+');
}

function labelKey(spec: KeySpec, opts: LabelOptions): string {
  if (spec.kind === 'code') {
    const printed = opts.layoutMap?.get(spec.value);
    if (printed) return printed.toUpperCase();
    if (KEY_FOR_CODE[spec.value]) return KEY_FOR_CODE[spec.value];
    // `KeyW` → `W`, `Digit1` → `1`, `ArrowLeft` → `←`.
    const stripped = spec.value.replace(/^(Key|Digit)/, '');
    return KEY_LABELS[stripped.toLowerCase()] ?? stripped.toUpperCase();
  }
  return KEY_LABELS[spec.value] ?? spec.value.toUpperCase();
}

/**
 * Build both spec spellings for a pressed key, so a "record a shortcut" UI can
 * offer the character form and the positional form of the same keystroke.
 * Returns `null` for a bare modifier press.
 */
export function specsFromEvent(e: KeyboardEvent): { key: string; code: string } | null {
  if (isModifierEvent(e)) return null;
  const mods: string[] = [];
  if (e.ctrlKey && e.metaKey) {
    mods.push('ctrl', 'meta');
  } else if (e.ctrlKey || e.metaKey) {
    mods.push('mod');
  }
  if (e.altKey) mods.push('alt');
  if (e.shiftKey) mods.push('shift');

  const lowered = e.key.toLowerCase();
  const friendly = Object.entries(KEY_ALIASES).find(([, full]) => full === lowered)?.[0] ?? lowered;
  return {
    key: [...mods, friendly].join('+'),
    code: [...mods, `code:${e.code}`].join('+'),
  };
}
