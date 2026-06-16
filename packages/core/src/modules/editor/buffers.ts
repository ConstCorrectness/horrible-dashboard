/**
 * Registry of live editor buffers keyed by their source URI, so the editor's
 * type-level agent tools (`editor.applyEdit`, `editor.save`) can act on a specific
 * open buffer. Each mounted `BufferView` registers a controller; the agent reads
 * the active buffer via `getAgentContext` and targets edits by URI.
 */
// A `type` (not `interface`) so it's assignable to `AgentContextSnapshot`
// (`Record<string, unknown>`) — interfaces lack the implicit index signature.
export type BufferSnapshot = {
  uri: string;
  title: string;
  content: string;
  dirty: boolean;
  selection: { from: number; to: number; text: string };
};

export interface BufferController {
  snapshot(): BufferSnapshot;
  setContent(content: string): void;
  /**
   * Show `content` as a *proposed* edit: the buffer enters a diff/review state
   * (original vs proposed) the user accepts or declines, rather than replacing the
   * content outright. Used by the gated `editor.proposeEdit` agent tool.
   */
  propose(content: string): void;
  save(): Promise<void>;
}

const controllers = new Map<string, BufferController>();

export function registerBuffer(uri: string, controller: BufferController): () => void {
  controllers.set(uri, controller);
  return () => {
    if (controllers.get(uri) === controller) controllers.delete(uri);
  };
}

export function getBuffer(uri: string): BufferController | undefined {
  return controllers.get(uri);
}

export function listBufferUris(): string[] {
  return [...controllers.keys()];
}
