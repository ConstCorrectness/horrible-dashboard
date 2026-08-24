// Declaration types live in @horribledashboard/sdk so plugins and built-in modules share
// the exact same contract; re-exported here so existing imports keep working.
import type { ComponentType } from 'react';

import { frameCommandHandler } from './layout/frame-bus';
import { noteViewOpened } from './layout/recents';
import type { FramePreset } from './layout/presets';
import type { ContextMenuProvider } from './overlay/context-menu';

import type {
  AgentCommandDecl,
  AgentContextSnapshot,
  AgentToolDecl,
  BackdropDecl,
  CollabDecl,
  CommandDecl,
  DockSide,
  ExplorerSourceDecl,
  JSONSchema,
  KeybindingDecl,
  PaneCaptureDecl,
  PaneRole,
  PanelDecl,
  RegionPosition,
  RegionViewDecl,
  SectionDecl,
  SettingDecl,
  SettingType,
  UseAgentContext,
  WidgetDecl,
} from '@horribledashboard/sdk';

export type {
  AgentCommandDecl,
  AgentContextSnapshot,
  AgentToolDecl,
  BackdropDecl,
  CollabDecl,
  CommandDecl,
  DockSide,
  ExplorerSourceDecl,
  JSONSchema,
  KeybindingDecl,
  PaneCaptureDecl,
  PaneRole,
  PanelDecl,
  RegionPosition,
  RegionViewDecl,
  SectionDecl,
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
  /** Backdrops this module offers a desktop. See `BackdropDecl`. */
  backdrops?: BackdropDecl[];
  keybindings?: KeybindingDecl[];
  settings?: SettingDecl[];
  settingsSections?: SettingsSectionDecl[];
  /**
   * Browsers this module adds to the Explorer pane (the view declaring
   * `explorerHost`). See `ExplorerSourceDecl` — the one place a module
   * legitimately contributes a section to a pane it does not own.
   */
  explorerSources?: ExplorerSourceDecl[];
  /**
   * Right-click menu items this module offers, by target kind. A module may
   * contribute to a surface it does not own — that is the point: the file tree
   * should not have to know which modules can do something with a `.ipynb`.
   * See `ContextMenuProvider` and docs/architecture/context-menus.mdx.
   */
  contextMenu?: ContextMenuProvider[];
}

/**
 * Top-level shell surfaces.
 *
 * There is exactly **one** main surface: `desktop`. `boot` and `oobe` are the
 * states before you reach it.
 *
 * Both of the old views are gone, for the same reason. `workspace` was not a
 * surface at all — a desktop IS a workspace, and whether it shows the tiling
 * frame or free windows is that desktop's `mode`, so making it a view meant one
 * fact had two homes that could disagree. `home` was a second landing screen
 * competing with the desktop for the same job; it survives as the `splash`
 * **backdrop**, which is strictly more useful, since windows can float over it.
 */
