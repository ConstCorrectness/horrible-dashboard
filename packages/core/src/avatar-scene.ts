/**
 * The avatar's three.js scene, kept apart from the React component so that it can
 * outlive one.
 *
 * Opening the agent pane used to freeze every window on the desktop for one to two
 * seconds, every time. Each mount fetched and parsed a 4.7 MB model, decoded
 * fifteen textures (most 1024², for a 120px canvas), uploaded them and compiled
 * seven shader programs — all on the page's one main thread, with nothing kept for
 * the next open. Three things here take that away:
 *
 * 1. **Assets are parsed once per session** (`loadAsset`) and each scene gets a
 *    skeleton-aware clone that shares the geometry and textures.
 * 2. **Textures are shrunk before they are ever uploaded**, off the main thread
 *    (`createImageBitmap` with a resize).
 * 3. **A finished scene is parked, not destroyed**, when its component unmounts
 *    (`acquireAvatar` / `releaseAvatar`). Reopening the pane adopts the parked
 *    canvas: no fetch, no parse, no upload, no compile.
 *
 * three is imported dynamically, so a session that never shows an avatar never
 * loads it.
 */
import type { AnimationAction, AnimationMixer, Object3D, Texture } from 'three';
import type { GLTF } from 'three/examples/jsm/loaders/GLTFLoader.js';

/** Maps a mood name to the animation clip (a .glb in apps/web/public) that expresses it. */
export type AvatarMoodMap = Record<string, string>;

export interface AvatarOptions {
  size: number;
  modelUrl: string;
  moods: AvatarMoodMap;
  /** The mood to fall back to when an unknown one is asked for. */
  fallbackMood: string;
}

export interface AvatarInstance {
  /** The canvas to put in the page. Transparent until the model has loaded. */
  readonly canvas: HTMLCanvasElement;
  /** Cross-fade to a mood. Safe before the model has loaded; the last call wins. */
  setMood(mood: string): void;
  /** Start rendering (while on screen and the page is visible). */
  start(): void;
  /** Stop rendering and listening. The scene stays ready to `start()` again. */
  stop(): void;
  /** Free the WebGL context. The instance is dead afterwards. */
  dispose(): void;
}

/**
 * The largest texture edge an avatar uploads. The biggest avatar on screen is
 * 180 CSS px (360 device px at 2×) and shows a whole figure, so a 512² map is
 * already more texels than pixels; the model ships 1024² ones.
 */
export const AVATAR_MAX_TEXTURE = 512;

/** How long a parked scene keeps its WebGL context before giving it back. */
export const AVATAR_PARK_MS = 5 * 60_000;

type Libs = {
  THREE: typeof import('three');
  GLTFLoader: typeof import('three/examples/jsm/loaders/GLTFLoader.js').GLTFLoader;
  cloneSkinned: (source: Object3D) => Object3D;
};

let libs: Promise<Libs> | null = null;

function loadLibs(): Promise<Libs> {
  libs ??= Promise.all([
    import('three'),
    import('three/examples/jsm/loaders/GLTFLoader.js'),
    import('three/examples/jsm/utils/SkeletonUtils.js'),
  ]).then(([THREE, { GLTFLoader }, SkeletonUtils]) => ({
    THREE,
    GLTFLoader,
    cloneSkinned: SkeletonUtils.clone,
  }));
  return libs;
}

/** Every texture a model's materials reference, once each. */
function texturesOf(root: Object3D): Texture[] {
  const found = new Set<Texture>();
  root.traverse((node) => {
    const material = (node as { material?: unknown }).material;
    for (const mat of Array.isArray(material) ? material : material ? [material] : []) {
      for (const value of Object.values(mat as Record<string, unknown>)) {
        if (value && (value as Texture).isTexture) found.add(value as Texture);
      }
    }
  });
  return [...found];
}

/** The scale that brings a `w`×`h` image within `max`, or 1 if it already is. */
export function shrinkFactor(w: number, h: number, max: number): number {
  const longest = Math.max(w, h);
  return longest > max && longest > 0 ? max / longest : 1;
}

