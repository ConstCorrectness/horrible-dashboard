import { useEffect, useRef } from 'react';

/**
 * The dashboard friend: a small three.js scene in the logo's palette —
 * red body, white eyes, green status orb — bobbing and following the pointer.
 * three is loaded dynamically so the workspace view never pays for it.
 */
export function Avatar3D({ size = 180 }: { size?: number }) {
  const mount = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = mount.current;
    if (!el) return;
    let disposed = false;
    let cleanup: (() => void) | undefined;

    void import('three').then((THREE) => {
      if (disposed) return;

      const scene = new THREE.Scene();
      const camera = new THREE.PerspectiveCamera(40, 1, 0.1, 50);
      camera.position.z = 6;
      const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true });
      renderer.setSize(size, size);
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
      el.appendChild(renderer.domElement);

      const friend = new THREE.Group();
      const body = new THREE.Mesh(
        new THREE.SphereGeometry(1.4, 48, 48),
        new THREE.MeshStandardMaterial({ color: 0xff4757, roughness: 0.35, metalness: 0.1 }),
      );
      friend.add(body);
      const eyeMaterial = new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.2 });
      const pupilMaterial = new THREE.MeshBasicMaterial({ color: 0x1e1e2f });
      for (const x of [-0.45, 0.45]) {
        const eye = new THREE.Mesh(new THREE.SphereGeometry(0.22, 24, 24), eyeMaterial);
        eye.position.set(x, 0.25, 1.25);
        friend.add(eye);
        const pupil = new THREE.Mesh(new THREE.SphereGeometry(0.1, 16, 16), pupilMaterial);
        pupil.position.set(x, 0.25, 1.44);
        friend.add(pupil);
      }
      const statusOrb = new THREE.Mesh(
        new THREE.SphereGeometry(0.16, 16, 16),
        new THREE.MeshStandardMaterial({
          color: 0x2ed573,
          emissive: 0x2ed573,
          emissiveIntensity: 0.6,
        }),
      );
      friend.add(statusOrb);
      scene.add(friend);

      scene.add(new THREE.AmbientLight(0xffffff, 0.7));
      const key = new THREE.DirectionalLight(0xffffff, 1.4);
      key.position.set(2, 3, 4);
      scene.add(key);

      const pointer = { x: 0, y: 0 };
      const onPointer = (e: PointerEvent) => {
        pointer.x = (e.clientX / window.innerWidth) * 2 - 1;
        pointer.y = (e.clientY / window.innerHeight) * 2 - 1;
      };
      window.addEventListener('pointermove', onPointer);

      let frame = 0;
      const clock = new THREE.Clock();
      const tick = () => {
        const t = clock.getElapsedTime();
        friend.position.y = Math.sin(t * 1.5) * 0.12;
        friend.rotation.y += (pointer.x * 0.5 - friend.rotation.y) * 0.05;
        friend.rotation.x += (-pointer.y * 0.3 - friend.rotation.x) * 0.05;
        statusOrb.position.set(Math.cos(t * 0.8) * 1.45, 1.0, Math.sin(t * 0.8) * 1.45);
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
    });

    return () => {
      disposed = true;
      cleanup?.();
    };
  }, [size]);

  return <div ref={mount} className="avatar3d" style={{ width: size, height: size }} aria-hidden />;
}
