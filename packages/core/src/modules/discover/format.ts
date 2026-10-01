/**
 * Pure pieces of the Discover pane: which source each section browses, and how a
 * metric is written down. Kept free of React and the store so they test in node.
 */
import type { Metric } from './api';

export interface SectionConfig {
  id: string;
  label: string;
  /** Backend source id. */
  source: string;
  /** The kinds this section offers, in tab order; the first is the default. */
  kinds: string[];
}

/** One section per catalog. The order is the pane's tab order. */
export const SECTIONS: SectionConfig[] = [
  { id: 'models', label: 'Models', source: 'hf', kinds: ['model'] },
  { id: 'datasets', label: 'Datasets', source: 'hf', kinds: ['dataset'] },
  { id: 'spaces', label: 'Spaces', source: 'hf', kinds: ['space'] },
  { id: 'papers', label: 'Papers', source: 'papers', kinds: ['paper'] },
  { id: 'arxiv', label: 'arXiv', source: 'arxiv', kinds: ['paper'] },
  { id: 'repos', label: 'Repos', source: 'github', kinds: ['repo'] },
  {
    id: 'kaggle',
    label: 'Kaggle',
    source: 'kaggle',
    kinds: ['competition', 'dataset', 'model', 'benchmark'],
  },
  { id: 'mcp', label: 'MCP', source: 'mcp', kinds: ['server'] },
  { id: 'plugins', label: 'Plugins', source: 'plugins', kinds: ['plugin'] },
  { id: 'skills', label: 'Skills', source: 'skills', kinds: ['skill'] },
  { id: 'docs', label: 'Docs', source: 'docs', kinds: ['entry', 'set'] },
];

export function sectionConfig(id: string | undefined): SectionConfig {
  return SECTIONS.find((s) => s.id === id) ?? SECTIONS[0];
}

/** `1234` → `1.2k`, `5_400_000` → `5.4M`. Exact below a thousand. */
export function compact(n: number): string {
  const abs = Math.abs(n);
  if (abs >= 1e9) return `${(n / 1e9).toFixed(1)}B`;
  if (abs >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (abs >= 1e3) return `${(n / 1e3).toFixed(1)}k`;
  return Number.isInteger(n) ? String(n) : n.toFixed(1);
}

export function bytes(n: number): string {
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let value = n;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i++;
  }
  return `${i === 0 ? value : value.toFixed(1)} ${units[i]}`;
}

/** A metric's value as text. `null` is "—": unknown is not zero. */
export function formatMetric(metric: Pick<Metric, 'value' | 'unit'>, value = metric.value): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  switch (metric.unit) {
    case 'bytes':
      return bytes(value);
    case 'percent':
      return `${Math.round(value * 100)}%`;
    default:
      return compact(value);
  }
}

/** "3m ago" from a unix-seconds timestamp. */
export function ago(epochSeconds: number, nowMs = Date.now()): string {
  const s = Math.max(0, Math.floor(nowMs / 1000 - epochSeconds));
  if (s < 45) return 'just now';
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 48) return `${h}h ago`;
  return `${Math.round(h / 24)}d ago`;
}

/** An ISO date as `2026-09-24`, or '' when it isn't one. */
export function day(iso: string | null | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '' : d.toISOString().slice(0, 10);
}

/** The metrics worth a cell in a list row: the first `n` that have a value. A row
 *  is a scan line; the detail view shows every metric, nulls included. */
export function rowMetrics(metrics: Metric[], n = 3): Metric[] {
  return metrics.filter((m) => m.value !== null).slice(0, n);
}

/**
 * A third-party README made fit for the app's small markdown renderer.
 *
 * Model cards and repo READMEs lean on raw HTML (`<div align="center">`, `<img>`) and
 * badge images (`[![License](shield.svg)](url)`). The renderer escapes HTML and has no
 * images, so both would show as literal markup. Outside fenced code blocks this:
 * turns a linked image into a plain link labelled with its alt text, drops bare
 * images, and strips HTML tags while keeping their text. Code blocks are untouched —
 * an HTML snippet in ```html is content, not markup.
 */
export function cleanReadme(markdown: string): string {
  let fenced = false;
  const out: string[] = [];
  for (const line of markdown.split('\n')) {
    if (/^\s*(```|~~~)/.test(line)) {
      fenced = !fenced;
      out.push(line);
      continue;
    }
    if (fenced) {
      out.push(line);
      continue;
    }
    const cleaned = line
      // [![alt](img)](href) → [alt](href)
      .replace(/\[!\[([^\]]*)\]\([^)]*\)\]\(([^)\s]+)[^)]*\)/g, (_, alt: string, href: string) =>
        alt.trim() ? `[${alt.trim()}](${href})` : `[link](${href})`,
      )
      // ![alt](img) → nothing (an image the renderer can't draw says nothing)
      .replace(/!\[[^\]]*\]\([^)]*\)/g, '')
      // <tag ...> / </tag> → removed, text kept
      .replace(/<\/?[a-zA-Z][^>]*>/g, '')
      .replace(/<!--.*?-->/g, '');
    out.push(cleaned);
  }
  // Collapse the blank runs that stripped HTML blocks leave behind.
  return out.join('\n').replace(/\n{3,}/g, '\n\n').trim();
}
