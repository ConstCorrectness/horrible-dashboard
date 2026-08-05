/**
 * The review queue's arrival path.
 *
 * Both of these pin the same defect from opposite sides: a proposal filed against a
 * table the user does not have selected used to be invisible. `getProposals()`
 * filters to the active schema (correct — the review pane must not hijack an open
 * record with an unrelated diff), and the rail's badge used to read from it, so the
 * only way to discover a waiting review was to already have guessed the right
 * table. The badge counts `getAllProposals()` now.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

interface MockMsg {
  channel: string;
  event: string;
  data?: unknown;
}

const listeners: Record<string, ((msg: MockMsg) => void)[]> = {};

vi.mock('../../../ws', () => ({
  subscribeChannel: vi.fn((channel: string, handler: (msg: MockMsg) => void) => {
    (listeners[channel] ??= []).push(handler);
    return () => {};
  }),
  sendChannel: vi.fn(),
}));

// The watch calls these on start; the network is not what is under test here.
vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api');
  return {
    ...actual,
    listSchemas: vi.fn(async () => ({ schemas: [] })),
    listProposals: vi.fn(async () => ({ proposals: [] })),
    listRows: vi.fn(async () => ({ rows: [] })),
    seedSchemas: vi.fn(async () => ({ created: [] })),
  };
});

const { getAllProposals, getPendingBySchema, getProposals, initRecordsWatch } =
  await import('../store');

function deliver(event: string, data: unknown): void {
  for (const handler of listeners.records ?? []) handler({ channel: 'records', event, data });
}

function proposal(id: string, schemaId: string) {
  return { id, schema_id: schemaId, fields: { name: { value: 'Ada' } }, status: 'pending' };
}

describe('the review queue counts every table', () => {
  beforeEach(() => {
    initRecordsWatch();
    for (const p of getAllProposals()) deliver('proposal_closed', { id: p.id });
  });

  it('keeps proposals filed against a table that is not selected', () => {
    deliver('proposal', proposal('p1', 'papers'));
    deliver('proposal', proposal('p2', 'contacts'));
    deliver('proposal', proposal('p3', 'papers'));

    // No table is active in this harness, so the schema-filtered view is empty —
    // which is exactly the state in which the badge used to read zero.
    expect(getProposals()).toHaveLength(0);
    expect(getAllProposals()).toHaveLength(3);
  });

  it('breaks the count down per table for the rail markers', () => {
    deliver('proposal', proposal('p1', 'papers'));
    deliver('proposal', proposal('p2', 'contacts'));
    deliver('proposal', proposal('p3', 'papers'));

    expect(getPendingBySchema()).toEqual({ papers: 2, contacts: 1 });
  });

  it('drops a proposal from the count once it is closed', () => {
    deliver('proposal', proposal('p1', 'papers'));
    deliver('proposal', proposal('p2', 'papers'));
    deliver('proposal_closed', { id: 'p1', status: 'applied' });

    expect(getAllProposals().map((p) => p.id)).toEqual(['p2']);
    expect(getPendingBySchema()).toEqual({ papers: 1 });
  });

  it('replaces a re-filed proposal rather than counting it twice', () => {
    deliver('proposal', proposal('p1', 'papers'));
    deliver('proposal', proposal('p1', 'papers'));

    expect(getAllProposals()).toHaveLength(1);
  });
});
