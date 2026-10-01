import { lazyPane } from '../../lazy-pane';
import { registry, type ModuleManifest } from '../../registry';
import { getActiveVisualizer, type VisualizerInstance } from './store';

// Loaded when the pane first renders, not at boot — see `lazyPane`.
const VisualizerWidget = lazyPane(() => import('./widgets'), 'VisualizerWidget');

/**
 * Open the pane and wait for its instance to register. A fixed 100ms wait was too
 * short the first time: the lazy chunk (three.js included) is still loading, so the
 * agent's first render reported "pane is not open" and burned a round on `show`.
 */
async function openVisualizer(timeoutMs = 5000): Promise<VisualizerInstance | null> {
  registry.openPanel('visualizer.pane');
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const active = getActiveVisualizer();
    if (active || Date.now() >= deadline) return active;
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
}

export const visualizerModule: ModuleManifest = {
  id: 'visualizer',
  title: 'Visualizer',
  category: 'play',
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
            const active = await openVisualizer();
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
          name: 'visualizer.render_html',
          description:
            'Show an HTML document in the Visualizer pane — the way to SHOW charts, diagrams, ' +
            'SVG, D3/Chart.js/Plotly pages, dashboards, infographics or any DOM/CSS visual to the ' +
            'user in-app. Pass a complete self-contained document (inline <style>/<script>; CDN ' +
            '<script src> from cdn.jsdelivr.net or unpkg.com works). It runs in a sandboxed ' +
            'frame with no access to the app or its storage. ' +
            'Prefer this over writing an .html file and telling the user to open it. For a ' +
            'frame-by-frame Canvas/Three.js/Babylon.js animation, render_js also works. To change ' +
            'what is on screen, read it with visualizer.get_state and resend it with a minimal edit.',
          params: {
            type: 'object',
            properties: {
              html: {
                type: 'string',
                description: 'The full HTML document (<!doctype html>…) to render.',
              },
            },
            required: ['html'],
          },
          sideEffect: true,
          handler: async (args) => {
            const { html } = args as { html: string };
            const first = await openVisualizer();
            if (!first) return { error: 'Visualizer pane is not open or mounted.' };
            // Unlink from editor buffers first: with the default "active buffer" source,
            // updateCode mirrors into whatever the user has focused, and an agent's
            // document must not overwrite the user's file.
            first.setTarget('none', 'html');
            // The instance re-registers once that state lands; use the fresh one.
            await new Promise((resolve) => setTimeout(resolve, 50));
            const active = getActiveVisualizer() ?? first;
            active.updateCode(html);
            active.run();
            return { success: true, mode: 'html', codeLength: html.length };
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
            const active = await openVisualizer();
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
