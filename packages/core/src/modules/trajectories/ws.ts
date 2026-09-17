/**
 * Client for the `/ws` `trajectories` channel: runs and their steps as they happen.
 *
 * ## Subscribe first, fetch second
 *
 * The obvious order — fetch the run, then subscribe — drops every step that lands
 * between the two, silently and permanently. (That race is live in
 * `telemetry/stream.py`, which drains its backlog and only then subscribes; it is
 * the reason this file does not copy it.) So we subscribe first, buffer what
 * arrives, then fetch, then reconcile.
 *
 * ## `seq` is the cursor, and it is free
 *
 * `traj_steps.seq` is a per-run monotonic primary key, so a buffered step is a
 * duplicate exactly when its `seq` is below what the snapshot already contains. No
 * server-side cursor, no `since` parameter, no per-subscriber state anywhere.
 *
 * ## The socket has no past
 *
 * A run that started before this tab connected emits nothing until its next step, so
 * a socket-only view opens empty and stays empty however much work is in flight. The
 * backlog is therefore fetched once on start and again on every reconnect — after
 * subscribing, never before, so the same ordering that protects the step buffer
 * protects the run list.
 *
 * ## A disconnect is refetched, not replayed
 *
 * Steps emitted while the socket was down are gone from the wire — no server keeps
 * them for us. Refetching every open run on reconnect is the only honest recovery;
 * pretending the buffer is complete would render a run with a hole in the middle
 * and no indication there was one.
 */
import {
  getRun,
  listRuns,
  getPeerRun,
  listPeerDatasets,
  listPeerRuns,
  listWatching,
  type PeerRun,
  type TrajectoryDetail,
  type TrajectoryRun,
  type TrajectoryStep,
} from './api';
import { onSocketOpen, subscribeChannel } from '../../ws';

/** A step as it arrives live: payloads above the wire cap are replaced by sizes. */
export interface LiveStep extends TrajectoryStep {
  /** Size of `args` in bytes, or null when there were none. */
  args_bytes?: number | null;
  /** Size of `result` in bytes, or null when there were none. */
  result_bytes?: number | null;
}

export interface LiveRun {
  run: TrajectoryRun;
  steps: LiveStep[];
  /** True until a `seal` arrives, or the snapshot says the run is already over. */
  live: boolean;
}

/**
 * A friend's run, streamed to this node because it joined their share session.
 *
 * Kept in its own map, never merged into `runs`: run ids are only unique per node, so
 * a friend's run could share an id with one of ours, and a single map keyed by id would
 * silently overwrite one with the other.
 */
export interface PeerLiveRun {
  host: string;
  hostName: string;
  run: PeerRun;
  steps: LiveStep[];
  live: boolean;
}

export interface TrajectoriesLiveState {
  /** Keyed by run id, newest-started first when listed. */
  runs: Map<string, LiveRun>;
  /** Keyed by `host:runId` — see `PeerLiveRun`. */
  peers: Map<string, PeerLiveRun>;
}

let state: TrajectoriesLiveState = { runs: new Map(), peers: new Map() };
const listeners = new Set<() => void>();

/** Steps seen before that run's snapshot came back. */
const pending = new Map<string, LiveStep[]>();
/** Runs we have asked the server about, so a burst of steps fetches once. */
const fetching = new Set<string>();

function emit(): void {
  state = { runs: new Map(state.runs), peers: new Map(state.peers) };
  listeners.forEach((l) => l());
}

export function getTrajectoriesLive(): TrajectoriesLiveState {
  return state;
}

