import { useEffect, useRef } from 'react';
// Type-only import — erased at build time, so three stays lazy-loaded below.
import type { AnimationMixer } from 'three';

/**
 * The dashboard friend: updated to load a custom GLB avatar,
 * retarget a looping animation from a separate GLB file,
 * and correct the Mixamo Y-up to glTF Z-up rotation.
 */
export function Avatar3D({
  size = 180,
  modelUrl = '/my-avatar.glb',
  animUrl = '/dancing.glb',
}: {
  size?: number;
  modelUrl?: string;
  animUrl?: string;
}) {
  const mount = useRef<HTMLDivElement>(null);

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

        let mixer: AnimationMixer | undefined;

        const loader = new GLTFLoader();

        // Load BOTH the avatar mesh and the dancing animation simultaneously
        Promise.all([loader.loadAsync(modelUrl), loader.loadAsync(animUrl)])
          .then(([avatarGltf, animGltf]) => {
            if (disposed) return;

            const model = avatarGltf.scene;

            // Scale the avatar
            model.scale.set(2.5, 2.5, 2.5);

            // --- THE FIX: Rotate 90 degrees to counter the Mixamo orientation ---
            model.rotation.x = Math.PI / 2;

            // Center the model mathematically
            const box = new THREE.Box3().setFromObject(model);
            const center = box.getCenter(new THREE.Vector3());

            // Framing for the new upright position
            model.position.x = -center.x;
            model.position.z = -center.z;
            model.position.y = -1.5;

            friend.add(model);

            // Apply the dancing animation to the avatar's skeleton
            if (animGltf.animations && animGltf.animations.length > 0) {
              mixer = new THREE.AnimationMixer(model);
              const action = mixer.clipAction(animGltf.animations[0]);

              // Ensure the animation loops infinitely
              action.setLoop(THREE.LoopRepeat, Infinity);
              action.play();
            }
          })
          .catch((error) => {
            console.error('Error loading 3D assets:', error);
          });

        // Status orb
        const statusOrb = new THREE.Mesh(
          new THREE.SphereGeometry(0.12, 16, 16),
          new THREE.MeshStandardMaterial({
            color: 0x2ed573,
            emissive: 0x2ed573,
            emissiveIntensity: 0.6,
          }),
        );
        scene.add(statusOrb);

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

          // Step the animation mixer forward
          if (mixer) {
            mixer.update(delta);
          }

          // Keep the subtle floating and cursor-tracking effect
          friend.position.y = Math.sin(t * 1.5) * 0.05;
          friend.rotation.y += (pointer.x * 0.5 - friend.rotation.y) * 0.05;
          friend.rotation.x += (-pointer.y * 0.3 - friend.rotation.x) * 0.05;

          // Orb orbits the head
          statusOrb.position.set(
            Math.cos(t * 0.8) * 0.8,
            1.7 + Math.sin(t * 1.2) * 0.1,
            Math.sin(t * 0.8) * 0.8,
          );

          renderer.render(scene, camera);
          frame = requestAnimationFrame(tick);
        };
        tick();

        cleanup = () => {
          cancelAnimationFrame(frame);
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
  }, [size, modelUrl, animUrl]);

  return <div ref={mount} className="avatar3d" style={{ width: size, height: size }} aria-hidden />;
}
