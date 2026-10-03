/**
 * One Scrive page, in four modes: **Write** (the block editor, `editor/WriteView`),
 * **Source** (the MyST text in CodeMirror), **Split** (source beside a live preview)
 * and **Preview** (the page as a reader sees it, at phone / tablet / desktop width).
 * Clicking a block in the preview puts the cursor on its first line; in Split, moving
 * the cursor scrolls the preview to the block it is in.
 *
 * The CodeMirror buffer is the one copy of the text. Write mode opens from it and
 * writes every change back into it (as a minimal edit, so Source's undo still
 * works), so saving, conflicts and the preview never need to know which mode the
 * author typed in. Notebooks (`.ipynb`) have no Write mode.
 *
 * The file on disk is the truth, so this pane's job is mostly honesty about it:
 *
 * - It writes back the line ending it read (`eol.ts`), so a save changes only what
 *   was edited.
 * - Saves carry the revision they were based on. A stale save, or a change on disk
 *   while there are unsaved edits here, raises a banner offering **Load disk
 *   version** or **Keep mine** — never a silent overwrite either way.
 * - A change on disk with nothing unsaved here is simply loaded.
 * - A change an **agent tool** made (`origin: agent`) is loaded too, but as a
 *   reviewable edit: the blocks it changed are marked in Write and Preview, and a
 *   strip offers **Keep** or **Undo**. Consecutive agent writes (an outline being
 *   filled section by section) add up to one review against the text before the
 *   first of them.
 *
 * It also tells the agent what the person is looking at (`useAgentContext`: the
 * page, its revision, the mode, the selection), and lets blocks ask the agent about
 * the page — saving first, so the agent reads what is on screen.
 */
import { markdown } from '@codemirror/lang-markdown';
import { EditorState } from '@codemirror/state';
import { oneDark } from '@codemirror/theme-one-dark';
import { EditorView } from '@codemirror/view';
import { basicSetup } from 'codemirror';
import { lazy, Suspense, useCallback, useContext, useEffect, useRef, useState } from 'react';

import { PaneInstanceContext, useAgentContext } from '../../../agent-context';
import { ApiError } from '../../../api';
import { dialogs } from '../../../dialogs';
import { minibuffer } from '../../../minibuffer';
import { setPaneDirty } from '../../../layout/close-guards';
import { usePaneParams } from '../../../panes';
import { subscribeChannel } from '../../../ws';
import { sendInChat } from '../../agent/openSession';
import {
  critiquePage,
  readPage,
  saveAsTemplate,
  savePage,
  SCRIVE_CHANNEL,
  type Finding,
  type Page,
  type PageChanged,
  type PageMeta,
} from '../api';
import { detectEol } from '../eol';
import { CloseIcon, SaveIcon, SparkIcon } from '../icons';
import { blockChanges, type BlockChanges } from '../myst/changes';
import { openShare } from '../open';
import { selectionPrompt } from '../prompts';
import { PageAgentContext } from '../render/page-agent';
import { registerPageController, type PageMode } from '../state';
import { usePageCells } from '../render/CellOutputs';
import { PreviewCanvas, type Device } from './PreviewCanvas';
import '../scrive.css';

export function PagePanel() {
  const params = usePaneParams();
  const site = String(params.site ?? '');
  const path = String(params.path ?? '');
  if (!site || !path) {
    return <div style={{ padding: 'var(--space-4)', color: 'var(--text-dim)' }}>No page.</div>;
  }
  // Keyed: a retargeted pane (openDocument reuses a clean one) gets a fresh editor.
  return <PageEditor key={`${site}/${path}`} site={site} path={path} />;
}

const MODES: PageMode[] = ['write', 'source', 'split', 'preview'];
// TipTap loads with the first Write view, not with the pane.
const WriteView = lazy(() => import('../editor/WriteView').then((m) => ({ default: m.WriteView })));
const DEVICES: Device[] = ['phone', 'tablet', 'desktop'];
/** How long typing settles before the preview re-parses. */
const PREVIEW_DEBOUNCE_MS = 150;

/** A per-browser preference: guarded, because storage can throw (private mode,
 * sandboxed origins) and losing a remembered choice is harmless. */
function remembered<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
  try {
    const value = globalThis.localStorage?.getItem(key) as T | null;
    return value && allowed.includes(value) ? value : fallback;
  } catch {
    return fallback;
  }
}

