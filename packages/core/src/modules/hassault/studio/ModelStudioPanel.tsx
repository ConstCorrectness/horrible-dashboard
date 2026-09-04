/**
 * HorribleAssault Studio: 3D Asset Inspector, Level Designer, PBR Material
 * Channel Viewer, and Animation Suite.
 *
 * Designed with a clean CAD/DCC aesthetic inspired by Blender 4.x and Unreal Engine.
 * Features Scene Outliner, 3D Transform Gizmos, PBR channel inspection, and universal
 * scene serialization (map.json export/import).
 */

import React, { useEffect, useRef, useState, useCallback } from 'react';
import { apiUrl } from '../../../origin';
import {
  IconMesh,
  IconArmature,
  IconCrosshair,
  IconActor,
  IconFilm,
  IconTexture,
  IconMap,
  IconTransform,
  IconSearch,
  IconClose,
  IconChevronDown,
  IconChevronRight,
  IconPlus,
  IconPlay,
  IconPause,
  IconLoop,
  IconDownload,
  IconSidebarLeft,
  IconCopy,
} from './StudioIcons';
import {
  StudioScene,
  SceneNode,
  SceneNodeType,
  createDefaultScene,
  serializeScene,
} from './sceneTypes';
import { TextureInspector } from './TextureInspector';
import { SceneOutliner } from './SceneOutliner';
import { createEntityMarker } from './EntityMarkers';
import { registry } from '../../../registry';

export interface ArtModelItem {
  id: string;
  name: string;
  category: string;
  format: string;
  file_path: string;
  size_bytes: number;
  textures?: string[];
  animations?: string[];
  is_compiled?: boolean;
}

export type StudioTab = 'viewer' | 'scene' | 'materials' | 'animator' | 'editor';

export function getModelDisplayMeta(m: ArtModelItem): {
  title: string;
  subtitle: string;
  iconType: 'arms' | 'weapon' | 'operator' | 'animation' | 'character' | 'map' | 'default';
  badge: string;
  badgeColor: string;
} {
  const n = m.name;
  const p = m.file_path;
  const isGlb = m.format.toLowerCase() === 'glb';
  const badge = isGlb ? 'GLB' : m.category === 'animation' ? 'ANIM' : m.category === 'map' ? 'MAP' : 'FBX';
  const badgeColor = isGlb
    ? 'rgb(16, 185, 129)'
    : m.category === 'animation'
    ? 'rgb(245, 158, 11)'
    : m.category === 'map'
    ? 'rgb(249, 115, 22)'
    : 'rgb(129, 140, 248)';

  if (m.category.includes('map') || m.file_path.includes('buildings')) {
    const isNyc = m.name.toLowerCase().includes('newyork') || m.file_path.toLowerCase().includes('new-york');
    return {
      title: isNyc ? 'New York City Block' : m.name.replace(/\.[^/.]+$/, '').replace(/[-_]/g, ' '),
      subtitle: isNyc ? 'Urban Streetscape & Criminal Case 3D' : 'Modular City Street & Building Kit',
      iconType: 'map',
      badge: 'MAP',
      badgeColor: 'rgb(249, 115, 22)',
    };
  }

  if (m.category.includes('arms')) {
    const isCompiled = m.id.includes('compiled') || isGlb;
    return {
      title: isCompiled ? 'FPS Female Arms (Rigged)' : 'FPS Female Arms (Source)',
      subtitle: isCompiled ? 'Viewport 2-Bone IK Rig • 94 Bones' : 'FBX Source Rig with PBR Textures',
      iconType: 'arms',
      badge,
      badgeColor,
    };
  }

  if (m.category.includes('weapon')) {
    if (m.id.includes('fal') || n.includes('FAL')) {
      return {
        title: 'FN FAL Rifle (Reload Anim)',
        subtitle: 'Animated Receiver, Bolt, Mag • 9 Bones',
        iconType: 'weapon',
        badge,
        badgeColor,
      };
    }
    if (m.id.includes('assault') || n.includes('assault')) {
      return {
        title: 'Assault Rifle (Standard)',
        subtitle: 'Primary Assault Rifle Prop',
        iconType: 'weapon',
        badge,
        badgeColor,
      };
    }
    if (m.id.includes('m4') || n.includes('M4')) {
      return {
        title: 'M4A1 Carbine',
        subtitle: 'High-Poly Tactical Carbine',
        iconType: 'weapon',
        badge,
        badgeColor,
      };
    }
    if (m.id.includes('beretta') || n.includes('Base.fbx') || n.includes('beretta')) {
      return {
        title: 'Beretta 92 Pistol',
        subtitle: '9mm Semi-Auto Sidearm Prop',
        iconType: 'weapon',
        badge,
        badgeColor,
      };
    }
    if (m.id.includes('remington') || n.includes('870')) {
      return {
        title: 'Remington 870 Shotgun',
        subtitle: '12-Gauge Pump Shotgun Prop',
        iconType: 'weapon',
        badge,
        badgeColor,
      };
    }
    if (m.id.includes('svu') || n.includes('sniper')) {
      return {
        title: m.id.includes('svu') ? 'SVU-A Sniper Rifle' : 'Sniper Rifle (Standard)',
        subtitle: 'Precision Bullpup Marksman Rifle',
        iconType: 'weapon',
        badge,
        badgeColor,
      };
    }
    if (m.id.includes('pistol')) {
      return {
        title: 'Pistol (Standard)',
        subtitle: 'Secondary Sidearm Prop',
        iconType: 'weapon',
        badge,
        badgeColor,
      };
    }
    if (m.id.includes('shotgun')) {
      return {
        title: 'Shotgun (Standard)',
        subtitle: 'Close-Range Pump Shotgun Prop',
        iconType: 'weapon',
        badge,
        badgeColor,
      };
    }
    return {
      title: n.replace(/\.[^/.]+$/, '').replace(/[-_]/g, ' '),
      subtitle: p,
      iconType: 'weapon',
      badge,
      badgeColor,
    };
  }

  if (m.category === 'animation') {
    const clean = n.replace(/([A-Z])/g, ' $1').trim().replace(/Fbx$/i, '');
    return {
      title: clean,
      subtitle: 'Skeletal Motion Clip',
      iconType: 'animation',
      badge,
      badgeColor,
    };
  }

  // Operators & Characters
  if (n.includes('swat') || m.id.includes('swat')) {
    return {
      title: 'Green SWAT Operator',
      subtitle: 'Counter-Terrorist Rigged Operator',
      iconType: 'operator',
      badge,
      badgeColor,
    };
  }
  if (m.id.includes('operator')) {
    return {
      title: 'Base Game Operator',
      subtitle: 'Standard Character Mesh',
      iconType: 'operator',
      badge,
      badgeColor,
    };
  }
  if (m.id.includes('clips')) {
    return {
      title: 'Operator Skeleton & Clips',
      subtitle: 'Locomotion & Action Bone Rig',
      iconType: 'operator',
      badge,
      badgeColor,
    };
  }
  if (n.includes('Ch18')) {
    return {
      title: 'SpecOps Operator (Ch18)',
      subtitle: 'Tactical Humanoid Mesh',
      iconType: 'character',
      badge,
      badgeColor,
    };
  }
  if (n.includes('Ch50')) {
    return {
      title: 'Assault Soldier (Ch50)',
      subtitle: 'Heavy Tactical Humanoid',
      iconType: 'character',
      badge,
      badgeColor,
    };
  }
  if (n.includes('Yaku')) {
    return {
      title: 'Yaku J Ignite',
      subtitle: 'Stylized Combat Character',
      iconType: 'character',
      badge,
      badgeColor,
    };
  }

  return {
    title: n.replace(/\.[^/.]+$/, ''),
    subtitle: p,
    iconType: 'default',
    badge,
    badgeColor,
  };
}

