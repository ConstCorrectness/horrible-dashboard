/**
 * The block editor's schema: StarterKit's CommonMark blocks, task lists, GFM tables,
 * and Scrive's MyST nodes (`nodes.tsx`), plus the attributes `myst/doc.ts` needs.
 *
 * Off on purpose: strikethrough and underline (MyST has no syntax for either, so they
 * could not be saved), and TipTap's code-mark exclusivity is left as it is — a block
 * whose source combines code with other marks fails the fidelity check and opens raw.
 */
import { Extension, type AnyExtension } from '@tiptap/core';
import { TableKit } from '@tiptap/extension-table';
import { TaskItem, TaskList } from '@tiptap/extension-list';
import { Placeholder } from '@tiptap/extensions';
import StarterKit from '@tiptap/starter-kit';

import {
  Admonition,
  CodeCell,
  FootnoteRef,
  R3fScene,
  HtmlInline,
  Image,
  MathBlock,
  MathInline,
  MystBlock,
  MystRole,
} from './nodes';
import { SlashCommand } from './slash';

/** Every node type that can sit at the top level of a page. */
export const TOP_LEVEL = [
  'paragraph',
  'heading',
  'bulletList',
  'orderedList',
  'taskList',
  'blockquote',
  'horizontalRule',
  'codeBlock',
  'table',
  'mathBlock',
  'admonition',
  'codeCell',
  'r3fScene',
  'mystBlock',
];

/**
 * - `seg`: the source segment a top-level block was opened from (`myst/doc.ts`).
 *   Not rendered, so a copy pasted elsewhere is new text, and not kept on split, so
 *   Enter makes a new block rather than a second claim on the old one's source.
 * - `spread`: whether a list was written loose.
 * - `meta`: a code fence's text after the language.
 * - `align`: a table column's alignment (`:--`, `:-:`, `--:`).
 */
const ScriveAttributes = Extension.create({
  name: 'scriveAttributes',
  addGlobalAttributes() {
    return [
      {
        types: TOP_LEVEL,
        attributes: { seg: { default: null, keepOnSplit: false, rendered: false } },
      },
      {
        types: ['bulletList', 'orderedList', 'taskList'],
        attributes: { spread: { default: false, rendered: false } },
      },
      { types: ['codeBlock'], attributes: { meta: { default: null, rendered: false } } },
      {
        types: ['tableHeader', 'tableCell'],
        attributes: {
          align: {
            default: null,
            parseHTML: (el) => el.style.textAlign || null,
            renderHTML: (a) => (a.align ? { style: `text-align: ${String(a.align)}` } : {}),
          },
        },
      },
    ];
  },
});

export function scriveExtensions(): AnyExtension[] {
  return [
    StarterKit.configure({
      strike: false,
      underline: false,
      heading: { levels: [1, 2, 3, 4, 5, 6] },
      link: { openOnClick: false, autolink: true, linkOnPaste: true },
      codeBlock: { HTMLAttributes: { class: 'scrive-ed-code' } },
    }),
    TaskList,
    TaskItem.configure({ nested: true }),
    TableKit.configure({ table: { resizable: false } }),
    Placeholder.configure({
      placeholder: ({ node }) =>
        node.type.name === 'heading' ? 'Heading' : 'Write, or type / for blocks',
      includeChildren: false,
    }),
    ScriveAttributes,
    MathInline,
    MathBlock,
    Admonition,
    CodeCell,
    R3fScene,
    MystRole,
    FootnoteRef,
    HtmlInline,
    Image,
    MystBlock,
    SlashCommand,
  ];
}
