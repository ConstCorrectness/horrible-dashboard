import type { CSSProperties, ReactElement } from 'react';

import type { NbOutput } from './types';
import type { WidgetManager } from './widgets/WidgetManager';
import { WidgetView } from './widgets/WidgetView';

const WIDGET_MIME = 'application/vnd.jupyter.widget-view+json';

const mono: CSSProperties = {
  fontFamily: 'var(--font-mono, monospace)',
  fontSize: '0.75rem',
  whiteSpace: 'pre-wrap',
  wordBreak: 'break-word',
  margin: 0,
};

// Minimal SGR palette (normal + bright) for traceback/stream ANSI colors.
const ANSI_COLORS: Record<number, string> = {
  30: '#3b4252',
  31: '#e5534b',
  32: '#57ab5a',
  33: '#c69026',
  34: '#539bf5',
  35: '#b083f0',
  36: '#39c5cf',
  37: '#adbac7',
  90: '#636e7b',
  91: '#ff938a',
  92: '#6bc46d',
  93: '#daaa3f',
  94: '#6cb6ff',
  95: '#dcbdfb',
  96: '#56d4dd',
  97: '#cdd9e5',
};

/** Render text with SGR color/bold codes as spans (hand-rolled: no dep). */
export function Ansi({ text }: { text: string }): ReactElement {
  const parts: ReactElement[] = [];
  let color: string | undefined;
  let bold = false;
  let key = 0;
  // eslint-disable-next-line no-control-regex
  const tokens = text.split(/(\x1b\[[0-9;]*m)/);
  for (const token of tokens) {
    // eslint-disable-next-line no-control-regex
    const m = /^\x1b\[([0-9;]*)m$/.exec(token);
    if (m) {
      for (const code of (m[1] || '0').split(';').map(Number)) {
        if (code === 0) {
          color = undefined;
          bold = false;
        } else if (code === 1) bold = true;
        else if (code === 22) bold = false;
        else if (code === 39) color = undefined;
        else if (ANSI_COLORS[code]) color = ANSI_COLORS[code];
      }
      continue;
    }
    if (!token) continue;
    parts.push(
      <span key={key++} style={{ color, fontWeight: bold ? 600 : undefined }}>
        {token}
      </span>,
    );
  }
  return <>{parts}</>;
}

function pickMime(data: Record<string, unknown>): [string, unknown] | null {
  for (const mime of ['image/png', 'image/jpeg', 'text/html', 'text/plain']) {
    if (data[mime] != null) return [mime, data[mime]];
  }
  return null;
}

const joined = (v: unknown): string => (Array.isArray(v) ? v.join('') : String(v ?? ''));

/** One nbformat output → React. Raw dicts in, so disk and wire agree. */
export function OutputRenderer({
  output,
  widgetManager,
}: {
  output: NbOutput;
  widgetManager?: WidgetManager;
}): ReactElement | null {
  const type = output.output_type;
  if (type === 'stream') {
    return (
      <pre
        style={{ ...mono, color: output.name === 'stderr' ? 'var(--danger, #e5534b)' : undefined }}
      >
        <Ansi text={joined(output.text)} />
      </pre>
    );
  }
  if (type === 'error') {
    const tb = Array.isArray(output.traceback) ? output.traceback.join('\n') : '';
    return (
      <pre style={{ ...mono, color: 'var(--danger, #e5534b)' }}>
        <Ansi text={tb || `${String(output.ename)}: ${String(output.evalue)}`} />
      </pre>
    );
  }
  if (type === 'execute_result' || type === 'display_data') {
    const data = (output.data ?? {}) as Record<string, unknown>;
    // Interactive ipywidgets: resolve the model_id against the session's manager.
    const widget = data[WIDGET_MIME] as { model_id?: string } | undefined;
    if (widget?.model_id && widgetManager) {
      return <WidgetView manager={widgetManager} modelId={widget.model_id} />;
    }
    const picked = pickMime(data);
    if (!picked) return null;
    const [mime, value] = picked;
    if (mime.startsWith('image/')) {
      return (
        <img
          src={`data:${mime};base64,${joined(value).replace(/\n/g, '')}`}
          style={{ maxWidth: '100%' }}
          alt="cell output"
        />
      );
    }
    if (mime === 'text/html') {
      // Trusted-local v1 posture (same as plugins): kernel output is the user's
      // own code running on their own machine.
      return (
        <div style={{ fontSize: '0.8rem' }} dangerouslySetInnerHTML={{ __html: joined(value) }} />
      );
    }
    return (
      <pre style={mono}>
        <Ansi text={joined(value)} />
      </pre>
    );
  }
  return null;
}
