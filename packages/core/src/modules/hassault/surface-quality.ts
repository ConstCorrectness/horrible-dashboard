/**
 * The map's surfaces at the cost the tier can afford.
 *
 * The modelled maps load as `MeshStandardMaterial` — a full PBR evaluation per
 * pixel, plus the prop environment's image-based light. On an integrated GPU at
 * 1080p that, not geometry, is the frame: Dust II measured 64 fps standard and
 * 89 fps with the same surfaces as `MeshLambertMaterial` on an Intel UHD (the
 * world is ~3k triangles; it is all fill). The GLB surfaces use nothing Lambert
 * lacks — a colour and a detail map (`applyGlbSurface` in world3d.ts) — so on
 * the Low tier they swap to a Lambert twin, and back when the tier rises.
 *
 * Twins are cached per source material (maps share materials across hundreds of
 * meshes), and the original is remembered on the mesh, so the swap is exact in
 * both directions and never allocates twice.
 *
 * Takes the three namespace as an argument rather than importing it, so this file
 * never pulls three into the bundle — same contract as backdrop.ts.
 */
import type * as THREE from 'three';

const twins = new WeakMap<THREE.Material, THREE.Material>();
const ORIGINAL = 'hassaultPbrMaterial';

function lambertTwin(three: typeof THREE, m: THREE.MeshStandardMaterial): THREE.Material {
  let twin = twins.get(m);
  if (!twin) {
    twin = new three.MeshLambertMaterial({
      name: m.name,
      color: m.color,
      map: m.map,
      emissive: m.emissive,
      emissiveMap: m.emissiveMap,
      emissiveIntensity: m.emissiveIntensity,
      alphaMap: m.alphaMap,
      aoMap: m.aoMap,
      aoMapIntensity: m.aoMapIntensity,
      transparent: m.transparent,
      opacity: m.opacity,
      alphaTest: m.alphaTest,
      side: m.side,
      shadowSide: m.shadowSide,
      vertexColors: m.vertexColors,
      depthWrite: m.depthWrite,
      flatShading: m.flatShading,
    });
    twins.set(m, twin);
  }
  return twin;
}

/** Swap `root`'s standard materials for Lambert twins (`cheap`) or back. */
export function setSurfaceQuality(
  three: typeof THREE,
  root: THREE.Object3D | null,
  cheap: boolean,
): void {
  if (!root) return;
  root.traverse((child) => {
    const mesh = child as THREE.Mesh;
    if (!mesh.isMesh || (mesh as THREE.SkinnedMesh).isSkinnedMesh) return;
    const original = mesh.userData[ORIGINAL] as THREE.Material | THREE.Material[] | undefined;
    if (!cheap) {
      if (original) {
        mesh.material = original;
        delete mesh.userData[ORIGINAL];
      }
      return;
    }
    if (original) return; // already cheap
    const source = mesh.material;
    const list = Array.isArray(source) ? source : [source];
    if (!list.some((m) => (m as THREE.MeshStandardMaterial).isMeshStandardMaterial)) return;
    const swapped = list.map((m) =>
      (m as THREE.MeshStandardMaterial).isMeshStandardMaterial
        ? lambertTwin(three, m as THREE.MeshStandardMaterial)
        : m,
    );
    mesh.userData[ORIGINAL] = source;
    mesh.material = Array.isArray(source) ? swapped : swapped[0]!;
  });
}
