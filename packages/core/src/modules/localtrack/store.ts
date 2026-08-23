import { useEffect, useState } from 'react';
import { getSetting } from '../../settings';
import { subscribeChannel } from '../../ws';
import {
  createProject,
  deleteProject,
  deleteRun,
  fetchLayout,
  fetchMetricKeys,
  fetchProjects,
  fetchRuns,
  saveLayout,
} from './api';
import type { PanelConfig, Project, Run } from './types';

/**
 * Narrow an unknown thrown value to a message.
 *
 * Every `catch` here used to be `catch (err: any)`, which lint rejects and which
 * also quietly accepts a thrown string or a rejected non-Error. This keeps the
 * message when there is one and says something honest when there isn't — an error
 * banner reading `undefined` is worse than one reading the fallback.
 */
function messageOf(err: unknown, fallback: string): string {
  if (err instanceof Error && err.message) return err.message;
  if (typeof err === 'string' && err) return err;
  return fallback;
}

// W&B-inspired curated distinct colors for multi-run overlays
export const RUN_PALETTE = [
  '#2ecc71', // emerald green (e.g. celestial-lake-3)
  '#3498db', // vibrant blue
  '#9b59b6', // purple
  '#e67e22', // orange
  '#e74c3c', // red
  '#1abc9c', // teal
  '#f1c40f', // yellow
  '#e056fd', // pink
  '#686de0', // indigo
  '#30336b', // deep navy
  '#48dbfb', // cyan
  '#ff9f43', // amber
];

export function getRunColor(runId: string, index = 0): string {
  let hash = 0;
  for (let i = 0; i < runId.length; i++) {
    hash = runId.charCodeAt(i) + ((hash << 5) - hash);
  }
  const idx = Math.abs(hash + index) % RUN_PALETTE.length;
  return RUN_PALETTE[idx];
}

/**
 * How many runs a freshly opened project selects.
 *
 * Every run would make a 200-run project unreadable and slow; the cap is the
 * right call. What was wrong is that it was silent — the sidebar showed 200 rows
 * with 5 ticked and nothing said why. The sidebar now says so.
 */
export const DEFAULT_SELECTION = 5;

const DEFAULT_PANELS: PanelConfig[] = [
  { id: 'p-1', title: 'Training Loss', metricKey: 'train/loss', chartType: 'line', colSpan: 1 },
  { id: 'p-2', title: 'Evaluation Loss', metricKey: 'eval/loss', chartType: 'line', colSpan: 1 },
  { id: 'p-3', title: 'Evaluation Accuracy', metricKey: 'eval/accuracy', chartType: 'line', colSpan: 1 },
  { id: 'p-4', title: 'Learning Rate', metricKey: 'train/learning_rate', chartType: 'line', colSpan: 1 },
];

/**
 * The panel arrangement, from the backend.
 *
 * This was `localStorage`, which is per-browser-origin: an arrangement built in
 * the browser layout was invisible in the desktop shell and vice versa, and
 * clearing site data reset it silently. It is per *project*, not per workspace,
 * so the workspace store is the wrong home for it — hence its own route.
 *
 * A null response means "never saved one" and yields the defaults. An empty array
 * is respected as an empty workspace: the old code tested `parsed.length > 0` and
 * so sprang back to the four default charts every time someone deliberately
 * removed them all.
 */
async function loadPanels(projectId: string): Promise<PanelConfig[]> {
  const saved = await fetchLayout(projectId);
  return saved ?? DEFAULT_PANELS;
}

