// @vitest-environment happy-dom
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { EditorState } from '@codemirror/state';
import { EditorView } from '@codemirror/view';
import { lspExtension } from '../lsp';
import { getLspClient } from '../lsp-registry';
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
      if (!listeners[channel]) listeners[channel] = [];
      listeners[channel].push(handler);
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
      const handlerList = listeners[channel] || [];
      for (const h of handlerList) {
        h({ channel, event, data });
      }
    },
  };
});

describe('LSP IntelliSense client', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    _clearSent();
  });

  it('correctly flushes changes before sending requests', async () => {
    const bufferUri = 'workspace-file:/mock/file.ts';
    const state = EditorState.create({
      doc: 'console.log("hello");',
      extensions: [
        lspExtension({
          path: '/mock/file.ts',
          languageId: 'typescript',
          root: '/mock',
          bufferUri,
        }),
      ],
    });

    const view = new EditorView({ state });
    const client = getLspClient(bufferUri);
    expect(client).toBeDefined();

    // The document attaches to its session through a `DocumentBinding`, which is
    // async for every language (Python resolves an interpreter and a project root
    // first). So the session lands a microtask after the view is constructed, not
    // during it — everything below needs it in place.
    await Promise.resolve();

    // Verify session started
    const sentMessages = _getSent();
    expect(sentMessages).toContainEqual(
      expect.objectContaining({
        channel: 'lsp',
        event: 'start',
        data: expect.objectContaining({ languageId: 'typescript' }),
      }),
    );

    const sessionId = sentMessages.find(
      (m: { event: string; data?: { sessionId?: string } }) => m.event === 'start',
    )?.data?.sessionId;
    expect(sessionId).toBeDefined();

    // Simulate backend sending 'started' event
    _triggerMessage('lsp', 'started', { sessionId });

    // The client should now send 'initialize' request
    expect(_getSent()).toContainEqual(
      expect.objectContaining({
        channel: 'lsp',
        event: 'rpc',
        data: expect.objectContaining({
          sessionId,
          payload: expect.objectContaining({ method: 'initialize' }),
        }),
      }),
    );

    // Respond to initialize
    _triggerMessage('lsp', 'rpc', {
      sessionId,
      payload: {
        id: 1,
        result: {
          capabilities: {
            completionProvider: { triggerCharacters: ['.'], resolveProvider: true },
          },
        },
      },
    });

    // The client should send the 'initialized' notification and open document
    expect(_getSent()).toContainEqual(
      expect.objectContaining({
        channel: 'lsp',
        event: 'rpc',
        data: expect.objectContaining({
          sessionId,
          payload: expect.objectContaining({ method: 'initialized' }),
        }),
      }),
    );

    expect(_getSent()).toContainEqual(
      expect.objectContaining({
        channel: 'lsp',
        event: 'rpc',
        data: expect.objectContaining({
          sessionId,
          payload: expect.objectContaining({
            method: 'textDocument/didOpen',
            params: expect.objectContaining({
              textDocument: expect.objectContaining({
                text: 'console.log("hello");',
              }),
            }),
          }),
        }),
      }),
    );

    _clearSent();

    // Modify CodeMirror document to simulate user typing "console.log("hello");a"
    view.dispatch({
      changes: { from: view.state.doc.length, insert: 'a' },
    });

    // The debounce timer is running. We haven't sent didChange yet.
    expect(_getSent()).not.toContainEqual(
      expect.objectContaining({
        channel: 'lsp',
        event: 'rpc',
        data: expect.objectContaining({
          sessionId,
          payload: expect.objectContaining({ method: 'textDocument/didChange' }),
        }),
      }),
    );

    // Call hover lookup (which should flush changes first)
    // We expect request() to flush changes synchronously and send didChange with version 2
    // before the hover request. getLspClient() is typed as the narrow agent-facing
    // LspBufferClient; the live registry object is the full LspClient with hover().
    void (client as unknown as { hover(pos: number): Promise<unknown> } | undefined)?.hover(0);

    expect(_getSent()[0]).toEqual(
      expect.objectContaining({
        channel: 'lsp',
        event: 'rpc',
        data: expect.objectContaining({
          sessionId,
          payload: expect.objectContaining({
            method: 'textDocument/didChange',
            params: expect.objectContaining({
              textDocument: expect.objectContaining({ version: 2 }),
              contentChanges: [{ text: 'console.log("hello");a' }],
            }),
          }),
        }),
      }),
    );

    expect(_getSent()[1]).toEqual(
      expect.objectContaining({
        channel: 'lsp',
        event: 'rpc',
        data: expect.objectContaining({
          sessionId,
          payload: expect.objectContaining({
            method: 'textDocument/hover',
            params: expect.objectContaining({
              position: { line: 0, character: 0 },
            }),
          }),
        }),
      }),
    );

    view.destroy();
  });
});
