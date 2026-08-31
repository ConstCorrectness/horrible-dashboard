/**
 * A single editor buffer: CodeMirror 6 over a URI source (see sources.ts). Reads
 * its `source` from pane params, loads the content, and saves back through the
 * source model. One panel instance per open buffer; reopening the same source
 * focuses the existing instance (stable instance id in index.tsx).
 *
 * The Mod-s keymap saves directly here; C3 routes editing commands through the
 * shell keybinding service. See docs/modules/editor.md.
 */
import { useContext, useEffect, useRef, useState } from 'react';
import { basicSetup } from 'codemirror';
import { EditorView } from '@codemirror/view';
import { Compartment, EditorState, type Extension } from '@codemirror/state';
import { markdown } from '@codemirror/lang-markdown';
import { oneDark } from '@codemirror/theme-one-dark';
import { unifiedMergeView } from '@codemirror/merge';

import { openContextMenu } from '../../overlay/context-menu';

import {
  clearActiveContextInstance,
  PaneInstanceContext,
  setActiveContextInstance,
  useAgentContext,
} from '../../agent-context';
import { ApiError } from '../../api';
import { dialogs } from '../../dialogs';
import { registerCloseGuard, setPaneDirty } from '../../layout/close-guards';
import { retargetPane } from '../../layout/controller';
import { getLocus, setLocus, subscribeLocus } from '../../locus';
import { registry } from '../../registry';
import { usePaneParams } from '../../panes';
import { useSetting } from '../../settings';
import { isVirtualPath } from '../files/api';
import { getRoots, loadRoots } from '../files/store';
import { autosuggest } from './autosuggest';
import { buildCompletion, completionKeymap, type CompletionTrigger } from './completion';
import {
  extensionForLanguage,
  hasExtension,
  PICKABLE_LANGUAGES,
  resolveLanguage,
} from './language';
import { registerBuffer, type BufferSnapshot } from './buffers';
import { openBuffer, setActiveBufferSource, setActiveSaveAs } from './index';
import { dirOf, lspExtension } from './lsp';
import { readDiagnostics } from './lsp-registry';
import { dirname, joinPath, loadSource, saveSource, sourceTitle } from './sources';
import { dbGhostFetch, indexBuffer, indexBufferNow } from './symbolCompletion';

const FILE_URI = 'workspace-file:';

interface UnsavedState {
  content: string;
  dirty: boolean;
  proposing: boolean;
  original?: string;
  dirtyBeforeProposal?: boolean;
}
const unsavedCache = new Map<string, UnsavedState>();

/** Open a workspace file (go-to-definition target) and reveal it in the tree. The
 * reveal runs once the new buffer is the active one. */
function goToFile(path: string): void {
  openBuffer(`${FILE_URI}${path}`);
  setTimeout(() => void registry.runCommand('files.revealActiveBuffer'), 150);
}

/** The intellisense knobs a buffer reads from settings, captured when it opens (the
 * LSP compartment is reconfigured on load, so changes take effect on reopen). */
interface IntellisenseSettings {
  /** `editor.pythonPath` — overrides the backend's auto-detected interpreter. */
  pythonPath?: string;
  /** `editor.frameworkImports` — the curated framework-import completions. */
  frameworkImports?: boolean;
  /** `editor.indexedSymbols` — the indexed stdlib/package symbols. */
  indexedSymbols?: boolean;
  /** `editor.completionWarmupMs` — cold-start wait before falling back. */
  warmupMs?: number;
  /** `editor.changeDebounceMs` — edit push debounce. */
  changeDebounceMs?: number;
  /** `editor.diagnostics` — render diagnostics in the gutter. */
  diagnostics?: boolean;
  /** `editor.hover` — hover tooltips. */
  hover?: boolean;
  /** `editor.importCompletions` — module/member completion in import statements. */
  importCompletions?: boolean;
  /** `editor.completionTrigger` — popup on typing, or only on Tab/Ctrl-Space. */
  trigger?: CompletionTrigger;
}

/**
 * Whether this buffer gets a language server: only a `workspace-file:` in a language
 * the backend has a server for. Everything else — notes, Drive files, and every
 * unsaved buffer — gets the standalone completion stack instead.
 *
 * The two are mutually exclusive by construction, and that is load-bearing: both
 * configure `autocompletion()`, whose `override` is a *replacing* field, so two live
 * instances mean one silently wins and the other's sources vanish with no error.
 */
