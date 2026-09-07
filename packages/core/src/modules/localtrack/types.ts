/** TypeScript types for LocalTrack */

export type RunStatus = 'running' | 'finished' | 'failed' | 'crashed';
/**
 * `table` and `parcoords` are the comparison panels, and they are not charts of a
 * series: they read each run's `config`, which nothing had ever written anything
 * comparable into until the training sweep started declaring one per point.
 */
export type ChartType = 'line' | 'bar' | 'scalar' | 'table' | 'parcoords';
export type YAxisScale = 'linear' | 'log';

export interface Project {
  id: string;
  name: string;
  description: string;
  created_at: string;
  updated_at: string;
  run_count: number;
  last_run_at: string | null;
}

export interface Run {
  id: string;
  project_id: string;
  name: string;
  status: RunStatus;
  config: Record<string, unknown>;
  system_info: Record<string, unknown>;
  summary: Record<string, number>;
  tags: string[];
  start_time: string;
  end_time: string | null;
  duration_seconds: number;
}

export interface MetricSeries {
  run_id: string;
  key: string;
  steps: number[];
  values: number[];
  epochs: (number | null)[];
  raw_point_count: number;
}

export interface PanelConfig {
  id: string;
  title: string;
  metricKey: string;
  chartType: ChartType;
  scale?: YAxisScale;
  smoothing?: number;
  colSpan?: number; // 1, 2, or 3 columns in grid
}

export interface RunArtifact {
  id: string;
  run_id: string;
  filename: string;
  file_path: string;
  size_bytes: number;
  content_type: string;
  created_at: string;
}


/** One run reduced to what differs from the others it is compared against. */
export interface CompareRow {
  run_id: string;
  name: string;
  status: RunStatus;
  /** Only the config keys that vary across the compared runs. */
  config: Record<string, unknown>;
  metrics: Record<string, number>;
}

export interface CompareResult {
  runs: CompareRow[];
  /** The axes of the experiment, in the order the sweep declared them. */
  varied: string[];
  /** Held constant across every run — reported once instead of on every row. */
  shared: Record<string, unknown>;
  metric_keys: string[];
  /** Config keys whose disagreement makes these runs not an ablation
   * (`_backend`, `_task`). Comparing them is allowed; presenting it as an
   * ablation is not. */
  mixed: string[];
}
