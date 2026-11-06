/**
 * Compile every program and upload every texture a map needs before the player
 * can look at it.
 *
 * three compiles a material's program the first time it draws, and uploads a
 * texture the first time a draw samples it. Both happen in the middle of a
 * frame, so the first sight of a new corner of the map cost a hitch: the G0
 * baseline's browser p99 was 6–40 ms against a 1–9 ms median, and the stalls
 * sat on frames that turned toward something new. This moves that work onto the
 * loading screen, where waiting is expected.
 *
 * **Compiling is not enough on its own: what is compiled must not be drawn
 * until it has linked.** With `KHR_parallel_shader_compile` the link runs on
 * the driver's threads, but the first draw with a program asks for its uniform
 * locations, and that waits for the link right there in the frame. So the
 * caller keeps the map hidden until this resolves (`HorribleAssaultPanel`'s
 * `prewarm`); three compiles hidden objects all the same.
 *
 * Takes the renderer and three's objects as parameters, like the rest of the
 * module, so the lazy-load stays in one place.
 */
import type * as THREE from 'three';

/** Every texture any material under `root` samples. */
export function texturesOf(root: THREE.Object3D): Set<THREE.Texture> {
  const out = new Set<THREE.Texture>();
  root.traverse((o) => {
    const material = (o as THREE.Mesh).material as THREE.Material | THREE.Material[] | undefined;
    if (!material) return;
    for (const m of Array.isArray(material) ? material : [material]) {
      for (const value of Object.values(m)) {
        if ((value as THREE.Texture | null)?.isTexture) out.add(value as THREE.Texture);
      }
    }
  });
  return out;
}

/** How long a load may wait on the driver before it gives up and draws anyway. */
export const PREWARM_TIMEOUT_MS = 4000;

/**
 * Upload `root`'s textures, then compile its programs against `target`: the
 * target the scene is really drawn into (`PostChain.sceneTarget`), or `null` for
 * the canvas. The target matters because three keys a program on it (tone
 * mapping and output colour space are applied only when drawing to the canvas),
 * so a program compiled against the wrong one is a different program and the
 * hitch comes back.
 *
 * `lights` is the scene `root` will be lit by, when `root` is not in it yet —
 * the first player to appear, say. The light count is part of every lit
 * program's key, so compiling against no lights would compile the wrong one.
 *
 * `upload` replaces the one-go `initTexture` pass, for something readied while
 * the game is running (`pacedUploads`): on the loading screen a long task does
 * not matter, in a firefight it is a frozen frame.
 *
 * Never rejects and never waits past `PREWARM_TIMEOUT_MS`: a warm cache is an
 * optimisation, and a driver that never reports a program ready must not hold
 * the map hostage.
 */
export async function prewarm(
  renderer: THREE.WebGLRenderer,
  root: THREE.Object3D,
  camera: THREE.Camera,
  target: THREE.WebGLRenderTarget | null,
  lights?: THREE.Scene,
  upload?: (texture: THREE.Texture) => Promise<void>,
): Promise<void> {
  const started = performance.now();
  try {
    if (upload) {
      for (const texture of texturesOf(root)) await upload(texture);
    } else {
      for (const texture of texturesOf(root)) renderer.initTexture(texture);
    }
    const prior = renderer.getRenderTarget();
    renderer.setRenderTarget(target);
    let compiled: Promise<unknown>;
    try {
      // The compile itself runs synchronously here, against `target`; the
      // promise only waits for the driver to finish linking in parallel.
      compiled = lights
        ? renderer.compileAsync(root, camera, lights)
        : renderer.compileAsync(root, camera);
    } finally {
      renderer.setRenderTarget(prior);
    }
    let timer: ReturnType<typeof setTimeout> | undefined;
    await Promise.race([
      compiled,
      new Promise((resolve) => {
        timer = setTimeout(resolve, PREWARM_TIMEOUT_MS);
      }),
    ]);
    clearTimeout(timer);
  } catch {
    // A failed prewarm is a hitch later, not an error now.
  }
  // In the browser's own performance timeline, so a slow load can be read off it.
  performance.measure?.(`hassault:prewarm:${root.name || root.type}`, { start: started });
}

/** How much of a task `pacedUploads` spends uploading before it yields. */
export const UPLOAD_BUDGET_MS = 4;

/**
 * `renderer.initTexture`, one texture at a time, yielding to the event loop
 * once `budgetMs` of uploading has gone by, so a frame can be drawn between
 * batches. For textures that arrive after the map is shown: the photo maps on a
 * slow download, or turned on live. A burst of uploads in one task delays the
 * next frame by all of them, so pacing them turns one long stall into a few
 * frames that are each slightly slower.
 *
 * The yield is a timeout rather than `requestAnimationFrame`, which never fires
 * in a hidden tab and would hold the uploads until it was shown.
 */
export function pacedUploads(
  renderer: Pick<THREE.WebGLRenderer, 'initTexture'>,
  budgetMs = UPLOAD_BUDGET_MS,
  now: () => number = () => performance.now(),
): (texture: THREE.Texture) => Promise<void> {
  let spent = 0;
  let queue: Promise<void> = Promise.resolve();
  return (texture) => {
    queue = queue.then(async () => {
      if (spent >= budgetMs) {
        await new Promise((resolve) => setTimeout(resolve, 0));
        spent = 0;
      }
      const started = now();
      try {
        renderer.initTexture(texture);
      } catch {
        // Uploaded on first draw instead: a hitch, not an error.
      }
      spent += now() - started;
    });
    return queue;
  };
}
