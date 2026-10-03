/**
 * The clip editor: one source video, one track. Keep stretches of it (split, trim,
 * reorder), crop to a platform's shape, change the speed, write or auto-generate
 * captions, put text over it, swap the sound, and render for X, YouTube, a page or a
 * GIF. The edit list is a file beside the source (`media/<name>.clip.json`, see
 * backend/modules/scrive/clips.py); edits are saved as they are made (debounced),
 * with the same stale-revision contract as pages.
 *
 * The preview is a `<video>` of the source that jumps between segments as it plays,
 * cropped by CSS with exactly the window ffmpeg will cut (`clip/edit.ts` mirrors the
 * backend's arithmetic) and with captions and overlays drawn on top. It is close to
 * the render, not identical: fonts and the burned-in outline are ffmpeg's.
 *
 * Opened without a clip it lists the site's clips and videos, and records the screen
 * into a new one. A finished render can become an X or YouTube **draft** — approving
 * and sending stay in the Share pane.
 */
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type MutableRefObject,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
} from 'react';

import { usePaneParams } from '../../../panes';
import { subscribeChannel } from '../../../ws';
import {
  cancelClipJob,
  captionClip,
  createClip,
  draftFromClip,
  filmstripUrl,
  listClipJobs,
  listMedia,
  mediaStatus,
  readClip,
  renderClip,
  saveClip,
  SCRIVE_CHANNEL,
  siteFileUrl,
  uploadAsset,
  type ClipCue,
  type ClipDoc,
  type ClipJob,
  type ClipOverlay,
  type ClipPreset,
  type CropAspect,
  type EditList,
  type ExtraState,
  type MediaListing,
} from '../api';
import {
  cropBox,
  cueAtPlayhead,
  cuesAt,
  formatTime,
  moveSegment,
  outputDuration,
  parseTime,
  removeSegment,
  round2,
  segmentAt,
  splitAt,
  toOutput,
  toSource,
  trim,
} from '../clip/edit';
import { recordingName, recordScreen, type Recording } from '../clip/recorder';
import { CloseIcon, FilmIcon, PlusIcon, RefreshIcon, SortDownIcon, SortUpIcon } from '../icons';
import { openClip, openShare } from '../open';
import '../scrive.css';

const SAVE_DEBOUNCE_MS = 600;
const FILMSTRIP_FRAMES = 24;
const ASPECTS: CropAspect[] = ['source', '16:9', '9:16', '1:1', '4:5'];
const SPEEDS = [0.5, 0.75, 1, 1.25, 1.5, 2, 3, 4];
const PRESETS: { id: ClipPreset; label: string }[] = [
  { id: 'x', label: 'X — up to 2:20, 1080p' },
  { id: 'youtube', label: 'YouTube — 1080p' },
  { id: 'web', label: 'Page — 720p, small' },
  { id: 'gif', label: 'GIF — no sound' },
];

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));
const field: CSSProperties = { padding: '0 0.6rem', minWidth: 0 };

export function ClipPanel() {
  const params = usePaneParams();
  const site = String(params.site ?? '');
  const path = String(params.path ?? '');
  if (!site) return <p className="scrive-meta">No site.</p>;
  return path ? <ClipEditor key={path} site={site} path={path} /> : <ClipPicker site={site} />;
}

// ── the picker ──────────────────────────────────────────────────────────────

