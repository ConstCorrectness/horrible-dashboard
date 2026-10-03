/**
 * Scrive's directives for a real Jupyter Book 2 / mystmd build.
 *
 * Scrive pushes this file beside the site's sources in jupyter-book mode and lists it
 * under `project.plugins` in the pushed `myst.yml`. Without it, a `{r3f}` scene would
 * render in Jupyter Book as an "unknown directive" warning box.
 *
 * - `{r3f} scenes/orbit.tsx` → an iframe of the scene's prebuilt page,
 *   `_scrive/scene/scenes/orbit.html`, which Scrive also pushes (with the scene
 *   runtime) and the workflow copies into the built site. `:params:` become the
 *   page's URL fragment, as in a static Scrive site.
 * - `{video} media/clip.mp4` → an image node, which MyST draws as a `<video>`.
 * - `{pending}` → nothing: a section still to write never reaches readers.
 *
 * `BASE_URL` is the path the site is served under (`/repo` on GitHub Pages), set by
 * the workflow; Jupyter Book reads the same variable.
 */
import path from 'node:path';

/** A path in the page, made a path in the site (the build runs at the site root). */
function sitePath(arg, vfile) {
  const target = String(arg ?? '').trim();
  if (target.startsWith('/')) return target.replace(/^\/+/, '');
  const file = vfile && vfile.path ? path.resolve(String(vfile.path)) : '';
  const dir = file ? path.relative(process.cwd(), path.dirname(file)) : '';
  return path.posix.normalize([dir.split(path.sep).join('/'), target].filter(Boolean).join('/'));
}

/** `:params:` (a JSON object of tweak specs) → the values a scene starts with. */
function paramValues(raw) {
  if (!raw) return {};
  try {
    const spec = JSON.parse(String(raw));
    if (!spec || typeof spec !== 'object' || Array.isArray(spec)) return {};
    return Object.fromEntries(
      Object.entries(spec).map(([key, s]) => [key, s && typeof s === 'object' ? s.value : s]),
    );
  } catch {
    return {};
  }
}

const r3f = {
  name: 'r3f',
  doc: 'A React Three Fiber scene from the site, run from its prebuilt Scrive page.',
  arg: { type: String, doc: 'The scene file (.tsx), relative to the page.', required: true },
  options: {
    height: { type: Number, doc: 'Height in pixels.' },
    params: { type: String, doc: 'JSON object of tweakable values.' },
    poster: { type: String, doc: 'A still image of the scene.' },
  },
  run(data, vfile) {
    const scene = sitePath(data.arg, vfile).replace(/\.tsx$/, '');
    const base = String(process.env.BASE_URL ?? '').replace(/\/+$/, '');
    const values = paramValues(data.options?.params);
    const hash = Object.keys(values).length ? `#${encodeURIComponent(JSON.stringify(values))}` : '';
    return [
      {
        type: 'iframe',
        src: `${base}/_scrive/scene/${scene}.html${hash}`,
        width: '100%',
        title: `3D scene ${scene}`,
      },
    ];
  },
};

const video = {
  name: 'video',
  doc: 'A video file from the site.',
  arg: { type: String, doc: 'The video file, relative to the page.', required: true },
  options: { width: { type: String } },
  run(data) {
    const node = { type: 'image', url: String(data.arg ?? '').trim() };
    if (data.options?.width) node.width = String(data.options.width);
    return [node];
  },
};

const pending = {
  name: 'pending',
  doc: 'A section Scrive has still to write; dropped from the build.',
  body: { type: String },
  run() {
    return [];
  },
};

const plugin = { name: 'Scrive blocks', directives: [r3f, video, pending] };

export default plugin;
