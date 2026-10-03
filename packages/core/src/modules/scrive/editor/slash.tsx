/**
 * The slash menu: type `/` in the page and pick a block.
 *
 * Every entry either runs an editor command (text blocks, lists, tables, math,
 * callouts) or inserts a `mystBlock` holding a MyST template (figures, embeds, tabs,
 * cards, Mermaid, code cells, 3D scenes). Inserted raw blocks are selected, which
 * opens their source for the author to fill in — the template *is* the syntax, so
 * the page never holds anything the Source view would not show.
 *
 * The menu's state lives in a small store the TipTap suggestion plugin writes and
 * `<SlashMenu>` reads; keyboard handling stays in the plugin (it sees the keys first).
 */
import { Extension, type Editor, type Range } from '@tiptap/core';
import { NodeSelection } from '@tiptap/pm/state';
import Suggestion, { type SuggestionProps } from '@tiptap/suggestion';
import { useSyncExternalStore } from 'react';

export interface SlashItem {
  id: string;
  label: string;
  group: string;
  /** Extra words the filter matches. */
  keywords?: string;
  run: (editor: Editor) => void;
}

/** Insert a raw MyST block and select it, so its source opens for editing. */
function insertRaw(editor: Editor, source: string, kind = 'mystDirective') {
  const { from } = editor.state.selection;
  editor.chain().focus().insertContent({ type: 'mystBlock', attrs: { source, kind } }).run();
  // The inserted block sits just before the cursor's new position.
  const { doc } = editor.state;
  let target = -1;
  doc.nodesBetween(
    Math.max(0, from - 2),
    Math.min(doc.content.size, editor.state.selection.from + 2),
    (node, pos) => {
      if (node.type.name === 'mystBlock' && node.attrs.source === source) target = pos;
    },
  );
  if (target >= 0) {
    editor.view.dispatch(
      editor.state.tr.setSelection(NodeSelection.create(editor.state.doc, target)),
    );
  }
}

const callout = (name: string) => (editor: Editor) =>
  editor
    .chain()
    .focus()
    .insertContent({
      type: 'admonition',
      attrs: { name, args: name === 'dropdown' ? 'Details' : '', options: {} },
      content: [{ type: 'paragraph' }],
    })
    .run();

