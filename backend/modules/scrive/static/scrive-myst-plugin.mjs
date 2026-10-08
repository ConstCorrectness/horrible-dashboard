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
 * - `{space} owner/name` → an iframe of the Hugging Face Space at its `:host:` (which
 *   the Scrive editor looked up from the Hub), or a link to the Space without one.
 * - `{app} name` → an iframe of the site's web app, published at `_scrive/apps/<name>/`.
 * - `{webllm} owner/model` → an iframe of `_scrive/webml/embed.html`, the in-browser
 *   model page, with its settings in the fragment. A `gguf:owner/repo/file.gguf`
 *   model runs in our own engine (`_scrive/webml/gguf.worker.js`, beside the page).
 * - `{tokenviz} data/run.json` → the recorded reply as text (the hover strip is the
 *   static build's), read from the file at build time.
 * - `{pending}` → nothing: a section still to write never reaches readers.
 *
 * `BASE_URL` is the path the site is served under (`/repo` on GitHub Pages), set by
 * the workflow; Jupyter Book reads the same variable.
 */
import fs from 'node:fs';
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

/** `:host:` if it is an hf.space host — the same allowlist as Scrive's renderer. */
function spaceHost(raw) {
  const host = String(raw ?? '')
    .trim()
    .replace(/^https:\/\//i, '')
    .replace(/\/+$/, '');
  return /^[a-z0-9-]+(?:\.static)?\.hf\.space$/i.test(host) ? `https://${host.toLowerCase()}` : null;
}

const space = {
  name: 'space',
  doc: 'A Hugging Face Space, embedded.',
  arg: { type: String, doc: 'The Space id (owner/name) or its huggingface.co URL.', required: true },
  options: {
    height: { type: Number, doc: 'Height in pixels.' },
    host: { type: String, doc: "The Space's embed host (*.hf.space)." },
  },
  run(data) {
    const arg = String(data.arg ?? '').trim();
    const m =
      /^([A-Za-z0-9][\w.-]*)\/([\w.-]+)$/.exec(arg) ||
      /^https:\/\/(?:www\.)?huggingface\.co\/spaces\/([^/?#]+)\/([^/?#]+)/.exec(arg);
    if (!m) return [];
    const id = `${m[1]}/${m[2]}`;
    const host = spaceHost(data.options?.host);
    const link = {
      type: 'paragraph',
      children: [
        {
          type: 'link',
          url: `https://huggingface.co/spaces/${id}`,
          children: [{ type: 'text', value: `Hugging Face Space: ${id}` }],
        },
      ],
    };
    return host ? [{ type: 'iframe', src: host, width: '100%', title: `Space ${id}` }, link] : [link];
  },
};

const app = {
  name: 'app',
  doc: "A web app from the site's apps/ folder, published under _scrive/apps/.",
  arg: { type: String, doc: 'The app folder name (or apps/<name>).', required: true },
  options: { height: { type: Number, doc: 'Height in pixels.' } },
  run(data) {
    const name = String(data.arg ?? '')
      .trim()
      .replace(/^\/?apps\//, '')
      .replace(/\/+$/, '');
    if (!/^[A-Za-z0-9][\w.-]{0,63}$/.test(name)) return [];
    const base = String(process.env.BASE_URL ?? '').replace(/\/+$/, '');
    return [{ type: 'iframe', src: `${base}/_scrive/apps/${name}/`, width: '100%', title: `App ${name}` }];
  },
};

const webllm = {
  name: 'webllm',
  doc: "A language model that runs in the reader's browser (WebGPU).",
  arg: {
    type: String,
    doc: 'Hugging Face model id (ONNX), or gguf:owner/repo/file.gguf.',
    required: true,
  },
  options: {
    dtype: { type: String },
    system: { type: String },
    show: { type: String },
    max: { type: Number },
    height: { type: Number },
  },
  run(data) {
    const model = String(data.arg ?? '').trim();
    // An ONNX repo, a Hub GGUF, or a node GGUF (whose page says readers cannot reach it).
    const ok =
      /^[A-Za-z0-9][\w.-]*\/[\w.-]+$/.test(model) ||
      /^gguf:[A-Za-z0-9][\w.-]*\/[\w.-]+\/[\w./-]+$/.test(model) ||
      /^gguf-node:\S+$/.test(model);
    if (!ok) return [];
    const o = data.options ?? {};
    const show = String(o.show ?? 'chat');
    const params = {
      model,
      dtype: String(o.dtype ?? ''),
      system: String(o.system ?? ''),
      tokens: /tokens|lens/.test(show),
      lens: /lens/.test(show),
      max: Number(o.max) || 256,
      sizes: {},
    };
    const base = String(process.env.BASE_URL ?? '').replace(/\/+$/, '');
    return [
      {
        type: 'iframe',
        src: `${base}/_scrive/webml/embed.html#${encodeURIComponent(JSON.stringify(params))}`,
        width: '100%',
        title: `In-browser model ${model}`,
      },
    ];
  },
};

const tokenviz = {
  name: 'tokenviz',
  doc: 'A recorded generation (token probabilities).',
  arg: { type: String, doc: 'The run file (JSON), relative to the page.', required: true },
  run(data, vfile) {
    try {
      const run = JSON.parse(fs.readFileSync(sitePath(data.arg, vfile), 'utf8'));
      const text = (run.steps ?? []).map((s) => String(s.token ?? '')).join('');
      return [
        {
          type: 'blockquote',
          children: [{ type: 'paragraph', children: [{ type: 'text', value: text }] }],
        },
      ];
    } catch {
      return [];
    }
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

const plugin = { name: 'Scrive blocks', directives: [r3f, video, space, app, webllm, tokenviz, pending] };

export default plugin;
