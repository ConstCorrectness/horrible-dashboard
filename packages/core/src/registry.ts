// Declaration types live in @horribledashboard/sdk so plugins and built-in modules share
// the exact same contract; re-exported here so existing imports keep working.
import type { ComponentType } from 'react';

import { regionCommandHandler } from './layout/region-bus';
import type { FramePreset } from './layout/presets';

import type {
  AgentCommandDecl,
  AgentContextSnapshot,
  AgentToolDecl,
  CollabDecl,
  CommandDecl,
  DockSide,
  JSONSchema,
  KeybindingDecl,
  PaneCaptureDecl,
  PaneRole,
  PanelDecl,
  RegionPosition,
  RegionViewDecl,
  SettingDecl,
  SettingType,
  UseAgentContext,
  WidgetDecl,
} from '@horribledashboard/sdk';

export type {
  AgentCommandDecl,
  AgentContextSnapshot,
  AgentToolDecl,
  CollabDecl,
  CommandDecl,
  DockSide,
  JSONSchema,
  KeybindingDecl,
  PaneCaptureDecl,
  PaneRole,
  PanelDecl,
  RegionPosition,
  RegionViewDecl,
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

/** Where a pane is placed relative to a reference pane. */
export type PaneDirection = 'left' | 'right' | 'above' | 'below' | 'within';

/**
 * Directions an area can be **split** toward. Excludes `within` (which adds a tab
 * to the same area rather than splitting). `left`/`right` produce a `row` split,
 * `above`/`below` a `column` one.
 */
export type SplitDirection = 'left' | 'right' | 'above' | 'below';

export interface ModuleManifest {
  id: string;
  title: string;
  commands?: CommandDecl[];
  panels?: PanelDecl[];
  widgets?: WidgetDecl[];
  /** Predefined full-frame workspaces (center tree + docks). */
  frames?: FramePreset[];
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
 * Workspace mutations the agent relay and shell chrome drive, decoupled from the
 * engine. Implemented by the frame controller (core/layout/controller.ts,
 * installed by the Frame on mount); richer frame verbs (areas, regions, docks)
 * are plain controller exports rather than part of this seam.
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
  /** Pop a pane out to a floating card (`true`) or dock it back (`false`). */
  setPaneFloating(instanceId: string, floating: boolean): boolean;
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
  private services = new Map<string, unknown>();
  private changeListeners = new Set<() => void>();

  /** Idempotent: re-registering the same module id is a no-op (StrictMode-safe). */
  register(manifest: ModuleManifest): void {
    if (this.modules.has(manifest.id)) return;
    this.modules.set(manifest.id, manifest);
    this.changeListeners.forEach((l) => l());
  }

  /** Test-only: drop every registered module (mirrors `layoutStore.resetForTests`). */
  resetForTests(): void {
    this.modules.clear();
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
    return [...declared, ...this.frameSynthesizedCommands()];
  }

  /** Every view (panel or widget) declaration, panels first. */
  private get allViews(): Array<PanelDecl | WidgetDecl> {
    return [...this.panels, ...this.widgets];
  }

  /**
   * Frame-engine synthesis: role-aware openers plus the region commands behind
   * the universal `t`/`n`/`b` toggles and per-view pick letters. The handlers
   * route through the region command bus (installed by the frame controller) so
   * the registry never imports the controller.
   */
  private frameSynthesizedCommands(): CommandDecl[] {
    const openers: CommandDecl[] = this.allViews.map((v) => ({
      id: `pane.open:${v.id}`,
      title: `Open: ${v.title}`,
      run: () => this.openPanel(v.id),
    }));
    const toggles: CommandDecl[] = [];
    const picks = new Map<string, CommandDecl>();
    for (const view of this.allViews) {
      if (!view.regions?.length) continue;
      const positions = new Set(view.regions.map((r) => r.position ?? 'right'));
      for (const position of positions) {
        toggles.push({
          id: `region.toggle:${position}:${view.id}`,
          title: `${view.title}: Toggle ${position} region`,
          run: () => regionCommandHandler()?.togglePosition(view.id, position),
        });
      }
      for (const r of view.regions) {
        if (picks.has(r.id)) continue;
        picks.set(r.id, {
          id: `region.pick:${r.id}`,
          title: `Toggle ${r.label}`,
          run: () => regionCommandHandler()?.pickView(r.id),
        });
      }
    }
    return [...openers, ...toggles, ...picks.values()];
  }

  get panels(): PanelDecl[] {
    return [...this.modules.values()].flatMap((m) => m.panels ?? []);
  }

  get widgets(): WidgetDecl[] {
    return [...this.modules.values()].flatMap((m) => m.widgets ?? []);
  }

  /** Predefined full-frame workspaces, in module registration order. */
  get framePresets(): FramePreset[] {
    return [...this.modules.values()].flatMap((m) => m.frames ?? []);
  }

  get keybindings(): KeybindingDecl[] {
    const declared = [...this.modules.values()].flatMap((m) => m.keybindings ?? []);
    return [...declared, ...this.frameSynthesizedKeybindings()];
  }

  /**
   * Frame-engine bindings, all scoped to the host view so they're only live
   * while one of its panes is focused: the universal position toggles
   * (`t`/`n`/`b` = left/right/bottom region) plus each region view's declared
   * pick letter. Letters colliding with the reserved position keys are dropped
   * with a warning (validated here, at the single synthesis point).
   */
  private frameSynthesizedKeybindings(): KeybindingDecl[] {
    const POSITION_KEYS = { left: 't', right: 'n', bottom: 'b' } as const;
    const out: KeybindingDecl[] = [];
    for (const view of this.allViews) {
      if (!view.regions?.length) continue;
      const positions = new Set(view.regions.map((r) => r.position ?? 'right'));
      for (const position of positions) {
        out.push({
          key: POSITION_KEYS[position],
          command: `region.toggle:${position}:${view.id}`,
          scope: view.id,
        });
      }
      for (const r of view.regions) {
        if (!r.key) continue;
        if (r.key === 't' || r.key === 'n' || r.key === 'b') {
          console.warn(
            `[registry] region view ${r.id} on ${view.id} declares reserved key "${r.key}" (t/n/b are the universal position toggles) — dropped`,
          );
          continue;
        }
        out.push({ key: r.key, command: `region.pick:${r.id}`, scope: view.id });
      }
    }
    return out;
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

  /**
   * Register a named cross-module service (e.g. the editor exposes its buffer
   * surface so the visualizer can open/read buffers without deep-importing the
   * editor module's internals). One impl per id; last registration wins.
   */
  provideService<T>(id: string, impl: T): void {
    this.services.set(id, impl);
  }

  /** Look up a service by id. Returns undefined if its provider hasn't loaded. */
  getService<T>(id: string): T | undefined {
    return this.services.get(id) as T | undefined;
  }
}

export const registry = new ModuleRegistry();