function ClipPicker({ site }: { site: string }) {
  const [media, setMedia] = useState<MediaListing | null>(null);
  const [status, setStatus] = useState<{ ffmpeg: ExtraState; voice: ExtraState } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [recording, setRecording] = useState<Recording | null>(null);
  const [elapsed, setElapsed] = useState(0);

  const refresh = useCallback(() => {
    listMedia(site).then(setMedia, (e: unknown) => setError(errText(e)));
  }, [site]);
  useEffect(refresh, [refresh]);
  useEffect(() => {
    mediaStatus().then(setStatus, () => {});
  }, []);
  useEffect(() => {
    if (!recording) return;
    const timer = window.setInterval(
      () => setElapsed(Math.round((Date.now() - recording.startedAt) / 1000)),
      500,
    );
    return () => window.clearInterval(timer);
  }, [recording]);

  const newClip = async (source: string) => {
    setBusy(source);
    try {
      const doc = await createClip(site, source);
      openClip(site, doc.path);
      refresh();
    } catch (e) {
      setError(errText(e));
    } finally {
      setBusy(null);
    }
  };

  const record = async () => {
    setError(null);
    try {
      const rec = await recordScreen();
      setElapsed(0);
      setRecording(rec);
      const blob = await rec.done;
      setRecording(null);
      setBusy('saving the recording');
      const rel = await uploadAsset(
        site,
        new File([blob], recordingName(), { type: 'video/webm' }),
        'media/recordings',
      );
      await newClip(rel);
    } catch (e) {
      setRecording(null);
      setBusy(null);
      const cancelled = (e as { cancelled?: boolean }).cancelled;
      if (!cancelled) setError(errText(e));
    }
  };

  const ffmpegMissing = status && !status.ffmpeg.available;

  return (
    <div className="scrive-publish">
      <header className="scrive-outline-bar">
        <div style={{ minWidth: 0 }}>
          <div className="scrive-head">Clips</div>
          <div className="scrive-meta">{site} · cut, crop, caption, render</div>
        </div>
        <button
          type="button"
          className="btn-mini"
          aria-label="Refresh"
          title="Refresh"
          onClick={refresh}
        >
          <RefreshIcon size={12} />
        </button>
      </header>
      {error && (
        <div className="scrive-publish-error" role="alert">
          {error}
        </div>
      )}
      {ffmpegMissing && (
        <div className="scrive-publish-error" role="status">
          Rendering needs ffmpeg: {status.ffmpeg.install || status.ffmpeg.reason}
        </div>
      )}
      <div className="scrive-publish-body">
        <div className="scrive-publish-main">
          <Section title="Clips" meta={media ? String(media.clips.length) : ''}>
            {media && !media.clips.length && (
              <p className="scrive-meta scrive-publish-note">
                No clips yet. Start one from a video, or record the screen.
              </p>
            )}
            <ul className="scrive-clip-list">
              {media?.clips.map((c, i) => (
                <li key={c.path}>
                  <button
                    type="button"
                    className="scrive-row"
                    style={{ animationDelay: `${Math.min(i, 10) * 20}ms` }}
                    onClick={() => openClip(site, c.path)}
                  >
                    <FilmIcon />
                    <span className="scrive-clip-name">{c.path}</span>
                    <span className="scrive-meta">{ago(c.updated_at)}</span>
                  </button>
                </li>
              ))}
            </ul>
          </Section>
          <Section title="Videos" meta={media ? String(media.videos.length) : ''}>
            {media && !media.videos.length && (
              <p className="scrive-meta scrive-publish-note">
                No videos in this site. Drop one into a page, or record the screen.
              </p>
            )}
            <ul className="scrive-clip-list">
              {media?.videos.map((v, i) => (
                <li key={v.path} className="scrive-publish-page">
                  <span
                    className="scrive-row"
                    style={{ animationDelay: `${Math.min(i, 10) * 20}ms`, cursor: 'default' }}
                  >
                    <span className="scrive-clip-name">{v.path}</span>
                    <span className="scrive-meta">{megabytes(v.size)}</span>
                  </span>
                  <button
                    type="button"
                    className="scrive-publish-include"
                    disabled={busy !== null || Boolean(ffmpegMissing)}
                    onClick={() => void newClip(v.path)}
                  >
                    {busy === v.path ? 'opening' : 'new clip'}
                  </button>
                </li>
              ))}
            </ul>
          </Section>
        </div>
        <div className="scrive-publish-side">
          <Section title="Record">
            <p className="scrive-meta scrive-publish-note">
              Record a screen, window or tab. It is saved under media/recordings and opens as a new
              clip.
            </p>
            {recording ? (
              <div className="scrive-share-actions">
                <span className="scrive-clip-rec" aria-hidden="true" />
                <span className="scrive-meta" role="status">
                  recording {formatTime(elapsed).replace(/\.\d$/, '')}
                </span>
                <button type="button" onClick={() => recording.stop()}>
                  Stop
                </button>
              </div>
            ) : (
              <button type="button" disabled={busy !== null} onClick={() => void record()}>
                {busy && !media?.videos.some((v) => v.path === busy) ? busy : 'Record screen'}
              </button>
            )}
          </Section>
        </div>
      </div>
    </div>
  );
}

// ── the editor ──────────────────────────────────────────────────────────────

type SaveState = 'saved' | 'pending' | 'saving' | 'error';

