/**
 * An `.ipynb` page as one MyST tree, so the Preview (and, in Phase 5, the static
 * site) renders a notebook with the same renderer as a page: markdown cells are
 * parsed as MyST, and code cells become `scriveNotebookCell` nodes carrying their
 * source and the outputs stored in the file.
 *
 * Read-only. A notebook is edited in the Notebook editor; Scrive shows and publishes
 * it. A file that is not a notebook reads as a single error node — never a throw.
 */
import { parseMyst, type MystNode, type MystRoot } from './parse';

interface RawCell {
  cell_type?: string;
  source?: string | string[];
  outputs?: Record<string, unknown>[];
  execution_count?: number | null;
}

const text = (source: string | string[] | undefined) =>
  Array.isArray(source) ? source.join('') : (source ?? '');

export interface NotebookPage {
  tree: MystRoot;
  /** The notebook's language, from its kernelspec or language_info. */
  language: string;
  /** Title from `metadata.title` or MyST frontmatter in metadata, if any. */
  title: string;
}

export function notebookTree(raw: string): NotebookPage {
  let nb: { cells?: RawCell[]; metadata?: Record<string, unknown> };
  try {
    nb = JSON.parse(raw) as typeof nb;
  } catch (err) {
    return {
      tree: {
        type: 'root',
        children: [{ type: 'scriveParseError', value: raw, message: String(err) }],
      },
      language: '',
      title: '',
    };
  }
  const meta = nb.metadata ?? {};
  const spec = (meta.kernelspec ?? {}) as Record<string, unknown>;
  const info = (meta.language_info ?? {}) as Record<string, unknown>;
  const language = String(spec.language ?? info.name ?? 'python');
  const title = typeof meta.title === 'string' ? meta.title : '';

  const children: MystNode[] = [];
  for (const cell of nb.cells ?? []) {
    const source = text(cell.source);
    if (cell.cell_type === 'markdown') {
      children.push(...parseMyst(source).children);
    } else if (cell.cell_type === 'code') {
      children.push({
        type: 'scriveNotebookCell',
        value: source,
        lang: language,
        outputs: cell.outputs ?? [],
        executionCount: cell.execution_count ?? null,
      });
    } else if (source) {
      children.push({ type: 'code', value: source });
    }
  }
  return { tree: { type: 'root', children }, language, title };
}
