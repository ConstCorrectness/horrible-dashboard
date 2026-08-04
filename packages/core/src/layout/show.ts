/**
 * Resolution for the agent's `show` verb: loose text → a concrete thing to reveal.
 *
 * This is the pure half (no registry, no store), so the ranking is unit-testable and
 * the controller keeps only the acting. See docs/architecture/agent-tools.mdx.
 *
 * Why this exists: reaching a surface used to cost the model three rounds —
 * `list_available_panes` (a row per registered view), then `open_pane`, then
 * `get_pane_context`. That is three chances to emit malformed JSON and a large
 * tool-result payload, on a model whose whole context may be 8k. `show("my friends")`
 * is one call, and the *host* does the matching rather than the model.
 */

/** What a resolved target turns out to be. */
export type ShowTarget =
  | { kind: 'view'; viewId: string; section?: string }
  | { kind: 'region'; regionViewId: string }
  | { kind: 'workspace'; workspaceId: string };

/** The candidate surfaces `resolveShowTarget` matches against. */
export interface ShowCandidates {
  /** Registered views: id + title, plus any section and region labels they host. */
  views: ReadonlyArray<{
    id: string;
    title: string;
    sections?: ReadonlyArray<{ id: string; label: string }>;
    regions?: ReadonlyArray<{ id: string; label: string }>;
  }>;
  workspaces: ReadonlyArray<{ id: string; name: string }>;
  /**
   * Retired ids/titles → where their content lives now. This is what makes
   * "merging a pane never reduces agent reachability" true: a workspace layout is
   * disposable and reseeds from its preset, but the agent's vocabulary is not, so a
   * name that ever worked keeps working.
   */
  aliases?: Readonly<Record<string, ShowTarget>>;
}

/** Loose comparison key — case, spaces, and separators all ignored. */
export function showKey(text: string): string {
  return text.toLowerCase().replace(/[^a-z0-9]/g, '');
}

/**
 * Resolve `target` to one surface, or null.
 *
 * Ordered most-specific first so an exact id can never lose to a fuzzy title:
 * alias → view id → section → region → workspace → exact title → unique substring.
 * The substring pass is deliberately last **and must be unambiguous** — silently
 * picking one of several partial matches is how an agent opens the wrong thing and
 * reports success.
 */
export function resolveShowTarget(target: string, candidates: ShowCandidates): ShowTarget | null {
  const key = showKey(target ?? '');
  if (!key) return null;
  const { views, workspaces, aliases = {} } = candidates;

  for (const [name, dest] of Object.entries(aliases)) {
    if (showKey(name) === key) return dest;
  }
  for (const v of views) {
    if (showKey(v.id) === key) return { kind: 'view', viewId: v.id };
  }
  // "friends" should land on the People pane's Friends section, not just the pane.
  for (const v of views) {
    for (const s of v.sections ?? []) {
      if (showKey(s.id) === key || showKey(s.label) === key) {
        return { kind: 'view', viewId: v.id, section: s.id };
      }
    }
  }
  for (const v of views) {
    for (const r of v.regions ?? []) {
      if (showKey(r.id) === key || showKey(r.label) === key) {
        return { kind: 'region', regionViewId: r.id };
      }
    }
  }
  for (const w of workspaces) {
    if (showKey(w.id) === key || showKey(w.name) === key) {
      return { kind: 'workspace', workspaceId: w.id };
    }
  }
  for (const v of views) {
    if (showKey(v.title) === key) return { kind: 'view', viewId: v.id };
  }

  const partial = views.filter((v) => {
    const t = showKey(v.title);
    return t.includes(key) || key.includes(t);
  });
  if (partial.length === 1) return { kind: 'view', viewId: partial[0].id };
  return null;
}