export function subscribeTrajectoriesLive(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/**
 * Running runs, most recently started first.
 *
 * Takes the state rather than reading the module's own: a component must derive
 * from the snapshot `useSyncExternalStore` handed it, or it renders a list that the
 * store has already moved past and React has no way to know it is stale.
 */
export function liveRuns(from: TrajectoriesLiveState): LiveRun[] {
  return [...from.runs.values()]
    .filter((r) => r.live)
    .sort((a, b) => b.run.started_at - a.run.started_at);
}

/** Friends' runs in flight, grouped by host in arrival order within each. */
export function livePeerRuns(from: TrajectoriesLiveState): PeerLiveRun[] {
  return [...from.peers.values()]
    .filter((r) => r.live)
    .sort((a, b) => b.run.started_at - a.run.started_at);
}

/**
 * Merge buffered and incoming steps into a run, dropping duplicates by `seq`.
 *
 * Exported for tests: this is the only part of the module with a correctness
 * argument, and it is pure.
 */
export function mergeSteps(existing: LiveStep[], incoming: LiveStep[]): LiveStep[] {
  if (!incoming.length) return existing;
  const bySeq = new Map<number, LiveStep>();
  for (const step of existing) bySeq.set(step.seq, step);
  // Incoming wins on a tie: a re-emitted step is a correction, not a duplicate.
  for (const step of incoming) bySeq.set(step.seq, step);
  return [...bySeq.values()].sort((a, b) => a.seq - b.seq);
}

function upsertRun(run: TrajectoryRun): void {
  const prev = state.runs.get(run.id);
  state.runs.set(run.id, {
    run,
    steps: prev?.steps ?? [],
    live: run.status === 'running',
  });
  // A run can reach us already several steps old — the pane opened mid-turn, or the
  // writer suppressed per-step frames because it wrote the run in bulk. The header
  // alone would then render "0 steps" beside a live pulse and an empty timeline,
  // which reads as an agent that has done nothing rather than one we joined late.
  if (run.status === 'running' && run.steps > 0 && !prev?.steps.length) {
    void hydrate(run.id);
  }
}

async function hydrate(runId: string): Promise<void> {
  if (fetching.has(runId)) return;
  fetching.add(runId);
  try {
    const detail: TrajectoryDetail = await getRun(runId);
    const buffered = pending.get(runId) ?? [];
    pending.delete(runId);
    state.runs.set(runId, {
      run: detail,
      steps: mergeSteps(detail.step_list as LiveStep[], buffered),
      live: detail.status === 'running',
    });
    emit();
  } catch {
    // A run we cannot read is a run we cannot show. Leave the buffer in place so a
    // later reconnect retries rather than stranding the steps we did receive.
  } finally {
    fetching.delete(runId);
  }
}

function onStep(runId: string, step: LiveStep): void {
  const known = state.runs.get(runId);
  if (!known) {
    // A step for a run we have never seen — we joined mid-flight, or the `run`
    // frame was sent before this tab connected. Buffer and go and get the header.
    pending.set(runId, [...(pending.get(runId) ?? []), step]);
    void hydrate(runId);
    return;
  }
  state.runs.set(runId, { ...known, steps: mergeSteps(known.steps, [step]) });
  emit();
}

// ---- friends' runs ---------------------------------------------------------------

const peerKey = (host: string, runId: string) => `${host}:${runId}`;
const peerPending = new Map<string, LiveStep[]>();
const peerFetching = new Set<string>();

async function hydratePeer(host: string, hostName: string, runId: string): Promise<void> {
  const key = peerKey(host, runId);
  if (peerFetching.has(key)) return;
  peerFetching.add(key);
  try {
    // The friend's node pages and redacts this; it arrives already safe to show.
    const detail = await getPeerRun(host, runId);
    const buffered = peerPending.get(key) ?? [];
    peerPending.delete(key);
    state.peers.set(key, {
      host,
      hostName,
      run: detail.run,
      steps: mergeSteps(detail.steps as LiveStep[], buffered),
      live: detail.run.status === 'running',
    });
    emit();
  } catch {
    // The friend left, stopped sharing, or went quiet. The buffer stays, so the next
    // reconnect can try again rather than stranding what did arrive.
  } finally {
    peerFetching.delete(key);
  }
}

/** Apply one `peer` frame: the same run/step/seal vocabulary, tagged with its host. */
function onPeerFrame(data: Record<string, unknown>): void {
  const host = String(data.host ?? '');
  const hostName = String(data.hostName ?? 'a friend');
  const inner = (data.data ?? {}) as Record<string, unknown>;
  if (!host) return;

  if (data.event === 'run') {
    const run = inner as unknown as PeerRun;
    const key = peerKey(host, run.id);
    const prev = state.peers.get(key);
    state.peers.set(key, {
      host,
      hostName,
      run,
      steps: prev?.steps ?? [],
      live: run.status === 'running',
    });
    emit();
    if (run.status === 'running' && run.steps > 0 && !prev?.steps.length) {
      void hydratePeer(host, hostName, run.id);
    }
  } else if (data.event === 'step') {
    const runId = String(inner.runId ?? '');
    const key = peerKey(host, runId);
    const known = state.peers.get(key);
    const step = inner.step as LiveStep;
    if (!known) {
      peerPending.set(key, [...(peerPending.get(key) ?? []), step]);
      void hydratePeer(host, hostName, runId);
      return;
    }
    state.peers.set(key, { ...known, steps: mergeSteps(known.steps, [step]) });
    emit();
  } else if (data.event === 'seal') {
    const key = peerKey(host, String(inner.runId ?? ''));
    const known = state.peers.get(key);
    if (!known) return;
    state.peers.set(key, {
      ...known,
      live: false,
      run: {
        ...known.run,
        status: (inner.status as PeerRun['status']) ?? 'complete',
        steps: (inner.steps as number) ?? known.run.steps,
      },
    });
    emit();
  }
}

/**
 * Catch up on friends' runs already in flight, for every session this node has joined.
 *
 * The same problem `loadInFlight` solves for local runs — the stream has no past —
 * plus one of its own: when a session ends, its host's runs must leave the view, or a
 * friend you stopped watching keeps a live pulse on your screen indefinitely.
 */
async function loadWatching(): Promise<void> {
  let sessions;
  try {
    sessions = await listWatching();
  } catch {
    return;
  }
  const hosts = new Set(sessions.map((s) => s.host));
  let dropped = false;
  for (const [key, entry] of state.peers) {
    if (!hosts.has(entry.host)) {
      state.peers.delete(key);
      dropped = true;
    }
  }
  if (dropped) emit();

  for (const session of sessions) {
    try {
      const datasets = await listPeerDatasets(session.host);
      for (const dataset of datasets) {
        const runs = await listPeerRuns(session.host, dataset.id, 'running');
        for (const run of runs) void hydratePeer(session.host, session.host_name, run.id);
      }
    } catch {
      // One unreachable host must not stop the others from loading.
    }
  }
}

let started = false;

/**
 * Load the runs that are already in flight.
 *
 * Deliberately not merged with `hydrate`: this discovers runs we have never heard
 * of, where that one fills in a run we already know about.
 */
async function loadInFlight(): Promise<void> {
  try {
    const { runs } = await listRuns({ status: 'running', limit: 50 });
    for (const run of runs) upsertRun(run);
    emit();
  } catch {
    // Leave the view empty rather than asserting nothing is running: the empty
    // state says capture may be off, which is the likelier cause either way.
  }
}

export function initTrajectoriesLive(): void {
  if (started) return;
  started = true;

  subscribeChannel('trajectories', (msg) => {
    const data = msg.data as Record<string, unknown>;
    if (msg.event === 'run') {
      upsertRun(data as unknown as TrajectoryRun);
      emit();
    } else if (msg.event === 'step') {
      onStep(String(data.runId), data.step as LiveStep);
    } else if (msg.event === 'peer') {
      onPeerFrame(data);
    } else if (msg.event === 'seal') {
      const runId = String(data.runId);
      const known = state.runs.get(runId);
      if (!known) return;
      state.runs.set(runId, {
        ...known,
        live: false,
        run: {
          ...known.run,
          status: (data.status as TrajectoryRun['status']) ?? 'complete',
          steps: (data.steps as number) ?? known.run.steps,
          rounds: (data.rounds as number) ?? known.run.rounds,
          duration_ms: (data.duration_ms as number | null) ?? known.run.duration_ms,
        },
      });
      emit();
    }
  });

  onSocketOpen(() => {
    // Anything emitted while we were disconnected is unrecoverable from the wire,
    // so every run still believed live is refetched whole — and the list itself is
    // re-read, because a run that both started and finished during the gap would
    // otherwise never appear at all.
    for (const [runId, entry] of state.runs) {
      if (entry.live) void hydrate(runId);
    }
    void loadInFlight();
    void loadWatching();
  });
}

/** Test hook. */
export function resetTrajectoriesLive(): void {
  state = { runs: new Map(), peers: new Map() };
  pending.clear();
  fetching.clear();
  peerPending.clear();
  peerFetching.clear();
  started = false;
  listeners.clear();
}
