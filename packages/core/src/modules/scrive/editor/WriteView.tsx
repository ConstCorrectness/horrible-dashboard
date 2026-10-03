/**
 * Write mode: the page as blocks — title and properties above, the block editor
 * below — over the same file the Source mode shows.
 *
 * It does not own the text. It opens from the page's current text, and every change
 * goes back out through `onChange` as the whole file, written by `myst/doc.ts`: the
 * blocks the author touched are printed as MyST, everything else is the original
 * bytes. The page pane keeps that text in its CodeMirror buffer, so Source, Preview,
 * save and conflict handling all work on one string.
 *
 * After TipTap loads the document it is checked once more: a block the schema
 * normalised into something that no longer prints back to its source (a mark
 * combination TipTap does not allow, say) is swapped for a raw block before the
 * author can touch it. The baseline `writeDoc` compares against is taken after that.
 *
 * Blocks an agent just changed carry `scrive-agent-changed` (a node decoration, so
 * nothing is written into the document), keyed by the file line their segment
 * started on — the page pane computes those lines (`myst/changes.ts`).
 */
import { Extension, isNodeSelection, type Editor } from '@tiptap/core';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import { Decoration, DecorationSet } from '@tiptap/pm/view';
import { EditorContent, useEditor } from '@tiptap/react';
import { BubbleMenu } from '@tiptap/react/menus';
import { useCallback, useEffect, useMemo, useRef, useState, type MutableRefObject } from 'react';

import { uploadAsset } from '../api';
import { openDoc, sameJson, sourceOf, writeDoc } from '../myst/doc';
import { setField, type FieldValue } from '../myst/frontmatter';
import { readFrontmatter } from '../myst/parse';
import { faithful, printBlock, type PMNode } from '../myst/pm';
import { relativeTo } from '../render/directives';
import { BlockHandle } from './BlockHandle';
import { ScriveDocContext } from './context';
import { scriveExtensions } from './extensions';
import { SlashMenu } from './slash';
import './editor.css';
import '../render/page.css';

/** How long typing settles before the file text is rebuilt. */
const WRITE_DEBOUNCE_MS = 150;

const STATUSES = ['draft', 'review', 'published'];
/** Frontmatter keys the properties strip edits; others are listed as Source-only. */
const EDITED_KEYS = new Set(['title', 'status', 'date', 'tags', 'description']);

export interface WriteViewProps {
  /** The file as it is when Write opens. Remount (key) to take a new one. */
  text: string;
  site: string;
  path: string;
  onChange: (text: string) => void;
  onError: (message: string) => void;
  /** Set to a function that pushes any pending change out synchronously. */
  flushRef: MutableRefObject<(() => void) | null>;
  /** File lines of blocks to mark as changed by the agent. */
  highlight?: readonly number[];
  /** "Ask the agent" from the selection toolbar: the selected blocks as MyST, the
   * exact words selected, and what the person asked. */
  onAsk?: (selection: string, excerpt: string, instruction: string) => void;
  /** The selected text, as it changes (for the agent's view of the pane). */
  onSelection?: (text: string) => void;
}

const AGENT_MARKS = new PluginKey('scriveAgentMarks');

/** Node decorations on the top-level blocks whose segment starts on a marked line. */
function agentMarks(lines: () => ReadonlySet<number>, lineOf: (seg: string) => number | undefined) {
  return Extension.create({
    name: 'scriveAgentMarks',
    addProseMirrorPlugins() {
      return [
        new Plugin({
          key: AGENT_MARKS,
          props: {
            decorations(state) {
              const marked = lines();
              if (!marked.size) return null;
              const decorations: Decoration[] = [];
              state.doc.forEach((node, offset) => {
                const line = lineOf(String(node.attrs.seg ?? ''));
                if (line !== undefined && marked.has(line)) {
                  decorations.push(
                    Decoration.node(offset, offset + node.nodeSize, {
                      class: 'scrive-agent-changed',
                    }),
                  );
                }
              });
              return DecorationSet.create(state.doc, decorations);
            },
          },
        }),
      ];
    },
  });
}

