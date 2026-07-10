import { useEffect, useRef, useState } from 'react';
import * as THREE from 'three';

import { useAgentContext } from '../../agent-context';
import { registry } from '../../registry';
import { sendChannel, subscribeChannel } from '../../ws';
import { registerVisualizerInstance } from './store';
import { languageForMode } from './bridge';
import type { EditorService } from '../editor/service';

/** The editor's buffer surface, looked up lazily (the editor module registers it
 * at load). Undefined only if the editor module never loaded. */
const editor = (): EditorService | undefined => registry.getService<EditorService>('editor');

type VisualizerMode = 'canvas' | 'three' | 'babylon' | 'pygame';

interface ScriptHooks {
  init?: (canvas: HTMLCanvasElement, threeLib: typeof THREE, babylonLib: unknown) => void;
  tick?: (timeMs: number, canvas: HTMLCanvasElement) => void;
  cleanup?: () => void;
}

// Default code templates
const TEMPLATES: Record<VisualizerMode, string> = {
  canvas: `// HTML5 2D Canvas Animation Template
return {
  init: (canvas) => {
    canvas.width = canvas.clientWidth;
    canvas.height = canvas.clientHeight;
  },
  tick: (time, canvas) => {
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    
    // Clear with trail effect
    ctx.fillStyle = 'rgba(20, 22, 26, 0.15)';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    
    // Draw bouncing/pulsing orb
    const cx = canvas.width / 2;
    const cy = canvas.height / 2;
    const radius = 50 + Math.sin(time * 0.003) * 20;
    
    const grad = ctx.createRadialGradient(cx, cy, 5, cx, cy, radius);
    grad.addColorStop(0, '#8bb9fe');
    grad.addColorStop(1, 'transparent');
    
    ctx.fillStyle = grad;
    ctx.beginPath();
    ctx.arc(cx + Math.sin(time * 0.0015) * 80, cy + Math.cos(time * 0.002) * 50, radius, 0, Math.PI * 2);
    ctx.fill();
  },
  cleanup: () => {
    console.log("Canvas visualization cleaned up.");
  }
};`,
  three: `// Three.js 3D WebGL Animation Template
let scene, camera, renderer, cube, torus;

return {
  init: (canvas, THREE) => {
    scene = new THREE.Scene();
    camera = new THREE.PerspectiveCamera(75, canvas.clientWidth / canvas.clientHeight, 0.1, 1000);
    camera.position.z = 6;
    
    renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
    renderer.setSize(canvas.clientWidth, canvas.clientHeight, false);
    
    const boxGeom = new THREE.BoxGeometry(2, 2, 2);
    const boxMat = new THREE.MeshNormalMaterial({ wireframe: false });
    cube = new THREE.Mesh(boxGeom, boxMat);
    scene.add(cube);
    
    const torusGeom = new THREE.TorusGeometry(3, 0.3, 16, 100);
    const torusMat = new THREE.MeshBasicMaterial({ color: 0x6ea8fe, wireframe: true });
    torus = new THREE.Mesh(torusGeom, torusMat);
    scene.add(torus);
  },
  tick: (time) => {
    if (cube) {
      cube.rotation.x = time * 0.0008;
      cube.rotation.y = time * 0.0012;
    }
    if (torus) {
      torus.rotation.z = time * 0.0003;
      torus.rotation.x = time * 0.0002;
    }
    if (renderer && scene && camera) {
      renderer.render(scene, camera);
    }
  },
  cleanup: () => {
    if (renderer) renderer.dispose();
    console.log("Three.js visualization cleaned up.");
  }
};`,
  babylon: `// Babylon.js 3D Animation Template
let engine, scene, sphere;

return {
  init: (canvas, THREE, BABYLON) => {
    engine = new BABYLON.Engine(canvas, true);
    scene = new BABYLON.Scene(engine);
    scene.clearColor = new BABYLON.Color4(0, 0, 0, 0); // Transparent background
    
    const camera = new BABYLON.ArcRotateCamera("camera", -Math.PI / 2, Math.PI / 2.5, 5, BABYLON.Vector3.Zero(), scene);
    camera.attachControl(canvas, true);
    
    const light = new BABYLON.HemisphericLight("light", new BABYLON.Vector3(0, 1, 0), scene);
    light.intensity = 0.8;
    
    sphere = BABYLON.MeshBuilder.CreateSphere("sphere", { diameter: 2 }, scene);
    
    const material = new BABYLON.StandardMaterial("sphMat", scene);
    material.diffuseColor = new BABYLON.Color3(0.43, 0.66, 0.99); // Blue
    material.roughness = 0.5;
    sphere.material = material;
  },
  tick: (time) => {
    if (sphere) {
      sphere.position.y = Math.sin(time * 0.003) * 0.8;
    }
    if (scene) {
      scene.render();
    }
  },
  cleanup: () => {
    if (engine) engine.dispose();
    console.log("Babylon.js visualization cleaned up.");
  }
};`,
  pygame: `# Headless Pygame Stream Template
import pygame
import math

pygame.init()
width, height = 400, 300
screen = pygame.display.set_mode((width, height))

clock = pygame.time.Clock()
running = True
angle = 0.0

while running:
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
            
    screen.fill((20, 22, 26))
    
    # Calculate bouncing coordinates
    for i in range(5):
        offset_angle = angle + (i * 0.5)
        x = 200 + 120 * math.sin(offset_angle)
        y = 150 + 60 * math.cos(offset_angle * 1.5)
        
        color_val = int(127 + 127 * math.sin(offset_angle))
        color = (110, 168, 254) if i % 2 == 0 else (color_val, 100, 255)
        
        pygame.draw.circle(screen, color, (int(x), int(y)), 20 - i * 2)
    
    # display.flip triggers frame capture & websocket push automatically
    pygame.display.flip()
    
    angle += 0.05
    clock.tick(30)
    
pygame.quit()
`,
};

