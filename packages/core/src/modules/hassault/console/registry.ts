/**
 * Client-side CVar and ConCommand Registry for hAssault.
 *
 * Keeps local and replicated CVar states, synchronizes definitions with the backend,
 * and provides rich autocomplete and search capabilities.
 */

import type {
  AutocompleteItem,
  CVarDefinition,
  CVarValue,
  ConCommandDefinition,
  MacroRecord,
} from './types';

type CVarListener = (name: string, value: CVarValue, prev: CVarValue) => void;

class ClientConsoleRegistry {
  readonly cvars = new Map<string, CVarDefinition>();
  readonly commands = new Map<string, ConCommandDefinition>();
  readonly macros = new Map<string, MacroRecord>();
  private readonly listeners = new Set<CVarListener>();
  private initialized = false;

  isInitialized(): boolean {
    return this.initialized;
  }

  constructor() {
    this.initDefaultClientCVars();
  }

  private initDefaultClientCVars(): void {
    // Client-side defaults in case backend is loading
    const defaults: CVarDefinition[] = [
      {
        name: 'net.graph',
        namespace: 'net',
        type: 'number',
        default_value: 0,
        current_value: 0,
        min_value: 0,
        max_value: 3,
        description: 'Draw in-game network graph (0: off, 1: fps/ping, 2: jitter/interp, 3: full breakdown)',
        flags: ['client'],
      },
      {
        name: 'net.simulate_lag',
        namespace: 'net',
        type: 'number',
        default_value: 0,
        current_value: 0,
        min_value: 0,
        max_value: 1000,
        description: 'Inject artificial latency in milliseconds',
        flags: ['client', 'cheat'],
      },
      {
        name: 'net.simulate_loss',
        namespace: 'net',
        type: 'number',
        default_value: 0,
        current_value: 0,
        min_value: 0,
        max_value: 1,
        description: 'Inject artificial packet loss fraction (0.0 to 1.0)',
        flags: ['client', 'cheat'],
      },
      {
        name: 'draw.hitboxes',
        namespace: 'draw',
        type: 'boolean',
        default_value: false,
        current_value: false,
        description: 'Render authoritative hitboxes and headshot bands around player bodies',
        flags: ['client'],
      },
      {
        name: 'draw.trajectories',
        namespace: 'draw',
        type: 'boolean',
        default_value: true,
        current_value: true,
        description: 'Trace bullet raycast trajectories and wall impact points',
        flags: ['client'],
      },
      {
        name: 'draw.wireframe',
        namespace: 'draw',
        type: 'boolean',
        default_value: false,
        current_value: false,
        description: 'Render map world geometry in wireframe mode',
        flags: ['client'],
      },
      {
        name: 'draw.fps',
        namespace: 'draw',
        type: 'boolean',
        default_value: false,
        current_value: false,
        description: 'Display FPS and frame time counter',
        flags: ['client'],
      },
      {
        name: 'draw.noise_rings',
        namespace: 'draw',
        type: 'boolean',
        default_value: true,
        current_value: true,
        description: 'Display spatial noise bearing indicator around crosshair',
        flags: ['client'],
      },
      {
        name: 'draw.fov',
        namespace: 'draw',
        type: 'number',
        default_value: 75,
        current_value: 75,
        min_value: 60,
        max_value: 110,
        description: 'Vertical field of view in degrees',
        flags: ['client', 'archived'],
      },
      {
        name: 'draw.viewmodel',
        namespace: 'draw',
        type: 'boolean',
        default_value: true,
        current_value: true,
        description: 'Render first-person weapon model',
        flags: ['client'],
      },
      {
        name: 'server.cheats',
        namespace: 'server',
        type: 'boolean',
        default_value: false,
        current_value: false,
        description: 'Allow cheat-protected commands (godmode, noclip, give)',
        flags: ['server', 'replicated'],
      },
      {
        name: 'server.timescale',
        namespace: 'server',
        type: 'number',
        default_value: 1.0,
        current_value: 1.0,
        min_value: 0.05,
        max_value: 5.0,
        description: 'Simulation speed multiplier (matrix slow-mo to fast-forward)',
        flags: ['server', 'cheat', 'replicated'],
      },
      {
        name: 'player.god',
        namespace: 'player',
        type: 'boolean',
        default_value: false,
        current_value: false,
        description: 'Invulnerability / god mode',
        flags: ['cheat', 'server'],
      },
      {
        name: 'player.noclip',
        namespace: 'player',
        type: 'boolean',
        default_value: false,
        current_value: false,
        description: 'Fly freely through walls and level geometry',
        flags: ['cheat', 'client'],
      },
      {
        name: 'player.infinite_ammo',
        namespace: 'player',
        type: 'boolean',
        default_value: false,
        current_value: false,
        description: 'Infinite magazine and reserve ammo',
        flags: ['cheat', 'server'],
      },
    ];

    for (const cvar of defaults) {
      this.cvars.set(cvar.name, cvar);
    }
  }

