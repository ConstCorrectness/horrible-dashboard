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
  /**
   * Hint for where the widget docks when opened as a pane in the workspace
   * (`left|center|right|bottom`). Once the user rearranges, the persisted
   * workspace layout wins. Defaults to `center` when omitted.
   */
  defaultPlacement?: 'left' | 'center' | 'right' | 'bottom';
}

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
