/**
 * Raw inline HTML, put back together.
 *
 * myst-parser emits each inline tag as its own `html` node, so `<kbd>Ctrl</kbd>` is
 * three siblings: `<kbd>`, text `Ctrl`, `</kbd>`. Sanitized one at a time, the opener
 * becomes an empty element and the text lands outside it. `groupInlineHtml` finds a
 * run that opens with a tag and ends at its matching close, and serializes the whole
 * run — tags as written, text escaped — into one string for `sanitizeHtml`.
 *
 * Only plain phrasing can sit inside a run (text, emphasis, code…). A run that holds
 * anything else — a link, math, a role, a footnote reference — is not formed, and its
 * tags render one by one as before: those nodes need the renderer, not a string.
 */
import type { MystNode } from '../myst/parse';

export type InlineItem = { node: MystNode } | { html: string; nodes: MystNode[] };

/** Elements that never close: never the start of a run. */
const VOID = new Set([
  'area',
  'base',
  'br',
  'col',
  'embed',
  'hr',
  'img',
  'input',
  'link',
  'meta',
  'source',
  'track',
  'wbr',
]);

const OPEN = /^<([a-z][a-z0-9-]*)(?:\s[^>]*)?>$/i;
const CLOSE = /^<\/([a-z][a-z0-9-]*)\s*>$/i;

/** The phrasing nodes a run may contain, by the element each one is. */
const WRAP: Record<string, string> = {
  emphasis: 'em',
  strong: 'strong',
  delete: 'del',
  underline: 'u',
  subscript: 'sub',
  superscript: 'sup',
};

function escapeText(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function openTag(node: MystNode): string | undefined {
  if (node.type !== 'html' || node.value?.endsWith('/>')) return undefined;
  const tag = OPEN.exec(node.value ?? '')?.[1]?.toLowerCase();
  return tag && !VOID.has(tag) ? tag : undefined;
}

function closeTag(node: MystNode): string | undefined {
  return node.type === 'html' ? CLOSE.exec(node.value ?? '')?.[1]?.toLowerCase() : undefined;
}

/** A node as HTML, or `undefined` when it is not plain phrasing. */
function serialize(node: MystNode): string | undefined {
  switch (node.type) {
    case 'text':
      return escapeText(node.value ?? '');
    case 'html':
      return node.value ?? '';
    case 'inlineCode':
      return `<code>${escapeText(node.value ?? '')}</code>`;
    case 'break':
      return '<br>';
  }
  const tag = WRAP[node.type];
  if (!tag) return undefined;
  const inner = (node.children ?? []).map(serialize);
  return inner.includes(undefined) ? undefined : `<${tag}>${inner.join('')}</${tag}>`;
}

/** The index of the node that closes the run opened at `start`, if there is one. */
function runEnd(children: MystNode[], start: number): number | undefined {
  const tag = openTag(children[start]);
  if (!tag) return undefined;
  let depth = 1;
  for (let i = start + 1; i < children.length; i++) {
    const node = children[i];
    if (openTag(node) === tag) depth++;
    else if (closeTag(node) === tag && --depth === 0) return i;
    else if (serialize(node) === undefined) return undefined;
  }
  return undefined;
}

/** Siblings, with each balanced run of raw inline HTML collapsed into one fragment. */
export function groupInlineHtml(children: MystNode[]): InlineItem[] {
  const items: InlineItem[] = [];
  for (let i = 0; i < children.length; i++) {
    const end = runEnd(children, i);
    if (end === undefined) {
      items.push({ node: children[i] });
      continue;
    }
    const nodes = children.slice(i, end + 1);
    items.push({ html: nodes.map(serialize).join(''), nodes });
    i = end;
  }
  return items;
}
