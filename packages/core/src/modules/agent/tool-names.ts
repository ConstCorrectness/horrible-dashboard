/**
 * Fuzzy matching of a tool name the model got slightly wrong.
 *
 * Kept as a **pure** sibling of tool-exec.ts — it takes the known names rather than
 * reading the registry — so it is unit-testable without booting the module graph,
 * and so the relay has one dependency-free place to answer "did you mean…".
 *
 * See docs/architecture/agent-tools.mdx.
 */

/**
 * The layout verbs, for the unknown-tool hint only.
 *
 * Deliberately not a dispatch table — the `switch` in tool-exec.ts is still the one
 * place a verb is handled, so if this list drifts the worst outcome is a poorer
 * suggestion, never a broken call.
 */
export const LAYOUT_VERBS: readonly string[] = [
  'list_available_panes',
  'list_workspaces',
  'list_open_panes',
  'get_layout',
  'get_pane_context',
  'open_pane',
  'close_pane',
  'focus_pane',
  'split_area',
  'join_area',
  'resize_area',
  'move_pane',
  'fullscreen_area',
  'toggle_region',
  'set_region_view',
  'open_tool_in_dock',
  'toggle_dock',
  'open_window',
  'dock_window',
  'window_state',
  'arrange_windows',
  'create_workspace',
  'switch_workspace',
];

/** Case/separator-insensitive key: `openPane`, `open-pane` and `open_pane` collapse. */
export function toolKey(name: string): string {
  return name.toLowerCase().replace(/[^a-z0-9]/g, '');
}

/** Words of a name: separators and camelCase both split. `openPane` → `[open, pane]`. */
function tokens(name: string): string[] {
  return name
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter(Boolean);
}

/** Whether every token of `a` appears in `b`, in order (allowing gaps). */
function isTokenSubsequence(a: readonly string[], b: readonly string[]): boolean {
  let i = 0;
  for (const t of b) if (i < a.length && a[i] === t) i++;
  return i === a.length;
}

/** The namespace before the first dot, matching the orchestrator's `_group_of`. */
function namespaceOf(name: string): string {
  const dot = name.indexOf('.');
  return dot === -1 ? '' : name.slice(0, dot).toLowerCase();
}

/** Levenshtein distance, two rows at a time (names are short). */
function editDistance(a: string, b: string): number {
  let prev = Array.from({ length: b.length + 1 }, (_, i) => i);
  for (let i = 1; i <= a.length; i++) {
    const row = [i];
    for (let j = 1; j <= b.length; j++) {
      row[j] = Math.min(prev[j] + 1, row[j - 1] + 1, prev[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
    }
    prev = row;
  }
  return prev[b.length];
}

/**
 * Names close to `name`, best first — the payload of an `unknown tool` reply.
 *
 * A small model that emits `show_pane` for `show`, or `openPane` for `open_pane`,
 * otherwise gets a bare failure and burns its remaining rounds guessing. Ranked
 * exact-normalized → containment → small edit distance, and capped so the hint stays
 * far cheaper than the catalog it stands in for.
 */
export function nearestToolNames(name: string, known: readonly string[], limit = 3): string[] {
  const key = toolKey(name);
  if (!key) return [];
  // Tolerance scales with length, or short names would match nearly everything.
  const budget = key.length <= 6 ? 1 : key.length <= 12 ? 2 : 3;
  const queryTokens = tokens(name);
  const queryNs = namespaceOf(name);
  const scored: { candidate: string; rank: number }[] = [];
  for (const candidate of new Set(known)) {
    const ck = toolKey(candidate);
    if (!ck) continue;
    if (ck === key) {
      scored.push({ candidate, rank: 0 });
      continue;
    }
    if (ck.includes(key) || key.includes(ck)) {
      scored.push({ candidate, rank: 1 });
      continue;
    }
    // A dropped or added word — `list_panes` for `list_open_panes`. Character
    // distance can't see this (four chars apart), but the words line up exactly.
    const candTokens = tokens(candidate);
    if (
      isTokenSubsequence(queryTokens, candTokens) ||
      isTokenSubsequence(candTokens, queryTokens)
    ) {
      scored.push({ candidate, rank: 2 });
      continue;
    }
    const d = editDistance(key, ck);
    if (d <= budget) {
      scored.push({ candidate, rank: 3 + d });
      continue;
    }
    // Last resort: the namespace is right, the verb is invented — `files.remove`
    // for `files.delete`. Not a typo at all, so no character rule finds it; but
    // "you have the right group, here is what it actually offers" is exactly the
    // hint that unsticks the model.
    if (queryNs && namespaceOf(candidate) === queryNs) {
      scored.push({ candidate, rank: 20 });
    }
  }
  return scored
    .sort((a, b) => a.rank - b.rank || a.candidate.localeCompare(b.candidate))
    .slice(0, limit)
    .map((m) => m.candidate);
}
