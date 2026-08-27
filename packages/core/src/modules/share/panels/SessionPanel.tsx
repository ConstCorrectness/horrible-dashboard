import { useCallback, useEffect, useMemo, useState, useSyncExternalStore } from 'react';

import { Button, Chip, EmptyState, Field, PaneHeader } from '../../../Primitives';
import { CopyableLink } from '../../../CopyableLink';
import type { GuestCursor } from '../ws';
import { DataList, DataRow, type RowKind } from '../../../DataList';
import { IconAlert, IconCheck, IconPlus, IconTrash } from '../../../glyphs';
import {
  GRANT_BLURB,
  GRANT_LADDER,
  getInvitees,
  getRestream,
  mintLink,
  revokeLink,
  startRestream,
  stopRestream,
  type RestreamState,
  type GrantLevel,
  type Invitee,
  type RelayState,
} from '../api';
import { probeCapture } from '../capture';
import type { Preflight } from '../preflight';
import {
  attachRelay,
  getStreamState,
  startStream,
  stopStream,
  subscribeStream,
} from '../stream';
import {
  dismissInviteViaChannel,
  getShareSnapshot,
  grantViaChannel,
  inviteViaChannel,
  joinViaChannel,
  kickViaChannel,
  leaveViaChannel,
  requestShareState,
  revokeAllViaChannel,
  startViaChannel,
  stopViaChannel,
  subscribeShare,
} from '../ws';

/**
 * The share pane: who is in this workspace, and what each of them may do.
 *
 * It is a *permission* surface first and a video one second, and that ordering is
 * why the grant sits on the participant row rather than behind a dialog: a host
 * has to see every rung at a glance and take any of them away in one click.
 * Screen sharing is a button here rather than the headline for the same reason —
 * the ladder governs what a guest can *do*, and the capture only governs what
 * they can see.
 */

/** A rung's position, for deciding how loudly to draw it. */
/** How each audit outcome reads in the list.
 *
 * `asked` is deliberately not a refusal: the host's own rules wanted a human,
 * which is a different fact from "denied" and points the host at a different
 * action (approve it, or write a rule).
 */
/** Labels for the destinations the connector knows. Mirrors `DESTINATIONS` in
 *  `backend/modules/share/streaming.py`; an id with no entry renders as itself,
 *  so a destination added there is never invisible here. */
const DESTINATION_LABEL: Record<string, string> = {
  twitch: 'Twitch',
  youtube: 'YouTube',
  custom: 'RTMP',
};

/** How each relay state reads on the chip.
 *
 *  Four entries, not two. `gone` and `unknown` look alike from here — no picture
 *  is reaching viewers either way — but they need opposite reactions: `gone`
 *  means the URL is dead and the fix is to mint another, while `unknown` means
 *  we could not ask and the link is quite possibly fine. Showing one for the
 *  other is exactly the lie this whole chip was fixed to stop telling. */
const RELAY_CHIP: Record<RelayState, { label: string; kind: RowKind; title: string }> = {
  live: { label: 'relaying', kind: 'ok', title: 'The relay is receiving this capture.' },
  idle: {
    label: 'no picture',
    kind: 'warn',
    title: 'The relay still has this link but is receiving nothing from it.',
  },
  gone: {
    label: 'link dead',
    kind: 'warn',
    title:
      'The relay no longer has this link — it expired, was revoked, or the relay ' +
      'restarted. Anyone holding the URL sees an expired page. Mint a new one.',
  },
  unknown: {
    label: 'relay unknown',
    kind: 'idle',
    title: 'Could not reach the relay to ask. The link may still be fine.',
  },
};

const AUDIT_KIND: Record<string, RowKind> = {
  allowed: 'ok',
  denied: 'warn',
  asked: 'idle',
  failed: 'warn',
};

/**
 * Where each guest is pointing, drawn over the host's own pane.
 *
 * Positions arrive as **fractions of the guest's viewport**, so they are
 * rendered as percentages here rather than pixels: the two windows are different
 * sizes, and a pixel offset from somebody else's screen means nothing on this
 * one.
 *
 * Stale cursors are dropped rather than left frozen. A guest who closes the tab
 * sends no goodbye, and a pointer that stopped moving three minutes ago
 * misrepresents where somebody is looking — which is the entire content of this
 * feature.
 */