export function VisualizerWidget() {
  const [mode, setMode] = useState<VisualizerMode>('canvas');
  const [code, setCode] = useState(TEMPLATES.canvas);
  const [error, setError] = useState<string | null>(null);
  const [pygameFrame, setPygameFrame] = useState<string | null>(null);
  const [isRunning, setIsRunning] = useState(true);
  const [targetUri, setTargetUri] = useState<string>('active');
  const [openBuffers, setOpenBuffers] = useState<string[]>([]);

  const canvasRef = useRef<HTMLCanvasElement>(null);
  const activeScriptRef = useRef<ScriptHooks | null>(null);
  const animationFrameRef = useRef<number | null>(null);

  // The editor buffer this visualizer is linked to (the "Source" dropdown):
  // 'active' tracks the focused buffer, 'none' uses the pane's own code, or a
  // specific buffer URI.
  const resolveTargetUri = (): string | null => {
    if (targetUri === 'active') return editor()?.getActiveBufferSource() ?? null;
    if (targetUri !== 'none') return targetUri;
    return null;
  };

  // Synchronous, live-only resolution: the targeted buffer's content if it's
  // mounted, else the pane's own `code`. Used by the hot-poll loop and snapshots
  // (a backend fetch per tick would be wasteful); the initial run uses the async
  // path below so an unmounted target tab still resolves.
  const getResolvedCode = (): { uri: string | null; code: string } => {
    const uri = resolveTargetUri();
    if (uri) {
      const live = editor()?.peekBufferContent(uri);
      if (live != null) return { uri, code: live };
    }
    return { uri: null, code };
  };

  // Expose context to agent orchestrator
  useAgentContext(() => {
    const resolved = getResolvedCode();
    return {
      activeMode: mode,
      isRendering: isRunning,
      hasError: error !== null,
      errorMsg: error,
      editorCodeLength: resolved.code.length,
      // The actual source, so the agent can read what's on screen and edit it in
      // place (read → modify → re-render) instead of asking the user for the code.
      code: resolved.code,
      targetUri: resolved.uri ?? 'none',
    };
  });

  // Register the visualizer instance callbacks for the store/agent tools
  useEffect(() => {
    const unregister = registerVisualizerInstance({
      setMode: (m) => setMode(m),
      updateCode: (newCode) => {
        setCode(newCode);
        // Mirror into the linked buffer so an edit here flows to the editor.
        const uri = resolveTargetUri();
        if (uri) editor()?.setBufferContent(uri, newCode);
      },
      run: () => {
        setIsRunning(true);
      },
      stop: () => {
        setIsRunning(false);
        stopAll();
      },
      getState: () => {
        const resolved = getResolvedCode();
        return {
          mode,
          isRunning,
          hasError: error !== null,
          errorMsg: error,
          codeLength: resolved.code.length,
          code: resolved.code,
        };
      },
      exportToEditor: (prefer) => exportToEditor(prefer),
      setTarget: (uri, m) => {
        setMode(m);
        setTargetUri(uri);
        setIsRunning(true);
      },
    });
    return () => {
      unregister();
    };
  }, [mode, isRunning, error, code, targetUri]);

  // Dynamic Babylon.js CDN script loader
  const loadBabylonLib = (): Promise<unknown> => {
    return new Promise((resolve, reject) => {
      const win = window as Window & typeof globalThis & { BABYLON?: unknown };
      if (win.BABYLON) {
        resolve(win.BABYLON);
        return;
      }
      const existing = document.getElementById('babylon-cdn-script');
      if (existing) {
        const check = setInterval(() => {
          if (win.BABYLON) {
            clearInterval(check);
            resolve(win.BABYLON);
          }
        }, 50);
        return;
      }
      const script = document.createElement('script');
      script.id = 'babylon-cdn-script';
      script.src = 'https://cdn.babylonjs.com/babylon.js';
      script.onload = () => resolve(win.BABYLON);
      script.onerror = (e) => reject(new Error('Failed to load Babylon.js from CDN: ' + e));
      document.body.appendChild(script);
    });
  };

  // Periodically refresh the list of open buffers in the dropdown
  useEffect(() => {
    const interval = setInterval(() => {
      setOpenBuffers(editor()?.listBuffers() ?? []);
    }, 1000);
    return () => clearInterval(interval);
  }, []);

  // Stop active animations and clean up loops
  const stopAll = () => {
    // 1. Cancel requestAnimationFrame
    if (animationFrameRef.current) {
      cancelAnimationFrame(animationFrameRef.current);
      animationFrameRef.current = null;
    }
    // 2. Call JS cleanup hooks
    if (activeScriptRef.current && activeScriptRef.current.cleanup) {
      try {
        activeScriptRef.current.cleanup();
      } catch (err) {
        console.error('Cleanup error:', err);
      }
    }
    activeScriptRef.current = null;

    // 3. Stop Pygame subprocess on backend
    sendChannel('visualizer', 'stop_pygame');
    setPygameFrame(null);
  };

  // Run the current script
  const runCode = async (currentCode: string) => {
    stopAll();
    setError(null);

    if (!canvasRef.current) return;
    const canvas = canvasRef.current;

    // Clear canvas before drawing (only for 2D Canvas mode)
    if (mode === 'canvas') {
      const ctx = canvas.getContext('2d');
      if (ctx) ctx.clearRect(0, 0, canvas.width, canvas.height);
    }

    if (!isRunning) return;

    if (mode === 'pygame') {
      // Stream frames from Pygame over WS
      sendChannel('visualizer', 'start_pygame', { code: currentCode });
      return;
    }

    try {
      let babylonLib: unknown = null;
      if (mode === 'babylon') {
        babylonLib = await loadBabylonLib();
      }

      // Track every frame a *standalone* script schedules, so its loop can be
      // cancelled on stop/re-run (hook scripts are driven by our renderLoop instead).
      const standaloneRafs = new Set<number>();
      const scopedRAF = (cb: FrameRequestCallback): number => {
        const id = requestAnimationFrame(cb);
        standaloneRafs.add(id);
        return id;
      };
      const scopedCAF = (id: number): void => {
        standaloneRafs.delete(id);
        cancelAnimationFrame(id);
      };
      // A standalone script commonly does `document.body.appendChild(renderer.domElement)`.
      // We bind the renderer to OUR pane canvas (already in the DOM), so that append would
      // yank the canvas out of the pane — no-op appends onto <body> to keep it contained.
      const scopedDoc = new Proxy(document, {
        get(target, prop) {
          if (prop === 'body') {
            return new Proxy(target.body, {
              get(b, bp) {
                if (bp === 'appendChild') return (node: Node) => node;
                const v = Reflect.get(b, bp);
                return typeof v === 'function' ? v.bind(b) : v;
              },
            });
          }
          const v = Reflect.get(target, prop);
          return typeof v === 'function' ? v.bind(target) : v;
        },
      });

      // Bind THREE so a standalone `new THREE.WebGLRenderer()` (no canvas arg) renders
      // into our pane canvas instead of creating a detached/fullscreen one.
      class BoundRenderer extends THREE.WebGLRenderer {
        constructor(params: THREE.WebGLRendererParameters = {}) {
          super({ antialias: true, alpha: true, ...params, canvas });
        }
        setSize(width: number, height: number): void {
          // Match the drawing buffer to the pane; CSS owns the displayed size.
          super.setSize(canvas.clientWidth || width, canvas.clientHeight || height, false);
        }
      }
      const boundThree = new Proxy(THREE, {
        get: (target, prop, recv) =>
          prop === 'WebGLRenderer' ? BoundRenderer : Reflect.get(target, prop, recv),
      });

      // Compile + run. A script may EITHER return lifecycle hooks {init, tick, cleanup}
      // OR run standalone (set up its own scene/loop) against the provided `canvas`,
      // `THREE`, and `BABYLON`. Scoped rAF/document keep a standalone script contained.
      const runFn = new Function(
        'THREE',
        'BABYLON',
        'canvas',
        'requestAnimationFrame',
        'cancelAnimationFrame',
        'document',
        currentCode,
      );
      const result: unknown = runFn(
        boundThree,
        babylonLib,
        canvas,
        scopedRAF,
        scopedCAF,
        scopedDoc,
      );

      const isHooks = (v: unknown): v is ScriptHooks =>
        !!v && typeof v === 'object' && ('init' in v || 'tick' in v || 'cleanup' in v);

      if (isHooks(result)) {
        const hooks = result;
        // Execute Init
        if (hooks.init) {
          hooks.init(canvas, THREE, babylonLib);
        }

        activeScriptRef.current = hooks;

        // Start tick loop
        if (hooks.tick) {
          const startTime = performance.now();
          const renderLoop = (now: number) => {
            try {
              if (hooks.tick) {
                hooks.tick(now - startTime, canvas);
              }
              animationFrameRef.current = requestAnimationFrame(renderLoop);
            } catch (err) {
              setError(`Runtime execution error: ${String(err)}`);
            }
          };
          animationFrameRef.current = requestAnimationFrame(renderLoop);
        }
      } else {
        // Standalone script: it already ran and started its own loop (tracked via
        // scopedRAF). Record a cleanup that cancels those frames on stop/re-run.
        activeScriptRef.current = {
          cleanup: () => {
            for (const id of standaloneRafs) cancelAnimationFrame(id);
            standaloneRafs.clear();
          },
        };
      }
    } catch (err) {
      const errMsg = String(err);
      if (
        errMsg.includes('WebGL') ||
        errMsg.includes('webgl') ||
        errMsg.includes('context') ||
        errMsg.toLowerCase().includes('webgl')
      ) {
        setError(
          `WebGL Error: Failed to create WebGL context. Hardware acceleration or WebGL support might be disabled in this environment (e.g. headless shell, VM, or browser settings). Please switch to 'canvas' (Canvas 2D) or 'pygame' mode, or enable hardware acceleration in your client settings.`,
        );
      } else {
        setError(`Compilation error: ${errMsg}`);
      }
    }
  };

  const lastCodeRef = useRef<string>('');

  // Hot-reload when target code changes (either edited in editor or dynamic template)
  useEffect(() => {
    if (!isRunning) return;

    let cancelled = false;
    // Initial run: prefer the live mounted content, but if the target buffer's tab
    // is unmounted (the frame drops inactive tabs), fall back to its persisted bytes
    // so the visualization still renders.
    const runInitial = async () => {
      const uri = resolveTargetUri();
      let code = getResolvedCode().code;
      if (uri && editor()?.peekBufferContent(uri) == null) {
        code = (await editor()?.getBufferContent(uri)) ?? code;
      }
      if (cancelled) return;
      lastCodeRef.current = code;
      runCode(code);
    };

    void runInitial();

    // Hot-reload poll: live edits only (a mounted buffer); cheap and synchronous.
    const interval = setInterval(() => {
      const resolved = getResolvedCode();
      if (resolved.code !== lastCodeRef.current) {
        lastCodeRef.current = resolved.code;
        runCode(resolved.code);
      }
    }, 500);

    return () => {
      cancelled = true;
      clearInterval(interval);
      stopAll();
    };
  }, [isRunning, targetUri, mode]);

  // Watch for websocket frame/error events on visualizer channel
  useEffect(() => {
    const unsubscribe = subscribeChannel('visualizer', (msg) => {
      const { event, data } = msg;
      if (event === 'frame') {
        const frameData = (data as { frame: string }).frame;
        setPygameFrame(frameData);
      } else if (event === 'error') {
        const errorMsg = (data as { message: string }).message;
        setError(errorMsg);
      }
    });

    return () => {
      unsubscribe();
    };
  }, []);

  const togglePlayback = () => {
    setIsRunning(!isRunning);
  };

  const handleModeChange = (newMode: VisualizerMode) => {
    setMode(newMode);
    setCode(TEMPLATES[newMode]);
  };

  // Send the current script to the editor as a new buffer, then link to it so
  // further edits there flow back into the visualization (the live "Source").
  // Returns the new buffer's URI, or null if it couldn't be created.
  const exportToEditor = async (prefer: 'note' | 'file'): Promise<string | null> => {
    const svc = editor();
    if (!svc) {
      setError('Editor module is not available.');
      return null;
    }
    try {
      const uri = await svc.openBufferFromContent({
        content: getResolvedCode().code,
        language: languageForMode(mode),
        title: `Visualizer (${mode})`,
        prefer,
      });
      setTargetUri(uri);
      return uri;
    } catch (err) {
      setError(`Export to editor failed: ${String(err)}`);
      return null;
    }
  };

  return (
    <div className="vdb-container visualizer-root" style={{ height: '100%', width: '100%' }}>
      {/* Renderer Pane */}
      <div
        className="vdb-body visualizer-render-pane"
        style={{ flex: 1, padding: 0, display: 'flex', flexDirection: 'column', height: '100%' }}
      >
        <div
          className="vdb-header visualizer-header"
          style={{ padding: '0.4rem 0.8rem', justifyContent: 'space-between' }}
        >
          <div style={{ display: 'flex', alignItems: 'center' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
              <span style={{ fontSize: '0.75rem', color: 'var(--text-dim)' }}>Engine:</span>
              <select
                value={mode}
                onChange={(e) => handleModeChange(e.target.value as VisualizerMode)}
                className="vdb-select"
                style={{
                  fontSize: '0.75rem',
                  padding: '0.15rem 0.4rem',
                  background: 'var(--bg)',
                  color: 'var(--text)',
                  border: '1px solid var(--border)',
                  borderRadius: '3px',
                  outline: 'none',
                }}
              >
                <option value="canvas">🎨 Canvas 2D</option>
                <option value="three">🔺 Three.js</option>
                <option value="babylon">🪐 Babylon.js</option>
                <option value="pygame">🐍 Pygame</option>
              </select>
            </div>

            <div
              style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', marginLeft: '1rem' }}
            >
              <span style={{ fontSize: '0.75rem', color: 'var(--text-dim)' }}>Source:</span>
              <select
                value={targetUri}
                onChange={(e) => setTargetUri(e.target.value)}
                className="vdb-select"
                style={{
                  fontSize: '0.75rem',
                  padding: '0.15rem 0.4rem',
                  background: 'var(--bg)',
                  color: 'var(--text)',
                  border: '1px solid var(--border)',
                  borderRadius: '3px',
                  outline: 'none',
                }}
              >
                <option value="active">🔗 Active Editor Buffer</option>
                <option value="none">💡 Sandbox (Templates Only)</option>
                {openBuffers.map((uri) => (
                  <option key={uri} value={uri}>
                    📄 {uri.replace(/^(workspace-file|note):/, '')}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div style={{ display: 'flex', gap: '0.4rem', alignItems: 'center' }}>
            <button
              className="vdb-btn-refresh"
              onClick={togglePlayback}
              title={isRunning ? 'Pause' : 'Play'}
              style={{
                width: '24px',
                height: '24px',
                padding: 0,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontSize: '0.75rem',
                borderRadius: '4px',
              }}
            >
              {isRunning ? '⏸' : '▶'}
            </button>
            <button
              className="vdb-btn-refresh"
              onClick={() => runCode(getResolvedCode().code)}
              title="Restart"
              style={{
                width: '24px',
                height: '24px',
                padding: 0,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontSize: '0.8rem',
                borderRadius: '4px',
              }}
            >
              ↻
            </button>
            <button
              className="vdb-btn-refresh"
              onClick={(e) => void exportToEditor(e.shiftKey ? 'file' : 'note')}
              title="Export to editor as a new note — Shift-click to write a workspace file instead"
              style={{
                height: '24px',
                padding: '0 0.5rem',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                fontSize: '0.7rem',
                borderRadius: '4px',
                gap: '0.25rem',
              }}
            >
              ⤴ Editor
            </button>
          </div>
        </div>

        {/* Display screen */}
        <div
          className="visualizer-display-container"
          style={{
            flex: 1,
            position: 'relative',
            background: '#14161a',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            minHeight: '200px',
            overflow: 'hidden',
          }}
        >
          {error && (
            <div
              className="vdb-alert error"
              style={{
                position: 'absolute',
                top: '1rem',
                left: '1rem',
                right: '1rem',
                zIndex: 10,
                opacity: 0.9,
                whiteSpace: 'pre-wrap',
                fontFamily: 'monospace',
              }}
            >
              {error}
            </div>
          )}

          {mode === 'pygame' && pygameFrame ? (
            <img
              src={pygameFrame}
              alt="Pygame Headless Frame"
              style={{ maxWidth: '100%', maxHeight: '100%', objectFit: 'contain' }}
            />
          ) : (
            <canvas
              key={mode}
              ref={canvasRef}
              style={{
                width: '100%',
                height: '100%',
                display: mode === 'pygame' ? 'none' : 'block',
              }}
            />
          )}

          {mode === 'pygame' && !pygameFrame && !error && (
            <div style={{ color: 'var(--text-dim)', fontSize: '0.85rem' }}>
              {isRunning ? 'Starting Pygame process & streaming frames...' : 'Playback paused.'}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
