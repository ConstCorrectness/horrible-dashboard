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
}

export interface KeybindingDecl {
  /** e.g. `mod+k` — `mod` is ctrl (or cmd on macOS). */
  key: string;
  command: string;
}

/** Envelope for every message on the shared `/ws` socket. */
export interface WsMessage {
  channel: string;
  event: string;
  data?: unknown;
}