function remember(key: string, value: string): void {
  try {
    globalThis.localStorage?.setItem(key, value);
  } catch {
    // see `remembered`
  }
}

type Banner =
  | { kind: 'changed'; page: Page } // newer on disk while we have unsaved edits
  | { kind: 'deleted' }
  | null;

/** The agent's edits since the person last kept or undid them. */
interface AgentReview extends BlockChanges {
  /** The text before the first of them: what Undo restores. */
  base: string;
}

const NO_LINES: readonly number[] = [];

function PageEditor({ site, path }: { site: string; path: string }) {
  const instanceId = useContext(PaneInstanceContext);
  const hostRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);
  const revisionRef = useRef('');
  const dirtyRef = useRef(false);
  const programmaticRef = useRef(false);
  // A ref, not state: two saves fired in one tick (a key repeat, a double click)
  // would both read `saving === false` from the same render.
  const savingRef = useRef(false);

  const [meta, setMeta] = useState<PageMeta | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [dirty, setDirtyState] = useState(false);
  const [saving, setSaving] = useState(false);
  const [banner, setBanner] = useState<Banner>(null);
  const [review, setReviewState] = useState<AgentReview | null>(null);
  const reviewRef = useRef<AgentReview | null>(null);
  const setReview = useCallback((next: AgentReview | null) => {
    reviewRef.current = next;
    setReviewState(next);
  }, []);
  const [findings, setFindings] = useState<Finding[] | null>(null);
  /** What is selected, for the agent's view of this pane. */
  const selectionRef = useRef('');
  const writable = path.endsWith('.md');
  const modes = writable ? MODES : MODES.filter((m) => m !== 'write');
  const [storedMode, setModeState] = useState<PageMode>(() =>
    remembered('scrive.pageMode', MODES, 'write'),
  );
  // A notebook has no Write; it opens as the page a reader sees.
  const mode: PageMode = storedMode === 'write' && !writable ? 'preview' : storedMode;
  /** Bumped when the buffer is replaced from disk: Write re-opens from the new text. */
  const [writeKey, setWriteKey] = useState(0);
  const writeFlushRef = useRef<(() => void) | null>(null);
  /** The page's code cells (outputs, kernel); null for notebooks. */
  const cells = usePageCells(site, path);
  useEffect(() => {
    if (!cells) return;
    // A run syncs the shadow notebook from the buffer, so Write's pending typing
    // goes into it first.
    cells.textSource = () => {
      writeFlushRef.current?.();
      return viewRef.current?.state.doc.toString() ?? '';
    };
  }, [cells]);
  const [device, setDeviceState] = useState<Device>(() =>
    remembered('scrive.previewDevice', DEVICES, 'desktop'),
  );
  /** The text the preview renders: the editor's, debounced. */
  const [previewText, setPreviewText] = useState('');
  const hasCells = previewText.includes('{code-cell}');
  const modeRef = useRef(mode);
  const previewRef = useRef<HTMLDivElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const setMode = useCallback((next: PageMode) => {
    modeRef.current = next;
    setModeState(next);
    remember('scrive.pageMode', next);
  }, []);

  const setDevice = useCallback((next: Device) => {
    setDeviceState(next);
    remember('scrive.previewDevice', next);
  }, []);

  /** In Split, scroll the preview to the block the cursor is in. */
  const syncPreviewTo = useCallback((line: number) => {
    const root = previewRef.current;
    if (!root || modeRef.current !== 'split') return;
    let target: HTMLElement | null = null;
    for (const el of root.querySelectorAll<HTMLElement>('[data-line]')) {
      if (Number(el.dataset.line) <= line) target = el;
      else break;
    }
    target?.scrollIntoView({ block: 'nearest' });
  }, []);

  const setDirty = useCallback(
    (value: boolean) => {
      dirtyRef.current = value;
      setDirtyState(value);
      if (instanceId) setPaneDirty(instanceId, value);
    },
    [instanceId],
  );

  /** Replace the document with `page` as the new clean baseline. */
  const load = useCallback(
    (page: Page) => {
      const host = hostRef.current;
      if (!host) return;
      viewRef.current?.destroy();
      viewRef.current = new EditorView({
        parent: host,
        state: EditorState.create({
          doc: page.content,
          extensions: [
            basicSetup,
            oneDark,
            markdown(),
            EditorView.lineWrapping,
            EditorState.lineSeparator.of(detectEol(page.content)),
            EditorView.updateListener.of((u) => {
              if (u.docChanged) {
                if (!programmaticRef.current) setDirty(true);
                if (debounceRef.current) clearTimeout(debounceRef.current);
                debounceRef.current = setTimeout(
                  () => setPreviewText(u.state.doc.toString()),
                  PREVIEW_DEBOUNCE_MS,
                );
              }
              if (u.selectionSet) {
                syncPreviewTo(u.state.doc.lineAt(u.state.selection.main.head).number);
                const { from, to } = u.state.selection.main;
                if (modeRef.current !== 'write') {
                  selectionRef.current = u.state.sliceDoc(from, to).slice(0, 4000);
                }
              }
            }),
          ],
        }),
      });
      revisionRef.current = page.meta.revision;
      setPreviewText(page.content);
      setMeta(page.meta);
      setDirty(false);
      setBanner(null);
      setWriteKey((k) => k + 1);
      void cells?.refresh();
    },
    [setDirty, syncPreviewTo, cells],
  );

  /** A preview block was clicked: put the cursor on its first line. */
  const revealLine = useCallback(
    (line: number) => {
      const view = viewRef.current;
      if (!view) return;
      if (modeRef.current === 'preview') setMode('split');
      const target = view.state.doc.line(Math.min(Math.max(1, line), view.state.doc.lines));
      view.dispatch({ selection: { anchor: target.from }, scrollIntoView: true });
      // After a mode switch the editor is only visible on the next frame.
      setTimeout(() => view.focus(), 0);
    },
    [setMode],
  );

  /**
   * Take `page` as the disk truth *without* touching the document — for when disk and
   * editor already agree. Identical text is never a conflict: it is the echo of our
   * own save arriving before the save's response, or a second save of the same text.
   * Answers whether it applied.
   */
  const adoptIfSame = useCallback(
    (page: Page): boolean => {
      const view = viewRef.current;
      if (!view || view.state.doc.toString() !== page.content) return false;
      revisionRef.current = page.meta.revision;
      setMeta(page.meta);
      setBanner(null);
      setDirty(false);
      return true;
    },
    [setDirty],
  );

  useEffect(() => {
    let cancelled = false;
    readPage(site, path)
      .then((page) => {
        if (!cancelled) load(page);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(String(e instanceof Error ? e.message : e));
      });
    return () => {
      cancelled = true;
      if (debounceRef.current) clearTimeout(debounceRef.current);
      viewRef.current?.destroy();
      viewRef.current = null;
      if (instanceId) setPaneDirty(instanceId, false);
    };
  }, [site, path, load, instanceId]);

  /** Write mode's change, into the buffer as the smallest edit that makes it so. */
  const applyText = useCallback((text: string) => {
    const view = viewRef.current;
    if (!view) return;
    const current = view.state.doc.toString();
    if (current === text) return;
    let start = 0;
    while (start < current.length && start < text.length && current[start] === text[start]) start++;
    let end = 0;
    while (
      end < current.length - start &&
      end < text.length - start &&
      current[current.length - 1 - end] === text[text.length - 1 - end]
    )
      end++;
    view.dispatch({
      changes: {
        from: start,
        to: current.length - end,
        insert: text.slice(start, text.length - end),
      },
    });
  }, []);

  const save = useCallback(async () => {
    writeFlushRef.current?.();
    const view = viewRef.current;
    if (!view || savingRef.current) return;
    savingRef.current = true;
    setSaving(true);
    try {
      const sent = view.state.doc.toString();
      const result = await savePage(site, path, sent, revisionRef.current);
      if ('conflict' in result) {
        if (!adoptIfSame(result.conflict)) setBanner({ kind: 'changed', page: result.conflict });
        return;
      }
      revisionRef.current = result.page.meta.revision;
      setMeta(result.page.meta);
      setError(null);
      setBanner(null);
      // Only clean if nothing was typed while the save was in flight.
      if (view.state.doc.toString() === sent) setDirty(false);
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    } finally {
      savingRef.current = false;
      setSaving(false);
    }
  }, [site, path, setDirty, adoptIfSame]);

  // Changes on disk: another editor, git, an agent tool — or the echo of our own save.
  useEffect(
    () =>
      subscribeChannel(SCRIVE_CHANNEL, (msg) => {
        if (msg.event !== 'page.changed') return;
        const event = msg.data as PageChanged;
        if (event.site !== site || event.path !== path) return;
        if (event.change !== 'deleted' && event.revision === revisionRef.current) return; // our own save
        // Always re-read rather than trusting the event's verdict: a file replaced in
        // place can be reported as deleted, and only a 404 proves it is gone.
        readPage(site, path).then(
          (page) => {
            if (page.meta.revision === revisionRef.current || adoptIfSame(page)) return;
            if (dirtyRef.current) {
              setBanner({ kind: 'changed', page });
              return;
            }
            if (event.origin === 'agent') {
              // Measured against the text before the first unreviewed agent edit, so
              // a page filled section by section is one review, not the last step.
              const base = reviewRef.current?.base ?? viewRef.current?.state.doc.toString();
              if (base !== undefined) setReview({ base, ...blockChanges(base, page.content) });
            } else {
              // Someone else wrote it: what the agent changed is no longer what is here.
              setReview(null);
            }
            load(page);
          },
          (err: unknown) => {
            if (err instanceof ApiError && err.status === 404) setBanner({ kind: 'deleted' });
          },
        );
      }),
    [site, path, load, adoptIfSame, setReview],
  );

  /** Put `text` in the buffer as the new document and re-open Write on it. */
  const replaceText = useCallback(
    (text: string) => {
      applyText(text);
      setPreviewText(text);
      setWriteKey((k) => k + 1);
    },
    [applyText],
  );

  const undoAgent = useCallback(async () => {
    const current = reviewRef.current;
    if (!current || dirtyRef.current) return;
    replaceText(current.base);
    setReview(null);
    await save();
  }, [replaceText, save, setReview]);

  /** Send the agent a prompt about this page, after saving it: the agent reads the
   * file, so it must be what is on screen. */
  const askAgent = useCallback(
    async (prompt: string) => {
      if (dirtyRef.current) await save();
      if (dirtyRef.current) {
        setError('Save the page before asking the agent about it.');
        return;
      }
      sendInChat(prompt);
    },
    [save],
  );

  const askAboutSelection = useCallback(
    (selection: string, excerpt: string, instruction: string) =>
      void askAgent(selectionPrompt({ site, path, selection, excerpt, instruction })),
    [askAgent, site, path],
  );

  const check = useCallback(async () => {
    try {
      if (dirtyRef.current) await save();
      setFindings(await critiquePage(site, path));
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    }
  }, [save, site, path]);

  const saveTemplate = useCallback(async () => {
    const name = await dialogs.prompt({
      title: 'Save as template',
      message:
        'New pages can start from this one’s structure. The template keeps the text; title and date are left for each new page.',
      defaultValue: meta?.title ?? '',
      placeholder: 'Template name',
      confirmLabel: 'Save template',
    });
    if (!name?.trim()) return;
    const id = name
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .slice(0, 64);
    try {
      if (dirtyRef.current) await save();
      await saveAsTemplate(site, { path, id, name: name.trim() });
      minibuffer.say(`Saved template “${name.trim()}” (templates/${id}.md)`);
    } catch (e) {
      minibuffer.say(String(e instanceof Error ? e.message : e), 'error');
    }
  }, [meta, save, site, path]);

  useAgentContext(() => ({
    kind: 'scrive.page',
    site,
    path,
    title: meta?.title ?? path,
    revision: meta?.revision ?? '',
    mode,
    unsaved: dirty,
    ...(selectionRef.current ? { selection: selectionRef.current } : {}),
    tools: 'Read and edit this page with scrive.readPage / scrive.editPage.',
  }));

  useEffect(() => {
    if (!instanceId) return;
    return registerPageController(instanceId, { save, setMode, saveAsTemplate: saveTemplate });
  }, [instanceId, save, setMode, saveTemplate]);

  const keepMine = useCallback(() => {
    if (banner?.kind === 'changed') {
      // The next save overwrites the disk version on purpose.
      revisionRef.current = banner.page.meta.revision;
    } else {
      revisionRef.current = '';
    }
    setDirty(true);
    setBanner(null);
  }, [banner, setDirty]);

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        minHeight: 0,
        fontSize: 'var(--fs-body)',
      }}
    >
      <header
        className="scrive-bar"
        style={{
          display: 'grid',
          gap: 'var(--space-1)',
          padding: 'var(--space-2) var(--space-3)',
        }}
      >
        {/* Row 1: what this is, and the one action that matters. */}
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'minmax(0, 1fr) auto',
            alignItems: 'center',
            gap: 'var(--space-3)',
          }}
        >
          <div
            className="scrive-head"
            title={meta?.title ?? path}
            style={{
              color: 'var(--text-strong)',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {meta?.title ?? path}
          </div>
          <button
            type="button"
            onClick={() => void save()}
            disabled={!dirty || saving}
            title="Save (mod+s)"
          >
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
              <SaveIcon /> Save
            </span>
          </button>
        </div>
        {/* Row 2: state on the left, view switches on the right; wraps when narrow. */}
        <div
          style={{
            display: 'flex',
            flexWrap: 'wrap',
            alignItems: 'center',
            gap: 'var(--space-2) var(--space-3)',
          }}
        >
          <div
            className="scrive-meta"
            style={{
              display: 'flex',
              gap: 'var(--space-3)',
              flex: '1 1 12rem',
              minWidth: 0,
              whiteSpace: 'nowrap',
              overflow: 'hidden',
            }}
          >
            <span
              title={`${site}/${path}`}
              style={{ overflow: 'hidden', textOverflow: 'ellipsis', minWidth: 0 }}
            >
              {site}/{path}
            </span>
            {meta?.status && <span>{meta.status}</span>}
            {meta && (
              <span title="Revision: a hash of the file's bytes">
                rev {meta.revision.slice(0, 7)}
              </span>
            )}
            <span style={{ color: dirty ? 'var(--warn)' : undefined }}>
              {saving ? 'saving' : dirty ? 'unsaved' : 'saved'}
            </span>
            {cells?.kernelStatus() && (
              <span
                title={cells.error() ?? 'The page’s Python kernel'}
                style={{ color: cells.error() ? 'var(--danger)' : undefined }}
              >
                kernel {cells.kernelStatus()}
              </span>
            )}
          </div>
          {cells && hasCells && (
            <button
              type="button"
              className="scrive-seg-btn scrive-run-all"
              title="Run every code cell, top to bottom"
              onClick={() => void cells.run().catch((e: Error) => setError(e.message))}
            >
              run all
            </button>
          )}
          {writable && (
            <button
              type="button"
              className="scrive-seg-btn"
              title="Review the page: placeholders left, dead links, missing alt text, skipped headings"
              aria-pressed={findings !== null}
              onClick={() => (findings ? setFindings(null) : void check())}
            >
              check
            </button>
          )}
          {writable && (
            <button
              type="button"
              className="scrive-seg-btn"
              title="Draft an X thread, a LinkedIn post or a YouTube upload about this page"
              onClick={() => openShare(site, path)}
            >
              share
            </button>
          )}
          <div className="scrive-seg" role="group" aria-label="Mode">
            {modes.map((m) => (
              <button
                key={m}
                type="button"
                className="scrive-seg-btn"
                aria-pressed={mode === m}
                onClick={() => setMode(m)}
              >
                {m}
              </button>
            ))}
          </div>
          {(mode === 'split' || mode === 'preview') && (
            <div className="scrive-seg" role="group" aria-label="Preview width">
              {DEVICES.map((d) => (
                <button
                  key={d}
                  type="button"
                  className="scrive-seg-btn"
                  aria-pressed={device === d}
                  onClick={() => setDevice(d)}
                >
                  {d}
                </button>
              ))}
            </div>
          )}
        </div>
      </header>

      {banner && (
        <div
          role="alert"
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 'var(--space-3)',
            padding: 'var(--space-2) var(--space-3)',
            borderBottom: '1px solid var(--border)',
            borderLeft: '2px solid var(--warn)',
            background: 'var(--bg-raised)',
          }}
        >
          <span style={{ flex: 1 }}>
            {banner.kind === 'deleted'
              ? 'This file was deleted on disk. Saving will write it back.'
              : 'This file changed on disk while you had unsaved edits here.'}
          </span>
          {banner.kind === 'changed' && (
            <button type="button" onClick={() => load(banner.page)}>
              Load disk version
            </button>
          )}
          <button type="button" onClick={keepMine}>
            Keep mine
          </button>
        </div>
      )}

      {review && (
        <div className="scrive-agent-strip" role="status">
          <SparkIcon size={13} />
          <span className="scrive-head" style={{ color: 'var(--accent)' }}>
            Agent edit
          </span>
          <span className="scrive-meta" style={{ flex: 1, minWidth: 0 }}>
            {describeReview(review)}
          </span>
          <button type="button" onClick={() => setReview(null)} title="Keep the agent’s changes">
            Keep
          </button>
          <button
            type="button"
            onClick={() => void undoAgent()}
            disabled={dirty}
            title={
              dirty
                ? 'Save or discard your own edits first'
                : 'Put the page back as it was before the agent’s edits'
            }
          >
            Undo
          </button>
        </div>
      )}

      {findings && (
        <div className="scrive-findings">
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 'var(--space-2)',
              padding: 'var(--space-1) var(--space-3)',
            }}
          >
            <span className="scrive-head" style={{ flex: 1 }}>
              Review
            </span>
            <span className="scrive-meta">
              {findings.length ? `${findings.length} to look at` : 'nothing to flag'}
            </span>
            <button
              type="button"
              className="btn-mini"
              aria-label="Close review"
              title="Close"
              onClick={() => setFindings(null)}
            >
              <CloseIcon size={12} />
            </button>
          </div>
          {findings.map((f, i) => (
            <button
              key={`${f.line}-${f.rule}-${i}`}
              type="button"
              className="scrive-row"
              data-severity={f.severity}
              style={{ animationDelay: `${Math.min(i, 10) * 18}ms` }}
              onClick={() => revealLine(f.line)}
              title={`Line ${f.line} · ${f.rule}`}
            >
              <span className="scrive-meta" style={{ width: '3.2rem', flex: 'none' }}>
                L{f.line}
              </span>
              <span style={{ flex: 1, minWidth: 0 }}>{f.message}</span>
            </button>
          ))}
        </div>
      )}

      {error && (
        <div style={{ padding: 'var(--space-2) var(--space-3)', color: 'var(--danger)' }}>
          {error}
        </div>
      )}
      <PageAgentContext.Provider value={writable ? (prompt) => void askAgent(prompt) : null}>
        <div
          style={{
            flex: 1,
            minHeight: 0,
            display: 'grid',
            gridTemplateColumns:
              mode === 'split' ? 'minmax(0, 1fr) minmax(0, 1fr)' : 'minmax(0, 1fr)',
          }}
        >
          {/* Always mounted: the editor holds the document, the preview only reads it. */}
          <div
            ref={hostRef}
            style={{
              minHeight: 0,
              overflow: 'auto',
              display: mode === 'preview' || mode === 'write' ? 'none' : undefined,
              borderRight: mode === 'split' ? '1px solid var(--border)' : undefined,
            }}
          />
          {mode === 'write' && viewRef.current && (
            <div style={{ minHeight: 0 }}>
              <Suspense
                fallback={
                  <div style={{ padding: 'var(--space-4)', color: 'var(--text-dim)' }}>
                    Loading editor…
                  </div>
                }
              >
                <WriteView
                  key={writeKey}
                  text={viewRef.current.state.doc.toString()}
                  site={site}
                  path={path}
                  onChange={applyText}
                  onError={setError}
                  flushRef={writeFlushRef}
                  highlight={review && !dirty ? review.lines : NO_LINES}
                  onAsk={askAboutSelection}
                  onSelection={(text) => {
                    selectionRef.current = text;
                  }}
                />
              </Suspense>
            </div>
          )}
          {(mode === 'split' || mode === 'preview') && (
            <div ref={previewRef} style={{ minHeight: 0 }}>
              <PreviewCanvas
                text={previewText}
                site={site}
                path={path}
                device={device}
                onBlockClick={revealLine}
                highlight={review && !dirty ? review.lines : NO_LINES}
              />
            </div>
          )}
        </div>
      </PageAgentContext.Provider>
    </div>
  );
}

function describeReview(review: AgentReview): string {
  const parts: string[] = [];
  if (review.changed)
    parts.push(`${review.changed} block${review.changed === 1 ? '' : 's'} changed`);
  if (review.removed) parts.push(`${review.removed} removed`);
  if (review.frontmatter) parts.push('properties changed');
  return parts.join(' · ') || 'no visible change';
}
