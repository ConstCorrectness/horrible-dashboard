/**
 * The model search every model field shares — the Agent pane's picker, the
 * onboarding card and the settings page.
 *
 * **Substring, not prefix.** A native `<select>`'s type-ahead and a `<datalist>`
 * both effectively match from the start of the id, so the queries people actually
 * have — every `:free` model, everything with `qwen` in it — are unreachable by
 * typing when there are several hundred OpenRouter ids. Every whitespace-separated
 * word must appear somewhere, so `free qwen` narrows rather than widening the way
 * an OR would.
 */
export function matchesModelQuery(haystack: string, query: string): boolean {
  const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (words.length === 0) return true;
  const hay = haystack.toLowerCase();
  return words.every((word) => hay.includes(word));
}
