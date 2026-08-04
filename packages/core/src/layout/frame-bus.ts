/**
 * Indirection between the registry's synthesized frame commands and the frame
 * controller. The registry can't import the controller (controller → registry
 * would cycle), so the controller installs its handlers here at mount and the
 * synthesized `region.toggle:*` / `region.pick:*` / `section.show:*` commands
 * call through.
 */
import type { RegionPosition } from './types';

export interface FrameCommandHandler {
  /** Toggle the `position` strip on the focused instance of `hostViewId`. */
  togglePosition(hostViewId: string, position: RegionPosition): void;
  /** Reveal `regionViewId` in its host's strip, or close it if already active. */
  pickView(regionViewId: string): void;
  /** Show `section` on the focused instance of `hostViewId`, opening it if needed. */
  revealSection(section: string, hostViewId: string): void;
}

let handler: FrameCommandHandler | null = null;

export function setFrameCommandHandler(impl: FrameCommandHandler): void {
  handler = impl;
}

export function frameCommandHandler(): FrameCommandHandler | null {
  return handler;
}
