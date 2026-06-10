import { useEffect, useRef } from 'react';
// Type-only imports — erased at build time, so three stays lazy-loaded below.
import type { AnimationAction, AnimationMixer } from 'three';

/** Maps a mood name to the animation clip (a .glb in apps/web/public) that expresses it. */
export type AvatarMoodMap = Record<string, string>;

/**
 * The avatar's emotional moods → animation clips. To add a mood, drop a .glb in
 * apps/web/public and add a line here; pass `mood` to switch (it cross-fades).
 */
export const DEFAULT_AVATAR_MOODS: AvatarMoodMap = {
  happy: '/dancing.glb',
  flair: '/flair.glb',
  error: '/falling-over.glb',
};

export const DEFAULT_AVATAR_MOOD = 'happy';

/**
 * The dashboard friend: a rigged glTF avatar that plays one of several mood
 * animations (retargeted onto its skeleton) and cross-fades when the mood
 * changes. Corrects the Mixamo Y-up to glTF Z-up orientation. three and the
 * loader are dynamically imported so non-home views never pay for them.
 */
export function Avatar3D({
  size = 180,
  modelUrl = '/my-avatar.glb',
  moods = DEFAULT_AVATAR_MOODS,
  mood = DEFAULT_AVATAR_MOOD,
}: {
  size?: number;
  modelUrl?: string;
  /** Memoize this if you pass a literal, or the scene rebuilds each render. */
  moods?: AvatarMoodMap;
  mood?: string;
}) {
  const mount = useRef<HTMLDivElement>(null);
  // Bridge mood changes into the running scene without tearing it down.
  const desiredMood = useRef(mood);
  const applyMood = useRef<(mood: string) => void>(() => {});

  useEffect(() => {
    desiredMood.current = mood;
    applyMood.current(mood);
  }, [mood]);

  useEffect(() => {
    const el = mount.current;
    if (!el) return;
    let disposed = false;
    let cleanup: (() => void) | undefined;

    Promise.all([import('three'), import('three/examples/jsm/loaders/GLTFLoader.js')]).then(
      ([THREE, { GLTFLoader }]) => {
        if (disposed) return;

        const scene = new THREE.Scene();

        // Camera framed for the upper body
        const camera = new THREE.PerspectiveCamera(40, 1, 0.1, 50);
        camera.position.z = 4.2;
        camera.position.y = 1.6;

        const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true });
        renderer.setSize(size, size);
        renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
        renderer.outputColorSpace = THREE.SRGBColorSpace;
        el.appendChild(renderer.domElement);

        const friend = new THREE.Group();
        scene.add(friend);

        // Animation state, populated once assets load.
        let mixer: AnimationMixer | undefined;
        const actions: Record<string, AnimationAction> = {};
        let active: AnimationAction | undefined;
        const setMood = (next: string) => {
          const action = actions[next] ?? actions[DEFAULT_AVATAR_MOOD];
          if (!action || action === active) return;
          action.reset().setLoop(THREE.LoopRepeat, Infinity).setEffectiveWeight(1).play();
          if (active) active.crossFadeTo(action, 0.4, false);
          active = action;
        };

        const loader = new GLTFLoader();
        const moodEntries = Object.entries(moods);

        // Load the avatar mesh and every mood's animation clip together.
        Promise.all([
          loader.loadAsync(modelUrl),
          ...moodEntries.map(([, url]) => loader.loadAsync(url)),
        ])
          .then(([avatarGltf, ...animGltfs]) => {
            if (disposed) return;

            const model = avatarGltf.scene;
            model.scale.set(2.5, 2.5, 2.5);
            // Counter the Mixamo orientation so the character stands upright.
            model.rotation.x = Math.PI / 2;

            // Center horizontally, drop to frame the upper body.
            const box = new THREE.Box3().setFromObject(model);
            const center = box.getCenter(new THREE.Vector3());
            model.position.x = -center.x;
            model.position.z = -center.z;
            model.position.y = -1.5;
            friend.add(model);

            mixer = new THREE.AnimationMixer(model);
            moodEntries.forEach(([moodName], i) => {
              const clip = animGltfs[i].animations[0];
              if (clip) actions[moodName] = mixer!.clipAction(clip);
            });

            applyMood.current = setMood;
            setMood(desiredMood.current); // honor the latest requested mood
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
        window.addEventListener('pointermove', onPointer);

        let frame = 0;
        const clock = new THREE.Clock();
        const tick = () => {
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
        tick();

        cleanup = () => {
          cancelAnimationFrame(frame);
          applyMood.current = () => {};
          window.removeEventListener('pointermove', onPointer);
          renderer.dispose();
          renderer.domElement.remove();
        };
      },
    );

    return () => {
      disposed = true;
      cleanup?.();
    };
  }, [size, modelUrl, moods]);

  return <div ref={mount} className="avatar3d" style={{ width: size, height: size }} aria-hidden />;
}