/**
 * Replace oversized texture images with resized ones, before anything uploads
 * them. The resize is `createImageBitmap`'s, which runs off the main thread. A
 * texture that cannot be resized is simply left as it was.
 */
async function shrinkTextures(root: Object3D, max: number): Promise<void> {
  if (typeof createImageBitmap !== 'function') return;
  await Promise.all(
    texturesOf(root).map(async (texture) => {
      const image = texture.image as (ImageBitmap & { close?: () => void }) | undefined;
      if (!image || !image.width || !image.height) return;
      const k = shrinkFactor(image.width, image.height, max);
      if (k === 1) return;
      try {
        const small = await createImageBitmap(image, {
          resizeWidth: Math.max(1, Math.round(image.width * k)),
          resizeHeight: Math.max(1, Math.round(image.height * k)),
          resizeQuality: 'medium',
          // As GLTFLoader decoded it: no second premultiply or colour conversion.
          premultiplyAlpha: 'none',
          colorSpaceConversion: 'none',
        });
        texture.image = small;
        texture.needsUpdate = true;
        image.close?.();
      } catch {
        // Keep the original: a large texture is slow, a missing one is wrong.
      }
    }),
  );
}

/** Parsed assets by URL. A failed load is forgotten, so the next mount retries. */
const assets = new Map<string, Promise<GLTF>>();

function loadAsset(url: string, shrink: boolean): Promise<GLTF> {
  let pending = assets.get(url);
  if (!pending) {
    pending = loadLibs().then(async ({ GLTFLoader }) => {
      const gltf = await new GLTFLoader().loadAsync(url);
      if (shrink) await shrinkTextures(gltf.scene, AVATAR_MAX_TEXTURE);
      return gltf;
    });
    pending.catch(() => assets.delete(url));
    assets.set(url, pending);
  }
  return pending;
}

