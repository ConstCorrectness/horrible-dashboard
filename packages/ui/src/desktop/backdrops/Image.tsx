/**
 * A user-supplied wallpaper. No imagery ships with the app — this provider
 * renders what the user uploaded (served back from `$HORRIBLE_DATA_DIR` by the
 * `desktop` backend module) or any URL they pasted.
 */
import { apiUrl } from '@horrible/core';

const FITS = ['cover', 'contain', 'fill', 'tile', 'center'] as const;
type Fit = (typeof FITS)[number];

export function ImageBackdrop({ params }: { params?: Record<string, unknown> }) {
  const raw = typeof params?.url === 'string' ? params.url : '';
  const declaredFit = params?.fit;
  const fit: Fit = FITS.includes(declaredFit as Fit) ? (declaredFit as Fit) : 'cover';
  // A dim veil over the image, because a photograph is usually too busy to read
  // window chrome against and the alternative is asking the user to edit it.
  const dim = clamp(typeof params?.dim === 'number' ? params.dim : 0.15, 0, 0.9);

  if (!raw) {
    return <div className="os-backdrop-image is-empty" aria-hidden="true" />;
  }

  return (
    <div className="os-backdrop-image" aria-hidden="true">
      <div
        className="os-backdrop-image-layer"
        style={{
          backgroundImage: cssUrl(resolveUrl(raw)),
          backgroundSize: fit === 'tile' ? 'auto' : fit === 'center' ? 'auto' : fit,
          backgroundRepeat: fit === 'tile' ? 'repeat' : 'no-repeat',
          backgroundPosition: 'center',
        }}
      />
      {dim > 0 && <div className="os-backdrop-image-veil" style={{ opacity: dim }} />}
    </div>
  );
}

/**
 * A stored wallpaper is recorded as an api-relative path (`/desktop/wallpapers/
 * <id>`) rather than an absolute URL: the backend origin differs between the
 * dev server, a packaged desktop build and a LAN-bound node, so an absolute one
 * baked into a saved workspace stops resolving the moment any of that changes.
 *
 * `/api` has to be added here. `apiUrl` prepends the backend **origin** only —
 * every other caller passes a path that already starts `/api`, and the wallpaper
 * path does not, so handing it straight to `apiUrl` produced a URL one segment
 * short that 404s. It fails silently too: a background-image that does not load
 * paints nothing, so the desktop just looks like the wallpaper was never set.
 */
function resolveUrl(url: string): string {
  if (!url.startsWith('/')) return url;
  return apiUrl(url.startsWith('/api/') ? url : `/api${url}`);
}

/**
 * Quote a URL for `background-image`.
 *
 * Deliberately **not** `CSS.escape`, which escapes CSS *identifiers* — run a
 * path through it and every `/` and `.` comes back backslashed, so the image
 * silently never loads. What actually needs escaping inside a quoted `url()` is
 * the quote character, the backslash and any newline; everything else is
 * literal. The value reaches us from a setting the user typed, so this is a
 * correctness fix and a small hardening at once — an unescaped quote would end
 * the string early and let the rest be read as more CSS.
 */
function cssUrl(url: string): string {
  const escaped = url.replace(/[\\"]/g, '\\$&').replace(/[\n\r]/g, '');
  return `url("${escaped}")`;
}

function clamp(n: number, min: number, max: number): number {
  return Number.isFinite(n) ? Math.min(max, Math.max(min, n)) : min;
}
