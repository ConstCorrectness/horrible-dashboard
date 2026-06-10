import { useEffect, useState, useRef, useCallback } from 'react';

import { apiGet } from '../../api';

interface Health {
  status: string;
  app: string;
  version: string;
}

export function WelcomeWidget() {
  return (
    <div>
      <p>
        Welcome to <strong>horrible-dashboard</strong> — your one-stop app for everything.
      </p>
      <p>
        Press <kbd>Ctrl</kbd>+<kbd>K</kbd> for the command palette.
      </p>
    </div>
  );
}

export function BackendStatusWidget() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const poll = () => {
      apiGet<Health>('/health')
        .then((h) => {
          if (cancelled) return;
          setHealth(h);
          setError(null);
        })
        .catch((e: unknown) => {
          if (cancelled) return;
          setHealth(null);
          setError(String(e));
        });
    };
    poll();
    const timer = setInterval(poll, 10_000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  if (error) {
    return <p className="widget-error">Backend unreachable — is it running on port 8000?</p>;
  }
  if (!health) return <p>Checking…</p>;
  return (
    <p>
      Backend <strong>{health.status}</strong> — {health.app} v{health.version}
    </p>
  );
}


export function GameWidget() {
  const [running, setRunning] = useState(true);
  const [esp, setEsp] = useState(true);
  const [aimbot, setAimbot] = useState(false);
  const [infHealth, setInfHealth] = useState(false);
  const [infAmmo, setInfAmmo] = useState(false);
  const [noRecoil, setNoRecoil] = useState(false);
  const [wallhack, setWallhack] = useState(false);
  const [autoplay, setAutoplay] = useState(true);

  // Status stats
  const [fps, setFps] = useState(60);
  const [cpu, setCpu] = useState(14.2);
  const [mem, setMem] = useState(485);
  const [pid] = useState(4912);

  const [logs, setLogs] = useState<{ id: string; time: string; text: string; type: string }[]>([]);

  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const logsEndRef = useRef<HTMLDivElement | null>(null);

  const lastShotTimeRef = useRef<number>(0);
  const particlesRef = useRef<{ x: number; y: number; vx: number; vy: number; life: number; maxLife: number; color: string; size: number }[]>([]);

  const enemiesRef = useRef([
    { id: '1', name: 'ac_camper_99', x: -35, y: 0, z: 120, hp: 100, maxHp: 100, active: true, speed: 0.15 },
    { id: '2', name: 'frag_master', x: 25, y: 5, z: 80, hp: 100, maxHp: 100, active: true, speed: -0.25 },
    { id: '3', name: 'cubeslayer', x: -10, y: -5, z: 160, hp: 100, maxHp: 100, active: true, speed: 0.08 },
    { id: '4', name: 'n00b_destroyer', x: 40, y: 10, z: 100, hp: 100, maxHp: 100, active: true, speed: -0.12 },
  ]);

  const playerStateRef = useRef({
    cameraZ: 0,
    cameraX: 0,
    yaw: 0,
    pitch: 0,
    recoil: 0,
    ammo: 30,
    health: 100,
    score: 0,
    frags: 0,
    reloading: false,
    reloadProgress: 0,
  });

  const runningRef = useRef(running);
  const espRef = useRef(esp);
  const aimbotRef = useRef(aimbot);
  const infHealthRef = useRef(infHealth);
  const infAmmoRef = useRef(infAmmo);
  const noRecoilRef = useRef(noRecoil);
  const wallhackRef = useRef(wallhack);
  const autoplayRef = useRef(autoplay);

  const addLog = useCallback((text: string, type = 'info') => {
    const time = new Date().toLocaleTimeString().split(' ')[0];
    const newLog = {
      id: Math.random().toString(36).substring(2, 9),
      time,
      text,
      type,
    };
    setLogs((prev) => [...prev.slice(-30), newLog]);
  }, []);

  const addLogRef = useRef(addLog);
  useEffect(() => {
    addLogRef.current = addLog;
  }, [addLog]);

  // Sync refs
  useEffect(() => {
    runningRef.current = running;
    espRef.current = esp;
    aimbotRef.current = aimbot;
    infHealthRef.current = infHealth;
    infAmmoRef.current = infAmmo;
    noRecoilRef.current = noRecoil;
    wallhackRef.current = wallhack;
    autoplayRef.current = autoplay;
  }, [running, esp, aimbot, infHealth, infAmmo, noRecoil, wallhack, autoplay]);

  // Scroll logs to bottom
  useEffect(() => {
    if (logsEndRef.current) {
      logsEndRef.current.scrollTop = logsEndRef.current.scrollHeight;
    }
  }, [logs]);

  // Handle manual canvas click (shooting)
  const handleCanvasClick = () => {
    if (!running) return;
    if (playerStateRef.current.reloading) return;
    fireWeapon();
  };

  const fireWeapon = () => {
    const now = performance.now();
    const state = playerStateRef.current;
    
    if (state.reloading) return;

    if (!infAmmoRef.current) {
      if (state.ammo <= 0) {
        state.reloading = true;
        state.reloadProgress = 0;
        addLogRef.current('[GAME] Out of ammo! Reloading...', 'warn');
        return;
      }
      state.ammo -= 1;
    }

    lastShotTimeRef.current = now;
    state.recoil = noRecoilRef.current ? 0 : 1.0;

    // Check hit
    let hit = false;
    const FOV = 300;
    
    enemiesRef.current.forEach((enemy) => {
      if (!enemy.active) return;
      
      const dx = enemy.x - state.cameraX;
      const dz = enemy.z - state.cameraZ;
      const dy = enemy.y;

      const rx = dx * Math.cos(-state.yaw) - dz * Math.sin(-state.yaw);
      const rz = dx * Math.sin(-state.yaw) + dz * Math.cos(-state.yaw);
      const ry = dy - state.pitch * rz;

      if (rz > 5) {
        const scale = FOV / rz;
        const screenX = 640 / 2 + rx * scale;
        const screenY = 360 / 2 - ry * scale;
        const size = 25 * scale;

        // Check distance from crosshair (320, 180)
        const dist = Math.sqrt((screenX - 320) ** 2 + (screenY - 180) ** 2);
        if (dist < size * 0.7) {
          hit = true;
          const dmg = 35;
          enemy.hp = Math.max(0, enemy.hp - dmg);

          // Hit particles
          for (let i = 0; i < 8; i++) {
            particlesRef.current.push({
              x: screenX,
              y: screenY,
              vx: (Math.random() - 0.5) * 160,
              vy: (Math.random() - 0.5) * 160,
              life: 0.2 + Math.random() * 0.2,
              maxLife: 0.4,
              color: '#f43f5e',
              size: 2 + Math.random() * 2
            });
          }

          if (enemy.hp <= 0) {
            enemy.active = false;
            state.frags += 1;
            state.score += 100;
            
            // Explosion particles
            for (let i = 0; i < 20; i++) {
              particlesRef.current.push({
                x: screenX,
                y: screenY,
                vx: (Math.random() - 0.5) * 320,
                vy: (Math.random() - 0.5) * 320,
                life: 0.4 + Math.random() * 0.4,
                maxLife: 0.8,
                color: '#ec4899',
                size: 3 + Math.random() * 3
              });
            }
            addLogRef.current(`[GAME] Fragged: ${enemy.name} (+100 SCORE)`, 'info');
          } else {
            addLogRef.current(`[GAME] Target Hit: ${enemy.name} (HP: ${enemy.hp}/100)`, 'debug');
          }
        }
      }
    });

    if (!hit) {
      addLogRef.current('[GAME] Shot fired (missed)', 'debug');
    }
  };

  // Setup initial logs and loop
  useEffect(() => {
    addLog('Harness initialized. Connecting to AssaultCube memory map...', 'info');
    setTimeout(() => addLog('Memory map found at offset 0x00400000', 'info'), 400);
    setTimeout(() => addLog('Process assaultcube_harness.exe successfully attached [PID 4912]', 'info'), 800);
    setTimeout(() => addLog('Cheats module loaded. Awaiting client actions.', 'info'), 1200);
  }, []);

  // CPU/MEM/FPS simulated fluctuations
  useEffect(() => {
    if (!running) return;
    const interval = setInterval(() => {
      setFps(Number((59.4 + Math.random() * 1.1).toFixed(1)));
      setCpu(Number((12.5 + Math.random() * 4.5).toFixed(1)));
      setMem(Number((482 + Math.random() * 8 - 4).toFixed(0)));
    }, 1500);
    return () => clearInterval(interval);
  }, [running]);

  // Periodic simulated game event logs (network ping, bot joins, chat)
  useEffect(() => {
    if (!running) return;
    const events = [
      () => addLog('[NET] Ping: 38ms | loss: 0.0%', 'debug'),
      () => addLog('[GAME] Player bot_reaper joined the server', 'info'),
      () => addLog('[GAME] Map rotation: ac_depot cgz loaded', 'debug'),
      () => addLog('[NET] Syncing client player coords with server...', 'debug'),
      () => addLog('[GAME] bot_reaper: "gg nice shot"', 'info'),
    ];
    const interval = setInterval(() => {
      const idx = Math.floor(Math.random() * events.length);
      events[idx]();
    }, 7000);
    return () => clearInterval(interval);
  }, [running, addLog]);

  // Main game rendering loop
  useEffect(() => {
    if (!running) return;
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    let animationId: number;
    let lastTime = performance.now();
    const FOV = 300;

    const gameLoop = (now: number) => {
      const dt = (now - lastTime) / 1000;
      lastTime = now;

      // 1. UPDATE STATE
      const state = playerStateRef.current;

      // Handle weapon reloading
      if (state.reloading) {
        state.reloadProgress += dt * 0.7; // Takes ~1.4s
        if (state.reloadProgress >= 1.0) {
          state.reloading = false;
          state.ammo = 30;
          addLogRef.current('[GAME] Reload complete.', 'info');
        }
      }

      // Decay recoil
      state.recoil = Math.max(0, state.recoil - dt * 6);

      // Move player camera forward (Z axis)
      const speed = 25 * (wallhackRef.current ? 0.6 : 1.0); // slow down to see structures in wallhack
      state.cameraZ += dt * speed;

      // Camera sway
      const swayX = Math.sin(state.cameraZ * 0.03) * 12;
      state.cameraX = swayX;

      // Check health decay if enemy is too close
      enemiesRef.current.forEach((enemy) => {
        if (!enemy.active) return;
        
        // If enemy is passed behind camera, respawn ahead
        if (enemy.z < state.cameraZ - 10) {
          enemy.z = state.cameraZ + 140 + Math.random() * 60;
          enemy.x = (Math.random() - 0.5) * 80;
          enemy.hp = 100;
          enemy.active = true;
          return;
        }

        // Check if enemy hit player
        const distZ = enemy.z - state.cameraZ;
        const distX = enemy.x - state.cameraX;
        if (distZ > 0 && distZ < 15 && Math.abs(distX) < 15) {
          if (!infHealthRef.current) {
            state.health = Math.max(0, state.health - dt * 25);
            if (state.health <= 0) {
              addLogRef.current('[GAME] Local player killed by bot. Respawning...', 'error');
              state.health = 100;
              state.cameraZ = 0;
              state.score = Math.max(0, state.score - 50);
            }
          }
        }
      });

      // Update enemy positions (sideways movement)
      enemiesRef.current.forEach((enemy) => {
        if (!enemy.active) {
          // Respawn after 2 seconds
          if (Math.random() < 0.01) {
            enemy.z = state.cameraZ + 130 + Math.random() * 50;
            enemy.x = (Math.random() - 0.5) * 70;
            enemy.hp = 100;
            enemy.active = true;
          }
          return;
        }
        enemy.x += enemy.speed * dt * 45;
        if (enemy.x > 50) {
          enemy.x = 50;
          enemy.speed = -Math.abs(enemy.speed);
        } else if (enemy.x < -50) {
          enemy.x = -50;
          enemy.speed = Math.abs(enemy.speed);
        }
      });

      // Autoplay target tracking
      let bestTarget: typeof enemiesRef.current[0] | null = null;
      let minDistance = 9999;
      
      enemiesRef.current.forEach((enemy) => {
        if (!enemy.active || enemy.z <= state.cameraZ) return;
        const dz = enemy.z - state.cameraZ;
        if (dz < minDistance) {
          minDistance = dz;
          bestTarget = enemy;
        }
      });

      if (bestTarget) {
        const target: typeof enemiesRef.current[0] = bestTarget;
        const dx = target.x - state.cameraX;
        const dz = target.z - state.cameraZ;
        const targetYaw = Math.atan2(dx, dz);
        const targetPitch = Math.atan2(target.y, Math.sqrt(dx*dx + dz*dz));

        if (aimbotRef.current || autoplayRef.current) {
          // Lock yaw/pitch with smoothing
          state.yaw += (targetYaw - state.yaw) * dt * 7;
          state.pitch += (targetPitch - state.pitch) * dt * 7;

          // Autoplay auto-shoot logic
          if (autoplayRef.current && !state.reloading) {
            const angleDiff = Math.sqrt((state.yaw - targetYaw)**2 + (state.pitch - targetPitch)**2);
            if (angleDiff < 0.06 && now - lastShotTimeRef.current > 350) {
              fireWeapon();
            }
          }
        }
      } else {
        // Natural camera sway when no targets
        if (!aimbotRef.current && !autoplayRef.current) {
          state.yaw += (Math.sin(state.cameraZ * 0.01) * 0.1 - state.yaw) * dt * 2;
          state.pitch += (-state.pitch) * dt * 2;
        }
      }

      // Update particles
      particlesRef.current = particlesRef.current.filter((p) => {
        p.x += p.vx * dt;
        p.y += p.vy * dt;
        p.life -= dt;
        return p.life > 0;
      });

      // 2. RENDER GAME CANVAS
      ctx.fillStyle = '#06080c';
      ctx.fillRect(0, 0, 640, 360);

      // Add a retro starfield/dot field background
      ctx.fillStyle = 'rgba(255, 255, 255, 0.15)';
      for (let i = 0; i < 20; i++) {
        const sx = ((Math.sin(i * 123 + state.cameraZ * 0.005) + 1) * 320 - state.yaw * 300) % 640;
        const sy = ((Math.cos(i * 456) + 1) * 180 + state.pitch * 300) % 360;
        if (sy > 0 && sy < 180) { // Keep stars in sky
          ctx.fillRect(sx, sy, 1.5, 1.5);
        }
      }

      // Vanishing point coordinates
      const vpX = 640 / 2 - state.yaw * 300;
      const vpY = 360 / 2 + state.pitch * 300;

      // Draw horizon line
      ctx.strokeStyle = 'rgba(110, 168, 254, 0.1)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(0, vpY);
      ctx.lineTo(640, vpY);
      ctx.stroke();

      // Draw floor grid perspective lines
      ctx.strokeStyle = wallhackRef.current ? 'rgba(46, 213, 115, 0.12)' : 'rgba(110, 168, 254, 0.08)';
      const numGrid = 16;
      for (let i = 0; i <= numGrid; i++) {
        const ratio = i / numGrid;
        const startX = vpX;
        const startY = vpY;
        const endX = (ratio - 0.5) * 5 * 640 + 640 / 2;
        const endY = 360;
        ctx.beginPath();
        ctx.moveTo(startX, startY);
        ctx.lineTo(endX, endY);
        ctx.stroke();
      }

      // Draw scrolling horizontal floor lines
      const floorTimer = state.cameraZ * 0.15;
      const numHoriz = 8;
      for (let i = 0; i < numHoriz; i++) {
        const offset = (i + (floorTimer % 1.0)) / numHoriz;
        const lineY = vpY + Math.pow(offset, 2.5) * (360 - vpY);
        ctx.beginPath();
        ctx.moveTo(0, lineY);
        ctx.lineTo(640, lineY);
        ctx.stroke();
      }

      // Draw wireframe structural walls if Wallhack/Wireframe is on
      if (wallhackRef.current) {
        ctx.strokeStyle = 'rgba(46, 213, 115, 0.25)';
        ctx.lineWidth = 1.5;
        // Draw recurring side pillars
        const pSpacing = 50;
        const startPillarIndex = Math.floor(state.cameraZ / pSpacing);
        for (let i = 0; i < 5; i++) {
          const pZ = (startPillarIndex + i) * pSpacing;
          const relativeZ = pZ - state.cameraZ;
          if (relativeZ < 5) continue;

          const scale = FOV / relativeZ;
          // Left pillar
          const rxL = -45 - state.cameraX;
          const ryL = -state.pitch * relativeZ;
          const sXL = 640 / 2 + rxL * scale;
          const sYL = 360 / 2 - ryL * scale;
          const hL = 40 * scale;

          // Right pillar
          const rxR = 45 - state.cameraX;
          const sXR = 640 / 2 + rxR * scale;

          // Render left column
          ctx.strokeRect(sXL - 3 * scale, sYL - hL, 6 * scale, hL);
          // Render right column
          ctx.strokeRect(sXR - 3 * scale, sYL - hL, 6 * scale, hL);
        }
      }

      // Draw enemies
      enemiesRef.current.forEach((enemy) => {
        if (!enemy.active) return;

        const dx = enemy.x - state.cameraX;
        const dz = enemy.z - state.cameraZ;
        const dy = enemy.y;

        const rx = dx * Math.cos(-state.yaw) - dz * Math.sin(-state.yaw);
        const rz = dx * Math.sin(-state.yaw) + dz * Math.cos(-state.yaw);
        const ry = dy - state.pitch * rz;

        if (rz > 5) {
          const scale = FOV / rz;
          const screenX = 640 / 2 + rx * scale;
          const screenY = 360 / 2 - ry * scale;
          const size = 25 * scale;

          // Draw floating octahedron enemy drone
          ctx.strokeStyle = wallhackRef.current ? 'rgba(239, 68, 68, 0.4)' : '#ec4899';
          ctx.lineWidth = 1.5;
          ctx.beginPath();
          // Top half pyramid
          ctx.moveTo(screenX, screenY - size / 2);
          ctx.lineTo(screenX - size / 3, screenY);
          ctx.lineTo(screenX, screenY + size / 6);
          ctx.lineTo(screenX + size / 3, screenY);
          ctx.lineTo(screenX, screenY - size / 2);
          // Bottom half pyramid
          ctx.lineTo(screenX, screenY + size / 2);
          ctx.lineTo(screenX - size / 3, screenY);
          ctx.moveTo(screenX, screenY + size / 2);
          ctx.lineTo(screenX, screenY + size / 6);
          ctx.moveTo(screenX, screenY + size / 2);
          ctx.lineTo(screenX + size / 3, screenY);
          // Mid connections
          ctx.moveTo(screenX - size / 3, screenY);
          ctx.lineTo(screenX, screenY + size / 6);
          ctx.lineTo(screenX + size / 3, screenY);
          ctx.stroke();

          // ESP Overlays
          if (espRef.current) {
            ctx.strokeStyle = '#2ed573';
            ctx.lineWidth = 1.0;
            ctx.strokeRect(screenX - size / 2.5, screenY - size / 2, size * 0.8, size);

            // Bounding box corners
            ctx.fillStyle = '#2ed573';
            ctx.font = '8px monospace';
            ctx.fillText(enemy.name, screenX - size / 2.5, screenY - size / 2 - 12);
            ctx.fillText(`DST: ${Math.round(rz)}m HP: ${enemy.hp}%`, screenX - size / 2.5, screenY - size / 2 - 3);

            // Tiny health bar
            ctx.fillStyle = 'rgba(0, 0, 0, 0.6)';
            ctx.fillRect(screenX - size / 2.5, screenY - size / 2 - 18, size * 0.8, 2.5);
            ctx.fillStyle = '#2ed573';
            ctx.fillRect(screenX - size / 2.5, screenY - size / 2 - 18, (size * 0.8) * (enemy.hp / 100), 2.5);
          }
        }
      });

      // Draw tracers & particles
      ctx.lineWidth = 1.5;
      particlesRef.current.forEach((p) => {
        ctx.fillStyle = p.color;
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
        ctx.fill();
      });

      // Draw laser tracer if fired within last 80ms
      const shotAge = now - lastShotTimeRef.current;
      if (shotAge < 80) {
        ctx.strokeStyle = 'rgba(255, 235, 120, 0.85)';
        ctx.lineWidth = 3 - (shotAge / 80) * 2;
        ctx.beginPath();
        ctx.moveTo(435 - state.recoil * 15, 330 + state.recoil * 25);
        ctx.lineTo(320, 180);
        ctx.stroke();

        // Screen flash effect
        ctx.fillStyle = 'rgba(255, 255, 255, 0.08)';
        ctx.fillRect(0, 0, 640, 360);
      }

      // Draw gun
      const rec = state.recoil;
      const gunX = 430 - rec * 20;
      const gunY = 280 + rec * 40;

      ctx.strokeStyle = '#6ea8fe';
      ctx.lineWidth = 2;
      ctx.fillStyle = 'rgba(29, 32, 38, 0.8)';
      ctx.beginPath();
      // Rifle body path
      ctx.moveTo(gunX, 360);
      ctx.lineTo(gunX - 25, gunY + 45);
      ctx.lineTo(gunX - 45, gunY + 15);
      ctx.lineTo(gunX - 110, gunY + 12); // Long barrel top
      ctx.lineTo(gunX - 110, gunY + 20); // Barrel tip
      ctx.lineTo(gunX - 45, gunY + 25); // Lower barrel
      ctx.lineTo(gunX - 35, gunY + 55); // Stock grip
      ctx.closePath();
      ctx.fill();
      ctx.stroke();

      // Gun Scope details
      ctx.beginPath();
      ctx.moveTo(gunX - 65, gunY + 7);
      ctx.lineTo(gunX - 50, gunY + 7);
      ctx.lineTo(gunX - 50, gunY + 12);
      ctx.lineTo(gunX - 65, gunY + 12);
      ctx.closePath();
      ctx.stroke();

      // Muzzle flash on gun tip
      if (shotAge < 60) {
        ctx.fillStyle = '#facc15';
        ctx.shadowColor = '#eab308';
        ctx.shadowBlur = 15;
        ctx.beginPath();
        ctx.arc(gunX - 110, gunY + 16, 15, 0, Math.PI * 2);
        ctx.fill();
        ctx.shadowBlur = 0; // reset shadow
      }

      // Aimbot Laser Guide
      if (aimbotRef.current) {
        ctx.strokeStyle = 'rgba(239, 68, 68, 0.3)';
        ctx.lineWidth = 1.0;
        ctx.beginPath();
        ctx.moveTo(gunX - 110, gunY + 16);
        ctx.lineTo(320, 180);
        ctx.stroke();
      }

      // Draw crosshair
      const lockOn = enemiesRef.current.some((enemy) => {
        if (!enemy.active) return false;
        const dx = enemy.x - state.cameraX;
        const dz = enemy.z - state.cameraZ;
        const scale = FOV / dz;
        const screenX = 640 / 2 + (dx * Math.cos(-state.yaw) - dz * Math.sin(-state.yaw)) * scale;
        const screenY = 360 / 2 - (enemy.y - state.pitch * dz) * scale;
        const dist = Math.sqrt((screenX - 320)**2 + (screenY - 180)**2);
        return dz > 5 && dist < 25 * scale * 0.7;
      });

      ctx.strokeStyle = lockOn ? '#ef4444' : '#2ed573';
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      // Inner dot
      ctx.arc(320, 180, 1.5, 0, Math.PI * 2);
      ctx.fillStyle = lockOn ? '#ef4444' : '#2ed573';
      ctx.fill();
      // Outer reticles
      ctx.moveTo(320 - 8, 180); ctx.lineTo(320 - 3, 180);
      ctx.moveTo(320 + 3, 180); ctx.lineTo(320 + 8, 180);
      ctx.moveTo(320, 180 - 8); ctx.lineTo(320, 180 - 3);
      ctx.moveTo(320, 180 + 3); ctx.lineTo(320, 180 + 8);
      ctx.stroke();

      // HUD indicators
      ctx.font = 'bold 10px monospace';
      
      // Health HUD
      ctx.fillStyle = '#f87171';
      ctx.fillText(`HP: ${Math.round(state.health)}`, 20, 340);
      ctx.fillStyle = 'rgba(239,68,68,0.2)';
      ctx.fillRect(60, 332, 60, 8);
      ctx.fillStyle = '#ef4444';
      ctx.fillRect(60, 332, 60 * (Math.min(100, state.health) / 100), 8);

      // Armor HUD
      ctx.fillStyle = '#6ea8fe';
      ctx.fillText('AP: 50', 135, 340);

      // Ammo HUD
      ctx.fillStyle = '#facc15';
      const ammoVal = infAmmoRef.current ? 'INF' : state.ammo.toString();
      const hudAmmoText = state.reloading
        ? `RELOADING (${Math.round(state.reloadProgress * 100)}%)`
        : `AMMO: ${ammoVal} / 90`;
      ctx.fillText(hudAmmoText, 480, 340);

      // Score
      ctx.fillStyle = '#ffffff';
      ctx.fillText(`SCORE: ${state.score}`, 20, 25);
      
      // Frags
      ctx.fillStyle = '#a78bfa';
      ctx.fillText(`FRAGS: ${state.frags}`, 140, 25);

      // System stats HUD
      ctx.fillStyle = 'rgba(255,255,255,0.4)';
      ctx.font = '8px monospace';
      ctx.fillText(`FPS: ${fps}`, 580, 25);

      animationId = requestAnimationFrame(gameLoop);
    };

    animationId = requestAnimationFrame(gameLoop);
    return () => cancelAnimationFrame(animationId);
  }, [running, fps]);

  return (
    <div className="game-harness">
      <div className="game-harness-left">
        <div className="game-harness-canvas-wrapper" onClick={handleCanvasClick}>
          <canvas ref={canvasRef} width="640" height="360" />
          <div className="game-harness-crt-overlay" />
          {!running && (
            <div className="game-harness-offline-overlay">
              <span className="offline-badge">PROCESS DETACHED</span>
              <span>assaultcube_harness.exe not running</span>
            </div>
          )}
        </div>
        <div className="game-harness-info-bar">
          <span>Target: <strong>AssaultCube v1.2.0.2</strong></span>
          <span>Harness Link: <strong>READY (0.0.0.0:4912)</strong></span>
          <span>Window size: <strong>640 x 360</strong></span>
        </div>
      </div>

      <div className="game-harness-right">
        <div className="game-harness-panel">
          <h4>Process Status</h4>
          <div className="harness-status-grid">
            <div className="harness-status-item">
              <span>Status:</span>
              <span>
                <span className={`harness-badge ${running ? 'active' : 'inactive'}`}>
                  {running ? 'ACTIVE' : 'OFFLINE'}
                </span>
              </span>
            </div>
            <div className="harness-status-item">
              <span>PID:</span>
              <span>{pid}</span>
            </div>
            <div className="harness-status-item">
              <span>CPU:</span>
              <span>{running ? `${cpu}%` : '0%'}</span>
            </div>
            <div className="harness-status-item">
              <span>Memory:</span>
              <span>{running ? `${mem} MB` : '0 MB'}</span>
            </div>
          </div>
          <div className="harness-actions">
            {running ? (
              <button onClick={() => { setRunning(false); addLog('Process terminated.', 'warn'); }}>
                Terminate
              </button>
            ) : (
              <button className="primary" onClick={() => { setRunning(true); addLog('Process spawned and attached.', 'info'); }}>
                Attach Game
              </button>
            )}
            <button
              onClick={() => {
                playerStateRef.current.score = 0;
                playerStateRef.current.frags = 0;
                playerStateRef.current.health = 100;
                playerStateRef.current.ammo = 30;
                addLog('Stats reset.', 'info');
              }}
            >
              Reset Stats
            </button>
          </div>
        </div>

        <div className="game-harness-panel">
          <h4>Harness Cheats</h4>
          <div className="harness-cheats-grid">
            <div className="harness-cheat-row">
              <label>
                <input
                  type="checkbox"
                  checked={esp}
                  onChange={(e) => {
                    setEsp(e.target.checked);
                    addLog(`ESP Box Hack ${e.target.checked ? 'Enabled' : 'Disabled'}.`, 'cheat');
                  }}
                  disabled={!running}
                />
                Enable ESP Box
              </label>
            </div>
            <div className="harness-cheat-row">
              <label>
                <input
                  type="checkbox"
                  checked={aimbot}
                  onChange={(e) => {
                    setAimbot(e.target.checked);
                    addLog(`Memory Aimbot ${e.target.checked ? 'Enabled' : 'Disabled'}.`, 'cheat');
                    if (e.target.checked) setAutoplay(false); // Disable autoplay shoot if manually aiming
                  }}
                  disabled={!running}
                />
                Enable Aimbot
              </label>
            </div>
            <div className="harness-cheat-row">
              <label>
                <input
                  type="checkbox"
                  checked={autoplay}
                  onChange={(e) => {
                    setAutoplay(e.target.checked);
                    addLog(`Gameplay Simulation (Autoplay) ${e.target.checked ? 'Enabled' : 'Disabled'}.`, 'cheat');
                    if (e.target.checked) setAimbot(false); // Autoplay covers aimbot logic
                  }}
                  disabled={!running}
                />
                Autoplay Simulation
              </label>
            </div>
            <div className="harness-cheat-row">
              <label>
                <input
                  type="checkbox"
                  checked={infHealth}
                  onChange={(e) => {
                    setInfHealth(e.target.checked);
                    addLog(`Infinite Health (Freeze HP at 999) ${e.target.checked ? 'Enabled' : 'Disabled'}.`, 'cheat');
                  }}
                  disabled={!running}
                />
                Infinite Health
              </label>
            </div>
            <div className="harness-cheat-row">
              <label>
                <input
                  type="checkbox"
                  checked={infAmmo}
                  onChange={(e) => {
                    setInfAmmo(e.target.checked);
                    addLog(`Infinite Ammo (No Reload) ${e.target.checked ? 'Enabled' : 'Disabled'}.`, 'cheat');
                  }}
                  disabled={!running}
                />
                Infinite Ammo
              </label>
            </div>
            <div className="harness-cheat-row">
              <label>
                <input
                  type="checkbox"
                  checked={noRecoil}
                  onChange={(e) => {
                    setNoRecoil(e.target.checked);
                    addLog(`No Recoil Mod ${e.target.checked ? 'Enabled' : 'Disabled'}.`, 'cheat');
                  }}
                  disabled={!running}
                />
                No Recoil
              </label>
            </div>
            <div className="harness-cheat-row">
              <label>
                <input
                  type="checkbox"
                  checked={wallhack}
                  onChange={(e) => {
                    setWallhack(e.target.checked);
                    addLog(`Wireframe / Wallhack Mode ${e.target.checked ? 'Enabled' : 'Disabled'}.`, 'cheat');
                  }}
                  disabled={!running}
                />
                Wallhack / Wireframe
              </label>
            </div>
          </div>
        </div>

        <div className="game-harness-panel harness-console-panel">
          <h4>Stdout Console</h4>
          <div className="harness-console-logs" ref={logsEndRef}>
            {logs.map((log) => (
              <div key={log.id} className="harness-console-log">
                <span className="harness-console-time">[{log.time}]</span>
                <span style={{ color: log.type === 'error' ? '#f87171' : log.type === 'warn' ? '#fbbf24' : log.type === 'cheat' ? '#c084fc' : '#2ed573' }}>
                  {log.text}
                </span>
              </div>
            ))}
            {logs.length === 0 && <span style={{ color: '#6b7280' }}>Waiting for logs...</span>}
          </div>
        </div>
      </div>
    </div>
  );
}
