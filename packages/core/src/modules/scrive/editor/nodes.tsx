/**
 * The block editor's own nodes — the MyST constructs StarterKit does not have — and
 * their React node views.
 *
 * | Node          | MyST                                   | Edited as                         |
 * | ------------- | -------------------------------------- | --------------------------------- |
 * | `mathInline`  | `$…$`                                  | KaTeX; TeX field when selected    |
 * | `mathBlock`   | `$$…$$`, `{math}`                      | KaTeX; TeX + label when selected  |
 * | `admonition`  | `{note}` … `{danger}`, `{dropdown}`    | a box of blocks, kind and title   |
 * | `mystRole`    | `` {name}`value` ``                    | chip; name + value when selected  |
 * | `footnoteRef` | `[^label]`                             | chip (definitions are in Source)  |
 * | `htmlInline`  | `<kbd>`                                | chip                              |
 * | `image`       | `![alt](src)`                          | the image; src + alt when selected|
 * | `codeCell`    | `{code-cell}`                          | source, Run (Shift+Enter), outputs|
 * | `r3fScene`    | `{r3f} scenes/x.tsx`                   | the live scene; poster, recording |
 * | `mystBlock`   | anything else, verbatim                | rendered; its source when selected|
 *
 * `mystBlock` is how the editor stays lossless: a directive or construct it does not
 * model is held as source text and rendered by the same `MystView` as the Preview.
 *
 * Every input inside a node view is a real `<input>`/`<textarea>`, which TipTap's
 * node views already keep ProseMirror's hands off.
 */
import { InputRule, mergeAttributes, Node, type NodeViewProps } from '@tiptap/core';
import {
  NodeViewContent,
  NodeViewWrapper,
  ReactNodeViewRenderer,
  type ReactNodeViewProps,
} from '@tiptap/react';
import { useEffect, useMemo, useRef, useState } from 'react';

import { renderMath } from '../../../notebook/math';
import { parseMyst } from '../myst/parse';
import { CONTAINERS } from '../myst/pm';
import { createClip, uploadAsset } from '../api';
import { openClip } from '../open';
import { CellOutputs, CellRunBar, usePageCells } from '../render/CellOutputs';
import { relativeTo } from '../render/directives';
import { SceneFrame } from '../render/SceneFrame';
import { fetchSpaceInfo, parseSpaceRef } from '../render/space';
import { SpaceEmbed } from '../render/SpaceEmbed';
import { AppFrame, parseAppRef } from '../render/AppFrame';
import { listApps, type AppInfo } from '../api';
import { openApp } from '../open';
import { TokenViz } from '../render/TokenViz';
import { WebLlm } from '../render/WebLlm';
import type { TokenRun } from '../../../token-strip/TokenStrip';
import { assetUrl } from '../render/directives';
import { MystView } from '../render/MystView';
import { useScriveDoc } from './context';

type ViewProps = ReactNodeViewProps<HTMLElement> & NodeViewProps;

/** KaTeX for the editor: its error box rather than a throw. */
function MathHtml({ tex, display }: { tex: string; display: boolean }) {
  const html = useMemo(() => {
    try {
      return renderMath(tex || '\\square', display);
    } catch {
      return '';
    }
  }, [tex, display]);
  // KaTeX output with `trust: false` (notebook/math.ts) — escaped at its source.
  return <span dangerouslySetInnerHTML={{ __html: html }} />;
}

/** Leave a node view's field and put the cursor just after the node. */
function exitAfter(props: ViewProps) {
  const pos = props.getPos();
  if (typeof pos === 'number') {
    props.editor
      .chain()
      .focus()
      .setTextSelection(pos + props.node.nodeSize)
      .run();
  }
}

function fieldKeys(props: ViewProps) {
  return (e: React.KeyboardEvent) => {
    if (
      e.key === 'Escape' ||
      (e.key === 'Enter' && !e.shiftKey && e.currentTarget.tagName === 'INPUT')
    ) {
      e.preventDefault();
      exitAfter(props);
    }
  };
}

/**
 * Whether a node's fields are open, and a ref for the one to focus. They open when
 * the author selects the node — not when the editor merely starts with it selected
 * (a page whose first block is an atom opens with a node selection on it, and
 * popping its source open would steal focus from wherever the author was).
 */
