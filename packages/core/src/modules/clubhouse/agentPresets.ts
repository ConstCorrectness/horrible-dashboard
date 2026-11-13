/**
 * Persona presets for the room agent.
 *
 * The built-ins ship with the app; the user's own live in `localStorage` under
 * `agentPresets`. Only the user's are stored: that key used to hold the built-ins
 * *as well* the first time anyone saved a preset, so a later change to the shipped
 * list never reached anyone who had saved one, and there was no way to tell which
 * entries were theirs. `loadPresets` reads both shapes.
 */

export interface PersonaPreset {
  name: string;
  prompt: string;
  /** Short label for the quick-pick chip. */
  chip?: string;
  /** Shipped with the app, so it cannot be deleted. */
  builtin?: boolean;
}

export const DEFAULT_PRESETS: PersonaPreset[] = [
  {
    name: 'Room Co-Host & Assistant',
    chip: 'Co-Host',
    builtin: true,
    prompt:
      'You are a friendly and proactive co-host in a live Clubhouse audio room. You answer questions concisely in 1-3 spoken sentences, engage with attendees naturally, and keep the discussion flowing.',
  },
  {
    name: 'Podcast & Interview Host',
    chip: 'Podcast Host',
    builtin: true,
    prompt:
      'You are an energetic podcast host. You ask insightful follow-up questions, introduce speakers warmly, and summarize key takeaways in a charismatic, broadcast-ready speaking style.',
  },
  {
    name: 'Debater & Critical Thinker',
    chip: 'Debater',
    builtin: true,
    prompt:
      'You are an intellectually sharp, respectful debate participant. You probe underlying assumptions, highlight alternative perspectives, and construct logical arguments.',
  },
  {
    name: 'Concise & Direct (No Fluff)',
    chip: 'Concise',
    builtin: true,
    prompt:
      'You are an ultra-concise assistant. You give immediate, direct answers in 1-2 punchy spoken sentences without fluff, preambles, or conversational filler.',
  },
  {
    name: 'Tech & Architecture Expert',
    chip: 'Tech Expert',
    builtin: true,
    prompt:
      'You are a senior software architect and tech expert. You explain complex technical concepts, system tradeoffs, algorithms, and engineering patterns clearly and concisely for an engineering audience.',
  },
];

const STORAGE_KEY = 'agentPresets';

/** The user's own presets: everything stored that is not one of the built-ins. */
export function parseCustomPresets(stored: string | null): PersonaPreset[] {
  if (!stored) return [];
  let parsed: unknown;
  try {
    parsed = JSON.parse(stored);
  } catch {
    return [];
  }
  if (!Array.isArray(parsed)) return [];
  const builtinNames = new Set(DEFAULT_PRESETS.map((p) => p.name));
  const seen = new Set<string>();
  const out: PersonaPreset[] = [];
  for (const item of parsed) {
    if (!item || typeof item !== 'object') continue;
    const { name, prompt } = item as { name?: unknown; prompt?: unknown };
    if (typeof name !== 'string' || typeof prompt !== 'string') continue;
    const trimmed = name.trim();
    if (!trimmed || !prompt.trim() || builtinNames.has(trimmed) || seen.has(trimmed)) continue;
    seen.add(trimmed);
    out.push({ name: trimmed, prompt });
  }
  return out;
}

/** Built-ins first, then the user's own. */
export function loadPresets(): PersonaPreset[] {
  let stored: string | null = null;
  try {
    stored = localStorage.getItem(STORAGE_KEY);
  } catch {
    /* storage unavailable: the built-ins still work */
  }
  return [...DEFAULT_PRESETS, ...parseCustomPresets(stored)];
}

/** Persist the user's own presets (built-ins are never written). */
export function savePresets(presets: PersonaPreset[]): void {
  const custom = presets.filter((p) => !p.builtin).map(({ name, prompt }) => ({ name, prompt }));
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(custom));
  } catch {
    /* storage unavailable: the preset lasts until the panel is closed */
  }
}

/** Add a preset, or replace the user's own preset of the same name. */
export function upsertPreset(
  presets: PersonaPreset[],
  name: string,
  prompt: string,
): PersonaPreset[] {
  const trimmed = name.trim();
  if (presets.some((p) => p.builtin && p.name === trimmed)) {
    // A built-in's name is taken; keep the user's copy distinct rather than shadow it.
    return upsertPreset(presets, `${trimmed} (mine)`, prompt);
  }
  const without = presets.filter((p) => p.builtin || p.name !== trimmed);
  return [...without, { name: trimmed, prompt }];
}
