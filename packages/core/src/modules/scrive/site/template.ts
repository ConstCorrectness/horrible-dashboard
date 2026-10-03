/**
 * The layout templates a site theme can ship (`themes/<id>/layouts/*.html`): plain HTML
 * with a few `{{…}}` tags, filled in by the static build.
 *
 * Deliberately logic-light — a template *chooses* and *repeats*, it does not compute.
 * Nothing in a template runs while the site is built: rendering is string work over a
 * parsed tree, so a theme (often agent-written) cannot reach the app it is built in.
 *
 * | Tag                                   | Meaning                                        |
 * | ------------------------------------- | ---------------------------------------------- |
 * | `{{ page.title }}`                    | a value, HTML-escaped                          |
 * | `{{{ content }}}`                     | a value as HTML (the page body, `head`)        |
 * | `{{#if page.date}}…{{else}}…{{/if}}`  | either branch, by truthiness                   |
 * | `{{#unless prev}}…{{/unless}}`        | the opposite                                   |
 * | `{{#each posts}}…{{else}}…{{/each}}`  | once per item; `{{this}}`, `{{@index}}`,       |
 * |                                       | `{{@first}}`, `{{@last}}`; names look in the   |
 * |                                       | item first, then outward                       |
 * | `{{! a note }}`, `{{!-- a note --}}`  | nothing                                        |
 *
 * Falsy: `false`, `null`, missing, `''`, `0`, and an empty list.
 */

export class TemplateError extends Error {
  constructor(name: string, message: string, offset: number, source: string) {
    const line = source.slice(0, offset).split('\n').length;
    super(`${name}:${line}: ${message}`);
    this.name = 'TemplateError';
  }
}

type Node =
  | { kind: 'text'; text: string }
  | { kind: 'value'; path: string; raw: boolean }
  | { kind: 'if'; path: string; negate: boolean; then: Node[]; else: Node[] }
  | { kind: 'each'; path: string; body: Node[]; else: Node[] };

export interface Template {
  name: string;
  nodes: Node[];
}

const TAG = /\{\{(\{)?\s*([\s\S]*?)\s*(\})?\}\}/g;
const PATH = /^(?:this|@index|@first|@last|[A-Za-z_][\w-]*)(?:\.[A-Za-z_][\w-]*)*$/;

interface Frame {
  kind: 'if' | 'unless' | 'each';
  node: Extract<Node, { kind: 'if' | 'each' }>;
  inElse: boolean;
  offset: number;
}