function useEditing(props: ViewProps) {
  const ref = useRef<HTMLInputElement & HTMLTextAreaElement>(null);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!props.selected) setOpen(false);
    else if (props.editor.isFocused) setOpen(true);
  }, [props.selected, props.editor]);
  useEffect(() => {
    if (open) ref.current?.focus();
  }, [open]);
  return { open, ref };
}

// ── math ───────────────────────────────────────────────────────────────────────

function MathInlineView(props: ViewProps) {
  const value = String(props.node.attrs.value ?? '');
  const { open, ref } = useEditing(props);
  return (
    <NodeViewWrapper as="span" className="scrive-ed-math-inline" data-selected={props.selected}>
      {open ? (
        <input
          ref={ref}
          type="text"
          className="scrive-ed-field is-inline"
          aria-label="Inline math (TeX)"
          value={value}
          size={Math.max(4, value.length + 1)}
          onChange={(e) => props.updateAttributes({ value: e.target.value })}
          onKeyDown={fieldKeys(props)}
        />
      ) : (
        <MathHtml tex={value} display={false} />
      )}
    </NodeViewWrapper>
  );
}

export const MathInline = Node.create({
  name: 'mathInline',
  group: 'inline',
  inline: true,
  atom: true,
  selectable: true,
  addAttributes() {
    return {
      value: {
        default: '',
        parseHTML: (el) => el.getAttribute('data-value') ?? '',
        renderHTML: (a) => ({ 'data-value': a.value }),
      },
    };
  },
  parseHTML() {
    return [{ tag: 'span[data-math-inline]' }];
  },
  renderHTML({ HTMLAttributes, node }) {
    return [
      'span',
      mergeAttributes({ 'data-math-inline': '' }, HTMLAttributes),
      `$${node.attrs.value}$`,
    ];
  },
  renderText({ node }) {
    return `$${String(node.attrs.value)}$`;
  },
  addNodeView() {
    return ReactNodeViewRenderer(MathInlineView);
  },
  addInputRules() {
    // `$x^2$` typed in prose becomes inline math. Not `\$`, not `$ x$`, not `$x $`.
    return [
      new InputRule({
        find: /(^|[^$\\])\$([^$\s](?:[^$]*[^$\s\\])?)\$$/,
        handler: ({ state, range, match }) => {
          const from = range.from + match[1].length;
          state.tr.replaceWith(from, range.to, this.type.create({ value: match[2] }));
        },
      }),
    ];
  },
});

function MathBlockView(props: ViewProps) {
  const value = String(props.node.attrs.value ?? '');
  const label = String(props.node.attrs.label ?? '');
  const { open, ref } = useEditing(props);
  return (
    <NodeViewWrapper className="scrive-ed-block scrive-ed-math" data-selected={props.selected}>
      <div className="scrive-math" contentEditable={false}>
        <MathHtml tex={value} display />
        {label && <span className="scrive-ed-label">({label})</span>}
      </div>
      {open && (
        <div className="scrive-ed-fields" contentEditable={false}>
          <textarea
            ref={ref}
            className="scrive-ed-field is-code"
            aria-label="Display math (TeX)"
            value={value}
            rows={Math.max(2, value.split('\n').length)}
            onChange={(e) => props.updateAttributes({ value: e.target.value })}
            onKeyDown={fieldKeys(props)}
          />
          <input
            type="text"
            className="scrive-ed-field"
            aria-label="Equation label"
            placeholder="label (for {eq} references)"
            value={label}
            onChange={(e) => props.updateAttributes({ label: e.target.value || null })}
            onKeyDown={fieldKeys(props)}
          />
        </div>
      )}
    </NodeViewWrapper>
  );
}

export const MathBlock = Node.create({
  name: 'mathBlock',
  group: 'block',
  atom: true,
  selectable: true,
  draggable: true,
  addAttributes() {
    return {
      value: { default: '' },
      label: { default: null },
      // `$$` or the `{math}` directive — kept as it was written.
      form: { default: 'dollars' },
    };
  },
  parseHTML() {
    return [{ tag: 'div[data-math-block]' }];
  },
  renderHTML({ HTMLAttributes, node }) {
    return [
      'div',
      mergeAttributes({ 'data-math-block': '' }, HTMLAttributes),
      `$$${node.attrs.value}$$`,
    ];
  },
  renderText({ node }) {
    return `$$\n${String(node.attrs.value)}\n$$`;
  },
  addNodeView() {
    return ReactNodeViewRenderer(MathBlockView);
  },
  addInputRules() {
    // `$$` and a space at the start of an empty paragraph opens an equation.
    return [
      new InputRule({
        find: /^\$\$\s$/,
        handler: ({ state, range }) => {
          const $from = state.doc.resolve(range.from);
          const start = $from.before();
          state.tr.replaceWith(start, $from.after(), this.type.create());
        },
      }),
    ];
  },
});