async function createAvatar(opts: AvatarOptions): Promise<AvatarInstance> {
  const { THREE, cloneSkinned } = await loadLibs();
  const { size, modelUrl, moods, fallbackMood } = opts;

  const scene = new THREE.Scene();

  // Camera framed for the upper body
  const camera = new THREE.PerspectiveCamera(40, 1, 0.1, 50);
  camera.position.z = 4.2;
  camera.position.y = 1.6;

  const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true });
  renderer.setSize(size, size);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  const canvas = renderer.domElement;

  const friend = new THREE.Group();
  scene.add(friend);

  let disposed = false;
  let mixer: AnimationMixer | undefined;
  const actions: Record<string, AnimationAction> = {};
  let active: AnimationAction | undefined;
  let desired = fallbackMood;

  const resolved = () => (moods[desired] ? desired : fallbackMood);

  const play = (action?: AnimationAction) => {
    if (!action || action === active) return;
    action.reset().setLoop(THREE.LoopRepeat, Infinity).setEffectiveWeight(1).play();
    if (active) active.crossFadeTo(action, 0.4, false);
    active = action;
  };

  /** The action for a mood, loading its clip on first use. */
  const actionFor = async (mood: string): Promise<AnimationAction | undefined> => {
    if (actions[mood] || !mixer) return actions[mood];
    const url = moods[mood];
    if (!url) return undefined;
    try {
      const clip = (await loadAsset(url, false)).animations[0];
      if (disposed || !mixer || !clip) return undefined;
      actions[mood] ??= mixer.clipAction(clip);
      return actions[mood];
    } catch (e) {
      console.error(`Failed to load mood ${mood}:`, e);
      return undefined;
    }
  };

  const applyMood = () => {
    if (!mixer) return; // not loaded yet — `desired` is applied when it is
    const mood = resolved();
    void actionFor(mood).then((action) => {
      // A slower clip must not land on top of a mood asked for after it.
      if (resolved() === mood) play(action);
    });
  };

  // The model, and the first mood's clip alongside it. Other clips load on demand.
  const firstMoodUrl = moods[fallbackMood];
  void Promise.all([
    loadAsset(modelUrl, true),
    firstMoodUrl ? loadAsset(firstMoodUrl, false) : Promise.resolve(null),
  ])
    .then(async ([avatarGltf]) => {
      if (disposed) return;
      // A clone, because the parsed scene is shared by every avatar in the
      // session and an Object3D has one parent. Geometry and textures are shared.
      const model = cloneSkinned(avatarGltf.scene);
      model.scale.set(2.5, 2.5, 2.5);
      // Counter the Mixamo orientation so the character stands upright.
      model.rotation.x = Math.PI / 2;

      // Center horizontally, drop to frame the upper body.
      const box = new THREE.Box3().setFromObject(model);
      const center = box.getCenter(new THREE.Vector3());
      model.position.x = -center.x;
      model.position.z = -center.z;
      model.position.y = -1.5;

      // Compile the model's shader programs before it joins the scene. Compiling
      // on first draw links seven programs inside one frame; this lets the driver
      // do it in parallel and resolves when they are ready.
      try {
        await renderer.compileAsync(model, camera, scene);
      } catch {
        // No parallel compile here: the first draw compiles, as it always did.
      }
      if (disposed) return;

      friend.add(model);
      mixer = new THREE.AnimationMixer(model);
      applyMood();
    })
    .catch((error) => {
      console.error('Error loading 3D assets:', error);
    });

  // Dashy — the dashboard mascot: a cute orb that orbits the avatar and
  // faces the viewer. Its glow doubles as the agent status light (green = ready).
  const dashy = new THREE.Group();
  dashy.add(
    new THREE.Mesh(
      new THREE.SphereGeometry(0.16, 24, 24),
      new THREE.MeshStandardMaterial({
        color: 0x2ed573,
        emissive: 0x2ed573,
        emissiveIntensity: 0.6,
      }),
    ),
  );
  const eyeMat = new THREE.MeshBasicMaterial({ color: 0xffffff });
  const pupilMat = new THREE.MeshBasicMaterial({ color: 0x1e1e2f });
  for (const ex of [-0.06, 0.06]) {
    const eye = new THREE.Mesh(new THREE.SphereGeometry(0.05, 16, 16), eyeMat);
    eye.position.set(ex, 0.03, 0.14);
    dashy.add(eye);
    const pupil = new THREE.Mesh(new THREE.SphereGeometry(0.022, 12, 12), pupilMat);
    pupil.position.set(ex, 0.03, 0.18);
    dashy.add(pupil);
  }
  scene.add(dashy);

  // Lighting
  scene.add(new THREE.AmbientLight(0xffffff, 1.2));
  const key = new THREE.DirectionalLight(0xffffff, 1.5);
  key.position.set(2, 3, 4);
  scene.add(key);
  const fill = new THREE.DirectionalLight(0xffffff, 0.5);
  fill.position.set(-2, 1, -2);
  scene.add(fill);

  // Pointer tracking
  const pointer = { x: 0, y: 0 };
  const onPointer = (e: PointerEvent) => {
    pointer.x = (e.clientX / window.innerWidth) * 2 - 1;
    pointer.y = (e.clientY / window.innerHeight) * 2 - 1;
  };

  let frame = 0;
  let running = false;
  let onScreen = true;
  const clock = new THREE.Clock();

  const tick = () => {
    frame = 0;
    if (!running || !onScreen || document.hidden) return;
    const delta = clock.getDelta();
    const t = clock.getElapsedTime();
    mixer?.update(delta);

    // Subtle floating + cursor tracking
    friend.position.y = Math.sin(t * 1.5) * 0.05;
    friend.rotation.y += (pointer.x * 0.5 - friend.rotation.y) * 0.05;
    friend.rotation.x += (-pointer.y * 0.3 - friend.rotation.x) * 0.05;

    // Dashy orbits the head and keeps its face toward the viewer
    dashy.position.set(
      Math.cos(t * 0.8) * 0.8,
      1.7 + Math.sin(t * 1.2) * 0.1,
      Math.sin(t * 0.8) * 0.8,
    );
    dashy.lookAt(camera.position);

    renderer.render(scene, camera);
    frame = requestAnimationFrame(tick);
  };

  /** Run the loop exactly when it should be running. */
  const sync = () => {
    const want = running && onScreen && !document.hidden;
    if (want && !frame) {
      clock.start();
      frame = requestAnimationFrame(tick);
    } else if (!want && frame) {
      cancelAnimationFrame(frame);
      frame = 0;
      clock.stop();
    }
  };

  const observer = new IntersectionObserver(
    (entries) => {
      const entry = entries[0];
      onScreen = entry ? entry.isIntersecting : true;
      sync();
    },
    { threshold: 0.05 },
  );

  const stop = () => {
    if (!running) return;
    running = false;
    window.removeEventListener('pointermove', onPointer);
    document.removeEventListener('visibilitychange', sync);
    observer.disconnect();
    sync();
  };

  return {
    canvas,
    setMood(mood) {
      desired = mood;
      applyMood();
    },
    start() {
      if (disposed || running) return;
      running = true;
      onScreen = true;
      window.addEventListener('pointermove', onPointer, { passive: true });
      document.addEventListener('visibilitychange', sync);
      observer.observe(canvas);
      sync();
    },
    stop,
    dispose() {
      if (disposed) return;
      stop();
      disposed = true;
      mixer?.stopAllAction();
      renderer.dispose();
      // `dispose()` alone leaves the context to the garbage collector, and a page
      // gets only a handful of them.
      renderer.forceContextLoss();
      canvas.remove();
    },
  };
}