  async syncDefinitions(): Promise<void> {
    try {
      const res = await fetch('/api/hassault/console/definitions');
      if (!res.ok) return;
      const data = (await res.json()) as {
        cvars: CVarDefinition[];
        commands: ConCommandDefinition[];
        macros: MacroRecord[];
      };
      for (const cvar of data.cvars ?? []) {
        const existing = this.cvars.get(cvar.name);
        if (existing) {
          // Preserve local current_value if modified
          cvar.current_value = existing.current_value;
        }
        this.cvars.set(cvar.name, cvar);
      }
      for (const cmd of data.commands ?? []) {
        this.commands.set(cmd.name, cmd);
      }
      for (const m of data.macros ?? []) {
        this.macros.set(m.name, m);
      }
      this.initialized = true;
    } catch {
      // Backend offline / detached; fallback to local defaults
    }
  }

  get(name: string): CVarValue | undefined {
    return this.cvars.get(name)?.current_value;
  }

  /**
   * Typed reads for the two kinds a UI actually binds to.
   *
   * `get` cannot know which of the three a name holds, so every caller used to
   * launder it through `any` and a `??` default — which silently accepted a string
   * where a boolean was meant. These coerce instead, and take the fallback the
   * caller would have written anyway.
   */
  getBool(name: string, fallback = false): boolean {
    const value = this.get(name);
    return value === undefined ? fallback : Boolean(value);
  }

  getNumber(name: string, fallback = 0): number {
    const value = this.get(name);
    if (value === undefined) return fallback;
    const num = typeof value === 'number' ? value : Number(value);
    return Number.isNaN(num) ? fallback : num;
  }

  set(name: string, value: CVarValue): boolean {
    const cvar = this.cvars.get(name);
    if (!cvar) return false;
    let coerced: CVarValue = value;
    if (cvar.type === 'boolean') {
      coerced = typeof value === 'string' ? ['1', 'true', 'yes', 'on'].includes(value.toLowerCase()) : Boolean(value);
    } else if (cvar.type === 'number') {
      let num = typeof value === 'string' ? parseFloat(value) : Number(value);
      // A number CVar's default is a number, but the type is the union — fall to 0
      // rather than assert, so a mistyped registration cannot put NaN on screen.
      if (Number.isNaN(num)) num = typeof cvar.default_value === 'number' ? cvar.default_value : 0;
      if (cvar.min_value != null) num = Math.max(num, cvar.min_value);
      if (cvar.max_value != null) num = Math.min(num, cvar.max_value);
      coerced = num;
    } else if (cvar.type === 'enum' && cvar.enum_values) {
      if (!cvar.enum_values.includes(String(value))) {
        return false;
      }
      coerced = String(value);
    }

    const prev = cvar.current_value;
    cvar.current_value = coerced;
    if (prev !== coerced) {
      for (const listener of this.listeners) {
        try {
          listener(name, coerced, prev);
        } catch {
          // listener error guard
        }
      }
    }
    return true;
  }

  subscribe(listener: CVarListener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  /**
   * Search matching CVars, ConCommands, and Macros for autocomplete popup.
   */
  autocomplete(query: string, limit = 10): AutocompleteItem[] {
    const q = query.trim().toLowerCase();
    const results: AutocompleteItem[] = [];

    // Search CVars
    for (const cvar of this.cvars.values()) {
      if (!q || cvar.name.toLowerCase().includes(q) || cvar.description.toLowerCase().includes(q)) {
        results.push({
          name: cvar.name,
          kind: 'cvar',
          type: cvar.type,
          currentValue: cvar.current_value,
          defaultValue: cvar.default_value,
          description: cvar.description,
          flags: cvar.flags,
        });
      }
    }

    // Search Commands
    for (const cmd of this.commands.values()) {
      if (!q || cmd.name.toLowerCase().includes(q) || cmd.description.toLowerCase().includes(q)) {
        results.push({
          name: cmd.name,
          kind: 'command',
          signature: cmd.signature,
          description: cmd.description,
          flags: cmd.flags,
        });
      }
    }

    // Search Macros
    for (const m of this.macros.values()) {
      const macroCmd = `macro.run("${m.name}")`;
      if (!q || m.name.toLowerCase().includes(q) || macroCmd.toLowerCase().includes(q) || m.description.toLowerCase().includes(q)) {
        results.push({
          name: macroCmd,
          kind: 'macro',
          description: m.description || `Run ${m.name} macro`,
        });
      }
    }

    // Sort: exact prefix matches first, then alphabetical
    results.sort((a, b) => {
      const aStarts = a.name.toLowerCase().startsWith(q);
      const bStarts = b.name.toLowerCase().startsWith(q);
      if (aStarts && !bStarts) return -1;
      if (!aStarts && bStarts) return 1;
      return a.name.localeCompare(b.name);
    });

    return results.slice(0, limit);
  }
}

export const consoleRegistry = new ClientConsoleRegistry();
