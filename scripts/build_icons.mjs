// Generate all raster icons from assets/logo.svg (single source of truth for branding).
// Run after changing the logo: pnpm build:icons
import { copyFile, mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import pngToIco from 'png-to-ico';
import sharp from 'sharp';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const LOGO = path.join(ROOT, 'assets/logo.svg');
// Favicon stays small (browsers use 16-48); the desktop app icon gets the full set.
const FAVICON_SIZES = [16, 32, 48];
const APP_ICON_SIZES = [16, 32, 48, 64, 128, 256];

const render = (size) => sharp(LOGO, { density: 300 }).resize(size, size).png().toBuffer();
const appPngs = await Promise.all(APP_ICON_SIZES.map(render));
const faviconIco = await pngToIco(
  FAVICON_SIZES.map((size) => appPngs[APP_ICON_SIZES.indexOf(size)]),
);
const appIco = await pngToIco(appPngs);

const webPublic = path.join(ROOT, 'apps/web/public');
await mkdir(webPublic, { recursive: true });
await writeFile(path.join(webPublic, 'favicon.ico'), faviconIco);
await copyFile(LOGO, path.join(webPublic, 'logo.svg'));

const tauriIcons = path.join(ROOT, 'apps/desktop/src-tauri/icons');
await mkdir(tauriIcons, { recursive: true });
await writeFile(path.join(tauriIcons, 'icon.ico'), appIco);
await writeFile(path.join(tauriIcons, 'icon.png'), appPngs[APP_ICON_SIZES.indexOf(256)]);

console.log(
  `favicon.ico ${faviconIco.length} bytes (${FAVICON_SIZES.join('/')}), ` +
    `icon.ico ${appIco.length} bytes (${APP_ICON_SIZES.join('/')})`,
);
