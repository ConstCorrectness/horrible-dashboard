/**
 * 3D Visual Entity Markers for HorribleAssault Studio
 *
 * Provides visual 3D representations for abstract scene entities:
 * - CLA Team Spawns: Red tactical ring + player bounding cylinder + forward orientation arrow
 * - RVS Team Spawns: Blue tactical ring + player bounding cylinder + forward orientation arrow
 * - Weapon Pickups: Rotating pedestal disc + hovering beacon ring + ammo box/pickup geometry
 * - Omni/Point Lights: Radiant spherical beacon with outward rays
 */

import * as THREE from 'three';
import { SceneNode } from './sceneTypes';

export function createEntityMarker(node: SceneNode): THREE.Group {
  const group = new THREE.Group();
  group.name = `marker_${node.id}`;
  group.userData = { nodeId: node.id, isEntityMarker: true };

  switch (node.type) {
    case 'spawn_point': {
      const isCLA = node.properties?.team === 'CLA' || node.name.toUpperCase().includes('CLA');
      const teamColor = isCLA ? 0xef4444 : 0x3b82f6; // Red for CLA, Blue for RVS
      const accentColor = isCLA ? 0xf87171 : 0x60a5fa;

      // 1. Base tactical spawn pad ring
      const ringGeo = new THREE.RingGeometry(0.6, 0.75, 32);
      ringGeo.rotateX(-Math.PI / 2);
      const ringMat = new THREE.MeshBasicMaterial({
        color: teamColor,
        side: THREE.DoubleSide,
        transparent: true,
        opacity: 0.85,
      });
      const ringMesh = new THREE.Mesh(ringGeo, ringMat);
      ringMesh.position.y = 0.02;
      group.add(ringMesh);

      // Inner disc
      const innerGeo = new THREE.CircleGeometry(0.55, 32);
      innerGeo.rotateX(-Math.PI / 2);
      const innerMat = new THREE.MeshBasicMaterial({
        color: teamColor,
        side: THREE.DoubleSide,
        transparent: true,
        opacity: 0.25,
      });
      const innerMesh = new THREE.Mesh(innerGeo, innerMat);
      innerMesh.position.y = 0.01;
      group.add(innerMesh);

      // 2. Player bounding cylinder (0.8m diameter, 1.8m height)
      const cylGeo = new THREE.CylinderGeometry(0.35, 0.35, 1.8, 16);
      const cylMat = new THREE.MeshBasicMaterial({
        color: accentColor,
        wireframe: true,
        transparent: true,
        opacity: 0.4,
      });
      const cylMesh = new THREE.Mesh(cylGeo, cylMat);
      cylMesh.position.y = 0.9;
      group.add(cylMesh);

      // 3. Direction / Forward orientation arrow on floor (points along -Z in FPS space)
      const arrowShape = new THREE.Shape();
      arrowShape.moveTo(0, -0.6);
      arrowShape.lineTo(0.2, -0.3);
      arrowShape.lineTo(0.08, -0.3);
      arrowShape.lineTo(0.08, 0.1);
      arrowShape.lineTo(-0.08, 0.1);
      arrowShape.lineTo(-0.08, -0.3);
      arrowShape.lineTo(-0.2, -0.3);
      arrowShape.closePath();

      const arrowGeo = new THREE.ShapeGeometry(arrowShape);
      arrowGeo.rotateX(-Math.PI / 2);
      const arrowMat = new THREE.MeshBasicMaterial({
        color: 0xffffff,
        side: THREE.DoubleSide,
      });
      const arrowMesh = new THREE.Mesh(arrowGeo, arrowMat);
      arrowMesh.position.y = 0.03;
      group.add(arrowMesh);

      // 4. Floating holographic team diamond
      const diamondGeo = new THREE.OctahedronGeometry(0.18, 0);
      const diamondMat = new THREE.MeshStandardMaterial({
        color: teamColor,
        emissive: teamColor,
        emissiveIntensity: 0.8,
        roughness: 0.2,
      });
      const diamondMesh = new THREE.Mesh(diamondGeo, diamondMat);
      diamondMesh.position.y = 2.1;
      diamondMesh.name = 'floating_diamond';
      group.add(diamondMesh);
      break;
    }

    case 'weapon_pickup': {
      // 1. Glowing floor pedestal ring (Amber / Golden)
      const ringGeo = new THREE.RingGeometry(0.45, 0.55, 32);
      ringGeo.rotateX(-Math.PI / 2);
      const ringMat = new THREE.MeshBasicMaterial({
        color: 0xf59e0b,
        side: THREE.DoubleSide,
        transparent: true,
        opacity: 0.8,
      });
      const ringMesh = new THREE.Mesh(ringGeo, ringMat);
      ringMesh.position.y = 0.02;
      group.add(ringMesh);

      // 2. Floating ammo / pickup supply box
      const boxGeo = new THREE.BoxGeometry(0.35, 0.2, 0.25);
      const boxMat = new THREE.MeshStandardMaterial({
        color: 0xd97706,
        roughness: 0.4,
        metalness: 0.6,
      });
      const boxMesh = new THREE.Mesh(boxGeo, boxMat);
      boxMesh.position.y = 0.4;
      boxMesh.name = 'floating_box';
      group.add(boxMesh);

      // Rotating glowing cross / beacon above box
      const beaconGeo = new THREE.TorusGeometry(0.22, 0.03, 8, 24);
      const beaconMat = new THREE.MeshBasicMaterial({
        color: 0xfbbf24,
      });
      const beaconMesh = new THREE.Mesh(beaconGeo, beaconMat);
      beaconMesh.position.y = 0.7;
      beaconMesh.name = 'beacon_ring';
      group.add(beaconMesh);
      break;
    }

    case 'light': {
      // Radiant yellow light diamond
      const lightGeo = new THREE.SphereGeometry(0.2, 8, 8);
      const lightMat = new THREE.MeshBasicMaterial({
        color: 0xfde047,
        wireframe: true,
      });
      const lightMesh = new THREE.Mesh(lightGeo, lightMat);
      group.add(lightMesh);
      break;
    }

    case 'collision_box': {
      // Translucent green collision box
      const boxGeo = new THREE.BoxGeometry(
        node.transform.scale?.[0] ?? 1,
        node.transform.scale?.[1] ?? 1,
        node.transform.scale?.[2] ?? 1
      );
      const boxMat = new THREE.MeshBasicMaterial({
        color: 0x22c55e,
        wireframe: true,
        transparent: true,
        opacity: 0.75,
      });
      const boxMesh = new THREE.Mesh(boxGeo, boxMat);
      group.add(boxMesh);
      break;
    }

    default:
      break;
  }

  // Set initial transform
  group.position.set(
    node.transform.position[0],
    node.transform.position[1],
    node.transform.position[2]
  );
  group.rotation.set(
    (node.transform.rotation[0] * Math.PI) / 180,
    (node.transform.rotation[1] * Math.PI) / 180,
    (node.transform.rotation[2] * Math.PI) / 180
  );
  group.scale.set(
    node.transform.scale[0],
    node.transform.scale[1],
    node.transform.scale[2]
  );

  return group;
}
