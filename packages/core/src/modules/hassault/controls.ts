/**
 * The game's own keyboard map — the keys that only mean anything while you are
 * playing.
 *
 * Deliberately **not** keymap bindings (`packages/core/src/keymap`). That module
 * resolves a chord to a *command*: something that happens once, dispatched by the
 * shell. Movement is the other shape entirely — `forward` is a key being *held*,
 * sampled by the render loop sixty times a second — and a command per key per
 * frame is not a thing worth building. So the game keeps a small table of its
 * own, and being separate is what makes it *game-scoped by construction*: the
 * only code that ever consults this is the panel's pointer-locked key handler, so
 * a binding here cannot fire anywhere else in the app no matter what it is bound
 * to. The shell's own bindings are already suppressed while the pane holds
 * capture (see `keymap/capture.ts`), so the two tables cannot collide either.
 *
 * Codes are `KeyboardEvent.code` — physical keys, not the letters printed on
 * them. WASD is a *shape*, and on AZERTY `key` would move it to ZQSD while `code`
 * keeps it where the player's fingers are.
 *
 * Pure and free of React and three, so all of it is unit-tested headless.
 */

/** Everything the game can be told to do with the keyboard. */
export type GameAction =
  | 'forward'
  | 'back'
  | 'left'
  | 'right'
  | 'jump'
  | 'crouch'
  | 'reload'
  | 'inspect'
  | 'scores'
  | 'noclip'
  | 'weapon1'
  | 'weapon2'
  | 'weapon3'
  | 'weapon4'
  | 'weapon5'
  | 'nadeHe'
  | 'nadeFlash'
  | 'nadeSmoke'
  | 'nadeMolotov'
  | 'throw'
  | 'lob';

/** Two keys per action: the one you expect, and the one somebody else expects. */
export type Bindings = Record<GameAction, string[]>;

export interface ActionDoc {
  action: GameAction;
  label: string;
  group: 'Movement' | 'Combat' | 'Utility' | 'View';
  /** Shown where an action needs a caveat the label can't carry. */
  note?: string;
}

/**
 * The actions, in the order the menu lists them.
 *
 * Firing and weapon cycling are absent on purpose: they are the mouse (button and
 * wheel), and offering a keyboard row that cannot be bound to a mouse button
 * would be a lie about what this table controls.
 */
export const ACTIONS: readonly ActionDoc[] = [
  { action: 'forward', label: 'Move forward', group: 'Movement' },
  { action: 'back', label: 'Move back', group: 'Movement' },
  { action: 'left', label: 'Strafe left', group: 'Movement' },
  { action: 'right', label: 'Strafe right', group: 'Movement' },
  {
    action: 'jump',
    label: 'Jump',
    group: 'Movement',
    note: 'Hold it. Landing and jumping again within a quarter second while strafing is 25% faster.',
  },
  {
    action: 'crouch',
    label: 'Crouch',
    group: 'Movement',
    note: 'Silent, shorter, and steadier — for 40% of your speed. Crouching in mid-air costs nothing.',
  },
  { action: 'reload', label: 'Reload', group: 'Combat' },
  {
    action: 'inspect',
    label: 'Inspect Weapon (Ogre-Twitch)',
    group: 'Combat',
    note: 'Admire your weapon skin finish in first-person and spectator view.',
  },
  { action: 'weapon1', label: 'Weapon 1', group: 'Combat' },
  { action: 'weapon2', label: 'Weapon 2', group: 'Combat' },
  { action: 'weapon3', label: 'Weapon 3', group: 'Combat' },
  { action: 'weapon4', label: 'Weapon 4', group: 'Combat' },
  { action: 'weapon5', label: 'Weapon 5', group: 'Combat' },
  {
    action: 'nadeHe',
    label: 'Select HE Grenade',
    group: 'Utility',
    note: 'Selecting only readies it. Throw is a separate key, so you can pick one and choose the moment.',
  },
  { action: 'nadeFlash', label: 'Select Flashbang', group: 'Utility' },
  { action: 'nadeSmoke', label: 'Select Smoke', group: 'Utility' },
  { action: 'nadeMolotov', label: 'Select Incendiary', group: 'Utility' },
  {
    action: 'throw',
    label: 'Throw',
    group: 'Utility',
    note: 'A full throw, where you are looking.',
  },
  {
    action: 'lob',
    label: 'Underhand throw',
    group: 'Utility',
    note: 'Short. This is how a smoke goes down at your own feet rather than across the room.',
  },
  { action: 'scores', label: 'Scoreboard (hold)', group: 'View' },
  {
    action: 'noclip',
    label: 'Noclip',
    group: 'View',
    note: 'Offline only — the server has no such move.',
  },
];

