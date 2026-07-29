/**
 * Resolving a keystroke to a command.
 *
 * Pure and DOM-free so the whole precedence table is unit-testable. The shell's
 * keydown handler is a thin wrapper over `resolveKey`. See
 * docs/architecture/keybindings.mdx.
 *
 * Precedence, highest first:
 *
 *   0. the **capture gate** — a pane holding the keyboard filters the candidate
 *      set before anything else is considered
 *   1. `source: 'user'` beats a shipped default
 *   2. `override: true`
 *   3. the more specific satisfied `when` (naming `paneInstance` beats naming
 *      `paneFocus` beats naming nothing), `priority` as an explicit thumb
 *   4. registration order — earlier wins, preserving the old `.find()` behavior
 */
import { keysUsed, testWhen, type KeyContext } from './context';
import { matchesSpec, type Chord } from './spec';

/** A binding after defaults and user overrides have been merged. */
export interface ResolvedBinding {
  /** Canonical spec text, for display and for keying overrides. */
  key: string;
  chord: Chord;
  command: string;
  when?: string;
  override?: boolean;
  /** Explicit thumb on the scale when two clauses are equally specific. */
  priority?: number;
  /**
   * Survives the capture gate regardless of `when` — for the few shell verbs
   * that must stay reachable while a pane owns the keyboard.
   */
  capturePassthrough?: boolean;
  /** Registered with the OS so it fires while the app is unfocused (desktop). */
  global?: boolean;
  source: 'default' | 'user';
  /** Position in the merged list; lower wins ties. */
  order: number;
}

export type KeyResolution =
  | { kind: 'command'; command: string; binding: ResolvedBinding }
  /** The stroke began a longer sequence — hold it and wait for the next one. */
  | { kind: 'pending'; candidates: ResolvedBinding[] }
  | { kind: 'none' };

/**
 * Is a binding suppressed because a pane holds the keyboard?
 *
 * The three modes differ only in how much they take:
 *   - `pointer` takes the mouse, nothing keyboard-side is suppressed;
 *   - `keyboard` suppresses **unmodified** bindings only — exactly the old
 *     `editor: true` behavior, so `mod+s` still saves while you type;
 *   - `full` suppresses everything that isn't the capturing pane's own.
 *
 * A binding belongs to the capturing pane when its `when` names `paneFocus` or
 * `captureView` and evaluates true — which is only possible for that pane, since
 * capture is released the moment focus leaves it.
 */
export function isSuppressedByCapture(binding: ResolvedBinding, ctx: KeyContext): boolean {
  if (!ctx.capture || ctx.capture === 'pointer') return false;
  if (binding.capturePassthrough) return false;

  const first = binding.chord[0];
  const modified = first.mod || first.ctrl || first.meta || first.alt;

  if (ctx.capture === 'keyboard') {
    // The pane is being typed into, so NO bare key may resolve — not even one
    // scoped to this very pane. The frame synthesizes a `t`/`n`/`b` region
    // toggle scoped to every view with regions, so exempting "the capturing
    // pane's own bindings" here would let `t` toggle the outline of the editor
    // you are typing `t` into. Modified chords are untouched, which is how
    // `mod+s` still saves from inside a buffer.
    return !modified;
  }

  // `full`: only the capturing pane's own bindings survive. A binding belongs to
  // it when its condition names the focused/capturing pane and holds — which is
  // only possible for that pane, since capture is released when focus moves.
  if (binding.when) {
    const used = keysUsed(binding.when);
    if (used.has('paneFocus') || used.has('captureView') || used.has('paneInstance')) return false;
  }
  return true;
}

/** Candidates whose `when` holds and which capture hasn't swallowed. */
function eligible(bindings: readonly ResolvedBinding[], ctx: KeyContext): ResolvedBinding[] {
  return bindings.filter((b) => testWhen(b.when, ctx) && !isSuppressedByCapture(b, ctx));
}

