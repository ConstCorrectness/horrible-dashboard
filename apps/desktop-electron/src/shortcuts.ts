import { globalShortcut, type BrowserWindow } from 'electron';

export class ShortcutsManager {
  private registeredAccelerators = new Set<string>();

  constructor(private mainWindow: BrowserWindow) {}

  public register(accelerators: string[]): string[] {
    this.unregisterAll();
    const successful: string[] = [];

    for (const rawAccel of accelerators) {
      // Normalize common accelerator variations if needed
      // Electron supports: Command, Cmd, Control, Ctrl, CommandOrControl, CmdOrCtrl, Alt, Option, AltGr, Shift, Super
      const accel = rawAccel;
      try {
        const ok = globalShortcut.register(accel, () => {
          if (!this.mainWindow.isDestroyed()) {
            this.mainWindow.webContents.send('global-shortcut', { accelerator: rawAccel });
          }
        });
        if (ok) {
          this.registeredAccelerators.add(accel);
          successful.push(rawAccel);
        } else {
          console.warn(`[shortcuts] Failed to register global shortcut: ${rawAccel}`);
        }
      } catch (err) {
        console.error(`[shortcuts] Error registering shortcut ${rawAccel}:`, err);
      }
    }

    return successful;
  }

  public unregisterAll(): void {
    globalShortcut.unregisterAll();
    this.registeredAccelerators.clear();
  }
}
