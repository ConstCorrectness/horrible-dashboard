/**
 * Shared declaration types for everything that can be contributed to the shell.
 * These are the single source of truth — `@horrible/core` re-exports them, and
 * plugins built against `@horribledashboard/sdk` use the exact same shapes as built-in
 * modules.
 */
import type { ComponentType } from 'react';

/** Platform capabilities — see docs/architecture/layout-shell.md for the table. */
export type Capability =
  | 'fs.nativeDialogs'
  | 'shell.revealInOS'
  | 'notifications.system'
  | 'window.multi'
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
  defaultPlacement: 'left' | 'center' | 'right' | 'bottom';
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
   * Hint for where the widget docks when opened as a pane in the workspace
   * (`left|center|right|bottom`). Once the user rearranges, the persisted
   * workspace layout wins. Defaults to `center` when omitted.
   */
  defaultPlacement?: 'left' | 'center' | 'right' | 'bottom';
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