function usesLanguageServer(source: string | null, lspId: string | null): boolean {
  return !!source && source.startsWith(FILE_URI) && !!lspId;
}

/** The LSP extension for a just-loaded source, or `[]` when {@link usesLanguageServer}
 * says no. */
function lspFor(
  source: string | null,
  lspId: string | null,
  settings: IntellisenseSettings,
): Extension {
  if (!usesLanguageServer(source, lspId) || !source || !lspId) return [];
  const path = source.slice(FILE_URI.length);
  return lspExtension({
    path,
    languageId: lspId,
    root: dirOf(path),
    bufferUri: source,
    openFile: goToFile,
    pythonPathOverride: settings.pythonPath || undefined,
    frameworkImports: settings.frameworkImports,
    indexedSymbols: settings.indexedSymbols,
    importCompletions: settings.importCompletions,
    trigger: settings.trigger,
    warmupMs: settings.warmupMs,
    changeDebounceMs: settings.changeDebounceMs,
    diagnostics: settings.diagnostics,
    hover: settings.hover,
  });
}

/** The standalone completion stack for a buffer with no language server — the same
 * sources, minus the one the server would have provided. `[]` when the LSP owns it. */
function completionFor(
  source: string | null,
  lspId: string | null,
  settings: IntellisenseSettings,
): Extension {
  if (usesLanguageServer(source, lspId)) return [];
  return buildCompletion({
    languageId: lspId,
    indexedSymbols: settings.indexedSymbols,
    frameworkImports: settings.frameworkImports,
    importCompletions: settings.importCompletions,
    trigger: settings.trigger,
  });
}

/**
 * The workspace directory an untitled buffer saves into: the **first real
 * workspace root**, which is also what the backend anchors a relative path to
 * (`_resolve_relative`), so the path shown here is the path written. Virtual roots
 * (Drive) are skipped — they're read-only. Null when no root is configured at all
 * (`HORRIBLE_NO_DEFAULT_ROOT`), which is the one case Save has nowhere to go.
 */
async function defaultSaveDir(): Promise<string | null> {
  if (getRoots().length === 0) await loadRoots().catch(() => undefined);
  return getRoots().find((r) => !isVirtualPath(r.path))?.path ?? null;
}

/** A filename for an untitled buffer: its tab title if it has a real one, else
 * `untitled`. An extension is appended only when the title carries none, so
 * "notes.md" stays as typed and "notes" becomes a markdown file; the extension comes
 * from the *resolved* language, so a sniffed-Python scratch buffer saves as `.py`. */
function suggestedFilename(title: string, language: string): string {
  // `untitled.md` is the scratch buffer's placeholder, not a name the user chose, so
  // its extension must not outrank a detected language — a buffer full of Python
  // offering to save itself as `.md` is the thing detection exists to avoid.
  const placeholder = !title || title === 'Untitled' || title === '…' || /^untitled\b/i.test(title);
  const base = placeholder ? 'untitled' : title;
  if (!placeholder && hasExtension(base)) return base;
  return `${base}.${extensionForLanguage(language)}`;
}