// ── admonitions ────────────────────────────────────────────────────────────────

/** The visual kind for a container's name (the page CSS has one per family). */
const kindOf = (name: string) => (name === 'admonition' || name === 'dropdown' ? 'note' : name);

function AdmonitionView(props: ViewProps) {
  const name = String(props.node.attrs.name ?? 'note');
  const args = String(props.node.attrs.args ?? '');
  const options = (props.node.attrs.options as Record<string, unknown> | null) ?? {};
  const collapsible =
    name === 'dropdown' ||
    String(options.class ?? '')
      .split(/\s+/)
      .includes('dropdown');
  const setCollapsible = (on: boolean) => {
    const classes = String(options.class ?? '')
      .split(/\s+/)
      .filter((c) => c && c !== 'dropdown');
    if (on) classes.push('dropdown');
    const next = { ...options };
    if (classes.length) next.class = classes.join(' ');
    else delete next.class;
    props.updateAttributes({ options: next });
  };
  return (
    <NodeViewWrapper
      as="aside"
      className={`scrive-ed-block scrive-admonition is-${kindOf(name)}`}
      data-selected={props.selected}
    >
      <div className="scrive-ed-admonition-head" contentEditable={false}>
        <select
          aria-label="Callout kind"
          className="scrive-ed-kind"
          value={name}
          onChange={(e) => props.updateAttributes({ name: e.target.value })}
        >
          {CONTAINERS.map((kind) => (
            <option key={kind} value={kind}>
              {kind}
            </option>
          ))}
        </select>
        <input
          type="text"
          className="scrive-ed-field is-title"
          aria-label="Callout title"
          placeholder={name === 'admonition' || name === 'dropdown' ? 'Title' : kindOf(name)}
          value={args}
          onChange={(e) => props.updateAttributes({ args: e.target.value })}
        />
        {name !== 'dropdown' && (
          <label className="scrive-meta scrive-ed-check">
            <input
              type="checkbox"
              checked={collapsible}
              onChange={(e) => setCollapsible(e.target.checked)}
            />
            collapsible
          </label>
        )}
      </div>
      <NodeViewContent className="scrive-admonition-body" />
    </NodeViewWrapper>
  );
}

export const Admonition = Node.create({
  name: 'admonition',
  group: 'block',
  content: 'block+',
  defining: true,
  draggable: true,
  addAttributes() {
    return {
      name: {
        default: 'note',
        parseHTML: (el) => el.getAttribute('data-name') ?? 'note',
        renderHTML: (a) => ({ 'data-name': a.name }),
      },
      args: {
        default: '',
        parseHTML: (el) => el.getAttribute('data-args') ?? '',
        renderHTML: (a) => ({ 'data-args': a.args }),
      },
      options: {
        default: {},
        parseHTML: (el) => {
          try {
            return JSON.parse(el.getAttribute('data-options') ?? '{}') as Record<string, unknown>;
          } catch {
            return {};
          }
        },
        renderHTML: (a) => ({ 'data-options': JSON.stringify(a.options ?? {}) }),
      },
      // The fence it was written with: `:::` or backticks.
      fence: { default: ':', rendered: false },
    };
  },
  parseHTML() {
    return [{ tag: 'aside[data-admonition]' }];
  },
  renderHTML({ HTMLAttributes }) {
    return ['aside', mergeAttributes({ 'data-admonition': '' }, HTMLAttributes), 0];
  },
  addNodeView() {
    return ReactNodeViewRenderer(AdmonitionView);
  },
});

// ── inline chips ───────────────────────────────────────────────────────────────

function RoleView(props: ViewProps) {
  const name = String(props.node.attrs.name ?? '');
  const value = String(props.node.attrs.value ?? '');
  const { open, ref } = useEditing(props);
  return (
    <NodeViewWrapper as="span" className="scrive-ed-chip" data-selected={props.selected}>
      {open ? (
        <>
          {'{'}
          <input
            type="text"
            className="scrive-ed-field is-inline"
            aria-label="Role name"
            value={name}
            size={Math.max(2, name.length + 1)}
            onChange={(e) => props.updateAttributes({ name: e.target.value })}
            onKeyDown={fieldKeys(props)}
          />
          {'}'}
          <input
            ref={ref}
            type="text"
            className="scrive-ed-field is-inline"
            aria-label="Role value"
            value={value}
            size={Math.max(4, value.length + 1)}
            onChange={(e) => props.updateAttributes({ value: e.target.value })}
            onKeyDown={fieldKeys(props)}
          />
        </>
      ) : (
        <>
          <span className="scrive-ed-chip-name">{name}</span>
          {value}
        </>
      )}
    </NodeViewWrapper>
  );
}

