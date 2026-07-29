/**
 * The void the map stands in: a procedural ground grid and a field of drifting
 * motes, both custom shaders.
 *
 * This is scenery for the boot sequence, not part of the world — the map has no
 * skybox and no ground beyond its own floor, so without something underneath it a
 * half-assembled level hangs in flat black and reads as broken rather than as
 * loading. The grid gives the build a floor to rise out of and the motes give the
 * air depth, which is what makes the camera orbit behind the sign-in screen look
 * deliberate.
 *
 * Both materials set `fog: false` and carry their own distance falloff instead.
 * The scene's fog (60→320) would otherwise eat them, and a raw `ShaderMaterial`
 * doesn't participate in three's fog without pulling in the fog chunks anyway —
 * so the vignette in the grid shader is doing that job, and it also hides the
 * plane's edge without needing the plane to be enormous.
 *
 * Takes the three namespace as an argument rather than importing it, so this file
 * never pulls three into the bundle — same contract as avatars.ts / effects.ts.
 */
import type * as THREE from 'three';

/** How far past the map the grid extends, as a multiple of the map's size. */
const GRID_SPREAD = 3;
const MOTE_COUNT = 900;

export interface Backdrop {
  /** Advance the animation. `t` is seconds since the backdrop was created. */
  update(t: number): void;
  /** 0 hides it entirely; the game fades it out once you deploy. */
  setOpacity(value: number): void;
  /** Re-centre and re-scale on the map currently loaded. */
  fit(center: [number, number], size: number): void;
  dispose(): void;
}

