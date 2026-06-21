// Declaration types live in @horribledashboard/sdk so plugins and built-in modules share
// the exact same contract; re-exported here so existing imports keep working.
import type { ComponentType } from 'react';

import type {
  AgentCommandDecl,
  AgentContextSnapshot,
  AgentToolDecl,
  CommandDecl,
  JSONSchema,
  KeybindingDecl,
  PanelDecl,
  SettingDecl,
  SettingType,
  UseAgentContext,
  WidgetDecl,
} from '@horribledashboard/sdk';

export type {
  AgentCommandDecl,
  AgentContextSnapshot,
  AgentToolDecl,
  CommandDecl,
  JSONSchema,
  KeybindingDecl,
  PanelDecl,
  SettingDecl,
  SettingType,
  UseAgentContext,
  WidgetDecl,
};

/**
 * A custom section a module renders on the settings page, for configuration too
 * rich for the declarative `SettingDecl` controls (e.g. the agent permission rule
 * lists). Built-in only for now — not part of the public plugin contract.
 */
export interface SettingsSectionDecl {
  id: string;
  title: string;
  component: ComponentType;
}

/** Where a pane is placed relative to a reference pane when seeding a layout. */
export type PaneDirection = 'left' | 'right' | 'above' | 'below' | 'within';

/**
 * Directions a pane can be **split** toward. Excludes `within` (which adds a tab
 * to the same group rather than splitting). `left`/`right` produce a vertical
 * split, `above`/`below` a horizontal one.
 */
export type SplitDirection = 'left' | 'right' | 'above' | 'below';

/** One pane in a layout preset: a pane id, optionally positioned next to another. */
export interface PanePlacement {
  id: string;
  position?: { referencePanel: string; direction: PaneDirection };
}

/**
 * A predefined **workflow layout** (Blender-style workspace) shown in the shell
 * rail. The preset is the *seed* for a stable-id workspace: first activation lays
 * out `panes`, after which the user's rearrangements persist like any workspace
 * (a `layout.reset` restores the preset). Built-in only for now.
 */
export interface LayoutPreset {
  id: string;
  name: string;
  /** Short rail glyph (emoji/letter); falls back to the name's first character. */
  icon?: string;
  panes: PanePlacement[];
}

export interface ModuleManifest {
  id: string;
  title: string;
  commands?: CommandDecl[];
  panels?: PanelDecl[];
  widgets?: WidgetDecl[];
  layouts?: LayoutPreset[];
  keybindings?: KeybindingDecl[];
  settings?: SettingDecl[];
  settingsSections?: SettingsSectionDecl[];
}

/** Top-level shell surfaces. `home` is the first-open view; `workspace` hosts panels. */
export type ShellView = 'home' | 'workspace';

/** An active pane instance in the workspace layout. */
export interface OpenPaneInfo {
  /** The ID of the View running in this pane (e.g., 'editor.buffer', 'observability'). */
  id: string;
  /**
   * The unique ID of this active Pane Instance (e.g., 'editor.buffer#2').
   * Used as the target for pane-specific actions and context snapshots.
   */
  instanceId: string;
  /** The title displayed in the pane's tab. */
  title: string;
  /** Whether this pane instance currently exposes an agent context snapshot. */
  hasContext: boolean;
}

export interface WorkspaceInfo {
  id: string;
  name: string;
}

/** Options for opening a pane: an explicit instance ID (so reopening the same
 * logical pane focuses it instead of duplicating) and initial parameters. */
export interface OpenPaneOptions {
  instanceId?: string;
  params?: Record<string, unknown>;
}

/**
 * Workspace mutations the agent orchestrator and layout controllers drive.
 * Installed by the Workspace component to decouple UI logic from the docking engine.
 */