export const DEFAULT_CONTROLS: Bindings = {
  forward: ['KeyW', 'ArrowUp'],
  back: ['KeyS', 'ArrowDown'],
  left: ['KeyA', 'ArrowLeft'],
  right: ['KeyD', 'ArrowRight'],
  jump: ['Space'],
  crouch: ['ControlLeft', 'KeyC'],
  reload: ['KeyR'],
  inspect: ['KeyF'],
  scores: ['Tab'],
  noclip: ['KeyV'],
  weapon1: ['Digit1'],
  weapon2: ['Digit2'],
  weapon3: ['Digit3'],
  weapon4: ['Digit4'],
  weapon5: ['Digit5'],
  // The four sit on the number row after the weapons, which is where a hand
  // already is. `KeyG` throws because that is where every shooter since
  // Half-Life has put it, and the underhand shares it with a modifier-free key
  // of its own rather than being Shift+G — a throw you have to hold two keys for
  // is one you will fumble under fire.
  nadeHe: ['Digit6'],
  nadeFlash: ['Digit7'],
  nadeSmoke: ['Digit8'],
  nadeMolotov: ['Digit9'],
  throw: ['KeyG'],
  lob: ['KeyH'],
};

/**
 * Grenade ids in slot order, matching `grenades.GRENADES` on the server.
 *
 * The *order* has to match — the wire carries a slot index, not a name — which
 * is exactly the kind of thing that drifts silently. It is asserted against the
 * served `/tacticals` list at runtime rather than trusted, so a reordering shows
 * up as a warning instead of as a smoke that turns out to be an HE.
 */
export const NADE_ACTIONS: readonly { action: GameAction; id: string }[] = [
  { action: 'nadeHe', id: 'he' },
  { action: 'nadeFlash', id: 'flash' },
  { action: 'nadeSmoke', id: 'smoke' },
  { action: 'nadeMolotov', id: 'molotov' },
];

/** How many keys one action can hold. */
export const SLOTS = 2;

/**
 * Keys the game refuses to bind.
 *
 * `Escape` is the menu — the one key that has to keep working when everything
 * else has been rebound to nonsense, or there is no way back to this table. The
 * others are the browser's, and are documented as such in `keymap/reserved.ts`:
 * binding them would produce a control that silently never fires.
 */
export const RESERVED_CODES = new Set(['Escape', 'F5', 'F11', 'F12']);

export const ALL_ACTIONS: readonly GameAction[] = ACTIONS.map((a) => a.action);

function isAction(value: string): value is GameAction {
  return (ALL_ACTIONS as readonly string[]).includes(value);
}

/** A fresh, independently-mutable copy of the shipped defaults. */
export function defaultControls(): Bindings {
  return Object.fromEntries(
    ALL_ACTIONS.map((action) => [action, [...DEFAULT_CONTROLS[action]]]),
  ) as Bindings;
}

/**
 * Read the stored map.
 *
 * Stored as a **diff** against the defaults, and merged over them here, so a
 * later release that adds an action (or moves a default) reaches players who
 * customized something else instead of leaving them with a table frozen at the
 * shape it had the day they touched it.
 *
 * Anything unrecognisable is dropped rather than thrown: this is persisted user
 * data, and a single bad entry must not cost someone their whole control setup.
 */
export function parseControls(raw: unknown): Bindings {
  const out = defaultControls();
  if (typeof raw !== 'string' || raw.trim() === '') return out;
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return out;
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return out;
  for (const [key, value] of Object.entries(parsed as Record<string, unknown>)) {
    if (!isAction(key) || !Array.isArray(value)) continue;
    const codes = value
      .filter((c): c is string => typeof c === 'string' && c !== '' && !RESERVED_CODES.has(c))
      .slice(0, SLOTS);
    out[key] = codes;
  }
  return out;
}