export interface LocalTrackState {
  projects: Project[];
  activeProjectId: string;
  runs: Run[];
  selectedRunIds: Set<string>;
  discoveredMetrics: string[];
  panels: PanelConfig[];
  searchRegex: string;
  globalSmoothing: number;
  selectedRunForDetails: Run | null;
  loading: boolean;
  error: string | null;
  /**
   * Per-metric-key revision counters.
   *
   * Keyed by metric, not a single global counter, and that distinction is worth
   * real money: a global one made every `train/loss` point refetch the accuracy
   * and learning-rate panels too. Measured at 172 refetches for 40 ingested
   * points across three panels — the charts were live and needlessly hammering
   * the backend. The `metrics` event carries its keys precisely so each panel can
   * ignore the ones it does not draw.
   *
   * A counter rather than the data itself: what a chart needs depends on its own
   * key, downsample budget and smoothing, so each panel refetches for itself and
   * this is only the signal that there is something to refetch.
   */
  metricRevisions: Record<string, number>;
}

type Listener = () => void;

class LocalTrackStore {
  private state: LocalTrackState = {
    projects: [],
    activeProjectId: 'default',
    runs: [],
    selectedRunIds: new Set(),
    discoveredMetrics: [],
    panels: DEFAULT_PANELS,
    searchRegex: '',
    globalSmoothing: 0.0,
    selectedRunForDetails: null,
    loading: false,
    error: null,
    metricRevisions: {},
  };

  private listeners = new Set<Listener>();

  getState(): LocalTrackState {
    return this.state;
  }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  private notify(): void {
    this.listeners.forEach((l) => l());
  }

  private setState(updates: Partial<LocalTrackState>): void {
    this.state = { ...this.state, ...updates };
    this.notify();
  }

  /** Live-update unsubscriber; null when nothing is listening yet. */
  private unsubscribe: (() => void) | null = null;

  async init(): Promise<void> {
    this.setState({ loading: true, error: null });
    try {
      // Both settings were declared in the manifest and read by nothing — the
      // store hardcoded 'default' and 0.0, so changing either in Settings did
      // exactly nothing. `??` and not `||`: a deliberate 0 smoothing is a real
      // choice and must not fall through to the default.
      const preferredProject = getSetting<string>('localtrack.defaultProject') || 'default';
      const smoothing = getSetting<number>('localtrack.defaultSmoothing') ?? 0.0;

      const projs = await fetchProjects();
      const wanted = this.state.activeProjectId || preferredProject;
      const activePid =
        projs.find((p) => p.id === wanted)?.id ??
        projs.find((p) => p.id === preferredProject)?.id ??
        projs[0]?.id ??
        'default';

      this.setState({
        projects: projs,
        activeProjectId: activePid,
        globalSmoothing: Math.max(0, Math.min(0.99, smoothing)),
        panels: await loadPanels(activePid),
      });

      await this.loadRunsForProject(activePid);
      this.listen();
    } catch (err: unknown) {
      this.setState({ error: messageOf(err, 'Failed to initialize LocalTrack') });
    } finally {
      this.setState({ loading: false });
    }
  }

  /**
   * Follow the `localtrack` channel so a run in flight moves the charts.
   *
   * There was no liveness at all before this — no channel, no polling, no
   * interval — so a sweep could write for ten minutes while the pane sat still.
   *
   * A metrics event bumps `metricsRevision` rather than refetching here: the
   * series a chart needs depends on its own key, downsample budget and smoothing,
   * so the panels do their own fetching and this is only the signal that there is
   * something new to fetch. Run lifecycle events do reload the list, because the
   * sidebar's rows are exactly what changed.
   */
  private listen(): void {
    if (this.unsubscribe) return;
    this.unsubscribe = subscribeChannel('localtrack', (msg) => {
      const { event, data } = msg;
      const payload = (data ?? {}) as { runId?: string; projectId?: string; keys?: string[] };
      if (event === 'metrics') {
        if (payload.runId && !this.state.selectedRunIds.has(payload.runId)) return;
        const keys = Array.isArray(payload.keys) ? payload.keys : [];
        const next = { ...this.state.metricRevisions };
        for (const key of keys) next[key] = (next[key] ?? 0) + 1;
        this.setState({ metricRevisions: next });
        // The scalar and bar panels read `run.summary`, which `ingest_metrics`
        // also updates — but that write only publishes `metrics`, so without a
        // refresh here those two panel kinds would sit on the value they had when
        // the pane opened while the line charts moved beside them. Throttled
        // hard: the wire is already capped at 20/s and refetching the whole run
        // list that often would be absurd.
        this.refreshRunsSoon();
        return;
      }
      if (event === 'run_created' || event === 'run_updated' || event === 'run_deleted') {
        // A run created under a different project is not this pane's business.
        if (payload.projectId && payload.projectId !== this.state.activeProjectId) return;
        void this.loadRunsForProject(this.state.activeProjectId, { keepSelection: true });
      }
    });
  }