const seg = (block: PMNode) => String(block.attrs?.seg ?? '');

function stripTags(blocks: PMNode[]): PMNode[] {
  return blocks.map((b) => {
    const attrs = { ...(b.attrs ?? {}) };
    delete attrs.seg;
    return { ...b, attrs };
  });
}

/** Text that is probably Markdown, not prose: worth parsing on paste. */
const MARKDOWNISH = /^(#{1,6}\s|[-*+]\s|\d+\.\s|>\s|```|:::|\$\$)|\*\*|\]\(|`|\$[^$\s]/m;

export function WriteView({
  text,
  site,
  path,
  onChange,
  onError,
  flushRef,
  highlight,
  onAsk,
  onSelection,
}: WriteViewProps) {
  const [opened] = useState(() => openDoc(text));
  const marksRef = useRef<ReadonlySet<number>>(new Set(highlight ?? []));
  const onSelectionRef = useRef(onSelection);
  onSelectionRef.current = onSelection;
  const [frontmatter, setFrontmatterState] = useState(opened.base.frontmatter);
  const fmRef = useRef(frontmatter);
  const baselineRef = useRef(new Map<string, PMNode>());
  const lastRef = useRef(text);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const editorRef = useRef<Editor | null>(null);
  const [host, setHost] = useState<HTMLDivElement | null>(null);
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;
  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;

  const emitNow = useCallback(() => {
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = null;
    const editor = editorRef.current;
    if (!editor || editor.isDestroyed) return;
    const out = writeDoc(
      editor.getJSON() as PMNode,
      opened.base,
      baselineRef.current,
      fmRef.current,
    );
    if (out === lastRef.current) return;
    lastRef.current = out;
    onChangeRef.current(out);
  }, [opened]);

  const schedule = useCallback(() => {
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(emitNow, WRITE_DEBOUNCE_MS);
  }, [emitNow]);

  /** Upload files and put them in the page at `pos`: images inline, video as a
   * `{video}` block, anything else as a link. */
  const insertFiles = useCallback(
    async (files: File[], pos: number) => {
      const editor = editorRef.current;
      if (!editor) return;
      const blocks: PMNode[] = [];
      for (const file of files) {
        try {
          const rel = relativeTo(path, await uploadAsset(site, file));
          const name = file.name.replace(/\.[^.]+$/, '');
          if (file.type.startsWith('image/')) {
            blocks.push({
              type: 'paragraph',
              content: [{ type: 'image', attrs: { src: rel, alt: name } }],
            });
          } else if (file.type.startsWith('video/')) {
            blocks.push({
              type: 'mystBlock',
              attrs: { source: `\`\`\`{video} ${rel}\n\`\`\``, kind: 'mystDirective' },
            });
          } else {
            blocks.push({
              type: 'paragraph',
              content: [
                { type: 'text', text: file.name, marks: [{ type: 'link', attrs: { href: rel } }] },
              ],
            });
          }
        } catch (e) {
          onErrorRef.current(
            `Could not add ${file.name}: ${e instanceof Error ? e.message : String(e)}`,
          );
        }
      }
      if (blocks.length) editor.chain().insertContentAt(pos, blocks).focus().run();
    },
    [site, path],
  );

  const extensions = useMemo(
    () => [
      ...scriveExtensions(),
      agentMarks(
        () => marksRef.current,
        (id) => opened.base.segments.get(id)?.line,
      ),
    ],
    [opened],
  );
  const editor = useEditor({
    extensions,
    content: opened.doc,
    shouldRerenderOnTransaction: false,
    editorProps: {
      attributes: { class: 'scrive-page scrive-ed', spellcheck: 'true' },
      handlePaste: (view, event) => {
        const files = [...(event.clipboardData?.files ?? [])];
        if (files.length) {
          void insertFiles(files, view.state.selection.from);
          return true;
        }
        const html = event.clipboardData?.getData('text/html');
        const plain = event.clipboardData?.getData('text/plain') ?? '';
        if (html || !MARKDOWNISH.test(plain)) return false;
        // Markdown from elsewhere (an agent, a README) becomes blocks, not a paragraph
        // of asterisks.
        const blocks = stripTags(openDoc(plain).doc.content ?? []);
        const ed = editorRef.current;
        if (!ed || !blocks.length) return false;
        const single = blocks.length === 1 && blocks[0].type === 'paragraph';
        ed.chain()
          .insertContent(single ? (blocks[0].content ?? []) : blocks)
          .run();
        return true;
      },
      handleDrop: (view, event, _slice, moved) => {
        if (moved) return false;
        const files = [...(event.dataTransfer?.files ?? [])];
        if (!files.length) return false;
        event.preventDefault();
        const at = view.posAtCoords({ left: event.clientX, top: event.clientY });
        void insertFiles(files, at?.pos ?? view.state.selection.from);
        return true;
      },
    },
    onUpdate: schedule,
  });

  // Take the editor TipTap hands back — not the one `onCreate` saw first: in
  // development React mounts twice, and the first instance is destroyed. Each live
  // instance is checked once.
  const preparedRef = useRef<Editor | null>(null);
  useEffect(() => {
    if (!editor || editor.isDestroyed) return;
    editorRef.current = editor;
    if (preparedRef.current === editor) return;
    preparedRef.current = editor;
    // Re-check fidelity on what TipTap actually holds.
    const json = editor.getJSON() as PMNode;
    let swapped = false;
    const content = (json.content ?? []).map((block) => {
      const segment = opened.base.segments.get(seg(block));
      if (!segment?.node || block.type === 'mystBlock' || faithful(segment.node, block)) {
        return block;
      }
      swapped = true;
      return {
        type: 'mystBlock',
        attrs: { source: sourceOf(segment), kind: segment.node.type, seg: segment.id },
      };
    });
    if (swapped) {
      editor
        .chain()
        .command(({ tr }) => {
          tr.setMeta('addToHistory', false);
          return true;
        })
        .setContent({ ...json, content }, { emitUpdate: false })
        .run();
    }
    baselineRef.current = new Map(
      ((editor.getJSON() as PMNode).content ?? []).map((b) => [seg(b), b] as const),
    );
  }, [editor, opened]);

  // New marks (or none, after Keep): redraw the decorations, changing nothing.
  useEffect(() => {
    marksRef.current = new Set(highlight ?? []);
    if (editor && !editor.isDestroyed) {
      editor.view.dispatch(
        editor.state.tr.setMeta(AGENT_MARKS, true).setMeta('addToHistory', false),
      );
    }
  }, [editor, highlight]);

  useEffect(() => {
    if (!editor) return;
    const report = () => {
      const { from, to } = editor.state.selection;
      onSelectionRef.current?.(editor.state.doc.textBetween(from, to, '\n').slice(0, 4000));
    };
    editor.on('selectionUpdate', report);
    return () => {
      editor.off('selectionUpdate', report);
    };
  }, [editor]);

  /** The top-level blocks the selection touches, as MyST: an untouched block is its
   * original source, an edited one is printed. */
  const selectionSource = useCallback((): string => {
    const ed = editorRef.current;
    if (!ed) return '';
    const { from, to } = ed.state.selection;
    const blocks = (ed.getJSON() as PMNode).content ?? [];
    const out: string[] = [];
    ed.state.doc.forEach((node, offset, index) => {
      if (offset + node.nodeSize <= from || offset >= to) return;
      const block = blocks[index];
      if (!block) return;
      const segment = opened.base.segments.get(seg(block));
      const baseline = baselineRef.current.get(seg(block));
      const [plain] = stripTags([block]);
      const unchanged = segment && baseline && sameJson(plain, stripTags([baseline])[0]);
      out.push(unchanged ? sourceOf(segment) : printBlock(plain).replace(/\n+$/, ''));
    });
    return out.join('\n\n');
  }, [opened]);

  useEffect(() => {
    flushRef.current = emitNow;
    return () => {
      // Leaving Write (a mode switch, a reload): nothing typed is lost.
      emitNow();
      flushRef.current = null;
    };
  }, [emitNow, flushRef]);

  const fields = useMemo(() => readFrontmatter(frontmatter), [frontmatter]);
  const setProp = useCallback(
    (key: string, value: FieldValue) => {
      const next = setField(fmRef.current, key, value);
      fmRef.current = next;
      setFrontmatterState(next);
      emitNow();
    },
    [emitNow],
  );

  const doc = useMemo(() => ({ site, pagePath: path }), [site, path]);

  return (
    <ScriveDocContext.Provider value={doc}>
      <div ref={setHost} className="scrive-ed-host">
        <div className="scrive-ed-column">
          <Properties fields={fields} onSet={setProp} />
          {editor && <BlockHandle editor={editor} host={host} />}
          <EditorContent editor={editor} />
          {editor && (
            <SelectionBar
              editor={editor}
              onAsk={
                onAsk
                  ? (instruction) => {
                      const { from, to } = editor.state.selection;
                      onAsk(
                        selectionSource(),
                        editor.state.doc.textBetween(from, to, '\n'),
                        instruction,
                      );
                    }
                  : undefined
              }
            />
          )}
        </div>
      </div>
      <SlashMenu />
    </ScriveDocContext.Provider>
  );
}

// ── properties ─────────────────────────────────────────────────────────────────

function Properties({
  fields,
  onSet,
}: {
  fields: Record<string, unknown>;
  onSet: (key: string, value: FieldValue) => void;
}) {
  const str = (key: string) => (typeof fields[key] === 'string' ? (fields[key] as string) : '');
  const tags = Array.isArray(fields.tags) ? fields.tags.map(String) : [];
  const [title, setTitle] = useState(str('title'));
  const [tagText, setTagText] = useState(tags.join(', '));
  const [description, setDescription] = useState(str('description'));
  const status = str('status');
  const others = Object.keys(fields).filter((k) => !EDITED_KEYS.has(k));

  const commitTags = () => {
    const next = tagText
      .split(',')
      .map((t) => t.trim())
      .filter(Boolean);
    if (next.join(',') !== tags.join(',')) onSet('tags', next);
  };

  return (
    <div className="scrive-ed-props">
      <input
        type="text"
        className="scrive-ed-title"
        aria-label="Title"
        placeholder="Untitled"
        value={title}
        onChange={(e) => {
          setTitle(e.target.value);
          onSet('title', e.target.value);
        }}
      />
      <div className="scrive-ed-propgrid">
        <label className="scrive-ed-prop">
          <span className="scrive-head">Status</span>
          <select
            value={status}
            onChange={(e) => onSet('status', e.target.value || null)}
            style={{ padding: '0 0.6rem' }}
          >
            <option value="">—</option>
            {[...new Set([...STATUSES, ...(status ? [status] : [])])].map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <label className="scrive-ed-prop">
          <span className="scrive-head">Date</span>
          <input
            type="date"
            value={/^\d{4}-\d{2}-\d{2}$/.test(str('date')) ? str('date') : ''}
            onChange={(e) => onSet('date', e.target.value || null)}
            style={{ padding: '0 0.6rem' }}
          />
        </label>
        <label className="scrive-ed-prop is-wide">
          <span className="scrive-head">Tags</span>
          <input
            type="text"
            placeholder="comma, separated"
            value={tagText}
            onChange={(e) => setTagText(e.target.value)}
            onBlur={commitTags}
            onKeyDown={(e) => {
              if (e.key === 'Enter') commitTags();
            }}
            style={{ padding: '0 0.6rem' }}
          />
        </label>
        <label className="scrive-ed-prop is-full">
          <span className="scrive-head">Description</span>
          <input
            type="text"
            placeholder="One sentence for link cards and the post list"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            onBlur={() => {
              if (description !== str('description')) onSet('description', description || null);
            }}
            style={{ padding: '0 0.6rem' }}
          />
        </label>
      </div>
      {others.length > 0 && (
        <div className="scrive-meta" title="Edit these in Source">
          also in frontmatter: {others.join(', ')}
        </div>
      )}
    </div>
  );
}

// ── selection toolbar ──────────────────────────────────────────────────────────

function SelectionBar({
  editor,
  onAsk,
}: {
  editor: Editor;
  onAsk?: (instruction: string) => void;
}) {
  const [linking, setLinking] = useState<string | null>(null);
  const [asking, setAsking] = useState<string | null>(null);
  const mark = (name: string, run: () => void) => (
    <button
      type="button"
      className="scrive-seg-btn"
      aria-pressed={editor.isActive(name)}
      onMouseDown={(e) => e.preventDefault()}
      onClick={run}
    >
      {name === 'bold' ? 'B' : name === 'italic' ? 'I' : name}
    </button>
  );
  return (
    <BubbleMenu
      editor={editor}
      shouldShow={({ editor: ed, from, to }) =>
        from !== to && !ed.isActive('codeBlock') && !isNodeSelection(ed.state.selection)
      }
      className="scrive-ed-bubble"
    >
      {asking !== null ? (
        <form
          className="scrive-ed-linkform scrive-ed-askform"
          onSubmit={(e) => {
            e.preventDefault();
            if (asking.trim()) onAsk?.(asking.trim());
            setAsking(null);
          }}
        >
          <input
            type="text"
            aria-label="Ask the agent about the selection"
            placeholder="Ask the agent: tighten this, add an example, cite a source…"
            value={asking}
            autoFocus
            onChange={(e) => setAsking(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Escape') setAsking(null);
            }}
            style={{ padding: '0 0.6rem' }}
          />
        </form>
      ) : linking === null ? (
        <div className="scrive-seg" role="toolbar" aria-label="Format">
          {mark('bold', () => editor.chain().focus().toggleBold().run())}
          {mark('italic', () => editor.chain().focus().toggleItalic().run())}
          {mark('code', () => editor.chain().focus().toggleCode().run())}
          <button
            type="button"
            className="scrive-seg-btn"
            aria-pressed={editor.isActive('link')}
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => setLinking(String(editor.getAttributes('link').href ?? ''))}
          >
            link
          </button>
          <button
            type="button"
            className="scrive-seg-btn"
            title="Turn the selected text into inline math"
            onMouseDown={(e) => e.preventDefault()}
            onClick={() => {
              const { from, to } = editor.state.selection;
              const value = editor.state.doc.textBetween(from, to);
              editor
                .chain()
                .focus()
                .insertContentAt({ from, to }, { type: 'mathInline', attrs: { value } })
                .run();
            }}
          >
            math
          </button>
          {onAsk && (
            <button
              type="button"
              className="scrive-seg-btn"
              title="Ask the agent about the selection"
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => setAsking('')}
            >
              ask
            </button>
          )}
        </div>
      ) : (
        <form
          className="scrive-ed-linkform"
          onSubmit={(e) => {
            e.preventDefault();
            const chain = editor.chain().focus().extendMarkRange('link');
            if (linking.trim()) chain.setLink({ href: linking.trim() }).run();
            else chain.unsetLink().run();
            setLinking(null);
          }}
        >
          <input
            type="text"
            aria-label="Link address"
            placeholder="https://… or ../other-page.md"
            value={linking}
            autoFocus
            onChange={(e) => setLinking(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Escape') setLinking(null);
            }}
            style={{ padding: '0 0.6rem' }}
          />
        </form>
      )}
    </BubbleMenu>
  );
}