export const SLASH_ITEMS: SlashItem[] = [
  {
    id: 'p',
    label: 'Text',
    group: 'Text',
    keywords: 'paragraph plain',
    run: (e) => e.chain().focus().setParagraph().run(),
  },
  {
    id: 'h1',
    label: 'Heading 1',
    group: 'Text',
    keywords: 'title h1',
    run: (e) => e.chain().focus().setHeading({ level: 1 }).run(),
  },
  {
    id: 'h2',
    label: 'Heading 2',
    group: 'Text',
    keywords: 'h2 section',
    run: (e) => e.chain().focus().setHeading({ level: 2 }).run(),
  },
  {
    id: 'h3',
    label: 'Heading 3',
    group: 'Text',
    keywords: 'h3 subsection',
    run: (e) => e.chain().focus().setHeading({ level: 3 }).run(),
  },
  {
    id: 'ul',
    label: 'Bulleted list',
    group: 'Text',
    keywords: 'unordered bullet',
    run: (e) => e.chain().focus().toggleBulletList().run(),
  },
  {
    id: 'ol',
    label: 'Numbered list',
    group: 'Text',
    keywords: 'ordered number',
    run: (e) => e.chain().focus().toggleOrderedList().run(),
  },
  {
    id: 'todo',
    label: 'To-do list',
    group: 'Text',
    keywords: 'task check',
    run: (e) => e.chain().focus().toggleTaskList().run(),
  },
  {
    id: 'quote',
    label: 'Quote',
    group: 'Text',
    keywords: 'blockquote',
    run: (e) => e.chain().focus().toggleBlockquote().run(),
  },
  {
    id: 'hr',
    label: 'Divider',
    group: 'Text',
    keywords: 'rule line hr',
    run: (e) => e.chain().focus().setHorizontalRule().run(),
  },
  {
    id: 'code',
    label: 'Code',
    group: 'Code & data',
    keywords: 'fence snippet',
    run: (e) => e.chain().focus().setCodeBlock().run(),
  },
  {
    id: 'table',
    label: 'Table',
    group: 'Code & data',
    keywords: 'grid rows columns',
    run: (e) => e.chain().focus().insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run(),
  },
  {
    id: 'math',
    label: 'Equation',
    group: 'Math',
    keywords: 'latex tex display formula',
    run: (e) =>
      e
        .chain()
        .focus()
        .insertContent({ type: 'mathBlock', attrs: { value: '' } })
        .run(),
  },
  {
    id: 'imath',
    label: 'Inline math',
    group: 'Math',
    keywords: 'latex tex formula',
    run: (e) =>
      e
        .chain()
        .focus()
        .insertContent({ type: 'mathInline', attrs: { value: 'x' } })
        .run(),
  },
  {
    id: 'note',
    label: 'Note',
    group: 'Callouts',
    keywords: 'admonition callout info',
    run: callout('note'),
  },
  { id: 'tip', label: 'Tip', group: 'Callouts', keywords: 'admonition hint', run: callout('tip') },
  {
    id: 'important',
    label: 'Important',
    group: 'Callouts',
    keywords: 'admonition',
    run: callout('important'),
  },
  {
    id: 'warning',
    label: 'Warning',
    group: 'Callouts',
    keywords: 'admonition caution',
    run: callout('warning'),
  },
  {
    id: 'danger',
    label: 'Danger',
    group: 'Callouts',
    keywords: 'admonition error',
    run: callout('danger'),
  },
  {
    id: 'dropdown',
    label: 'Dropdown',
    group: 'Callouts',
    keywords: 'toggle collapse details',
    run: callout('dropdown'),
  },
  {
    id: 'figure',
    label: 'Figure',
    group: 'Media',
    keywords: 'image picture caption',
    run: (e) =>
      insertRaw(
        e,
        '```{figure} ../media/image.png\n:name: fig-name\n:alt: What the image shows\n\nCaption.\n```',
      ),
  },
  {
    id: 'embed',
    label: 'Embed',
    group: 'Media',
    keywords: 'iframe youtube video web',
    run: (e) =>
      insertRaw(e, '```{iframe} https://www.youtube.com/embed/VIDEO_ID\n:width: 100%\n```'),
  },
  {
    id: 'video',
    label: 'Video',
    group: 'Media',
    keywords: 'mp4 clip movie',
    run: (e) => insertRaw(e, '```{video} ../media/clip.mp4\n```'),
  },
  {
    id: 'tabs',
    label: 'Tabs',
    group: 'Layout',
    keywords: 'tab-set tab-item',
    run: (e) =>
      insertRaw(
        e,
        '::::{tab-set}\n:::{tab-item} First\nContent.\n:::\n:::{tab-item} Second\nContent.\n:::\n::::',
      ),
  },
  {
    id: 'cards',
    label: 'Cards',
    group: 'Layout',
    keywords: 'grid card columns',
    run: (e) =>
      insertRaw(
        e,
        '::::{grid} 2\n:::{card} One\nCard body.\n:::\n:::{card} Two\nCard body.\n:::\n::::',
      ),
  },
  {
    id: 'mermaid',
    label: 'Diagram',
    group: 'Diagrams',
    keywords: 'mermaid flowchart graph sequence',
    run: (e) => insertRaw(e, '```{mermaid}\ngraph LR\n  A --> B\n```'),
  },
  {
    id: 'cell',
    label: 'Code cell',
    group: 'Live',
    keywords: 'python jupyter run notebook',
    run: (e) =>
      e
        .chain()
        .focus()
        .insertContent({
          type: 'codeCell',
          attrs: { language: 'python', options: {}, source: 'print("hello")' },
        })
        .run(),
  },
  {
    id: 'r3f',
    label: '3D scene',
    group: 'Live',
    keywords: 'r3f three react fiber webgl',
    run: (e) =>
      e
        .chain()
        .focus()
        .insertContent({
          type: 'r3fScene',
          attrs: { src: '../scenes/scene.tsx', options: { height: '400' } },
        })
        .run(),
  },
  {
    id: 'raw',
    label: 'MyST source',
    group: 'Live',
    keywords: 'raw markdown directive',
    run: (e) => insertRaw(e, '', ''),
  },
];

