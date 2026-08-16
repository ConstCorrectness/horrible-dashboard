/**
 * The **live** side of the keymap: assembling the current `KeyContext`, and
 * merging declared defaults with user overrides into the resolved binding list.
 *
 * Both are memoized. The old service re-flatMapped every module's manifest and
 * re-synthesized the frame's region bindings on *every keystroke*; here the list
 * is rebuilt only when the registry, the overrides, or the host change.
 */
import { useSyncExternalStore } from 'react';

import { dialogsStore } from '../dialogs';
import { layoutStore } from '../layout/store';
import { findPaneAnywhere } from '../layout/model';
import { registry, type KeybindingDecl } from '../registry';
import { getCapture } from './capture';
import type { KeyContext } from './context';
import { checkReserved } from './reserved';
import { type ResolvedBinding } from './resolve';
import { tryParseSpec, formatSpec, type KeyPlatform } from './spec';

// ---------------------------------------------------------------------------
// Host + platform (fixed for the life of the page)
// ---------------------------------------------------------------------------

let platform: KeyPlatform = 'linux';
let host: 'browser' | 'desktop' = 'browser';

/** Called once by the app entry, alongside `initCapabilities`. */
export function initKeymapHost(next: {
  platform?: KeyPlatform;
  host?: 'browser' | 'desktop';
}): void {
  if (next.platform) platform = next.platform;
  if (next.host) host = next.host;
  invalidateBindings();
}

/** Best-effort platform sniff, used when the entry doesn't pass one explicitly. */
export function detectPlatform(): KeyPlatform {
  if (typeof navigator === 'undefined') return 'linux';
  const ua = `${navigator.userAgent} ${navigator.platform ?? ''}`.toLowerCase();
  if (ua.includes('mac')) return 'mac';
  if (ua.includes('win')) return 'win';
  return 'linux';
}

export function keyPlatform(): KeyPlatform {
  return platform;
}

export function keyHost(): 'browser' | 'desktop' {
  return host;
}

// ---------------------------------------------------------------------------
// The live context
// ---------------------------------------------------------------------------

/**
 * Is the event target a text-entry element? Plain-letter shortcuts must defer to
 * typing when one of these is focused.
 */
export function isEditableTarget(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el || !el.tagName) return false;
  const tag = el.tagName.toLowerCase();
  return tag === 'input' || tag === 'textarea' || tag === 'select' || el.isContentEditable;
}

let shellView: KeyContext['shellView'] = 'desktop';

/** The shell reports which top-level surface is showing. */
export function setShellView(view: KeyContext['shellView']): void {
  if (shellView === view) return;
  shellView = view;
  emitContext();
}

/**
 * Assemble the context. `textInput` is read from live DOM focus rather than
 * tracked, because focus can move without any store dispatch (Tab, autofocus).
 */
export function readKeyContext(): KeyContext {
  const frame = layoutStore.getSnapshot().frame;
  const focused = frame.focusedInstanceId ? findPaneAnywhere(frame, frame.focusedInstanceId) : null;
  const capture = getCapture();
  return {
    paneFocus: focused?.pane.viewId ?? null,
    paneInstance: focused?.pane.instanceId ?? null,
    capture: capture?.mode ?? null,
    captureView: capture?.viewId ?? null,
    textInput: typeof document !== 'undefined' && isEditableTarget(document.activeElement),
    dialogOpen: dialogsStore.getActive() !== null,
    // Either mechanism counts: a `when: fullscreenArea` binding means "something
    // is filling the screen", and a presented window is that just as much as a
    // fullscreened centre area is.
    fullscreenArea: frame.fullscreenAreaId !== null || frame.presentedInstanceId !== null,
    windowFocused: frame.focusedWindowId !== null,
    desktopMode: frame.mode,
    shellView,
    platform,
    host,
  };
}

let contextSnapshot: KeyContext | null = null;
const contextListeners = new Set<() => void>();

function sameContext(a: KeyContext, b: KeyContext): boolean {
  return (Object.keys(a) as (keyof KeyContext)[]).every((k) => a[k] === b[k]);
}

function emitContext(): void {
  const next = readKeyContext();
  if (contextSnapshot && sameContext(contextSnapshot, next)) return;
  contextSnapshot = next;
  for (const listener of contextListeners) listener();
}

export const keyContextStore = {
  subscribe(listener: () => void): () => void {
    if (contextListeners.size === 0) startWatching();
    contextListeners.add(listener);
    return () => {
      contextListeners.delete(listener);
      if (contextListeners.size === 0) stopWatching();
    };
  },
  getSnapshot(): KeyContext {
    if (!contextSnapshot) contextSnapshot = readKeyContext();
    return contextSnapshot;
  },
};

let unwatch: (() => void)[] = [];

function startWatching(): void {
  unwatch = [layoutStore.subscribe(emitContext), dialogsStore.subscribe(emitContext)];
  if (typeof document !== 'undefined') {
    // focusin/focusout are the only signal for `textInput` — DOM focus moves
    // without any store dispatch (Tab, autofocus, a pane focusing its editor).
    document.addEventListener('focusin', emitContext);
    document.addEventListener('focusout', emitContext);
    unwatch.push(() => {
      document.removeEventListener('focusin', emitContext);
      document.removeEventListener('focusout', emitContext);
    });
  }
}

function stopWatching(): void {
  for (const off of unwatch) off();
  unwatch = [];
}

export function useKeyContext(): KeyContext {
  return useSyncExternalStore(
    keyContextStore.subscribe,
    keyContextStore.getSnapshot,
    readKeyContext,
  );
}

// ---------------------------------------------------------------------------
// The resolved binding list
// ---------------------------------------------------------------------------

