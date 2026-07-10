/**
 * Shared declaration types for everything that can be contributed to the shell.
 * These are the single source of truth — `@horrible/core` re-exports them, and
 * plugins built against `@horribledashboard/sdk` use the exact same shapes as built-in
 * modules.
 */
import type { ComponentType } from 'react';

/** Platform capabilities — see docs/architecture/layout-shell.mdx for the table.
 * The `window.*`/`chrome.*` entries are the contract for the phase-2 native
 * shell (workspace-per-OS-window, native chrome hosting the workspace tabs,
 * OS fullscreen); nothing implements them yet. */
export type Capability =
  | 'fs.nativeDialogs'
  | 'shell.revealInOS'
  | 'notifications.system'
  | 'window.multi'
  | 'window.perWorkspace'
  | 'window.fullscreen'
  | 'chrome.workspaceTabs'
  | 'shortcuts.global'
  | 'tray';

/**
 * Minimal JSON Schema subset used to describe an agent tool's arguments to the
 * model. Not a full draft implementation — just the shapes the orchestrator
 * passes through to the provider's tool definitions. See
 * docs/architecture/agent-tools.md.
 */
export interface JSONSchema {
  type?: 'object' | 'string' | 'number' | 'integer' | 'boolean' | 'array';
  description?: string;
  properties?: Record<string, JSONSchema>;
  items?: JSONSchema;
  required?: string[];
  enum?: Array<string | number | boolean>;
  default?: unknown;
}

/**
 * Opts a command into being callable by the agent orchestrator. App-wide verbs
 * (layout, navigation) are exposed this way; widget/panel-specific actions use
 * `AgentToolDecl` instead. See docs/architecture/agent-tools.md.
 */
export interface AgentCommandDecl {
  /** Natural-language description handed to the model. */
  description: string;
  params?: JSONSchema;
  /** Falsy = read-only: never gated by the permission engine. */
  sideEffect?: boolean;
}

/**
 * A widget/panel-specific action or state-read exposed to the agent (MCP-style).
 * The `handler` and live arguments stay frontend-owned; only the serialized
 * schema and `specifierTemplate` are pushed to the backend in the capability
 * manifest. See docs/architecture/agent-tools.md.
 */
export interface AgentToolDecl {
  /** Namespaced like a command id, e.g. `terminal.exec`. */
  name: string;
  /** Natural-language description handed to the model. */
  description: string;
  params?: JSONSchema;
  /** Falsy = read-only: never gated by the permission engine. */
  sideEffect?: boolean;
  /**
   * Template the backend renders into the permission **specifier** it matches
   * rule specifiers against; `{name}` placeholders are filled from the call args.
   * The tool name is implicit (never part of the template), so a terminal exec
   * tool uses `"{command}"` → specifier `npm run build`, matched by a rule like
   * `terminal.exec(npm run *)`. Declarative so the gate stays fully server-side.
   * Omit for a tool gated by bare name only.
   */
  specifierTemplate?: string;
  handler: (args: Record<string, unknown>) => unknown | Promise<unknown>;
}

export interface CommandDecl {
  /** `module.verb`, e.g. `dashboard.open`. */
  id: string;
  title: string;
  run: () => void | Promise<void>;
  /** When present, the command is callable by the agent orchestrator. */
  agent?: AgentCommandDecl;
}

/**
 * How a pane participates in the distributed peer fabric's `collab` channel.
 *
 * Declaring this makes a pane **network-aware** at the contract level: the shell
 * and the `useCollab` host hook know the pane syncs its state across nodes, so a
 * standard Share affordance + live peer-presence count come for free instead of
 * each pane hand-wiring the collab channel. See docs/modules/network.mdx (collab)
 * and docs/architecture/plugin-sdk.mdx.
 */
export interface CollabDecl {
  /**
   * Room-key strategy. `shared` joins one well-known room across every instance of
   * this view (e.g. a single shared scratchpad everyone edits together);
   * `instance` gives each open pane its own room (collaborators must share the key
   * out of band). The host derives the actual `paneKey` from this + `key`.
   */
  room: 'shared' | 'instance';
  /** Fixed key suffix; defaults to the view id. Combined with `room` into a paneKey. */
  key?: string;
  /**
   * Whether sharing is on the moment the pane opens. Defaults to false — the user
   * opts in via the Share toggle (the privacy-preserving default).
   */
  autoShare?: boolean;
}

/**
 * Declaration of a Panel View. A Panel is a multi-instance view (e.g. terminals,
 * editor buffers) by default. Each time it is opened, a new Pane instance is
 * created, unless `singleton` is set to true.
 */
export interface PanelDecl {
  id: string;
  title: string;
  component: ComponentType;
  /**
   * How this pane participates in the frame layout: `document` panes tab into
   * center areas, `widget` panes take a center area of their own, `tool` panes
   * live in the docks. See docs/architecture/windowing.mdx.
   */
  role: PaneRole;
  /** Region strips (Blender N/T-panel style) this view hosts inside its area. */
  regions?: RegionViewDecl[];
  /** Glyph for the activity rail / area-header type switcher. */
  icon?: string;
  /** For role `tool`: which dock it opens in by default. Defaults to `left`. */
  defaultDock?: DockSide;
  /**
   * When true, only one Pane instance running this view can exist in a workspace —
   * opening it again focuses the existing one. When false/omitted, each open
   * creates a new Pane instance (e.g. terminals, editor buffers).
   */
  singleton?: boolean;
  /** Actions/state-reads this panel exposes to the agent orchestrator. */
  agentTools?: AgentToolDecl[];
  /** When set, this pane is network-aware: it syncs over the `collab` channel. */
  collab?: CollabDecl;
}

