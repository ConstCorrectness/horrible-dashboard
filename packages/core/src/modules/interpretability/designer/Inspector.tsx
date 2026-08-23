/**
 * Blender's N-panel: the selected node's properties, or the model's own when
 * nothing is selected.
 *
 * Fields are rendered from the served `ParamSpec`, which means the help text next to
 * a knob is the same string the generator's author wrote next to the code that emits
 * it. A form hand-maintained beside a schema is a form that eventually describes a
 * parameter the backend no longer has.
 *
 * The one non-obvious control is the **`$` reference**: a text param whose value
 * starts with `$` reads the model's config rather than holding a number, which is
 * what keeps the generated class parametric. It is offered as a dropdown of the
 * config keys, because typing `$d_model` correctly is not a skill worth requiring.
 */
import type { DesignGraph, GraphNode, NodeSpec, ParamSpec } from './graph';
import { formatCount } from './graph';

function isReference(value: unknown): value is string {
  return typeof value === 'string' && value.startsWith('$');
}

function Field({
  spec,
  value,
  configKeys,
  onChange,
}: {
  spec: ParamSpec;
  value: unknown;
  configKeys: string[];
  onChange: (next: unknown) => void;
}) {
  const referenced = isReference(value);

  const control = () => {
    if (referenced) {
      return (
        <select
          className="mg-input"
          value={String(value).slice(1)}
          onChange={(e) => onChange(`$${e.target.value}`)}
        >
          {configKeys.map((key) => (
            <option key={key} value={key}>
              {key}
            </option>
          ))}
        </select>
      );
    }
    if (spec.type === 'bool') {
      return (
        <input
          type="checkbox"
          checked={Boolean(value)}
          onChange={(e) => onChange(e.target.checked)}
        />
      );
    }
    if (spec.type === 'select') {
      return (
        <select
          className="mg-input"
          value={String(value ?? '')}
          onChange={(e) => onChange(e.target.value)}
        >
          {spec.options.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      );
    }
    if (spec.type === 'int' || spec.type === 'float') {
      return (
        <input
          className="mg-input"
          type="number"
          step={spec.type === 'float' ? 'any' : 1}
          value={value === '' || value === null || value === undefined ? '' : Number(value)}
          onChange={(e) => {
            const raw = e.target.value;
            if (raw === '') {
              onChange('');
              return;
            }
            onChange(spec.type === 'int' ? Math.trunc(Number(raw)) : Number(raw));
          }}
        />
      );
    }
    return (
      <input
        className="mg-input"
        value={String(value ?? '')}
        onChange={(e) => onChange(e.target.value)}
      />
    );
  };

  // Only text params can carry a reference: a checkbox pointing at a config key
  // would be a control whose displayed state is a lie.
  const referenceable = spec.type === 'text' && configKeys.length > 0;

  return (
    <label className="mg-field" title={spec.help}>
      <span className="mg-field-label">
        {spec.label}
        {referenceable && (
          <button
            type="button"
            className={`mg-ref-toggle${referenced ? ' mg-ref-on' : ''}`}
            title={
              referenced
                ? 'Reading the model config. Click to enter a fixed value instead.'
                : 'Fixed value. Click to read it from the model config, keeping the generated class parametric.'
            }
            onClick={() => onChange(referenced ? '' : `$${configKeys[0]}`)}
          >
            $
          </button>
        )}
      </span>
      {control()}
      <span className="mg-field-help">{spec.help}</span>
    </label>
  );
}

export function Inspector({
  graph,
  node,
  spec,
  params,
  onNodeChange,
  onGraphChange,
  onDelete,
}: {
  graph: DesignGraph;
  node: GraphNode | null;
  spec: NodeSpec | null;
  params: number | undefined;
  onNodeChange: (next: GraphNode) => void;
  onGraphChange: (next: DesignGraph) => void;
  onDelete: (id: string) => void;
}) {
  const configKeys = Object.keys(graph.config);

  if (!node || !spec) {
    return (
      <div className="mg-inspector">
        <h3 className="mg-inspector-title">Model</h3>
        <label className="mg-field">
          <span className="mg-field-label">Class name</span>
          <input
            className="mg-input"
            value={graph.name}
            onChange={(e) => onGraphChange({ ...graph, name: e.target.value })}
          />
          <span className="mg-field-help">The generated root class, and the file it lands in.</span>
        </label>

        <h3 className="mg-inspector-title">Configuration</h3>
        <p className="mg-inspector-note">
          These become the generated class&apos;s keyword arguments. Nodes read them with{' '}
          <code>$name</code>, which is what makes one graph describe a family of models rather than
          a single frozen one.
        </p>
        {configKeys.map((key) => (
          <label key={key} className="mg-field">
            <span className="mg-field-label">{key}</span>
            <input
              className="mg-input"
              type={typeof graph.config[key] === 'number' ? 'number' : 'text'}
              value={String(graph.config[key])}
              onChange={(e) => {
                const raw = e.target.value;
                const next = typeof graph.config[key] === 'number' ? Number(raw) : raw;
                onGraphChange({ ...graph, config: { ...graph.config, [key]: next } });
              }}
            />
          </label>
        ))}
        {configKeys.length === 0 && (
          <p className="mg-inspector-note">No configuration values yet.</p>
        )}
      </div>
    );
  }

  const update = (key: string, value: unknown) =>
    onNodeChange({ ...node, params: { ...node.params, [key]: value } });

  return (
    <div className="mg-inspector">
      <h3 className="mg-inspector-title">{spec.label}</h3>
      <p className="mg-inspector-note">{spec.doc}</p>

      <label className="mg-field">
        <span className="mg-field-label">Name</span>
        <input
          className="mg-input"
          placeholder="auto"
          value={node.name ?? ''}
          onChange={(e) => onNodeChange({ ...node, name: e.target.value })}
        />
        <span className="mg-field-help">
          The attribute this becomes in the generated class. Leave blank to have one derived.
        </span>
      </label>

      {spec.params.map((param) => (
        <Field
          key={param.name}
          spec={param}
          value={node.params[param.name] ?? param.default}
          configKeys={configKeys}
          onChange={(next) => update(param.name, next)}
        />
      ))}

      <div className="mg-inspector-actions">
        <button
          type="button"
          className={`mg-button${node.muted ? ' mg-button-on' : ''}`}
          onClick={() => onNodeChange({ ...node, muted: !node.muted })}
          title="Pass the input straight through, emitting nothing — an ablation you can regenerate and compare."
        >
          {node.muted ? 'Unmute' : 'Mute'}
        </button>
        <button
          type="button"
          className="mg-button mg-button-danger"
          onClick={() => onDelete(node.id)}
        >
          Delete
        </button>
      </div>

      {params !== undefined && (
        <p className="mg-inspector-cost">
          <span className="mg-mono">{formatCount(params)}</span> parameters
        </p>
      )}
    </div>
  );
}
