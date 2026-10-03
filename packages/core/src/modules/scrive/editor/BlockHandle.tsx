/**
 * The handle beside the top-level block under the pointer: `+` inserts a block below
 * (and opens the slash menu), the grip drags the block, and clicking the grip opens
 * the block menu — Turn into, Duplicate, Edit as MyST, Delete.
 *
 * Dragging hands ProseMirror the block as a move (`view.dragging`), so its own drop
 * handling places it; the block keeps its source segment and is written back
 * byte-for-byte in its new place.
 */
import type { Editor } from '@tiptap/core';
import type { Node as PMNode } from '@tiptap/pm/model';
import { NodeSelection, Selection } from '@tiptap/pm/state';
import { useCallback, useEffect, useRef, useState } from 'react';

import { openDoc } from '../myst/doc';
import { printBlock, type PMNode as JsonNode } from '../myst/pm';

interface Target {
  pos: number;
  node: PMNode;
  top: number;
  left: number;
}

const TEXTUAL = new Set([
  'paragraph',
  'heading',
  'bulletList',
  'orderedList',
  'taskList',
  'blockquote',
  'codeBlock',
]);

interface TurnInto {
  label: string;
  run: (editor: Editor) => void;
}

const TURN_INTO: TurnInto[] = [
  { label: 'Text', run: (e) => e.chain().focus().setParagraph().run() },
  { label: 'Heading 1', run: (e) => e.chain().focus().setHeading({ level: 1 }).run() },
  { label: 'Heading 2', run: (e) => e.chain().focus().setHeading({ level: 2 }).run() },
  { label: 'Heading 3', run: (e) => e.chain().focus().setHeading({ level: 3 }).run() },
  { label: 'Bulleted list', run: (e) => e.chain().focus().toggleBulletList().run() },
  { label: 'Numbered list', run: (e) => e.chain().focus().toggleOrderedList().run() },
  { label: 'To-do list', run: (e) => e.chain().focus().toggleTaskList().run() },
  { label: 'Quote', run: (e) => e.chain().focus().toggleBlockquote().run() },
  { label: 'Code', run: (e) => e.chain().focus().setCodeBlock().run() },
  {
    label: 'Callout',
    run: (e) =>
      e.chain().focus().wrapIn('admonition', { name: 'note', args: '', options: {} }).run(),
  },
];

/** A block's JSON without its source tag: a copy is new text. */
function untagged(json: JsonNode): JsonNode {
  const attrs = { ...(json.attrs ?? {}) };
  delete attrs.seg;
  return { ...json, attrs };
}

