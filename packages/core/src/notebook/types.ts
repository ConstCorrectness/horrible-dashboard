/**
 * Domain-neutral notebook types shared by the `notebook` and `training` modules —
 * the wire/UI shapes of an nbformat document and the kernel protocol. Mirrors
 * `backend/notebook_core/models.py` and the kernel ws events.
 */

export type KernelStatus = 'starting' | 'idle' | 'busy' | 'restarting' | 'dead';
export type CellRunState = 'queued' | 'running' | 'done' | 'error';

/** nbformat output dict, kept raw (mirrors the backend). */
export type NbOutput = Record<string, unknown>;

export interface NotebookCell {
  id: string;
  cell_type: 'code' | 'markdown';
  source: string;
  outputs: NbOutput[];
  execution_count?: number | null;
}

export interface Notebook {
  path: string;
  cells: NotebookCell[];
  metadata: Record<string, unknown>;
}

export interface CellOp {
  op: 'insert' | 'edit' | 'delete' | 'move';
  cellId?: string;
  source?: string;
  cellType?: 'code' | 'markdown';
  afterCellId?: string;
  index?: number;
}
