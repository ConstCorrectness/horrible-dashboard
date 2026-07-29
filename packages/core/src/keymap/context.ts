/**
 * The **context keys** a binding's `when` clause may test, and the evaluator for
 * those clauses.
 *
 * The vocabulary is deliberately **closed** — every key is listed in
 * `CONTEXT_KEYS` below. VS Code lets any extension invent a context key; here the
 * whole set fits in one screen, which is what lets the Shortcuts UI explain a
 * clause and the agent's tool schema enumerate the legal keys (so it can't write
 * a clause that silently never matches). See docs/architecture/keybindings.mdx.
 */
import type { KeyPlatform } from './spec';

/** How much of the keyboard a capturing pane owns. */
export type CaptureMode = 'keyboard' | 'pointer' | 'full';

export interface KeyContext {
  /** View id of the focused pane (e.g. `editor.buffer`), or null off any pane. */
  paneFocus: string | null;
  /** Instance id of the focused pane — distinguishes two panes of one view. */
  paneInstance: string | null;
  /** Capture mode currently held, or null when nothing has captured input. */
  capture: CaptureMode | null;
  /** View id holding capture, or null. */
  captureView: string | null;
  /** An editable element (input/textarea/contenteditable) holds DOM focus. */
  textInput: boolean;
  /** A modal dialog is open. */
  dialogOpen: boolean;
  /** An area is fullscreened in-window. */
  fullscreenArea: boolean;
  shellView: 'home' | 'workspace';
  platform: KeyPlatform;
  host: 'browser' | 'desktop';
}

export interface ContextKeyDoc {
  key: keyof KeyContext;
  type: 'string' | 'boolean';
  description: string;
  /** Known values, when the key is an enum. Absent for open-ended strings. */
  values?: string[];
}

/** The complete vocabulary. Surfaced to the Shortcuts UI and the agent tools. */
export const CONTEXT_KEYS: ContextKeyDoc[] = [
  {
    key: 'paneFocus',
    type: 'string',
    description: "View id of the focused pane, e.g. 'editor.buffer'. Null off any pane.",
  },
  {
    key: 'paneInstance',
    type: 'string',
    description: 'Instance id of the focused pane, e.g. `editor.buffer#3`.',
  },
  {
    key: 'capture',
    type: 'string',
    description: 'Capture mode a pane currently holds, or null.',
    values: ['keyboard', 'pointer', 'full'],
  },
  { key: 'captureView', type: 'string', description: 'View id holding capture, or null.' },
  {
    key: 'textInput',
    type: 'boolean',
    description: 'An input, textarea or contenteditable holds DOM focus.',
  },
  { key: 'dialogOpen', type: 'boolean', description: 'A modal dialog is open.' },
  { key: 'fullscreenArea', type: 'boolean', description: 'An area is fullscreened in-window.' },
  {
    key: 'shellView',
    type: 'string',
    description: 'Which top-level shell surface is showing.',
    values: ['home', 'workspace'],
  },
  { key: 'platform', type: 'string', description: 'OS family.', values: ['mac', 'win', 'linux'] },
  {
    key: 'host',
    type: 'string',
    description: 'Which shell the app is running in.',
    values: ['browser', 'desktop'],
  },
];

const KEY_NAMES = new Set<string>(CONTEXT_KEYS.map((k) => k.key));

export class WhenError extends Error {}

// ---------------------------------------------------------------------------
// Expression AST + recursive-descent parser
//
// Grammar (loosest to tightest):
//   or   := and ('||' and)*
//   and  := unary ('&&' unary)*
//   unary:= '!' unary | primary
//   prim := '(' or ')' | key ('==' | '!=') literal | key
// ---------------------------------------------------------------------------

type Node =
  | { t: 'or'; l: Node; r: Node }
  | { t: 'and'; l: Node; r: Node }
  | { t: 'not'; v: Node }
  | { t: 'cmp'; key: keyof KeyContext; op: '==' | '!='; value: string | null }
  | { t: 'truthy'; key: keyof KeyContext };

type Token = { t: 'op'; v: string } | { t: 'word'; v: string } | { t: 'str'; v: string };