export function BlockHandle({ editor, host }: { editor: Editor; host: HTMLElement | null }) {
  const [target, setTarget] = useState<Target | null>(null);
  const [menu, setMenu] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  // Track the top-level block under the pointer.
  useEffect(() => {
    if (!host) return;
    const onMove = (event: MouseEvent) => {
      if (menu) return;
      const view = editor.view;
      const root = view.dom;
      let found: Target | null = null;
      view.state.doc.forEach((node, offset) => {
        if (found) return;
        const dom = view.nodeDOM(offset);
        if (!(dom instanceof HTMLElement) || dom.parentElement !== root) return;
        const rect = dom.getBoundingClientRect();
        if (event.clientY >= rect.top - 4 && event.clientY <= rect.bottom + 4) {
          const hostRect = host.getBoundingClientRect();
          found = {
            pos: offset,
            node,
            top: rect.top - hostRect.top + host.scrollTop,
            left: rect.left - hostRect.left + host.scrollLeft,
          };
        }
      });
      if (found) setTarget(found);
    };
    host.addEventListener('mousemove', onMove);
    return () => host.removeEventListener('mousemove', onMove);
  }, [editor, host, menu]);

  // Close the menu on an outside click or Escape.
  useEffect(() => {
    if (!menu) return;
    const close = (e: Event) => {
      if (e instanceof KeyboardEvent && e.key !== 'Escape') return;
      if (e instanceof MouseEvent && menuRef.current?.contains(e.target as Node)) return;
      setMenu(false);
    };
    document.addEventListener('mousedown', close);
    document.addEventListener('keydown', close);
    return () => {
      document.removeEventListener('mousedown', close);
      document.removeEventListener('keydown', close);
    };
  }, [menu]);

  /** Put the cursor at the start of the target block's text, for Turn into. */
  const focusTarget = useCallback(() => {
    if (!target) return;
    const { state, view } = editor;
    view.dispatch(state.tr.setSelection(Selection.near(state.doc.resolve(target.pos + 1))));
  }, [editor, target]);

  const act = (fn: () => void) => () => {
    fn();
    setMenu(false);
  };

  if (!target) return null;
  const json = target.node.toJSON() as JsonNode;
  const isRaw = target.node.type.name === 'mystBlock';

  return (
    <div
      className="scrive-ed-handle"
      style={{ top: target.top, left: target.left }}
      contentEditable={false}
    >
      <button
        type="button"
        className="scrive-ed-handle-btn"
        title="Add a block below"
        aria-label="Add a block below"
        onClick={() => {
          const at = target.pos + target.node.nodeSize;
          editor
            .chain()
            .insertContentAt(at, { type: 'paragraph', content: [{ type: 'text', text: '/' }] })
            .setTextSelection(at + 2)
            .focus()
            .run();
        }}
      >
        <svg
          width="12"
          height="12"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          aria-hidden="true"
        >
          <path d="M12 5v14M5 12h14" />
        </svg>
      </button>
      <button
        type="button"
        className="scrive-ed-handle-btn"
        title="Drag to move · click for options"
        aria-label="Block options"
        aria-expanded={menu}
        draggable
        onClick={() => setMenu((open) => !open)}
        onDragStart={(event) => {
          const { view, state } = editor;
          const selection = NodeSelection.create(state.doc, target.pos);
          view.dispatch(state.tr.setSelection(selection));
          const slice = selection.content();
          const { dom, text } = view.serializeForClipboard(slice);
          event.dataTransfer.clearData();
          event.dataTransfer.setData('text/html', dom.innerHTML);
          event.dataTransfer.setData('text/plain', text);
          event.dataTransfer.effectAllowed = 'copyMove';
          const blockDom = view.nodeDOM(target.pos);
          if (blockDom instanceof HTMLElement) event.dataTransfer.setDragImage(blockDom, 0, 0);
          view.dragging = { slice, move: true };
        }}
      >
        <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
          {[6, 12, 18].flatMap((y) =>
            [9, 15].map((x) => <circle key={`${x}-${y}`} cx={x} cy={y} r="1.6" />),
          )}
        </svg>
      </button>

      {menu && (
        <div ref={menuRef} className="scrive-ed-menu is-block" role="menu">
          {TEXTUAL.has(target.node.type.name) && (
            <>
              <div className="scrive-ed-menu-group">Turn into</div>
              {TURN_INTO.map((item) => (
                <button
                  key={item.label}
                  type="button"
                  role="menuitem"
                  className="scrive-ed-menu-item"
                  onClick={act(() => {
                    focusTarget();
                    item.run(editor);
                  })}
                >
                  {item.label}
                </button>
              ))}
            </>
          )}
          <div className="scrive-ed-menu-group">Block</div>
          <button
            type="button"
            role="menuitem"
            className="scrive-ed-menu-item"
            onClick={act(() =>
              editor
                .chain()
                .insertContentAt(target.pos + target.node.nodeSize, untagged(json))
                .focus()
                .run(),
            )}
          >
            Duplicate
          </button>
          {isRaw ? (
            <button
              type="button"
              role="menuitem"
              className="scrive-ed-menu-item"
              title="Open the source as rich blocks, where the editor can hold them losslessly"
              onClick={act(() => {
                const blocks = (
                  openDoc(String(target.node.attrs.source ?? '')).doc.content ?? []
                ).map(untagged);
                if (!blocks.length) return;
                editor
                  .chain()
                  .insertContentAt(
                    { from: target.pos, to: target.pos + target.node.nodeSize },
                    blocks,
                  )
                  .focus()
                  .run();
              })}
            >
              Edit as blocks
            </button>
          ) : (
            <button
              type="button"
              role="menuitem"
              className="scrive-ed-menu-item"
              title="Hold this block as MyST source"
              onClick={act(() => {
                const raw = { type: 'mystBlock', attrs: { source: printBlock(json), kind: '' } };
                editor
                  .chain()
                  .insertContentAt({ from: target.pos, to: target.pos + target.node.nodeSize }, raw)
                  .run();
              })}
            >
              Edit as MyST
            </button>
          )}
          <button
            type="button"
            role="menuitem"
            className="scrive-ed-menu-item is-danger"
            onClick={act(() =>
              editor
                .chain()
                .deleteRange({ from: target.pos, to: target.pos + target.node.nodeSize })
                .focus()
                .run(),
            )}
          >
            Delete
          </button>
        </div>
      )}
    </div>
  );
}
