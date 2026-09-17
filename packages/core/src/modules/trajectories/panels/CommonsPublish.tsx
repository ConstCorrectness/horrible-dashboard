/**
 * Publishing one run to the agent commons — preview first, then a typed confirmation.
 *
 * A toggle would be wrong here. The commons is strangers, re-served by an index this
 * node does not run, and **a federated index has no recall**: unpublishing stops future
 * fetches and reaches no copy already taken. So the panel shows the exact digest that
 * would leave, and publishing needs the short code printed under it. The code is the
 * start of the digest's content hash, which the backend checks against the digest it
 * rebuilds at send time — so a confirmation typed against one preview cannot publish
 * different content (a goal toggled on afterwards, a label added since).
 */
import { useEffect, useState } from 'react';

import { Button } from '../../../Primitives';
import { getCommonsDigest, publishRun, type CommonsDigest } from '../api';
import { card, heading, Json, mono } from './common';

export function CommonsPublish({ runId, running }: { runId: string; running: boolean }) {
  const [open, setOpen] = useState(false);
  const [includeGoal, setIncludeGoal] = useState(false);
  const [preview, setPreview] = useState<{ digest: CommonsDigest; confirm: string } | null>(null);
  const [typed, setTyped] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');

  useEffect(() => {
    setPreview(null);
    setTyped('');
    if (!open) return;
    let cancelled = false;
    getCommonsDigest(runId, includeGoal)
      .then((p) => !cancelled && setPreview(p))
      .catch((err: Error) => !cancelled && setMessage(err.message));
    return () => {
      cancelled = true;
    };
  }, [open, runId, includeGoal]);

  useEffect(() => {
    setOpen(false);
    setMessage('');
  }, [runId]);

  if (!open) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)' }}>
        <Button
          size="sm"
          intent="ghost"
          disabled={running}
          onClick={() => {
            setMessage('');
            setOpen(true);
          }}
        >
          Publish to commons…
        </Button>
        {running ? <span style={mono}>publishable once it finishes</span> : null}
        {message ? <span style={mono}>{message}</span> : null}
      </div>
    );
  }

  const publish = async () => {
    setBusy(true);
    setMessage('');
    try {
      const out = await publishRun(runId, { include_goal: includeGoal, confirm: typed });
      setMessage(
        out.duplicate
          ? `Already on the index as ${out.digest_id.slice(0, 12)}.`
          : `Published as ${out.digest_id.slice(0, 12)}.`,
      );
      setOpen(false);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ ...card, borderTop: '2px solid var(--warn)' }}>
      <div style={heading}>Publish to the agent commons</div>
      <div style={{ ...mono, marginTop: 'var(--space-2)' }}>
        What leaves is below, exactly. It is signed with this node&apos;s key, so it names this
        node. No arguments, results or messages are included — but tool names are, and an MCP server
        id says whatever you named it. The index cannot recall a copy someone already fetched.
      </div>
      <label
        style={{
          ...mono,
          display: 'flex',
          alignItems: 'center',
          gap: 'var(--space-2)',
          marginTop: 'var(--space-3)',
        }}
      >
        <input
          type="checkbox"
          checked={includeGoal}
          onChange={(e) => setIncludeGoal(e.target.checked)}
        />
        include the goal text (scrubbed, first 500 characters)
      </label>
      {preview ? (
        <>
          <Json value={preview.digest} />
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 'var(--space-3)',
              marginTop: 'var(--space-3)',
            }}
          >
            <span style={mono}>
              type <strong>{preview.confirm}</strong> to publish
            </span>
            <input
              type="text"
              aria-label="Confirmation code"
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              style={{ width: 110, padding: '0 0.6rem' }}
              spellCheck={false}
            />
            <span style={{ flex: 1 }} />
            <Button size="sm" intent="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button
              size="sm"
              intent="danger"
              disabled={busy || typed.trim().toLowerCase() !== preview.confirm}
              onClick={() => void publish()}
            >
              {busy ? 'Publishing…' : 'Publish'}
            </Button>
          </div>
        </>
      ) : message ? null : (
        // A refusal (a run from a friend, one still running) lands in `message` below,
        // and must replace this line rather than sit under a spinner that never ends.
        <div style={{ ...mono, marginTop: 'var(--space-3)' }}>Building the digest…</div>
      )}
      {message ? <div style={{ ...mono, marginTop: 'var(--space-2)' }}>{message}</div> : null}
    </div>
  );
}
