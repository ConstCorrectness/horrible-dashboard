/**
 * Bring playlists in: from an account (Spotify, Last.fm loved tracks, any plugin
 * importer), or from a file someone exported (Apple Music, M3U, CSV).
 *
 * An import only *describes* songs. The backend matches each one against the
 * library, downloads the rest, and files the matches it isn't sure of under
 * **Review** instead of guessing — that list is the last section here, because a
 * wrong download (a live cut, a karaoke version) is the failure worth a person's
 * glance.
 */
import { useEffect, useRef, useState } from 'react';

import { requestConnect } from '../../../connectors/store';
import { DataList, DataRow, RollingNumber } from '../../../DataList';
import { IconImport } from '../../../glyphs';
import { Button, Chip, EmptyState } from '../../../Primitives';
import { toastsStore } from '../../../toasts';
import {
  cancelJob,
  importerPlaylists,
  importPlaylist,
  importPlaylistFile,
  reviewTrack,
  type AccountPlaylist,
  type Candidate,
  type ImportJob,
} from '../api';
import {
  getJobs,
  getMusicStatus,
  getTracks,
  refreshJobs,
  refreshStatus,
  upsertTrack,
} from '../store';
import { formatTime, useMusicState } from '../ui';

/** Which connector unlocks an account importer. */
const CONNECTOR_FOR: Record<string, string> = { spotify: 'spotify', 'lastfm-loved': 'lastfm' };

function jobKind(job: ImportJob): 'ok' | 'info' | 'warn' | 'fail' | 'idle' {
  if (job.status === 'running') return 'info';
  if (job.status === 'failed') return 'fail';
  if ((job.counts.review ?? 0) > 0 || (job.counts.failed ?? 0) > 0) return 'warn';
  return job.status === 'done' ? 'ok' : 'idle';
}