/**
 * Declaration of a Widget View. A Widget is a singleton view (e.g. dashboard welcome
 * banner, observability stats, settings). Only one Pane instance running this
 * view can exist in a workspace at any time; opening it focuses the existing pane.
 */
export interface WidgetDecl {
  id: string;
  title: string;
  component: ComponentType;
  requiredCapabilities?: Capability[];
  /**
   * How this pane participates in the frame layout: `document` panes tab into
   * center areas, `widget` panes take a center area of their own, `tool` panes
   * live in the docks. See docs/architecture/windowing.mdx.
   */
  role: PaneRole;
  /** Region strips (Blender N/T-panel style) this view hosts inside its area. */
  regions?: RegionViewDecl[];
  /** Glyph for the activity rail / area-header type switcher. */
  icon?: string;
  /** For role `tool`: which dock it opens in by default. Defaults to `left`. */
  defaultDock?: DockSide;
  /** Actions/state-reads this widget exposes to the agent orchestrator. */
  agentTools?: AgentToolDecl[];
  /** When set, this pane is network-aware: it syncs over the `collab` channel. */
  collab?: CollabDecl;
}

/**
 * A JSON-serializable snapshot of a pane's current state/selection, read by the
 * agent on demand (pull, not push). Returned by an `useAgentContext` provider.
 */
export type AgentContextSnapshot = Record<string, unknown>;

/**
 * Host hook signature: a pane instance registers a provider returning its
 * current agent-readable snapshot. The host keys providers by pane instance id
 * and invokes one on demand for `get_pane_context`. Implemented in packages/ui;
 * declared here so modules and plugins code against a stable contract.
 */
export type UseAgentContext = (provider: () => AgentContextSnapshot) => void;

export interface KeybindingDecl {
  /** e.g. `mod+k` — `mod` is ctrl (or cmd on macOS). */
  key: string;
  command: string;
  /**
   * Pane view id this binding is scoped to (e.g. `terminal.instance`). When set,
   * the binding is only active while a pane of that view is focused, and it
   * **takes precedence over a plain global binding** for the same key — so a
   * focused pane can shadow a global shortcut. Omit for a global binding active
   * everywhere.
   */
  scope?: string;
  /**
   * For a *global* binding (no `scope`): when true it wins even if the focused
   * pane has a scoped binding for the same key. The escape hatch for shortcuts
   * that must never be shadowed (e.g. the command palette). Ignored on a scoped
   * binding.
   */
  override?: boolean;
}

/** The value kinds a setting can hold in v1. */
export type SettingType = 'string' | 'number' | 'boolean' | 'enum';

/**
 * A user-configurable setting a module or plugin contributes to the settings
 * page (VS Code `contributes.configuration` style). The declared `default`
 * applies until the user overrides it; the schema lives here on the frontend,
 * while only the overridden values are persisted server-side.
 */
export interface SettingDecl {
  /** `<module>.<name>` — for plugins, `<pluginId>.<name>` (same namespacing as ids). */
  key: string;
  title: string;
  description?: string;
  type: SettingType;
  default: string | number | boolean;
  /** Allowed values when `type === 'enum'`; ignored otherwise. */
  enumValues?: string[];
}

/** Envelope for every message on the shared `/ws` socket. */
export interface WsMessage {
  channel: string;
  event: string;
  data?: unknown;
}

/**
 * How a pane participates in the frame layout. `document` panes live in center
 * areas and stack as tabs; `tool` panes live in the shell docks (left/right/
 * bottom), one visible per dock; `widget` panes live in center areas one-per-area
 * with no tab strip. Zones are strict: tools never open in the center, documents
 * and widgets never dock. See docs/architecture/windowing.mdx.
 */
export type PaneRole = 'document' | 'tool' | 'widget';

/** Positions a region strip can occupy inside its host pane's area. */
export type RegionPosition = 'left' | 'right' | 'bottom';

/** The shell's fixed tool docks. */
export type DockSide = 'left' | 'right' | 'bottom';

/**
 * One view stacked in a host pane's **region** — a toggleable, resizable strip
 * (Blender N/T-panel style) rendered inside the host's area and persisted
 * per pane instance. Successor of the panel-group companion. Universal position
 * keys (`t`/`n`/`b` = left/right/bottom) toggle the strip; the declared `key`
 * picks this view within its position. See docs/architecture/panel-groups.mdx.
 */
export interface RegionViewDecl {
  /** View id of any registered panel/widget (e.g. `code.outline`). */
  id: string;
  /** Tooltip / aria-label shown on the region tab. */
  label: string;
  /** Single glyph for the region tab; falls back to `label`'s first character. */
  icon?: string;
  /**
   * Pick/cycle letter within the region position, scoped to the host pane's
   * focus. Plain letters only; must not be `t`, `n`, or `b` (reserved for the
   * universal position toggles) — violations are dropped with a console warning.
   */
  key?: string;
  /** Which strip this view stacks into. Defaults to `right`. */
  position?: RegionPosition;
  /** Open the strip with this view active when the host pane is first created. */
  defaultOpen?: boolean;
  /** Initial strip size in px (width for left/right, height for bottom). */
  defaultSize?: number;
}
