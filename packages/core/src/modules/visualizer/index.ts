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
      role: 'document',
      // A rendered animation is something you show; the surrounding shell is a
      // border around it.
      fullscreen: true,
      icon: '🎞',
      agentTools: [
        {
          name: 'visualizer.render_js',
          description:
            'Render a JavaScript drawing/animation with HTML5 2D Canvas, Three.js, or Babylon.js. ' +
            'One tool for all three JS engines — pick with `mode`. ' +
            'Your `code` is a function body that MUST end by `return`ing a lifecycle object ' +
            '{ init, tick, cleanup }. Declare all state in outer-scope `let`s, assign them in `init`, ' +
            'and read them in `tick` (which receives only `(timeMs, canvas)` — do NOT stash state on ' +
            "`canvas` or rely on init's return value). `init(canvas, THREE, BABYLON)` builds the scene " +
            'once; `tick(timeMs, canvas)` draws ONE frame (the host calls it every animation frame); ' +
            '`cleanup()` disposes. ' +
            "DON'T: create your own canvas, call `requestAnimationFrame` yourself, use `window.onload`, " +
            'or append anything to the page — the host owns the render loop and the canvas. ' +
            'Copy this shape exactly (three mode; swap geometry/material for other shapes/colors): ' +
            '```\nlet scene, camera, renderer, mesh;\nreturn {\n' +
            '  init(canvas, THREE) {\n' +
            '    scene = new THREE.Scene();\n' +
            '    camera = new THREE.PerspectiveCamera(75, canvas.clientWidth / canvas.clientHeight, 0.1, 1000);\n' +
            '    camera.position.z = 5;\n' +
            '    renderer = new THREE.WebGLRenderer({ canvas, alpha: true });\n' +
            '    renderer.setSize(canvas.clientWidth, canvas.clientHeight, false);\n' +
            '    mesh = new THREE.Mesh(new THREE.SphereGeometry(1, 32, 32), new THREE.MeshBasicMaterial({ color: 0xff0000 }));\n' +
            '    scene.add(mesh);\n' +
            '  },\n' +
            '  tick(timeMs) { mesh.rotation.y = timeMs * 0.001; renderer.render(scene, camera); },\n' +
            '  cleanup() { renderer.dispose(); },\n' +
            '};\n```\n' +
            'For other modes keep the same skeleton, changing only the drawing API: ' +
            "canvas → draw with `canvas.getContext('2d')` inside tick (no THREE). " +
            'babylon → `new BABYLON.Engine(canvas, true)` + `BABYLON.MeshBuilder` in init; `scene.render()` in tick. ' +
            'To change something already on screen, first read the loaded script with `visualizer.get_state` ' +
            'and return it with your minimal edit applied — do not rewrite from scratch.',
          params: {
            type: 'object',
            properties: {
              mode: {
                type: 'string',
                enum: ['canvas', 'three', 'babylon'],
                description:
                  'The rendering engine: canvas (2D), three (Three.js), or babylon (Babylon.js).',
              },
              code: {
                type: 'string',
                description:
                  'JavaScript that returns the { init, tick, cleanup } lifecycle object described above. ' +
                  'When editing an existing visualization, pass the current script from get_state with minimal changes.',
              },
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
            'Run a Python Pygame animation script on the backend and stream the frames to the ' +
            'visualizer panel. Structure: `pygame.init()`, set a modest display size, then a ' +
            '`while running:` loop that fills the screen, draws, and calls `pygame.display.flip()` ' +
            'each iteration (flip triggers the frame capture + websocket push). To modify what is ' +
            'on screen, read the loaded script with visualizer.get_state and apply a minimal edit ' +
            'rather than rewriting.',
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
            'Retrieve the current visualizer state: the active mode, rendering status, error ' +
            'details, AND the full current source `code`. To modify what is on screen (e.g. ' +
            '"make the sphere red"), call this first to read the current code, apply a MINIMAL ' +
            'edit that preserves the existing structure (keep its { init, tick, cleanup } hooks), ' +
            'then call visualizer.render_js (or run_pygame) with the edited script. Do not rewrite ' +
            'from scratch, and do not ask the user for code that is already loaded.',
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