export const MystRole = Node.create({
  name: 'mystRole',
  group: 'inline',
  inline: true,
  atom: true,
  selectable: true,
  addAttributes() {
    return { name: { default: '' }, value: { default: '' } };
  },
  parseHTML() {
    return [{ tag: 'span[data-myst-role]' }];
  },
  renderHTML({ HTMLAttributes, node }) {
    return [
      'span',
      mergeAttributes({ 'data-myst-role': '' }, HTMLAttributes),
      `{${node.attrs.name}}\`${node.attrs.value}\``,
    ];
  },
  renderText({ node }) {
    return `{${String(node.attrs.name)}}\`${String(node.attrs.value)}\``;
  },
  addNodeView() {
    return ReactNodeViewRenderer(RoleView);
  },
});

export const FootnoteRef = Node.create({
  name: 'footnoteRef',
  group: 'inline',
  inline: true,
  atom: true,
  selectable: true,
  addAttributes() {
    return { label: { default: '' } };
  },
  parseHTML() {
    return [
      {
        tag: 'sup[data-footnote]',
        getAttrs: (el) => ({ label: el.getAttribute('data-footnote') }),
      },
    ];
  },
  renderHTML({ node }) {
    return [
      'sup',
      {
        'data-footnote': node.attrs.label,
        class: 'scrive-ed-chip',
        title: 'Footnote — its text is in Source',
      },
      `[^${node.attrs.label}]`,
    ];
  },
  renderText({ node }) {
    return `[^${String(node.attrs.label)}]`;
  },
});

export const HtmlInline = Node.create({
  name: 'htmlInline',
  group: 'inline',
  inline: true,
  atom: true,
  selectable: true,
  addAttributes() {
    return { value: { default: '' } };
  },
  parseHTML() {
    return [{ tag: 'code[data-html-inline]', getAttrs: (el) => ({ value: el.textContent ?? '' }) }];
  },
  renderHTML({ node }) {
    return [
      'code',
      { 'data-html-inline': '', class: 'scrive-ed-chip is-html' },
      String(node.attrs.value),
    ];
  },
  renderText({ node }) {
    return String(node.attrs.value);
  },
});

// ── images ─────────────────────────────────────────────────────────────────────

function ImageView(props: ViewProps) {
  const { site, pagePath } = useScriveDoc();
  const src = String(props.node.attrs.src ?? '');
  const alt = String(props.node.attrs.alt ?? '');
  const { open, ref } = useEditing(props);
  return (
    <NodeViewWrapper as="span" className="scrive-ed-image" data-selected={props.selected}>
      <img src={assetUrl(site, pagePath, src)} alt={alt} draggable={false} />
      {open && (
        <span className="scrive-ed-fields is-image" contentEditable={false}>
          <input
            type="text"
            className="scrive-ed-field"
            aria-label="Image path"
            value={src}
            onChange={(e) => props.updateAttributes({ src: e.target.value })}
            onKeyDown={fieldKeys(props)}
          />
          <input
            ref={ref}
            type="text"
            className="scrive-ed-field"
            aria-label="Alt text"
            placeholder="Alt text — what the image shows"
            value={alt}
            onChange={(e) => props.updateAttributes({ alt: e.target.value })}
            onKeyDown={fieldKeys(props)}
          />
        </span>
      )}
    </NodeViewWrapper>
  );
}

export const Image = Node.create({
  name: 'image',
  group: 'inline',
  inline: true,
  atom: true,
  selectable: true,
  draggable: true,
  addAttributes() {
    return { src: { default: '' }, alt: { default: '' }, title: { default: null } };
  },
  parseHTML() {
    return [{ tag: 'img[src]' }];
  },
  renderHTML({ HTMLAttributes }) {
    return ['img', HTMLAttributes];
  },
  addNodeView() {
    return ReactNodeViewRenderer(ImageView);
  },
});