function tokenize(src: string): Token[] {
  const out: Token[] = [];
  let i = 0;
  while (i < src.length) {
    const c = src[i];
    if (/\s/.test(c)) {
      i++;
      continue;
    }
    if (c === "'" || c === '"') {
      const end = src.indexOf(c, i + 1);
      if (end === -1) throw new WhenError(`Unterminated string in when clause: ${src}`);
      out.push({ t: 'str', v: src.slice(i + 1, end) });
      i = end + 1;
      continue;
    }
    const two = src.slice(i, i + 2);
    if (two === '&&' || two === '||' || two === '==' || two === '!=') {
      out.push({ t: 'op', v: two });
      i += 2;
      continue;
    }
    if (c === '(' || c === ')' || c === '!') {
      out.push({ t: 'op', v: c });
      i++;
      continue;
    }
    const word = /^[A-Za-z0-9_.#:-]+/.exec(src.slice(i));
    if (!word) throw new WhenError(`Unexpected character "${c}" in when clause: ${src}`);
    out.push({ t: 'word', v: word[0] });
    i += word[0].length;
  }
  return out;
}

function assertKey(name: string): keyof KeyContext {
  if (!KEY_NAMES.has(name)) {
    throw new WhenError(
      `Unknown context key "${name}". Known keys: ${[...KEY_NAMES].sort().join(', ')}`,
    );
  }
  return name as keyof KeyContext;
}

function parseWhen(src: string): Node {
  const tokens = tokenize(src);
  let pos = 0;
  const peek = () => tokens[pos];
  const eat = (v: string) => {
    const t = peek();
    if (t && t.t === 'op' && t.v === v) {
      pos++;
      return true;
    }
    return false;
  };

  function primary(): Node {
    if (eat('(')) {
      const inner = or();
      if (!eat(')')) throw new WhenError(`Missing ")" in when clause: ${src}`);
      return inner;
    }
    const t = peek();
    if (!t || t.t === 'op') throw new WhenError(`Expected a context key in when clause: ${src}`);
    pos++;
    const key = assertKey(t.v);
    const op = peek();
    if (op && op.t === 'op' && (op.v === '==' || op.v === '!=')) {
      pos++;
      const rhs = peek();
      if (!rhs || rhs.t === 'op') {
        throw new WhenError(`Expected a value after ${op.v} in when clause: ${src}`);
      }
      pos++;
      // `null` and `false` are written bare; everything else is a string value.
      const value = rhs.t === 'word' && (rhs.v === 'null' || rhs.v === 'false') ? null : rhs.v;
      return { t: 'cmp', key, op: op.v, value };
    }
    return { t: 'truthy', key };
  }

  function unary(): Node {
    if (eat('!')) return { t: 'not', v: unary() };
    return primary();
  }

  function and(): Node {
    let left = unary();
    while (eat('&&')) left = { t: 'and', l: left, r: unary() };
    return left;
  }

  function or(): Node {
    let left = and();
    while (eat('||')) left = { t: 'or', l: left, r: and() };
    return left;
  }

  const node = or();
  if (pos !== tokens.length) throw new WhenError(`Trailing input in when clause: ${src}`);
  return node;
}

const cache = new Map<string, Node>();

function compile(src: string): Node {
  const hit = cache.get(src);
  if (hit) return hit;
  const node = parseWhen(src);
  cache.set(src, node);
  return node;
}

function evaluate(node: Node, ctx: KeyContext): boolean {
  switch (node.t) {
    case 'or':
      return evaluate(node.l, ctx) || evaluate(node.r, ctx);
    case 'and':
      return evaluate(node.l, ctx) && evaluate(node.r, ctx);
    case 'not':
      return !evaluate(node.v, ctx);
    case 'truthy': {
      const v = ctx[node.key];
      return v !== null && v !== false && v !== '';
    }
    case 'cmp': {
      const actual = ctx[node.key];
      const normalized = actual === false ? null : actual === true ? 'true' : actual;
      const equal = normalized === node.value;
      return node.op === '==' ? equal : !equal;
    }
  }
}

/** Evaluate a `when` clause. Throws `WhenError` on a malformed clause. */
export function evaluateWhen(clause: string, ctx: KeyContext): boolean {
  return evaluate(compile(clause), ctx);
}

/** Evaluate, treating a malformed clause as "never matches" rather than throwing. */
export function testWhen(clause: string | undefined, ctx: KeyContext): boolean {
  if (!clause) return true;
  try {
    return evaluateWhen(clause, ctx);
  } catch {
    return false;
  }
}

/** Validate a clause without a context — for the Shortcuts UI and agent tools. */
export function validateWhen(clause: string): { ok: true } | { ok: false; error: string } {
  try {
    compile(clause);
    return { ok: true };
  } catch (err) {
    return { ok: false, error: err instanceof Error ? err.message : String(err) };
  }
}

/**
 * Which context keys a clause names. The resolver uses this for specificity —
 * a clause mentioning `paneFocus` is more specific than one that doesn't.
 */
export function keysUsed(clause: string): Set<keyof KeyContext> {
  const out = new Set<keyof KeyContext>();
  const walk = (n: Node): void => {
    switch (n.t) {
      case 'or':
      case 'and':
        walk(n.l);
        walk(n.r);
        break;
      case 'not':
        walk(n.v);
        break;
      default:
        out.add(n.key);
    }
  };
  try {
    walk(compile(clause));
  } catch {
    /* a malformed clause names nothing */
  }
  return out;
}
