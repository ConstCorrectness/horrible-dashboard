import { registry, type ModuleManifest } from '../../registry';
import { LocalTrackWorkspacePane } from './LocalTrackWorkspacePane';

export const localtrackModule: ModuleManifest = {
  id: 'localtrack',
  title: 'LocalTrack',
  panels: [
    {
      id: 'localtrack.workspace',
      title: 'LocalTrack Workspace',
      component: LocalTrackWorkspacePane,
      role: 'document',
      icon: '∿',
    },
  ],
  commands: [
    {
      id: 'localtrack.openWorkspace',
      title: 'LocalTrack: Open Experiment Workspace',
      run: () => {
        registry.openPanel('localtrack.workspace');
      },
    },
  ],
  settings: [
    {
      key: 'localtrack.defaultProject',
      title: 'Default Project',
      description: 'Default project to select when opening the LocalTrack workspace.',
      type: 'string',
      default: 'default',
    },
    {
      key: 'localtrack.defaultSmoothing',
      title: 'Default EMA Smoothing',
      description: 'Default Exponential Moving Average (EMA) smoothing weight for metric charts.',
      type: 'number',
      default: 0.0,
    },
  ],
};

export { LocalTrackWorkspacePane };
export { LocalTrackIcon } from './components/LocalTrackIcon';
export * from './types';
export * from './store';
export * from './api';
