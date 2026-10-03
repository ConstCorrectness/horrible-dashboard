/**
 * A page's code cells: their outputs, and running them.
 *
 * The backend keeps each page's outputs in a **shadow notebook** (see
 * backend/modules/scrive/kernel.py). A shadow cell's id is a hash of its source plus
 * which occurrence of that source it is (`c-<sha256[:12]>-<n>`), so:
 *
 * - an output is found by *source*, not by position — inserting a paragraph or a cell
 *   above does not detach it;
 * - editing a cell's source makes it a different cell, with no output: the old output
 *   is never shown under new code.
 *
 * Outputs come from the live kernel session when one is open, and otherwise from the
 * cache (`GET …/cells`), so the Preview of a page that ran yesterday shows its results
 * without starting Python. Running is always an explicit click.
 */
import { apiGet } from '../../api';
import { interruptKernel } from '../../notebook/kernelClient';
import { openSession, type SessionStore } from '../../notebook/SessionStore';
import type { CellOp, CellRunState, NbOutput } from '../../notebook/types';
import { parseMyst, splitFrontmatter, type MystNode } from './myst/parse';

export const KERNEL_CHANNEL = 'scrive-kernel';

export interface PageCell {
  source: string;
  /** Which occurrence of this exact source it is, in page order. */
  occurrence: number;
}

export interface CellView {
  outputs: NbOutput[];
  executionCount: number | null;
  /** `none`: this source has never run. */
  state: CellRunState | 'none';
}

/** The page's `{code-cell}` sources, in order, with their occurrence counts. */
export function codeCellsOf(text: string): PageCell[] {
  const tree = parseMyst(splitFrontmatter(text).body);
  const out: PageCell[] = [];
  const seen = new Map<string, number>();
  const visit = (node: MystNode) => {
    if (node.type === 'mystDirective' && node.name === 'code-cell') {
      const source = String(node.value ?? '');
      const occurrence = seen.get(source) ?? 0;
      seen.set(source, occurrence + 1);
      out.push({ source, occurrence });
      return;
    }
    for (const child of node.children ?? []) visit(child);
  };
  visit(tree);
  return out;
}

/** The shadow cell id for a code cell — `kernel.cell_id` on the backend. */
export async function cellId(source: string, occurrence: number): Promise<string> {
  const bytes = new TextEncoder().encode(source);
  const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', bytes));
  const hex = [...digest].map((b) => b.toString(16).padStart(2, '0')).join('');
  return `c-${hex.slice(0, 12)}-${occurrence}`;
}

/**
 * Cell ops that make the shadow notebook hold exactly `desired` (ids + sources), in
 * order. Cells already there keep their outputs; the rest are inserted, and cells no
 * longer on the page are dropped.
 */
export function syncOps(
  shadow: { id: string }[],
  desired: { id: string; source: string }[],
): CellOp[] {
  const want = new Set(desired.map((d) => d.id));
  const ops: CellOp[] = [];
  // Simulate the notebook so only the ops that change something are sent.
  const order = shadow.map((c) => c.id);
  for (const id of order.filter((id) => !want.has(id))) {
    ops.push({ op: 'delete', cellId: id });
    order.splice(order.indexOf(id), 1);
  }
  // Invariant: after placing k cells, positions 0..k-1 hold desired[0..k-1].
  desired.forEach((cell, index) => {
    const at = order.indexOf(cell.id);
    if (at === index) return;
    if (at >= 0) {
      ops.push({ op: 'move', cellId: cell.id, index });
      order.splice(at, 1);
    } else {
      ops.push({
        op: 'insert',
        cellId: cell.id,
        cellType: 'code',
        source: cell.source,
        ...(index > 0 ? { afterCellId: desired[index - 1].id } : { index: 0 }),
      });
    }
    order.splice(index, 0, cell.id);
  });
  return ops;
}

interface CachedCell {
  id: string;
  source: string;
  outputs: NbOutput[];
  execution_count: number | null;
}

/** One page's cells, shared by its Write view and its Preview. */
export class PageCells {
  private cached = new Map<string, CachedCell>();
  /** Source → id, for the cells the controller has resolved. */
  private ids = new Map<string, string>();
  private session: SessionStore | null = null;
  private unsubscribeSession: (() => void) | null = null;
  private listeners = new Set<() => void>();
  private version = 0;
  /** The page text to sync from — set by the page pane (the live buffer). */
  textSource: () => string = () => '';

