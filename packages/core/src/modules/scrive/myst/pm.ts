/**
 * MyST mdast ⇄ the block editor's document (ProseMirror JSON, as TipTap speaks it).
 *
 * Pure and JSON-only — no schema object, no DOM — so it runs in core's vitest, and
 * the editor (`editor/`) is the only place TipTap is imported.
 *
 * **Faithful or raw.** `fromMdast` converts only what it can represent completely;
 * anything else throws `Unsupported`, and the caller keeps that block as a `mystBlock`
 * holding its source verbatim. `faithful` goes one step further: it prints the
 * converted block, re-parses the print, and checks the result is the node it came
 * from. A block that fails is also kept raw. So a block is only ever editable as rich
 * content when editing it cannot lose anything the author wrote.
 *
 * Equality is MyST's, not the file's: a soft line break is a space (the editor flows
 * paragraphs), and a directive's raw `value` is ignored in favour of its parsed
 * children (the body may be re-indented or re-fenced).
 */
import { parseMyst, type MystNode } from './parse';
import { splitDirectiveBody } from '../render/directives';
import { printBlocks, RAW } from './printer';

export interface PMMark {
  type: string;
  attrs?: Record<string, unknown>;
}

export interface PMNode {
  type: string;
  attrs?: Record<string, unknown>;
  content?: PMNode[];
  marks?: PMMark[];
  text?: string;
}

export class Unsupported extends Error {}

const unsupported = (what: string): never => {
  throw new Unsupported(what);
};

/** The directives the editor edits as rich content: admonitions. */
export const ADMONITIONS = [
  'admonition',
  'attention',
  'caution',
  'danger',
  'error',
  'hint',
  'important',
  'note',
  'seealso',
  'tip',
  'warning',
] as const;

/** Directives edited as a container of blocks: the admonitions, and `{dropdown}`. */
export const CONTAINERS = [...ADMONITIONS, 'dropdown'] as const;

// ── mdast → editor ─────────────────────────────────────────────────────────────

function inlines(nodes: MystNode[] = [], marks: PMMark[] = []): PMNode[] {
  const out: PMNode[] = [];
  const withMarks = (node: PMNode): PMNode => (marks.length ? { ...node, marks } : node);
  for (const node of nodes) {
    switch (node.type) {
      case 'text': {
        // A soft line break renders as a space; the editor flows the paragraph.
        const text = String(node.value ?? '').replace(/[ \t]*\r?\n[ \t]*/g, ' ');
        if (text) out.push(withMarks({ type: 'text', text }));
        break;
      }
      case 'emphasis':
        out.push(...inlines(node.children, [...marks, { type: 'italic' }]));
        break;
      case 'strong':
        out.push(...inlines(node.children, [...marks, { type: 'bold' }]));
        break;
      case 'link': {
        const attrs: Record<string, unknown> = { href: String(node.url ?? '') };
        if (typeof node.title === 'string') attrs.title = node.title;
        out.push(...inlines(node.children, [...marks, { type: 'link', attrs }]));
        break;
      }
      case 'inlineCode':
        if (node.value) {
          out.push({ type: 'text', text: String(node.value), marks: [...marks, { type: 'code' }] });
        }
        break;
      case 'break':
        out.push(withMarks({ type: 'hardBreak' }));
        break;
      case 'inlineMath':
        out.push(withMarks({ type: 'mathInline', attrs: { value: String(node.value ?? '') } }));
        break;
      case 'mystRole':
        out.push(
          withMarks({
            type: 'mystRole',
            attrs: { name: String(node.name ?? ''), value: String(node.value ?? '') },
          }),
        );
        break;
      case 'footnoteReference':
        out.push(
          withMarks({
            type: 'footnoteRef',
            attrs: { label: String(node.label ?? node.identifier ?? '') },
          }),
        );
        break;
      case 'html':
        out.push(withMarks({ type: 'htmlInline', attrs: { value: String(node.value ?? '') } }));
        break;
      case 'image': {
        const attrs: Record<string, unknown> = {
          src: String(node.url ?? ''),
          alt: String(node.alt ?? ''),
        };
        if (typeof node.title === 'string') attrs.title = node.title;
        out.push(withMarks({ type: 'image', attrs }));
        break;
      }
      default:
        unsupported(`inline ${node.type}`);
    }
  }
  return out;
}

const paragraph = (content: PMNode[]): PMNode =>
  content.length ? { type: 'paragraph', content } : { type: 'paragraph' };