export function BufferView() {
  const params = usePaneParams();
  const source = typeof params.source === 'string' ? params.source : null;
  const langHint = typeof params.language === 'string' ? params.language : null;
  const instanceId = useContext(PaneInstanceContext);

  // The workspace file path this buffer edits (locus is keyed by path), or null for
  // note/scratch buffers that have no place in the code tree. Refs so the mount-time
  // CodeMirror listener always reads the live value.
  const filePath = source && source.startsWith(FILE_URI) ? source.slice(FILE_URI.length) : null;
  const filePathRef = useRef(filePath);
  filePathRef.current = filePath;
  const lastLocusLineRef = useRef<number | null>(null);
  // Scroll+select the current locus if it targets this file. A stable ref so both the
  // locus subscription and the load effect (once content has arrived) call the latest;
  // it reads everything live, so calling a slightly stale reference is fine.
  const applyLocusRef = useRef<() => void>(() => {});

  const hostRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);
  const langRef = useRef(new Compartment());
  const mergeRef = useRef(new Compartment());
  const autoRef = useRef(new Compartment());
  const lspRef = useRef(new Compartment());
  // Completion for buffers with no language server. Mutually exclusive with `lspRef`
  // (see usesLanguageServer) — two live `autocompletion()` instances silently fight.
  const completionRef = useRef(new Compartment());
  // Read-only-ness arrives with the content (Drive files, GitHub blobs), so it's a
  // compartment reconfigured on load rather than a mount-time extension.
  const readOnlyRef = useRef(new Compartment());
  const revisionRef = useRef<number | undefined>(undefined);
  // The buffer content captured when a proposal opens, restored on Decline.
  const originalRef = useRef('');
  const dirtyBeforeProposalRef = useRef(false);

  const [title, setTitle] = useState(source ? '…' : (params.title as string) || 'untitled.md');
  // A language the user chose by hand, which outranks the filename and the sniffer.
  const [pinnedLang, setPinnedLang] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [readOnly, setReadOnly] = useState(false);
  const [proposing, setProposing] = useState(false);
  const [status, setStatus] = useState<string | null>(null);
  // Where an untitled buffer would land, resolved once so the header can answer
  // "where does Save put this?" before the Save As dialog opens.
  const [saveDir, setSaveDir] = useState<string | null>(null);
  const autosuggestOn = useSetting<boolean>('editor.autosuggest') ?? false;
  // Interpreter for basedpyright (Python third-party import completions); overrides
  // the backend's auto-detected one. Captured when a buffer opens (see the load
  // effect) — changing it takes effect on reopen.
  const pythonPath = useSetting<string>('editor.pythonPath') ?? '';
  // Curated framework-import suggestions (see pythonImports.ts); on by default.
  const frameworkImports = useSetting<boolean>('editor.frameworkImports') ?? true;
  // The rest of the intellisense knobs, all captured on open like `pythonPath`.
  const indexedSymbols = useSetting<boolean>('editor.indexedSymbols') ?? true;
  const warmupMs = useSetting<number>('editor.completionWarmupMs') ?? 2000;
  const changeDebounceMs = useSetting<number>('editor.changeDebounceMs') ?? 300;
  const diagnosticsOn = useSetting<boolean>('editor.diagnostics') ?? true;
  const hoverOn = useSetting<boolean>('editor.hover') ?? true;
  // These two reach the *live* completion compartment rather than being captured on
  // open like the LSP knobs above, so changing them doesn't need a buffer reopen.
  const importCompletions = useSetting<boolean>('editor.importCompletions') ?? true;
  const completionTrigger =
    (useSetting<string>('editor.completionTrigger') as CompletionTrigger) ?? 'auto';

  // Save As, published to the module so `/save-as` can reach the active buffer.
  // Assigned each render (below, once `saveAs` is defined) and cleared on unmount
  // so a closed buffer's Save As can't outlive it.
  const saveAsRef = useRef<() => Promise<boolean>>(() => Promise.resolve(false));
  useEffect(() => () => setActiveSaveAs(null), []);
  const sourceRef = useRef(source);
  sourceRef.current = source;
  const instanceIdRef = useRef(instanceId);
  instanceIdRef.current = instanceId;
  const isProgrammaticRef = useRef(false);
  // The LSP language id of the loaded buffer ('' when unknown), read live by the
  // mount-time change listener so it can push edits to the symbol index.
  const indexLangRef = useRef('');
  // The resolved language, read by handlers registered at mount (which would
  // otherwise close over first render's value). Assigned once `resolved` exists.
  const languageRef = useRef<ReturnType<typeof resolveLanguage>>({
    name: 'Markdown',
    desc: null,
    lspId: null,
  });
  // Live dirty/title, read by the close guard (registered once, called on close).
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;
  const titleRef = useRef(title);
  titleRef.current = title;

  // The debounced content snapshot an untitled buffer's language is guessed from.
  const [sniffedContent, setSniffedContent] = useState('');
  const sniffTimerRef = useRef<number | undefined>(undefined);
  // Held in a ref because the mount-time change listener is registered once.
  const scheduleSniffRef = useRef((text: string) => {
    if (sniffTimerRef.current !== undefined) window.clearTimeout(sniffTimerRef.current);
    sniffTimerRef.current = window.setTimeout(() => setSniffedContent(text), 600);
  });
  useEffect(
    () => () => {
      if (sniffTimerRef.current !== undefined) window.clearTimeout(sniffTimerRef.current);
    },
    [],
  );

  /**
   * What language this buffer is. A **sourceless** buffer has no filename at all — its
   * title (`untitled.md`) is a placeholder, not a name — so it is resolved from its
   * content. Once it has a source the name is the answer, and the sniffer stops
   * mattering: a `.py` file that momentarily holds prose is still Python.
   */
  const resolved = resolveLanguage({
    title: source ? title : '',
    hint: langHint,
    content: source ? undefined : sniffedContent,
    pinned: pinnedLang,
  });
  languageRef.current = resolved;

  // Mount CodeMirror once.
  useEffect(() => {
    if (!hostRef.current) return;
    const view = new EditorView({
      parent: hostRef.current,
      state: EditorState.create({
        doc: '',
        extensions: [
          basicSetup,
          oneDark,
          // Accept an open popup, else open one where completions could plausibly come
          // from (`from <Tab>`), else indent. See completion.ts for why the middle rung
          // has to be conditional.
          completionKeymap,
          langRef.current.of(markdown()),
          mergeRef.current.of([]),
          autoRef.current.of([]),
          lspRef.current.of([]),
          completionRef.current.of([]),
          readOnlyRef.current.of([]),
          EditorView.updateListener.of((u) => {
            if (u.docChanged && !isProgrammaticRef.current) {
              setDirty(true);
              const src = sourceRef.current;
              if (src) {
                const content = u.state.doc.toString();
                unsavedCache.set(src, {
                  content,
                  dirty: true,
                  proposing: false, // user edits drop proposing state in our cache model
                });
                // Keep the buffer's symbols current in the completion index
                // (debounced; harvest is a no-op for languages we don't parse yet).
                if (indexLangRef.current) indexBuffer(src, indexLangRef.current, content);
              } else {
                // No source means no filename, so the content is the only signal for
                // what language this is. Debounced: re-resolving a grammar per
                // keystroke would be absurd, and a language that flickers mid-word is
                // worse than one that arrives a beat late.
                scheduleSniffRef.current(u.state.doc.toString());
              }
            }
            // Track the focused buffer as "active" (Mod-s etc. route through the
            // shell keybinding service → editor.save, never a hardcoded handler;
            // the agent attaches this instance's snapshot to a turn so it can alter
            // the open code without a discovery round-trip).
            if (u.focusChanged && u.view.hasFocus) {
              const src = sourceRef.current;
              if (src) setActiveBufferSource(src);
              if (instanceIdRef.current) setActiveContextInstance(instanceIdRef.current);
              // Same "active buffer" moment: publish this buffer's Save As so
              // `/save-as` in the minibuffer reaches the one you're looking at.
              setActiveSaveAs(() => saveAsRef.current());
            }
            // Publish the cursor to the code-locus bus (so the outline follows) — on
            // focus, and when the cursor changes line. Throttled to line granularity;
            // only workspace files have a locus path.
            const path = filePathRef.current;
            if (path && (u.selectionSet || (u.focusChanged && u.view.hasFocus))) {
              const head = u.state.selection.main.head;
              const line = u.state.doc.lineAt(head);
              if (lastLocusLineRef.current !== line.number || u.focusChanged) {
                lastLocusLineRef.current = line.number;
                const pos = { line: line.number, column: head - line.from + 1 };
                setLocus({ path, range: { start: pos, end: pos } }, 'editor');
              }
            }
          }),
        ],
      }),
    });
    viewRef.current = view;
    return () => view.destroy();
  }, []);

  // Load (or reload) the source content into the editor.
  useEffect(() => {
    if (!source) {
      // A sourceless buffer keeps the name its opener gave it (`untitled.md`), which
      // is also what Save As proposes. It is a placeholder, not a filename — the
      // language comes from the content until the file is actually named.
      setTitle(typeof params.title === 'string' ? params.title : 'Untitled');
      return;
    }
    setActiveBufferSource(source);
    setActiveSaveAs(() => saveAsRef.current());
    if (instanceId) setActiveContextInstance(instanceId);
    let cancelled = false;
    setStatus('Loading…');
    void loadSource(source)
      .then((loaded) => {
        const view = viewRef.current;
        if (cancelled || !view) return;
        revisionRef.current = loaded.revision;
        setTitle(loaded.title);
        setReadOnly(loaded.readOnly ?? false);

        let insertContent = loaded.content;
        let initialDirty = false;
        let isProposing = false;

        const src = sourceRef.current;
        if (src) {
          const cached = unsavedCache.get(src);
          if (cached) {
            if (cached.dirty || cached.proposing) {
              insertContent = cached.content;
              initialDirty = cached.dirty;
            }
            if (cached.proposing) {
              isProposing = true;
              originalRef.current = cached.original || '';
              dirtyBeforeProposalRef.current = cached.dirtyBeforeProposal || false;
            }
          }
        }

        isProgrammaticRef.current = true;
        view.dispatch({
          changes: { from: 0, to: view.state.doc.length, insert: insertContent },
          effects: [
            // The grammar is owned by the language effect below (it may change without
            // the source changing, and loading one is async).
            //
            // Connect (or disconnect) a language server for this buffer. The
            // reconfigure tears down any prior session (didClose + stop) and the
            // new plugin sees the just-applied content for its didOpen.
            lspRef.current.reconfigure(
              lspFor(source, resolveLanguage({ title: loaded.title, hint: langHint }).lspId, {
                pythonPath,
                frameworkImports,
                indexedSymbols,
                warmupMs,
                changeDebounceMs,
                diagnostics: diagnosticsOn,
                hover: hoverOn,
                importCompletions,
                trigger: completionTrigger,
              }),
            ),
            readOnlyRef.current.reconfigure(EditorState.readOnly.of(loaded.readOnly ?? false)),
            ...(isProposing
              ? [mergeRef.current.reconfigure(unifiedMergeView({ original: originalRef.current }))]
              : []),
          ],
        });
        isProgrammaticRef.current = false;

        // Seed the completion index with this buffer's symbols right away.
        const indexLang = resolveLanguage({ title: loaded.title, hint: langHint }).lspId ?? '';
        if (indexLang) indexBufferNow(source, indexLang, insertContent);

        setDirty(initialDirty);
        setProposing(isProposing);
        setStatus(null);
        // Content is now in the doc — re-apply any pending locus so a jump that opened
        // this file (its scroll fired against an empty doc) lands on the right line.
        applyLocusRef.current();
      })
      .catch((err: unknown) => {
        if (!cancelled) setStatus(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [source]);

  /**
   * Keep the grammar and the completion stack pointed at the resolved language.
   *
   * Its own effect rather than part of the load dispatch, for two reasons: the
   * language can change without the source changing (an untitled buffer being typed
   * into, a manual pin), and `@codemirror/language-data` grammars are dynamic imports
   * — loading one is async, so it cannot ride a synchronous load dispatch.
   */
  useEffect(() => {
    let cancelled = false;
    const view = viewRef.current;
    if (!view) return;
    indexLangRef.current = resolved.lspId ?? '';
    const settings = {
      indexedSymbols,
      frameworkImports,
      importCompletions,
      trigger: completionTrigger,
    };
    void (async () => {
      const support = resolved.desc ? await resolved.desc.load().catch(() => null) : null;
      if (cancelled || !viewRef.current) return;
      viewRef.current.dispatch({
        effects: [
          langRef.current.reconfigure(support ?? markdown()),
          completionRef.current.reconfigure(completionFor(source, resolved.lspId, settings)),
        ],
      });
    })();
    return () => {
      cancelled = true;
    };
    // `resolved` is derived fresh each render; the identity that matters is its name.
  }, [
    resolved.name,
    resolved.lspId,
    source,
    indexedSymbols,
    frameworkImports,
    importCompletions,
    completionTrigger,
  ]);

  // Resolve the untitled buffer's would-be destination (a sourced buffer already
  // shows its own path).
  useEffect(() => {
    if (source) return;
    let cancelled = false;
    void defaultSaveDir().then((dir) => {
      if (!cancelled) setSaveDir(dir);
    });
    return () => {
      cancelled = true;
    };
  }, [source]);

  // Drop the active-context marker when this buffer unmounts, so a closed pane
  // doesn't keep feeding stale content to the agent.
  useEffect(() => {
    if (!instanceId) return;
    return () => clearActiveContextInstance(instanceId);
  }, [instanceId]);

  // Follow the code locus: when another pane (outline, dash, agent — anything but
  // this editor) points the locus at this file, scroll to and select the range. The
  // `source !== 'editor'` guard is the echo break — we ignore loci we published.
  applyLocusRef.current = () => {
    const view = viewRef.current;
    const path = filePathRef.current;
    const loc = getLocus();
    if (!view || !path || loc.source === 'editor' || loc.path !== path || !loc.range) return;
    const doc = view.state.doc;
    const startLine = Math.min(Math.max(loc.range.start.line, 1), doc.lines);
    const endLine = Math.min(Math.max(loc.range.end.line, 1), doc.lines);
    const from = doc.line(startLine).from;
    const to = doc.line(endLine).to;
    lastLocusLineRef.current = startLine; // don't echo this jump back as our own move
    view.dispatch({
      selection: { anchor: from, head: to },
      effects: EditorView.scrollIntoView(from, { y: 'center' }),
    });
  };
  useEffect(() => {
    const apply = () => applyLocusRef.current();
    apply(); // apply whatever the locus already is when this buffer (re)mounts
    return subscribeLocus(apply);
  }, [source]);

  // Returns whether the buffer was saved — the close guard uses this to decide
  // whether closing may proceed (a failed save must not silently discard work).
  const save = async (): Promise<boolean> => {
    const view = viewRef.current;
    if (!view) return false;
    if (!source) {
      // A sourceless scratch buffer has nowhere to save in place — Save As it.
      return saveAs();
    }
    try {
      setStatus('Saving…');
      const res = await saveSource(source, view.state.doc.toString(), revisionRef.current);
      if (res.revision !== undefined) revisionRef.current = res.revision;
      setDirty(false);
      unsavedCache.delete(source);
      setStatus('Saved');
      setTimeout(() => setStatus((s) => (s === 'Saved' ? null : s)), 1200);
      return true;
    } catch (err) {
      setStatus(
        err instanceof ApiError && err.status === 409
          ? 'Conflict — file changed since load; reload to merge'
          : err instanceof Error
            ? err.message
            : String(err),
      );
      return false;
    }
  };

  /**
   * Re-point this pane at a source it didn't have before (an untitled buffer that
   * just became a file). Retargeting keeps the pane's area, tab position and region
   * strips — reopening would leave the untitled pane behind next to a second one.
   * The dirty flag is cleared first so the retarget can't trip the close guard.
   *
   * Returns false when the pane can't be retargeted (no instance id, or that file is
   * already open in another buffer), leaving the caller to just report the save.
   */
  const adoptSource = (uri: string): boolean => {
    const instance = instanceIdRef.current;
    if (!instance) return false;
    setDirty(false);
    setPaneDirty(instance, false);
    return (
      retargetPane(instance, `editor.buffer:${uri}`, { source: uri, title: sourceTitle(uri) }) !==
      null
    );
  };

  // Save the current content to a **new** destination (VS Code's "Save As…"): always
  // a real file under a workspace root. An untitled buffer used to become a `note:`,
  // which put work somewhere the file tree, git, and every other tool couldn't see it;
  // a buffer with no source is an unsaved *file*, so it saves as one.
  //
  // The prompt is prefilled with the **full absolute path**, so where the bytes land
  // is visible before confirming rather than implied by a bare title.
  //
  // An untitled buffer then **adopts** the file it just wrote (`adoptSource`) — the
  // title bar, tab, and later Mod-s saves all follow it. A buffer that already has a
  // source keeps it: Save As writes a copy there, as it did before.
  const saveAs = async (): Promise<boolean> => {
    const view = viewRef.current;
    if (!view) return false;
    const content = view.state.doc.toString();
    const untitled = !source;
    try {
      let defaultValue: string;
      if (source && source.startsWith(FILE_URI)) {
        defaultValue = source.slice(FILE_URI.length);
      } else {
        const dir = await defaultSaveDir();
        if (!dir) {
          setStatus('No workspace root configured — add one in Settings → Files to save.');
          return false;
        }
        defaultValue = joinPath(dir, suggestedFilename(title, resolved.name));
      }
      const dest = await dialogs.prompt({
        title: 'Save As',
        message: `Path to save the file to (a bare name lands in ${dirname(defaultValue) || 'the workspace root'})`,
        defaultValue,
        confirmLabel: 'Save',
      });
      const path = dest?.trim();
      if (!path) return false;
      setStatus('Saving…');
      await saveSource(`${FILE_URI}${path}`, content);

      if (untitled) {
        // The file now has a name, and a name outranks a guess — pin it so a later
        // edit can't sniff the buffer back to some other language.
        setPinnedLang(null);
        if (adoptSource(`${FILE_URI}${path}`)) return true;
      }
      const label = untitled ? `Saved ${path}` : 'Saved a copy';
      setStatus(label);
      setTimeout(() => setStatus((s) => (s === label ? null : s)), 1500);
      return true;
    } catch (err) {
      setStatus(err instanceof Error ? err.message : String(err));
      return false;
    }
  };

  // Held in a ref so the focus handler above (registered once, on mount) always
  // publishes the *current* closure rather than the one from first render.
  saveAsRef.current = saveAs;
  // Show an agent-proposed edit as an inline diff (original vs proposed). The user
  // reviews per-chunk in the gutter, then Accepts (keep) or Declines (revert).
  const propose = (content: string) => {
    const view = viewRef.current;
    if (!view) return;
    if (!proposing) {
      originalRef.current = view.state.doc.toString();
      dirtyBeforeProposalRef.current = dirty;
    }
    isProgrammaticRef.current = true;
    view.dispatch({
      changes: { from: 0, to: view.state.doc.length, insert: content },
      effects: mergeRef.current.reconfigure(unifiedMergeView({ original: originalRef.current })),
    });
    isProgrammaticRef.current = false;
    setProposing(true);

    const src = sourceRef.current;
    if (src) {
      unsavedCache.set(src, {
        content,
        dirty, // we retain the dirty state it had, or whatever it is, but it's part of proposal
        proposing: true,
        original: originalRef.current,
        dirtyBeforeProposal: dirtyBeforeProposalRef.current,
      });
    }
  };
  // The controller registers once per source; route through a ref so it always
  // calls the current `propose` (which closes over live `dirty`/`proposing`).
  const proposeRef = useRef(propose);
  proposeRef.current = propose;

  const closeProposal = (revert: boolean) => {
    const view = viewRef.current;
    if (!view) return;
    isProgrammaticRef.current = true;
    view.dispatch({
      ...(revert
        ? { changes: { from: 0, to: view.state.doc.length, insert: originalRef.current } }
        : {}),
      effects: mergeRef.current.reconfigure([]),
    });
    isProgrammaticRef.current = false;
    // Accept leaves changes in place (dirty); Decline restores the pre-proposal state.
    const newDirty = revert ? dirtyBeforeProposalRef.current : true;
    setDirty(newDirty);
    setProposing(false);

    const src = sourceRef.current;
    if (src) {
      unsavedCache.set(src, {
        content: view.state.doc.toString(),
        dirty: newDirty,
        proposing: false,
      });
    }
  };

  // Close guard: an unsaved buffer prompts before it's removed. Mirrors VS Code's
  // prompt exactly — Save (default) / Don't Save / Cancel. An untitled buffer has
  // nowhere to write, so its Save runs the Save As flow (`save()` falls through);
  // VS Code labels that button "Save" too rather than offering a separate one.
  // Returns whether the close proceeds.
  const closeGuard = async (): Promise<boolean> => {
    if (!dirtyRef.current) return true;
    const choice = await dialogs.choice({
      title: `Do you want to save the changes you made to ${titleRef.current}?`,
      message: "Your changes will be lost if you don't save.",
      buttons: [
        { label: 'Save', value: 'save', primary: true },
        { label: "Don't Save", value: 'dontSave' },
        { label: 'Cancel', value: 'cancel' },
      ],
      cancelValue: 'cancel',
    });
    switch (choice) {
      case 'save':
        return save();
      case 'dontSave':
        return true;
      default:
        return false; // Cancel / Esc / backdrop — keep the buffer open
    }
  };
  const closeGuardRef = useRef(closeGuard);
  closeGuardRef.current = closeGuard;

  // Register the guard for this pane instance (the frame's close paths run it),
  // and flag the buffer dirty for the app-exit (beforeunload) warning.
  useEffect(() => {
    if (!instanceId) return;
    return registerCloseGuard(instanceId, () => closeGuardRef.current());
  }, [instanceId]);
  useEffect(() => {
    const id = instanceId ?? source;
    if (!id) return;
    setPaneDirty(id, dirty);
    return () => setPaneDirty(id, false);
  }, [instanceId, source, dirty]);

  // A live snapshot for the agent (kept current via a ref reassigned each render).
  const snapshotRef = useRef<() => BufferSnapshot>(() => ({
    uri: '(unsaved)',
    title,
    content: '',
    dirty,
    selection: { from: 0, to: 0, text: '' },
    diagnostics: [],
  }));
  snapshotRef.current = () => {
    const view = viewRef.current;
    const sel = view?.state.selection.main;
    return {
      uri: source ?? '(unsaved)',
      title,
      content: view?.state.doc.toString() ?? '',
      dirty,
      selection:
        view && sel
          ? { from: sel.from, to: sel.to, text: view.state.doc.sliceString(sel.from, sel.to) }
          : { from: 0, to: 0, text: '' },
      diagnostics: source ? readDiagnostics(source) : [],
    };
  };

  // Read path: the agent pulls this buffer's snapshot on demand.
  useAgentContext(() => snapshotRef.current());

  // Inline autosuggest: enabled live by the setting, suppressed while reviewing a
  // proposed edit (the diff owns the buffer then). Reconfigured via a compartment.
  useEffect(() => {
    const view = viewRef.current;
    if (!view) return;
    const ext =
      autosuggestOn && !proposing
        ? autosuggest({
            // Ghost text is a prefix lookup into the DB symbol index — the same
            // index the dropdown uses — not a model. It returns the tail of the
            // single best-matching symbol for the token at the cursor, so there's
            // no round-trip latency and no hallucinated multi-line guesses.
            fetch: (prefix, suffix) =>
              dbGhostFetch(prefix, suffix, languageRef.current.lspId ?? ''),
          })
        : [];
    view.dispatch({ effects: autoRef.current.reconfigure(ext) });
  }, [autosuggestOn, proposing, title]);

  // Write path: register a controller so `editor.applyEdit`/`editor.save` (gated,
  // type-level tools) can act on this buffer instance by URI.
  useEffect(() => {
    if (!source) return;
    return registerBuffer(source, {
      snapshot: () => snapshotRef.current(),
      setContent: (content) => {
        const view = viewRef.current;
        if (view) {
          view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: content } });
        }
      },
      propose: (content) => proposeRef.current(content),
      save: () => save().then(() => undefined),
    });
    // `save`/`snapshotRef`/`propose` read current values via refs; only `source` re-registers.
  }, [source]);

  // The header's second line of identity: the file this buffer *is*, or — for an
  // untitled one — the file it would become. A tab title is only a basename, so
  // without this two `index.ts` buffers are indistinguishable and an untitled buffer
  // gives no clue where Save writes.
  const pathLabel =
    filePath ??
    (source ? null : saveDir && joinPath(saveDir, suggestedFilename(title, resolved.name)));

  return (
    <div className="editor-buffer">
      <div className="editor-header">
        <span className="editor-title">
          {title}
          {dirty ? ' •' : ''}
        </span>
        {pathLabel && (
          <span
            className="editor-path"
            title={filePath ?? `Unsaved — Save writes a file here (you can change it)`}
          >
            {pathLabel}
          </span>
        )}
        <span className="editor-status">{status}</span>
        {/* The resolved language, and the override. It says so out loud because the
            answer is now sometimes a guess — a buffer silently highlighting as the
            wrong language, with the wrong completions, is the failure this prevents. */}
        <select
          className="editor-language"
          value={resolved.name}
          onChange={(e) => setPinnedLang(e.target.value)}
          title={
            pinnedLang
              ? `Language pinned to ${pinnedLang}`
              : source
                ? `Detected from the file name`
                : `Detected from the content — save the buffer to fix it`
          }
          aria-label="Buffer language"
        >
          {/* The resolved language may be one language-data knows but the menu does
              not list (an .ini, a .toml); include it so the control never shows a
              value it doesn't have. */}
          {(PICKABLE_LANGUAGES.includes(resolved.name)
            ? PICKABLE_LANGUAGES
            : [resolved.name, ...PICKABLE_LANGUAGES]
          ).map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
        {readOnly ? (
          <span className="editor-readonly" title="This source can't be written back">
            Read-only
          </span>
        ) : (
          <button className="editor-save" onClick={() => void save()}>
            Save
          </button>
        )}
      </div>
      {proposing && (
        <div className="editor-proposal">
          <span className="editor-proposal-label">⤳ Agent proposed an edit — review the diff</span>
          <button className="editor-proposal-accept" onClick={() => closeProposal(false)}>
            Accept
          </button>
          <button className="editor-proposal-decline" onClick={() => closeProposal(true)}>
            Decline
          </button>
        </div>
      )}
      <div
        className="editor-cm"
        ref={hostRef}
        // The selection, offered to whoever can do something with it. Read from
        // the live view rather than from the persisted bytes: what is on screen
        // is what the user right-clicked, and tracing anything else is worse
        // than declining. An empty selection is a real target — "trace the whole
        // file" is the same convention "run selection" tools use.
        onContextMenu={(e) => {
          const view = viewRef.current;
          if (!view) return;
          const { from, to } = view.state.selection.main;
          if (
            openContextMenu(e, {
              kind: 'editor.selection',
              uri: source,
              text: view.state.sliceDoc(from, to),
              empty: from === to,
            })
          )
            e.preventDefault();
        }}
      />
    </div>
  );
}
