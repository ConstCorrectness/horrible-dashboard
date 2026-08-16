/**
 * Spotlight: one surface for "ask, run, or open".
 *
 * `mod+k` used to open a flat filter over `registry.commands`. It is now the
 * single place you type anything into — commands, panes you already have open,
 * and the agent — because the alternative was a second always-available overlay
 * for the agent, and two overlays on one keyboard is a choice the user has to
 * make before they can start typing.
 *
 * Resolution is pure over the registry + a frame snapshot so it can be
 * unit-tested; the rendering half is packages/ui/src/Spotlight.tsx.
 */
import { registry } from './registry';
import { taskbarEntries } from './layout/taskbar';
import type { FrameState } from './layout/types';

export type SpotlightKind = 'command' | 'pane' | 'agent';

/** What running an item means. The caller dispatches; this module never acts. */
export type SpotlightAction =
  | { type: 'command'; commandId: string }
  | { type: 'focusPane'; instanceId: string }
  | { type: 'ask'; prompt: string };

export interface SpotlightItem {
  /** Unique within one result list. */
  key: string;
  kind: SpotlightKind;
  title: string;
  /** Right-aligned hint: a shortcut, a pane's state, the agent's invitation. */
  hint?: string;
  icon?: string;
  action: SpotlightAction;
}

/** An item with its match score attached, used only while sorting. */
interface Scored {
  item: SpotlightItem;
  score: number;
}

/**
 * Explicit source filters, so a user who knows what they want can say so.
 *
 * `?` is the agent's, and it is the only one that is also the **fallback** — a
 * query matching nothing still offers to ask, because "I typed a sentence and
 * nothing happened" is the failure mode a command palette has always had.
 */
const PREFIXES: Record<string, SpotlightKind> = {
  '>': 'command',
  '@': 'pane',
  '?': 'agent',
};

/**
 * How far every command is pushed down the list relative to an open pane.
 *
 * Larger than any score `rank` can return, so the two groups never interleave:
 * typing "terminal" almost always means the terminal you already have, not the
 * command that would open a second one.
 */
const COMMAND_PENALTY = 1000;

export interface SpotlightQuery {
  /** The text to match on, with any prefix removed. */
  text: string;
  /** Restrict to one source, or null for everything. */
  only: SpotlightKind | null;
}

export function parseSpotlightQuery(raw: string): SpotlightQuery {
  const prefix = raw[0];
  if (prefix && prefix in PREFIXES) {
    return { text: raw.slice(1).trimStart(), only: PREFIXES[prefix] };
  }
  return { text: raw.trim(), only: null };
}

/**
 * The result list for `raw`, best first.
 *
 * `shortcutFor` is supplied by the caller rather than resolved here: the live
 * binding for a command depends on the key context, which is a UI concern this
 * module deliberately does not reach into.
 */
export function spotlightResults(
  raw: string,
  frame: FrameState,
  shortcutFor?: (commandId: string) => string | null,
): SpotlightItem[] {
  const { text, only } = parseSpotlightQuery(raw);
  const q = text.toLowerCase();
  const scored: Scored[] = [];

  if (only === null || only === 'pane') {
    for (const e of taskbarEntries(frame)) {
      const score = rank(e.title, e.viewId, q);
      if (score === null) continue;
      scored.push({
        score,
        item: {
          key: `pane:${e.instanceId}`,
          kind: 'pane',
          title: e.title,
          hint: e.state === 'minimized' ? 'minimized' : e.state === 'hidden' ? 'open' : 'showing',
          icon: e.icon,
          action: { type: 'focusPane', instanceId: e.instanceId },
        },
      });
    }
  }

  if (only === null || only === 'command') {
    for (const c of registry.commands) {
      const score = rank(c.title, c.id, q);
      if (score === null) continue;
      scored.push({
        score: score + COMMAND_PENALTY,
        item: {
          key: `cmd:${c.id}`,
          kind: 'command',
          title: c.title,
          hint: shortcutFor?.(c.id) ?? undefined,
          action: { type: 'command', commandId: c.id },
        },
      });
    }
  }

  // Stable within a score: `sort` is stable in every engine we target, so equal
  // scores keep registry order rather than shuffling as the user types.
  const items = scored.sort((a, b) => a.score - b.score).map((s) => s.item);

  // The agent is offered whenever there is text: last when other things matched
  // (they are more likely what was meant), first when nothing did — at which
  // point it is the only thing that can help.
  if (text && only !== 'command' && only !== 'pane') {
    const ask: SpotlightItem = {
      key: 'agent:ask',
      kind: 'agent',
      title: text,
      hint: 'Ask the agent',
      icon: '✦',
      action: { type: 'ask', prompt: text },
    };
    return items.length ? [...items, ask] : [ask];
  }
  return items;
}

/**
 * Match score, or null for no match. Lower is better.
 *
 * Three tiers rather than a fuzzy matcher: a prefix match on the title beats a
 * substring of it, which beats a match that only appears in the id. The id is
 * searchable because `pane.open:terminal.instance` is how someone who knows the
 * system searches, but an id-only hit is never what a plain word meant.
 */
function rank(title: string, id: string, q: string): number | null {
  if (!q) return 100;
  const t = title.toLowerCase();
  if (t.startsWith(q)) return 0;
  const at = t.indexOf(q);
  if (at >= 0) return 10 + at;
  return id.toLowerCase().includes(q) ? 500 : null;
}