function listItems(node: MystNode, itemType: string): PMNode[] {
  return (node.children ?? []).map((item) => {
    if (item.type !== 'listItem') unsupported(`list child ${item.type}`);
    const children = blocks(item.children ?? []);
    // The editor's list items open with a paragraph, as almost every list item does.
    if (children.length === 0) children.push(paragraph([]));
    if (children[0].type !== 'paragraph') unsupported('list item without a leading paragraph');
    const attrs = itemType === 'taskItem' ? { checked: item.checked === true } : undefined;
    return attrs
      ? { type: itemType, attrs, content: children }
      : { type: itemType, content: children };
  });
}

function list(node: MystNode): PMNode {
  const items = node.children ?? [];
  const checks = items.filter((item) => typeof item.checked === 'boolean').length;
  const spread = node.spread === true;
  if (checks && checks !== items.length) unsupported('a list mixing tasks and items');
  if (checks) {
    if (node.ordered) unsupported('an ordered task list');
    return { type: 'taskList', attrs: { spread }, content: listItems(node, 'taskItem') };
  }
  if (node.ordered) {
    const start = typeof node.start === 'number' ? node.start : 1;
    return { type: 'orderedList', attrs: { start, spread }, content: listItems(node, 'listItem') };
  }
  return { type: 'bulletList', attrs: { spread }, content: listItems(node, 'listItem') };
}

function table(node: MystNode): PMNode {
  const rows = node.children ?? [];
  if (!rows.length) unsupported('an empty table');
  return {
    type: 'table',
    content: rows.map((row, r) => {
      if (row.type !== 'tableRow') unsupported(`table child ${row.type}`);
      return {
        type: 'tableRow',
        content: (row.children ?? []).map((cell) => {
          // GFM: the first row is the header row, and only it.
          if ((cell.header === true) !== (r === 0)) unsupported('a header cell outside row 1');
          return {
            type: r === 0 ? 'tableHeader' : 'tableCell',
            attrs: { align: typeof cell.align === 'string' ? cell.align : null },
            content: [paragraph(inlines(cell.children))],
          };
        }),
      };
    }),
  };
}

/** The fence character a directive was written with, from its first line. */
export function fenceOf(source: string): '`' | ':' {
  return /^[ \t]*`/.test(source) ? '`' : ':';
}

/** The node a container directive parses to, and the child that is its title. */
const CONTAINER_INNER: Record<string, [string, string]> = {
  dropdown: ['details', 'summary'],
};

/** An admonition, or a `{dropdown}`: a directive whose body is Markdown blocks. */
function admonition(node: MystNode, source?: string): PMNode {
  const inner = node.children ?? [];
  const [innerType, titleType] = CONTAINER_INNER[String(node.name)] ?? [
    'admonition',
    'admonitionTitle',
  ];
  if (inner.length !== 1 || inner[0].type !== innerType) unsupported('directive');
  const body = (inner[0].children ?? []).filter((c) => c.type !== titleType);
  const content = blocks(body);
  return {
    type: 'admonition',
    attrs: {
      name: String(node.name),
      args: typeof node.args === 'string' ? node.args : '',
      options: { ...((node.options as Record<string, unknown> | undefined) ?? {}) },
      fence: source ? fenceOf(source) : ':',
    },
    content: content.length ? content : [paragraph([])],
  };
}

/** `{code-cell}`: its source, language and options (`:tags:` and the like, kept
 * as the strings they were written as). Run from the editor; see `cells.ts`. */
function codeCell(node: MystNode): PMNode {
  return {
    type: 'codeCell',
    attrs: {
      language: typeof node.args === 'string' ? node.args : '',
      options: { ...((node.options as Record<string, unknown> | undefined) ?? {}) },
      source: String(node.value ?? ''),
    },
  };
}

/** `{r3f} scenes/x.tsx` with options and no body. The parser leaves an unknown
 * directive's options inside its `value`; they are split off here. */
function r3fScene(node: MystNode): PMNode {
  const { options, body } = splitDirectiveBody(String(node.value ?? ''));
  if (body.trim()) unsupported('an {r3f} with a body');
  return {
    type: 'r3fScene',
    attrs: { src: typeof node.args === 'string' ? node.args : '', options },
  };
}

/**
 * Scrive's live embeds: an argument and options, never a body. One editor block
 * (`scriveEmbed`) holds all of them; its node view picks what to draw by `name`.
 * `{space}` is a Hugging Face Space (render/space.ts); `{app}` a web app from the
 * site's `apps/` folder (render/AppFrame.tsx); `{webllm}` an in-browser model
 * (render/WebLlm.tsx); `{tokenviz}` a recorded generation (render/TokenViz.tsx).
 */
