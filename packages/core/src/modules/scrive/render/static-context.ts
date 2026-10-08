/**
 * The published-site side of `MystView`.
 *
 * The Preview and the static site build render pages with the same components. What
 * differs is everything that reaches outside the page: in the app, an image is
 * fetched from the backend's asset route, a code cell runs on a kernel, a diagram is
 * drawn after mount and a scene posts its source into a frame. A published page has
 * none of that — no backend, no kernel, no effects (`renderToStaticMarkup` runs
 * none). When this context is set, each of those blocks asks it instead, and the
 * site build (`site/build.tsx`) answers with relative URLs, cached outputs and
 * pre-rendered SVG. Interactive chrome — run bars, "write with agent", tweak
 * panels — is left out.
 */
import { createContext } from 'react';

import type { NbOutput } from '../../../notebook/types';

export interface StaticRender {
  /** An embedded site file's URL, relative to the page being written. Records the
   * file so the build copies it. `url` is as written in the page. */
  asset(pagePath: string, url: string): string;
  /** A link's href for the published site: a link to another page (`../x.md`)
   * becomes that page's URL; `undefined` when it points at a page that is not
   * published (the link text stays, without a link). */
  link(pagePath: string, href: string): string | undefined;
  /** A `{code-cell}`'s cached outputs. */
  cellOutputs(pagePath: string, source: string, occurrence: number): NbOutput[];
  /** A `{mermaid}` diagram drawn ahead of time, or `null`. */
  mermaid(code: string): string | null;
  /** The URL of a scene's standalone page, with its tweak values. */
  scene(pagePath: string, src: string, params: Record<string, unknown>): string;
  /** The URL of a web app (`apps/<name>/`, published under `_scrive/apps/`). */
  app(pagePath: string, name: string): string;
  /** The URL of the in-browser model embed (`_scrive/webml/embed.html`) with its settings. */
  webllm(pagePath: string, params: Record<string, unknown>): string;
  /** A site data file's parsed JSON, preloaded by the build (`{tokenviz}`); undefined if absent. */
  data(pagePath: string, src: string): unknown;
}

export const StaticRenderContext = createContext<StaticRender | null>(null);
