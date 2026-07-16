import React, { useCallback, useEffect, useRef } from 'react';
import * as THREE from 'three';

import type { SocialBubble, SocialOccupant } from '../game-ws';

interface Rendered {
  x: number;
  y: number;
  history: { x: number; y: number }[];
  running: boolean;
  group: THREE.Group;
  billboardTexture: THREE.CanvasTexture;
  accessorySprite?: THREE.Sprite;
}

interface Particle {
  mesh: THREE.Mesh;
  vx: number;
  vy: number;
  vz: number;
  alpha: number;
}

export function PlazaCanvas({
  occupants,
  bubbles,
  accountId,
  onMove,
  onSelectPlayer,
  playerAccessories = {},
  speakingPlayers = {},
}: {
  occupants: SocialOccupant[];
  bubbles: SocialBubble[];
  accountId: string | null;
  roomName: string;
  onMove: (x: number, y: number) => void;
  onSelectPlayer?: (occupant: SocialOccupant) => void;
  playerAccessories?: Record<string, string>;
  speakingPlayers?: Record<string, boolean>;
}) {
  const mountRef = useRef<HTMLDivElement | null>(null);

  // Live refs so the Three.js loop always reads the latest props
  const occRef = useRef<SocialOccupant[]>(occupants);
  const bubRef = useRef<SocialBubble[]>(bubbles);
  const meRef = useRef<string | null>(accountId);
  const accRef = useRef<Record<string, string>>(playerAccessories);
  const speakRef = useRef<Record<string, boolean>>(speakingPlayers);

  occRef.current = occupants;
  bubRef.current = bubbles;
  meRef.current = accountId;
  accRef.current = playerAccessories;
  speakRef.current = speakingPlayers;

  // Cache for custom Base64/url images
  const imageCacheRef = useRef<Map<string, HTMLImageElement>>(new Map());

  // Roster rendering history & run state
  const renderedRef = useRef<Map<string, Rendered>>(new Map());

  useEffect(() => {
    const container = mountRef.current;
    if (!container) return;

    let disposed = false;
    const imageCache = imageCacheRef.current;

    // ── Setup Three.js Scene ──
    const scene = new THREE.Scene();
    scene.background = null; // transparent to inherit raised panel style

    const width = container.clientWidth || 480;
    const height = container.clientHeight || 360;

    const camera = new THREE.PerspectiveCamera(42, width / height, 0.1, 100);
    // Isometric-like high perspective camera
    camera.position.set(0, 11, 13);
    camera.lookAt(0, 0.5, 0);

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setSize(width, height);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    container.appendChild(renderer.domElement);

    // ── Grid Floor ──
    const floorSize = 24;
    const floorGeo = new THREE.PlaneGeometry(floorSize, floorSize);
    const floorMat = new THREE.MeshStandardMaterial({
      color: 0x14161a,
      roughness: 0.9,
      metalness: 0.1,
    });
    const floor = new THREE.Mesh(floorGeo, floorMat);
    floor.rotation.x = -Math.PI / 2;
    scene.add(floor);

    // Grid helper overlay
    const gridHelper = new THREE.GridHelper(floorSize, 24, 0x6ea8fe, 0x23262d);
    gridHelper.position.y = 0.01; // slightly above floor to prevent z-fighting
    scene.add(gridHelper);

    // ── Lighting ──
    const ambientLight = new THREE.AmbientLight(0xffffff, 0.85);
    scene.add(ambientLight);

    const dirLight = new THREE.DirectionalLight(0xffffff, 0.7);
    dirLight.position.set(5, 12, 5);
    scene.add(dirLight);

    // Spotlight following the local player
    const playerSpotlight = new THREE.SpotLight(0x6ea8fe, 3, 12, Math.PI / 6, 0.5, 1);
    playerSpotlight.position.set(0, 8, 0);
    scene.add(playerSpotlight);
    scene.add(playerSpotlight.target);

    // ── Utility Canvas Draw Helpers ──
    const drawAvatarTexture = (avatarStr: string, name: string, isSpeaking: boolean) => {
      const canvas = document.createElement('canvas');
      canvas.width = 256;
      canvas.height = 256;
      const ctx = canvas.getContext('2d')!;

      ctx.clearRect(0, 0, 256, 256);

      // Draw speaking highlight border if they are active on mic
      if (isSpeaking) {
        ctx.strokeStyle = '#2ed573';
        ctx.lineWidth = 10;
        ctx.beginPath();
        ctx.arc(128, 100, 70, 0, Math.PI * 2);
        ctx.stroke();
      }

      // Draw avatar image or emoji
      if (
        avatarStr.startsWith('data:image/') ||
        avatarStr.startsWith('http://') ||
        avatarStr.startsWith('https://')
      ) {
        let img = imageCache.get(avatarStr);
        if (!img) {
          img = new Image();
          img.src = avatarStr;
          imageCache.set(avatarStr, img);
          img.onload = () => {
            texture.needsUpdate = true;
          };
        }
        if (img.complete) {
          ctx.save();
          ctx.beginPath();
          ctx.arc(128, 100, 60, 0, Math.PI * 2);
          ctx.clip();
          ctx.drawImage(img, 68, 40, 120, 120);
          ctx.restore();
        } else {
          ctx.fillStyle = '#6ea8fe';
          ctx.beginPath();
          ctx.arc(128, 100, 60, 0, Math.PI * 2);
          ctx.fill();
        }
      } else {
        ctx.font = '95px Arial';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(avatarStr, 128, 100);
      }

      // Draw Nameplate Background
      ctx.font = 'bold 22px system-ui';
      const tw = ctx.measureText(name).width;
      ctx.fillStyle = 'rgba(20, 22, 26, 0.85)';
      ctx.beginPath();
      // Draw rounded rectangle
      const rx = 128 - tw / 2 - 12;
      const ry = 185;
      const rw = tw + 24;
      const rh = 34;
      const rad = 17;
      ctx.moveTo(rx + rad, ry);
      ctx.arcTo(rx + rw, ry, rx + rw, ry + rh, rad);
      ctx.arcTo(rx + rw, ry + rh, rx, ry + rh, rad);
      ctx.arcTo(rx, ry + rh, rx, ry, rad);
      ctx.arcTo(rx, ry, rx + rw, ry, rad);
      ctx.closePath();
      ctx.fill();

      // Draw Name text
      ctx.fillStyle = '#ffffff';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(name, 128, 202);

      const texture = new THREE.CanvasTexture(canvas);
      return { texture, canvas };
    };

    const getAccessoryEmoji = (accName: string): string => {
      switch (accName) {
        case 'crown':
          return '👑';
        case 'wizard':
          return '🧙';
        case 'goggles':
          return '🕶️';
        case 'halo':
          return '👼';
        default:
          return '';
      }
    };

    const drawAccessoryTexture = (accName: string) => {
      const canvas = document.createElement('canvas');
      canvas.width = 128;
      canvas.height = 128;
      const ctx = canvas.getContext('2d')!;
      ctx.clearRect(0, 0, 128, 128);

      const emoji = getAccessoryEmoji(accName);
      if (emoji) {
        ctx.font = '72px Arial';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(emoji, 64, 64);
      }

      return new THREE.CanvasTexture(canvas);
    };

    // ── Occupant 3D Cache ──
    const rendered = renderedRef.current;

    // Clean up past runs
    for (const key of rendered.keys()) {
      const cur = rendered.get(key)!;
      scene.remove(cur.group);
      cur.billboardTexture.dispose();
      if (cur.accessorySprite) {
        cur.accessorySprite.material.map?.dispose();
        cur.accessorySprite.material.dispose();
      }
    }
    rendered.clear();

    // ── Particles System ──
    const particles: Particle[] = [];
    const particleGeo = new THREE.SphereGeometry(0.08, 8, 8);

    // ── Animation Frame loop ──
    const animate = () => {
      if (disposed) return;
      requestAnimationFrame(animate);

      const present = new Set(occRef.current.map((o) => o.account_id));

      // Clean up gone players
      for (const id of [...rendered.keys()]) {
        if (!present.has(id)) {
          const cur = rendered.get(id)!;
          scene.remove(cur.group);
          cur.billboardTexture.dispose();
          if (cur.accessorySprite) {
            cur.accessorySprite.material.map?.dispose();
            cur.accessorySprite.material.dispose();
          }
          rendered.delete(id);
        }
      }

      // Position sync & lerping
      let meGroup: THREE.Group | null = null;

      for (const o of occRef.current) {
        let cur = rendered.get(o.account_id);
        const accessory = accRef.current[o.account_id] || '';
        const isSpeaking = speakRef.current[o.account_id] || false;

        if (!cur) {
          // Initialize new 3D player representation
          const group = new THREE.Group();

          // Capsule body mesh
          const capsuleGeo = new THREE.CylinderGeometry(0.4, 0.4, 1.4, 16);
          const capsuleMat = new THREE.MeshStandardMaterial({
            color: o.account_id === meRef.current ? 0x2e7d32 : 0x2a2d36,
            roughness: 0.6,
            metalness: 0.15,
          });
          const body = new THREE.Mesh(capsuleGeo, capsuleMat);
          body.position.y = 0.7; // rest on floor
          group.add(body);

          // Billboard sprite for emoji/image + nameplate
          const { texture: billboardTexture } = drawAvatarTexture(o.avatar, o.name, isSpeaking);
          const spriteMat = new THREE.SpriteMaterial({ map: billboardTexture });
          const sprite = new THREE.Sprite(spriteMat);
          sprite.position.set(0, 1.9, 0);
          sprite.scale.set(2.2, 2.2, 1);
          group.add(sprite);

          scene.add(group);

          cur = {
            x: o.x,
            y: o.y,
            history: [],
            running: false,
            group,
            billboardTexture,
          };
          rendered.set(o.account_id, cur);
        }

        // Detect running state
        const dx = o.x - cur.x;
        const dy = o.y - cur.y;
        const dist = Math.sqrt(dx * dx + dy * dy);

        if (dist > 15) {
          cur.running = true;
        } else if (dist < 1.5) {
          cur.running = false;
        }

        const lerpFactor = cur.running ? 0.36 : 0.18;
        cur.x += dx * lerpFactor;
        cur.y += dy * lerpFactor;

        cur.history.unshift({ x: cur.x, y: cur.y });
        if (cur.history.length > 10) cur.history.pop();

        // Map world coords (0..100) to 3D space (-12..12)
        const targetX = (cur.x / 100) * floorSize - floorSize / 2;
        const targetZ = (cur.y / 100) * floorSize - floorSize / 2;
        cur.group.position.set(targetX, 0, targetZ);

        // Update/create accessory sprite if equipped
        if (accessory) {
          if (!cur.accessorySprite) {
            const accTex = drawAccessoryTexture(accessory);
            const accMat = new THREE.SpriteMaterial({ map: accTex });
            const accSprite = new THREE.Sprite(accMat);
            accSprite.position.set(0, 2.7, 0); // floats above avatar head
            accSprite.scale.set(1.0, 1.0, 1);
            cur.group.add(accSprite);
            cur.accessorySprite = accSprite;
          }
        } else if (cur.accessorySprite) {
          cur.group.remove(cur.accessorySprite);
          cur.accessorySprite.material.map?.dispose();
          cur.accessorySprite.material.dispose();
          cur.accessorySprite = undefined;
        }

        // Animate floating accessory Bobbing
        if (cur.accessorySprite) {
          cur.accessorySprite.position.y = 2.7 + Math.sin(Date.now() * 0.005) * 0.08;
        }

        // Animate pulsing speaking border
        if (isSpeaking) {
          // Bob the billboard scale slightly to represent sound
          const pulse = 1 + Math.sin(Date.now() * 0.015) * 0.08;
          cur.group.children[1].scale.set(2.2 * pulse, 2.2 * pulse, 1);
        } else {
          cur.group.children[1].scale.set(2.2, 2.2, 1);
        }

        // Track local player group to align spotlight
        if (o.account_id === meRef.current) {
          meGroup = cur.group;
        }

        // Spawn dust particles
        if (cur.running && Math.random() < 0.35) {
          const particleMat = new THREE.MeshBasicMaterial({
            color: 0xcccccc,
            transparent: true,
            opacity: 0.5,
          });
          const pMesh = new THREE.Mesh(particleGeo, particleMat);
          // Place at the base of player cylinder
          pMesh.position.set(
            targetX + (Math.random() - 0.5) * 0.4,
            0.1,
            targetZ + (Math.random() - 0.5) * 0.4,
          );
          scene.add(pMesh);

          particles.push({
            mesh: pMesh,
            vx: (Math.random() - 0.5) * 0.04,
            vy: Math.random() * 0.03 + 0.02,
            vz: (Math.random() - 0.5) * 0.04,
            alpha: 0.5,
          });
        }
      }

      // Update spotlight position
      if (meGroup) {
        playerSpotlight.position.set(meGroup.position.x, 8, meGroup.position.z + 4);
        playerSpotlight.target.position.copy(meGroup.position);

        // Smooth camera follow
        const camTargetX = meGroup.position.x;
        const camTargetZ = meGroup.position.z + 13;
        camera.position.x += (camTargetX - camera.position.x) * 0.05;
        camera.position.z += (camTargetZ - camera.position.z) * 0.05;
        camera.lookAt(meGroup.position.x, 0.5, meGroup.position.z);
      }

      // Update particles
      for (let i = particles.length - 1; i >= 0; i--) {
        const p = particles[i];
        p.mesh.position.x += p.vx;
        p.mesh.position.y += p.vy;
        p.mesh.position.z += p.vz;
        p.alpha -= 0.015;
        if (p.alpha <= 0) {
          scene.remove(p.mesh);
          p.mesh.geometry.dispose();
          if (Array.isArray(p.mesh.material)) {
            p.mesh.material.forEach((m) => m.dispose());
          } else {
            p.mesh.material.dispose();
          }
          particles.splice(i, 1);
        } else {
          (p.mesh.material as THREE.Material).opacity = p.alpha;
        }
      }

      renderer.render(scene, camera);
    };
    animate();

    // Resize listener
    const handleResize = () => {
      const w = container.clientWidth || 480;
      const h = container.clientHeight || 360;
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      renderer.setSize(w, h);
    };
    window.addEventListener('resize', handleResize);

    // Clean up
    return () => {
      disposed = true;
      window.removeEventListener('resize', handleResize);
      if (container.contains(renderer.domElement)) {
        container.removeChild(renderer.domElement);
      }

      // Resource disposal
      floorGeo.dispose();
      floorMat.dispose();
      gridHelper.dispose();
      particleGeo.dispose();
      renderer.dispose();
    };
  }, []);

  // Raycaster click handling
  const handleClick = useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      const container = mountRef.current;
      if (!container) return;

      const rect = container.getBoundingClientRect();
      const width = container.clientWidth || 480;
      const height = container.clientHeight || 360;

      // Mouse normalized coordinates
      const mx = ((e.clientX - rect.left) / width) * 2 - 1;
      const my = -((e.clientY - rect.top) / height) * 2 + 1;

      const raycaster = new THREE.Raycaster();
      const mouse = new THREE.Vector2(mx, my);

      // Recreate camera for calculation
      const tempCamera = new THREE.PerspectiveCamera(42, width / height, 0.1, 100);
      const localMe = occRef.current.find((o) => o.account_id === meRef.current);

      if (localMe) {
        const curMe = renderedRef.current.get(localMe.account_id);
        if (curMe) {
          const targetX = (curMe.x / 100) * 24 - 12;
          const targetZ = (curMe.y / 100) * 24 - 12;
          tempCamera.position.set(targetX, 11, targetZ + 13);
          tempCamera.lookAt(targetX, 0.5, targetZ);
        } else {
          tempCamera.position.set(0, 11, 13);
          tempCamera.lookAt(0, 0.5, 0);
        }
      } else {
        tempCamera.position.set(0, 11, 13);
        tempCamera.lookAt(0, 0.5, 0);
      }
      tempCamera.updateMatrixWorld();

      raycaster.setFromCamera(mouse, tempCamera);

      // 1. Check if we intersected an occupant capsule
      const occupantGroups: { group: THREE.Object3D; occupant: SocialOccupant }[] = [];
      renderedRef.current.forEach((val: Rendered, key: string) => {
        if (key !== meRef.current) {
          const occ = occRef.current.find((o) => o.account_id === key);
          if (occ) {
            occupantGroups.push({ group: val.group, occupant: occ });
          }
        }
      });

      let clickedOccupant: SocialOccupant | null = null;
      let closestDistance = Infinity;

      for (const item of occupantGroups) {
        const intersects = raycaster.intersectObjects(item.group.children, true);
        if (intersects.length > 0) {
          if (intersects[0].distance < closestDistance) {
            closestDistance = intersects[0].distance;
            clickedOccupant = item.occupant;
          }
        }
      }

      if (clickedOccupant && onSelectPlayer) {
        onSelectPlayer(clickedOccupant);
        return;
      }

      // 2. Otherwise intersect floor
      const floorPlane = new THREE.Mesh(new THREE.PlaneGeometry(24, 24));
      floorPlane.rotation.x = -Math.PI / 2;
      floorPlane.updateMatrixWorld();

      const intersectsFloor = raycaster.intersectObject(floorPlane);
      if (intersectsFloor.length > 0) {
        const pt = intersectsFloor[0].point;
        // Map 3D (-12..12) back to world coordinates (0..100)
        const wx = ((pt.x + 12) / 24) * 100;
        const wy = ((pt.z + 12) / 24) * 100;
        onMove(Math.max(0, Math.min(100, wx)), Math.max(0, Math.min(100, wy)));
      }
    },
    [onMove, onSelectPlayer],
  );

  return (
    <div
      ref={mountRef}
      style={{
        width: '100%',
        height: '100%',
        position: 'relative',
        cursor: 'pointer',
      }}
      onClick={handleClick}
      title="Click 3D floor to move/run · Click avatars to interact"
    />
  );
}
