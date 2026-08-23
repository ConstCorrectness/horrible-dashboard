// @vitest-environment happy-dom
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { NotebookLspDoc, type LspCell } from '../notebook-lsp';
// @ts-expect-error — these test-only mock helpers aren't part of the real ws module's types.
import { _getSent, _clearSent, _triggerMessage } from '../../../ws';

type MockMsg = { channel: string; event: string; data?: unknown };

vi.mock('../../../ws', () => {
  const listeners: Record<string, ((msg: MockMsg) => void)[]> = {};
  const sent: MockMsg[] = [];
  return {
    sendChannel: vi.fn((channel: string, event: string, data?: unknown) => {
      sent.push({ channel, event, data });
    }),
    subscribeChannel: vi.fn((channel: string, handler: (msg: MockMsg) => void) => {
      (listeners[channel] ??= []).push(handler);
      return () => {
        const idx = listeners[channel].indexOf(handler);
        if (idx !== -1) listeners[channel].splice(idx, 1);
      };
    }),
    _getSent: () => sent,
    _clearSent: () => {
      sent.length = 0;
    },
    _triggerMessage: (channel: string, event: string, data: unknown) => {
      for (const h of listeners[channel] ?? []) h({ channel, event, data });
    },
  };
});

// The coordinator resolves an interpreter and a project root before it can join a
// session; neither is available (or interesting) here.
//
// The root is unique per call on purpose. Sessions are pooled by
// `language::root::interpreter` and kept warm for a minute after their last document
// closes, so a fixed root would hand every test after the first a session that had
// already handshaked — and no `start` to drive.
vi.mock('../pythonEnv', () => {
  let n = 0;
  return {
    fetchPythonEnv: vi.fn(async () => ({
      interpreter: '/py',
      root: `/proj-${++n}`,
      packages: {},
    })),
  };
});

const cell = (id: string, source: string, type: 'code' | 'markdown' = 'code'): LspCell => ({
  id,
  cell_type: type,
  source,
});

/** Every JSON-RPC payload the client has sent, newest last. */
function payloads(): Record<string, unknown>[] {
  return (_getSent() as MockMsg[])
    .filter((m) => m.event === 'rpc')
    .map((m) => (m.data as { payload: Record<string, unknown> }).payload);
}

function methods(): string[] {
  return payloads().map((p) => String(p.method));
}

/** Bring a freshly-started doc's session through the handshake so queued opens flush. */
function completeHandshake(): void {
  const start = (_getSent() as MockMsg[]).find((m) => m.event === 'start');
  const sessionId = (start?.data as { sessionId: string }).sessionId;
  _triggerMessage('lsp', 'started', { sessionId });
  _triggerMessage('lsp', 'rpc', {
    sessionId,
    payload: { id: 1, result: { capabilities: {} } },
  });
}

/** A started doc with `cells` open and the handshake done. */
async function openDoc(cells: LspCell[]): Promise<NotebookLspDoc> {
  const doc = new NotebookLspDoc('/proj/nb.ipynb');
  doc.sync(cells);
  await doc.start();
  completeHandshake();
  return doc;
}