export interface LayoutController {
  /** Close a pane. Accepts either a View ID or a Pane Instance ID. */
  closePane(id: string): boolean;
  /** Bring a pane instance forward (activate its tab). Returns false if unknown. */
  focusPane(instanceId: string): boolean;
  /** List all active pane instances in the active workspace. */
  listOpenPanes(): OpenPaneInfo[];
  createWorkspace(name: string): Promise<WorkspaceInfo>;
  listWorkspaces(): Promise<{ active: string | null; workspaces: WorkspaceInfo[] }>;
  /** Re-seed the active workflow layout from its preset (discarding tweaks). */
  resetLayout(): void;
  /** Delete the active workspace if it's a custom one (presets reset instead). */
  deleteActiveWorkspace(): void;
  /** Rename a specific workspace. */
  renameWorkspace(id: string, name: string): Promise<void>;
  /** Delete a specific workspace. */
  deleteWorkspace(id: string): Promise<void>;

  // --- Geometry: the shared operations the agent's tools AND the user's
  // Blender-style gestures both drive. Every mutation triggers the dockview
  // autosave (onDidLayoutChange), so the new layout persists like any other. ---

  /**
   * Split the pane `instanceId`, opening `viewId` in a new region beside it
   * (`left`/`right` → vertical split, `above`/`below` → horizontal). Returns the
   * new pane's instance id, or null if either id is unknown.
   */
  splitPane(instanceId: string, direction: SplitDirection, viewId: string): string | null;
  /** Resize the group holding a pane (pixels; omit a dimension to leave it). */
  resizePane(instanceId: string, size: { width?: number; height?: number }): boolean;
  /** Move a pane beside another pane, or into its tab group with `within`. */
  movePane(instanceId: string, referenceInstanceId: string, direction: PaneDirection): boolean;
  /** Pop a pane out to a floating window (`true`) or dock it back (`false`). */
  setPaneFloating(instanceId: string, floating: boolean): boolean;
  /** Maximize a pane to fill the workspace (`true`) or restore the layout (`false`). */
  maximizePane(instanceId: string, maximized: boolean): boolean;
  /**
   * Swap an open pane's view content in place (preserves geometry and instanceId).
   * Returns false if either the pane instance or the target `viewId` is unknown.
   */
  changePaneType(instanceId: string, viewId: string): boolean;
}

class ModuleRegistry {
  private modules = new Map<string, ModuleManifest>();
  private panelOpener: ((panelId: string, opts?: OpenPaneOptions) => void) | null = null;
  private workspaceSwitcher: ((workspaceId: string) => void) | null = null;
  private layoutControllerImpl: LayoutController | null = null;
  private changeListeners = new Set<() => void>();

  /** Idempotent: re-registering the same module id is a no-op (StrictMode-safe). */
  register(manifest: ModuleManifest): void {
    if (this.modules.has(manifest.id)) return;
    this.modules.set(manifest.id, manifest);
    this.changeListeners.forEach((l) => l());
  }

  /**
   * Fire `listener` whenever the set of registered modules changes (e.g. a plugin
   * registers at boot). Returns an unsubscribe. Used to re-push the agent
   * capability manifest when the tool catalog changes.
   */
  onChange(listener: () => void): () => void {
    this.changeListeners.add(listener);
    return () => {
      this.changeListeners.delete(listener);
    };
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

  /** Predefined workflow layouts contributed by modules, in registration order. */
  get layouts(): LayoutPreset[] {
    return [...this.modules.values()].flatMap((m) => m.layouts ?? []);
  }

  get keybindings(): KeybindingDecl[] {
    return [...this.modules.values()].flatMap((m) => m.keybindings ?? []);
  }

  get settings(): SettingDecl[] {
    return [...this.modules.values()].flatMap((m) => m.settings ?? []);
  }

  get settingsSections(): SettingsSectionDecl[] {
    return [...this.modules.values()].flatMap((m) => m.settingsSections ?? []);
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
  setPanelOpener(opener: (panelId: string, opts?: OpenPaneOptions) => void): void {
    this.panelOpener = opener;
  }

  openPanel(panelId: string, opts?: OpenPaneOptions): void {
    this.panelOpener?.(panelId, opts);
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