export const EMBED_DIRECTIVES: readonly string[] = ['space', 'app', 'webllm', 'tokenviz'];

function embedDirective(node: MystNode): PMNode {
  const name = String(node.name);
  const { options, body } = splitDirectiveBody(String(node.value ?? ''));
  if (body.trim()) unsupported(`a {${name}} with a body`);
  return {
    type: 'scriveEmbed',
    attrs: { name, src: typeof node.args === 'string' ? node.args : '', options },
  };
}

/** `{math}` with at most a `:label:` — the same block as `$$`, remembered as written. */
function mathDirective(node: MystNode): PMNode {
  const inner = node.children ?? [];
  const options = Object.keys((node.options as Record<string, unknown> | undefined) ?? {});
  if (inner.length !== 1 || inner[0].type !== 'math' || options.some((k) => k !== 'label')) {
    unsupported('math directive');
  }
  const label = typeof inner[0].label === 'string' ? inner[0].label : null;
  return {
    type: 'mathBlock',
    attrs: { value: String(inner[0].value ?? ''), label, form: 'directive' },
  };
}

/** One block node. `source` is its text when known (the top level), for style
 * details the tree does not keep. */
export function blockFromMdast(node: MystNode, source?: string): PMNode {
  switch (node.type) {
    case 'paragraph':
      return paragraph(inlines(node.children));
    case 'heading':
      return {
        type: 'heading',
        attrs: { level: Number(node.depth) || 1 },
        content: inlines(node.children),
      };
    case 'list':
      return list(node);
    case 'blockquote': {
      const content = blocks(node.children ?? []);
      return { type: 'blockquote', content: content.length ? content : [paragraph([])] };
    }
    case 'thematicBreak':
      return { type: 'horizontalRule' };
    case 'code': {
      const value = String(node.value ?? '');
      const attrs = {
        language: typeof node.lang === 'string' && node.lang ? node.lang : null,
        meta: typeof node.meta === 'string' && node.meta ? node.meta : null,
      };
      return value
        ? { type: 'codeBlock', attrs, content: [{ type: 'text', text: value }] }
        : { type: 'codeBlock', attrs };
    }
    case 'math': {
      const label = typeof node.label === 'string' ? node.label : null;
      return {
        type: 'mathBlock',
        attrs: { value: String(node.value ?? ''), label, form: 'dollars' },
      };
    }
    case 'mystDirective':
      if (node.name === 'math') return mathDirective(node);
      if (node.name === 'code-cell') return codeCell(node);
      if (node.name === 'r3f') return r3fScene(node);
      if (EMBED_DIRECTIVES.includes(String(node.name))) return embedDirective(node);
      if (!CONTAINERS.includes(String(node.name) as (typeof CONTAINERS)[number])) {
        unsupported(`directive ${String(node.name)}`);
      }
      return admonition(node, source);
    case 'table':
      return table(node);
    default:
      return unsupported(`block ${node.type}`);
  }
}

function blocks(nodes: MystNode[]): PMNode[] {
  return nodes.map((node) => blockFromMdast(node));
}

// ── editor → mdast ─────────────────────────────────────────────────────────────

const sameMark = (a: PMMark, b: PMMark) =>
  a.type === b.type && JSON.stringify(a.attrs ?? {}) === JSON.stringify(b.attrs ?? {});

const MARK_ORDER = ['link', 'bold', 'italic'];

/** Inline editor nodes → phrasing mdast, nesting marks so each covers the longest
 * run it can (so `*a **b***` comes back as it was written). */
function phrasing(items: PMNode[], applied: PMMark[] = []): MystNode[] {
  const out: MystNode[] = [];
  const pending = (item: PMNode) =>
    (item.marks ?? []).filter((m) => m.type !== 'code' && !applied.some((a) => sameMark(a, m)));
  let i = 0;
  while (i < items.length) {
    const open = pending(items[i]);
    if (!open.length) {
      out.push(leaf(items[i]));
      i++;
      continue;
    }
    // The pending mark whose run is longest; ties in a fixed order.
    const runEnd = (mark: PMMark) => {
      let end = i;
      while (end + 1 < items.length && pending(items[end + 1]).some((m) => sameMark(m, mark)))
        end++;
      return end;
    };
    const ordered = open.sort((a, b) => MARK_ORDER.indexOf(a.type) - MARK_ORDER.indexOf(b.type));
    let best = ordered[0];
    let bestEnd = runEnd(best);
    for (const mark of ordered.slice(1)) {
      const end = runEnd(mark);
      if (end > bestEnd) [best, bestEnd] = [mark, end];
    }
    const children = phrasing(items.slice(i, bestEnd + 1), [...applied, best]);
    out.push(wrap(best, children));
    i = bestEnd + 1;
  }
  return mergeText(out);
}