// ── code cells ─────────────────────────────────────────────────────────────────

/** Which occurrence of its own source this cell is, counting from the top. */
function occurrenceOf(props: ViewProps, source: string): number {
  const at = props.getPos();
  if (typeof at !== 'number') return 0;
  let count = 0;
  props.editor.state.doc.nodesBetween(0, at, (node, pos) => {
    if (pos < at && node.type.name === 'codeCell' && node.attrs.source === source) count++;
  });
  return count;
}

function CodeCellView(props: ViewProps) {
  const { site, pagePath } = useScriveDoc();
  const cells = usePageCells(site, pagePath);
  const source = String(props.node.attrs.source ?? '');
  const language = String(props.node.attrs.language ?? '');
  const occurrence = occurrenceOf(props, source);
  return (
    <NodeViewWrapper className="scrive-ed-block scrive-cell" data-selected={props.selected}>
      <CellRunBar cells={cells} source={source} occurrence={occurrence} language={language} />
      <div contentEditable={false}>
        <textarea
          className="scrive-ed-field is-code scrive-cell-source"
          aria-label="Code cell source"
          spellCheck={false}
          value={source}
          rows={Math.max(1, source.split('\n').length)}
          onChange={(e) => props.updateAttributes({ source: e.target.value })}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && e.shiftKey) {
              e.preventDefault();
              void cells?.run({ source, occurrence });
            } else if (e.key === 'Tab' && !e.shiftKey) {
              // Indent, as a code editor does, rather than leave the cell.
              e.preventDefault();
              const el = e.currentTarget;
              const { selectionStart: a, selectionEnd: b } = el;
              const next = `${source.slice(0, a)}    ${source.slice(b)}`;
              props.updateAttributes({ source: next });
              requestAnimationFrame(() => el.setSelectionRange(a + 4, a + 4));
            } else if (e.key === 'Escape') {
              e.preventDefault();
              exitAfter(props);
            }
          }}
        />
      </div>
      <CellOutputs cells={cells} source={source} occurrence={occurrence} />
    </NodeViewWrapper>
  );
}

export const CodeCell = Node.create({
  name: 'codeCell',
  group: 'block',
  atom: true,
  selectable: true,
  draggable: true,
  addAttributes() {
    return {
      source: {
        default: '',
        parseHTML: (el) => el.getAttribute('data-source') ?? '',
        renderHTML: (a) => ({ 'data-source': a.source }),
      },
      language: {
        default: 'python',
        parseHTML: (el) => el.getAttribute('data-language') ?? 'python',
        renderHTML: (a) => ({ 'data-language': a.language }),
      },
      options: { default: {}, rendered: false },
    };
  },
  parseHTML() {
    return [{ tag: 'div[data-code-cell]' }];
  },
  renderHTML({ HTMLAttributes }) {
    return ['div', mergeAttributes({ 'data-code-cell': '' }, HTMLAttributes)];
  },
  renderText({ node }) {
    return String(node.attrs.source);
  },
  addNodeView() {
    return ReactNodeViewRenderer(CodeCellView);
  },
});

// ── 3D scenes ──────────────────────────────────────────────────────────────────

async function dataUrlFile(dataUrl: string, name: string): Promise<File> {
  const blob = await (await fetch(dataUrl)).blob();
  return new File([blob], name, { type: blob.type });
}

