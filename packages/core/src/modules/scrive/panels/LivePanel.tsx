/**
 * A page someone else is hosting, edited live (the guest's side).
 *
 * The page is not on this machine: the text arrives from the host's (`live/session.ts`)
 * and every change goes back the same way, merged with theirs as you both type. The
 * host's machine saves it. When the host ends the session the text stays readable
 * here but stops syncing, and nothing on this machine kept it — the banner says so.
 *
 * The preview draws the page's structure; images and scenes live on the host's
 * machine and do not load here.
 */
import { markdown } from '@codemirror/lang-markdown';
import { Compartment, EditorState } from '@codemirror/state';
import { oneDark } from '@codemirror/theme-one-dark';
import { EditorView } from '@codemirror/view';
import { basicSetup } from 'codemirror';
import { useCallback, useEffect, useRef, useState } from 'react';

import { usePaneParams } from '../../../panes';
import { LiveIcon } from '../icons';
import {
  collabExtension,
  joinSession,
  onRoom,
  presenceOf,
  type JoinedSession,
  type LivePresence,
} from '../live/session';
import { PreviewCanvas } from './PreviewCanvas';
import '../scrive.css';

const PREVIEW_DEBOUNCE_MS = 250;

type Phase = 'joining' | 'live' | 'left' | 'ended' | 'failed';

export function LivePanel() {
  const params = usePaneParams();
  const key = String(params.key ?? '');
  // A reset (the host reloaded) rejoins from scratch: a new key for the editor.
  const [attempt, setAttempt] = useState(0);
  if (!key) {
    return <div style={{ padding: 'var(--space-4)', color: 'var(--text-dim)' }}>No session.</div>;
  }
  return (
    <LiveEditor
      key={`${key}:${attempt}`}
      sessionKey={key}
      onReset={() => setAttempt((n) => n + 1)}
    />
  );
}

function LiveEditor({ sessionKey, onReset }: { sessionKey: string; onReset: () => void }) {
  const hostRef = useRef<HTMLDivElement>(null);
  const viewRef = useRef<EditorView | null>(null);
  const editable = useRef(new Compartment());
  const sessionRef = useRef<JoinedSession | null>(null);
  const [phase, setPhase] = useState<Phase>('joining');
  const [error, setError] = useState<string | null>(null);
  const [title, setTitle] = useState('');
  const [host, setHost] = useState('');
  const [site, setSite] = useState('');
  const [path, setPath] = useState('');
  const [presence, setPresence] = useState<LivePresence[]>([]);
  const [previewText, setPreviewText] = useState('');
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const stopSyncing = useCallback(() => {
    viewRef.current?.dispatch({
      effects: editable.current.reconfigure([
        EditorState.readOnly.of(true),
        EditorView.editable.of(false),
      ]),
    });
  }, []);

  useEffect(() => {
    let cancelled = false;
    const offRoom = onRoom(sessionKey, {
      ended: () => {
        setPhase('ended');
        stopSyncing();
      },
      reset: onReset,
      error: (message) => setError(message),
    });
    joinSession(sessionKey).then(
      (session) => {
        if (cancelled) {
          session.destroy();
          return;
        }
        sessionRef.current = session;
        setTitle(session.summary.title);
        setHost(session.summary.host);
        setSite(session.summary.site);
        setPath(session.summary.path);
        const text = session.text.toString();
        setPreviewText(text);
        const parent = hostRef.current;
        if (!parent) return;
        viewRef.current = new EditorView({
          parent,
          state: EditorState.create({
            doc: text,
            extensions: [
              basicSetup,
              oneDark,
              markdown(),
              EditorView.lineWrapping,
              editable.current.of([]),
              collabExtension(session.text, session.provider),
              EditorView.updateListener.of((u) => {
                if (!u.docChanged) return;
                if (debounceRef.current) clearTimeout(debounceRef.current);
                debounceRef.current = setTimeout(
                  () => setPreviewText(u.state.doc.toString()),
                  PREVIEW_DEBOUNCE_MS,
                );
              }),
            ],
          }),
        });
        const update = () => setPresence(presenceOf(session.provider));
        session.provider.awareness.on('change', update);
        update();
        setPhase('live');
      },
      (e: unknown) => {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : String(e));
        setPhase('failed');
      },
    );
    return () => {
      cancelled = true;
      offRoom();
      if (debounceRef.current) clearTimeout(debounceRef.current);
      viewRef.current?.destroy();
      viewRef.current = null;
      sessionRef.current?.destroy();
      sessionRef.current = null;
    };
  }, [sessionKey, onReset, stopSyncing]);

  const leave = () => {
    sessionRef.current?.destroy();
    sessionRef.current = null;
    stopSyncing();
    setPhase('left');
  };

  return (
    <div className="scrive-live">
      <header className="scrive-bar scrive-live-bar">
        <div style={{ minWidth: 0 }}>
          <div className="scrive-head scrive-live-title" title={title}>
            <LiveIcon size={13} /> {title || 'Live page'}
          </div>
          <div className="scrive-meta">
            {phase === 'joining'
              ? 'joining…'
              : phase === 'failed'
                ? 'could not join'
                : phase === 'ended' || phase === 'left'
                  ? 'not syncing'
                  : `${host ? `hosted by ${host}` : 'live'} · ${site}/${path}`}
          </div>
        </div>
        <ul className="scrive-live-people" aria-label="Editing now">
          {presence.map((p) => (
            <li key={p.clientId} className="scrive-chip">
              <span className="scrive-live-dot" style={{ background: p.color }} />
              {p.name}
            </li>
          ))}
        </ul>
        {phase === 'live' && (
          <button type="button" onClick={leave} title="Stop editing this page">
            Leave
          </button>
        )}
      </header>
      {(phase === 'ended' || phase === 'left') && (
        <div className="scrive-live-banner" role="status">
          {phase === 'ended' ? 'The session has ended.' : 'You left the session.'} This text is no
          longer syncing and is not saved on this machine; {host || 'the host'}’s copy is the page.
        </div>
      )}
      {error && (
        <div className="scrive-live-banner" role="alert" data-tone="danger">
          {error}
        </div>
      )}
      <div className="scrive-live-body">
        <div ref={hostRef} className="scrive-live-source" />
        <div className="scrive-live-preview">
          {phase !== 'joining' && phase !== 'failed' && (
            <PreviewCanvas text={previewText} site={site} path={path} device="desktop" />
          )}
        </div>
      </div>
    </div>
  );
}