/** A user's stored customization. `disabled` suppresses a shipped default. */
export interface KeymapOverride {
  key: string;
  command: string;
  when?: string;
  /** Suppress the matching default rather than adding a binding. */
  disabled?: boolean;
}

let overrides: KeymapOverride[] = [];
/**
 * The active named preset's bindings (see keymap/presets.ts).
 *
 * Kept separate from the user's own overrides rather than merged into them: a
 * preset must not be *saved* as if the user had typed it, or switching back to
 * the default set would leave the i3 bindings behind with nothing to remove
 * them. It layers between the shipped defaults and the user's edits, so a hand
 * rebind still wins over the preset.
 */
let presetOverrides: KeymapOverride[] = [];
let bindingsSnapshot: ResolvedBinding[] | null = null;
const bindingListeners = new Set<() => void>();

export function invalidateBindings(): void {
  bindingsSnapshot = null;
  for (const listener of bindingListeners) listener();
}

/** Replace the user's overrides (called by the overrides store after load/edit). */
export function setKeymapOverrides(next: KeymapOverride[]): void {
  overrides = next;
  invalidateBindings();
}

export function getKeymapOverrides(): readonly KeymapOverride[] {
  return overrides;
}

/** Install a named preset's bindings. Called when `keymap.preset` changes. */
export function setKeymapPreset(bindings: KeymapOverride[]): void {
  presetOverrides = bindings;
  invalidateBindings();
}

/** Legacy `scope` is exactly `paneFocus == '<scope>'`; normalize it away here. */
function whenOf(decl: KeybindingDecl): string | undefined {
  if (decl.when) return decl.when;
  if (decl.scope) return `paneFocus == '${decl.scope}'`;
  return undefined;
}

/** A default whose host/platform filters exclude this build never resolves. */
function appliesHere(decl: KeybindingDecl): boolean {
  if (decl.hosts && !decl.hosts.includes(host)) return false;
  if (decl.platforms && !decl.platforms.includes(platform)) return false;
  return true;
}

function build(): ResolvedBinding[] {
  const out: ResolvedBinding[] = [];
  let order = 0;

  // A preset's `disabled` entries suppress shipped defaults exactly as a user's
  // do — that is how the i3 set takes `mod+k` away from the spotlight without a
  // special case anywhere in the resolver.
  const layered = [...presetOverrides, ...overrides];
  const suppressed = new Set(layered.filter((o) => o.disabled).map((o) => `${o.key} ${o.command}`));

  for (const decl of registry.keybindings) {
    if (!appliesHere(decl)) continue;
    const chord = tryParseSpec(decl.key);
    if (!chord) {
      console.warn(`[keymap] ignoring unparseable binding "${decl.key}" → ${decl.command}`);
      continue;
    }
    const key = formatSpec(chord);
    if (suppressed.has(`${key} ${decl.command}`)) continue;
    out.push({
      key,
      chord,
      command: decl.command,
      when: whenOf(decl),
      override: decl.override,
      priority: decl.priority,
      capturePassthrough: decl.capturePassthrough,
      global: decl.global,
      source: 'default',
      order: order++,
    });
  }

  // Preset first, then the user's own: `order` breaks ties in favour of whatever
  // came later, so a hand rebind beats the preset it was layered over.
  for (const o of layered) {
    if (o.disabled) continue;
    const chord = tryParseSpec(o.key);
    if (!chord) continue;
    out.push({
      key: formatSpec(chord),
      chord,
      command: o.command,
      when: o.when,
      source: 'user',
      order: order++,
    });
  }
  return out;
}

// Registering a module contributes bindings, so the cache has to drop then.
// Subscribed eagerly rather than on first `subscribe`, because the keydown
// handler reads `getKeymap()` without ever subscribing. The registry exposes
// `onChange` so the keymap never has to be imported back into it (a cycle).
registry.onChange(invalidateBindings);

export const keymapStore = {
  subscribe(listener: () => void): () => void {
    bindingListeners.add(listener);
    return () => {
      bindingListeners.delete(listener);
    };
  },
  getSnapshot(): ResolvedBinding[] {
    if (!bindingsSnapshot) bindingsSnapshot = build();
    return bindingsSnapshot;
  },
};

/** The resolved keymap right now. */
export function getKeymap(): ResolvedBinding[] {
  return keymapStore.getSnapshot();
}

/**
 * Every shipped default the host will never deliver on this build.
 *
 * `mod+1..9` sat in the frame's manifest for the life of the browser layout
 * without once firing, because Chrome's tab switching isn't cancellable and
 * nothing checked. The entry logs these in dev; the Shortcuts pane shows the same
 * check per row for the user.
 */
export function unreachableDefaults(): { binding: ResolvedBinding; owner: string }[] {
  const ctx = { platform, host };
  const out: { binding: ResolvedBinding; owner: string }[] = [];
  for (const binding of getKeymap()) {
    if (binding.source !== 'default') continue;
    const hit = checkReserved(binding.chord, ctx);
    if (hit && !hit.preventable) out.push({ binding, owner: hit.owner });
  }
  return out;
}

/** Dev-only: shout about bindings that can never fire on this host. */
export function auditKeymap(): void {
  const dead = unreachableDefaults();
  if (dead.length === 0) return;
  console.warn(
    `[keymap] ${dead.length} default binding(s) are unreachable on ${host}/${platform}:\n` +
      dead.map((d) => `  ${d.binding.key} → ${d.binding.command} (${d.owner})`).join('\n'),
  );
}

export function useKeymap(): ResolvedBinding[] {
  return useSyncExternalStore(keymapStore.subscribe, keymapStore.getSnapshot, getKeymap);
}
