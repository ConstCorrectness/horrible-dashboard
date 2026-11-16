/**
 * The app-wide motion switch.
 *
 * `desktop.animations` (kept under the floating desktop's key, so an existing
 * "off" survives the change) is mirrored onto `<html data-motion>`, which every
 * stylesheet with an animation reads — the workbench's pane entrance, the region
 * strips, the duel and replay boards.
 */
import { getSetting, settingsStore } from '@horrible/core';

export const ANIMATIONS_SETTING_KEY = 'desktop.animations';

/** Keep `data-motion` in step with the setting. Returns the unsubscribe. */
export function syncMotionAttribute(): () => void {
  const apply = () => {
    document.documentElement.dataset.motion =
      getSetting<boolean>(ANIMATIONS_SETTING_KEY) === false ? 'off' : 'on';
  };
  apply();
  return settingsStore.subscribe(apply);
}