function GuestCursors({ cursors }: { cursors: Record<string, GuestCursor> }) {
  const [, tick] = useState(0);
  useEffect(() => {
    // Re-render on a slow timer so stale cursors actually disappear; nothing
    // else would trigger a render once the guest stops moving.
    const timer = setInterval(() => tick((n) => n + 1), 2000);
    return () => clearInterval(timer);
  }, []);

  const now = Date.now() / 1000;
  const live = Object.values(cursors).filter((c) => now - c.ts < CURSOR_STALE_S);
  if (live.length === 0) return null;

  return (
    <div className="share-cursors">
      {live.map((c) => (
        <div
          key={c.node_id}
          className="share-cursor"
          style={{ left: `${Math.min(100, Math.max(0, c.x * 100))}%`, top: `${Math.min(100, Math.max(0, c.y * 100))}%` }}
        >
          <div className="share-cursor__dot" />
          <span className="share-cursor__label">{c.name}</span>
        </div>
      ))}
    </div>
  );
}

/** How long a pointer stands before it is treated as gone. */
const CURSOR_STALE_S = 15;

function rungOf(grant: GrantLevel): number {
  return GRANT_LADDER.indexOf(grant);
}

/**
 * How a rung reads in the list. Anything past `edit` is drawn as a warning —
 * not because it is wrong, but because "somebody else can run commands here"
 * should never be a quiet row.
 */
function grantKind(grant: GrantLevel): RowKind {
  if (grant === 'view') return 'idle';
  if (rungOf(grant) >= rungOf('terminal')) return 'warn';
  return 'info';
}

function GrantPicker({
  value,
  onChange,
  disabled,
}: {
  value: GrantLevel;
  onChange: (next: GrantLevel) => void;
  disabled?: boolean;
}) {
  return (
    <select
      value={value}
      disabled={disabled}
      aria-label="Grant level"
      title={GRANT_BLURB[value]}
      onChange={(e) => onChange(e.target.value as GrantLevel)}
    >
      {GRANT_LADDER.map((level) => (
        <option key={level} value={level}>
          {level}
        </option>
      ))}
    </select>
  );
}

/**
 * The pre-flight warning.
 *
 * Deliberately blocking, deliberately specific, and deliberately phrased as what
 * *will* happen rather than what might. The semantic mirror can withhold a pane;
 * a screen capture cannot, so this is the last point at which the difference can
 * be explained to the person it affects.
 */
function PreflightWarning({
  result,
  onCancel,
  onConfirm,
}: {
  result: Preflight;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <EmptyState
      title={
        <>
          <IconAlert /> {result.undeclared.length} pane
          {result.undeclared.length === 1 ? '' : 's'} would be visible
        </>
      }
      actions={
        <>
          <Button intent="ghost" onClick={onCancel}>
            Cancel
          </Button>
          <Button intent="danger" onClick={onConfirm}>
            Share anyway
          </Button>
        </>
      }
    >
      A screen capture sends light, not structure — so unlike the mirror it cannot
      hide anything on screen. These panes never declared themselves shareable and
      will be in the video:
      <ul style={{ margin: '8px 0 0', paddingLeft: '1.1rem' }}>
        {result.undeclared.map((p) => (
          <li key={p.instanceId}>{p.title}</li>
        ))}
      </ul>
    </EmptyState>
  );
}

