
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
  };
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
