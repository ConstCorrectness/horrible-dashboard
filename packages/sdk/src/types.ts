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
  | 'tray'
  // The embedded browser can pop a page out to a real native OS window (a true
  // browser, bypassing iframe X-Frame-Options/CSP). Desktop-only; the browser
  // build leaves the window seam null and falls back to opening a new tab.
  | 'browser.nativeWindow';

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
  /**
   * Short name for the minibuffer's slash form, without the slash — `save` makes
   * the command run as `/save`. Opt-in and global, so keep it unambiguous: two
   * commands claiming the same slash name is a conflict the minibuffer resolves
   * by first-registered-wins. Everything stays reachable by fuzzy title search
   * whether or not it declares one.
   */
  slash?: string;
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
   * Where this pane opens by *default*: `document` panes tab into center areas,
   * `widget` panes take a center area of their own, `tool` panes go to a dock.
   * This is a default, not a restriction — see `dockable`.
   * See docs/architecture/windowing.mdx.
   */
  role: PaneRole;
  /**
   * Docks this view may be toggled into from a rail, beyond wherever `role` puts
   * it by default. Omit and the view is dockable only if `role: 'tool'` (in which
   * case `defaultDock` names the side) — so declaring this is how a `widget` or
   * `document` view earns a rail glyph while still opening in the center.
   * The first entry is the preferred side.
   */
  dockable?: DockSide | DockSide[];
  /** Region strips (Blender N/T-panel style) this view hosts inside its area. */
  regions?: RegionViewDecl[];
  /** In-pane sections (tabs) this view switches between. See `SectionDecl`. */
  sections?: SectionDecl[];
  /**
   * This view accepts `explorerSources` contributed by other modules, appended
   * after its own `sections`. Exactly one view should set it (Explorer); the flag
   * exists so the registry resolves the host by declaration rather than by
   * hardcoding a module's view id.
   */
  explorerHost?: boolean;
  /**
   * This view exists only *inside* another pane — as a region strip, a section
   * body, or an explorer source — and is not a destination of its own.
   *
   * It stays fully registered, so regions, sections, `openPaneInArea` and
   * dragging a strip out into the center all keep working. What it loses is
   * every affordance that would present it as a second, competing home for the
   * same content: no `pane.open:<id>` command, no entry in the area-header type
   * switcher or empty-area picker, no rail glyph (embedded implies never
   * dockable), and no top-level row in the agent's `list_available_panes` — it
   * is listed under its host instead, so `show` still reaches it by name.
   */
  embedded?: boolean;
  /** Glyph for the activity rail / area-header type switcher. */
  icon?: string;
  /** For role `tool`: which dock it opens in by default. Defaults to `left`. */
  defaultDock?: DockSide;
  /**
   * For role `tool`: the dock extent (px width for left/right, height for bottom)
   * this view opens at the first time. Thereafter the pane remembers whatever the
   * user dragged it to (`PaneState.dockSize`), so this is only the starting point.
   * Omit to inherit the dock's current size.
   */
  defaultDockSize?: number;
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
  /**
   * @deprecated Alias for `capture: { mode: 'keyboard', escape: 'passthrough' }`.
   * Means "this view needs the plain-letter keys (t, n, b) for itself".
   */
  editor?: boolean;
  /**
   * This view takes the keyboard (and optionally the mouse) while it is focused.
   * Declaring it here grants capture on focus and releases it on blur, which is
   * what editors and terminals want. A view that captures only *sometimes* (a
   * game, and only once pointer-locked) omits this and calls
   * `useCapture().request()` at the moment it needs the keyboard.
   */
  capture?: PaneCaptureDecl;
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
   * Where this pane opens by *default*: `document` panes tab into center areas,
   * `widget` panes take a center area of their own, `tool` panes go to a dock.
   * This is a default, not a restriction — see `dockable`.
   * See docs/architecture/windowing.mdx.
   */
  role: PaneRole;
  /**
   * Docks this view may be toggled into from a rail, beyond wherever `role` puts
   * it by default. Omit and the view is dockable only if `role: 'tool'` (in which
   * case `defaultDock` names the side) — so declaring this is how a `widget` or
   * `document` view earns a rail glyph while still opening in the center.
   * The first entry is the preferred side.
   */
  dockable?: DockSide | DockSide[];
  /** Region strips (Blender N/T-panel style) this view hosts inside its area. */
  regions?: RegionViewDecl[];
  /** In-pane sections (tabs) this view switches between. See `SectionDecl`. */
  sections?: SectionDecl[];
  /**
   * This view accepts `explorerSources` contributed by other modules, appended
   * after its own `sections`. Exactly one view should set it (Explorer); the flag
   * exists so the registry resolves the host by declaration rather than by
   * hardcoding a module's view id.
   */
  explorerHost?: boolean;
  /**
   * This view exists only *inside* another pane — as a region strip, a section
   * body, or an explorer source — and is not a destination of its own.
   *
   * It stays fully registered, so regions, sections, `openPaneInArea` and
   * dragging a strip out into the center all keep working. What it loses is
   * every affordance that would present it as a second, competing home for the
   * same content: no `pane.open:<id>` command, no entry in the area-header type
   * switcher or empty-area picker, no rail glyph (embedded implies never
   * dockable), and no top-level row in the agent's `list_available_panes` — it
   * is listed under its host instead, so `show` still reaches it by name.
   */
  embedded?: boolean;
  /** Glyph for the activity rail / area-header type switcher. */
  icon?: string;
  /** For role `tool`: which dock it opens in by default. Defaults to `left`. */
  defaultDock?: DockSide;
  /**
   * For role `tool`: the dock extent (px width for left/right, height for bottom)
   * this view opens at the first time. Thereafter the pane remembers whatever the
   * user dragged it to (`PaneState.dockSize`), so this is only the starting point.
   * Omit to inherit the dock's current size.
   */
  defaultDockSize?: number;
  /** Actions/state-reads this widget exposes to the agent orchestrator. */
  agentTools?: AgentToolDecl[];
  /** When set, this pane is network-aware: it syncs over the `collab` channel. */
  collab?: CollabDecl;
  /**
   * @deprecated Alias for `capture: { mode: 'keyboard', escape: 'passthrough' }`.
   * Means "this view needs the plain-letter keys (t, n, b) for itself".
   */
  editor?: boolean;
  /**
   * This view takes the keyboard (and optionally the mouse) while it is focused.
   * Declaring it here grants capture on focus and releases it on blur, which is
   * what editors and terminals want. A view that captures only *sometimes* (a
   * game, and only once pointer-locked) omits this and calls
   * `useCapture().request()` at the moment it needs the keyboard.
   */
  capture?: PaneCaptureDecl;
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