export function filterItems(query: string): SlashItem[] {
  const q = query.trim().toLowerCase();
  if (!q) return SLASH_ITEMS;
  return SLASH_ITEMS.filter((item) =>
    `${item.label} ${item.group} ${item.keywords ?? ''}`.toLowerCase().includes(q),
  );
}

// ── menu state ─────────────────────────────────────────────────────────────────

export interface SlashState {
  items: SlashItem[];
  index: number;
  rect: DOMRect | null;
  pick: (item: SlashItem) => void;
}

/** One menu at a time: there is one focused editor, and the menu follows its caret. */
let state: SlashState | null = null;
const listeners = new Set<() => void>();
const publish = (next: SlashState | null) => {
  state = next;
  for (const listener of listeners) listener();
};

export function useSlashState(): SlashState | null {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => state,
  );
}

function fromProps(props: SuggestionProps<SlashItem, SlashItem>, index: number): SlashState {
  return {
    items: props.items,
    index: Math.min(index, Math.max(0, props.items.length - 1)),
    rect: props.clientRect?.() ?? null,
    pick: (item) => props.command(item),
  };
}

export const SlashCommand = Extension.create({
  name: 'scriveSlash',
  addProseMirrorPlugins() {
    return [
      Suggestion<SlashItem, SlashItem>({
        editor: this.editor,
        char: '/',
        allowedPrefixes: null,
        items: ({ query }) => filterItems(query),
        allow: ({ state: s, range }) => {
          const $from = s.doc.resolve(range.from);
          return $from.parent.type.name !== 'codeBlock';
        },
        command: ({ editor, range, props }: { editor: Editor; range: Range; props: SlashItem }) => {
          editor.chain().focus().deleteRange(range).run();
          props.run(editor);
        },
        render: () => {
          let current: SuggestionProps<SlashItem, SlashItem> | null = null;
          return {
            onStart: (props) => {
              current = props;
              publish(fromProps(props, 0));
            },
            onUpdate: (props) => {
              current = props;
              publish(fromProps(props, 0));
            },
            onKeyDown: ({ event }) => {
              if (!state || !current) return false;
              const count = state.items.length;
              if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
                if (!count) return true;
                const step = event.key === 'ArrowDown' ? 1 : -1;
                publish({ ...state, index: (state.index + step + count) % count });
                return true;
              }
              if (event.key === 'Enter' || event.key === 'Tab') {
                const item = state.items[state.index];
                if (!item) return false;
                current.command(item);
                return true;
              }
              if (event.key === 'Escape') {
                publish(null);
                return true;
              }
              return false;
            },
            onExit: () => {
              current = null;
              publish(null);
            },
          };
        },
      }),
    ];
  },
});

/** The menu, fixed under the caret. Rendered once by the Write view. */
export function SlashMenu() {
  const menu = useSlashState();
  if (!menu?.rect) return null;
  const below = menu.rect.bottom + 6;
  const fitsBelow = below + 320 < window.innerHeight;
  let group = '';
  return (
    <div
      className="scrive-ed-menu"
      role="listbox"
      aria-label="Insert block"
      style={{
        position: 'fixed',
        left: Math.min(menu.rect.left, window.innerWidth - 260),
        ...(fitsBelow ? { top: below } : { bottom: window.innerHeight - menu.rect.top + 6 }),
      }}
      // Keep focus (and the suggestion) in the editor while clicking.
      onMouseDown={(e) => e.preventDefault()}
    >
      {menu.items.length === 0 && (
        <div className="scrive-meta scrive-ed-menu-empty">No blocks match</div>
      )}
      {menu.items.map((item, i) => {
        const header = item.group !== group ? (group = item.group) : null;
        return (
          <div key={item.id}>
            {header && <div className="scrive-ed-menu-group">{header}</div>}
            <button
              type="button"
              role="option"
              className="scrive-ed-menu-item"
              aria-selected={i === menu.index}
              ref={(el) => {
                if (el && i === menu.index) el.scrollIntoView({ block: 'nearest' });
              }}
              onClick={() => menu.pick(item)}
            >
              {item.label}
            </button>
          </div>
        );
      })}
    </div>
  );
}