function R3fSceneView(props: ViewProps) {
  const { site, pagePath } = useScriveDoc();
  const src = String(props.node.attrs.src ?? '');
  const options = (props.node.attrs.options as Record<string, unknown> | null) ?? {};
  const [note, setNote] = useState<string | null>(null);
  const { open, ref } = useEditing(props);
  const stem = (src.split('/').pop() ?? 'scene').replace(/\.[^.]+$/, '');
  const setOption = (key: string, value: string | null) => {
    const next = { ...options };
    if (value === null || value === '') delete next[key];
    else next[key] = value;
    props.updateAttributes({ options: next });
  };
  const save = async (file: File, then: (rel: string) => void) => {
    try {
      then(relativeTo(pagePath, await uploadAsset(site, file)));
    } catch (e) {
      setNote(`Could not save: ${e instanceof Error ? e.message : String(e)}`);
    }
  };
  // A take goes straight into the clip editor: trimming it, cropping it for X and
  // turning it into a GIF is what a five-second recording is for.
  const editTake = async (blob: Blob) => {
    try {
      const file = new File([blob], `${stem}-take.webm`, { type: 'video/webm' });
      const rel = await uploadAsset(site, file);
      const clip = await createClip(site, rel, { page: pagePath });
      setNote(`Recording saved as ${relativeTo(pagePath, rel)}; opened in the clip editor`);
      openClip(site, clip.path);
    } catch (e) {
      setNote(`Could not save: ${e instanceof Error ? e.message : String(e)}`);
    }
  };
  return (
    <NodeViewWrapper className="scrive-ed-block" data-selected={props.selected}>
      <div contentEditable={false}>
        <SceneFrame
          site={site}
          pagePath={pagePath}
          src={src}
          height={Number(options.height) || 360}
          params={options.params}
          onPoster={(dataUrl) =>
            void dataUrlFile(dataUrl, `${stem}-poster.png`).then((file) =>
              save(file, (rel) => {
                setOption('poster', rel);
                setNote(`Poster saved as ${rel}`);
              }),
            )
          }
          onRecorded={(blob) => void editTake(blob)}
        />
        {note && <div className="scrive-meta scrive-scene-note">{note}</div>}
        {open && (
          <div className="scrive-ed-fields is-image">
            <input
              ref={ref}
              type="text"
              className="scrive-ed-field"
              aria-label="Scene file"
              value={src}
              onChange={(e) => props.updateAttributes({ src: e.target.value })}
              onKeyDown={fieldKeys(props)}
            />
            <input
              type="number"
              className="scrive-ed-field"
              aria-label="Height in pixels"
              value={String(options.height ?? '')}
              placeholder="360"
              onChange={(e) => setOption('height', e.target.value)}
              onKeyDown={fieldKeys(props)}
            />
          </div>
        )}
      </div>
    </NodeViewWrapper>
  );
}

export const R3fScene = Node.create({
  name: 'r3fScene',
  group: 'block',
  atom: true,
  selectable: true,
  draggable: true,
  addAttributes() {
    return {
      src: {
        default: '',
        parseHTML: (el) => el.getAttribute('data-src') ?? '',
        renderHTML: (a) => ({ 'data-src': a.src }),
      },
      options: { default: {}, rendered: false },
    };
  },
  parseHTML() {
    return [{ tag: 'div[data-r3f]' }];
  },
  renderHTML({ HTMLAttributes }) {
    return ['div', mergeAttributes({ 'data-r3f': '' }, HTMLAttributes)];
  },
  addNodeView() {
    return ReactNodeViewRenderer(R3fSceneView);
  },
});

// ── live embeds ({space}, …) ───────────────────────────────────────────────────

