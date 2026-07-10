/**
 * Indirection between the registry's synthesized region commands and the frame
 * controller. The registry can't import the controller (controller → registry
 * would cycle), so the controller installs its handlers here at mount and the
 * synthesized `region.toggle:*` / `region.pick:*` commands call through.
 */
import type { RegionPosition } from './types';

export interface RegionCommandHandler {
  /** Toggle the `position` strip on the focused instance of `hostViewId`. */
  togglePosition(hostViewId: string, position: RegionPosition): void;
  /** Reveal `regionViewId` in its host's strip, or close it if already active. */
  pickView(regionViewId: string): void;
}

let handler: RegionCommandHandler | null = null;

export function setRegionCommandHandler(impl: RegionCommandHandler): void {
  handler = impl;
}

export function regionCommandHandler(): RegionCommandHandler | null {
  return handler;
}
