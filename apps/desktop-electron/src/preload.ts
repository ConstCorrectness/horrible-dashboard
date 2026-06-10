import { contextBridge } from "electron";

/**
 * Platform capability bridge (see docs/architecture/layout-shell.md and
 * docs/architecture/electron-shell.md). The shared frontend never forks per
 * platform; it branches on `window.horriblePlatform.capabilities` instead.
 *
 * Declaration list only for now — none of these are implemented yet.
 */
const horriblePlatform = {
  shell: "electron",
  capabilities: [
    "fs.nativeDialogs",
    "shell.revealInOS",
    "notifications.system",
    "window.multi",
    "shortcuts.global",
    "tray",
  ],
} as const;

contextBridge.exposeInMainWorld("horriblePlatform", horriblePlatform);