/** Fields for a `{space}`: its id, and the height of the frame once it is run. */
function SpaceFields({ props, inputRef }: { props: ViewProps; inputRef: React.Ref<HTMLInputElement & HTMLTextAreaElement> }) {
  const src = String(props.node.attrs.src ?? '');
  const options = (props.node.attrs.options as Record<string, unknown> | null) ?? {};
  const [note, setNote] = useState<string | null>(null);
  const setOptions = (next: Record<string, unknown>) => props.updateAttributes({ options: next });
  // The embed host is the Hub's to say (static Spaces live on another domain), so it
  // is looked up once, when the id is settled, and written down as `:host:` — a
  // published page cannot ask.
  const resolve = () => {
    const ref = parseSpaceRef(src);
    if (!ref) {
      setNote('A Space is owner/name, or its huggingface.co/spaces/… URL');
      return;
    }
    if (ref.id !== src) props.updateAttributes({ src: ref.id });
    setNote('Looking up the Space…');
    fetchSpaceInfo(ref.id).then(
      (info) => {
        setOptions({ ...options, host: info.host.replace(/^https:\/\//, '') });
        setNote(`${info.title} · ${info.sdk} Space`);
      },
      (e: unknown) => setNote(e instanceof Error ? e.message : String(e)),
    );
  };
  return (
    <div className="scrive-ed-fields is-image">
      <input
        ref={inputRef}
        type="text"
        className="scrive-ed-field"
        aria-label="Hugging Face Space"
        placeholder="owner/name"
        value={src}
        onChange={(e) => props.updateAttributes({ src: e.target.value })}
        onBlur={resolve}
        onKeyDown={(e) => {
          if (e.key === 'Enter') resolve();
          fieldKeys(props)(e);
        }}
      />
      <input
        type="number"
        className="scrive-ed-field"
        aria-label="Height in pixels"
        value={String(options.height ?? '')}
        placeholder="640"
        onChange={(e) => {
          const next = { ...options };
          if (e.target.value) next.height = e.target.value;
          else delete next.height;
          setOptions(next);
        }}
        onKeyDown={fieldKeys(props)}
      />
      {note && <span className="scrive-meta">{note}</span>}
    </div>
  );
}

/** Fields for an `{app}`: which of the site's apps, and the frame's height. */
function AppFields({
  props,
  inputRef,
}: {
  props: ViewProps;
  inputRef: React.Ref<HTMLInputElement & HTMLTextAreaElement>;
}) {
  const { site } = useScriveDoc();
  const src = String(props.node.attrs.src ?? '');
  const options = (props.node.attrs.options as Record<string, unknown> | null) ?? {};
  const [known, setKnown] = useState<AppInfo[] | null>(null);
  useEffect(() => {
    let live = true;
    listApps(site).then(
      (a) => live && setKnown(a),
      () => live && setKnown([]),
    );
    return () => {
      live = false;
    };
  }, [site]);
  const name = parseAppRef(src);
  const exists = !name || !known || known.some((a) => a.name === name);
  return (
    <div className="scrive-ed-fields is-image">
      <input
        ref={inputRef}
        type="text"
        className="scrive-ed-field"
        aria-label="App folder"
        placeholder="app name (a folder in apps/)"
        list={`scrive-apps-${site}`}
        value={src}
        onChange={(e) => props.updateAttributes({ src: e.target.value })}
        onKeyDown={fieldKeys(props)}
      />
      <datalist id={`scrive-apps-${site}`}>
        {(known ?? []).map((a) => (
          <option key={a.name} value={a.name}>
            {a.title}
          </option>
        ))}
      </datalist>
      <input
        type="number"
        className="scrive-ed-field"
        aria-label="Height in pixels"
        value={String(options.height ?? '')}
        placeholder="600"
        onChange={(e) => {
          const next = { ...options };
          if (e.target.value) next.height = e.target.value;
          else delete next.height;
          props.updateAttributes({ options: next });
        }}
        onKeyDown={fieldKeys(props)}
      />
      {!exists && (
        <button type="button" className="scrive-app-tool" onClick={() => openApp(site)}>
          No apps/{name} yet — make or import one
        </button>
      )}
    </div>
  );
}

function ScriveEmbedView(props: ViewProps) {
  const name = String(props.node.attrs.name ?? '');
  const src = String(props.node.attrs.src ?? '');
  const options = (props.node.attrs.options as Record<string, unknown> | null) ?? {};
  const { open, ref } = useEditing(props);
  const { site, pagePath } = useScriveDoc();
  const app = name === 'app' ? parseAppRef(src) : null;
  // A reply kept from a {webllm} block: its run saved as data/<stem>.json and a
  // {tokenviz} figure inserted right after the block.
  const keep = async (run: TokenRun) => {
    const stem = `${(run.model.split('/').pop() ?? 'model').toLowerCase()}-${Date.now().toString(36)}`;
    const file = new File([JSON.stringify(run, null, 2)], `${stem}.json`, { type: 'application/json' });
    const saved = await uploadAsset(site, file, 'data');
    const pos = props.getPos();
    if (typeof pos !== 'number') return;
    props.editor
      .chain()
      .insertContentAt(pos + props.node.nodeSize, {
        type: 'scriveEmbed',
        attrs: { name: 'tokenviz', src: relativeTo(pagePath, saved), options: {} },
      })
      .run();
  };
  return (
    <NodeViewWrapper className="scrive-ed-block" data-selected={props.selected}>
      <div contentEditable={false}>
        {name === 'space' && <SpaceEmbed arg={src} options={options} />}
        {open && name === 'space' && <SpaceFields props={props} inputRef={ref} />}
        {name === 'app' && (
          <AppFrame
            site={site}
            name={src}
            height={Number(options.height) || 600}
            onPreview={app ? () => openApp(site, app) : undefined}
          />
        )}
        {open && name === 'app' && <AppFields props={props} inputRef={ref} />}
        {name === 'webllm' && <WebLlm arg={src} options={options} onKeep={(run) => void keep(run)} />}
        {name === 'tokenviz' && <TokenViz site={site} pagePath={pagePath} src={src} />}
        {open && (name === 'webllm' || name === 'tokenviz') && (
          <div className="scrive-ed-fields is-image">
            <input
              ref={ref}
              type="text"
              className="scrive-ed-field"
              aria-label={name === 'webllm' ? 'Hugging Face model id' : 'Run file (JSON)'}
              placeholder={name === 'webllm' ? 'owner/model (ONNX)' : 'data/run.json'}
              value={src}
              onChange={(e) => props.updateAttributes({ src: e.target.value })}
              onKeyDown={fieldKeys(props)}
            />
          </div>
        )}
      </div>
    </NodeViewWrapper>
  );
}

/** Scrive's live embeds (`EMBED_DIRECTIVES` in myst/pm.ts): one node, drawn by name. */
export const ScriveEmbed = Node.create({
  name: 'scriveEmbed',
  group: 'block',
  atom: true,
  selectable: true,
  draggable: true,
  addAttributes() {
    return {
      name: {
        default: '',
        parseHTML: (el) => el.getAttribute('data-embed') ?? '',
        renderHTML: (a) => ({ 'data-embed': a.name }),
      },
      src: {
        default: '',
        parseHTML: (el) => el.getAttribute('data-src') ?? '',
        renderHTML: (a) => ({ 'data-src': a.src }),
      },
      options: { default: {}, rendered: false },
    };
  },
  parseHTML() {
    return [{ tag: 'div[data-embed]' }];
  },
  renderHTML({ HTMLAttributes }) {
    return ['div', mergeAttributes(HTMLAttributes)];
  },
  addNodeView() {
    return ReactNodeViewRenderer(ScriveEmbedView);
  },
});

// ── raw MyST ───────────────────────────────────────────────────────────────────

/** What a raw block is, for its label: the directive name, or the construct. */
export function rawLabel(source: string, kind: string): string {
  const directive = /^\s*(?:`{3,}|:{3,})\{([^}]+)\}/.exec(source);
  if (directive) return directive[1];
  if (kind === 'break') return 'block break';
  if (kind === 'mystTarget') return 'label';
  if (kind === 'comment') return 'comment';
  if (kind === 'definitionList') return 'definitions';
  if (kind === 'html') return 'html';
  return kind || 'myst';
}

/** Constructs that render as nothing: show their source instead of a blank. */
const SHOWN_AS_SOURCE = new Set(['break', 'mystTarget', 'comment']);

function MystBlockView(props: ViewProps) {
  const { site, pagePath } = useScriveDoc();
  const source = String(props.node.attrs.source ?? '');
  const kind = String(props.node.attrs.kind ?? '');
  const tree = useMemo(() => parseMyst(source), [source]);
  const { open, ref } = useEditing(props);
  const asSource = SHOWN_AS_SOURCE.has(kind);
  return (
    <NodeViewWrapper className="scrive-ed-block scrive-ed-raw" data-selected={props.selected}>
      <div contentEditable={false}>
        <div className="scrive-ed-raw-label scrive-meta">{rawLabel(source, kind)}</div>
        {open && (
          <textarea
            ref={ref}
            className="scrive-ed-field is-code"
            aria-label="MyST source"
            spellCheck={false}
            value={source}
            rows={Math.min(24, Math.max(2, source.split('\n').length))}
            onChange={(e) => props.updateAttributes({ source: e.target.value })}
            onKeyDown={(e) => {
              if (e.key === 'Escape') {
                e.preventDefault();
                exitAfter(props);
              }
            }}
          />
        )}
        {asSource ? (
          !open && <pre className="scrive-ed-raw-source">{source}</pre>
        ) : (
          <div className="scrive-ed-raw-preview">
            <MystView tree={tree} site={site} pagePath={pagePath} />
          </div>
        )}
      </div>
    </NodeViewWrapper>
  );
}

export const MystBlock = Node.create({
  name: 'mystBlock',
  group: 'block',
  atom: true,
  selectable: true,
  draggable: true,
  addAttributes() {
    return {
      source: {
        default: '',
        parseHTML: (el) => el.getAttribute('data-source') ?? '',
        renderHTML: (a) => ({ 'data-source': a.source }),
      },
      kind: { default: '', rendered: false },
    };
  },
  parseHTML() {
    return [{ tag: 'div[data-myst-block]' }];
  },
  renderHTML({ HTMLAttributes }) {
    return ['div', mergeAttributes({ 'data-myst-block': '' }, HTMLAttributes)];
  },
  renderText({ node }) {
    return String(node.attrs.source);
  },
  addNodeView() {
    return ReactNodeViewRenderer(MystBlockView);
  },
});