function wrap(mark: PMMark, children: MystNode[]): MystNode {
  switch (mark.type) {
    case 'bold':
      return { type: 'strong', children };
    case 'italic':
      return { type: 'emphasis', children };
    case 'link': {
      const node: MystNode = { type: 'link', url: String(mark.attrs?.href ?? ''), children };
      if (typeof mark.attrs?.title === 'string' && mark.attrs.title) node.title = mark.attrs.title;
      return node;
    }
    default:
      return unsupported(`mark ${mark.type}`);
  }
}

function leaf(item: PMNode): MystNode {
  const a = item.attrs ?? {};
  switch (item.type) {
    case 'text':
      return (item.marks ?? []).some((m) => m.type === 'code')
        ? { type: 'inlineCode', value: item.text ?? '' }
        : { type: 'text', value: item.text ?? '' };
    case 'hardBreak':
      return { type: 'break' };
    case 'mathInline':
      return { type: 'inlineMath', value: String(a.value ?? '') };
    case 'mystRole':
      return { type: 'mystRole', name: String(a.name ?? ''), value: String(a.value ?? '') };
    case 'footnoteReference':
    case 'footnoteRef': {
      const label = String(a.label ?? '');
      return { type: 'footnoteReference', identifier: label.toLowerCase(), label };
    }
    case 'htmlInline':
      return { type: 'html', value: String(a.value ?? '') };
    case 'image': {
      const node: MystNode = { type: 'image', url: String(a.src ?? ''), alt: String(a.alt ?? '') };
      if (typeof a.title === 'string' && a.title) node.title = a.title;
      return node;
    }
    default:
      return unsupported(`inline ${item.type}`);
  }
}

function mergeText(nodes: MystNode[]): MystNode[] {
  const out: MystNode[] = [];
  for (const node of nodes) {
    const last = out[out.length - 1];
    if (node.type === 'text' && last?.type === 'text') {
      out[out.length - 1] = { ...last, value: String(last.value) + String(node.value) };
    } else out.push(node);
  }
  return out;
}

function listToMdast(node: PMNode, ordered: boolean): MystNode {
  const spread = node.attrs?.spread === true;
  const out: MystNode = {
    type: 'list',
    ordered,
    spread,
    children: (node.content ?? []).map((item) => {
      const li: MystNode = {
        type: 'listItem',
        spread,
        children: blocksToMdast(item.content ?? []),
      };
      if (item.type === 'taskItem') li.checked = item.attrs?.checked === true;
      return li;
    }),
  };
  if (ordered) out.start = Number(node.attrs?.start ?? 1);
  return out;
}

