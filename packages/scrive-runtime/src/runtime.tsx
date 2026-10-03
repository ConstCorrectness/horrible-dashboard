/**
 * The Scrive scene runtime: what runs *inside* an `{r3f}` frame.
 *
 * The frame is a srcdoc iframe sandboxed with `allow-scripts` only — an opaque
 * origin, no access to the app, its storage or its session — and a CSP that allows
 * this script, eval (to run the compiled scene), and fetching the site's own assets.
 * Scene code is often agent-written; this is where it is allowed to be wrong.
 *
 * Protocol (`window.postMessage`, every message `{ scrive: 1, type, ... }`):
 *
 *   host → frame   load    { source, params }    compile + mount the scene
 *                  params  { params }            re-render with new tweak values
 *                  poster  {}                    capture the canvas as a PNG
 *                  record  { seconds, fps }      record the canvas as WebM
 *   frame → host   booted  {}                    the runtime is listening
 *                  ready   {}                    the scene mounted (GL context up)
 *                  error   { message }           compile or render failure
 *                  poster  { dataUrl }
 *                  recorded { blob, mime }
 *
 * `ready` is sent when the canvas is created, not on the first frame: a hidden
 * window runs no animation frames, and the host must still learn the scene is up.
 *
 * A published site loads this same file from a standalone scene page that sets
 * `window.__SCRIVE_SCENE__ = { source, params }` first; the scene then mounts at once.
 *
 * A scene is a TSX module whose default export is a component; it receives
 * `{ params }`. It may import `react`, `three`, `@react-three/fiber` and
 * `@react-three/drei` — the copies bundled here — and nothing else.
 */
import * as Drei from '@react-three/drei';
import * as Fiber from '@react-three/fiber';
import * as React from 'react';
import * as JSXRuntime from 'react/jsx-runtime';
import { createRoot } from 'react-dom/client';
import { transform } from 'sucrase';
import * as THREE from 'three';

type Params = Record<string, unknown>;
type SceneComponent = React.ComponentType<{ params: Params }>;

const MODULES: Record<string, unknown> = {
  react: React,
  'react/jsx-runtime': JSXRuntime,
  three: THREE,
  '@react-three/fiber': Fiber,
  '@react-three/drei': Drei,
};

function post(type: string, data: Record<string, unknown> = {}) {
  window.parent.postMessage({ scrive: 1, type, ...data }, '*');
}

/** TSX source → its default export. Imports resolve to the bundled modules only. */
export function compile(source: string): SceneComponent {
  const { code } = transform(source, {
    transforms: ['typescript', 'jsx', 'imports'],
    jsxRuntime: 'automatic',
    production: true,
  });
  const module = { exports: {} as Record<string, unknown> };
  const require = (name: string) => {
    if (!(name in MODULES)) {
      throw new Error(
        `A scene can import react, three, @react-three/fiber and @react-three/drei — not "${name}"`,
      );
    }
    return MODULES[name];
  };

  new Function('require', 'exports', 'module', code)(require, module.exports, module);
  const scene = (module.exports.default ?? module.exports.Scene) as SceneComponent | undefined;
  if (typeof scene !== 'function') {
    throw new Error('A scene module must `export default` a component');
  }
  return scene;
}

class Boundary extends React.Component<{ children: React.ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch(error: unknown) {
    post('error', { message: error instanceof Error ? error.message : String(error) });
  }
  render() {
    return this.state.failed ? null : this.props.children;
  }
}

let canvas: HTMLCanvasElement | null = null;

function App({ Scene, params }: { Scene: SceneComponent; params: Params }) {
  return (
    <Fiber.Canvas
      gl={{ preserveDrawingBuffer: true, antialias: true }}
      dpr={[1, 2]}
      camera={{ position: [3, 2, 5], fov: 45 }}
      onCreated={(state) => {
        canvas = state.gl.domElement;
        post('ready');
      }}
    >
      <Boundary>
        <React.Suspense fallback={null}>
          <Scene params={params} />
        </React.Suspense>
      </Boundary>
    </Fiber.Canvas>
  );
}

const root = createRoot(document.getElementById('scene') ?? document.body);
let Scene: SceneComponent | null = null;

function render(params: Params) {
  if (Scene) root.render(<App Scene={Scene} params={params} />);
}

async function record(seconds: number, fps: number) {
  if (!canvas) throw new Error('the scene has no canvas yet');
  const stream = canvas.captureStream(fps);
  const mime = MediaRecorder.isTypeSupported('video/webm;codecs=vp9')
    ? 'video/webm;codecs=vp9'
    : 'video/webm';
  const recorder = new MediaRecorder(stream, { mimeType: mime, videoBitsPerSecond: 6_000_000 });
  const chunks: Blob[] = [];
  recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data);
  const done = new Promise<void>((resolve) => (recorder.onstop = () => resolve()));
  recorder.start(250);
  await new Promise((r) => setTimeout(r, Math.min(60, Math.max(1, seconds)) * 1000));
  recorder.stop();
  await done;
  post('recorded', { blob: new Blob(chunks, { type: 'video/webm' }), mime: 'video/webm' });
}

window.addEventListener('error', (e) => post('error', { message: e.message }));
window.addEventListener('unhandledrejection', (e) =>
  post('error', { message: String((e.reason as Error)?.message ?? e.reason) }),
);

window.addEventListener('message', (event) => {
  // Only the embedding page talks to a scene.
  if (event.source !== window.parent) return;
  const msg = event.data as { scrive?: number; type?: string; [k: string]: unknown };
  if (msg?.scrive !== 1) return;
  try {
    switch (msg.type) {
      case 'load':
        Scene = compile(String(msg.source ?? ''));
        render((msg.params as Params) ?? {});
        break;
      case 'params':
        render((msg.params as Params) ?? {});
        break;
      case 'poster':
        if (!canvas) throw new Error('the scene has no canvas yet');
        post('poster', { dataUrl: canvas.toDataURL('image/png') });
        break;
      case 'record':
        void record(Number(msg.seconds ?? 5), Number(msg.fps ?? 30)).catch((err: Error) =>
          post('error', { message: err.message }),
        );
        break;
    }
  } catch (err) {
    post('error', { message: err instanceof Error ? err.message : String(err) });
  }
});

// A published scene page (backend/modules/scrive/publish.py `scene_page`) carries its
// scene inline instead of waiting for a host to post it: the page is the host.
const inline = (window as { __SCRIVE_SCENE__?: { source?: string; params?: Params } })
  .__SCRIVE_SCENE__;
if (inline) {
  try {
    Scene = compile(String(inline.source ?? ''));
    render(inline.params ?? {});
  } catch (err) {
    post('error', { message: err instanceof Error ? err.message : String(err) });
    document.body.textContent = `This scene did not compile: ${
      err instanceof Error ? err.message : String(err)
    }`;
  }
}

post('booted');
