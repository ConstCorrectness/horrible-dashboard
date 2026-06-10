import type { ComponentType } from 'react';

import type { Capability } from './capabilities';

export interface CommandDecl {
  /** `module.verb`, e.g. `dashboard.open`. */
  id: string;
  title: string;
  run: () => void | Promise<void>;
}

export interface PanelDecl {
  id: string;
  title: string;
  component: ComponentType;
  defaultPlacement: 'left' | 'center' | 'right' | 'bottom';
  /**
   * When true, only one window of this panel can exist — opening it again
   * focuses the existing one (e.g. the dashboard). When false/omitted, each
   * open creates a new instance (e.g. terminals, editor buffers).
   */
  singleton?: boolean;
}

export interface WidgetDecl {
  id: string;
  title: string;
  component: ComponentType;
  requiredCapabilities?: Capability[];
}

export interface KeybindingDecl {
  /** e.g. `mod+k` — `mod` is ctrl (or cmd on macOS). */
  key: string;
  command: string;
}

export interface ModuleManifest {
  id: string;
  title: string;
  commands?: CommandDecl[];
  panels?: PanelDecl[];
  widgets?: WidgetDecl[];
  keybindings?: KeybindingDecl[];
}

/** Top-level shell surfaces. `home` is the first-open view; `workspace` hosts panels. */
export type ShellView = 'home' | 'workspace';

class ModuleRegistry {
  private modules = new Map<string, ModuleManifest>();
  private panelOpener: ((panelId: string) => void) | null = null;
  private viewOpener: ((view: ShellView) => void) | null = null;

  /** Idempotent: re-registering the same module id is a no-op (StrictMode-safe). */
  register(manifest: ModuleManifest): void {
    if (this.modules.has(manifest.id)) return;
    this.modules.set(manifest.id, manifest);
  }

  get commands(): CommandDecl[] {
    return [...this.modules.values()].flatMap((m) => m.commands ?? []);
  }

  get panels(): PanelDecl[] {
    return [...this.modules.values()].flatMap((m) => m.panels ?? []);
  }

  get widgets(): WidgetDecl[] {
    return [...this.modules.values()].flatMap((m) => m.widgets ?? []);
  }

  get keybindings(): KeybindingDecl[] {
    return [...this.modules.values()].flatMap((m) => m.keybindings ?? []);
  }

  async runCommand(id: string): Promise<void> {
    const command = this.commands.find((c) => c.id === id);
    if (!command) throw new Error(`Unknown command: ${id}`);
    await command.run();
  }

  /** The layout shell installs the opener; modules call openPanel. */
  setPanelOpener(opener: (panelId: string) => void): void {
    this.panelOpener = opener;
  }

  openPanel(panelId: string): void {
    this.panelOpener?.(panelId);
  }

  setViewOpener(opener: (view: ShellView) => void): void {
    this.viewOpener = opener;
  }

  openView(view: ShellView): void {
    this.viewOpener?.(view);
  }
}

export const registry = new ModuleRegistry();
