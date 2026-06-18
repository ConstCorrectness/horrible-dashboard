import { useEffect, useRef, useState } from 'react';
import * as THREE from 'three';

import { useAgentContext } from '../../agent-context';
import { sendChannel, subscribeChannel } from '../../ws';
import { registerVisualizerInstance } from './store';
import { getBuffer, listBufferUris } from '../editor/buffers';
import { getActiveBufferSource } from '../editor/index';

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
`
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

  // Helper to resolve the active code and targeted buffer URI
  const getResolvedCode = (): { uri: string | null; code: string } => {
    let uri: string | null = null;
    if (targetUri === 'active') {
      uri = getActiveBufferSource();
    } else if (targetUri !== 'none') {
      uri = targetUri;
    }

    if (uri) {
      const buffer = getBuffer(uri);
      if (buffer) {
        return { uri, code: buffer.snapshot().content };
      }
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
      targetUri: resolved.uri ?? 'none',
    };
  });

  // Register the visualizer instance callbacks for the store/agent tools
  useEffect(() => {
    const unregister = registerVisualizerInstance({
      setMode: (m) => setMode(m),
      updateCode: (newCode) => {
        setCode(newCode);
        const resolved = getResolvedCode();
        if (resolved.uri) {
          const buffer = getBuffer(resolved.uri);
          if (buffer) {
            buffer.setContent(newCode);
          }
        }
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
        };
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
      setOpenBuffers(listBufferUris());
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
        console.error("Cleanup error:", err);
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
      let babylonLib = null;
      if (mode === 'babylon') {
        babylonLib = await loadBabylonLib();
      }

      // Compile JS code using Function constructor
      const runFn = new Function('THREE', 'BABYLON', currentCode);
      const hooks: ScriptHooks = runFn(THREE, babylonLib);

      if (!hooks || typeof hooks !== 'object') {
        throw new Error('Script must return a lifecycle hooks object.');
      }

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
    } catch (err) {
      const errMsg = String(err);
      if (
        errMsg.includes('WebGL') ||
        errMsg.includes('webgl') ||
        errMsg.includes('context') ||
        errMsg.toLowerCase().includes('webgl')
      ) {
        setError(
          `WebGL Error: Failed to create WebGL context. Hardware acceleration or WebGL support might be disabled in this environment (e.g. headless shell, VM, or browser settings). Please switch to 'canvas' (Canvas 2D) or 'pygame' mode, or enable hardware acceleration in your client settings.`
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

    const runResolved = () => {
      const resolved = getResolvedCode();
      lastCodeRef.current = resolved.code;
      runCode(resolved.code);
    };

    runResolved();

    const interval = setInterval(() => {
      const resolved = getResolvedCode();
      if (resolved.code !== lastCodeRef.current) {
        lastCodeRef.current = resolved.code;
        runCode(resolved.code);
      }
    }, 500);

    return () => {
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

  return (
    <div className="vdb-container visualizer-root" style={{ height: '100%', width: '100%' }}>
      {/* Renderer Pane */}
      <div className="vdb-body visualizer-render-pane" style={{ flex: 1, padding: 0, display: 'flex', flexDirection: 'column', height: '100%' }}>
        <div className="vdb-header visualizer-header" style={{ padding: '0.4rem 0.8rem', justifyContent: 'space-between' }}>
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

            <div style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', marginLeft: '1rem' }}>
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