export function createBackdrop(three: typeof THREE, scene: THREE.Scene): Backdrop {
  const opacity = { value: 1 };

  // ---- the ground grid ------------------------------------------------------
  //
  // Lines are drawn procedurally from the fragment's own position rather than
  // from geometry: one quad, no line primitives, and the lines stay one pixel
  // crisp at any distance because `smoothstep` antialiases them.
  const gridUniforms = {
    uOpacity: opacity,
    uCell: { value: 4 },
    uFade: { value: 240 },
  };
  const gridMaterial = new three.ShaderMaterial({
    uniforms: gridUniforms,
    transparent: true,
    depthWrite: false,
    fog: false,
    vertexShader: `
      varying vec2 vP;
      void main() {
        vP = position.xy;
        gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
      }
    `,
    fragmentShader: `
      uniform float uOpacity;
      uniform float uCell;
      uniform float uFade;
      varying vec2 vP;

      /**
       * One grid line, one pixel wide, at any viewing angle.
       *
       * Dividing the distance-to-line by fwidth (how much the coordinate changes
       * between neighbouring pixels) is what makes this work on a *ground* plane.
       * A fixed-width smoothstep is fine face-on but collapses at the grazing
       * angles a low camera produces: cells compress below one pixel near the
       * horizon and alias into a flat grey wash. Scaling by the derivative keeps
       * the line one pixel wide in screen space and lets it fade out honestly
       * once the cells are too small to resolve.
       */
      float gridLine(vec2 p, float cell) {
        vec2 coord = p / cell;
        vec2 d = abs(fract(coord - 0.5) - 0.5) / max(fwidth(coord), vec2(1e-5));
        return 1.0 - min(min(d.x, d.y), 1.0);
      }

      void main() {
        float line = gridLine(vP, uCell);
        // Every eighth line brighter, so the grid reads as measured rather than
        // as noise. Ten was too sparse at this plane size — a handful of huge
        // diagonals and nothing between them.
        float major = gridLine(vP, uCell * 8.0);

        float r = length(vP);
        // Its own falloff: the scene fog is disabled for this material, and this
        // is also what hides the quad's edge.
        float vig = 1.0 - smoothstep(uFade * 0.25, uFade, r);
        // Kept faint. A bright centre reads as haze and swallows the lines it is
        // supposed to sit under — the grid is the subject here, not the glow.
        float glow = exp(-r * r / (uFade * uFade * 0.05)) * 0.16;

        vec3 col = vec3(0.40, 0.52, 0.78) * line * 0.75
                 + vec3(0.58, 0.74, 1.00) * major * 1.0
                 + vec3(0.09, 0.12, 0.21) * glow;
        float alpha = (line * 0.55 + major * 0.75 + glow * 0.22) * vig * uOpacity;
        if (alpha < 0.002) discard;
        gl_FragColor = vec4(col, alpha);
      }
    `,
  });
  const grid = new three.Mesh(new three.PlaneGeometry(1, 1), gridMaterial);
  grid.rotation.x = -Math.PI / 2;
  grid.renderOrder = -1;
  scene.add(grid);

  // ---- drifting motes -------------------------------------------------------
  //
  // A per-point seed drives position drift, twinkle phase and size, so 900 points
  // animate entirely on the GPU from one static buffer — nothing is rewritten per
  // frame.
  const positions = new Float32Array(MOTE_COUNT * 3);
  const seeds = new Float32Array(MOTE_COUNT);
  for (let i = 0; i < MOTE_COUNT; i += 1) {
    positions[i * 3] = Math.random();
    positions[i * 3 + 1] = Math.random();
    positions[i * 3 + 2] = Math.random();
    seeds[i] = Math.random();
  }
  const moteGeometry = new three.BufferGeometry();
  moteGeometry.setAttribute('position', new three.BufferAttribute(positions, 3));
  moteGeometry.setAttribute('aSeed', new three.BufferAttribute(seeds, 1));
  // The positions above are unit-cube fractions; `fit` maps them onto the map via
  // uniforms, so switching map never rebuilds the buffer.
  moteGeometry.boundingSphere = new three.Sphere(new three.Vector3(), 1e6);

  const moteUniforms = {
    uTime: { value: 0 },
    uOpacity: opacity,
    uDpr: { value: Math.min(window.devicePixelRatio, 2) },
    uOrigin: { value: new three.Vector3() },
    uSpan: { value: new three.Vector3(1, 1, 1) },
  };
  const moteMaterial = new three.ShaderMaterial({
    uniforms: moteUniforms,
    transparent: true,
    depthWrite: false,
    fog: false,
    blending: three.AdditiveBlending,
    vertexShader: `
      attribute float aSeed;
      uniform float uTime;
      uniform float uDpr;
      uniform vec3 uOrigin;
      uniform vec3 uSpan;
      varying float vA;
      void main() {
        // The stored position is a 0..1 fraction of the map's bounds.
        vec3 p = uOrigin + position * uSpan;
        p.x += sin(uTime * 0.12 + aSeed * 7.0) * 1.6;
        p.y += cos(uTime * 0.10 + aSeed * 13.0) * 1.1;
        p.z += sin(uTime * 0.09 + aSeed * 19.0) * 1.6;
        vec4 mv = modelViewMatrix * vec4(p, 1.0);
        gl_PointSize = (1.0 + aSeed * 2.0) * (60.0 / -mv.z) * uDpr;
        vA = 0.45 + 0.55 * sin(uTime * (0.4 + aSeed * 0.7) + aSeed * 20.0);
        gl_Position = projectionMatrix * mv;
      }
    `,
    fragmentShader: `
      uniform float uOpacity;
      varying float vA;
      void main() {
        // Round, soft-edged sprite from the point's own coordinate — no texture.
        float d = length(gl_PointCoord - 0.5);
        float a = smoothstep(0.5, 0.05, d) * vA * 0.30 * uOpacity;
        if (a < 0.002) discard;
        gl_FragColor = vec4(0.72, 0.80, 0.98, a);
      }
    `,
  });
  const motes = new three.Points(moteGeometry, moteMaterial);
  motes.frustumCulled = false;
  scene.add(motes);

  return {
    update(t: number) {
      moteUniforms.uTime.value = t;
    },
    setOpacity(value: number) {
      opacity.value = value;
      grid.visible = value > 0.001;
      motes.visible = value > 0.001;
    },
    fit(center: [number, number], size: number) {
      const spread = size * GRID_SPREAD;
      grid.scale.set(spread, spread, 1);
      grid.position.set(center[0], -0.05, center[1]);
      gridUniforms.uFade.value = spread * 0.45;
      // Scaled to the map rather than fixed, so a 64-cube and a 256-cube map
      // show a comparable number of cells instead of one reading as graph paper.
      gridUniforms.uCell.value = Math.max(2, size / 32);
      // Motes fill a slab over the map, tall enough to read as air rather than a
      // layer, and wider than the map so the orbit never flies out of them.
      moteUniforms.uOrigin.value.set(center[0] - size, 0, center[1] - size);
      moteUniforms.uSpan.value.set(size * 2, size * 0.5, size * 2);
    },
    dispose() {
      scene.remove(grid);
      scene.remove(motes);
      grid.geometry.dispose();
      gridMaterial.dispose();
      moteGeometry.dispose();
      moteMaterial.dispose();
    },
  };
}
