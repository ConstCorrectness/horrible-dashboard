/**
 * The agent commons, as trajectories: digests published to the connected index.
 *
 * A digest is a run's *shape* — tool sequence, per-step outcome and timing, rounds,
 * outcome, the harness fingerprint — never its payloads (see
 * `backend/modules/trajectories/commons.py`). "Mine" lists this node's own, which are the
 * only ones it can withdraw, and withdrawing is said for what it is: the index stops
 * serving it; a copy someone already fetched is out of reach.
 *
 * With no index connected the section says so and points at the setting, rather than
 * rendering an empty list that reads as "nobody has published anything".
 */
import { useCallback, useEffect, useState } from 'react';

import { DataList, DataRow } from '../../../DataList';
import { Button, Chip, EmptyState, PaneHeader } from '../../../Primitives';
import { listCommons, unpublishDigest, type CommonsDigest } from '../api';
import { RefreshIcon } from '../icons';
import { ago, bodyScroll, mono, ms, SectionShell, tokens } from './common';

/** The tool sequence, collapsed: `files.read ×3 → editor.open`. */
export function toolSequence(digest: Pick<CommonsDigest, 'steps'>, limit = 6): string {
  const runs: { name: string; count: number }[] = [];
  for (const step of digest.steps) {
    if (!step.name) continue;
    const last = runs[runs.length - 1];
    if (last && last.name === step.name) last.count += 1;
    else runs.push({ name: step.name, count: 1 });
  }
  const shown = runs
    .slice(0, limit)
    .map((r) => (r.count > 1 ? `${r.name} ×${r.count}` : r.name))
    .join(' → ');
  return runs.length > limit ? `${shown} → +${runs.length - limit} more` : shown;
}

export function CommonsSection() {
  const [mine, setMine] = useState(false);
  const [state, setState] = useState<Awaited<ReturnType<typeof listCommons>> | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');

  const load = useCallback(async () => {
    try {
      setState(await listCommons(mine));
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [mine]);

  useEffect(() => {
    setState(null);
    void load();
  }, [load]);

  const withdraw = async (digest: CommonsDigest) => {
    setBusy(digest.digest_id);
    try {
      const out = await unpublishDigest(digest.digest_id);
      if (!out.ok) setError('The index did not withdraw it.');
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy('');
    }
  };

  return (
    <SectionShell
      error={error}
      header={
        <PaneHeader
          title="Commons"
          meta={state?.connected ? [`${state.digests.length} digests`] : ['not connected']}
          actions={
            <>
              <Button size="sm" intent={mine ? 'ghost' : 'primary'} onClick={() => setMine(false)}>
                Everyone
              </Button>
              <Button size="sm" intent={mine ? 'primary' : 'ghost'} onClick={() => setMine(true)}>
                Mine
              </Button>
              <Button size="sm" intent="ghost" icon={<RefreshIcon />} onClick={() => void load()}>
                Refresh
              </Button>
            </>
          }
        />
      }
    >
      <div style={{ ...bodyScroll, padding: 'var(--space-5)' }}>
        {state === null ? (
          <div style={mono}>Loading…</div>
        ) : !state.connected ? (
          <EmptyState title="Not connected to a commons index">
            Turn on the agent commons and set its server URL in Settings (commons.enabled,
            commons.serverUrl). Publishing a run happens from its detail view, after a preview of
            exactly what would leave.
          </EmptyState>
        ) : state.digests.length === 0 ? (
          <EmptyState title={mine ? 'You have not published any runs' : 'Nothing published yet'}>
            Open a finished run and choose Publish to commons. Only its shape is sent — no
            arguments, results or messages.
          </EmptyState>
        ) : (
          <DataList label="Published trajectories">
            {state.digests.map((d, index) => {
              const yours = d.node_id === state.me;
              const failed = d.steps.filter((s) => s.ok === false).length;
              return (
                <DataRow
                  key={d.digest_id}
                  index={index}
                  kind={d.status === 'failed' ? 'fail' : 'idle'}
                  title={d.goal || toolSequence(d) || '(no tool calls)'}
                  badge={
                    yours ? (
                      <Chip kind="info" dot>
                        yours
                      </Chip>
                    ) : undefined
                  }
                  meta={[
                    d.model || '—',
                    `${d.steps.length} steps · ${d.rounds} rounds`,
                    ...(failed ? [`${failed} failed`] : []),
                    ...(tokens(d.tokens_in, d.tokens_out)
                      ? [tokens(d.tokens_in, d.tokens_out)]
                      : []),
                    ms(d.duration_ms),
                    d.publisher_name || d.node_id.slice(0, 8),
                    ...(d.published_at ? [ago(d.published_at)] : []),
                  ]}
                  actions={
                    yours ? (
                      <Button
                        size="sm"
                        intent="ghost"
                        disabled={busy === d.digest_id}
                        title="Stops the index serving it. Copies already fetched are not recalled."
                        onClick={() => void withdraw(d)}
                      >
                        Withdraw
                      </Button>
                    ) : undefined
                  }
                >
                  {d.goal ? <div style={mono}>{toolSequence(d)}</div> : null}
                </DataRow>
              );
            })}
          </DataList>
        )}
      </div>
    </SectionShell>
  );
}
