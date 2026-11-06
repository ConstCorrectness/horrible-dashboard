/**
 * `prewarm`: compile against the target the scene is really drawn into, upload
 * every texture, and never hold a map load hostage to the driver.
 */
import { describe, expect, it, vi } from 'vitest';
import * as THREE from 'three';

import { PREWARM_TIMEOUT_MS, pacedUploads, prewarm, texturesOf } from '../prewarm';

function fakeRenderer(compiled: Promise<unknown>) {
  let current: THREE.WebGLRenderTarget | null = null;
  const log: string[] = [];
  const renderer = {
    getRenderTarget: () => current,
    setRenderTarget: (t: THREE.WebGLRenderTarget | null) => {
      current = t;
    },
    initTexture: (t: THREE.Texture) => log.push(`upload ${t.name}`),
    compileAsync: (root: THREE.Object3D, _camera: THREE.Camera, lights?: THREE.Scene) => {
      const what = lights ? `${root.name} lit by ${lights.name}` : root.name || 'scene';
      log.push(`compile ${what} into ${current ? current.texture.name : 'canvas'}`);
      return compiled;
    },
  };
  return { renderer: renderer as unknown as THREE.WebGLRenderer, log, current: () => current };
}

function sceneWithTextures() {
  const scene = new THREE.Scene();
  const map = new THREE.Texture();
  map.name = 'albedo';
  const normal = new THREE.Texture();
  normal.name = 'normal';
  scene.add(
    new THREE.Mesh(
      new THREE.BoxGeometry(),
      new THREE.MeshStandardMaterial({ map, normalMap: normal }),
    ),
  );
  // Shared: uploaded once, not once per mesh.
  scene.add(new THREE.Mesh(new THREE.BoxGeometry(), new THREE.MeshBasicMaterial({ map })));
  return scene;
}

describe('texturesOf', () => {
  it('finds every texture a material samples, once each', () => {
    const names = [...texturesOf(sceneWithTextures())].map((t) => t.name).sort();
    expect(names).toEqual(['albedo', 'normal']);
  });
});

describe('prewarm', () => {
  it('uploads, then compiles against the given target, then puts the old one back', async () => {
    const { renderer, log, current } = fakeRenderer(Promise.resolve());
    const prior = new THREE.WebGLRenderTarget(1, 1);
    prior.texture.name = 'prior';
    renderer.setRenderTarget(prior);
    const sceneTarget = new THREE.WebGLRenderTarget(1, 1);
    sceneTarget.texture.name = 'post scene';

    await prewarm(renderer, sceneWithTextures(), new THREE.PerspectiveCamera(), sceneTarget);
    expect(log).toEqual(['upload albedo', 'upload normal', 'compile scene into post scene']);
    expect(current()).toBe(prior);
  });

  it('gives up waiting on a driver that never reports ready', async () => {
    vi.useFakeTimers();
    try {
      const { renderer } = fakeRenderer(new Promise(() => {}));
      let done = false;
      void prewarm(renderer, new THREE.Scene(), new THREE.PerspectiveCamera(), null).then(() => {
        done = true;
      });
      await vi.advanceTimersByTimeAsync(PREWARM_TIMEOUT_MS - 1);
      expect(done).toBe(false);
      await vi.advanceTimersByTimeAsync(2);
      expect(done).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });

  it('never rejects', async () => {
    const { renderer } = fakeRenderer(Promise.reject(new Error('context lost')));
    await expect(
      prewarm(renderer, new THREE.Scene(), new THREE.PerspectiveCamera(), null),
    ).resolves.toBeUndefined();
  });
});

describe('prewarm with a body not yet in the scene', () => {
  it('compiles it against the scene, so it is lit by its lights', async () => {
    const { renderer, log } = fakeRenderer(Promise.resolve());
    const scene = new THREE.Scene();
    scene.name = 'map';
    const body = new THREE.Group();
    body.name = 'operator';
    await prewarm(renderer, body, new THREE.PerspectiveCamera(), null, scene);
    expect(log).toEqual(['compile operator lit by map into canvas']);
  });
});

describe('pacedUploads', () => {
  it('uploads in order and yields once a task has spent its budget', async () => {
    vi.useFakeTimers();
    try {
      let clock = 0;
      const uploaded: string[] = [];
      const renderer = {
        initTexture: (t: THREE.Texture) => {
          uploaded.push(t.name);
          clock += 3; // each upload "takes" 3 ms
        },
      };
      const upload = pacedUploads(renderer, 4, () => clock);
      const textures = ['a', 'b', 'c', 'd'].map((name) =>
        Object.assign(new THREE.Texture(), { name }),
      );
      const done = Promise.all(textures.map((t) => upload(t)));
      // Two uploads (6 ms) exceed the 4 ms budget, so the third waits for a
      // yield: without running any timer, only the microtasks, two are done.
      for (let i = 0; i < 20; i++) await Promise.resolve();
      expect(uploaded).toEqual(['a', 'b']);
      // One yield, then c and d fit in the fresh budget together.
      expect(vi.getTimerCount()).toBe(1);
      await vi.runAllTimersAsync();
      expect(uploaded).toEqual(['a', 'b', 'c', 'd']);
      await done;
    } finally {
      vi.useRealTimers();
    }
  });

  it('keeps going past an upload that throws', async () => {
    const seen: string[] = [];
    const upload = pacedUploads({
      initTexture: (t: THREE.Texture) => {
        seen.push(t.name);
        if (t.name === 'bad') throw new Error('context lost');
      },
    });
    const bad = Object.assign(new THREE.Texture(), { name: 'bad' });
    const good = Object.assign(new THREE.Texture(), { name: 'good' });
    await expect(Promise.all([upload(bad), upload(good)])).resolves.toBeDefined();
    expect(seen).toEqual(['bad', 'good']);
  });
});
