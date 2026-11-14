/**
 * Settings section for **cloned voices**, which live in Unsloth Desktop.
 *
 * Record a clip (or pick a file), say what was said, and save it as a voice in
 * Unsloth. Every saved voice then appears in the TTS voice pickers (the Clubhouse
 * agent's included) as `unsloth:<id>`, and `/api/agent/tts` speaks it locally.
 *
 * The voices are Unsloth's, not ours: this lists, saves, plays and deletes them
 * through the backend's proxy, which holds the Unsloth key the browser never sees.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import {
  createUnslothVoice,
  deleteUnslothVoice,
  getUnslothVoices,
  ttsUrl,
  unslothVoiceAudioUrl,
  type UnslothVoicesStatus,
} from './api';

const SAMPLE = 'Hello. This is how I sound when the agent speaks for me.';

const heading: CSSProperties = {
  fontWeight: 700,
  textTransform: 'uppercase',
  letterSpacing: '0.14em',
  fontSize: 'var(--fs-micro, 0.7rem)',
  color: 'var(--text-dim)',
  margin: '0.9rem 0 0.4rem',
};

const meta: CSSProperties = {
  fontFamily: 'var(--font-mono, monospace)',
  color: 'var(--text-dim)',
  fontSize: 'var(--fs-micro, 0.7rem)',
};

const panel: CSSProperties = {
  border: '1px solid var(--border)',
  borderTopWidth: 2,
  borderTopColor: 'var(--accent)',
  borderRadius: 'var(--radius-sm)',
  background: 'var(--bg-raised)',
  padding: '0.6rem 0.75rem',
  display: 'grid',
  gap: '0.5rem',
};

const field: CSSProperties = { display: 'grid', gap: '0.2rem' };

export function VoicesSettings() {
  const [status, setStatus] = useState<UnslothVoicesStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sample, setSample] = useState(SAMPLE);
  const [busy, setBusy] = useState<string | null>(null);
  const player = useRef<HTMLAudioElement | null>(null);

  const refresh = useCallback(() => {
    void getUnslothVoices()
      .then((s) => {
        setStatus(s);
        // A successful read clears a stale failure (a backend restart, say).
        setError(null);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, []);
  useEffect(refresh, [refresh]);

  /** One player for the whole section, so a second Play stops the first. */
  const play = useCallback(async (url: string, key: string) => {
    setError(null);
    setBusy(key);
    try {
      // Fetched first rather than set as `src`, so a failure shows the backend's
      // reason (not running, refused key) instead of a silent dead player.
      const res = await fetch(url);
      if (!res.ok) {
        let detail = `HTTP ${res.status}`;
        try {
          detail = ((await res.json()) as { detail?: string }).detail || detail;
        } catch {
          /* keep the status */
        }
        throw new Error(detail);
      }
      const blobUrl = URL.createObjectURL(await res.blob());
      player.current?.pause();
      const audio = new Audio(blobUrl);
      audio.onended = () => URL.revokeObjectURL(blobUrl);
      player.current = audio;
      await audio.play();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }, []);

  const remove = async (id: string, name: string) => {
    if (!window.confirm(`Delete the voice "${name}" from Unsloth? This can't be undone.`)) return;
    setError(null);
    try {
      await deleteUnslothVoice(id);
      refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  if (!status && !error) return <p className="setting-desc">Asking Unsloth…</p>;

  return (
    <div className="voices-settings">
      <p className="setting-desc">
        Voices cloned from a short recording and spoken by Unsloth Desktop on this machine. A saved
        voice shows up in every TTS voice picker, the Clubhouse agent&rsquo;s included, labelled
        &ldquo;cloned, Unsloth&rdquo;. Only clone your own voice, or one whose owner has agreed to
        it.
      </p>

      {status && !status.reachable && <p className="setting-desc">{status.error}</p>}

      {status?.reachable && (
        <>
          <div style={heading}>Saved voices</div>
          {status.voices.length === 0 ? (
            <p className="setting-desc">None yet. Record one below.</p>
          ) : (
            <>
              <div style={{ ...field, marginBottom: '0.5rem' }}>
                <span style={meta}>Test sentence</span>
                <input type="text" value={sample} onChange={(e) => setSample(e.target.value)} />
              </div>
              {status.voices.map((v, i) => (
                <div
                  key={v.id}
                  className="setting-row voice-row"
                  style={{ animationDelay: `${Math.min(i, 8) * 40}ms` }}
                >
                  <div className="setting-label">
                    <label>{v.name}</label>
                    <p className="setting-desc" style={meta}>
                      unsloth:{v.id}
                      {v.duration_s != null && ` · ${v.duration_s.toFixed(1)}s`}
                      {v.language && ` · ${v.language}`}
                    </p>
                  </div>
                  <div className="setting-control" style={{ display: 'flex', gap: '0.4rem' }}>
                    <button
                      disabled={busy !== null}
                      title="Play the recording this voice was made from"
                      onClick={() => void play(unslothVoiceAudioUrl(v.id), `clip:${v.id}`)}
                    >
                      {busy === `clip:${v.id}` ? 'Loading…' : 'Original'}
                    </button>
                    <button
                      disabled={busy !== null || !sample.trim()}
                      title="Speak the test sentence in this voice"
                      onClick={() => void play(ttsUrl(sample, `unsloth:${v.id}`), `say:${v.id}`)}
                    >
                      {busy === `say:${v.id}` ? 'Speaking…' : 'Speak'}
                    </button>
                    <button className="setting-reset" onClick={() => void remove(v.id, v.name)}>
                      Delete
                    </button>
                  </div>
                </div>
              ))}
            </>
          )}
          <div style={heading}>New voice</div>
          <NewVoice onSaved={refresh} />
        </>
      )}

      {error && <p className="widget-error">{error}</p>}
    </div>
  );
}

/** Record or pick a clip, describe it, save it. */
function NewVoice({ onSaved }: { onSaved: () => void }) {
  const [clip, setClip] = useState<{ blob: Blob; url: string; filename: string } | null>(null);
  const [recording, setRecording] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [name, setName] = useState('');
  const [transcript, setTranscript] = useState('');
  const [language, setLanguage] = useState('');
  const [consent, setConsent] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const timer = useRef<number | null>(null);

  const replaceClip = (next: typeof clip) =>
    setClip((prev) => {
      if (prev) URL.revokeObjectURL(prev.url);
      return next;
    });

  // Release the mic and the object URL if the section unmounts mid-recording.
  useEffect(
    () => () => {
      if (timer.current !== null) window.clearInterval(timer.current);
      recorder.current?.stream.getTracks().forEach((t) => t.stop());
    },
    [],
  );

  const start = async () => {
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const rec = new MediaRecorder(stream);
      const chunks: Blob[] = [];
      rec.ondataavailable = (e) => e.data.size > 0 && chunks.push(e.data);
      rec.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        const blob = new Blob(chunks, { type: rec.mimeType || 'audio/webm' });
        const ext = blob.type.includes('ogg') ? 'ogg' : blob.type.includes('mp4') ? 'm4a' : 'webm';
        replaceClip({ blob, url: URL.createObjectURL(blob), filename: `recording.${ext}` });
      };
      recorder.current = rec;
      rec.start();
      setRecording(true);
      setSeconds(0);
      const began = Date.now();
      timer.current = window.setInterval(() => setSeconds((Date.now() - began) / 1000), 200);
    } catch (e) {
      setError(`Could not open the microphone: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const stop = () => {
    if (timer.current !== null) window.clearInterval(timer.current);
    timer.current = null;
    recorder.current?.stop();
    recorder.current = null;
    setRecording(false);
  };

  const save = async () => {
    if (!clip) return;
    setSaving(true);
    setError(null);
    try {
      await createUnslothVoice(clip.blob, {
        name: name.trim(),
        transcript: transcript.trim() || undefined,
        language: language.trim() || undefined,
        filename: clip.filename,
      });
      replaceClip(null);
      setName('');
      setTranscript('');
      setConsent(false);
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div style={panel}>
      <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap' }}>
        {recording ? (
          <button onClick={stop}>Stop</button>
        ) : (
          <button onClick={() => void start()}>{clip ? 'Record again' : 'Record'}</button>
        )}
        <label style={{ ...meta, cursor: 'pointer' }}>
          or choose a file{' '}
          <input
            type="file"
            accept="audio/*"
            style={{ ...meta, width: '14rem' }}
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) replaceClip({ blob: f, url: URL.createObjectURL(f), filename: f.name });
            }}
          />
        </label>
        <span style={meta}>
          {recording ? `REC ${seconds.toFixed(1)}s` : 'Aim for 6 to 15 seconds of clear speech'}
        </span>
      </div>

      {clip && !recording && <audio controls src={clip.url} style={{ width: '100%' }} />}

      <div style={field}>
        <span style={meta}>Name</span>
        <input type="text" value={name} maxLength={80} onChange={(e) => setName(e.target.value)} />
      </div>
      <div style={field}>
        <span style={meta}>What was said (improves the clone)</span>
        <textarea
          rows={2}
          value={transcript}
          maxLength={4000}
          onChange={(e) => setTranscript(e.target.value)}
        />
      </div>
      <div style={field}>
        <span style={meta}>Language (optional, e.g. en)</span>
        <input
          type="text"
          value={language}
          maxLength={64}
          style={{ width: '8rem' }}
          onChange={(e) => setLanguage(e.target.value)}
        />
      </div>
      <label
        className="setting-desc"
        style={{ display: 'flex', gap: '0.4rem', alignItems: 'center' }}
      >
        <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
        This is my voice, or its owner has agreed to it being cloned.
      </label>
      <div>
        <button
          disabled={saving || recording || !clip || !name.trim() || !consent}
          onClick={() => void save()}
        >
          {saving ? 'Saving…' : 'Save voice'}
        </button>
      </div>
      {error && <p className="widget-error">{error}</p>}
    </div>
  );
}
