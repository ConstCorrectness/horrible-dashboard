// Declaration types live in @horrible/sdk so plugins and built-in modules share
// the exact same contract; re-exported here so existing imports keep working.
import type { CommandDecl, KeybindingDecl, PanelDecl, WidgetDecl } from '@horrible/sdk';

export type { CommandDecl, KeybindingDecl, PanelDecl, WidgetDecl };

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
