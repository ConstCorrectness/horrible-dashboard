// Declaration types live in @horribledashboard/sdk so plugins and built-in modules share
// the exact same contract; re-exported here so existing imports keep working.
import type {
  CommandDecl,
  KeybindingDecl,
  PanelDecl,
  SettingDecl,
  SettingType,
  WidgetDecl,
} from '@horribledashboard/sdk';

export type { CommandDecl, KeybindingDecl, PanelDecl, SettingDecl, SettingType, WidgetDecl };

export interface ModuleManifest {
  id: string;
  title: string;
  commands?: CommandDecl[];
  panels?: PanelDecl[];
  widgets?: WidgetDecl[];
  keybindings?: KeybindingDecl[];
  settings?: SettingDecl[];
}

/** Top-level shell surfaces. `home` is the first-open view; `workspace` hosts panels. */
export type ShellView = 'home' | 'workspace';

/** An open pane in the active workspace. */
export interface OpenPaneInfo {
  id: string;
  title: string;
}

export interface WorkspaceInfo {
  id: string;
  name: string;
}

/**
 * Workspace mutations the agent orchestrator needs that don't already have a
 * seam. The Workspace component installs this; it owns the dockview api. Keeps
 * the agent (and any module) decoupled from the docking engine.
 */
export interface LayoutController {
  closePane(id: string): boolean;
  listOpenPanes(): OpenPaneInfo[];
  createWorkspace(name: string): Promise<WorkspaceInfo>;
  listWorkspaces(): Promise<{ active: string | null; workspaces: WorkspaceInfo[] }>;
}

class ModuleRegistry {
  private modules = new Map<string, ModuleManifest>();
  private panelOpener: ((panelId: string) => void) | null = null;
  private workspaceSwitcher: ((workspaceId: string) => void) | null = null;
  private layoutControllerImpl: LayoutController | null = null;

  /** Idempotent: re-registering the same module id is a no-op (StrictMode-safe). */
  register(manifest: ModuleManifest): void {
    if (this.modules.has(manifest.id)) return;
    this.modules.set(manifest.id, manifest);
  }

  get commands(): CommandDecl[] {
    const declared = [...this.modules.values()].flatMap((m) => m.commands ?? []);
    // Every widget is openable as a pane from the palette — synthesize an open
    // command per widget so "Open widget: <title>" is discoverable via Ctrl+K.
    // Opening routes through the same panel-opener seam (the workspace resolves
    // widget ids against registry.widgets).
    const widgetOpeners: CommandDecl[] = this.widgets.map((w) => ({
      id: `widget.open:${w.id}`,
      title: `Open widget: ${w.title}`,
      run: () => this.openPanel(w.id),
    }));
    return [...declared, ...widgetOpeners];
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

  get settings(): SettingDecl[] {
    return [...this.modules.values()].flatMap((m) => m.settings ?? []);
  }

  /**
   * Title of the module that declared `settingKey`, for grouping the settings
   * page by contributor. Falls back to `undefined` for an unknown key.
   */
  settingOwner(settingKey: string): string | undefined {
    for (const m of this.modules.values()) {
      if (m.settings?.some((s) => s.key === settingKey)) return m.title;
    }
    return undefined;
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

  /** The workspace installs the switcher; commands call switchWorkspace. */
  setWorkspaceSwitcher(switcher: (workspaceId: string) => void): void {
    this.workspaceSwitcher = switcher;
  }

  /** Select a named workspace tab by id (e.g. `dashboard.open` → 'dashboard'). */
  switchWorkspace(workspaceId: string): void {
    this.workspaceSwitcher?.(workspaceId);
  }

  /** The workspace installs the layout controller; the agent executor uses it. */
  setLayoutController(controller: LayoutController): void {
    this.layoutControllerImpl = controller;
  }

  get layoutController(): LayoutController | null {
    return this.layoutControllerImpl;
  }
}

export const registry = new ModuleRegistry();
