/**
 * Record a room speaker and save their voice as a clone in Unsloth Desktop.
 *
 * Opened from a speaker's profile card. Consent comes first and gates the Record
 * button, because recording someone is already the sensitive step, not just saving.
 * The audio is that speaker's own Agora track with their silences dropped (see
 * `speakerVoiceSample`), auto-transcribed so the clone gets its reference text, then
 * uploaded through the same `/api/agent/voices/unsloth` proxy Settings → Cloned
 * voices uses. The saved voice appears in every TTS picker as `unsloth:<id>`.
 */
import { useEffect, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import { IconDot, IconStop } from '../../glyphs';
import { apiUrl } from '../../origin';
import { toastsStore } from '../../toasts';
import { createUnslothVoice } from '../agent/api';
import type { ClubhouseRoomSession } from './roomSession';
import {
  cloneWav,
  sttWav,
  startSpeakerRecording,
  type SpeakerRecording,
  type VoiceSampleCollector,
} from './speakerVoiceSample';

/** Below this much speech a clone is mostly guesswork; Unsloth's guidance is 6–15 s. */
const MIN_SPEECH_MS = 4000;
/** Give up waiting on a quiet speaker; what was caught so far is kept. */
const MAX_WALL_MS = 3 * 60_000;
/** The meter's full scale, as RMS. Speech on a remote track sits around 0.03–0.15. */
const METER_FULL = 0.12;

const heading: CSSProperties = {
  fontWeight: 700,
  textTransform: 'uppercase',
  letterSpacing: '0.14em',
  fontSize: 'var(--fs-micro, 0.7rem)',
  color: 'var(--text-dim)',
  margin: 0,
};
const meta: CSSProperties = {
  fontFamily: 'var(--font-mono, monospace)',
  color: 'var(--text-dim)',
  fontSize: 'var(--fs-micro, 0.7rem)',
};
const field: CSSProperties = { display: 'grid', gap: '0.2rem' };

interface Progress {
  level: number;
  speechMs: number;
  keptMs: number;
  threshold: number;
  targetMs: number;
}

interface Clip {
  blob: Blob;
  url: string;
  speechMs: number;
}

export function CloneSpeakerVoice({
  session,
  userId,
  name,
  onClose,
}: {
  session: ClubhouseRoomSession;
  userId: number;
  name: string;
  onClose: () => void;
}) {
  const [consent, setConsent] = useState(false);
  const [recording, setRecording] = useState(false);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [clip, setClip] = useState<Clip | null>(null);
  const [voiceName, setVoiceName] = useState(name);
  const [transcript, setTranscript] = useState('');
  const [transcribing, setTranscribing] = useState(false);
  const [language, setLanguage] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const rec = useRef<SpeakerRecording | null>(null);
  const wallTimer = useRef<number | null>(null);
  const clipUrl = useRef<string | null>(null);

  const stopCapture = () => {
    if (wallTimer.current !== null) window.clearTimeout(wallTimer.current);
    wallTimer.current = null;
    rec.current?.stop();
    setRecording(false);
  };

  // Unmount: stop listening and free the preview. Never stops the speaker's track.
  useEffect(
    () => () => {
      if (wallTimer.current !== null) window.clearTimeout(wallTimer.current);
      rec.current?.stop();
      if (clipUrl.current) URL.revokeObjectURL(clipUrl.current);
    },
    [],
  );

  // Leaving the room ends the recording with whatever was caught.
  useEffect(
    () =>
      session.subscribe(() => {
        if (!session.getState().joined && rec.current) finish(rec.current.collector);
      }),
    // `finish` only reads refs and setters, so a stale closure of it is fine.
    [session],
  );

  const transcribe = async (audio: Float32Array) => {
    setTranscribing(true);
    try {
      const form = new FormData();
      form.append('file', new File([sttWav(audio)], 'speaker.wav', { type: 'audio/wav' }));
      const res = await fetch(apiUrl('/api/agent/stt'), { method: 'POST', body: form });
      if (!res.ok) return; // 503 without the voice extra: the field just stays empty.
      const json = (await res.json()) as { text?: string; error?: string };
      const text = String(json.text ?? '').trim();
      // Only fill an empty field: never overwrite what the operator typed.
      if (text) setTranscript((prev) => prev || text);
    } catch {
      /* optional: the clone works without a transcript */
    } finally {
      setTranscribing(false);
    }
  };

  /** End the capture and turn what was kept into a clip. */
  function finish(collector: VoiceSampleCollector) {
    if (!rec.current) return;
    stopCapture();
    rec.current = null;
    const audio = collector.audio();
    if (collector.speechMs === 0) {
      setError(`Didn't hear ${name} speak. Record again once they're talking.`);
      return;
    }
    const blob = new Blob([cloneWav(audio)], { type: 'audio/wav' });
    if (clipUrl.current) URL.revokeObjectURL(clipUrl.current);
    clipUrl.current = URL.createObjectURL(blob);
    setClip({ blob, url: clipUrl.current, speechMs: collector.speechMs });
    void transcribe(audio);
  }

  const start = async () => {
    setError(null);
    setClip(null);
    setTranscript('');
    let lastPaint = 0;
    try {
      const r = await startSpeakerRecording(
        session,
        userId,
        (c) => {
          if (c.done) {
            finish(c);
            return;
          }
          // ~10 Hz: frames arrive every ~40 ms and this re-renders the dialog.
          const now = Date.now();
          if (now - lastPaint < 100) return;
          lastPaint = now;
          setProgress({
            level: c.level,
            speechMs: c.speechMs,
            keptMs: c.keptMs,
            threshold: c.threshold,
            targetMs: c.targetSpeechMs,
          });
        },
        () => {
          if (rec.current) finish(rec.current.collector);
        },
      );
      rec.current = r;
      setRecording(true);
      setProgress({
        level: 0,
        speechMs: 0,
        keptMs: 0,
        threshold: r.collector.threshold,
        targetMs: r.collector.targetSpeechMs,
      });
      wallTimer.current = window.setTimeout(() => {
        if (rec.current) finish(rec.current.collector);
      }, MAX_WALL_MS);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const save = async () => {
    if (!clip) return;
    setSaving(true);
    setError(null);
    try {
      const voice = await createUnslothVoice(clip.blob, {
        name: voiceName.trim(),
        transcript: transcript.trim() || undefined,
        language: language.trim() || undefined,
        filename: 'speaker.wav',
      });
      toastsStore.add(
        'success',
        'Voice cloned',
        `"${voice.name}" is saved in Unsloth as unsloth:${voice.id}.`,
      );
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const pct = (v: number) => `${Math.min(100, Math.max(0, v * 100)).toFixed(1)}%`;
  const short = clip !== null && clip.speechMs < MIN_SPEECH_MS;

  return (
    <div className="ch-profile-overlay" onClick={recording ? undefined : onClose}>
      <div
        className="ch-profile-card"
        onClick={(e) => e.stopPropagation()}
        style={{
          display: 'grid',
          gap: '0.65rem',
          borderTop: '2px solid var(--accent)',
          maxWidth: '28rem',
        }}
      >
        <button className="ch-profile-close" onClick={onClose} disabled={recording}>
          ✕
        </button>
        <h4 style={heading}>Clone {name}&rsquo;s voice</h4>
        <p className="setting-desc" style={{ margin: 0 }}>
          Records only {name}&rsquo;s audio track (no one else in the room), keeps the parts where
          they speak, and saves it as a voice in Unsloth Desktop on this machine.
        </p>

        <label
          className="setting-desc"
          style={{ display: 'flex', gap: '0.45rem', alignItems: 'flex-start', margin: 0 }}
        >
          <input
            type="checkbox"
            checked={consent}
            disabled={recording || clip !== null}
            onChange={(e) => setConsent(e.target.checked)}
          />
          I asked {name}, and they agreed to be recorded and to have their voice cloned.
        </label>

        <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
          {recording ? (
            <button
              onClick={() => rec.current && finish(rec.current.collector)}
              style={{ display: 'inline-flex', gap: '0.35rem', alignItems: 'center' }}
            >
              <IconStop width={12} height={12} /> Stop
            </button>
          ) : (
            <button
              disabled={!consent || saving}
              onClick={() => void start()}
              style={{ display: 'inline-flex', gap: '0.35rem', alignItems: 'center' }}
            >
              <IconDot width={12} height={12} style={{ color: 'var(--danger)' }} />
              {clip ? 'Record again' : 'Record'}
            </button>
          )}
          <span style={meta}>
            {recording && progress
              ? `REC · speech ${(progress.speechMs / 1000).toFixed(1)} / ${(progress.targetMs / 1000).toFixed(0)}s`
              : clip
                ? `${(clip.speechMs / 1000).toFixed(1)}s of speech captured`
                : 'Stops by itself at 15 s of speech'}
          </span>
        </div>

        {recording && progress && (
          <div style={{ display: 'grid', gap: '0.3rem' }}>
            {/* Live level of this speaker alone, with the speech threshold marked. */}
            <div
              style={{
                position: 'relative',
                height: 6,
                background: 'var(--bg-sunken, var(--bg))',
                border: '1px solid var(--border)',
              }}
              title="Their level; the tick is where it counts as speech"
            >
              <div
                style={{
                  height: '100%',
                  // Scaled, not resized: this updates ~10x a second.
                  transform: `scaleX(${Math.min(1, Math.max(0, progress.level / METER_FULL))})`,
                  transformOrigin: 'left',
                  background:
                    progress.level >= progress.threshold ? 'var(--accent)' : 'var(--text-dim)',
                  transition: 'transform 90ms linear',
                }}
              />
              <div
                style={{
                  position: 'absolute',
                  top: -2,
                  bottom: -2,
                  width: 1,
                  left: pct(progress.threshold / METER_FULL),
                  background: 'var(--text-secondary, var(--text))',
                }}
              />
            </div>
            <div style={{ height: 3, background: 'var(--border)' }}>
              <div
                style={{
                  width: pct(progress.speechMs / progress.targetMs),
                  height: '100%',
                  background: 'var(--accent)',
                }}
              />
            </div>
          </div>
        )}

        {clip && !recording && (
          <>
            <audio controls src={clip.url} style={{ width: '100%' }} />
            {short && (
              <p className="setting-desc" style={{ margin: 0 }}>
                That&rsquo;s under {MIN_SPEECH_MS / 1000}s of speech; the clone will be rough.
                Record again for a better one.
              </p>
            )}
            <div style={field}>
              <span style={meta}>Name</span>
              <input
                type="text"
                value={voiceName}
                maxLength={80}
                onChange={(e) => setVoiceName(e.target.value)}
              />
            </div>
            <div style={field}>
              <span style={meta}>
                What was said {transcribing ? '(transcribing…)' : '(improves the clone)'}
              </span>
              <textarea
                rows={3}
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
            <div>
              <button
                disabled={saving || transcribing || !voiceName.trim() || !consent}
                onClick={() => void save()}
              >
                {saving ? 'Saving…' : 'Save voice to Unsloth'}
              </button>
            </div>
          </>
        )}

        {error && <p className="widget-error">{error}</p>}
      </div>
    </div>
  );
}
