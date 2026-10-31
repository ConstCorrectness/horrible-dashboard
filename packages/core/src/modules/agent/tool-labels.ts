/**
 * How a tool call reads in the chat's activity timeline: a short verb phrase for
 * the row, and a one-line summary of its arguments beside it in mono.
 *
 * Tool names are the model's vocabulary (`files.read_file`, `get_pane_context`),
 * not the user's. A small table covers the always-on core, where a literal
 * rendering would be worst; everything else derives from the name, which is
 * honest about what ran even for a tool this file has never heard of.
 */

const KNOWN: Record<string, string> = {
  show: 'Show',
  list_available_panes: 'List panes',
  list_open_panes: 'List open panes',
  get_layout: 'Read layout',
  get_pane_context: 'Read pane',
  open_pane: 'Open pane',
  close_pane: 'Close pane',
  list_tool_groups: 'List tool groups',
  load_tools: 'Load tools',
  use_skill: 'Read skill',
  list_peers: 'List peers',
  'agent.ask_peer': 'Ask a peer',
  'agent.delegate': 'Delegate',
  ask_user: 'Ask you',
  update_todos: 'Update tasks',
  submit_plan: 'Submit plan',
};

/** `files.read_file` → `Read file`; `editor.proposeEdit` → `Propose edit`. */
function humanize(name: string): string {
  const verb = name.includes('.') ? name.slice(name.lastIndexOf('.') + 1) : name;
  const words = verb
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .replace(/[_-]+/g, ' ')
    .trim()
    .toLowerCase();
  return words ? words[0].toUpperCase() + words.slice(1) : name;
}

export function toolLabel(name: string): string {
  return KNOWN[name] ?? humanize(name);
}

/** The group a tool belongs to, as the backend names it — shown muted beside the
 *  label so `Search` from `github` and `Search` from `kb` stay distinguishable. */
export function toolGroup(name: string): string | null {
  return name.includes('.') ? name.slice(0, name.indexOf('.')) : null;
}

function clip(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

function scalar(value: unknown): string {
  if (typeof value === 'string') return clip(value.replace(/\s+/g, ' '), 48);
  if (value === null || typeof value !== 'object') return String(value);
  if (Array.isArray(value)) return `[${value.length}]`;
  return '{…}';
}

/** `{path: "/x/y.ts", line: 3}` → `path: /x/y.ts, line: 3`, one line, capped. */
export function argsSummary(args: unknown, max = 120): string {
  if (args === null || args === undefined) return '';
  if (typeof args !== 'object' || Array.isArray(args)) return clip(scalar(args), max);
  const parts = Object.entries(args as Record<string, unknown>)
    .filter(([, v]) => v !== undefined && v !== '')
    .map(([k, v]) => `${k}: ${scalar(v)}`);
  return clip(parts.join(', '), max);
}

/** Milliseconds as the timeline prints them: `340ms`, `2.4s`, `1m 12s`. */
export function formatDuration(ms: number): string {
  if (ms < 1000) return `${Math.max(0, Math.round(ms))}ms`;
  if (ms < 10_000) return `${(ms / 1000).toFixed(1)}s`;
  const s = Math.round(ms / 1000);
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
}