/** Parse a template; a malformed one throws `TemplateError` naming the line. */
export function compile(source: string, name = 'template'): Template {
  const root: Node[] = [];
  const stack: Frame[] = [];
  const target = (): Node[] => {
    const top = stack[stack.length - 1];
    if (!top) return root;
    if (top.node.kind === 'if') return top.inElse ? top.node.else : top.node.then;
    return top.inElse ? top.node.else : top.node.body;
  };
  let last = 0;
  // `{{!-- … --}}` may contain `}}`: cut them out before scanning for tags. (Same
  // length — a private-use character for each — so offsets, and the line numbers
  // errors give, still match `source`.)
  const scan = source.replace(/\{\{!--[\s\S]*?--\}\}/g, (m) => m.replace(/[^\n]/g, ''));
  const text = (from: number, to: number) => scan.slice(from, to).replace(//g, '');
  for (const match of scan.matchAll(TAG)) {
    const at = match.index ?? 0;
    if (at > last) target().push({ kind: 'text', text: text(last, at) });
    last = at + match[0].length;
    const triple = match[1] === '{';
    if (triple !== (match[3] === '}'))
      throw new TemplateError(name, 'unbalanced {{{ }}}', at, source);
    const body = match[2];
    if (body.startsWith('!')) continue;
    const open = /^#(if|unless|each)\s+(\S+)$/.exec(body);
    if (open) {
      const [, kind, path] = open;
      if (!PATH.test(path)) throw new TemplateError(name, `not a name: ${path}`, at, source);
      const node: Extract<Node, { kind: 'if' | 'each' }> =
        kind === 'each'
          ? { kind: 'each', path, body: [], else: [] }
          : { kind: 'if', path, negate: kind === 'unless', then: [], else: [] };
      target().push(node);
      stack.push({ kind: kind as Frame['kind'], node, inElse: false, offset: at });
      continue;
    }
    if (body === 'else') {
      const top = stack[stack.length - 1];
      if (!top || top.inElse) throw new TemplateError(name, '{{else}} out of place', at, source);
      top.inElse = true;
      continue;
    }
    const close = /^\/(if|unless|each)$/.exec(body);
    if (close) {
      const top = stack.pop();
      if (!top) throw new TemplateError(name, `{{/${close[1]}}} closes nothing`, at, source);
      if (top.kind !== close[1])
        throw new TemplateError(name, `{{/${close[1]}}} closes a {{#${top.kind}}}`, at, source);
      continue;
    }
    if (body.startsWith('#') || body.startsWith('/') || body.startsWith('>'))
      throw new TemplateError(name, `unknown tag {{${body}}}`, at, source);
    if (!PATH.test(body)) throw new TemplateError(name, `not a name: ${body}`, at, source);
    target().push({ kind: 'value', path: body, raw: triple });
  }
  if (last < source.length) target().push({ kind: 'text', text: text(last, source.length) });
  const open = stack.pop();
  if (open) throw new TemplateError(name, `{{#${open.kind}}} is never closed`, open.offset, source);
  return { name, nodes: root };
}

export function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

interface Scope {
  value: unknown;
  index?: number;
  count?: number;
}

function lookup(scopes: Scope[], path: string): unknown {
  const top = scopes[scopes.length - 1];
  if (path === '@index') return top.index;
  if (path === '@first') return top.index === 0;
  if (path === '@last') return top.index !== undefined && top.index === (top.count ?? 0) - 1;
  const [head, ...rest] = path.split('.');
  let value: unknown;
  if (head === 'this') value = top.value;
  else {
    for (let i = scopes.length - 1; i >= 0; i--) {
      const v = scopes[i].value;
      if (v && typeof v === 'object' && head in (v as Record<string, unknown>)) {
        value = (v as Record<string, unknown>)[head];
        break;
      }
    }
  }
  for (const key of rest) {
    if (!value || typeof value !== 'object') return undefined;
    value = (value as Record<string, unknown>)[key];
  }
  return value;
}

function truthy(value: unknown): boolean {
  return Array.isArray(value) ? value.length > 0 : !!value;
}

function show(value: unknown): string {
  if (value === null || value === undefined || value === false) return '';
  if (typeof value === 'object') return '';
  return String(value);
}

function run(nodes: Node[], scopes: Scope[], out: string[]): void {
  for (const node of nodes) {
    if (node.kind === 'text') out.push(node.text);
    else if (node.kind === 'value') {
      const text = show(lookup(scopes, node.path));
      out.push(node.raw ? text : escapeHtml(text));
    } else if (node.kind === 'if') {
      const yes = truthy(lookup(scopes, node.path)) !== node.negate;
      run(yes ? node.then : node.else, scopes, out);
    } else {
      const list = lookup(scopes, node.path);
      const items = Array.isArray(list) ? list : [];
      if (!items.length) run(node.else, scopes, out);
      items.forEach((item, index) =>
        run(node.body, [...scopes, { value: item, index, count: items.length }], out),
      );
    }
  }
}

export function render(template: Template, context: Record<string, unknown>): string {
  const out: string[] = [];
  run(template.nodes, [{ value: context }], out);
  return out.join('');
}

/** The layouts a theme may ship, by file name in `layouts/`. */
export const TEMPLATE_NAMES = ['base', 'page', 'home', 'list'] as const;
export type TemplateName = (typeof TEMPLATE_NAMES)[number];

export type ThemeTemplates = Partial<Record<TemplateName, Template>>;

/** Compile a theme's layouts (`name → source`); unknown names are ignored. */
export function compileTheme(sources: Record<string, string> | undefined): ThemeTemplates {
  const out: ThemeTemplates = {};
  for (const name of TEMPLATE_NAMES) {
    const source = sources?.[name];
    if (source !== undefined) out[name] = compile(source, `layouts/${name}.html`);
  }
  return out;
}