/** Serialize for storage: only what differs from the defaults. */
export function serializeControls(bindings: Bindings): string {
  const diff: Partial<Record<GameAction, string[]>> = {};
  for (const action of ALL_ACTIONS) {
    const mine = bindings[action] ?? [];
    const shipped = DEFAULT_CONTROLS[action];
    if (mine.length !== shipped.length || mine.some((c, i) => c !== shipped[i])) {
      diff[action] = [...mine];
    }
  }
  return JSON.stringify(diff);
}

export function isDefaultControls(bindings: Bindings): boolean {
  return serializeControls(bindings) === '{}';
}

/**
 * Code → action, which is the direction the key handler needs.
 *
 * Built once per change rather than searched per keystroke, and it is also what
 * decides whether the game `preventDefault`s a key at all: a code that is in here
 * is the game's, and everything else belongs to the browser.
 */
export function codeMap(bindings: Bindings): Map<string, GameAction> {
  const map = new Map<string, GameAction>();
  for (const action of ALL_ACTIONS) {
    for (const code of bindings[action] ?? []) {
      // First binding wins, so a duplicate left over from hand-edited storage is
      // deterministic rather than dependent on action order changing later.
      if (!map.has(code)) map.set(code, action);
    }
  }
  return map;
}

/**
 * Bind `code` to `action`'s slot, or clear the slot with `null`.
 *
 * Taking a code that another action holds **removes it from that other action**.
 * The alternative is two actions on one key, where pressing it does both things —
 * a state the player can reach in two clicks and cannot see, so it is resolved
 * here instead of being displayed as a warning nobody reads.
 */
export function setBinding(
  bindings: Bindings,
  action: GameAction,
  slot: number,
  code: string | null,
): Bindings {
  if (code !== null && RESERVED_CODES.has(code)) return bindings;
  const next = defaultControls();
  for (const a of ALL_ACTIONS) next[a] = [...(bindings[a] ?? [])];

  if (code !== null) {
    for (const a of ALL_ACTIONS) {
      next[a] = next[a].filter((c, i) => c !== code || (a === action && i === slot));
    }
  }

  const slots: (string | null)[] = [];
  for (let i = 0; i < SLOTS; i++) slots.push(next[action][i] ?? null);
  slots[slot] = code;
  // Compacted, so clearing the primary promotes the alternate rather than
  // leaving a hole the next rebind would silently fill in the wrong slot.
  next[action] = slots.filter((c): c is string => c !== null);
  return next;
}

/** Which action already holds `code`, if any — for the "was bound to" hint. */
export function boundTo(bindings: Bindings, code: string): GameAction | null {
  return codeMap(bindings).get(code) ?? null;
}

const NAMED_KEYS: Record<string, string> = {
  Space: 'Space',
  Tab: 'Tab',
  ArrowUp: '↑',
  ArrowDown: '↓',
  ArrowLeft: '←',
  ArrowRight: '→',
  ShiftLeft: 'L Shift',
  ShiftRight: 'R Shift',
  ControlLeft: 'L Ctrl',
  ControlRight: 'R Ctrl',
  AltLeft: 'L Alt',
  AltRight: 'R Alt',
  Enter: 'Enter',
  Backspace: 'Backspace',
  CapsLock: 'Caps',
  Backquote: '`',
  Minus: '−',
  Equal: '=',
  BracketLeft: '[',
  BracketRight: ']',
  Backslash: '\\',
  Semicolon: ';',
  Quote: "'",
  Comma: ',',
  Period: '.',
  Slash: '/',
};

/** A `code` as something worth printing on a key cap. */
export function keyLabel(code: string): string {
  if (NAMED_KEYS[code]) return NAMED_KEYS[code];
  if (code.startsWith('Key')) return code.slice(3);
  if (code.startsWith('Digit')) return code.slice(5);
  if (code.startsWith('Numpad')) return `Num ${code.slice(6)}`;
  return code;
}

/** The controls line the "click to play" hint shows, in the player's own keys. */
export function describeControls(bindings: Bindings): string {
  const first = (action: GameAction) => keyLabel(bindings[action]?.[0] ?? '—');
  const move = (['forward', 'left', 'back', 'right'] as const).map(first).join('');
  return `${move} move · mouse look · ${first('jump')} jump · ${first('crouch')} crouch`;
}
