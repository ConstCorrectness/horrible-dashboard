/**
 * Friends' shared runs: browse them on the friend's node, pull a copy of a finished one.
 *
 * Nothing here is cached or mirrored locally until you pull. Browsing asks the friend's
 * node each time, which is the honest reading of "shared": a dataset they stop sharing
 * disappears from this list at once, rather than lingering from a stale copy.
 *
 * A pulled run lands in a `peer-…` dataset, keyed by the friend's authenticated device,
 * and cannot be shared onward — they shared it with you, not with your friends.
 */
import { useCallback, useEffect, useState } from 'react';

import { DataList, DataRow } from '../../../DataList';
import { Button, Chip, EmptyState, PaneHeader } from '../../../Primitives';
import {
  listPeerDatasets,
  listPeerRuns,
  listPeers,
  pullPeerRun,
  type PeerDataset,
  type PeerPerson,
  type PeerRun,
} from '../api';
import { RefreshIcon } from '../icons';
import { ago, bodyScroll, heading, mono, SectionShell } from './common';

interface Selection {
  node: string;
  label: string;
  person: string;
}

export function PeersSection({ onPulled }: { onPulled: () => void }) {
  const [people, setPeople] = useState<PeerPerson[] | null>(null);
  const [device, setDevice] = useState<Selection | null>(null);
  const [datasets, setDatasets] = useState<PeerDataset[] | null>(null);
  const [dataset, setDataset] = useState<string>('');
  const [runs, setRuns] = useState<PeerRun[] | null>(null);
  const [busy, setBusy] = useState<string>('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const loadPeople = useCallback(async () => {
    try {
      setPeople(await listPeers());
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void loadPeople();
  }, [loadPeople]);

  useEffect(() => {
    setDatasets(null);
    setDataset('');
    setRuns(null);
    if (!device) return;
    let cancelled = false;
    listPeerDatasets(device.node)
      .then((rows) => !cancelled && setDatasets(rows))
      .catch((err: Error) => !cancelled && setError(err.message));
    return () => {
      cancelled = true;
    };
  }, [device]);

  useEffect(() => {
    setRuns(null);
    if (!device || !dataset) return;
    let cancelled = false;
    listPeerRuns(device.node, dataset)
      .then((rows) => !cancelled && setRuns(rows))
      .catch((err: Error) => !cancelled && setError(err.message));
    return () => {
      cancelled = true;
    };
  }, [device, dataset]);

  const pull = async (run: PeerRun) => {
    if (!device) return;
    setBusy(run.id);
    setNotice('');
    try {
      await pullPeerRun(device.node, run.id);
      setNotice(`Pulled "${run.goal || run.id}" from ${device.person}.`);
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
          title="Friends"
          meta={people ? [`${people.length} online`] : []}
          actions={
            <Button
              size="sm"
              intent="ghost"
              icon={<RefreshIcon />}
              onClick={() => void loadPeople()}
            >
              Refresh
            </Button>
          }
        />
      }
    >
      <div style={{ ...bodyScroll, padding: 'var(--space-5)' }}>
        {notice ? (
          <div
            style={{
              ...mono,
              display: 'flex',
              alignItems: 'center',
              gap: 'var(--space-3)',
              marginBottom: 'var(--space-4)',
            }}
          >
            <span style={{ flex: 1 }}>{notice}</span>
            <Button size="sm" onClick={onPulled}>
              Open Runs
            </Button>
          </div>
        ) : null}

        {people === null ? (
          <div style={mono}>Loading…</div>
        ) : people.length === 0 ? (
          <EmptyState title="No friends online who can share runs">
            A friend appears here when one of their devices is connected and running a build that
            shares trajectories. They choose which datasets to share; nothing is visible until they
            do.
          </EmptyState>
        ) : (
          <>
            <div style={{ ...heading, marginBottom: 'var(--space-3)' }}>Devices</div>
            <DataList label="Friends' devices">
              {people.flatMap((person) =>
                person.devices.map((d, index) => (
                  <DataRow
                    key={d.node_id}
                    index={index}
                    kind={device?.node === d.node_id ? 'info' : 'idle'}
                    title={`${person.name} — ${d.label}`}
                    meta={[
                      d.shared_datasets == null
                        ? 'sharing unknown'
                        : `${d.shared_datasets} shared ${d.shared_datasets === 1 ? 'dataset' : 'datasets'}`,
                    ]}
                    onClick={() =>
                      setDevice({ node: d.node_id, label: d.label, person: person.name })
                    }
                  />
                )),
              )}
            </DataList>
          </>
        )}

        {device ? (
          <div style={{ marginTop: 'var(--space-6)' }}>
            <div style={{ ...heading, marginBottom: 'var(--space-3)' }}>
              Shared by {device.person}
            </div>
            {datasets === null ? (
              <div style={mono}>Asking {device.label}…</div>
            ) : datasets.length === 0 ? (
              <div style={mono}>{device.person} is not sharing any datasets from this device.</div>
            ) : (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--space-2)' }}>
                {datasets.map((d) => (
                  <Button
                    key={d.id}
                    size="sm"
                    intent={d.id === dataset ? 'primary' : 'ghost'}
                    onClick={() => setDataset(d.id)}
                  >
                    {d.name} · {d.run_count}
                  </Button>
                ))}
              </div>
            )}
          </div>
        ) : null}

        {device && dataset ? (
          <div style={{ marginTop: 'var(--space-5)' }}>
            {runs === null ? (
              <div style={mono}>Loading runs…</div>
            ) : runs.length === 0 ? (
              <div style={mono}>No runs in this dataset.</div>
            ) : (
              <DataList label="Shared runs">
                {runs.map((run, index) => (
                  <DataRow
                    key={run.id}
                    index={index}
                    kind={run.status === 'running' ? 'warn' : 'idle'}
                    title={run.goal || '(no goal recorded)'}
                    badge={
                      run.status === 'running' ? (
                        <Chip kind="warn" dot>
                          running
                        </Chip>
                      ) : undefined
                    }
                    meta={[
                      run.model || '—',
                      `${run.steps} ${run.steps === 1 ? 'step' : 'steps'}`,
                      ago(run.started_at),
                    ]}
                    actions={
                      // A run in flight is not pullable: its snapshot would be sealed
                      // here as if finished. Said on the button, not only on refusal.
                      run.status === 'running' ? (
                        <span style={mono}>in progress — pull when it ends</span>
                      ) : (
                        <Button size="sm" disabled={busy === run.id} onClick={() => void pull(run)}>
                          {busy === run.id ? 'Pulling…' : 'Pull'}
                        </Button>
                      )
                    }
                  />
                ))}
              </DataList>
            )}
          </div>
        ) : null}
      </div>
    </SectionShell>
  );
}
