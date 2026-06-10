import { app, BrowserWindow } from "electron";
import * as path from "node:path";

/**
 * Dev URL contract: the Electron shell wraps the same web frontend as the
 * browser layout. In dev it points at the Vite dev server (apps/web), which
 * defaults to http://localhost:5173 and can be overridden via ELECTRON_DEV_URL.
 */
const DEV_URL = process.env.ELECTRON_DEV_URL ?? "http://localhost:5173";

const PLACEHOLDER_PATH = path.join(app.getAppPath(), "placeholder.html");

function createWindow(): void {
  const win = new BrowserWindow({
    width: 1280,
    height: 800,
    title: "horrible-dashboard (electron)",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  // If the dev server is not running, fall back to a bundled placeholder page
  // that tells the user how to start it. The handler stays registered so a
  // failed retry from the placeholder lands back on the placeholder.
  win.webContents.on("did-fail-load", (_event, errorCode, _desc, validatedURL) => {
    const isAborted = errorCode === -3; // ERR_ABORTED, e.g. user navigated away
    if (!isAborted && validatedURL.startsWith("http")) {
      void win.loadFile(PLACEHOLDER_PATH, { query: { devUrl: DEV_URL } });
    }
  });

  // Rejection is also surfaced via did-fail-load above; swallow it here so we
  // don't crash on an unhandled promise rejection.
  win.loadURL(DEV_URL).catch(() => undefined);
}

void app.whenReady().then(() => {
  createWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});