/**
 * How much of the input a view takes while focused.
 *
 * `keyboard` swallows unmodified keys only (an editor: `t` types a `t`, but
 * `mod+s` still saves). `full` swallows every shortcut but the view's own — for
 * a pointer-locked game. `pointer` takes the mouse and nothing else.
 */
export interface PaneCaptureDecl {
  mode: 'keyboard' | 'pointer' | 'full';
  /**
   * What a tap of Escape does. `release` gives input back; `passthrough` hands
   * Escape to the view and releases only on a **hold** — but the browser only
   * permits that with the Keyboard Lock API (Chromium, in fullscreen), so it
   * degrades to `release` elsewhere and the on-screen hint says which is live.
   * Defaults to `release`.
   */
  escape?: 'release' | 'passthrough';
}

export interface KeybindingDecl {
  /**
   * e.g. `mod+k` — `mod` is ctrl (or cmd on macOS). Space-separated strokes are a
   * **sequence** (`mod+k mod+s`). A key token is the character it produces
   * (`w`), or its physical position when prefixed (`code:KeyW`) — use `code:` for
   * anything positional (game movement), because a character spec follows the
   * user's layout. See docs/architecture/keybindings.mdx.
   */
  key: string;
  command: string;
  /**
   * @deprecated Use `when: "paneFocus == '<viewId>'"`, which this is shorthand
   * for. Kept so existing manifests and plugins keep working.
   */
  scope?: string;
  /**
   * Condition over the closed context-key vocabulary — `paneFocus`,
   * `paneInstance`, `capture`, `captureView`, `textInput`, `dialogOpen`,
   * `fullscreenArea`, `shellView`, `platform`, `host` — combined with `&&`,
   * `||`, `!`, `==`, `!=` and parentheses. A binding naming `paneFocus` beats one
   * that names nothing, which is what lets a focused pane shadow a global.
   */
  when?: string;
  /**
   * Win even against a more specific `when`. The escape hatch for shortcuts that
   * must never be shadowed (the command palette, the minibuffer).
   */
  override?: boolean;
  /** Explicit thumb on the scale when two conditions are equally specific. */
  priority?: number;
  /**
   * Stay reachable while a pane holds the keyboard. Reserve this for shell verbs
   * a user must always be able to reach — normally a capturing pane is meant to
   * swallow everything but its own bindings.
   */
  capturePassthrough?: boolean;
  /**
   * Restrict this default to certain hosts. Use when a chord is unreachable in
   * one of them — `mod+1..9` is browser tab switching and never reaches the page,
   * so the workspace-switch bindings ship as `mod+` on desktop and `alt+` in the
   * browser.
   */
  hosts?: ('browser' | 'desktop')[];
  /** Restrict this default to certain platforms (IME and macOS collisions). */
  platforms?: ('mac' | 'win' | 'linux')[];
  /**
   * Register with the OS so it fires while the app is unfocused. Desktop only
   * (needs the `shortcuts.global` capability); ignored in the browser.
   */
  global?: boolean;
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
  /**
   * Tuck this setting into the contributor's collapsed **Advanced** fold instead of
   * its main list.
   *
   * For the knobs that are correct to leave alone: infrastructure a working setup
   * never touches (TURN credentials, STUN hosts, relay URLs), and anything whose
   * wrong value degrades quietly rather than loudly. It is a *presentation* hint
   * only — an advanced setting reads, writes, and resets exactly like any other, and
   * a user override keeps working whether or not the fold is open.
   *
   * It is not a substitute for a good `description`. If a setting needs a warning,
   * write the warning.
   */
  advanced?: boolean;
}

