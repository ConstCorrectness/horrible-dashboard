/**
 * 3D Scene and Map Document data structures for HorribleAssault Studio.
 *
 * Provides a universal, lightweight scene graph for level design, prop
 * placement, spawn mapping, lighting, and general 3D scene editing.
 */

export type SceneNodeType =
  | 'mesh_prop'
  | 'spawn_point'
  | 'weapon_pickup'
  | 'light'
  | 'ladder'
  | 'collision_box';

export interface SceneNode {
  id: string;
  name: string;
  type: SceneNodeType;
  transform: {
    position: [number, number, number];
    rotation: [number, number, number]; // Euler angles in degrees
    scale: [number, number, number];
  };
  assetPath?: string;
  assetFormat?: string;
  properties?: {
    team?: 'CLA' | 'RVS' | 'FFA';
    weaponId?: string;
    lightColor?: string;
    intensity?: number;
    ladderHeight?: number;
    color?: string;
    dimensions?: [number, number, number];
  };
  visible: boolean;
  locked: boolean;
}

export interface StudioScene {
  version: 1;
  name: string;
  created_at: string;
  environment: {
    ambientColor: string;
    ambientIntensity: number;
    sunColor: string;
    sunIntensity: number;
    sunPosition: [number, number, number];
    fogColor?: string;
    fogDensity?: number;
  };
  nodes: SceneNode[];
}

/** Generate a default ready-to-edit starter arena scene. */
export function createDefaultScene(name = 'New Arena'): StudioScene {
  return {
    version: 1,
    name,
    created_at: new Date().toISOString(),
    environment: {
      ambientColor: 'rgb(51, 57, 64)',
      ambientIntensity: 0.8,
      sunColor: 'rgb(255, 255, 255)',
      sunIntensity: 1.5,
      sunPosition: [10, 20, 15],
    },
    nodes: [
      {
        id: 'sun_light',
        name: 'Sun Directional Light',
        type: 'light',
        transform: {
          position: [10, 20, 15],
          rotation: [-45, 30, 0],
          scale: [1, 1, 1],
        },
        properties: {
          lightColor: 'rgb(255, 255, 255)',
          intensity: 1.5,
        },
        visible: true,
        locked: false,
      },
      {
        id: 'spawn_cla_01',
        name: 'CLA Team Spawn 1',
        type: 'spawn_point',
        transform: {
          position: [-8, 0, 0],
          rotation: [0, 90, 0],
          scale: [1, 1, 1],
        },
        properties: {
          team: 'CLA',
          color: 'rgb(239, 68, 68)',
        },
        visible: true,
        locked: false,
      },
      {
        id: 'spawn_rvs_01',
        name: 'RVS Team Spawn 1',
        type: 'spawn_point',
        transform: {
          position: [8, 0, 0],
          rotation: [0, -90, 0],
          scale: [1, 1, 1],
        },
        properties: {
          team: 'RVS',
          color: 'rgb(59, 130, 246)',
        },
        visible: true,
        locked: false,
      },
      {
        id: 'pickup_fal_01',
        name: 'FN FAL Weapon Pickup',
        type: 'weapon_pickup',
        transform: {
          position: [0, 0.4, 0],
          rotation: [0, 0, 0],
          scale: [1, 1, 1],
        },
        assetPath: 'apps/web/public/hassault-weapon-fal.glb',
        assetFormat: 'glb',
        properties: {
          weaponId: 'fal',
        },
        visible: true,
        locked: false,
      },
    ],
  };
}

export function serializeScene(scene: StudioScene): string {
  return JSON.stringify(scene, null, 2);
}

export function deserializeScene(raw: string): StudioScene | null {
  try {
    const data = JSON.parse(raw);
    if (!data.nodes || !Array.isArray(data.nodes)) return null;
    return data as StudioScene;
  } catch {
    return null;
  }
}
