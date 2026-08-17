import { useEffect, useState } from 'react';
import {
  createProject,
  deleteProject,
  deleteRun,
  fetchMetricKeys,
  fetchProjects,
  fetchRuns,
} from './api';
import type { PanelConfig, Project, Run } from './types';

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

const DEFAULT_PANELS: PanelConfig[] = [
  { id: 'p-1', title: 'Training Loss', metricKey: 'train/loss', chartType: 'line', colSpan: 1 },
  { id: 'p-2', title: 'Evaluation Loss', metricKey: 'eval/loss', chartType: 'line', colSpan: 1 },
  { id: 'p-3', title: 'Evaluation Accuracy', metricKey: 'eval/accuracy', chartType: 'line', colSpan: 1 },
  { id: 'p-4', title: 'Learning Rate', metricKey: 'train/learning_rate', chartType: 'line', colSpan: 1 },
];

function getStoredPanels(projectId: string): PanelConfig[] {
  try {
    const raw = localStorage.getItem(`localtrack_panels_${projectId}`);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed) && parsed.length > 0) return parsed;
    }
  } catch {}
  return DEFAULT_PANELS;
}

function saveStoredPanels(projectId: string, panels: PanelConfig[]): void {
  try {
    localStorage.setItem(`localtrack_panels_${projectId}`, JSON.stringify(panels));
  } catch {}
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

  async init(): Promise<void> {
    this.setState({ loading: true, error: null });
    try {
      const projs = await fetchProjects();
      const activePid =
        projs.find((p) => p.id === this.state.activeProjectId)?.id ?? projs[0]?.id ?? 'default';
      const storedPanels = getStoredPanels(activePid);

      this.setState({
        projects: projs,
        activeProjectId: activePid,
        panels: storedPanels,
      });

      await this.loadRunsForProject(activePid);
    } catch (err: any) {
      this.setState({ error: err?.message ?? 'Failed to initialize LocalTrack' });
    } finally {
      this.setState({ loading: false });
    }
  }

  async setActiveProject(projectId: string): Promise<void> {
    const panels = getStoredPanels(projectId);
    this.setState({
      activeProjectId: projectId,
      panels,
      selectedRunIds: new Set(),
    });
    await this.loadRunsForProject(projectId);
  }

  async createNewProject(name: string, description = ''): Promise<void> {
    try {
      const proj = await createProject(name, description);
      await this.init();
      await this.setActiveProject(proj.id);
    } catch (err: any) {
      this.setState({ error: err?.message ?? 'Failed to create project' });
    }
  }

  async removeProject(projectId: string): Promise<void> {
    try {
      await deleteProject(projectId);
      await this.init();
    } catch (err: any) {
      this.setState({ error: err?.message ?? 'Failed to delete project' });
    }
  }

  async loadRunsForProject(projectId: string): Promise<void> {
    try {
      const runs = await fetchRuns(projectId);
      // By default select all active runs or first 5
      const newSelected = new Set<string>();
      runs.forEach((r, idx) => {
        if (idx < 5) newSelected.add(r.id);
      });

      const metrics = await fetchMetricKeys(projectId);

      this.setState({
        runs,
        selectedRunIds: newSelected,
        discoveredMetrics: metrics,
      });
    } catch (err: any) {
      this.setState({ error: err?.message ?? 'Failed to load runs' });
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
    } catch (err: any) {
      this.setState({ error: err?.message ?? 'Failed to delete run' });
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
    saveStoredPanels(this.state.activeProjectId, next);
  }

  removePanel(panelId: string): void {
    const next = this.state.panels.filter((p) => p.id !== panelId);
    this.setState({ panels: next });
    saveStoredPanels(this.state.activeProjectId, next);
  }

  resetPanels(): void {
    this.setState({ panels: DEFAULT_PANELS });
    saveStoredPanels(this.state.activeProjectId, DEFAULT_PANELS);
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
  };
}