/** Envelope for every message on the shared `/ws` socket. */
export interface WsMessage {
  channel: string;
  event: string;
  data?: unknown;
}

/**
 * A pane's *default* placement in the frame. `document` panes live in center
 * areas and stack as tabs; `tool` panes live in the shell docks (left/right/
 * bottom), one visible per dock; `widget` panes live in center areas one-per-area
 * with no tab strip.
 *
 * Roles are no longer strict zones. Any view can be moved into a center area,
 * and a view opts into being dock/rail-toggleable with `dockable` (implied for
 * `role: 'tool'`). The role only decides where an `openPanel` with no further
 * instruction puts it. See docs/architecture/windowing.mdx.
 */
export type PaneRole = 'document' | 'tool' | 'widget';

/** Positions a region strip can occupy inside its host pane's area. */
export type RegionPosition = 'left' | 'right' | 'bottom';

/** The shell's fixed tool docks. */
export type DockSide = 'left' | 'right' | 'bottom';

/**
 * One section of a multi-section pane — an in-pane tab, the sibling of a region
 * strip. A region is a *companion* alongside the main content; a section
 * *replaces* it, which is what lets several formerly-separate panes become one.
 *
 * The host owns the tab strip, the persisted active section (per pane instance),
 * the synthesized `section.show:<host>:<id>` command and pick key, and the
 * agent's ability to name a section — so a module does not hand-roll any of it.
 * See docs/architecture/windowing.mdx.
 */
export interface SectionDecl {
  /** Unique within the host pane, e.g. `play`, `friends`. */
  id: string;
  /** Tab text, and what `show("friends")` matches against. */
  label: string;
  /** Glyph for the tab; falls back to `label`'s first character. */
  icon?: string;
  /**
   * Section body, given either inline as a component or as the id of a
   * registered (usually `embedded`) view. Exactly one of the two.
   */
  component?: ComponentType;
  /** View id whose component renders this section. Alternative to `component`. */
  view?: string;
  /**
   * Pick letter within the host pane's focus scope. Plain letters only; must not
   * be `t`, `n`, or `b` (the universal region-position toggles) — violations are
   * dropped with a console warning, the same rule regions follow.
   */
  key?: string;
  /** The section shown when a pane of this view first opens. First one wins. */
  default?: boolean;
}

/**
 * A browser a module contributes to the **Explorer** pane.
 *
 * Sections are declared by the pane that owns them, which is right for a pane
 * whose tabs are its own — but wrong for Explorer, whose whole purpose is to be
 * the one place you go to find *something*, wherever it lives. A module can't
 * reach into another module's decl (nor should it), so Explorer publishes this
 * extension point instead and the registry folds every contribution into its
 * section list. Order follows module registration; a plugin's browser sits
 * alongside the built-ins with no special casing.
 *
 * Structurally a `SectionDecl` — everything downstream (the tab strip, the
 * persisted active tab, `show("notebooks")`, the pick key, the agent's section
 * argument) is the section machinery, not a second mechanism.
 */
export type ExplorerSourceDecl = SectionDecl;

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