function ClipEditor({ site, path }: { site: string; path: string }) {
  const [doc, setDoc] = useState<ClipDoc | null>(null);
  const [edit, setEdit] = useState<EditList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [saveState, setSaveState] = useState<SaveState>('saved');
  const [jobs, setJobs] = useState<Record<string, ClipJob>>({});
  const [audioFiles, setAudioFiles] = useState<string[]>([]);
  const [voice, setVoice] = useState<ExtraState | null>(null);
  const [view, setView] = useState<'edit' | 'render'>('edit');
  const [frame, setFrame] = useState(false);
  const [playing, setPlaying] = useState(false);
  const [srcT, setSrcT] = useState(0);
  const [outT, setOutT] = useState(0);
  const [selected, setSelected] = useState(0);
  const [renderStamp, setRenderStamp] = useState(0);
  const [undoCaptions, setUndoCaptions] = useState<ClipCue[] | null>(null);

  const videoRef = useRef<HTMLVideoElement | null>(null);
  const revision = useRef('');
  const pending = useRef<EditList | null>(null);
  const timer = useRef<number | null>(null);
  const saving = useRef<Promise<void> | null>(null);
  const segIndex = useRef(0);
  const editRef = useRef<EditList | null>(null);
  const applied = useRef(new Set<string>());
  editRef.current = edit;

  const load = useCallback(() => {
    readClip(site, path).then(
      (d) => {
        setDoc(d);
        setEdit(d.edit);
        revision.current = d.revision;
        setError(null);
      },
      (e: unknown) => setError(errText(e)),
    );
  }, [site, path]);

  useEffect(load, [load]);
  useEffect(() => {
    listMedia(site).then(
      (m) => setAudioFiles(m.audio.map((a) => a.path)),
      () => {},
    );
    mediaStatus().then(
      (s) => setVoice(s.voice),
      () => {},
    );
    listClipJobs(site, path).then(
      (list) => {
        // Earlier jobs' results were applied (or not) by the pane that started them.
        for (const j of list) applied.current.add(j.id);
        setJobs(Object.fromEntries(list.map((j) => [j.id, j])));
      },
      () => {},
    );
  }, [site, path]);

  // ── saving ────────────────────────────────────────────────────────────────

  const saveNow = useCallback(async () => {
    const next = pending.current;
    if (!next) return;
    pending.current = null;
    setSaveState('saving');
    try {
      const result = await saveClip(site, path, next, revision.current);
      if ('conflict' in result) {
        revision.current = result.conflict.revision;
        setDoc(result.conflict);
        setEdit(result.conflict.edit);
        setNotice('The clip changed on disk; showing that version.');
      } else {
        revision.current = result.doc.revision;
        setDoc(result.doc);
      }
      setSaveState(pending.current ? 'pending' : 'saved');
    } catch (e) {
      setSaveState('error');
      setError(errText(e));
    }
  }, [site, path]);

  const flush = useCallback(async () => {
    if (timer.current !== null) {
      window.clearTimeout(timer.current);
      timer.current = null;
    }
    if (saving.current) await saving.current;
    if (pending.current) {
      saving.current = saveNow();
      await saving.current;
      saving.current = null;
    }
  }, [saveNow]);

  const update = useCallback(
    (next: EditList) => {
      setEdit(next);
      pending.current = next;
      setSaveState('pending');
      if (timer.current !== null) window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => {
        timer.current = null;
        void flush();
      }, SAVE_DEBOUNCE_MS);
    },
    [flush],
  );

  useEffect(
    () => () => {
      // Leaving with an edit in hand: save it rather than drop it.
      if (timer.current !== null) window.clearTimeout(timer.current);
      if (pending.current) void saveNow();
    },
    [saveNow],
  );

  // ── jobs ──────────────────────────────────────────────────────────────────

  const onJob = useCallback(
    (job: ClipJob) => {
      setJobs((all) => ({ ...all, [job.id]: job }));
      if (job.status !== 'done' || applied.current.has(job.id)) return;
      applied.current.add(job.id);
      if (job.kind === 'render') {
        setRenderStamp(Date.now());
        readClip(site, path).then(
          (d) => setDoc((cur) => (cur ? { ...cur, rendered: d.rendered, output: d.output } : d)),
          () => {},
        );
      } else if (job.kind === 'captions') {
        const current = editRef.current;
        if (!current) return;
        setUndoCaptions(current.captions);
        update({ ...current, captions: job.cues });
        setNotice(
          job.cues.length
            ? `Auto-captions: ${job.cues.length} cue${job.cues.length === 1 ? '' : 's'}.`
            : 'Auto-captions heard no speech.',
        );
      }
    },
    [site, path, update],
  );

  useEffect(
    () =>
      subscribeChannel(SCRIVE_CHANNEL, (msg) => {
        if (msg.event !== 'clip.job') return;
        const job = msg.data as ClipJob;
        if (job.site === site && job.clip === path) onJob(job);
      }),
    [site, path, onJob],
  );

  const latest = (kind: ClipJob['kind']) =>
    Object.values(jobs)
      .filter((j) => j.kind === kind)
      .sort((a, b) => b.started_at - a.started_at)[0] ?? null;
  const renderJob = latest('render');
  const captionJob = latest('captions');

  const start = async (kind: ClipJob['kind']) => {
    setError(null);
    setNotice(null);
    try {
      await flush();
      const job = await (kind === 'render' ? renderClip(site, path) : captionClip(site, path));
      setJobs((all) => ({ ...all, [job.id]: job }));
    } catch (e) {
      setError(errText(e));
    }
  };

  const draft = async (target: 'x' | 'youtube') => {
    setError(null);
    try {
      const item = await draftFromClip(site, path, target);
      openShare(site, item.page);
      setNotice(`Drafted for ${target === 'x' ? 'X' : 'YouTube'} — review it in the Share pane.`);
    } catch (e) {
      setError(errText(e));
    }
  };

  // ── playback ──────────────────────────────────────────────────────────────

  const duration = edit ? outputDuration(edit) : 0;
  const probe = doc?.probe ?? null;

  const tick = useCallback(() => {
    const v = videoRef.current;
    const e = editRef.current;
    if (!v || !e || !e.segments.length) return;
    let i = segIndex.current;
    if (i >= e.segments.length) i = segIndex.current = 0;
    const seg = e.segments[i];
    if (!v.paused && v.currentTime >= seg.out - 0.03) {
      if (i + 1 < e.segments.length) {
        segIndex.current = i + 1;
        v.currentTime = e.segments[i + 1].in;
      } else {
        v.pause();
        setPlaying(false);
        setOutT(outputDuration(e));
        return;
      }
    }
    setSrcT(v.currentTime);
    const mapped = toOutput(e, v.currentTime, segmentAt(e, v.currentTime));
    if (mapped !== null) setOutT(mapped);
  }, []);

  useEffect(() => {
    if (!playing) return;
    let raf = 0;
    const loop = () => {
      tick();
      raf = requestAnimationFrame(loop);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, [playing, tick]);

  useEffect(() => {
    const v = videoRef.current;
    if (!v || !edit) return;
    v.playbackRate = edit.speed;
    v.muted = edit.audio.mute || Boolean(edit.audio.replace);
    v.volume = Math.min(1, edit.audio.gain);
  }, [edit, view]);

  const play = () => {
    const v = videoRef.current;
    if (!v || !edit?.segments.length) return;
    if (playing) {
      v.pause();
      setPlaying(false);
      return;
    }
    const from = outT >= duration - 0.05 ? 0 : outT;
    const { index, t } = toSource(edit, from);
    segIndex.current = index;
    v.currentTime = t;
    v.playbackRate = edit.speed;
    void v.play().then(
      () => setPlaying(true),
      (e: unknown) => setError(errText(e)),
    );
  };

  const seekSource = (t: number) => {
    const v = videoRef.current;
    if (!v || !edit) return;
    const clamped = Math.max(0, Math.min(t, probe?.duration ?? t));
    v.currentTime = clamped;
    setSrcT(clamped);
    const i = segmentAt(edit, clamped);
    if (i >= 0) {
      segIndex.current = i;
      setSelected(i);
      setOutT(toOutput(edit, clamped, i) ?? 0);
    }
  };

  const seekOutput = (t: number) => {
    if (!edit?.segments.length) return;
    const { index, t: s } = toSource(edit, t);
    segIndex.current = index;
    setOutT(t);
    seekSource(s);
  };

  const step = (frames: number) => {
    if (!probe) return;
    seekSource(srcT + frames / (probe.fps || 30));
  };

  const onKey = (e: KeyboardEvent) => {
    const target = e.target as HTMLElement;
    if (/^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName) || !edit) return;
    const key = e.key.toLowerCase();
    if (key === ' ' || key === 'k') play();
    else if (key === 's') update(splitAt(edit, srcT));
    else if (key === 'i') update(trim(edit, selected, 'in', srcT));
    else if (key === 'o') update(trim(edit, selected, 'out', srcT));
    else if (key === 'arrowleft') step(-1);
    else if (key === 'arrowright') step(1);
    else return;
    e.preventDefault();
  };

  if (error && !edit) {
    return (
      <div className="scrive-publish">
        <div className="scrive-publish-error" role="alert">
          {error}
        </div>
      </div>
    );
  }
  if (!edit || !doc) return <p className="scrive-meta">Loading the clip…</p>;

  const set = (patch: Partial<EditList>) => update({ ...edit, ...patch });
  const rendering = renderJob?.status === 'running';
  const errors = doc.findings.filter((f) => f.severity === 'error');
  const isGif = edit.output.preset === 'gif';

  return (
    <div className="scrive-publish scrive-clip" tabIndex={0} onKeyDown={onKey}>
      <header className="scrive-outline-bar">
        <div style={{ minWidth: 0 }}>
          <div className="scrive-head">
            Clip · {path.split('/').pop()?.replace('.clip.json', '')}
          </div>
          <div className="scrive-meta" style={{ overflowWrap: 'anywhere' }}>
            {edit.source}
            {probe &&
              ` · ${probe.width}x${probe.height} · ${Math.round(probe.fps)} fps · ${formatTime(
                probe.duration,
              )}${probe.has_audio ? '' : ' · no sound'}`}
          </div>
        </div>
        <span className="scrive-meta" role="status" data-state={saveState}>
          {saveState === 'saved' ? 'saved' : saveState === 'error' ? 'not saved' : 'saving…'}
        </span>
      </header>
      {error && (
        <div className="scrive-publish-error" role="alert">
          {error}
        </div>
      )}
      {notice && (
        <div className="scrive-clip-notice" role="status">
          <span>{notice}</span>
          {undoCaptions && (
            <button
              type="button"
              className="scrive-publish-include"
              onClick={() => {
                set({ captions: undoCaptions });
                setUndoCaptions(null);
                setNotice(null);
              }}
            >
              undo
            </button>
          )}
          <button
            type="button"
            className="btn-mini"
            aria-label="Dismiss"
            onClick={() => {
              setNotice(null);
              setUndoCaptions(null);
            }}
          >
            <CloseIcon size={11} />
          </button>
        </div>
      )}
      <div className="scrive-publish-body scrive-clip-body">
        <div className="scrive-publish-main">
          <div className="scrive-clip-toolbar">
            <div className="scrive-seg" role="group" aria-label="View">
              <button
                type="button"
                className="scrive-seg-btn"
                aria-pressed={view === 'edit' && !frame}
                onClick={() => {
                  setView('edit');
                  setFrame(false);
                }}
              >
                Cut
              </button>
              <button
                type="button"
                className="scrive-seg-btn"
                aria-pressed={view === 'edit' && frame}
                title="The whole frame, with the crop window to drag"
                onClick={() => {
                  setView('edit');
                  setFrame(true);
                }}
              >
                Frame
              </button>
              <button
                type="button"
                className="scrive-seg-btn"
                aria-pressed={view === 'render'}
                disabled={!doc.rendered}
                title={doc.rendered ? doc.output : 'Not rendered yet'}
                onClick={() => {
                  videoRef.current?.pause();
                  setPlaying(false);
                  setView('render');
                }}
              >
                Render
              </button>
            </div>
            {view === 'edit' && (
              <>
                <button
                  type="button"
                  className="btn-mini"
                  onClick={play}
                  aria-label={playing ? 'Pause' : 'Play'}
                >
                  {playing ? <PauseIcon /> : <PlayIcon />}
                </button>
                <span className="scrive-meta scrive-clip-clock">
                  {formatTime(outT)} / {formatTime(duration)}
                </span>
              </>
            )}
          </div>
          {view === 'render' ? (
            <RenderView site={site} doc={doc} stamp={renderStamp} />
          ) : probe ? (
            <Stage
              edit={edit}
              width={probe.width}
              height={probe.height}
              frame={frame}
              outT={outT}
              src={siteFileUrl(site, edit.source)}
              videoRef={videoRef}
              onTime={tick}
              onPause={() => setPlaying(false)}
              onFocus={(x, y) => set({ crop: { ...edit.crop, x: round2(x), y: round2(y) } })}
            />
          ) : (
            <p className="scrive-publish-warn">{edit.source} is not a readable video.</p>
          )}
          {view === 'edit' && probe && (
            <>
              <Timeline
                site={site}
                edit={edit}
                total={probe.duration}
                srcT={srcT}
                selected={selected}
                onSeek={seekSource}
              />
              <OutputBar edit={edit} duration={duration} outT={outT} onSeek={seekOutput} />
              <div className="scrive-share-actions">
                <button
                  type="button"
                  title="Cut the segment at the playhead in two (S)"
                  onClick={() => update(splitAt(edit, srcT))}
                >
                  Split
                </button>
                <button
                  type="button"
                  title="Start the selected segment at the playhead (I)"
                  onClick={() => update(trim(edit, selected, 'in', srcT))}
                >
                  Set in
                </button>
                <button
                  type="button"
                  title="End the selected segment at the playhead (O)"
                  onClick={() => update(trim(edit, selected, 'out', srcT))}
                >
                  Set out
                </button>
                <button
                  type="button"
                  disabled={!edit.segments.length}
                  onClick={() => {
                    update(removeSegment(edit, selected));
                    setSelected((s) => Math.max(0, Math.min(s, edit.segments.length - 2)));
                  }}
                >
                  Delete
                </button>
                <button
                  type="button"
                  title="Add the whole source as a segment"
                  onClick={() =>
                    update({
                      ...edit,
                      segments: [...edit.segments, { in: 0, out: round2(probe.duration) }],
                    })
                  }
                >
                  <PlusIcon size={12} /> Segment
                </button>
                <span className="scrive-meta">space play · S split · I/O in/out · ←/→ frame</span>
              </div>
              <SegmentList
                edit={edit}
                selected={selected}
                onSelect={(i) => {
                  setSelected(i);
                  seekSource(edit.segments[i].in);
                }}
                onChange={update}
              />
            </>
          )}
        </div>
        <div className="scrive-publish-side">
          <Section title="Shape">
            <div className="scrive-seg" role="group" aria-label="Crop">
              {ASPECTS.map((a) => (
                <button
                  key={a}
                  type="button"
                  className="scrive-seg-btn"
                  aria-pressed={edit.crop.aspect === a}
                  onClick={() => set({ crop: { aspect: a, x: 0.5, y: 0.5 } })}
                >
                  {a === 'source' ? 'Full' : a}
                </button>
              ))}
            </div>
            {edit.crop.aspect !== 'source' && (
              <p className="scrive-meta scrive-publish-note">
                Drag in Frame view to move the window.
              </p>
            )}
            <Field label="Speed">
              <select
                value={edit.speed}
                onChange={(e) => set({ speed: Number(e.target.value) })}
                style={field}
              >
                {SPEEDS.map((s) => (
                  <option key={s} value={s}>
                    {s}x
                  </option>
                ))}
              </select>
            </Field>
          </Section>

          <Section title="Captions" meta={String(edit.captions.length)}>
            <div className="scrive-share-actions" style={{ borderTop: 0, paddingTop: 0 }}>
              <button
                type="button"
                disabled={
                  captionJob?.status === 'running' ||
                  voice?.available === false ||
                  (!probe?.has_audio && !edit.audio.replace)
                }
                title={
                  voice?.available === false
                    ? `Needs local speech-to-text: ${voice.install || voice.reason}`
                    : !probe?.has_audio && !edit.audio.replace
                      ? 'The clip has no sound'
                      : 'Transcribe the sound into captions (replaces the current ones)'
                }
                onClick={() => void start('captions')}
              >
                {captionJob?.status === 'running' ? 'Listening…' : 'Auto-caption'}
              </button>
              <button
                type="button"
                onClick={() => set({ captions: [...edit.captions, cueAtPlayhead(edit, outT)] })}
              >
                <PlusIcon size={12} /> At playhead
              </button>
            </div>
            {captionJob?.status === 'running' && <Progress job={captionJob} />}
            {captionJob?.status === 'failed' && (
              <p className="scrive-publish-warn">{captionJob.error}</p>
            )}
            <CueList
              cues={edit.captions}
              onSeek={seekOutput}
              onChange={(captions) => set({ captions })}
            />
            <label className="scrive-share-check">
              <input
                type="checkbox"
                checked={edit.output.burn_captions}
                onChange={(e) =>
                  set({ output: { ...edit.output, burn_captions: e.target.checked } })
                }
              />
              Burn into the picture
            </label>
          </Section>

          <Section title="Text over" meta={String(edit.overlays.length)}>
            <button
              type="button"
              onClick={() =>
                set({
                  overlays: [...edit.overlays, { ...cueAtPlayhead(edit, outT), pos: 'top' }],
                })
              }
            >
              <PlusIcon size={12} /> At playhead
            </button>
            <CueList
              cues={edit.overlays}
              onSeek={seekOutput}
              onChange={(overlays) => set({ overlays: overlays as ClipOverlay[] })}
              positions
            />
          </Section>

          <Section title="Sound">
            <label className="scrive-share-check">
              <input
                type="checkbox"
                checked={edit.audio.mute}
                onChange={(e) => set({ audio: { ...edit.audio, mute: e.target.checked } })}
              />
              Mute
            </label>
            <Field label="Replace with">
              <select
                value={edit.audio.replace}
                onChange={(e) => set({ audio: { ...edit.audio, replace: e.target.value } })}
                style={field}
              >
                <option value="">The clip's own sound</option>
                {audioFiles.map((a) => (
                  <option key={a} value={a}>
                    {a}
                  </option>
                ))}
              </select>
            </Field>
            <Field label={`Volume ${Math.round(edit.audio.gain * 100)}%`}>
              <input
                type="range"
                min={0}
                max={2}
                step={0.05}
                value={edit.audio.gain}
                onChange={(e) => set({ audio: { ...edit.audio, gain: Number(e.target.value) } })}
              />
            </Field>
            {edit.audio.replace && (
              <p className="scrive-meta scrive-publish-note">
                The new sound plays in the render; the preview is silent.
              </p>
            )}
          </Section>

          <Section title="Render">
            <Field label="For">
              <select
                value={edit.output.preset}
                onChange={(e) =>
                  set({ output: { ...edit.output, preset: e.target.value as ClipPreset } })
                }
                style={field}
              >
                {PRESETS.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.label}
                  </option>
                ))}
              </select>
            </Field>
            {isGif && (
              <div className="scrive-clip-pair">
                <Field label="Frames a second">
                  <input
                    type="number"
                    min={4}
                    max={30}
                    value={edit.output.gif_fps}
                    onChange={(e) =>
                      set({ output: { ...edit.output, gif_fps: Number(e.target.value) || 12 } })
                    }
                    style={field}
                  />
                </Field>
                <Field label="Width">
                  <input
                    type="number"
                    min={120}
                    max={1080}
                    step={10}
                    value={edit.output.gif_width}
                    onChange={(e) =>
                      set({
                        output: { ...edit.output, gif_width: Number(e.target.value) || 480 },
                      })
                    }
                    style={field}
                  />
                </Field>
              </div>
            )}
            {doc.findings.length > 0 && (
              <ul className="scrive-publish-findings">
                {doc.findings.map((f, i) => (
                  <li key={i} data-severity={f.severity === 'error' ? 'block' : f.severity}>
                    {f.message}
                  </li>
                ))}
              </ul>
            )}
            <div className="scrive-share-actions">
              {rendering ? (
                <button type="button" onClick={() => void cancelClipJob(renderJob.id)}>
                  Cancel
                </button>
              ) : (
                <button
                  type="button"
                  disabled={errors.length > 0 || saveState === 'error'}
                  onClick={() => void start('render')}
                >
                  Render
                </button>
              )}
              <span className="scrive-meta" style={{ overflowWrap: 'anywhere' }}>
                {doc.output}
              </span>
            </div>
            {renderJob && <JobLine job={renderJob} />}
            {doc.rendered && !rendering && (
              <div className="scrive-clip-share">
                <span className="scrive-head">Share the render</span>
                <div className="scrive-share-actions" style={{ borderTop: 0, paddingTop: 0 }}>
                  <button type="button" onClick={() => void draft('x')}>
                    Draft for X
                  </button>
                  <button
                    type="button"
                    disabled={doc.output.endsWith('.gif')}
                    title={doc.output.endsWith('.gif') ? 'YouTube takes a video, not a GIF' : ''}
                    onClick={() => void draft('youtube')}
                  >
                    Draft for YouTube
                  </button>
                </div>
                <Field label="Embed in a page">
                  <input
                    type="text"
                    readOnly
                    value={embedFor(doc.output)}
                    onFocus={(e) => e.target.select()}
                    style={field}
                  />
                </Field>
              </div>
            )}
          </Section>
        </div>
      </div>
    </div>
  );
}