export type ShellView = 'boot' | 'oobe' | 'desktop';

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
  /**
   * Create a workspace. `mode` is the desktop's paradigm, which belongs to the
   * workspace rather than to a global switch; `fromCurrent` seeds it with the
   * arrangement on screen instead of an empty frame.
   */
  createWorkspace(
    name: string,
    options?: { mode?: 'tiling' | 'floating'; fromCurrent?: boolean },
  ): Promise<WorkspaceInfo>;
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
  /** Bumped whenever the module set changes; the only cache-invalidation signal. */
  private generation = 0;
  private explorerCache: {
    generation: number;
    decls: Map<string, PanelDecl | WidgetDecl>;
  } | null = null;

  /** Idempotent: re-registering the same module id is a no-op (StrictMode-safe). */
  register(manifest: ModuleManifest): void {
    if (this.modules.has(manifest.id)) return;
    this.modules.set(manifest.id, manifest);
    this.generation += 1;
    this.changeListeners.forEach((l) => l());
  }

  /** Test-only: drop every registered module (mirrors `layoutStore.resetForTests`). */
  resetForTests(): void {
    this.modules.clear();
    this.generation += 1;
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

  /** Every module's declared context-menu providers, in registration order. */
  get contextMenuProviders(): ContextMenuProvider[] {
    return [...this.modules.values()].flatMap((m) => m.contextMenu ?? []);
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
    // `embedded` views get no opener: they live inside a host pane, and a command
    // palette entry that opened one standalone would present it as a second,
    // competing home for content that already has one. Their `region.pick:` /
    // `section.show:` commands below are how they are reached.
    const openers: CommandDecl[] = this.allViews
      .filter((v) => !v.embedded)
      .map((v) => ({
        id: `pane.open:${v.id}`,
        title: `Open: ${v.title}`,
        run: () => this.openPanel(v.id),
      }));
    const sections: CommandDecl[] = this.allViews.flatMap((view) =>
      (view.sections ?? []).map((s) => ({
        id: `section.show:${view.id}:${s.id}`,
        title: `${view.title}: ${s.label}`,
        run: () => frameCommandHandler()?.revealSection(s.id, view.id),
      })),
    );
    const toggles: CommandDecl[] = [];
    const picks = new Map<string, CommandDecl>();
    for (const view of this.allViews) {
      if (!view.regions?.length) continue;
      const positions = new Set(view.regions.map((r) => r.position ?? 'right'));
      for (const position of positions) {
        toggles.push({
          id: `region.toggle:${position}:${view.id}`,
          title: `${view.title}: Toggle ${position} region`,
          run: () => frameCommandHandler()?.togglePosition(view.id, position),
        });
      }
      for (const r of view.regions) {
        if (picks.has(r.id)) continue;
        picks.set(r.id, {
          id: `region.pick:${r.id}`,
          title: `Toggle ${r.label}`,
          run: () => frameCommandHandler()?.pickView(r.id),
        });
      }
    }
    // One switch command per registered backdrop, synthesized rather than
    // declared for the same reason the pane openers are: a plugin contributes a
    // provider and its command exists, with nobody maintaining a second list
    // that can fall out of step with the first.
    const backdrops: CommandDecl[] = this.backdrops.map((b) => ({
      id: `desktop.backdrop:${b.id}`,
      title: `Desktop: Use the ${b.title} backdrop`,
      run: () => frameCommandHandler()?.applyBackdrop(b.id),
    }));
    return [...openers, ...sections, ...toggles, ...picks.values(), ...backdrops];
  }

  get panels(): PanelDecl[] {
    return this.withExplorerSources([...this.modules.values()].flatMap((m) => m.panels ?? []));
  }

  get widgets(): WidgetDecl[] {
    return this.withExplorerSources([...this.modules.values()].flatMap((m) => m.widgets ?? []));
  }

  /** Every `explorerSources` contribution, in module registration order. */
  get explorerSources(): ExplorerSourceDecl[] {
    return [...this.modules.values()].flatMap((m) => m.explorerSources ?? []);
  }

  /**
   * Append contributed sources to the `explorerHost` view's own sections.
   *
   * Done at read time rather than at registration so it cannot depend on module
   * order — Explorer may register before or after its contributors, and both must
   * work. Everything downstream (`sectionsOf`, the synthesized `section.show:`
   * commands and pick keys, `show`, `list_available_panes`) reads through these
   * getters, so the sources are indistinguishable from declared sections.
   *
   * The result is **memoized against the registration counter**: these getters run
   * on every render path, and returning a fresh decl object each time would break
   * the identity that `useSyncExternalStore` snapshots and React memoization rely
   * on. Modules that contribute nothing are returned untouched, so the identity of
   * every other decl is preserved exactly.
   */
  private withExplorerSources<T extends PanelDecl | WidgetDecl>(views: T[]): T[] {
    const sources = this.explorerSources;
    if (!sources.length) return views;
    if (this.explorerCache?.generation !== this.generation) {
      this.explorerCache = { generation: this.generation, decls: new Map() };
    }
    const cache = this.explorerCache.decls;
    return views.map((view) => {
      if (!view.explorerHost) return view;
      const cached = cache.get(view.id) as T | undefined;
      if (cached) return cached;
      const merged = { ...view, sections: [...(view.sections ?? []), ...sources] };
      cache.set(view.id, merged);
      return merged;
    });
  }

  /** Predefined full-frame workspaces, in module registration order. */
  get framePresets(): FramePreset[] {
    return [...this.modules.values()].flatMap((m) => m.frames ?? []);
  }

  /** Every contributed desktop backdrop, in module registration order. */
  get backdrops(): BackdropDecl[] {
    return [...this.modules.values()].flatMap((m) => m.backdrops ?? []);
  }

  /**
   * Look up one backdrop by id. Returns undefined for an id whose provider is
   * not registered — a desktop saved against a plugin that has since been
   * uninstalled. The caller falls back rather than blanking the desktop, since
   * a missing wallpaper is not a reason to lose the windows on top of it.
   */
  backdrop(id: string): BackdropDecl | undefined {
    return this.backdrops.find((b) => b.id === id);
  }

  get keybindings(): KeybindingDecl[] {
    const declared = [...this.modules.values()].flatMap((m) => m.keybindings ?? []);
    return [...declared, ...this.frameSynthesizedKeybindings()];
  }

  /**
   * Frame-engine bindings, all scoped to the host view so they're only live
   * while one of its panes is focused: the universal position toggles
   * (`t`/`n`/`b` = left/right/bottom region) plus each region view's and
   * section's declared pick letter. Letters colliding with the reserved position
   * keys — or with another pick on the same host — are dropped with a warning
   * (validated here, at the single synthesis point).
   *
   * Regions and sections share one per-host letter space because they share one
   * keyboard scope: two picks on the same letter would be a binding that fires
   * whichever the keymap happened to resolve first.
   */
  private frameSynthesizedKeybindings(): KeybindingDecl[] {
    const POSITION_KEYS = { left: 't', right: 'n', bottom: 'b' } as const;
    const out: KeybindingDecl[] = [];
    for (const view of this.allViews) {
      if (!view.regions?.length && !view.sections?.length) continue;
      const taken = new Set<string>();
      const claim = (key: string, what: string): boolean => {
        if (key === 't' || key === 'n' || key === 'b') {
          console.warn(
            `[registry] ${what} on ${view.id} declares reserved key "${key}" (t/n/b are the universal position toggles) — dropped`,
          );
          return false;
        }
        if (taken.has(key)) {
          console.warn(
            `[registry] ${what} on ${view.id} declares key "${key}", already taken on this pane — dropped`,
          );
          return false;
        }
        taken.add(key);
        return true;
      };

      const positions = new Set((view.regions ?? []).map((r) => r.position ?? 'right'));
      for (const position of positions) {
        out.push({
          key: POSITION_KEYS[position],
          command: `region.toggle:${position}:${view.id}`,
          scope: view.id,
        });
      }
      for (const r of view.regions ?? []) {
        if (!r.key) continue;
        if (!claim(r.key, `region view ${r.id}`)) continue;
        out.push({ key: r.key, command: `region.pick:${r.id}`, scope: view.id });
      }
      for (const s of view.sections ?? []) {
        if (!s.key) continue;
        if (!claim(s.key, `section ${s.id}`)) continue;
        out.push({
          key: s.key,
          command: `section.show:${view.id}:${s.id}`,
          scope: view.id,
        });
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
   * Title of the module that declared a view, for launchers.
   *
   * A view's title says what the pane *is* ("Data flow"); the module's says what
   * feature it belongs to ("Observability"). Those differ often enough that
   * searching a launcher for the feature name found nothing — the pane was
   * present under a name the user had no reason to guess. Every surface that
   * lists views can match and label with this, so no module has to rename its
   * panes to be findable.
   */
  viewOwner(viewId: string): string | undefined {
    for (const m of this.modules.values()) {
      const declared =
        m.panels?.some((p) => p.id === viewId) || m.widgets?.some((w) => w.id === viewId);
      if (declared) return m.title;
    }
    return undefined;
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
    // Recorded here rather than at each call site, so every route to a pane —
    // the launcher, the command palette, spotlight, an agent tool — feeds the
    // same history. Only a real open counts: with no opener installed there is
    // no shell yet, and remembering a pane the user never saw would be a lie.
    if (this.panelOpener) noteViewOpened(panelId);
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
