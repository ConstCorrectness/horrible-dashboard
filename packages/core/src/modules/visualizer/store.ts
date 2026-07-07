type VisualizerMode = 'canvas' | 'three' | 'babylon' | 'pygame';

export interface VisualizerInstance {
  setMode: (mode: VisualizerMode) => void;
  updateCode: (code: string) => void;
  run: () => void;
  stop: () => void;
  getState: () => {
    mode: VisualizerMode;
    isRunning: boolean;
    hasError: boolean;
    errorMsg: string | null;
    codeLength: number;
    /** The current source script — so an agent can read it, edit it, and re-render. */
    code: string;
  };
  /** Export the current script to the editor as a new buffer; returns its URI. */
  exportToEditor: (prefer: 'note' | 'file') => Promise<string | null>;
  /**
   * Point the visualizer at an editor buffer (the live "Source") and switch to the
   * given engine — used by the editor's "Open in visualizer" command.
   */
  setTarget: (uri: string, mode: VisualizerMode) => void;
}

let activeInstance: VisualizerInstance | null = null;

export function registerVisualizerInstance(instance: VisualizerInstance) {
  activeInstance = instance;
  return () => {
    if (activeInstance === instance) {
      activeInstance = null;
    }
  };
}

export function getActiveVisualizer() {
  return activeInstance;
}
