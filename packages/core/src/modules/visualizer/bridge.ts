/**
 * The mode ⇄ language bridge between the visualizer and the editor. Visualizer
 * engines map to an editor language for export; the reverse is ambiguous (any JS
 * engine shares one language), so import preserves the current engine when the
 * language still fits and otherwise picks a sensible default.
 */
import type { BufferLanguage } from '../editor/service';

export type VisualizerMode = 'canvas' | 'three' | 'babylon' | 'pygame';

const JS_MODES: VisualizerMode[] = ['canvas', 'three', 'babylon'];

/** The editor language a given engine's code should be highlighted as. */
export function languageForMode(mode: VisualizerMode): BufferLanguage {
  return mode === 'pygame' ? 'python' : 'javascript';
}

/**
 * The engine to use for a buffer of `language`. `current` is preserved when it
 * already matches the language family (a JS buffer keeps three/canvas/babylon as
 * the user left it); otherwise we fall back to a default for that family.
 */
export function modeForLanguage(
  language: BufferLanguage,
  current?: VisualizerMode,
): VisualizerMode {
  if (language === 'python') return 'pygame';
  // JavaScript: keep the current engine if it's a JS one, else default to three.
  return current && JS_MODES.includes(current) ? current : 'three';
}

/** Best-effort language guess from a source URI's extension (for editor→visualizer). */
export function languageForUri(uri: string): BufferLanguage {
  return /\.py$/i.test(uri) ? 'python' : 'javascript';
}