describe('notebook LSP coordinator', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    _clearSent();
  });

  it('opens the notebook as one document, with only its code cells', async () => {
    await openDoc([
      cell('a', 'import os'),
      cell('b', '# title', 'markdown'),
      cell('c', 'os.getcwd()'),
    ]);

    const open = payloads().find((p) => p.method === 'notebookDocument/didOpen');
    expect(open).toBeDefined();
    const params = open!.params as {
      notebookDocument: { notebookType: string; cells: { document: string }[] };
      cellTextDocuments: { uri: string; text: string }[];
    };
    // The markdown cell must not be in the array: the server's own selector asks for
    // python cells, and including it would shift every later cell's index so that a
    // structural edit landed in the wrong place.
    expect(params.notebookDocument.cells).toHaveLength(2);
    expect(params.notebookDocument.notebookType).toBe('jupyter-notebook');
    expect(params.cellTextDocuments.map((c) => c.text)).toEqual(['import os', 'os.getcwd()']);
  });

  it('addresses cells with the vscode-notebook-cell scheme', async () => {
    // Load-bearing, and silent when wrong: under `file:` with a `#cell0` fragment the
    // server accepts the notebook and then resolves nothing — no diagnostics, empty
    // completions, no error. Measured against basedpyright 1.39.9.
    const doc = await openDoc([cell('a', 'x = 1')]);
    expect(doc.cellUri('a')).toBe('vscode-notebook-cell:///proj/nb.ipynb#a');

    const open = payloads().find((p) => p.method === 'notebookDocument/didOpen');
    const uris = (open!.params as { cellTextDocuments: { uri: string }[] }).cellTextDocuments;
    expect(uris[0].uri.startsWith('vscode-notebook-cell:')).toBe(true);
  });

  it('sends a cell edit as notebookDocument/didChange, never textDocument/didChange', async () => {
    // The other half of the same trap: a `textDocument/didChange` aimed at a cell URI
    // is accepted and discarded, leaving the server analyzing the text the cell was
    // opened with — which reads as stale completions rather than as a sync bug.
    const doc = await openDoc([cell('a', 'x = 1')]);
    _clearSent();

    const ext = doc.cellExtension('a');
    expect(ext).toBeDefined();
    doc.sync([cell('a', 'x = 2')]);

    expect(methods()).toContain('notebookDocument/didChange');
    expect(methods()).not.toContain('textDocument/didChange');
    const change = payloads().find((p) => p.method === 'notebookDocument/didChange');
    const textContent = (
      change!.params as { change: { cells: { textContent: { changes: { text: string }[] }[] } } }
    ).change.cells.textContent;
    expect(textContent[0].changes[0].text).toBe('x = 2');
  });

  it('splices an inserted cell in at its notebook index', async () => {
    const doc = await openDoc([cell('a', 'a = 1'), cell('b', 'b = 2')]);
    _clearSent();

    doc.sync([cell('a', 'a = 1'), cell('mid', 'm = 0'), cell('b', 'b = 2')]);

    const change = payloads().find((p) => p.method === 'notebookDocument/didChange');
    const structure = (
      change!.params as {
        change: {
          cells: {
            structure: {
              array: { start: number; deleteCount: number; cells: { document: string }[] };
              didOpen: { uri: string; text: string }[];
            };
          };
        };
      }
    ).change.cells.structure;
    expect(structure.array.start).toBe(1);
    expect(structure.array.deleteCount).toBe(0);
    expect(structure.didOpen[0].text).toBe('m = 0');
    expect(structure.didOpen[0].uri).toBe(doc.cellUri('mid'));
  });

  it('splices a deleted cell out and stops tracking it', async () => {
    const doc = await openDoc([cell('a', 'a = 1'), cell('b', 'b = 2'), cell('c', 'c = 3')]);
    _clearSent();

    doc.sync([cell('a', 'a = 1'), cell('c', 'c = 3')]);

    const change = payloads().find((p) => p.method === 'notebookDocument/didChange');
    const structure = (
      change!.params as {
        change: {
          cells: {
            structure: {
              array: { start: number; deleteCount: number };
              didClose: { uri: string }[];
            };
          };
        };
      }
    ).change.cells.structure;
    expect(structure.array.start).toBe(1);
    expect(structure.array.deleteCount).toBe(1);
    expect(structure.didClose[0].uri).toBe(doc.cellUri('b'));
  });

  it('does not resend a cell whose source did not change', async () => {
    const doc = await openDoc([cell('a', 'x = 1'), cell('b', 'y = 2')]);
    _clearSent();

    doc.sync([cell('a', 'x = 1'), cell('b', 'y = 2')]);

    expect(methods()).not.toContain('notebookDocument/didChange');
  });

  it('waits for cells rather than opening an empty notebook', async () => {
    // `fetchPythonEnv` is cached per directory, so on a second open the session is
    // ready before React has run the effect that describes the cells. Opening anyway
    // sent `didOpen` with `cells: []`, and a notebook the server believes is empty
    // resolves nothing in any cell — no error, just silence.
    const doc = new NotebookLspDoc('/proj/nb.ipynb');
    await doc.start(); // no `sync` yet — the pane hasn't rendered
    completeHandshake();
    expect(methods()).not.toContain('notebookDocument/didOpen');

    doc.sync([cell('a', 'x = 1')]);

    const open = payloads().find((p) => p.method === 'notebookDocument/didOpen');
    expect(open).toBeDefined();
    const cells = (open!.params as { notebookDocument: { cells: unknown[] } }).notebookDocument
      .cells;
    expect(cells).toHaveLength(1);
    // …and exactly once, however many times the pane re-syncs.
    doc.sync([cell('a', 'x = 1')]);
    expect(payloads().filter((p) => p.method === 'notebookDocument/didOpen')).toHaveLength(1);
  });

  it('queues cells described before the session comes up', async () => {
    const doc = new NotebookLspDoc('/proj/nb.ipynb');
    doc.sync([cell('a', 'early = 1')]);
    expect(methods()).not.toContain('notebookDocument/didOpen');

    await doc.start();
    completeHandshake();

    const open = payloads().find((p) => p.method === 'notebookDocument/didOpen');
    const texts = (open!.params as { cellTextDocuments: { text: string }[] }).cellTextDocuments;
    expect(texts[0].text).toBe('early = 1');
  });
});
