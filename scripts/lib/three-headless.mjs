import { readFileSync, existsSync } from 'node:fs';
import { basename, resolve as resolvePath } from 'node:path';

/**
 * Headless shims for Three.js loaders and exporters.
 *
 * three's FBXLoader and GLTFExporter both reach for browser image APIs. Rather
 * than pulling in a full canvas implementation, these shims carry the *encoded
 * bytes* straight through: an image is decoded and re-encoded once by sharp on
 * the way in, and the exporter's "draw onto a canvas, then read a blob back out"
 * dance just moves that buffer around. Nothing is rasterised here, so no pixel is
 * resampled twice and no map is re-compressed on the way out.
 */

/** Stands in for both OffscreenCanvas and the image drawn onto it. */
export class ShimCanvas {
  constructor(width = 1, height = 1) {
    this.width = width;
    this.height = height;
    /** Encoded bytes of the image this canvas is carrying. */
    this.__bytes = null;
    this.__mime = 'image/png';
  }

  getContext() {
    return {
      drawImage: (image) => {
        this.__bytes = image?.__bytes ?? null;
        this.__mime = image?.__mime ?? 'image/png';
      },
      // The exporter uses these for the flipY and DataTexture paths. Flips are
      // already baked into the pixels by resolveFlips before the exporter runs,
      // so by the time these are called there is nothing left for them to do.
      translate: () => {},
      scale: () => {},
      putImageData: () => {
        throw new Error('DataTexture export is not supported by this headless shim');
      },
    };
  }

  async convertToBlob() {
    if (!this.__bytes) {
      throw new Error('canvas has no image bytes — an unsupported texture reached the exporter');
    }
    return new Blob([this.__bytes], { type: this.__mime });
  }

  toBlob(callback) {
    this.convertToBlob().then(callback);
  }
}

/**
 * Minimal FileReader over Node's Blob — GLTFExporter assembles the GLB through
 * one.
 *
 * The exporter calls `readAsArrayBuffer` *before* assigning `onloadend`, so the
 * callback must not fire synchronously: a shim that resolved immediately would
 * find no handler installed yet and the export would hang with no error. Going
 * through the blob's own promise puts the callback a turn later, which is the
 * ordering the real API guarantees.
 */
export class ShimFileReader {
  constructor() {
    this.result = null;
    this.onloadend = null;
    this.onerror = null;
  }

  #finish(promise) {
    promise
      .then((value) => {
        this.result = value;
        this.onloadend?.();
      })
      .catch((err) => {
        if (this.onerror) this.onerror(err);
        else throw err;
      });
  }

  readAsArrayBuffer(blob) {
    this.#finish(blob.arrayBuffer());
  }

  readAsDataURL(blob) {
    this.#finish(
      blob
        .arrayBuffer()
        .then(
          (ab) =>
            `data:${blob.type || 'application/octet-stream'};base64,${Buffer.from(ab).toString('base64')}`,
        ),
    );
  }
}

export const blobRegistry = new Map();
let blobSeq = 0;

export function installShims() {
  globalThis.OffscreenCanvas = ShimCanvas;
  globalThis.FileReader ??= ShimFileReader;
  // FBXLoader reads `window.URL.createObjectURL` for FBX-embedded textures.
  // Keeping the bytes in a Map (rather than minting a real blob: URL) lets the
  // TextureLoader shim resolve them directly, with no fetch involved.
  const createObjectURL = (blob) => {
    const id = `hdblob:${blobSeq++}`;
    blobRegistry.set(id, blob);
    return id;
  };
  const revokeObjectURL = (id) => {
    blobRegistry.delete(id);
  };
  globalThis.window = { URL: { createObjectURL, revokeObjectURL } };
}

/**
 * Replace TextureLoader with one that reads from disk (or the blob registry),
 * downscales and re-encodes with sharp, and hands back a texture whose `image`
 * is a ShimCanvas carrying the encoded bytes.
 */
export function installTextureLoader(THREE, sharp, opts, pending, report) {
  THREE.TextureLoader.prototype.load = function load(url, onLoad) {
    const texture = new THREE.Texture();
    const loaderPath = this.path ?? '';
    const task = (async () => {
      let bytes;
      let label = url;
      if (blobRegistry.has(url)) {
        bytes = Buffer.from(await blobRegistry.get(url).arrayBuffer());
        label = `<embedded ${url}>`;
      } else {
        const file = resolvePath(loaderPath, url);
        if (!existsSync(file)) {
          report?.missingTextures?.push(url);
          return;
        }
        bytes = readFileSync(file);
        label = basename(file);
      }

      let img = sharp(bytes, { failOn: 'none' });
      const meta = await img.metadata();
      const longest = Math.max(meta.width ?? 0, meta.height ?? 0);
      if (opts.textureSize > 0 && longest > opts.textureSize) {
        img = img.resize({ width: opts.textureSize, height: opts.textureSize, fit: 'inside' });
      }
      const mime = `image/${opts.textureFormat ?? 'png'}`;
      let encoded;
      if (opts.textureFormat === 'webp') {
        encoded = await img.webp({ quality: 88 }).toBuffer();
      } else if (opts.textureFormat === 'jpeg') {
        encoded = await img.jpeg({ quality: 90 }).toBuffer();
      } else {
        encoded = await img.png({ compressionLevel: 9 }).toBuffer();
      }
      const out = await sharp(encoded).metadata();

      const canvas = new ShimCanvas(out.width, out.height);
      canvas.__bytes = encoded;
      canvas.__mime = mime;
      texture.image = canvas;
      texture.userData.mimeType = mime;
      texture.needsUpdate = true;
      report?.textures?.push({
        name: label,
        from: bytes.length,
        to: encoded.length,
        size: `${out.width}x${out.height}`,
      });
      onLoad?.(texture);
    })();
    pending.push(task);
    return texture;
  };
}

/**
 * Bake out every `flipY` before the exporter sees it.
 *
 * FBX and glTF disagree about which way up a texture's V axis runs, so
 * FBXLoader hands back textures marked `flipY = true` and GLTFExporter honours
 * that by transforming the canvas it draws onto. Our canvas is a passthrough and
 * cannot flip, so the flip happens here instead — in sharp, on the real pixels —
 * and the flag is cleared so the exporter has nothing left to do.
 *
 * A texture carrying no bytes is a hard failure rather than a skip: it would
 * come out the right way up only by accident, and an upside-down character is
 * the kind of thing that gets blamed on the UVs for an hour.
 */
export async function resolveFlips(root, sharp) {
  const seen = new Set();
  const jobs = [];
  root.traverse((obj) => {
    const materials = Array.isArray(obj.material)
      ? obj.material
      : obj.material
        ? [obj.material]
        : [];
    for (const mat of materials) {
      for (const value of Object.values(mat)) {
        if (!value?.isTexture || value.flipY !== true || seen.has(value)) continue;
        seen.add(value);
        const canvas = value.image;
        if (!canvas?.__bytes) {
          throw new Error(
            `texture "${value.name || '<unnamed>'}" is marked flipY but carries no image data`,
          );
        }
        jobs.push(
          sharp(canvas.__bytes)
            .flip()
            .toBuffer()
            .then((bytes) => {
              canvas.__bytes = bytes;
              value.flipY = false;
            }),
        );
      }
    }
  });
  await Promise.all(jobs);
  return jobs.length;
}