export function SessionPanel() {
  const state = useSyncExternalStore(subscribeShare, getShareSnapshot, getShareSnapshot);
  const stream = useSyncExternalStore(subscribeStream, getStreamState, getStreamState);
  const [title, setTitle] = useState('');
  const [invitees, setInvitees] = useState<Invitee[] | null>(null);
  const [picking, setPicking] = useState(false);
  const [pending, setPending] = useState<Preflight | null>(null);
  const [passphrase, setPassphrase] = useState('');
  const [minting, setMinting] = useState(false);
  const [linkError, setLinkError] = useState<string | null>(null);

  const makeLink = useCallback(async () => {
    setMinting(true);
    setLinkError(null);
    try {
      // The relay reports a misconfiguration as text rather than a status code,
      // because "no relay configured" is a thing the host can fix and a 500 is not.
      const link = await mintLink({ passphrase: passphrase.trim() });
      if (link.error) setLinkError(link.error);
      else {
        setPassphrase('');
        // A share that is already running has to be pushed to the link we just
        // made. Minting is deliberately independent of sharing, so this is the
        // ordinary "start sharing, then decide to make it public" order — and
        // without this it silently produced a link nobody could ever watch.
        await attachRelay();
      }
    } catch (err) {
      setLinkError((err as Error).message);
    } finally {
      setMinting(false);
    }
  }, [passphrase]);

  const dropLink = useCallback(async () => {
    setLinkError(null);
    await revokeLink();
  }, []);

  const capture = useMemo(() => probeCapture(), []);
  const hosting = state.hosting;

  const [restream, setRestream] = useState<RestreamState | null>(null);

  // Only meaningful while a public link exists — the relay restreams *that*
  // stream, so there is nothing to push without one.
  useEffect(() => {
    if (!hosting?.link) {
      setRestream(null);
      return;
    }
    let cancelled = false;
    void getRestream().then((r) => {
      if (!cancelled) setRestream(r);
    });
    return () => {
      cancelled = true;
    };
  }, [hosting?.link]);

  const beginRestream = useCallback(async (destination: string) => {
    setRestream(await startRestream(destination));
  }, []);

  const endRestream = useCallback(async () => {
    await stopRestream();
    setRestream(await getRestream());
  }, []);


  const beginStream = useCallback(async (force: boolean) => {
    const blocked = await startStream(force);
    setPending(blocked);
  }, []);

  useEffect(() => {
    requestShareState();
  }, []);

  const loadInvitees = useCallback(() => {
    getInvitees()
      .then(setInvitees)
      .catch(() => setInvitees([]));
  }, []);

  useEffect(() => {
    if (picking) loadInvitees();
  }, [picking, loadInvitees]);

  const guests = hosting?.participants.filter((p) => p.role === 'guest') ?? [];
  const raised = guests.filter((p) => p.grant !== 'view').length;

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 16,
        padding: 16,
        height: '100%',
        overflowY: 'auto',
      }}
    >
      <PaneHeader
        title="Share"
        meta={
          hosting
            ? [hosting.id, `${guests.length} guest${guests.length === 1 ? '' : 's'}`]
            : undefined
        }
        actions={
          hosting ? (
            <>
              <Button
                intent="ghost"
                size="sm"
                disabled={raised === 0}
                title="Drop every guest back to view-only without ending the session."
                onClick={revokeAllViaChannel}
              >
                Revoke all
              </Button>
              <Button intent="danger" size="sm" icon={<IconTrash />} onClick={stopViaChannel}>
                Stop
              </Button>
            </>
          ) : undefined
        }
      />

      {!hosting && (
        <EmptyState
          title="Nothing shared"
          actions={
            <Button
              intent="primary"
              icon={<IconPlus />}
              onClick={() => {
                startViaChannel(title);
                setTitle('');
              }}
            >
              Start session
            </Button>
          }
        >
          <Field label="Session name" hint="What you are working on. Guests see this.">
            <input
              value={title}
              placeholder="debugging the crawler"
              onChange={(e) => setTitle(e.target.value)}
            />
          </Field>
          Start a session, then invite a friend. Everyone joins view-only — nothing is shared until
          you say which panes, and nobody can act until you raise them.
        </EmptyState>
      )}

      {hosting && (
        <>
          {/* Positioned so the overlay has a box to sit in. The cursors are
              pointer-events:none, so nothing here intercepts the host's clicks. */}
          <div style={{ position: 'relative' }}>
            <GuestCursors cursors={state.cursors} />
          </div>

          <DataList label="Participants">
            {hosting.participants.map((p, i) => (
              <DataRow
                key={p.node_id}
                index={i}
                title={p.name}
                kind={p.role === 'host' ? 'info' : grantKind(p.grant)}
                hideMark={p.role === 'host'}
                meta={[p.node_id.slice(0, 8), p.role]}
                badge={p.role === 'host' ? <Chip kind="info">you</Chip> : undefined}
                actions={
                  p.role === 'guest' ? (
                    <>
                      <GrantPicker
                        value={p.grant}
                        onChange={(next) => grantViaChannel(p.person_id, next)}
                      />
                      <Button
                        intent="ghost"
                        size="sm"
                        title="Remove from the session"
                        onClick={() => kickViaChannel(p.node_id)}
                      >
                        Remove
                      </Button>
                    </>
                  ) : undefined
                }
              >
                {p.role === 'guest' ? GRANT_BLURB[p.grant] : 'Hosting this session.'}
              </DataRow>
            ))}
          </DataList>

          {guests.length === 0 && (
            <EmptyState title="No guests yet">
              Invite a friend whose machine is online. They join view-only.
            </EmptyState>
          )}

          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <Button icon={<IconPlus />} onClick={() => setPicking((v) => !v)}>
              {picking ? 'Done' : 'Invite a friend'}
            </Button>
            {/* What the guests can actually see. The one thing a person sharing
                a workspace needs and cannot otherwise get: a redaction model
                nobody can audit is a redaction model nobody trusts. */}
            {hosting.mirror_panes === null ? (
              <Chip title="Your workspace has not been projected yet.">not projected</Chip>
            ) : (
              <Chip
                kind={hosting.mirror_hidden ? 'warn' : 'ok'}
                dot
                title={
                  `Guests see ${hosting.mirror_panes - (hosting.mirror_hidden ?? 0)} of your ` +
                  `${hosting.mirror_panes} panes. The rest are redacted — not even their titles left this machine.`
                }
              >
                {hosting.mirror_panes - (hosting.mirror_hidden ?? 0)}/{hosting.mirror_panes} panes
                visible
              </Chip>
            )}
            {stream.live ? (
              <Button intent="danger" size="sm" onClick={() => void stopStream()}>
                Stop sharing screen
              </Button>
            ) : (
              <Button
                size="sm"
                disabled={capture.support !== 'available'}
                title={capture.reason}
                onClick={() => void beginStream(false)}
              >
                Share screen
              </Button>
            )}
            {stream.live && (
              <Chip kind="ok" dot title={`${stream.peers} guest connection(s).`}>
                {stream.audio ? 'screen + audio' : 'screen, no audio'}
              </Chip>
            )}
            {hosting.link ? (
              <Chip kind="ok" dot>
                public link live
              </Chip>
            ) : (
              <Chip title="No public link has been minted. Only invited friends can reach this session.">
                fabric only
              </Chip>
            )}
            {stream.live && hosting.link && (
              <Chip
                kind={RELAY_CHIP[stream.relayState].kind}
                dot
                title={stream.relayError ?? RELAY_CHIP[stream.relayState].title}
              >
                {RELAY_CHIP[stream.relayState].label}
                {stream.relayState === 'live' && stream.relayViewers > 0
                  ? ` · ${stream.relayViewers}`
                  : ''}
              </Chip>
            )}
          </div>

          {/* The public link is its own row, not a button beside Share screen:
              minting one is the single most consequential thing in this pane and
              deliberately never happens as a side effect of starting a share. */}
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            {hosting.link ? (
              <>
                <CopyableLink url={hosting.link} label={hosting.link} />
                <Button size="sm" intent="danger" onClick={() => void dropLink()}>
                  Revoke link
                </Button>
              </>
            ) : (
              <>
                <input
                  value={passphrase}
                  onChange={(e) => setPassphrase(e.target.value)}
                  placeholder="passphrase (optional)"
                  style={{
                    height: 30,
                    borderRadius: 6,
                    border: '1px solid var(--border)',
                    background: 'var(--bg-inset)',
                    color: 'var(--text-primary)',
                    padding: '0 10px',
                    fontSize: 12.5,
                  }}
                />
                <Button size="sm" onClick={() => void makeLink()} disabled={minting}>
                  {minting ? 'Minting…' : 'Create public link'}
                </Button>
              </>
            )}
          </div>

          {/* Restreaming is deliberately below the link and only appears with one:
              it pushes the public stream, so without a link there is nothing to
              push. Starting a broadcast to a platform is never implicit. */}
          {hosting.link && restream && (
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              {restream.live ? (
                <>
                  <Chip kind="ok" dot>
                    broadcasting to {restream.label}
                  </Chip>
                  <Button size="sm" intent="danger" onClick={() => void endRestream()}>
                    Stop broadcast
                  </Button>
                </>
              ) : (
                restream.available.map((d) => (
                  <Button key={d} size="sm" onClick={() => void beginRestream(d)}>
                    Go live on {DESTINATION_LABEL[d] ?? d}
                  </Button>
                ))
              )}
              {restream.available.length === 0 && !restream.live && (
                <span style={{ fontSize: 12.5, color: 'var(--text-dim)' }}>
                  Add a stream key in the <strong>Streaming</strong> connector to broadcast
                  this to Twitch, YouTube or your own RTMP server.
                </span>
              )}
            </div>
          )}

          {restream?.error && (
            <p style={{ margin: 0, fontSize: 12.5, color: 'var(--danger)' }}>
              {restream.error}
            </p>
          )}

          {linkError && (
            <p style={{ margin: 0, fontSize: 12.5, color: 'var(--danger)' }}>{linkError}</p>
          )}

          {stream.live && stream.relayError && (
            <p style={{ margin: 0, fontSize: 12.5, color: 'var(--warn)' }}>
              The public link is live but the stream is not reaching the relay:{' '}
              {stream.relayError} Friends on the fabric are unaffected.
            </p>
          )}

          {pending && (
            <PreflightWarning
              result={pending}
              onCancel={() => setPending(null)}
              onConfirm={() => {
                setPending(null);
                void beginStream(true);
              }}
            />
          )}

          {capture.support !== 'available' && (
            // The probe's reason was a `title` only, which is a hover tooltip: it
            // never appears on a touch device, is never read aloud, and cannot be
            // seen at all on the disabled button most people will actually look
            // at. "An unexplained no is the failure" means visible text.
            <p
              style={{
                margin: 0,
                fontSize: 12.5,
                color: capture.support === 'insecure-context' ? 'var(--warn)' : 'var(--text-dim)',
              }}
            >
              {capture.reason}
            </p>
          )}

          {stream.error && (
            <p style={{ margin: 0, fontSize: 12.5, color: 'var(--danger)' }}>{stream.error}</p>
          )}

          {stream.live && !stream.audio && !stream.audioFault && (
            <p style={{ margin: 0, fontSize: 12.5, color: 'var(--text-dim)' }}>
              Guests hear nothing. Route a strip to the <strong>Viewers</strong> bus in the
              audio mixer to send them sound — starting a share deliberately does not move
              audio on its own.
            </p>
          )}

          {stream.live && stream.audioFault && (
            <p style={{ margin: 0, fontSize: 12.5, color: 'var(--warn)' }}>
              {stream.audioFault} Guests are getting video only; reopen the audio mixer to
              retry, then restart the share.
            </p>
          )}

          {state.audit.length > 0 && (
            <DataList label="What guests have done">
              {[...state.audit]
                .slice(-12)
                .reverse()
                .map((entry, i) => (
                  <DataRow
                    key={`${entry.ts}-${i}`}
                    index={i}
                    title={entry.action}
                    // A refusal is the more interesting half — it is the only
                    // trace a blocked guest leaves anywhere.
                    kind={AUDIT_KIND[entry.outcome] ?? 'idle'}
                    meta={[
                      entry.name,
                      entry.outcome,
                      String(entry.detail.specifier ?? ''),
                    ].filter(Boolean)}
                  >
                    {entry.reason}
                  </DataRow>
                ))}
            </DataList>
          )}

          {picking && (
            <DataList label="Friends you can invite">
              {invitees?.length === 0 && (
                <DataRow title="Nobody online" kind="idle" hideMark index={0}>
                  None of your friends has a machine connected right now.
                </DataRow>
              )}
              {(invitees ?? []).map((f, i) => (
                <DataRow
                  key={f.person_id}
                  index={i}
                  title={f.name}
                  kind={f.can_share ? 'idle' : 'warn'}
                  hideMark
                  meta={[
                    f.username ? `@${f.username}` : f.friend_code,
                    `${f.devices_online} online`,
                  ]}
                  actions={
                    <Button
                      size="sm"
                      icon={<IconCheck />}
                      disabled={!f.can_share}
                      onClick={() => inviteViaChannel(f.person_id)}
                    >
                      Invite
                    </Button>
                  }
                >
                  {f.can_share
                    ? 'Invites every machine of theirs that is online — they pick one.'
                    : 'Online, but their app does not support shared sessions yet.'}
                </DataRow>
              ))}
            </DataList>
          )}
        </>
      )}

      {state.invites.length > 0 && (
        <DataList label="Invitations">
          {state.invites.map((invite, i) => (
            <DataRow
              key={invite.session_id}
              index={i}
              title={invite.title}
              kind="info"
              meta={[invite.host_name, invite.host_device].filter(Boolean)}
              actions={
                <>
                  <Button
                    intent="primary"
                    size="sm"
                    onClick={() => joinViaChannel(invite.session_id, invite.host)}
                  >
                    Join
                  </Button>
                  <Button
                    intent="ghost"
                    size="sm"
                    onClick={() => dismissInviteViaChannel(invite.session_id)}
                  >
                    Dismiss
                  </Button>
                </>
              }
            >
              {invite.host_name} invited you to their workspace.
            </DataRow>
          ))}
        </DataList>
      )}

      {state.joined.length > 0 && (
        <DataList label="Sessions you have joined">
          {state.joined.map((s, i) => (
            <DataRow
              key={s.id}
              index={i}
              title={s.title}
              kind={grantKind(s.grant)}
              meta={[s.host_name, s.grant]}
              actions={
                <Button intent="ghost" size="sm" onClick={() => leaveViaChannel(s.id)}>
                  Leave
                </Button>
              }
            >
              {GRANT_BLURB[s.grant]}
            </DataRow>
          ))}
        </DataList>
      )}

      {state.error && (
        <EmptyState
          title={
            <>
              <IconAlert /> Something went wrong
            </>
          }
        >
          {state.error}
        </EmptyState>
      )}
    </div>
  );
}
