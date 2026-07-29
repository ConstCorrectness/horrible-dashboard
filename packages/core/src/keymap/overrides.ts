/**
 * User keybinding customizations: load, edit, persist.
 *
 * Mirrors `settings.ts` — optimistic local write, then PUT — but against its own
 * `/api/keymap` document rather than the settings bag: a list of bindings is not
 * a `string | number | boolean`, and the settings bag is handed to the browser
 * whole on every boot. See docs/architecture/keybindings.mdx.
 */
import { apiDelete, apiGet, apiPut } from '../api';
import { getKeymapOverrides, setKeymapOverrides, type KeymapOverride } from './state';
import { formatSpec, tryParseSpec } from './spec';

interface KeymapDocument {
  schema?: string;
  version?: number;
  bindings: KeymapOverride[];
}

function sameBinding(a: KeymapOverride, b: KeymapOverride): boolean {
  return a.key === b.key && a.command === b.command && (a.when ?? null) === (b.when ?? null);
}

/** Load the stored keymap at boot. A backend outage leaves defaults in place. */
export async function loadKeymapOverrides(): Promise<void> {
  try {
    const doc = await apiGet<KeymapDocument>('/keymap');
    setKeymapOverrides(doc.bindings ?? []);
  } catch {
    setKeymapOverrides([]);
  }
}

async function persist(next: KeymapOverride[]): Promise<void> {
  setKeymapOverrides(next);
  await apiPut<KeymapDocument>('/keymap', { bindings: next });
}

/**
 * Bind `key` to `command`.
 *
 * `replaces` is the shipped default this is meant to take over from; it is
 * recorded as a `disabled` entry so the default stops resolving. Without it a
 * "rebind" would just add a second binding and leave the original live — which is
 * exactly the surprise a user reporting "I changed it but the old key still
 * works" would hit.
 */
export async function setKeybinding(input: {
  key: string;
  command: string;
  when?: string;
  replaces?: { key: string; command: string };
}): Promise<void> {
  const chord = tryParseSpec(input.key);
  if (!chord) throw new Error(`Not a valid key spec: "${input.key}"`);
  const entry: KeymapOverride = {
    key: formatSpec(chord),
    command: input.command,
    ...(input.when ? { when: input.when } : {}),
  };

  const next = getKeymapOverrides().filter((o) => !sameBinding(o, entry));
  if (input.replaces) {
    const replaced = tryParseSpec(input.replaces.key);
    const disabled: KeymapOverride = {
      key: replaced ? formatSpec(replaced) : input.replaces.key,
      command: input.replaces.command,
      disabled: true,
    };
    if (!next.some((o) => sameBinding(o, disabled) && o.disabled)) next.push(disabled);
  }
  next.push(entry);
  await persist(next);
}

/** Suppress a shipped default without putting anything in its place. */
export async function disableKeybinding(key: string, command: string): Promise<void> {
  const chord = tryParseSpec(key);
  const entry: KeymapOverride = {
    key: chord ? formatSpec(chord) : key,
    command,
    disabled: true,
  };
  const next = getKeymapOverrides().filter((o) => !sameBinding(o, entry));
  next.push(entry);
  await persist(next);
}

/**
 * Drop the user's customizations for one command, restoring its defaults. Removes
 * both halves of a rebind — the added binding and the `disabled` tombstone.
 */
export async function resetKeybindings(command: string): Promise<void> {
  await persist(getKeymapOverrides().filter((o) => o.command !== command));
}

/** Drop every customization. */
export async function resetAllKeybindings(): Promise<void> {
  setKeymapOverrides([]);
  await apiDelete<KeymapDocument>('/keymap');
}

/** Has the user customized anything for this command? */
export function isCommandCustomized(command: string): boolean {
  return getKeymapOverrides().some((o) => o.command === command);
}
