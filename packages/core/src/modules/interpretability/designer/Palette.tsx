/**
 * The add menu, as a strip rather than a popup.
 *
 * Blender's `Shift-A` opens a categorised menu; the categories here come from the
 * served catalog rather than a hand-kept list, so a node type added on the backend
 * appears without a frontend change. Clicking adds at the canvas centre — dragging
 * is the nicer gesture and comes with the drop-onto-a-link insert, but a palette you
 * can only use by dragging is a palette that does not work on a trackpad.
 */
import { useMemo, useState, type RefObject } from 'react';

import type { NodeSpec, TemplateSpec } from './graph';

const ORDER = ['io', 'embedding', 'norm', 'attention', 'ffn', 'activation', 'op', 'structure'];

const TITLES: Record<string, string> = {
  io: 'Input / output',
  embedding: 'Embedding',
  norm: 'Normalisation',
  attention: 'Attention',
  ffn: 'Feed-forward',
  activation: 'Activation',
  op: 'Operators',
  structure: 'Structure',
};

export function Palette({
  specs,
  templates,
  inspectedModel,
  importing,
  onAdd,
  onTemplate,
  onImport,
  searchRef,
}: {
  specs: NodeSpec[];
  templates: TemplateSpec[];
  /** The model the Inspect tab is showing, when there is one. */
  inspectedModel?: string;
  importing?: boolean;
  onAdd: (spec: NodeSpec) => void;
  onTemplate: (id: string) => void;
  onImport: () => void;
  /** Focused by the add-node binding — Blender's Shift-A, which opens a search
   * rather than making the user hunt the list. */
  searchRef?: RefObject<HTMLInputElement | null>;
}) {
  const [query, setQuery] = useState('');

  const groups = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const matched = needle
      ? specs.filter(
          (s) =>
            s.label.toLowerCase().includes(needle) ||
            s.type.includes(needle) ||
            s.doc.toLowerCase().includes(needle),
        )
      : specs;
    const byCategory = new Map<string, NodeSpec[]>();
    for (const spec of matched) {
      const list = byCategory.get(spec.category) ?? [];
      list.push(spec);
      byCategory.set(spec.category, list);
    }
    const rank = (category: string): number => {
      const index = ORDER.indexOf(category);
      return index < 0 ? ORDER.length : index;
    };
    return [...byCategory.entries()].sort((a, b) => rank(a[0]) - rank(b[0]));
  }, [specs, query]);

  return (
    <div className="mg-palette">
      <input
        ref={searchRef}
        className="mg-search"
        placeholder="Search nodes"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        aria-label="Search nodes"
      />

      {templates.length > 0 && !query && (
        <section className="mg-palette-group">
          <h3 className="mg-palette-title">Start from</h3>
          {/* The bridge to the other tab, and the reason the two share a pane: the
              model you are reading is the obvious thing to fork. It sits with the
              templates because that is what it is — a starting point, not a copy. */}
          {inspectedModel && (
            <button
              type="button"
              className="mg-template mg-template-import"
              onClick={onImport}
              disabled={importing}
              title="Build an editable design from the model the Inspect tab is showing. It will tell you what it had to assume."
            >
              <span className="mg-template-label">
                {importing ? 'Importing…' : 'The inspected model'}
              </span>
              <span className="mg-template-desc mg-mono">{inspectedModel}</span>
            </button>
          )}
          {templates.map((template) => (
            <button
              key={template.id}
              type="button"
              className="mg-template"
              onClick={() => onTemplate(template.id)}
              title={template.description}
            >
              <span className="mg-template-label">{template.label}</span>
              <span className="mg-template-desc">{template.description}</span>
            </button>
          ))}
        </section>
      )}

      {groups.map(([category, entries]) => (
        <section key={category} className="mg-palette-group">
          <h3 className="mg-palette-title">{TITLES[category] ?? category}</h3>
          {entries.map((spec) => (
            <button
              key={spec.type}
              type="button"
              className={`mg-palette-item mg-cat-${spec.category}`}
              onClick={() => onAdd(spec)}
              title={spec.doc}
            >
              {spec.label}
            </button>
          ))}
        </section>
      ))}
    </div>
  );
}
