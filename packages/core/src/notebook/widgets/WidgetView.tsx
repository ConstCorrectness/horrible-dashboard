import { useSyncExternalStore, type ReactElement } from 'react';

import { AnyWidgetView } from './AnyWidgetView';
import type { WidgetManager, WidgetState } from './WidgetManager';

/** Subscribe to one widget model's live state. */
function useWidgetState(manager: WidgetManager, modelId: string): WidgetState | undefined {
  return useSyncExternalStore(
    (cb) => manager.subscribe(modelId, cb),
    () => manager.getState(modelId),
  );
}

const num = (v: unknown, d = 0): number => (typeof v === 'number' ? v : d);
const str = (v: unknown, d = ''): string => (v == null ? d : String(v));
const label = { fontSize: '0.72rem', color: 'var(--text-dim)', minWidth: '5rem' } as const;
const row = {
  display: 'flex',
  alignItems: 'center',
  gap: '0.5rem',
  margin: '0.2rem 0',
  fontSize: '0.8rem',
} as const;

/**
 * Render one ipywidgets model with a native control. Covers the common core
 * widgets; unknown models show a compact fallback with their state.
 */
export function WidgetView({
  manager,
  modelId,
}: {
  manager: WidgetManager;
  modelId: string;
}): ReactElement {
  const state = useWidgetState(manager, modelId);
  if (!state) return <span style={{ ...label }}>· widget ·</span>;

  // anywidget: the model ships its own front-end (_esm). Render it directly.
  if (typeof state._esm === 'string') {
    return <AnyWidgetView manager={manager} modelId={modelId} />;
  }

  const name = str(state._model_name);
  const desc = str(state.description);
  const disabled = state.disabled === true;

  const setValue = (value: unknown) => manager.setState(modelId, { value });

  if (name.includes('Slider')) {
    const isFloat = name.startsWith('Float');
    return (
      <div style={row}>
        {desc && <span style={label}>{desc}</span>}
        <input
          type="range"
          disabled={disabled}
          min={num(state.min, 0)}
          max={num(state.max, 100)}
          step={num(state.step, isFloat ? 0.1 : 1)}
          value={num(state.value)}
          onChange={(e) =>
            setValue(isFloat ? parseFloat(e.target.value) : parseInt(e.target.value, 10))
          }
        />
        <span style={{ fontFamily: 'var(--font-mono, monospace)', minWidth: '3rem' }}>
          {num(state.value)}
        </span>
      </div>
    );
  }

  if (name.includes('Progress')) {
    return (
      <div style={row}>
        {desc && <span style={label}>{desc}</span>}
        <progress max={num(state.max, 100)} value={num(state.value)} />
      </div>
    );
  }

  if (name.includes('Text') && !name.includes('Textarea')) {
    const isNumber =
      name.startsWith('Int') || name.startsWith('Float') || name.startsWith('Bounded');
    return (
      <div style={row}>
        {desc && <span style={label}>{desc}</span>}
        <input
          type={isNumber ? 'number' : 'text'}
          disabled={disabled}
          value={isNumber ? num(state.value) : str(state.value)}
          onChange={(e) => setValue(isNumber ? Number(e.target.value) : e.target.value)}
        />
      </div>
    );
  }

  if (name === 'TextareaModel') {
    return (
      <div style={row}>
        {desc && <span style={label}>{desc}</span>}
        <textarea
          disabled={disabled}
          value={str(state.value)}
          onChange={(e) => setValue(e.target.value)}
          style={{ flex: 1, minHeight: '3rem' }}
        />
      </div>
    );
  }

  if (name === 'ButtonModel') {
    return (
      <button disabled={disabled} onClick={() => manager.sendCustom(modelId, { event: 'click' })}>
        {str(state.description, 'Button')}
      </button>
    );
  }

  if (name === 'CheckboxModel' || name === 'ValidModel') {
    return (
      <label style={row}>
        <input
          type="checkbox"
          disabled={disabled}
          checked={state.value === true}
          onChange={(e) => setValue(e.target.checked)}
        />
        {desc && <span>{desc}</span>}
      </label>
    );
  }

  if (name === 'ToggleButtonModel') {
    const on = state.value === true;
    return (
      <button
        disabled={disabled}
        onClick={() => setValue(!on)}
        style={{ background: on ? 'var(--accent, #539bf5)' : undefined }}
      >
        {str(state.description, 'Toggle')}
      </button>
    );
  }

  if (name === 'DropdownModel' || name === 'SelectModel' || name === 'ToggleButtonsModel') {
    const options = Array.isArray(state._options_labels) ? (state._options_labels as string[]) : [];
    return (
      <div style={row}>
        {desc && <span style={label}>{desc}</span>}
        <select
          disabled={disabled}
          value={num(state.index)}
          onChange={(e) => manager.setState(modelId, { index: parseInt(e.target.value, 10) })}
        >
          {options.map((opt, i) => (
            <option key={i} value={i}>
              {opt}
            </option>
          ))}
        </select>
      </div>
    );
  }

  if (name === 'LabelModel') {
    return <span style={{ fontSize: '0.8rem' }}>{str(state.value)}</span>;
  }
  if (name === 'HTMLModel' || name === 'HTMLMathModel') {
    // Trusted-local posture (same as kernel HTML output).
    return <div dangerouslySetInnerHTML={{ __html: str(state.value) }} />;
  }

  return (
    <span style={{ ...label, fontStyle: 'italic' }}>
      · {name || 'widget'} (view not supported yet) ·
    </span>
  );
}
