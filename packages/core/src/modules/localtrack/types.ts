/** TypeScript types for LocalTrack */

export type RunStatus = 'running' | 'finished' | 'failed' | 'crashed';
export type ChartType = 'line' | 'bar' | 'scalar';
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
  config: Record<string, any>;
  system_info: Record<string, any>;
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
