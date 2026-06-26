import { registry, type ModuleManifest } from '../../registry';
import { getActiveVisualizer } from './store';
import { VisualizerWidget } from './widgets';

export const visualizerModule: ModuleManifest = {
  id: 'visualizer',
  title: 'Visualizer',
  widgets: [
    {
      id: 'visualizer.pane',
      title: 'Visualizer',
      component: VisualizerWidget,
      defaultPlacement: 'center',
      agentTools: [
        {
          name: 'visualizer.render_js',
          description:
            'Render a JavaScript drawing/animation using HTML5 2D Canvas, Three.js, or Babylon.js. ' +
            'The script may EITHER return lifecycle hooks { init(canvas, THREE, BABYLON), tick(timeMs, canvas), cleanup() } ' +
            'OR be a standalone script: a `canvas`, `THREE`, and `BABYLON` are in scope, and a renderer created with ' +
            '`new THREE.WebGLRenderer()` automatically targets that canvas (no need to append it to the page).',
          params: {
            type: 'object',
            properties: {
              mode: {
                type: 'string',
                enum: ['canvas', 'three', 'babylon'],
                description: 'The rendering engine mode.',
              },
              code: { type: 'string', description: 'The raw JavaScript code.' },
            },
            required: ['mode', 'code'],
          },
          sideEffect: true,
          handler: async (args) => {
            const { mode, code } = args as { mode: 'canvas' | 'three' | 'babylon'; code: string };
            // Auto open the panel
            registry.openPanel('visualizer.pane');
            // Wait 100ms for React mounting if needed
            await new Promise((resolve) => setTimeout(resolve, 100));

            const active = getActiveVisualizer();
            if (active) {
              active.setMode(mode);
              active.updateCode(code);
              active.run();
              return { success: true, mode, codeLength: code.length };
            }
            return { error: 'Visualizer pane is not open or mounted.' };
          },
        },
        {
          name: 'visualizer.run_pygame',
          description:
            'Run a Python Pygame animation script on the backend and stream the frames to the visualizer panel.',
          params: {
            type: 'object',
            properties: {
              code: { type: 'string', description: 'The raw Python/Pygame script.' },
            },
            required: ['code'],
          },
          sideEffect: true,
          handler: async (args) => {
            const { code } = args as { code: string };
            registry.openPanel('visualizer.pane');
            await new Promise((resolve) => setTimeout(resolve, 100));

            const active = getActiveVisualizer();
            if (active) {
              active.setMode('pygame');
              active.updateCode(code);
              active.run();
              return { success: true, codeLength: code.length };
            }
            return { error: 'Visualizer pane is not open or mounted.' };
          },
        },
        {
          name: 'visualizer.clear',
          description: 'Clear the active visualizer visualization and code editor.',
          params: { type: 'object', properties: {} },
          sideEffect: true,
          handler: () => {
            const active = getActiveVisualizer();
            if (active) {
              active.stop();
              active.updateCode('');
              return { success: true };
            }
            return { error: 'Visualizer pane is not open.' };
          },
        },
        {
          name: 'visualizer.get_state',
          description:
            'Retrieve the current visualizer rendering status, active mode, and error details.',
          params: { type: 'object', properties: {} },
          handler: () => {
            const active = getActiveVisualizer();
            if (active) {
              return active.getState();
            }
            return { error: 'Visualizer pane is not open.' };
          },
        },
        {
          name: 'visualizer.export_to_editor',
          description:
            "Send the visualizer's current script to the editor as a new editable buffer, " +
            'and link the visualizer to it so subsequent edits in the editor re-render live. ' +
            "Use 'note' (default) for a backend-persisted scratch buffer, or 'file' to write a " +
            'workspace file (falls back to a note when no workspace root is available).',
          params: {
            type: 'object',
            properties: {
              target: {
                type: 'string',
                enum: ['note', 'file'],
                description: 'Where the exported buffer lives. Defaults to note.',
              },
            },
          },
          sideEffect: true,
          handler: async (args) => {
            const { target } = args as { target?: 'note' | 'file' };
            const active = getActiveVisualizer();
            if (!active) return { error: 'Visualizer pane is not open or mounted.' };
            const uri = await active.exportToEditor(target ?? 'note');
            return uri ? { success: true, uri } : { error: 'Export to editor failed.' };
          },
        },
      ],
    },
  ],
  commands: [
    {
      id: 'visualizer.open',
      title: 'Visualizer: Open Pane',
      run: () => registry.openPanel('visualizer.pane'),
    },
  ],
};