  async setActiveProject(projectId: string): Promise<void> {
    this.setState({ activeProjectId: projectId, selectedRunIds: new Set() });
    try {
      this.setState({ panels: await loadPanels(projectId) });
    } catch (err: unknown) {
      this.setState({ error: messageOf(err, 'Failed to load the panel layout') });
    }
    await this.loadRunsForProject(projectId);
  }

  async createNewProject(name: string, description = ''): Promise<void> {
    try {
      const proj = await createProject(name, description);
      await this.init();
      await this.setActiveProject(proj.id);
    } catch (err: unknown) {
      this.setState({ error: messageOf(err, 'Failed to create project') });
    }
  }

  async removeProject(projectId: string): Promise<void> {
    try {
      await deleteProject(projectId);
      await this.init();
    } catch (err: unknown) {
      this.setState({ error: messageOf(err, 'Failed to delete project') });
    }
  }

  async loadRunsForProject(
    projectId: string,
    opts: { keepSelection?: boolean } = {},
  ): Promise<void> {
    try {
      const runs = await fetchRuns(projectId);
      const metrics = await fetchMetricKeys(projectId);

      // A live reload (a run started, finished, or was deleted) must not throw
      // away what the user had selected — that is the whole difference between
      // "the chart gained a point" and "the chart jumped to something else".
      // Only a fresh project load picks a default selection.
      let selectedRunIds = this.state.selectedRunIds;
      if (opts.keepSelection) {
        const alive = new Set(runs.map((r) => r.id));
        selectedRunIds = new Set([...selectedRunIds].filter((id) => alive.has(id)));
      } else {
        selectedRunIds = new Set(runs.slice(0, DEFAULT_SELECTION).map((r) => r.id));
      }

      this.setState({ runs, selectedRunIds, discoveredMetrics: metrics });
    } catch (err: unknown) {
      this.setState({ error: messageOf(err, 'Failed to load runs') });
    }
  }

  toggleRunSelection(runId: string): void {
    const next = new Set(this.state.selectedRunIds);
    if (next.has(runId)) {
      next.delete(runId);
    } else {
      next.add(runId);
    }
    this.setState({ selectedRunIds: next });
  }

  selectAllRuns(): void {
    const next = new Set(this.state.runs.map((r) => r.id));
    this.setState({ selectedRunIds: next });
  }

  deselectAllRuns(): void {
    this.setState({ selectedRunIds: new Set() });
  }

  async removeRun(runId: string): Promise<void> {
    try {
      await deleteRun(runId);
      await this.loadRunsForProject(this.state.activeProjectId);
    } catch (err: unknown) {
      this.setState({ error: messageOf(err, 'Failed to delete run') });
    }
  }

  setSearchRegex(query: string): void {
    this.setState({ searchRegex: query });
  }

  setGlobalSmoothing(val: number): void {
    const clamped = Math.max(0, Math.min(0.99, val));
    this.setState({ globalSmoothing: clamped });
  }