export function ModelStudioPanel({ initialTab = 'viewer' }: { initialTab?: StudioTab }) {
  const [tab, setTab] = useState<StudioTab>(initialTab);
  const [models, setModels] = useState<ArtModelItem[]>([]);
  const [selectedModel, setSelectedModel] = useState<ArtModelItem | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Model stats & tree
  const [meshCount, setMeshCount] = useState(0);
  const [vertCount, setVertCount] = useState(0);
  const [triCount, setTriCount] = useState(0);
  const [boneList, setBoneList] = useState<string[]>([]);
  const [materialNames, setMaterialNames] = useState<string[]>([]);
  const [textureList, setTextureList] = useState<string[]>([]);
  const [boundsText, setBoundsText] = useState<string>('0 x 0 x 0');

  // Viewer options
  const [wireframe, setWireframe] = useState(false);
  const [showSkeleton, setShowSkeleton] = useState(true);
  const [showGrid, setShowGrid] = useState(true);
  const [lightPreset, setLightPreset] = useState<'studio' | 'outdoor' | 'combat'>('studio');
  const [activeTextureChannel, setActiveTextureChannel] = useState<'pbr' | 'albedo' | 'normal' | 'roughness' | 'wireframe'>('pbr');

  // Level Design / Scene state
  const [sceneDoc, setSceneDoc] = useState<StudioScene>(() => createDefaultScene('New Map'));
  const [selectedSceneNodeId, setSelectedSceneNodeId] = useState<string | null>(null);
  const [transformMode, setTransformMode] = useState<'translate' | 'rotate' | 'scale'>('translate');
  const [sceneObjects] = useState<Map<string, any>>(() => new Map());

  // Editor parameters
  const [modelScale, setModelScale] = useState<number>(1.0);
  const [modelRotX, setModelRotX] = useState<number>(0);
  const [modelRotY, setModelRotY] = useState<number>(0);
  const [modelRotZ, setModelRotZ] = useState<number>(0);
  const [modelPosX, setModelPosX] = useState<number>(0);
  const [modelPosY, setModelPosY] = useState<number>(0);
  const [modelPosZ, setModelPosZ] = useState<number>(0);
  const [primaryGrip] = useState<[number, number, number]>([0, -0.3, 0.22]);
  const [supportGrip] = useState<[number, number, number]>([0, -0.22, -0.55]);

  // Animation timeline
  const [clips, setClips] = useState<string[]>([]);
  const [selectedClip, setSelectedClip] = useState<string>('');
  const [isPlaying, setIsPlaying] = useState<boolean>(false);
  const [animTime, setAnimTime] = useState<number>(0);
  const [animDuration, setAnimDuration] = useState<number>(1);
  const [playbackSpeed, setPlaybackSpeed] = useState<number>(1.0);
  const [loopAnim, setLoopAnim] = useState<boolean>(true);

  // Sidebar controls
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');
  const [filterCategory, setFilterCategory] = useState<'all' | 'arms' | 'weapon' | 'operator' | 'map' | 'animation'>('all');
  const [collapsedCats, setCollapsedCats] = useState<Record<string, boolean>>({});

  // Map project management
  const [savedMaps, setSavedMaps] = useState<Array<{ id: string; name: string; nodes_count: number }>>([]);
  const [selectedMapId, setSelectedMapId] = useState<string>('default');
  const [saveStatus, setSaveStatus] = useState<string | null>(null);

  const fetchStudioMaps = useCallback(() => {
    fetch(apiUrl('/api/hassault/studio/maps'))
      .then((res) => res.json())
      .then((data) => {
        if (Array.isArray(data)) {
          setSavedMaps(data);
        }
      })
      .catch((err) => console.warn('Could not fetch studio maps:', err));
  }, []);

  useEffect(() => {
    fetchStudioMaps();
  }, [fetchStudioMaps]);

  const loadStudioMap = useCallback(async (mapId: string) => {
    if (mapId === 'default') {
      setSceneDoc(createDefaultScene('New Arena'));
      setSelectedMapId('default');
      setSelectedSceneNodeId(null);
      return;
    }
    try {
      const res = await fetch(apiUrl(`/api/hassault/studio/maps/${encodeURIComponent(mapId)}`));
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const mapData = await res.json();
      setSceneDoc(mapData);
      setSelectedMapId(mapId);
      setSelectedSceneNodeId(null);
    } catch (err: any) {
      console.error('Failed to load studio map:', err);
      setError(`Failed to load map: ${err.message}`);
    }
  }, []);

  const saveCurrentMap = useCallback(async () => {
    try {
      setSaveStatus('Saving...');
      const mapId =
        selectedMapId === 'default'
          ? sceneDoc.name.toLowerCase().replace(/[^a-z0-9]+/g, '_') || 'custom_arena'
          : selectedMapId;
      const res = await fetch(apiUrl(`/api/hassault/studio/maps/${encodeURIComponent(mapId)}`), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(sceneDoc),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setSaveStatus('Saved!');
      setSelectedMapId(data.id);
      fetchStudioMaps();
      setTimeout(() => setSaveStatus(null), 3000);
    } catch (err: any) {
      console.error('Failed to save map:', err);
      setSaveStatus('Failed');
      setTimeout(() => setSaveStatus(null), 3000);
    }
  }, [selectedMapId, sceneDoc, fetchStudioMaps]);

  // Resizable panels
  const [sidebarWidth, setSidebarWidth] = useState<number>(360);
  const [outlinerWidth, setOutlinerWidth] = useState<number>(280);
  const [isResizingSidebar, setIsResizingSidebar] = useState<boolean>(false);
  const [isResizingOutliner, setIsResizingOutliner] = useState<boolean>(false);

  const resizingSidebarRef = useRef<{ startX: number; startW: number } | null>(null);
  const resizingOutlinerRef = useRef<{ startX: number; startW: number } | null>(null);

  const onSidebarPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    e.currentTarget.setPointerCapture(e.pointerId);
    resizingSidebarRef.current = { startX: e.clientX, startW: sidebarWidth };
    setIsResizingSidebar(true);
  };

  const onSidebarPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!resizingSidebarRef.current) return;
    const delta = e.clientX - resizingSidebarRef.current.startX;
    setSidebarWidth(Math.max(220, Math.min(700, resizingSidebarRef.current.startW + delta)));
  };

  const onSidebarPointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    if (resizingSidebarRef.current) {
      resizingSidebarRef.current = null;
      setIsResizingSidebar(false);
      try {
        e.currentTarget.releasePointerCapture(e.pointerId);
      } catch {}
    }
  };

  const onOutlinerPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    e.currentTarget.setPointerCapture(e.pointerId);
    resizingOutlinerRef.current = { startX: e.clientX, startW: outlinerWidth };
    setIsResizingOutliner(true);
  };

  const onOutlinerPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!resizingOutlinerRef.current) return;
    const delta = resizingOutlinerRef.current.startX - e.clientX;
    setOutlinerWidth(Math.max(200, Math.min(550, resizingOutlinerRef.current.startW + delta)));
  };

  const onOutlinerPointerUp = (e: React.PointerEvent<HTMLDivElement>) => {
    if (resizingOutlinerRef.current) {
      resizingOutlinerRef.current = null;
      setIsResizingOutliner(false);
      try {
        e.currentTarget.releasePointerCapture(e.pointerId);
      } catch {}
    }
  };

  const toggleCat = (cat: string) => {
    setCollapsedCats((prev) => ({ ...prev, [cat]: !prev[cat] }));
  };

  const containerRef = useRef<HTMLDivElement>(null);
  const threeRef = useRef<any>(null);
  const sceneRef = useRef<any>(null);
  const cameraRef = useRef<any>(null);
  const rendererRef = useRef<any>(null);
  const controlsRef = useRef<any>(null);
  const transformControlsRef = useRef<any>(null);
  const currentObjectRef = useRef<any>(null);
  const mixerRef = useRef<any>(null);
  const actionRef = useRef<any>(null);
  const skeletonHelperRef = useRef<any>(null);
  const gridHelperRef = useRef<any>(null);
  const axesHelperRef = useRef<any>(null);
  const gripMarkersRef = useRef<any[]>([]);

  // Fetch list of art models from backend
  useEffect(() => {
    fetch(apiUrl('/api/hassault/art/models'))
      .then((res) => res.json())
      .then((data) => {
        const list: ArtModelItem[] = data.models || [];
        setModels(list);
        if (list.length > 0) {
          const def = list.find((m) => m.id.includes('arms') || m.id.includes('fal')) || list[0];
          setSelectedModel(def);
        }
      })
      .catch((err) => {
        console.error('Failed to load art models:', err);
        setError('Could not connect to /api/hassault/art/models');
      });
  }, []);

  // Initialize Three.js scene & TransformControls
  useEffect(() => {
    let animFrameId: number;
    let clock: any;

    async function init() {
      const container = containerRef.current;
      if (!container) return;

      const THREE = await import('three');
      const { OrbitControls } = await import('three/examples/jsm/controls/OrbitControls.js');
      const { TransformControls } = await import('three/examples/jsm/controls/TransformControls.js');
      const { GLTFLoader } = await import('three/examples/jsm/loaders/GLTFLoader.js');
      const { FBXLoader } = await import('three/examples/jsm/loaders/FBXLoader.js');

      threeRef.current = { THREE, OrbitControls, TransformControls, GLTFLoader, FBXLoader };

      const scene = new THREE.Scene();
      scene.background = new THREE.Color(0x0a0e17);
      sceneRef.current = scene;

      const initW = Math.max(1, container.clientWidth || 400);
      const initH = Math.max(1, container.clientHeight || 300);

      const camera = new THREE.PerspectiveCamera(50, initW / initH, 0.01, 1000);
      camera.position.set(0, 2.5, 5);
      cameraRef.current = camera;

      const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
      renderer.setSize(initW, initH, false);
      renderer.shadowMap.enabled = true;
      renderer.domElement.style.width = '100%';
      renderer.domElement.style.height = '100%';
      renderer.domElement.style.display = 'block';
      rendererRef.current = renderer;

      container.innerHTML = '';
      container.appendChild(renderer.domElement);

      const controls = new OrbitControls(camera, renderer.domElement);
      controls.enableDamping = true;
      controls.dampingFactor = 0.05;
      controlsRef.current = controls;

      // 3D Transform Gizmo
      const transformControls = new TransformControls(camera, renderer.domElement);
      transformControls.size = 0.75;
      transformControls.addEventListener('dragging-changed', (event: any) => {
        if (controlsRef.current) {
          controlsRef.current.enabled = !event.value;
        }
      });
      transformControls.addEventListener('objectChange', () => {
        const targetObj = transformControls.object;
        if (targetObj && targetObj.userData?.nodeId) {
          const nodeId = targetObj.userData.nodeId;
          setSceneDoc((prev) => ({
            ...prev,
            nodes: prev.nodes.map((n) => {
              if (n.id !== nodeId) return n;
              return {
                ...n,
                transform: {
                  position: [
                    Number(targetObj.position.x.toFixed(2)),
                    Number(targetObj.position.y.toFixed(2)),
                    Number(targetObj.position.z.toFixed(2)),
                  ],
                  rotation: [
                    Number(((targetObj.rotation.x * 180) / Math.PI).toFixed(1)),
                    Number(((targetObj.rotation.y * 180) / Math.PI).toFixed(1)),
                    Number(((targetObj.rotation.z * 180) / Math.PI).toFixed(1)),
                  ],
                  scale: [
                    Number(targetObj.scale.x.toFixed(2)),
                    Number(targetObj.scale.y.toFixed(2)),
                    Number(targetObj.scale.z.toFixed(2)),
                  ],
                },
              };
            }),
          }));
        }
      });
      scene.add(((transformControls as any).getHelper ? (transformControls as any).getHelper() : transformControls) as any);
      transformControlsRef.current = transformControls;

      // Responsive viewport resize updater
      const updateSize = () => {
        const c = containerRef.current;
        const cam = cameraRef.current;
        const ren = rendererRef.current;
        if (!c || !cam || !ren) return;
        const w = c.clientWidth;
        const h = c.clientHeight;
        if (w <= 0 || h <= 0) return;
        cam.aspect = w / h;
        cam.updateProjectionMatrix();
        ren.setSize(w, h, false);
      };

      const ro = new ResizeObserver(() => {
        updateSize();
      });
      ro.observe(container);

      // Lighting
      const hemi = new THREE.HemisphereLight(0xffffff, 0x333940, 1.0);
      hemi.position.set(0, 20, 0);
      scene.add(hemi);

      const dirLight = new THREE.DirectionalLight(0xffffff, 1.5);
      dirLight.position.set(10, 20, 15);
      dirLight.castShadow = true;
      scene.add(dirLight);

      // Grid and Axes
      const grid = new THREE.GridHelper(20, 40, 0x3b82f6, 0x1e293b);
      grid.position.y = -0.01;
      scene.add(grid);
      gridHelperRef.current = grid;

      const axes = new THREE.AxesHelper(1.5);
      scene.add(axes);
      axesHelperRef.current = axes;

      clock = new THREE.Clock();

      let lastW = 0;
      let lastH = 0;

      const animate = () => {
        animFrameId = requestAnimationFrame(animate);
        const dt = clock.getDelta();

        const c = containerRef.current;
        if (c) {
          const cw = c.clientWidth;
          const ch = c.clientHeight;
          if (cw > 0 && ch > 0 && (cw !== lastW || ch !== lastH)) {
            lastW = cw;
            lastH = ch;
            updateSize();
          }
        }

        if (mixerRef.current && isPlaying) {
          mixerRef.current.update(dt * playbackSpeed);
          if (actionRef.current) {
            setAnimTime(actionRef.current.time);
          }
        }

        controls.update();
        renderer.render(scene, camera);
      };
      animate();

      window.addEventListener('resize', updateSize);

      return () => {
        cancelAnimationFrame(animFrameId);
        ro.disconnect();
        window.removeEventListener('resize', updateSize);
        if (rendererRef.current) {
          rendererRef.current.dispose();
        }
      };
    }

    let cleanupFn: (() => void) | undefined;
    init().then((cleanup) => {
      cleanupFn = cleanup;
    });

    return () => {
      if (cleanupFn) cleanupFn();
    };
  }, []);

  // Update transform gizmo mode and attach target
  useEffect(() => {
    const tc = transformControlsRef.current;
    if (!tc) return;

    if (tab === 'scene') {
      if (selectedSceneNodeId && sceneObjects.has(selectedSceneNodeId)) {
        const obj = sceneObjects.get(selectedSceneNodeId);
        tc.attach(obj);
        tc.setMode(transformMode);
      } else {
        tc.detach();
      }
    } else if (tab === 'editor' && currentObjectRef.current) {
      tc.attach(currentObjectRef.current);
      tc.setMode(transformMode);
    } else {
      tc.detach();
    }
  }, [tab, selectedSceneNodeId, transformMode, sceneObjects]);

  // Synchronize 3D visual entity markers for scene nodes
  useEffect(() => {
    if (tab !== 'scene' || !sceneRef.current || !threeRef.current) return;
    const scene = sceneRef.current;

    // Remove any markers that no longer exist in sceneDoc.nodes
    const activeIds = new Set(sceneDoc.nodes.map((n) => n.id));
    for (const [id, obj] of sceneObjects.entries()) {
      if (!activeIds.has(id)) {
        scene.remove(obj);
        sceneObjects.delete(id);
      }
    }

    // Create or update markers for sceneDoc.nodes
    for (const node of sceneDoc.nodes) {
      if (!sceneObjects.has(node.id)) {
        const marker = createEntityMarker(node);
        scene.add(marker);
        sceneObjects.set(node.id, marker);

        // If this node is a 3D mesh prop (such as a city block or building), asynchronously load its geometry
        if (node.type === 'mesh_prop' && node.assetPath && threeRef.current) {
          const fileUrl = apiUrl(`/api/hassault/art/file?path=${encodeURIComponent(node.assetPath.replace(/^\//, ''))}`);
          const format = node.assetFormat || (node.assetPath.toLowerCase().endsWith('.fbx') ? 'fbx' : 'gltf');
          if (format === 'fbx') {
            const loader = new threeRef.current.FBXLoader();
            loader.load(fileUrl, (fbx: any) => {
              marker.add(fbx);
            }, undefined, (err: any) => console.warn('Failed to load FBX mesh prop:', err));
          } else if (format === 'glb' || format === 'gltf') {
            const loader = new threeRef.current.GLTFLoader();
            loader.load(fileUrl, (gltf: any) => {
              marker.add(gltf.scene);
            }, undefined, (err: any) => console.warn('Failed to load GLTF mesh prop:', err));
          }
        }
      } else {
        const existing = sceneObjects.get(node.id);
        existing.visible = node.visible;
        existing.position.set(
          node.transform.position[0],
          node.transform.position[1],
          node.transform.position[2]
        );
        existing.rotation.set(
          (node.transform.rotation[0] * Math.PI) / 180,
          (node.transform.rotation[1] * Math.PI) / 180,
          (node.transform.rotation[2] * Math.PI) / 180
        );
        existing.scale.set(
          node.transform.scale[0],
          node.transform.scale[1],
          node.transform.scale[2]
        );
      }
    }
  }, [tab, sceneDoc.nodes, sceneObjects]);

  // Load selected 3D model into scene
  const loadModel = useCallback(
    async (item: ArtModelItem) => {
      if (!threeRef.current || !sceneRef.current) return;
      setLoading(true);
      setError(null);

      const { THREE, GLTFLoader, FBXLoader } = threeRef.current;
      const scene = sceneRef.current;

      // Remove existing model & helpers
      if (currentObjectRef.current) {
        scene.remove(currentObjectRef.current);
        currentObjectRef.current = null;
      }
      if (skeletonHelperRef.current) {
        scene.remove(skeletonHelperRef.current);
        skeletonHelperRef.current = null;
      }
      for (const marker of gripMarkersRef.current) {
        scene.remove(marker);
      }
      gripMarkersRef.current = [];

      if (mixerRef.current) {
        mixerRef.current.stopAllAction();
        mixerRef.current = null;
      }

      const fileUrl = apiUrl(`/api/hassault/art/file?path=${encodeURIComponent(item.file_path)}`);

      try {
        let loadedObject: any = null;
        let loadedAnimations: any[] = [];

        if (item.format === 'glb' || item.format === 'gltf') {
          const gltf = await new Promise<any>((res, rej) => {
            new GLTFLoader().load(fileUrl, res, undefined, rej);
          });
          loadedObject = gltf.scene;
          loadedAnimations = gltf.animations || [];
        } else if (item.format === 'fbx') {
          const fbx = await new Promise<any>((res, rej) => {
            new FBXLoader().load(fileUrl, res, undefined, rej);
          });
          loadedObject = fbx;
          loadedAnimations = fbx.animations || [];
        }

        if (!loadedObject) throw new Error(`Unsupported format: ${item.format}`);

        currentObjectRef.current = loadedObject;
        scene.add(loadedObject);

        // Center and inspect bounding box
        const box = new THREE.Box3().setFromObject(loadedObject);
        const size = box.getSize(new THREE.Vector3());
        const center = box.getCenter(new THREE.Vector3());
        setBoundsText(`${size.x.toFixed(2)} x ${size.y.toFixed(2)} x ${size.z.toFixed(2)}m`);

        // Camera auto-frame
        const maxDim = Math.max(size.x, size.y, size.z, 0.5);
        if (cameraRef.current && controlsRef.current) {
          cameraRef.current.position.set(center.x, center.y + maxDim * 0.8, center.z + maxDim * 2.2);
          controlsRef.current.target.copy(center);
          controlsRef.current.update();
        }

        // Count geometry stats and bones
        let meshes = 0;
        let verts = 0;
        let tris = 0;
        const bones: string[] = [];
        const mats = new Set<string>();

        loadedObject.traverse((child: any) => {
          if (child.isMesh) {
            meshes++;
            const geom = child.geometry;
            if (geom) {
              verts += geom.attributes?.position?.count || 0;
              tris += geom.index ? geom.index.count / 3 : (geom.attributes?.position?.count || 0) / 3;
            }
            if (child.material) {
              if (Array.isArray(child.material)) {
                child.material.forEach((m: any) => m.name && mats.add(m.name));
              } else if (child.material.name) {
                mats.add(child.material.name);
              }
            }
            child.castShadow = true;
            child.receiveShadow = true;
          } else if (child.isBone) {
            bones.push(child.name || `Bone_${bones.length}`);
          }
        });

        setMeshCount(meshes);
        setVertCount(verts);
        setTriCount(Math.round(tris));
        setBoneList(bones);
        setMaterialNames(Array.from(mats));
        setTextureList(item.textures || []);

        // Skeleton visualization
        if (bones.length > 0 && showSkeleton) {
          const helper = new THREE.SkeletonHelper(loadedObject);
          helper.material.linewidth = 2;
          scene.add(helper);
          skeletonHelperRef.current = helper;
        }

        // Grip markers
        const mkCube = (color: number, pos: [number, number, number]) => {
          const m = new THREE.Mesh(
            new THREE.BoxGeometry(0.04, 0.04, 0.04),
            new THREE.MeshBasicMaterial({ color }),
          );
          m.position.set(...pos);
          scene.add(m);
          gripMarkersRef.current.push(m);
        };
        mkCube(0x38bdf8, primaryGrip);
        mkCube(0x34d399, supportGrip);

        // Animations setup
        if (loadedAnimations.length > 0) {
          const clipNames = loadedAnimations.map((c: any) => c.name || 'default');
          setClips(clipNames);
          setSelectedClip(clipNames[0]);

          const mixer = new THREE.AnimationMixer(loadedObject);
          mixerRef.current = mixer;

          const action = mixer.clipAction(loadedAnimations[0]);
          actionRef.current = action;
          action.play();
          setAnimDuration(loadedAnimations[0].duration || 1);
          setAnimTime(0);
          setIsPlaying(true);
        } else {
          setClips([]);
          setSelectedClip('');
        }
      } catch (err: any) {
        console.error('Failed to load 3D model:', err);
        setError(`Failed to load: ${err.message || err}`);
      } finally {
        setLoading(false);
      }
    },
    [showSkeleton, primaryGrip, supportGrip],
  );

  // Reload model when selection changes
  useEffect(() => {
    if (selectedModel) {
      loadModel(selectedModel);
    }
  }, [selectedModel, loadModel]);

  // Wireframe toggle
  useEffect(() => {
    if (!currentObjectRef.current) return;
    currentObjectRef.current.traverse((child: any) => {
      if (child.isMesh && child.material) {
        if (Array.isArray(child.material)) {
          child.material.forEach((m: any) => (m.wireframe = wireframe));
        } else {
          child.material.wireframe = wireframe;
        }
      }
    });
  }, [wireframe]);

  // Skeleton toggle
  useEffect(() => {
    if (!skeletonHelperRef.current) return;
    skeletonHelperRef.current.visible = showSkeleton;
  }, [showSkeleton]);

  // Grid toggle
  useEffect(() => {
    if (!gridHelperRef.current) return;
    gridHelperRef.current.visible = showGrid;
  }, [showGrid]);

  // Apply model transforms from editor
  useEffect(() => {
    const obj = currentObjectRef.current;
    if (!obj || tab !== 'editor') return;
    const { THREE } = threeRef.current || {};
    if (!THREE) return;

    obj.scale.setScalar(modelScale);
    obj.rotation.set(
      (modelRotX * Math.PI) / 180,
      (modelRotY * Math.PI) / 180,
      (modelRotZ * Math.PI) / 180,
    );
    obj.position.set(modelPosX, modelPosY, modelPosZ);
  }, [modelScale, modelRotX, modelRotY, modelRotZ, modelPosX, modelPosY, modelPosZ, tab]);

  // Place selected asset into current Scene/Map
  const placeAssetInScene = (item: ArtModelItem) => {
    const id = `prop_${Date.now()}`;
    const newNode: SceneNode = {
      id,
      name: item.name.replace(/\.[^/.]+$/, ''),
      type: 'mesh_prop',
      transform: {
        position: [0, 0, 0],
        rotation: [0, 0, 0],
        scale: item.category === 'map' ? [0.01, 0.01, 0.01] : [1, 1, 1],
      },
      assetPath: item.file_path,
      assetFormat: item.format,
      visible: true,
      locked: false,
    };
    setSceneDoc((prev) => ({
      ...prev,
      nodes: [...prev.nodes, newNode],
    }));
    setSelectedSceneNodeId(id);
    setTab('scene');
  };

  // Add entity to scene (spawn, light, pickup)
  const handleAddEntity = (type: SceneNodeType, name?: string, weaponId?: string) => {
    const id = `${type}_${Date.now()}`;
    const newNode: SceneNode = {
      id,
      name: name || `${type}_${sceneDoc.nodes.length + 1}`,
      type,
      transform: {
        position: [
          Number((Math.random() * 6 - 3).toFixed(1)),
          type === 'weapon_pickup' ? 0.4 : 0,
          Number((Math.random() * 6 - 3).toFixed(1)),
        ],
        rotation: [0, 0, 0],
        scale: [1, 1, 1],
      },
      properties: {
        team: name?.includes('CLA') ? 'CLA' : name?.includes('RVS') ? 'RVS' : undefined,
        weaponId: weaponId || (type === 'weapon_pickup' ? 'fal' : undefined),
        color:
          type === 'weapon_pickup'
            ? 'rgb(245, 158, 11)'
            : name?.includes('CLA')
            ? 'rgb(239, 68, 68)'
            : name?.includes('RVS')
            ? 'rgb(59, 130, 246)'
            : undefined,
      },
      visible: true,
      locked: false,
    };
    setSceneDoc((prev) => ({
      ...prev,
      nodes: [...prev.nodes, newNode],
    }));
    setSelectedSceneNodeId(id);
  };

  // Scene node visibility toggle
  const handleToggleNodeVisibility = (nodeId: string) => {
    setSceneDoc((prev) => ({
      ...prev,
      nodes: prev.nodes.map((n) => (n.id === nodeId ? { ...n, visible: !n.visible } : n)),
    }));
  };

  // Scene node lock toggle
  const handleToggleNodeLock = (nodeId: string) => {
    setSceneDoc((prev) => ({
      ...prev,
      nodes: prev.nodes.map((n) => (n.id === nodeId ? { ...n, locked: !n.locked } : n)),
    }));
  };

  // Delete scene node
  const handleDeleteNode = (nodeId: string) => {
    setSceneDoc((prev) => ({
      ...prev,
      nodes: prev.nodes.filter((n) => n.id !== nodeId),
    }));
    if (selectedSceneNodeId === nodeId) {
      setSelectedSceneNodeId(null);
    }
  };

  // Export scene JSON
  const handleExportScene = () => {
    const json = serializeScene(sceneDoc);
    navigator.clipboard?.writeText(json);
    const blob = new Blob([json], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${sceneDoc.name.toLowerCase().replace(/\s+/g, '_')}_map.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  // Align forward down -Z (for weapons)
  const alignDownNegZ = () => {
    setModelRotX(0);
    setModelRotY(180);
    setModelRotZ(0);
  };

  // Center model to origin
  const centerToOrigin = () => {
    const obj = currentObjectRef.current;
    if (!obj || !threeRef.current) return;
    const { THREE } = threeRef.current;
    const box = new THREE.Box3().setFromObject(obj);
    const center = box.getCenter(new THREE.Vector3());
    setModelPosX(-center.x);
    setModelPosY(-box.min.y);
    setModelPosZ(-center.z);
  };

  // Switch animation clip
  const handleClipChange = (clipName: string) => {
    setSelectedClip(clipName);
    if (!mixerRef.current || !currentObjectRef.current) return;
    const anims = currentObjectRef.current.animations || [];
    const clip = anims.find((c: any) => (c.name || '') === clipName) || anims[0];
    if (clip) {
      mixerRef.current.stopAllAction();
      const action = mixerRef.current.clipAction(clip);
      actionRef.current = action;
      action.play();
      setAnimDuration(clip.duration);
      setAnimTime(0);
      setIsPlaying(true);
    }
  };

  // Timeline scrub
  const handleScrub = (time: number) => {
    setAnimTime(time);
    if (actionRef.current && mixerRef.current) {
      actionRef.current.time = time;
      mixerRef.current.update(0);
    }
  };

  const renderIconForType = (type: string) => {
    switch (type) {
      case 'arms':
        return <IconArmature size={14} color="rgb(56, 189, 248)" />;
      case 'weapon':
        return <IconCrosshair size={14} color="rgb(245, 158, 11)" />;
      case 'operator':
      case 'character':
        return <IconActor size={14} color="rgb(52, 211, 153)" />;
      case 'map':
        return <IconMap size={14} color="rgb(249, 115, 22)" />;
      case 'animation':
        return <IconFilm size={14} color="rgb(168, 85, 247)" />;
      default:
        return <IconMesh size={14} color="rgb(148, 163, 184)" />;
    }
  };

  return (
    <div style={styles.root}>
      {/* Sidebar: Models Catalog */}
      {sidebarOpen ? (
        <div style={{ ...styles.sidebar, width: `${sidebarWidth}px` }}>
          {/* Header */}
          <div style={styles.sidebarHeader}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '4px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <IconCrosshair size={16} color="rgb(56, 189, 248)" />
                <div>
                  <h1 style={{ margin: 0, fontSize: '0.88rem', fontWeight: 700, color: 'rgb(248, 250, 252)', letterSpacing: '0.02em' }}>
                    HorribleAssault Studio
                  </h1>
                  <p style={{ margin: 0, fontSize: '0.66rem', color: 'rgb(100, 116, 139)' }}>3D Asset, Level &amp; Material Suite</p>
                </div>
              </div>
              <button
                onClick={() => setSidebarOpen(false)}
                title="Collapse sidebar"
                style={styles.iconBtn}
              >
                <IconSidebarLeft size={14} color="rgb(100, 116, 139)" />
              </button>
            </div>

            {/* Workspace Mode Tabs */}
            <div style={styles.tabRow}>
              <button
                onClick={() => setTab('viewer')}
                style={{
                  ...styles.tabBtn,
                  ...(tab === 'viewer' ? styles.tabBtnActive : styles.tabBtnInactive),
                }}
                title="Single Asset Inspection"
              >
                <IconMesh size={11} />
                <span>Asset</span>
              </button>
              <button
                onClick={() => setTab('scene')}
                style={{
                  ...styles.tabBtn,
                  ...(tab === 'scene' ? styles.tabBtnActive : styles.tabBtnInactive),
                }}
                title="3D Map & Level Designer"
              >
                <IconMap size={11} />
                <span>Map/Scene</span>
              </button>
              <button
                onClick={() => setTab('materials')}
                style={{
                  ...styles.tabBtn,
                  ...(tab === 'materials' ? styles.tabBtnActive : styles.tabBtnInactive),
                }}
                title="Principled BSDF Texture Channels"
              >
                <IconTexture size={11} />
                <span>Materials</span>
              </button>
              <button
                onClick={() => setTab('animator')}
                style={{
                  ...styles.tabBtn,
                  ...(tab === 'animator' ? styles.tabBtnActive : styles.tabBtnInactive),
                }}
                title="Skeletal Motion Clips"
              >
                <IconFilm size={11} />
                <span>Anim</span>
              </button>
              <button
                onClick={() => setTab('editor')}
                style={{
                  ...styles.tabBtn,
                  ...(tab === 'editor' ? styles.tabBtnActive : styles.tabBtnInactive),
                }}
                title="Rig Anchors & Grips"
              >
                <IconTransform size={11} />
                <span>Rig</span>
              </button>
            </div>

            {/* Search Input */}
            <div style={{ position: 'relative', marginTop: '8px' }}>
              <input
                type="text"
                placeholder="Filter models & animations..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                style={styles.searchInput}
              />
              <span style={{ position: 'absolute', left: '8px', top: '50%', transform: 'translateY(-50%)' }}>
                <IconSearch size={12} color="rgb(100, 116, 139)" />
              </span>
              {searchQuery && (
                <button
                  onClick={() => setSearchQuery('')}
                  style={styles.clearSearchBtn}
                >
                  <IconClose size={11} color="rgb(148, 163, 184)" />
                </button>
              )}
            </div>

            {/* Category Filter Chips */}
            <div style={{ display: 'flex', gap: '4px', marginTop: '6px', overflowX: 'auto', paddingBottom: '2px' }}>
              {(['all', 'arms', 'weapon', 'operator', 'map', 'animation'] as const).map((cat) => {
                const count = cat === 'all'
                  ? models.length
                  : models.filter((m) => m.category.includes(cat)).length;
                const label =
                  cat === 'all'
                    ? 'All'
                    : cat === 'arms'
                    ? 'Arms'
                    : cat === 'weapon'
                    ? 'Guns'
                    : cat === 'operator'
                    ? 'Chars'
                    : cat === 'map'
                    ? 'Maps'
                    : 'Anims';
                const isActive = filterCategory === cat;
                return (
                  <button
                    key={cat}
                    onClick={() => setFilterCategory(cat)}
                    style={{
                      padding: '2px 6px',
                      borderRadius: '10px',
                      fontSize: '0.64rem',
                      fontWeight: 600,
                      border: 'none',
                      cursor: 'pointer',
                      whiteSpace: 'nowrap',
                      transition: 'all 0.12s',
                      ...(isActive
                        ? { background: 'rgb(37, 99, 235)', color: 'rgb(255, 255, 255)' }
                        : { background: 'rgba(255,255,255,0.06)', color: 'rgb(148, 163, 184)' }),
                    }}
                  >
                    {label} <span style={{ opacity: 0.7, fontSize: '0.58rem' }}>{count}</span>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Model Categories Accordion */}
          <div style={styles.catalog}>
            {['arms', 'weapon', 'operator', 'map', 'animation', 'character'].map((cat) => {
              if (filterCategory !== 'all' && !cat.includes(filterCategory)) return null;

              let catModels = models.filter((m) => m.category.includes(cat));
              if (searchQuery.trim()) {
                const q = searchQuery.toLowerCase();
                catModels = catModels.filter(
                  (m) =>
                    m.name.toLowerCase().includes(q) ||
                    m.file_path.toLowerCase().includes(q) ||
                    m.format.toLowerCase().includes(q),
                );
              }
              if (catModels.length === 0) return null;

              const isCollapsed = collapsedCats[cat];
              const catTitle =
                cat === 'arms'
                  ? 'Viewport Arms'
                  : cat === 'weapon'
                  ? 'Weapons & Firearms'
                  : cat === 'map'
                  ? 'Maps & City Blocks'
                  : cat === 'animation'
                  ? 'Skeletal Animations'
                  : 'Operators & Characters';

              return (
                <div key={cat} style={{ marginBottom: '8px' }}>
                  {/* Category Header */}
                  <div
                    onClick={() => toggleCat(cat)}
                    style={styles.catHeaderBtn}
                    title="Click to toggle category"
                  >
                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                      {isCollapsed ? (
                        <IconChevronRight size={10} color="rgb(100, 116, 139)" />
                      ) : (
                        <IconChevronDown size={10} color="rgb(100, 116, 139)" />
                      )}
                      <span>{catTitle}</span>
                    </div>
                    <span style={styles.catCountBadge}>{catModels.length}</span>
                  </div>

                  {/* Category Items */}
                  {!isCollapsed && (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '2px', marginTop: '2px' }}>
                      {catModels.map((m) => {
                        const isSelected = selectedModel?.id === m.id;
                        const meta = getModelDisplayMeta(m);

                        return (
                          <div
                            key={m.id}
                            onClick={() => setSelectedModel(m)}
                            style={{
                              ...styles.modelCard,
                              ...(isSelected ? styles.modelCardActive : styles.modelCardInactive),
                            }}
                          >
                            {/* Left Icon Tile */}
                            <div style={styles.modelIconBox}>
                              {renderIconForType(meta.iconType)}
                            </div>

                            {/* Center Info */}
                            <div style={{ flex: 1, minWidth: 0, paddingRight: '6px' }}>
                              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                                <span
                                  style={{
                                    fontSize: '0.72rem',
                                    fontWeight: isSelected ? 700 : 500,
                                    color: isSelected ? 'rgb(255, 255, 255)' : 'rgb(226, 232, 240)',
                                    overflow: 'hidden',
                                    textOverflow: 'ellipsis',
                                    whiteSpace: 'nowrap',
                                  }}
                                  title={meta.title}
                                >
                                  {meta.title}
                                </span>
                              </div>
                              <div
                                style={{
                                  fontSize: '0.62rem',
                                  color: isSelected ? 'rgb(147, 197, 253)' : 'rgb(100, 116, 139)',
                                  overflow: 'hidden',
                                  textOverflow: 'ellipsis',
                                  whiteSpace: 'nowrap',
                                  marginTop: '1px',
                                }}
                              >
                                {meta.subtitle}
                              </div>
                            </div>

                            {/* Right Format Badge & Quick Place Button */}
                            <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                              <span
                                style={{
                                  ...styles.formatBadge,
                                  background: `${meta.badgeColor}22`,
                                  color: meta.badgeColor,
                                  border: `1px solid ${meta.badgeColor}44`,
                                }}
                              >
                                {meta.badge}
                              </span>

                              {tab === 'scene' && (
                                <button
                                  onClick={(e) => {
                                    e.stopPropagation();
                                    placeAssetInScene(m);
                                  }}
                                  style={styles.quickPlaceBtn}
                                  title="Add to Scene"
                                >
                                  <IconPlus size={10} color="rgb(52, 211, 153)" />
                                </button>
                              )}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {/* Sidebar Footer Info */}
          <div style={styles.sidebarFooter}>
            <span>{models.length} assets indexed</span>
            <span style={{ color: 'rgb(16, 185, 129)', display: 'flex', alignItems: 'center', gap: '4px' }}>
              <span style={{ width: '6px', height: '6px', borderRadius: '50%', background: 'rgb(16, 185, 129)' }} />
              ready
            </span>
          </div>
        </div>
      ) : null}

      {/* Resizer Handle for Sidebar */}
      {sidebarOpen && (
        <div
          onPointerDown={onSidebarPointerDown}
          onPointerMove={onSidebarPointerMove}
          onPointerUp={onSidebarPointerUp}
          onPointerCancel={onSidebarPointerUp}
          style={{
            ...styles.sidebarResizer,
            ...(isResizingSidebar ? styles.resizerActive : {}),
          }}
          title="Drag to resize sidebar"
        />
      )}

      {/* Main Area: 3D Viewport & Tool Panels */}
      <div style={styles.mainArea}>
        {/* Top Control Bar */}
        <div style={styles.topBar}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            {!sidebarOpen && (
              <button
                onClick={() => setSidebarOpen(true)}
                style={styles.openSidebarBtn}
                title="Open Assets Catalog"
              >
                <IconSidebarLeft size={12} color="rgb(255, 255, 255)" />
                <span>Assets ({models.length})</span>
              </button>
            )}
            <span style={{ fontSize: '0.8rem', fontWeight: 600, color: 'rgb(241, 245, 249)' }}>
              {selectedModel ? getModelDisplayMeta(selectedModel).title : 'Select an Asset'}
            </span>
            {loading && <span style={{ fontSize: '0.72rem', color: 'rgb(251, 191, 36)' }}>Loading model...</span>}
            {error && <span style={{ fontSize: '0.72rem', color: 'rgb(248, 113, 113)' }}>{error}</span>}
          </div>

          {/* Transform Gizmo Controls in Scene / Editor Mode */}
          {(tab === 'scene' || tab === 'editor') && (
            <div style={styles.gizmoModeGroup}>
              <span style={{ fontSize: '0.68rem', color: 'rgb(100, 116, 139)', fontWeight: 600 }}>Gizmo:</span>
              <button
                onClick={() => setTransformMode('translate')}
                style={{
                  ...styles.gizmoBtn,
                  ...(transformMode === 'translate' ? styles.gizmoBtnActive : styles.gizmoBtnInactive),
                }}
                title="Translate (W)"
              >
                <IconTransform size={12} />
                <span>Move [W]</span>
              </button>
              <button
                onClick={() => setTransformMode('rotate')}
                style={{
                  ...styles.gizmoBtn,
                  ...(transformMode === 'rotate' ? styles.gizmoBtnActive : styles.gizmoBtnInactive),
                }}
                title="Rotate (E)"
              >
                <IconLoop size={12} />
                <span>Rotate [E]</span>
              </button>
              <button
                onClick={() => setTransformMode('scale')}
                style={{
                  ...styles.gizmoBtn,
                  ...(transformMode === 'scale' ? styles.gizmoBtnActive : styles.gizmoBtnInactive),
                }}
                title="Scale (R)"
              >
                <IconMesh size={12} />
                <span>Scale [R]</span>
              </button>
            </div>
          )}

          {/* Scene Map Selector & Actions in Scene Mode */}
          {tab === 'scene' && (
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              <span style={{ fontSize: '0.68rem', color: 'rgb(100, 116, 139)', fontWeight: 600 }}>Map:</span>
              <select
                value={selectedMapId}
                onChange={(e) => loadStudioMap(e.target.value)}
                onFocus={fetchStudioMaps}
                style={styles.mapSelect}
              >
                <option value="default">Starter Arena (Default)</option>
                {savedMaps.map((sm) => (
                  <option key={sm.id} value={sm.id}>
                    {sm.name} ({sm.nodes_count} obj)
                  </option>
                ))}
              </select>
              <button
                onClick={saveCurrentMap}
                style={styles.saveMapBtn}
                title="Save this map design to project assets/horribleAssault/maps"
              >
                <IconDownload size={11} color="rgb(255, 255, 255)" />
                <span>{saveStatus || 'Save Map'}</span>
              </button>
              <button
                onClick={handleExportScene}
                style={styles.sceneActionBtn}
                title="Export Map JSON to clipboard"
              >
                <IconDownload size={11} color="rgb(56, 189, 248)" />
                <span>Export</span>
              </button>
              <button
                onClick={() => {
                  registry.openPanel('hassault.play');
                }}
                style={styles.playGameBtn}
                title="Launch this arena in HorribleAssault gameplay"
              >
                <IconCrosshair size={12} color="rgb(255, 255, 255)" />
                <span>Play in Game</span>
              </button>
            </div>
          )}

          <div style={{ display: 'flex', alignItems: 'center', gap: '12px', fontSize: '0.72rem' }}>
            {/* Display Toggles */}
            <label style={{ display: 'flex', alignItems: 'center', gap: '4px', cursor: 'pointer', color: 'rgb(203, 213, 225)' }}>
              <input
                type="checkbox"
                checked={wireframe}
                onChange={(e) => setWireframe(e.target.checked)}
              />
              Wireframe
            </label>
            <label style={{ display: 'flex', alignItems: 'center', gap: '4px', cursor: 'pointer', color: 'rgb(203, 213, 225)' }}>
              <input
                type="checkbox"
                checked={showSkeleton}
                onChange={(e) => setShowSkeleton(e.target.checked)}
              />
              Bones
            </label>
            <label style={{ display: 'flex', alignItems: 'center', gap: '4px', cursor: 'pointer', color: 'rgb(203, 213, 225)' }}>
              <input
                type="checkbox"
                checked={showGrid}
                onChange={(e) => setShowGrid(e.target.checked)}
              />
              Grid
            </label>

            <select
              value={lightPreset}
              onChange={(e: any) => setLightPreset(e.target.value)}
              style={styles.select}
            >
              <option value="studio">Studio Light</option>
              <option value="outdoor">Outdoor Sun</option>
              <option value="combat">High Contrast</option>
            </select>
          </div>
        </div>

        {/* 3D Canvas Container & Outliner Split */}
        <div style={{ flex: 1, display: 'flex', position: 'relative', minHeight: 0 }}>
          <div style={styles.canvasWrapper}>
            <div
              ref={containerRef}
              style={{
                position: 'absolute',
                top: 0,
                left: 0,
                width: '100%',
                height: '100%',
                minWidth: 0,
                minHeight: 0,
              }}
            />

            {/* Quick HUD Overlay */}
            <div style={styles.hudOverlay}>
              <div>Meshes: <strong style={{ color: 'rgb(255, 255, 255)', fontFamily: 'monospace' }}>{meshCount}</strong></div>
              <div>Vertices: <strong style={{ color: 'rgb(255, 255, 255)', fontFamily: 'monospace' }}>{vertCount.toLocaleString()}</strong></div>
              <div>Triangles: <strong style={{ color: 'rgb(255, 255, 255)', fontFamily: 'monospace' }}>{triCount.toLocaleString()}</strong></div>
              <div>Bounds: <strong style={{ color: 'rgb(255, 255, 255)', fontFamily: 'monospace' }}>{boundsText}</strong></div>
              {boneList.length > 0 && <div>Bones: <strong style={{ color: 'rgb(56, 189, 248)', fontFamily: 'monospace' }}>{boneList.length}</strong></div>}
            </div>
          </div>

          {/* Right Outliner Drawer in Scene/Map Mode */}
          {tab === 'scene' && (
            <>
              <div
                onPointerDown={onOutlinerPointerDown}
                onPointerMove={onOutlinerPointerMove}
                onPointerUp={onOutlinerPointerUp}
                onPointerCancel={onOutlinerPointerUp}
                style={{
                  ...styles.outlinerResizer,
                  ...(isResizingOutliner ? styles.resizerActive : {}),
                }}
                title="Drag to resize Scene Outliner"
              />
              <div style={{ ...styles.outlinerDrawer, width: `${outlinerWidth}px` }}>
                <SceneOutliner
                  scene={sceneDoc}
                  selectedNodeId={selectedSceneNodeId}
                  onSelectNode={setSelectedSceneNodeId}
                  onToggleVisibility={handleToggleNodeVisibility}
                  onToggleLock={handleToggleNodeLock}
                  onDeleteNode={handleDeleteNode}
                  onAddEntity={handleAddEntity}
                />
              </div>
            </>
          )}
        </div>

        {/* Tab-Specific Bottom Inspector/Editor Bar */}
        {tab === 'materials' && (
          <TextureInspector
            modelName={selectedModel ? selectedModel.name : ''}
            textures={textureList}
            materials={materialNames}
            activeChannel={activeTextureChannel}
            onChannelChange={setActiveTextureChannel}
          />
        )}

        {tab === 'editor' && (
          <div style={styles.bottomPanel}>
            {/* Transform Controls */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', borderRight: '1px solid rgba(255,255,255,0.08)', paddingRight: '16px', minWidth: '200px' }}>
              <div style={{ fontWeight: 700, textTransform: 'uppercase', color: 'rgb(100, 116, 139)', fontSize: '0.68rem', letterSpacing: '0.05em' }}>Transform</div>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px' }}>
                <span style={{ color: 'rgb(148, 163, 184)' }}>Scale</span>
                <input
                  type="range"
                  min="0.01"
                  max="3.0"
                  step="0.01"
                  value={modelScale}
                  onChange={(e) => setModelScale(parseFloat(e.target.value))}
                  style={{ width: '80px' }}
                />
                <span style={{ fontFamily: 'monospace', width: '38px', textAlign: 'right' }}>{modelScale.toFixed(2)}x</span>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px' }}>
                <span style={{ color: 'rgb(148, 163, 184)' }}>Rot Y</span>
                <input
                  type="range"
                  min="-180"
                  max="180"
                  step="5"
                  value={modelRotY}
                  onChange={(e) => setModelRotY(parseFloat(e.target.value))}
                  style={{ width: '80px' }}
                />
                <span style={{ fontFamily: 'monospace', width: '38px', textAlign: 'right' }}>{modelRotY}°</span>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px' }}>
                <span style={{ color: 'rgb(148, 163, 184)' }}>Pos Y</span>
                <input
                  type="range"
                  min="-2"
                  max="2"
                  step="0.05"
                  value={modelPosY}
                  onChange={(e) => setModelPosY(parseFloat(e.target.value))}
                  style={{ width: '80px' }}
                />
                <span style={{ fontFamily: 'monospace', width: '38px', textAlign: 'right' }}>{modelPosY.toFixed(2)}</span>
              </div>
            </div>

            {/* Weapon Presets & Alignment */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', borderRight: '1px solid rgba(255,255,255,0.08)', paddingRight: '16px', minWidth: '180px' }}>
              <div style={{ fontWeight: 700, textTransform: 'uppercase', color: 'rgb(100, 116, 139)', fontSize: '0.68rem', letterSpacing: '0.05em' }}>Weapon Tools</div>
              <button
                onClick={alignDownNegZ}
                style={styles.btnSecondary}
              >
                Point Barrel Down -Z
              </button>
              <button
                onClick={centerToOrigin}
                style={styles.btnSecondary}
              >
                Center to Origin
              </button>
            </div>

            {/* Grip Anchors */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', minWidth: '220px' }}>
              <div style={{ fontWeight: 700, textTransform: 'uppercase', color: 'rgb(100, 116, 139)', fontSize: '0.68rem', letterSpacing: '0.05em' }}>Grip Anchors (cubes)</div>
              <div style={{ fontSize: '0.72rem', color: 'rgb(148, 163, 184)' }}>
                Primary Hand: <span style={{ fontFamily: 'monospace', color: 'rgb(56, 189, 248)' }}>[{primaryGrip.join(', ')}]</span>
              </div>
              <div style={{ fontSize: '0.72rem', color: 'rgb(148, 163, 184)' }}>
                Support Hand: <span style={{ fontFamily: 'monospace', color: 'rgb(52, 211, 153)' }}>[{supportGrip.join(', ')}]</span>
              </div>
              <button
                onClick={() => {
                  navigator.clipboard?.writeText(
                    JSON.stringify({ primary: primaryGrip, support: supportGrip }, null, 2),
                  );
                }}
                style={{ ...styles.btnSecondary, color: 'rgb(56, 189, 248)', fontFamily: 'monospace', fontSize: '0.7rem', display: 'flex', alignItems: 'center', gap: '6px' }}
              >
                <IconCopy size={12} color="rgb(56, 189, 248)" />
                <span>Copy grips.json format</span>
              </button>
            </div>
          </div>
        )}

        {tab === 'animator' && (
          <div style={{ ...styles.bottomPanel, flexDirection: 'column', gap: '8px' }}>
            {/* Clip Selector & Speed */}
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <span style={{ fontWeight: 700, color: 'rgb(203, 213, 225)' }}>Clip:</span>
                {clips.length > 0 ? (
                  <select
                    value={selectedClip}
                    onChange={(e) => handleClipChange(e.target.value)}
                    style={styles.select}
                  >
                    {clips.map((c) => (
                      <option key={c} value={c}>
                        {c}
                      </option>
                    ))}
                  </select>
                ) : (
                  <span style={{ color: 'rgb(100, 116, 139)', fontStyle: 'italic' }}>No animation clips in model</span>
                )}
              </div>

              <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                <span style={{ color: 'rgb(148, 163, 184)' }}>Speed:</span>
                {[0.25, 0.5, 1.0, 2.0].map((s) => (
                  <button
                    key={s}
                    onClick={() => setPlaybackSpeed(s)}
                    style={{
                      padding: '2px 6px',
                      borderRadius: '3px',
                      fontSize: '0.7rem',
                      fontFamily: 'monospace',
                      border: 'none',
                      cursor: 'pointer',
                      ...(playbackSpeed === s
                        ? { background: 'rgb(37, 99, 235)', color: 'rgb(255, 255, 255)', fontWeight: 'bold' }
                        : { background: 'rgba(255,255,255,0.06)', color: 'rgb(148, 163, 184)' }),
                    }}
                  >
                    {s}x
                  </button>
                ))}
              </div>
            </div>

            {/* Timeline Scrubber */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
              <button
                onClick={() => setIsPlaying(!isPlaying)}
                disabled={clips.length === 0}
                style={{
                  ...styles.btnPrimary,
                  opacity: clips.length === 0 ? 0.5 : 1,
                  padding: '4px 10px',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '5px',
                }}
              >
                {isPlaying ? <IconPause size={12} color="rgb(255, 255, 255)" /> : <IconPlay size={12} color="rgb(255, 255, 255)" />}
                <span>{isPlaying ? 'Pause' : 'Play'}</span>
              </button>

              <input
                type="range"
                min="0"
                max={animDuration}
                step="0.01"
                value={animTime}
                onChange={(e) => handleScrub(parseFloat(e.target.value))}
                disabled={clips.length === 0}
                style={{ flex: 1, cursor: 'pointer', accentColor: 'rgb(37, 99, 235)' }}
              />

              <span style={{ fontFamily: 'monospace', color: 'rgb(203, 213, 225)', width: '90px', textAlign: 'right' }}>
                {animTime.toFixed(2)}s / {animDuration.toFixed(2)}s
              </span>
            </div>

            {/* Normalized progress and info */}
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', fontSize: '0.7rem', color: 'rgb(148, 163, 184)' }}>
              <div>
                Progress: <span style={{ fontFamily: 'monospace', color: 'rgb(241, 245, 249)' }}>{((animTime / Math.max(0.001, animDuration)) * 100).toFixed(1)}%</span>
              </div>
              <label style={{ display: 'flex', alignItems: 'center', gap: '4px', cursor: 'pointer', color: 'rgb(203, 213, 225)' }}>
                <input
                  type="checkbox"
                  checked={loopAnim}
                  onChange={(e) => setLoopAnim(e.target.checked)}
                />
                Loop
              </label>
            </div>
          </div>
        )}

        {tab === 'viewer' && (
          <div style={styles.bottomPanel}>
            <div style={{ borderRight: '1px solid rgba(255,255,255,0.08)', paddingRight: '16px' }}>
              <div style={{ fontWeight: 700, textTransform: 'uppercase', color: 'rgb(100, 116, 139)', fontSize: '0.68rem', letterSpacing: '0.05em', marginBottom: '4px' }}>
                Materials ({materialNames.length})
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px', maxWidth: '380px' }}>
                {materialNames.slice(0, 8).map((m) => (
                  <span key={m} style={{ padding: '2px 5px', background: 'rgba(255,255,255,0.06)', color: 'rgb(203, 213, 225)', borderRadius: '3px', fontFamily: 'monospace', fontSize: '0.68rem' }}>
                    {m}
                  </span>
                ))}
                {materialNames.length > 8 && (
                  <span style={{ fontSize: '0.68rem', color: 'rgb(100, 116, 139)' }}>+{materialNames.length - 8} more</span>
                )}
              </div>
            </div>

            {boneList.length > 0 && (
              <div>
                <div style={{ fontWeight: 700, textTransform: 'uppercase', color: 'rgb(100, 116, 139)', fontSize: '0.68rem', letterSpacing: '0.05em', marginBottom: '4px' }}>
                  Skeleton Bones ({boneList.length})
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px', maxWidth: '450px' }}>
                  {boneList.slice(0, 10).map((b) => (
                    <span key={b} style={{ padding: '2px 5px', background: 'rgba(56,189,248,0.1)', color: 'rgb(56, 189, 248)', borderRadius: '3px', fontFamily: 'monospace', fontSize: '0.68rem' }}>
                      {b}
                    </span>
                  ))}
                  {boneList.length > 10 && (
                    <span style={{ fontSize: '0.68rem', color: 'rgb(100, 116, 139)' }}>+{boneList.length - 10} more</span>
                  )}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  root: {
    display: 'flex',
    width: '100%',
    height: '100%',
    background: 'rgb(10, 14, 23)',
    color: 'rgb(241, 245, 249)',
    fontFamily: 'system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
    overflow: 'hidden',
    userSelect: 'none',
    minWidth: 0,
    minHeight: 0,
  },
  sidebar: {
    borderRight: '1px solid rgba(255, 255, 255, 0.08)',
    background: 'rgb(13, 18, 29)',
    display: 'flex',
    flexDirection: 'column',
    flexShrink: 0,
    zIndex: 10,
    minHeight: 0,
  },
  sidebarResizer: {
    width: '6px',
    cursor: 'col-resize',
    background: 'rgba(255, 255, 255, 0.04)',
    borderRight: '1px solid rgba(255, 255, 255, 0.08)',
    transition: 'background 0.15s',
    zIndex: 20,
    flexShrink: 0,
    touchAction: 'none',
  },
  outlinerResizer: {
    width: '6px',
    cursor: 'col-resize',
    background: 'rgba(255, 255, 255, 0.04)',
    borderLeft: '1px solid rgba(255, 255, 255, 0.08)',
    transition: 'background 0.15s',
    zIndex: 20,
    flexShrink: 0,
    touchAction: 'none',
  },
  resizerActive: {
    background: 'rgb(59, 130, 246)',
  },
  mapSelect: {
    background: 'rgb(15, 23, 42)',
    border: '1px solid rgba(255, 255, 255, 0.15)',
    borderRadius: '4px',
    color: 'rgb(241, 245, 249)',
    fontSize: '0.68rem',
    padding: '3px 6px',
    outline: 'none',
    cursor: 'pointer',
  },
  saveMapBtn: {
    display: 'flex',
    alignItems: 'center',
    gap: '4px',
    background: 'rgb(37, 99, 235)',
    border: '1px solid rgb(59, 130, 246)',
    borderRadius: '4px',
    padding: '3px 8px',
    fontSize: '0.68rem',
    fontWeight: 600,
    color: 'rgb(255, 255, 255)',
    cursor: 'pointer',
    transition: 'background 0.12s',
  },
  playGameBtn: {
    display: 'flex',
    alignItems: 'center',
    gap: '5px',
    background: 'rgb(22, 163, 74)',
    border: '1px solid rgb(34, 197, 94)',
    borderRadius: '4px',
    padding: '3px 8px',
    fontSize: '0.68rem',
    fontWeight: 600,
    color: 'rgb(255, 255, 255)',
    cursor: 'pointer',
    transition: 'background 0.12s',
  },
  sidebarHeader: {
    padding: '8px 10px',
    borderBottom: '1px solid rgba(255, 255, 255, 0.08)',
    flexShrink: 0,
  },
  tabRow: {
    display: 'flex',
    flexWrap: 'wrap',
    background: 'rgba(0, 0, 0, 0.35)',
    borderRadius: '6px',
    padding: '3px',
    marginTop: '6px',
    gap: '3px',
  },
  tabBtn: {
    flex: '1 1 auto',
    minWidth: '50px',
    padding: '5px 8px',
    fontSize: '0.68rem',
    fontWeight: 600,
    borderRadius: '4px',
    border: 'none',
    cursor: 'pointer',
    textAlign: 'center',
    transition: 'background 0.15s, color 0.15s',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    gap: '4px',
    whiteSpace: 'nowrap',
  },
  tabBtnActive: {
    background: 'rgb(37, 99, 235)',
    color: 'rgb(255, 255, 255)',
  },
  tabBtnInactive: {
    background: 'transparent',
    color: 'rgb(148, 163, 184)',
  },
  searchInput: {
    width: '100%',
    boxSizing: 'border-box',
    background: 'rgb(7, 10, 16)',
    border: '1px solid rgba(255, 255, 255, 0.1)',
    borderRadius: '6px',
    color: 'rgb(241, 245, 249)',
    fontSize: '0.7rem',
    padding: '5px 22px 5px 24px',
    outline: 'none',
  },
  clearSearchBtn: {
    position: 'absolute',
    right: '6px',
    top: '50%',
    transform: 'translateY(-50%)',
    background: 'transparent',
    border: 'none',
    cursor: 'pointer',
    padding: '2px',
    display: 'flex',
    alignItems: 'center',
  },
  catalog: {
    flex: 1,
    overflowY: 'auto',
    padding: '6px 8px',
    minHeight: 0,
  },
  catHeaderBtn: {
    width: '100%',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '5px 6px',
    background: 'rgba(255, 255, 255, 0.03)',
    borderRadius: '4px',
    border: 'none',
    cursor: 'pointer',
    fontSize: '0.66rem',
    fontWeight: 700,
    textTransform: 'uppercase',
    color: 'rgb(148, 163, 184)',
    letterSpacing: '0.04em',
    userSelect: 'none',
    marginBottom: '2px',
  },
  catCountBadge: {
    fontFamily: 'monospace',
    fontSize: '0.62rem',
    background: 'rgba(255, 255, 255, 0.08)',
    padding: '1px 5px',
    borderRadius: '8px',
    color: 'rgb(203, 213, 225)',
  },
  modelCard: {
    display: 'flex',
    alignItems: 'center',
    padding: '4px 6px',
    borderRadius: '5px',
    cursor: 'pointer',
    transition: 'all 0.12s ease',
    userSelect: 'none',
    boxSizing: 'border-box',
  },
  modelCardActive: {
    background: 'linear-gradient(90deg, rgba(37, 99, 235, 0.28) 0%, rgba(30, 58, 138, 0.15) 100%)',
    borderLeft: '3px solid rgb(59, 130, 246)',
    borderTop: '1px solid rgba(59, 130, 246, 0.25)',
    borderRight: '1px solid rgba(59, 130, 246, 0.2)',
    borderBottom: '1px solid rgba(59, 130, 246, 0.2)',
  },
  modelCardInactive: {
    background: 'transparent',
    borderLeft: '3px solid transparent',
    borderTop: '1px solid transparent',
    borderRight: '1px solid transparent',
    borderBottom: '1px solid transparent',
  },
  modelIconBox: {
    width: '24px',
    height: '24px',
    borderRadius: '4px',
    background: 'rgba(255, 255, 255, 0.05)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    marginRight: '6px',
    flexShrink: 0,
  },
  formatBadge: {
    fontSize: '0.6rem',
    fontFamily: 'monospace',
    textTransform: 'uppercase',
    padding: '1px 4px',
    borderRadius: '3px',
    flexShrink: 0,
  },
  quickPlaceBtn: {
    background: 'rgba(52, 211, 153, 0.15)',
    border: '1px solid rgba(52, 211, 153, 0.3)',
    borderRadius: '3px',
    padding: '2px 4px',
    cursor: 'pointer',
    display: 'flex',
    alignItems: 'center',
  },
  sidebarFooter: {
    padding: '6px 10px',
    borderTop: '1px solid rgba(255, 255, 255, 0.08)',
    fontSize: '0.64rem',
    color: 'rgb(100, 116, 139)',
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    background: 'rgb(10, 14, 23)',
    flexShrink: 0,
  },
  iconBtn: {
    background: 'transparent',
    border: 'none',
    color: 'rgb(100, 116, 139)',
    cursor: 'pointer',
    padding: '3px',
    borderRadius: '3px',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
  },
  openSidebarBtn: {
    background: 'rgb(29, 78, 216)',
    color: 'rgb(255, 255, 255)',
    border: 'none',
    borderRadius: '4px',
    padding: '3px 8px',
    fontSize: '0.7rem',
    fontWeight: 600,
    cursor: 'pointer',
    display: 'flex',
    alignItems: 'center',
    gap: '4px',
  },
  gizmoModeGroup: {
    display: 'flex',
    alignItems: 'center',
    gap: '4px',
    background: 'rgba(0, 0, 0, 0.3)',
    padding: '2px 6px',
    borderRadius: '4px',
    border: '1px solid rgba(255, 255, 255, 0.08)',
  },
  gizmoBtn: {
    display: 'flex',
    alignItems: 'center',
    gap: '4px',
    border: 'none',
    borderRadius: '3px',
    padding: '2px 6px',
    fontSize: '0.66rem',
    fontWeight: 600,
    cursor: 'pointer',
    transition: 'background 0.12s',
  },
  gizmoBtnActive: {
    background: 'rgb(37, 99, 235)',
    color: 'rgb(255, 255, 255)',
  },
  gizmoBtnInactive: {
    background: 'transparent',
    color: 'rgb(148, 163, 184)',
  },
  sceneActionBtn: {
    display: 'flex',
    alignItems: 'center',
    gap: '4px',
    background: 'rgba(56, 189, 248, 0.15)',
    border: '1px solid rgba(56, 189, 248, 0.3)',
    color: 'rgb(56, 189, 248)',
    borderRadius: '4px',
    padding: '2px 8px',
    fontSize: '0.68rem',
    fontWeight: 600,
    cursor: 'pointer',
  },
  mainArea: {
    flex: 1,
    display: 'flex',
    flexDirection: 'column',
    position: 'relative',
    overflow: 'hidden',
    minWidth: 0,
    minHeight: 0,
  },
  topBar: {
    height: '38px',
    borderBottom: '1px solid rgba(255, 255, 255, 0.08)',
    background: 'rgb(13, 18, 29)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '0 12px',
    flexShrink: 0,
    zIndex: 10,
  },
  canvasWrapper: {
    flex: 1,
    position: 'relative',
    width: '100%',
    minHeight: 0,
    background: 'rgb(7, 10, 16)',
    overflow: 'hidden',
  },
  outlinerDrawer: {
    width: '260px',
    borderLeft: '1px solid rgba(255, 255, 255, 0.08)',
    background: 'rgb(13, 18, 29)',
    display: 'flex',
    flexDirection: 'column',
    minHeight: 0,
    zIndex: 10,
    flexShrink: 0,
  },
  hudOverlay: {
    position: 'absolute',
    top: '10px',
    left: '10px',
    background: 'rgba(13, 18, 29, 0.85)',
    backdropFilter: 'blur(8px)',
    border: '1px solid rgba(255, 255, 255, 0.1)',
    borderRadius: '6px',
    padding: '8px 12px',
    fontSize: '0.7rem',
    color: 'rgb(148, 163, 184)',
    pointerEvents: 'none',
    zIndex: 5,
    lineHeight: 1.5,
  },
  bottomPanel: {
    borderTop: '1px solid rgba(255, 255, 255, 0.08)',
    background: 'rgb(13, 18, 29)',
    padding: '8px 12px',
    display: 'flex',
    gap: '16px',
    overflowX: 'auto',
    flexShrink: 0,
    zIndex: 10,
    fontSize: '0.72rem',
  },
  select: {
    background: 'rgb(21, 28, 42)',
    border: '1px solid rgba(255, 255, 255, 0.12)',
    color: 'rgb(226, 232, 240)',
    fontSize: '0.72rem',
    borderRadius: '4px',
    padding: '2px 6px',
    outline: 'none',
  },
  btnPrimary: {
    background: 'rgb(37, 99, 235)',
    color: 'rgb(255, 255, 255)',
    border: 'none',
    borderRadius: '4px',
    padding: '4px 10px',
    cursor: 'pointer',
    fontWeight: 600,
    fontSize: '0.72rem',
  },
  btnSecondary: {
    background: 'rgba(255, 255, 255, 0.06)',
    color: 'rgb(203, 213, 225)',
    border: '1px solid rgba(255, 255, 255, 0.1)',
    borderRadius: '4px',
    padding: '4px 8px',
    cursor: 'pointer',
    fontSize: '0.7rem',
    textAlign: 'left',
  },
};

export function ModelViewerPanel() {
  return <ModelStudioPanel initialTab="viewer" />;
}

export function ModelEditorPanel() {
  return <ModelStudioPanel initialTab="editor" />;
}

export function AnimationEditorPanel() {
  return <ModelStudioPanel initialTab="animator" />;
}