/** What makes two avatars interchangeable. */
export function avatarKey(opts: AvatarOptions): string {
  const moods = Object.keys(opts.moods)
    .sort()
    .map((mood) => `${mood}=${opts.moods[mood]}`)
    .join(',');
  return `${opts.size}|${opts.modelUrl}|${opts.fallbackMood}|${moods}`;
}

interface Parked {
  avatar: Promise<AvatarInstance>;
  expiry: ReturnType<typeof setTimeout>;
}

/** One finished scene per key, waiting for its component to come back. */
const parked = new Map<string, Parked>();

/** Seam for tests: how a scene is built. */
let build: (opts: AvatarOptions) => Promise<AvatarInstance> = createAvatar;

/** Test-only: swap the scene builder and empty the pool. */
export function setAvatarBuilderForTests(
  next: ((opts: AvatarOptions) => Promise<AvatarInstance>) | null,
): void {
  build = next ?? createAvatar;
  for (const { expiry } of parked.values()) clearTimeout(expiry);
  parked.clear();
}

/**
 * An avatar for these options: the parked one if there is one, else a new one.
 *
 * Deals in **promises**, so a scene still being built can be parked and adopted
 * too. That is what React's StrictMode does on every mount in development —
 * mount, unmount, mount — and without it each open built the scene twice.
 */
export function acquireAvatar(opts: AvatarOptions): Promise<AvatarInstance> {
  const key = avatarKey(opts);
  const waiting = parked.get(key);
  if (waiting) {
    clearTimeout(waiting.expiry);
    parked.delete(key);
    return waiting.avatar;
  }
  return build(opts);
}

/** Give an avatar back. It is parked for the next mount, or disposed if one already is. */
export function releaseAvatar(opts: AvatarOptions, avatar: Promise<AvatarInstance>): void {
  const key = avatarKey(opts);
  // Callbacks on one promise run in the order they were attached, so this stop
  // always lands before the `start()` of whichever mount adopts the scene next —
  // including a scene that is parked and adopted while it is still being built.
  avatar.then(
    (instance) => {
      instance.stop();
      instance.canvas.remove();
    },
    () => {},
  );
  const discard = () => {
    avatar.then(
      (instance) => instance.dispose(),
      () => {},
    );
  };
  if (parked.has(key)) {
    discard();
    return;
  }
  parked.set(key, {
    avatar,
    expiry: setTimeout(() => {
      if (parked.get(key)?.avatar === avatar) parked.delete(key);
      discard();
    }, AVATAR_PARK_MS),
  });
}