function AccountImporter({
  id,
  label,
  connected,
}: {
  id: string;
  label: string;
  connected: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [playlists, setPlaylists] = useState<AccountPlaylist[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [started, setStarted] = useState<Set<string>>(new Set());

  const browse = async () => {
    setOpen(true);
    setError(null);
    try {
      setPlaylists((await importerPlaylists(id)).playlists);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const start = async (pl: AccountPlaylist) => {
    try {
      await importPlaylist(id, pl.ref);
      setStarted((s) => new Set(s).add(pl.ref));
      void refreshJobs().catch(() => {});
    } catch (e) {
      toastsStore.add('error', 'Import', e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div className="mu-importer">
      <div className="mu-importer__head">
        <span className="mu-importer__name">{label}</span>
        <Chip kind={connected ? 'ok' : 'idle'} dot>
          {connected ? 'connected' : 'not connected'}
        </Chip>
        <span className="mu-spacer" />
        {connected ? (
          <Button size="sm" onClick={() => void (open ? setOpen(false) : browse())}>
            {open ? 'Hide' : 'Browse playlists'}
          </Button>
        ) : (
          <Button
            size="sm"
            intent="primary"
            onClick={() => requestConnect(CONNECTOR_FOR[id] ?? id)}
          >
            Connect
          </Button>
        )}
      </div>
      {open && error && <p className="mu-muted mu-notes">{error}</p>}
      {open && playlists && (
        <DataList label={`${label} playlists`}>
          {playlists.map((pl, index) => (
            <DataRow
              key={pl.ref}
              index={index}
              hideMark
              title={pl.name}
              meta={[
                pl.track_count != null ? `${pl.track_count} songs` : '—',
                ...(pl.owner ? [pl.owner] : []),
              ]}
              actions={
                <Button size="sm" disabled={started.has(pl.ref)} onClick={() => void start(pl)}>
                  {started.has(pl.ref) ? 'Importing' : 'Import'}
                </Button>
              }
            >
              {pl.note || undefined}
            </DataRow>
          ))}
        </DataList>
      )}
    </div>
  );
}

function FileImporter() {
  const [busy, setBusy] = useState(false);
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const upload = async (files: File[]) => {
    for (const file of files) {
      setBusy(true);
      try {
        const job = await importPlaylistFile(file);
        toastsStore.add(
          'info',
          'Importing',
          `${job.name}: ${job.total} songs${job.note ? ` — ${job.note}` : ''}`,
        );
        void refreshJobs().catch(() => {});
      } catch (e) {
        toastsStore.add('error', `Import ${file.name}`, e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(false);
      }
    }
  };

  return (
    <div
      className="mu-drop"
      data-over={over ? 'true' : undefined}
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        void upload(Array.from(e.dataTransfer.files));
      }}
    >
      <IconImport />
      <span className="mu-drop__title">A playlist file</span>
      <span className="mu-muted">
        Apple Music: select a playlist, then File → Library → Export Playlist… and drop the .txt or
        .xml here. M3U and CSV exports (Exportify, TuneMyMusic) work too.
      </span>
      <Button size="sm" disabled={busy} onClick={() => input.current?.click()}>
        Choose a file
      </Button>
      <input
        ref={input}
        type="file"
        accept=".txt,.xml,.m3u,.m3u8,.csv"
        hidden
        onChange={(e) => {
          void upload(Array.from(e.target.files ?? []));
          e.target.value = '';
        }}
      />
    </div>
  );
}

function ReviewList() {
  const review = getTracks().filter((t) => t.status === 'needs_review');
  const decide = async (trackId: string, accept: boolean, candidate?: Candidate) => {
    try {
      const res = await reviewTrack(trackId, accept, candidate);
      if (res.track) upsertTrack(res.track);
    } catch (e) {
      toastsStore.add('error', 'Review', e instanceof Error ? e.message : String(e));
    }
  };
  if (review.length === 0) return null;
  return (
    <>
      <div className="mu-section-head">
        <span className="mu-section-title">Review</span>
        <span className="mu-section-meta">{review.length}</span>
      </div>
      <p className="mu-muted mu-notes">
        No upload matched these closely enough to download without asking. Pick the right one, or
        skip it.
      </p>
      <DataList label="Matches to review">
        {review.map((track, index) => (
          <DataRow
            key={track.id}
            index={index}
            kind="warn"
            title={track.title}
            meta={[
              track.artist || '—',
              formatTime(track.duration),
              `${Math.round((track.match_confidence ?? 0) * 100)}% sure`,
            ]}
            actions={
              <Button size="sm" intent="ghost" onClick={() => void decide(track.id, false)}>
                Skip
              </Button>
            }
          >
            <ul className="mu-candidates">
              {track.candidates.slice(0, 3).map((cand) => (
                <li key={`${cand.source}:${cand.ref}`}>
                  <button
                    type="button"
                    className="mu-candidate"
                    onClick={() => void decide(track.id, true, cand)}
                  >
                    <span className="mu-candidate__title">{cand.title}</span>
                    <span className="mu-candidate__meta">
                      {cand.channel || cand.artist} · {formatTime(cand.duration)}
                      {cand.score != null ? ` · ${Math.round(cand.score * 100)}%` : ''}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </DataRow>
        ))}
      </DataList>
    </>
  );
}

export function ImportSection() {
  useMusicState();
  useEffect(() => {
    void refreshStatus().catch(() => {});
    void refreshJobs().catch(() => {});
  }, []);

  const status = getMusicStatus();
  const accountImporters = (status?.importers ?? []).filter((i) => i.kind === 'account');
  const jobs = getJobs();

  return (
    <div className="mu-import">
      <div className="mu-section-head">
        <span className="mu-section-title">From an account</span>
      </div>
      {accountImporters.length === 0 ? (
        <EmptyState title="No account importers">
          The backend reports none — is it up to date?
        </EmptyState>
      ) : (
        accountImporters.map((imp) => (
          <AccountImporter key={imp.id} id={imp.id} label={imp.label} connected={imp.configured} />
        ))
      )}
      <p className="mu-muted mu-notes">
        Spotify can only be read, never played here: each song is found and downloaded into your
        library. A Spotify app in development mode needs its owner on Premium, and may only return
        playlists you own.
      </p>

      <div className="mu-section-head">
        <span className="mu-section-title">From a file</span>
      </div>
      <FileImporter />

      {jobs.length > 0 && (
        <>
          <div className="mu-section-head">
            <span className="mu-section-title">Imports</span>
          </div>
          <DataList label="Import jobs">
            {jobs.map((job, index) => (
              <DataRow
                key={job.id}
                index={index}
                kind={jobKind(job)}
                title={job.name}
                meta={[
                  <>
                    <RollingNumber value={job.finished} />/{job.total}
                  </>,
                  ...(job.counts.review ? [`${job.counts.review} to review`] : []),
                  ...(job.counts.failed ? [`${job.counts.failed} not found`] : []),
                  job.importer,
                ]}
                badge={job.status}
                actions={
                  job.status === 'running' ? (
                    <Button
                      size="sm"
                      intent="ghost"
                      onClick={() => void cancelJob(job.id).then(() => refreshJobs())}
                    >
                      Stop
                    </Button>
                  ) : undefined
                }
              >
                {job.note || undefined}
              </DataRow>
            ))}
          </DataList>
        </>
      )}

      <ReviewList />
    </div>
  );
}
