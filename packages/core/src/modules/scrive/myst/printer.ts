/**
 * MyST mdast → MyST text, for the blocks the editor changed.
 *
 * Only an *edited* block is ever printed (`myst/segments.ts` keeps every other byte),
 * so the printer's style choices show up exactly where the author typed. They are
 * the conventional ones: `-` bullets, `*em*`, `**strong**`, backtick code fences,
 * `$…$` / `$$…$$` math, and colon fences (`:::{note}`) for directives with a
 * Markdown body, as the MyST guide recommends — unless the directive was written
 * with backticks, which is kept.
 *
 * Built on `mdast-util-to-markdown`, which knows CommonMark's escaping; the handlers
 * here add MyST's own constructs, and the `unsafe` patterns teach it MyST's extra
 * syntax characters (`$` opens math, `%` at a line start is a comment).
 *
 * `myst-to-md` is not used: it rewrites `$x$` to `` {math}`x` `` and drops
 * directive options and `(label)=` targets — see docs/modules/scrive.mdx.
 */
import type { Nodes, Root } from 'mdast';
import { gfmFootnoteToMarkdown } from 'mdast-util-gfm-footnote';
import { gfmTableToMarkdown } from 'mdast-util-gfm-table';
import { gfmTaskListItemToMarkdown } from 'mdast-util-gfm-task-list-item';
import { toMarkdown, type Handle, type Options, type State } from 'mdast-util-to-markdown';

import type { MystNode } from './parse';

/** A block the printer writes verbatim: source the editor held as text (a raw
 * block inside a container). */
export const RAW = 'scriveRaw';

type Any = MystNode & { [key: string]: unknown };

const longestRun = (text: string, char: string): number => {
  let best = 0;
  for (const match of text.matchAll(new RegExp(`^[ \\t]*(\\${char}+)`, 'gm'))) {
    best = Math.max(best, match[1].length);
  }
  return best;
};

/** The child blocks of a node, printed as a flow (blank lines between). */
function flow(children: MystNode[], state: State, info: Parameters<Handle>[3]): string {
  return state.containerFlow({ type: 'root', children } as unknown as Root, info);
}

const inlineMath: Handle = (node: Any) => {
  const value = String(node.value ?? '');
  return `$${value}$`;
};

/** Display math: `$$…$$`, or the `{math}` directive when it was written as one. */
const math: Handle = (node: Any) => {
  const value = String(node.value ?? '');
  const label = typeof node.label === 'string' && node.label ? node.label : '';
  if (node.form === 'directive') {
    const fence = '`'.repeat(Math.max(3, longestRun(value, '`') + 1));
    return [`${fence}{math}`, ...(label ? [`:label: ${label}`] : []), value, fence].join('\n');
  }
  return `$$\n${value}\n$$${label ? ` (${label})` : ''}`;
};

const mystRole: Handle = (node: Any) => {
  const value = String(node.value ?? '');
  let tick = '`';
  while (value.includes(tick)) tick += '`';
  const pad = value.startsWith('`') || value.endsWith('`') ? ' ' : '';
  return `{${String(node.name)}}${tick}${pad}${value}${pad}${tick}`;
};

const mystTarget: Handle = (node: Any) => `(${String(node.label)})=`;

const comment: Handle = (node: Any) =>
  String(node.value ?? '')
    .split('\n')
    .map((line) => (line ? `% ${line}` : '%'))
    .join('\n');

const raw: Handle = (node: Any) => String(node.value ?? '');

/**
 * A directive whose body is Markdown (an admonition, today). `fence` is the
 * character it was written with; the fence is made one longer than any run of
 * that character opening a line of the body, so nested directives and code fences
 * stay inside it.
 */
const mystDirective: Handle = (node: Any, _parent, state, info) => {
  const fenceChar = node.fence === '`' ? '`' : ':';
  // A directive whose body is not Markdown (a code cell's source) prints verbatim.
  const body =
    node.rawBody === true
      ? String(node.value ?? '')
      : flow((node.children as MystNode[] | undefined) ?? [], state, info);
  const fence = fenceChar.repeat(Math.max(3, longestRun(body, fenceChar) + 1));
  const args = typeof node.args === 'string' && node.args ? ` ${node.args}` : '';
  const options = Object.entries((node.options as Record<string, unknown> | undefined) ?? {})
    .map(([key, value]) => (value === true ? `:${key}:` : `:${key}: ${String(value)}`))
    .join('\n');
  const parts = [`${fence}{${String(node.name)}}${args}`];
  if (options) parts.push(options);
  if (body) parts.push(body);
  parts.push(fence);
  return parts.join('\n');
};

/**
 * MyST tables carry `header` and `align` on each cell; the GFM printer reads one
 * `align` array off the table. Convert at the boundary.
 */
function gfmTable(node: Any): Any {
  const rows = (node.children ?? []) as Any[];
  const first = (rows[0]?.children ?? []) as Any[];
  return {
    ...node,
    align: first.map((cell) => (typeof cell.align === 'string' ? cell.align : null)),
  };
}

const OPTIONS: Options = {
  bullet: '-',
  bulletOther: '*',
  emphasis: '*',
  strong: '*',
  fence: '`',
  fences: true,
  // `***`, not `---`: a `---` opening the body reads as frontmatter.
  rule: '*',
  listItemIndent: 'one',
  incrementListMarker: true,
  resourceLink: true,
  extensions: [gfmTableToMarkdown(), gfmTaskListItemToMarkdown(), gfmFootnoteToMarkdown()],
  handlers: {
    inlineMath,
    math,
    mystRole,
    mystTarget,
    mystDirective,
    comment,
    [RAW]: raw,
  } as unknown as Options['handlers'],
  unsafe: [
    // `$` opens inline math anywhere in MyST.
    { character: '$', inConstruct: 'phrasing' },
    // `%` opening a line is a comment.
    { character: '%', atBreak: true },
    // `{name}` right before a backtick would read as a role.
    { character: '{', after: '[\\w:-]+\\}`', inConstruct: 'phrasing' },
  ],
};

/** Prepare a tree for the printer: GFM table alignment. */
function prepare(node: Any): Any {
  const children = node.children?.map((child) => prepare(child as Any));
  const next = children ? { ...node, children } : node;
  return node.type === 'table' ? gfmTable(next) : next;
}

/**
 * Print block nodes as MyST, separated by blank lines, with no trailing newline.
 * Line endings are `\n`; the caller converts to the file's own.
 */
export function printBlocks(blocks: MystNode[]): string {
  const root = { type: 'root', children: blocks.map((b) => prepare(b as Any)) };
  return toMarkdown(root as unknown as Nodes, OPTIONS).replace(/\n+$/, '');
}
