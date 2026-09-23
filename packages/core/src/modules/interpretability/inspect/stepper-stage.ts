/**
 * The stepper speaks `node-kind.ts`'s vocabulary (a ggml node's stage); this
 * diagram names its sub-blocks by `Selection.stage`. Mapped here, at the edge,
 * rather than by importing the llama.cpp module's classifier — the panes share the
 * locus bus and nothing else. A `residual` step is the block's output, so it selects
 * the block itself; an unknown stage selects nothing narrower than the block.
 */
export function explorerStage(stage: string | undefined, moe: boolean): string | null {
  switch (stage) {
    case 'attention':
      return 'attention';
    case 'ffn':
    case 'moe':
      return moe ? 'moe' : 'ffn';
    case 'norm':
      return 'norm';
    default:
      return null;
  }
}

/** The inverse, for a click on the diagram steering the stepper. */
export function stepperStage(stage: string): string | undefined {
  return ['attention', 'ffn', 'moe', 'norm'].includes(stage) ? stage : undefined;
}