/** One editor block → mdast. */
export function blockToMdast(node: PMNode): MystNode {
  const a = node.attrs ?? {};
  switch (node.type) {
    case 'paragraph':
      return { type: 'paragraph', children: phrasing(node.content ?? []) };
    case 'heading':
      return {
        type: 'heading',
        depth: Number(a.level ?? 1),
        children: phrasing(node.content ?? []),
      };
    case 'bulletList':
    case 'taskList':
      return listToMdast(node, false);
    case 'orderedList':
      return listToMdast(node, true);
    case 'blockquote':
      return { type: 'blockquote', children: blocksToMdast(node.content ?? []) };
    case 'horizontalRule':
      return { type: 'thematicBreak' };
    case 'codeBlock': {
      const out: MystNode = {
        type: 'code',
        value: (node.content ?? []).map((t) => t.text ?? '').join(''),
      };
      if (typeof a.language === 'string' && a.language) out.lang = a.language;
      if (typeof a.meta === 'string' && a.meta) out.meta = a.meta;
      return out;
    }
    case 'mathBlock': {
      const out: MystNode = { type: 'math', value: String(a.value ?? '') };
      if (typeof a.label === 'string' && a.label) out.label = a.label;
      if (a.form === 'directive') out.form = 'directive';
      return out;
    }
    case 'admonition':
      return {
        type: 'mystDirective',
        name: String(a.name ?? 'note'),
        args: String(a.args ?? ''),
        options: (a.options as Record<string, unknown> | undefined) ?? {},
        fence: a.fence === '`' ? '`' : ':',
        children: blocksToMdast(node.content ?? []).filter(
          // An admonition left with one empty paragraph has no body.
          (child, _i, all) => !(all.length === 1 && isEmptyParagraph(child)),
        ),
      };
    case 'table':
      return {
        type: 'table',
        children: (node.content ?? []).map((row) => ({
          type: 'tableRow',
          children: (row.content ?? []).map((cell) => {
            const out: MystNode = {
              type: 'tableCell',
              children: phrasing((cell.content ?? []).flatMap((p) => p.content ?? [])),
            };
            if (typeof cell.attrs?.align === 'string') out.align = cell.attrs.align;
            return out;
          }),
        })),
      };
    case 'scriveEmbed':
      return {
        type: 'mystDirective',
        name: String(a.name ?? ''),
        args: String(a.src ?? ''),
        options: (a.options as Record<string, unknown> | undefined) ?? {},
        value: '',
        fence: '`',
        rawBody: true,
      };
    case 'r3fScene':
      return {
        type: 'mystDirective',
        name: 'r3f',
        args: String(a.src ?? ''),
        options: (a.options as Record<string, unknown> | undefined) ?? {},
        value: '',
        fence: '`',
        rawBody: true,
      };
    case 'codeCell':
      return {
        type: 'mystDirective',
        name: 'code-cell',
        args: String(a.language ?? ''),
        options: (a.options as Record<string, unknown> | undefined) ?? {},
        value: String(a.source ?? ''),
        fence: '`',
        rawBody: true,
      };
    case 'mystBlock':
      return { type: RAW, value: String(a.source ?? '') };
    default:
      return unsupported(`block ${node.type}`);
  }
}

const isEmptyParagraph = (node: MystNode) =>
  node.type === 'paragraph' && !(node.children ?? []).length;

function blocksToMdast(nodes: PMNode[]): MystNode[] {
  return nodes.map(blockToMdast);
}

/** An editor block as MyST text (`\n` line endings, no trailing newline). */
export function printBlock(node: PMNode): string {
  return printBlocks([blockToMdast(node)]);
}

// ── fidelity ───────────────────────────────────────────────────────────────────

/** Fields that describe where a node came from rather than what it is. */
const IGNORED = new Set(['position', 'key', 'html_id']);

function normalize(node: unknown, parent?: string): unknown {
  if (Array.isArray(node)) return node.map((n) => normalize(n, parent));
  if (!node || typeof node !== 'object') return node;
  const n = node as MystNode;
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(n)) {
    if (IGNORED.has(key)) continue;
    if (key === 'value' && n.type === 'mystDirective' && n.children) continue;
    // A code cell's (empty) outputs node gets a random id per parse.
    if (key === 'id' && n.type === 'outputs') continue;
    if (key === 'value' && n.type === 'text' && typeof value === 'string') {
      out.value = value.replace(/[ \t]*\r?\n[ \t]*/g, ' ');
      continue;
    }
    out[key] = normalize(value, n.type);
  }
  return out;
}

function footnoteLabels(node: MystNode, into = new Set<string>()): Set<string> {
  if (node.type === 'footnoteReference') into.add(String(node.label ?? node.identifier));
  for (const child of node.children ?? []) footnoteLabels(child, into);
  return into;
}

/**
 * Whether `pm` prints back to `node`: print it, re-parse the print, compare. A
 * footnote reference only parses as one when its definition exists, so stand-in
 * definitions are appended for the re-parse (and ignored after it).
 */
export function faithful(node: MystNode, pm: PMNode): boolean {
  let printed: string;
  try {
    printed = printBlock(pm);
  } catch {
    return false;
  }
  const labels = [...footnoteLabels(node)];
  const defs = labels.map((label) => `[^${label}]: .`).join('\n');
  const reparsed = parseMyst(defs ? `${printed}\n\n${defs}\n` : `${printed}\n`).children.filter(
    (child) => child.type !== 'footnoteDefinition',
  );
  if (reparsed.length !== 1) return false;
  return JSON.stringify(normalize(reparsed[0])) === JSON.stringify(normalize(node));
}

/** `node` as an editor block if that is lossless, else `null` (keep it raw). */
export function editableBlock(node: MystNode, source: string): PMNode | null {
  let pm: PMNode;
  try {
    pm = blockFromMdast(node, source);
  } catch (err) {
    if (err instanceof Unsupported) return null;
    throw err;
  }
  return faithful(node, pm) ? pm : null;
}
