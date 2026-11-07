/**
 * Where the sun's shadow camera goes: around a sphere that holds everything the
 * map draws — native `shadow.rs` `fit`, the same rule in both clients.
 *
 * The shadow map is rendered **once per map** in both (the world and the sun
 * are static), so it never moves under the camera and there is nothing to
 * shimmer: texel snapping, the usual cure for a shadow that crawls as the view
 * turns, has nothing to do here. What decides how sharp it is is how many
 * texels each unit of map gets, and that is the fit.
 *
 * It used to be fitted to `MAP_CONFIGS`' hand-written `extent`, a number from
 * the playable area, at ±1.1 of it. Its texels were 1.1–1.7× native's on eight
 * of the ten maps (Aim, Assault and Bank worst), and on Junk Flea the frustum
 * was narrower than the geometry, so the map's edge had no shadows. Fitted to
 * what is actually drawn, the two clients have the same texel size everywhere.
 *
 * A sphere rather than the box as the sun sees it: on these maps the sun comes
 * in near a diagonal, where the longer side of that box is within 1–8% of the
 * sphere's diameter anyway, and the sphere does not depend on the sun.
 */

export interface ShadowSphere {
  center: [number, number, number];
  radius: number;
}

/** The sphere around an axis-aligned box: native `fit`'s `center` and `radius`. */
export function shadowSphere(
  min: readonly [number, number, number],
  max: readonly [number, number, number],
): ShadowSphere {
  const dx = max[0] - min[0];
  const dy = max[1] - min[1];
  const dz = max[2] - min[2];
  return {
    center: [(min[0] + max[0]) / 2, (min[1] + max[1]) / 2, (min[2] + max[2]) / 2],
    // Never under 1, as native: an empty map still gets a usable matrix.
    radius: Math.max(1, Math.sqrt(dx * dx + dy * dy + dz * dz) / 2),
  };
}

/**
 * The box around every mesh under `root` that is drawn: invisible nodes
 * (collision-only, clip brushes) are skipped, as native builds its bounds from
 * the render vertices only. `root` itself may be hidden (a map is, until it
 * has compiled); only its descendants' own flags count. World matrices must be
 * current.
 */
export function drawnBounds(
  THREE: typeof import('three'),
  root: import('three').Object3D,
): { min: [number, number, number]; max: [number, number, number] } | null {
  const box = new THREE.Box3();
  const part = new THREE.Box3();
  const visit = (obj: import('three').Object3D) => {
    if (obj !== root && !obj.visible) return;
    const mesh = obj as import('three').Mesh;
    if (mesh.isMesh && mesh.geometry) {
      if (!mesh.geometry.boundingBox) mesh.geometry.computeBoundingBox();
      if (mesh.geometry.boundingBox) {
        part.copy(mesh.geometry.boundingBox).applyMatrix4(mesh.matrixWorld);
        box.union(part);
      }
    }
    for (const child of obj.children) visit(child);
  };
  visit(root);
  if (box.isEmpty()) return null;
  return { min: [box.min.x, box.min.y, box.min.z], max: [box.max.x, box.max.y, box.max.z] };
}