  addPanel(panel: Omit<PanelConfig, 'id'>): void {
    const newPanel: PanelConfig = {
      ...panel,
      id: `p-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
    };
    const next = [...this.state.panels, newPanel];
    this.setState({ panels: next });
    void this.persistPanels(next);
  }

  removePanel(panelId: string): void {
    const next = this.state.panels.filter((p) => p.id !== panelId);
    this.setState({ panels: next });
    void this.persistPanels(next);
  }

  resetPanels(): void {
    this.setState({ panels: DEFAULT_PANELS });
    void this.persistPanels(DEFAULT_PANELS);
  }

  /**
   * Write the arrangement back.
   *
   * Optimistic — the state is already updated when this runs — so a failed save
   * must SAY so rather than silently reverting under the user's hands. The old
   * localStorage version swallowed its write errors entirely.
   */
  private async persistPanels(panels: PanelConfig[]): Promise<void> {
    try {
      await saveLayout(this.state.activeProjectId, panels);
    } catch (err: unknown) {
      this.setState({ error: messageOf(err, 'The panel layout could not be saved') });
    }
  }

  dismissError(): void {
    this.setState({ error: null });
  }

  private refreshTimer: ReturnType<typeof setTimeout> | null = null;

  /** Reload the run list at most once a second, for `summary`-driven panels. */
  private refreshRunsSoon(): void {
    if (this.refreshTimer) return;
    this.refreshTimer = setTimeout(() => {
      this.refreshTimer = null;
      void this.loadRunsForProject(this.state.activeProjectId, { keepSelection: true });
    }, 1000);
  }

  /** Drop the live subscription. Test hook, and used if the store is ever torn down. */
  stop(): void {
    this.unsubscribe?.();
    this.unsubscribe = null;
    if (this.refreshTimer) clearTimeout(this.refreshTimer);
    this.refreshTimer = null;
  }

  openRunDetails(run: Run): void {
    this.setState({ selectedRunForDetails: run });
  }

  closeRunDetails(): void {
    this.setState({ selectedRunForDetails: null });
  }
}

export const localTrackStore = new LocalTrackStore();

export function useLocalTrackStore(): LocalTrackState & {
  setActiveProject: (id: string) => Promise<void>;
  createNewProject: (name: string, description?: string) => Promise<void>;
  removeProject: (id: string) => Promise<void>;
  loadRuns: () => Promise<void>;
  toggleRunSelection: (id: string) => void;
  selectAllRuns: () => void;
  deselectAllRuns: () => void;
  removeRun: (id: string) => Promise<void>;
  setSearchRegex: (q: string) => void;
  setGlobalSmoothing: (v: number) => void;
  addPanel: (p: Omit<PanelConfig, 'id'>) => void;
  removePanel: (id: string) => void;
  resetPanels: () => void;
  openRunDetails: (r: Run) => void;
  closeRunDetails: () => void;
  dismissError: () => void;
} {
  const [state, setState] = useState(() => localTrackStore.getState());

  useEffect(() => {
    return localTrackStore.subscribe(() => {
      setState(localTrackStore.getState());
    });
  }, []);

  return {
    ...state,
    setActiveProject: (id) => localTrackStore.setActiveProject(id),
    createNewProject: (name, desc) => localTrackStore.createNewProject(name, desc),
    removeProject: (id) => localTrackStore.removeProject(id),
    loadRuns: () => localTrackStore.loadRunsForProject(state.activeProjectId),
    toggleRunSelection: (id) => localTrackStore.toggleRunSelection(id),
    selectAllRuns: () => localTrackStore.selectAllRuns(),
    deselectAllRuns: () => localTrackStore.deselectAllRuns(),
    removeRun: (id) => localTrackStore.removeRun(id),
    setSearchRegex: (q) => localTrackStore.setSearchRegex(q),
    setGlobalSmoothing: (v) => localTrackStore.setGlobalSmoothing(v),
    addPanel: (p) => localTrackStore.addPanel(p),
    removePanel: (id) => localTrackStore.removePanel(id),
    resetPanels: () => localTrackStore.resetPanels(),
    openRunDetails: (r) => localTrackStore.openRunDetails(r),
    closeRunDetails: () => localTrackStore.closeRunDetails(),
    dismissError: () => localTrackStore.dismissError(),
  };
}
