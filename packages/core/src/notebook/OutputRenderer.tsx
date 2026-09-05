import type { CSSProperties, ReactElement } from 'react';

import { renderLatexOutput } from './math';
import type { NbOutput } from './types';
import type { WidgetManager } from './widgets/WidgetManager';
import { WidgetView } from './widgets/WidgetView';

const WIDGET_MIME = 'application/vnd.jupyter.widget-view+json';

const mono: CSSProperties = {
  fontFamily: 'var(--font-mono, monospace)',
  // The `telemetry` step: mono, on the ramp in themes.css. Stream text and a
  // traceback are program output, which is what that step is for.
  fontSize: 'var(--fs-body)',
  whiteSpace: 'pre-wrap',
  wordBreak: 'break-word',
  margin: 0,
};

/**
 * The SGR palette (normal + bright), for traceback and stream colors.
 *
 * Literal hexes on purpose, and the one place in this module where that is right:
 * these are the **ANSI/xterm** colors, defined by the terminal protocol rather than
 * by our design system. A program that prints SGR 31 said "ANSI red"; remapping that
 * onto `--danger` would change what the program reported, and would tint a whole
 * traceback with the theme's failure color whichever code it actually emitted.
 */
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

/**
 * The xterm 256-colour cube and greyscale ramp, as CSS.
 *
 * 0–15 map onto the palette above; 16–231 are a 6×6×6 cube; 232–255 are 24 greys.
 * Rich, pytest and IPython's own traceback all emit these, and swallowing the codes
 * (the previous behaviour) produced tracebacks that were colourful in the terminal
 * and half plain here, which reads as a rendering bug rather than a missing feature.
 */
function xterm256(n: number): string | undefined {
  if (n < 8) return ANSI_COLORS[30 + n];
  if (n < 16) return ANSI_COLORS[90 + (n - 8)];
  if (n < 232) {
    const i = n - 16;
    const step = (v: number) => (v === 0 ? 0 : 55 + v * 40);
    return `rgb(${step(Math.floor(i / 36))}, ${step(Math.floor(i / 6) % 6)}, ${step(i % 6)})`;
  }
  if (n < 256) {
    const v = 8 + (n - 232) * 10;
    return `rgb(${v}, ${v}, ${v})`;
  }
  return undefined;
}

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
      const codes = (m[1] || '0').split(';').map(Number);
      for (let i = 0; i < codes.length; i++) {
        const code = codes[i];
        // Extended colour: `38;5;n` (256) and `38;2;r;g;b` (truecolor) consume the
        // arguments that follow. Read as plain SGR codes those arguments would be
        // interpreted as colours in their own right, so the loop has to advance.
        if (code === 38 || code === 48) {
          const kind = codes[i + 1];
          if (kind === 5) {
            const c = xterm256(codes[i + 2]);
            if (code === 38 && c) color = c;
            i += 2;
          } else if (kind === 2) {
            const [r, g, b] = [codes[i + 2], codes[i + 3], codes[i + 4]];
            if (code === 38) color = `rgb(${r || 0}, ${g || 0}, ${b || 0})`;
            i += 4;
          }
          continue;
        }
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

/**
 * Richest representation first.
 *
 * `text/latex` outranks the images: SymPy and `IPython.display.Math` emit TeX plus a
 * `text/plain` repr, and taking the repr shows `<IPython.core.display.Latex object>`
 * where the user asked for an equation.
 */
const MIME_ORDER = [
  'text/latex',
  'image/svg+xml',
  'image/png',
  'image/jpeg',
  'image/gif',
  'image/webp',
  'text/html',
  'text/markdown',
  'text/plain',
] as const;

function pickMime(data: Record<string, unknown>): [string, unknown] | null {
  for (const mime of MIME_ORDER) {
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
        className="nb-output-text"
        style={{ ...mono, color: output.name === 'stderr' ? 'var(--danger)' : undefined }}
      >
        <Ansi text={joined(output.text)} />
      </pre>
    );
  }
  if (type === 'error') {
    const tb = Array.isArray(output.traceback) ? output.traceback.join('\n') : '';
    return (
      <pre className="nb-output-text" style={{ ...mono, color: 'var(--danger)' }}>
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
    if (!picked) {
      // Something was displayed and nothing here can draw it. Say so: rendering
      // nothing looks identical to a cell that produced no output, and the user is
      // left debugging their code instead of reading "install the thing".
      const mimes = Object.keys(data).filter((m) => m !== WIDGET_MIME);
      if (mimes.length === 0) return null;
      return (
        <div className="nb-output-unsupported">
          unsupported output <code>{mimes.join(', ')}</code>
        </div>
      );
    }
    const [mime, value] = picked;
    if (mime === 'text/latex') {
      return (
        <div
          className="nb-output-latex"
          dangerouslySetInnerHTML={{ __html: renderLatexOutput(joined(value)) }}
        />
      );
    }
    if (mime === 'image/svg+xml') {
      // Inlined rather than data-URI'd: matplotlib's svg backend and graphviz both
      // emit text, and wrapping it in an image element would lose the selectable
      // labels that are half the reason to choose a vector backend.
      return (
        <div className="nb-output-media" dangerouslySetInnerHTML={{ __html: joined(value) }} />
      );
    }
    if (mime.startsWith('image/')) {
      return (
        <div className="nb-output-media">
          <img src={`data:${mime};base64,${joined(value).replace(/\n/g, '')}`} alt="cell output" />
        </div>
      );
    }
    if (mime === 'text/html') {
      // Trusted-local v1 posture (same as plugins): kernel output is the user's
      // own code running on their own machine.
      return <div className="nb-output-html" dangerouslySetInnerHTML={{ __html: joined(value) }} />;
    }
    return (
      <pre className="nb-output-text" style={mono}>
        <Ansi text={joined(value)} />
      </pre>
    );
  }
  return null;
}
