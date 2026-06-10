// Generate all raster icons from logo.svg (single source of truth for branding).
// Run after changing logo.svg: pnpm build:icons
import { copyFile, mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import pngToIco from 'png-to-ico';
import sharp from 'sharp';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const LOGO = path.join(ROOT, 'logo.svg');
const SIZES = [16, 32, 48, 64, 128, 256];

const pngs = await Promise.all(
  SIZES.map((size) => sharp(LOGO, { density: 300 }).resize(size, size).png().toBuffer()),
);
const ico = await pngToIco(pngs);

const webPublic = path.join(ROOT, 'apps/web/public');
await mkdir(webPublic, { recursive: true });
await writeFile(path.join(webPublic, 'favicon.ico'), ico);
await copyFile(LOGO, path.join(webPublic, 'logo.svg'));

const tauriIcons = path.join(ROOT, 'apps/desktop/src-tauri/icons');
await mkdir(tauriIcons, { recursive: true });
await writeFile(path.join(tauriIcons, 'icon.ico'), ico);
await writeFile(path.join(tauriIcons, 'icon.png'), pngs[SIZES.indexOf(256)]);

console.log('wrote apps/web/public/{favicon.ico,logo.svg} and apps/desktop/src-tauri/icons/{icon.ico,icon.png}');