function embedFor(output: string): string {
  const fence = '```';
  return output.endsWith('.gif') ? `![](/${output})` : `${fence}{video} /${output}\n${fence}`;
}

// ── the stage ───────────────────────────────────────────────────────────────

function Stage({
  edit,
  width,
  height,
  frame,
  outT,
  src,
  videoRef,
  onTime,
  onPause,
  onFocus,
}: {
  edit: EditList;
  width: number;
  height: number;
  frame: boolean;
  outT: number;
  src: string;
  videoRef: MutableRefObject<HTMLVideoElement | null>;
  onTime: () => void;
  onPause: () => void;
  onFocus: (x: number, y: number) => void;
}) {
  const box = cropBox(edit.crop, width, height);
  const dragging = useRef(false);
  const shown = frame ? { w: width, h: height, x: 0, y: 0 } : box;
  const videoStyle: CSSProperties = {
    position: 'absolute',
    width: `${(width / shown.w) * 100}%`,
    height: `${(height / shown.h) * 100}%`,
    left: `${(-shown.x / shown.w) * 100}%`,
    top: `${(-shown.y / shown.h) * 100}%`,
  };
  const focusFrom = (e: ReactPointerEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    onFocus(
      Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width)),
      Math.min(1, Math.max(0, (e.clientY - rect.top) / rect.height)),
    );
  };
  const captions = edit.output.burn_captions ? cuesAt(edit.captions, outT) : [];
  const overlays = cuesAt(edit.overlays, outT);
  return (
    <div className="scrive-clip-stage">
      <div
        className="scrive-clip-viewport"
        style={
          {
            aspectRatio: `${shown.w} / ${shown.h}`,
            '--clip-aspect': shown.w / shown.h,
          } as CSSProperties
        }
        data-frame={frame}
        onPointerDown={(e) => {
          if (!frame || edit.crop.aspect === 'source') return;
          dragging.current = true;
          e.currentTarget.setPointerCapture(e.pointerId);
          focusFrom(e);
        }}
        onPointerMove={(e) => {
          if (dragging.current) focusFrom(e);
        }}
        onPointerUp={() => {
          dragging.current = false;
        }}
      >
        <video
          ref={videoRef}
          src={src}
          preload="auto"
          playsInline
          style={videoStyle}
          onTimeUpdate={onTime}
          onSeeked={onTime}
          onPause={onPause}
        />
        {frame ? (
          edit.crop.aspect !== 'source' && (
            <div
              className="scrive-clip-window"
              style={{
                left: `${(box.x / width) * 100}%`,
                top: `${(box.y / height) * 100}%`,
                width: `${(box.w / width) * 100}%`,
                height: `${(box.h / height) * 100}%`,
              }}
            />
          )
        ) : (
          <>
            {overlays.map((o, i) => (
              <div key={`o${i}`} className="scrive-clip-overlay" data-pos={o.pos}>
                {o.text}
              </div>
            ))}
            {captions.length > 0 && (
              <div className="scrive-clip-caption">{captions.map((c) => c.text).join('\n')}</div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function RenderView({ site, doc, stamp }: { site: string; doc: ClipDoc; stamp: number }) {
  const url = siteFileUrl(site, doc.output, stamp || doc.revision);
  return (
    <div className="scrive-clip-stage">
      {doc.output.endsWith('.gif') ? (
        <img src={url} alt={`Render of ${doc.path}`} className="scrive-clip-render" />
      ) : (
        <video src={url} controls preload="metadata" className="scrive-clip-render" />
      )}
    </div>
  );
}

// ── timelines ───────────────────────────────────────────────────────────────

function Timeline({
  site,
  edit,
  total,
  srcT,
  selected,
  onSeek,
}: {
  site: string;
  edit: EditList;
  total: number;
  srcT: number;
  selected: number;
  onSeek: (t: number) => void;
}) {
  const pct = (t: number) => `${(Math.max(0, Math.min(t, total)) / Math.max(total, 0.001)) * 100}%`;
  const seek = (e: ReactPointerEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    onSeek(((e.clientX - rect.left) / rect.width) * total);
  };
  return (
    <div className="scrive-clip-track-wrap">
      <span className="scrive-head">Source</span>
      <div
        className="scrive-clip-track"
        role="slider"
        aria-label="Source position"
        aria-valuemin={0}
        aria-valuemax={total}
        aria-valuenow={round2(srcT)}
        tabIndex={-1}
        onPointerDown={(e) => {
          e.currentTarget.setPointerCapture(e.pointerId);
          seek(e);
        }}
        onPointerMove={(e) => {
          if (e.buttons & 1) seek(e);
        }}
      >
        <img
          className="scrive-clip-strip"
          src={filmstripUrl(site, edit.source, FILMSTRIP_FRAMES)}
          alt=""
          draggable={false}
        />
        {edit.segments.map((s, i) => (
          <div
            key={i}
            className="scrive-clip-seg"
            aria-current={i === selected}
            style={{ left: pct(s.in), width: `calc(${pct(s.out)} - ${pct(s.in)})` }}
          >
            <span>{i + 1}</span>
          </div>
        ))}
        <div className="scrive-clip-playhead" style={{ left: pct(srcT) }} />
      </div>
      <div className="scrive-clip-ticks scrive-meta">
        <span>0:00</span>
        <span>{formatTime(total)}</span>
      </div>
    </div>
  );
}

/** The output's own timeline: segments end to end, with caption and overlay cues. */
function OutputBar({
  edit,
  duration,
  outT,
  onSeek,
}: {
  edit: EditList;
  duration: number;
  outT: number;
  onSeek: (t: number) => void;
}) {
  const pct = (t: number) => `${(Math.min(t, duration) / Math.max(duration, 0.001)) * 100}%`;
  return (
    <div className="scrive-clip-track-wrap">
      <span className="scrive-head">Output</span>
      <div
        className="scrive-clip-out"
        onPointerDown={(e) => {
          const rect = e.currentTarget.getBoundingClientRect();
          onSeek(((e.clientX - rect.left) / rect.width) * duration);
        }}
      >
        {[...edit.captions, ...edit.overlays].map((c, i) => (
          <div
            key={i}
            className="scrive-clip-cue"
            data-kind={'pos' in c ? 'overlay' : 'caption'}
            title={c.text}
            style={{ left: pct(c.t0), width: `calc(${pct(c.t1)} - ${pct(c.t0)})` }}
          />
        ))}
        <div className="scrive-clip-playhead" style={{ left: pct(outT) }} />
      </div>
    </div>
  );
}

function SegmentList({
  edit,
  selected,
  onSelect,
  onChange,
}: {
  edit: EditList;
  selected: number;
  onSelect: (i: number) => void;
  onChange: (next: EditList) => void;
}) {
  return (
    <ol className="scrive-clip-segments">
      {edit.segments.map((s, i) => (
        <li
          key={i}
          aria-current={i === selected}
          style={{ animationDelay: `${Math.min(i, 10) * 20}ms` }}
        >
          <button type="button" className="scrive-publish-include" onClick={() => onSelect(i)}>
            {i + 1}
          </button>
          <TimeInput label="in" value={s.in} onChange={(t) => onChange(trim(edit, i, 'in', t))} />
          <TimeInput
            label="out"
            value={s.out}
            onChange={(t) => onChange(trim(edit, i, 'out', t))}
          />
          <span className="scrive-meta">{formatTime(s.out - s.in)}</span>
          <button
            type="button"
            className="scrive-publish-include"
            aria-label="Earlier"
            disabled={i === 0}
            onClick={() => onChange(moveSegment(edit, i, -1))}
          >
            <SortUpIcon size={11} />
          </button>
          <button
            type="button"
            className="scrive-publish-include"
            aria-label="Later"
            disabled={i === edit.segments.length - 1}
            onClick={() => onChange(moveSegment(edit, i, 1))}
          >
            <SortDownIcon size={11} />
          </button>
        </li>
      ))}
    </ol>
  );
}

function CueList({
  cues,
  onChange,
  onSeek,
  positions = false,
}: {
  cues: (ClipCue | ClipOverlay)[];
  onChange: (cues: (ClipCue | ClipOverlay)[]) => void;
  onSeek: (t: number) => void;
  positions?: boolean;
}) {
  const patch = (i: number, p: Partial<ClipOverlay>) =>
    onChange(cues.map((c, j) => (j === i ? { ...c, ...p } : c)));
  return (
    <ol className="scrive-clip-cues">
      {cues.map((c, i) => (
        <li key={i}>
          <div className="scrive-clip-cue-times">
            <button
              type="button"
              className="scrive-publish-include"
              title="Go to this cue"
              onClick={() => onSeek(c.t0)}
            >
              {i + 1}
            </button>
            <TimeInput label="from" value={c.t0} onChange={(t) => patch(i, { t0: t })} />
            <TimeInput label="to" value={c.t1} onChange={(t) => patch(i, { t1: t })} />
            {positions && (
              <select
                aria-label="Position"
                value={(c as ClipOverlay).pos}
                onChange={(e) => patch(i, { pos: e.target.value as ClipOverlay['pos'] })}
                style={field}
              >
                <option value="top">top</option>
                <option value="center">middle</option>
                <option value="bottom">bottom</option>
              </select>
            )}
            <button
              type="button"
              className="btn-mini"
              aria-label="Remove"
              onClick={() => onChange(cues.filter((_, j) => j !== i))}
            >
              <CloseIcon size={11} />
            </button>
          </div>
          <input
            type="text"
            aria-label="Text"
            value={c.text}
            placeholder={positions ? 'Text to show' : 'What is said'}
            onChange={(e) => patch(i, { text: e.target.value })}
            style={field}
          />
        </li>
      ))}
    </ol>
  );
}

/** A time field: shows `m:ss.s`, takes `1:02.5` or `62.5`, commits on Enter or blur. */
function TimeInput({
  label,
  value,
  onChange,
}: {
  label: string;
  value: number;
  onChange: (t: number) => void;
}) {
  const [text, setText] = useState<string | null>(null);
  const commit = () => {
    if (text === null) return;
    const t = parseTime(text);
    if (t !== null) onChange(round2(t));
    setText(null);
  };
  return (
    <input
      type="text"
      className="scrive-clip-time"
      aria-label={label}
      title={label}
      value={text ?? formatTime(value)}
      onChange={(e) => setText(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => {
        if (e.key === 'Enter') commit();
        if (e.key === 'Escape') setText(null);
      }}
      style={field}
    />
  );
}

// ── small pieces ────────────────────────────────────────────────────────────

function Progress({ job }: { job: ClipJob }) {
  return (
    <div
      className="scrive-clip-progress"
      role="progressbar"
      aria-valuenow={Math.round(job.progress * 100)}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <div style={{ transform: `scaleX(${Math.min(1, Math.max(0, job.progress))})` }} />
    </div>
  );
}

function JobLine({ job }: { job: ClipJob }) {
  if (job.status === 'running') {
    return (
      <div className="scrive-publish-phase">
        <span className="scrive-meta">
          <span className="scrive-publish-pulse" aria-hidden="true" />
          {job.label} · {Math.round(job.progress * 100)}%
        </span>
        <Progress job={job} />
      </div>
    );
  }
  return (
    <div className={`scrive-publish-phase${job.status === 'done' ? ' is-done' : ''}`}>
      <span className="scrive-meta">
        {job.status === 'done'
          ? `Rendered · ${job.label}`
          : job.status === 'cancelled'
            ? 'Cancelled'
            : `Failed: ${job.error}`}
      </span>
      {job.findings.map((f, i) => (
        <span key={i} className="scrive-publish-warn">
          {f.message}
        </span>
      ))}
    </div>
  );
}

function Section({ title, meta, children }: { title: string; meta?: string; children: ReactNode }) {
  return (
    <section className="scrive-publish-section">
      <div className="scrive-publish-section-head">
        <span className="scrive-head">{title}</span>
        {meta !== undefined && <span className="scrive-meta">{meta}</span>}
      </div>
      {children}
    </section>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="scrive-clip-field">
      <span className="scrive-meta">{label}</span>
      {children}
    </label>
  );
}

function ago(seconds: number): string {
  const d = new Date(seconds * 1000);
  return d.toLocaleDateString([], { month: 'short', day: 'numeric' });
}

function megabytes(bytes: number): string {
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

const PlayIcon = () => (
  <svg width={12} height={12} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
    <path d="M7 4.5v15l13-7.5z" />
  </svg>
);

const PauseIcon = () => (
  <svg width={12} height={12} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
    <path d="M6 4h4v16H6zM14 4h4v16h-4z" />
  </svg>
);