  constructor(
    readonly site: string,
    readonly path: string,
  ) {}

  get key(): string {
    return `scrive:${this.site}/${this.path}`;
  }

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  snapshot = (): number => this.version;

  private changed(): void {
    this.version++;
    for (const listener of this.listeners) listener();
  }

  /** Re-read the output cache (after a page loads or the cache was written). */
  async refresh(): Promise<void> {
    try {
      const { cells } = await apiGet<{ cells: CachedCell[] }>(
        `/scrive/sites/${this.site}/cells?path=${encodeURIComponent(this.path)}`,
      );
      this.cached = new Map(cells.map((c) => [c.id, c]));
      this.changed();
    } catch {
      // No cache is the same as nothing run yet.
    }
  }

  /** What to show under a cell. Synchronous: ids are resolved as cells render. */
  view(source: string, occurrence: number): CellView {
    const key = `${occurrence}:${source}`;
    const id = this.ids.get(key);
    if (id === undefined) {
      void cellId(source, occurrence).then((resolved) => {
        this.ids.set(key, resolved);
        this.changed();
      });
      return { outputs: [], executionCount: null, state: 'none' };
    }
    const live = this.session?.snapshot();
    const liveCell = live?.cells.find((c) => c.id === id);
    if (live && liveCell) {
      return {
        outputs: liveCell.outputs,
        executionCount: liveCell.execution_count ?? null,
        state: live.runStates[id] ?? (liveCell.outputs.length ? 'done' : 'none'),
      };
    }
    const cached = this.cached.get(id);
    return cached
      ? { outputs: cached.outputs, executionCount: cached.execution_count, state: 'done' }
      : { outputs: [], executionCount: null, state: 'none' };
  }

  kernelStatus(): string | null {
    return this.session?.snapshot().kernel ?? null;
  }

  error(): string | null {
    return this.session?.snapshot().error ?? null;
  }

  /** Start (or attach to) the page's kernel and wait until it is open. */
  private async open(): Promise<SessionStore> {
    if (!this.session) {
      this.session = openSession(KERNEL_CHANNEL, this.key, { site: this.site, path: this.path });
      this.unsubscribeSession = this.session.subscribe(() => this.changed());
    }
    const session = this.session;
    if (session.snapshot().sessionKey) return session;
    return new Promise((resolve, reject) => {
      const stop = session.subscribe(() => {
        const state = session.snapshot();
        if (state.sessionKey) {
          stop();
          resolve(session);
        } else if (state.error) {
          stop();
          reject(new Error(state.error));
        }
      });
    });
  }

  /**
   * Run one cell (`target`), or every cell in page order. The shadow notebook is
   * first made to match the page, so a run never executes code that is no longer
   * there.
   */
  async run(target?: PageCell): Promise<void> {
    const cells = codeCellsOf(this.textSource());
    const desired = await Promise.all(
      cells.map(async (c) => ({ id: await cellId(c.source, c.occurrence), source: c.source })),
    );
    cells.forEach((c, i) => this.ids.set(`${c.occurrence}:${c.source}`, desired[i].id));
    const session = await this.open();
    const ops = syncOps(session.snapshot().cells, desired);
    if (ops.length) {
      // The backend's reply re-syncs; the optimistic list only has to keep the
      // cells we are about to run.
      const keep = new Map(session.snapshot().cells.map((c) => [c.id, c]));
      session.applyLocal(
        ops,
        desired.map(
          (d) =>
            keep.get(d.id) ?? {
              id: d.id,
              cell_type: 'code' as const,
              source: d.source,
              outputs: [],
              execution_count: null,
            },
        ),
      );
    }
    if (target) {
      session.run(await cellId(target.source, target.occurrence));
    } else {
      session.runAll();
    }
  }

  interrupt(): void {
    const key = this.session?.snapshot().sessionKey;
    if (key) interruptKernel(KERNEL_CHANNEL, key);
  }

  dispose(): void {
    this.unsubscribeSession?.();
  }
}

const pages = new Map<string, PageCells>();

/** The shared controller for a page (created on first use; cache loaded then). */
export function pageCells(site: string, path: string): PageCells {
  const key = `${site}/${path}`;
  let cells = pages.get(key);
  if (!cells) {
    cells = new PageCells(site, path);
    pages.set(key, cells);
    void cells.refresh();
  }
  return cells;
}