/** How targeted a binding's condition is. Higher is more specific. */
function specificity(binding: ResolvedBinding): number {
  let score = (binding.priority ?? 0) * 10;
  if (!binding.when) return score;
  const used = keysUsed(binding.when);
  if (used.has('paneInstance')) score += 4;
  if (used.has('paneFocus') || used.has('captureView')) score += 2;
  if (used.has('capture')) score += 1;
  return score;
}

/** The precedence comparator. Negative means `a` wins. */
function compare(a: ResolvedBinding, b: ResolvedBinding): number {
  const bySource = Number(b.source === 'user') - Number(a.source === 'user');
  if (bySource !== 0) return bySource;
  const byOverride = Number(!!b.override) - Number(!!a.override);
  if (byOverride !== 0) return byOverride;
  const bySpecificity = specificity(b) - specificity(a);
  if (bySpecificity !== 0) return bySpecificity;
  return a.order - b.order;
}

/** The winner among eligible candidates, or null. */
export function pickBinding(
  candidates: readonly ResolvedBinding[],
  ctx: KeyContext,
): ResolvedBinding | null {
  const usable = eligible(candidates, ctx);
  if (usable.length === 0) return null;
  return [...usable].sort(compare)[0];
}

/**
 * Resolve a keydown.
 *
 * `pending` is the prefix of a sequence already typed (empty for a fresh
 * stroke). A binding matches when every stroke of `pending` matched and the new
 * event matches the next one; if that completes the chord it fires, otherwise
 * the caller holds the prefix and waits.
 */
export function resolveKey(
  e: KeyboardEvent,
  ctx: KeyContext,
  bindings: readonly ResolvedBinding[],
  pending: readonly KeyboardEvent[] = [],
): KeyResolution {
  const depth = pending.length;
  const matching = bindings.filter((b) => {
    if (b.chord.length <= depth) return false;
    for (let i = 0; i < depth; i++) {
      if (!matchesSpec(pending[i], b.chord[i])) return false;
    }
    return matchesSpec(e, b.chord[depth]);
  });
  if (matching.length === 0) return { kind: 'none' };

  const complete = matching.filter((b) => b.chord.length === depth + 1);
  const longer = eligible(
    matching.filter((b) => b.chord.length > depth + 1),
    ctx,
  );

  const winner = pickBinding(complete, ctx);
  // A completed binding fires immediately; an unfinished longer chord only holds
  // the keyboard when nothing shorter already claimed the stroke.
  if (winner) return { kind: 'command', command: winner.command, binding: winner };
  if (longer.length > 0) return { kind: 'pending', candidates: longer };
  return { kind: 'none' };
}

/**
 * Every binding for a command, best first — the ones that would actually fire in
 * the current context ahead of the ones that wouldn't, so a label shows a live
 * shortcut when there is one. Powers the palette's labels and `keymap.describe`.
 */
export function bindingsFor(
  command: string,
  bindings: readonly ResolvedBinding[],
  ctx: KeyContext,
): ResolvedBinding[] {
  const mine = bindings.filter((b) => b.command === command);
  const live = new Set(eligible(mine, ctx));
  return mine.sort((a, b) => {
    const byLive = Number(live.has(b)) - Number(live.has(a));
    return byLive !== 0 ? byLive : compare(a, b);
  });
}

/** Why a binding did not win, for `keymap.describe` and the Shortcuts UI. */
export type LossReason =
  | { reason: 'active' }
  | { reason: 'when-false'; when: string }
  | { reason: 'captured'; by: string | null }
  | { reason: 'shadowed'; by: ResolvedBinding };

export function explainBinding(
  binding: ResolvedBinding,
  bindings: readonly ResolvedBinding[],
  ctx: KeyContext,
): LossReason {
  if (binding.when && !testWhen(binding.when, ctx)) {
    return { reason: 'when-false', when: binding.when };
  }
  if (isSuppressedByCapture(binding, ctx)) {
    return { reason: 'captured', by: ctx.captureView };
  }
  const rivals = bindings.filter(
    (b) => b !== binding && b.chord.length === binding.chord.length && b.key === binding.key,
  );
  const winner = pickBinding([binding, ...rivals], ctx);
  if (winner && winner !== binding) return { reason: 'shadowed', by: winner };
  return { reason: 'active' };
}
