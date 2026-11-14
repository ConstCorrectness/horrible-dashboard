/**
 * Neurons: every MLP neuron of the window's GGUF model, layer by layer, as a
 * stack of plates you can orbit; pick a neuron, dose it, and compare the
 * model's greedy reply with and without the recipe. Click a token of either
 * reply to light the stack by what fired there.
 *
 * Inspired by webml-community's Neuron Studio, on our own WebGPU engine: the edit
 * is made on the gated activation right before the down projection, on every
 * token; an additive dose is in units of the layer's median residual norm over the
 * neuron's down-column norm (`recipe.ts`), so one slider means the same push at
 * every depth.
 */
import { Fragment, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';

import { openPane } from '../../../layout/controller';
import { useGraphicsQuality } from '../../../graphics';
import { RollingCounter, entranceProps } from '../../../viz';
import { useEngineState } from '../../webml/engine';
import { showToken } from '../../webml/lens-lab/model';
import { S } from '../styles';
import { bindNeuronActions } from './actions';
import { cellOf } from './grid';
import { influence, MAX_DOSE, MAX_EDITS, type NeuronEdit } from './recipe';
import { DETAIL_ZOOM, NeuronStack, type Hover } from './scene';
import { neuronSound, isMuted, setMuted } from './sound';
import {
  activationOf,
  addSelected,
  beginDose,
  captureAt,
  clearCapture,
  clearError,
  clearRecipe,
  editAt,
  exportJson,
  heatOf,
  importJson,
  modelFile,
  neuronStore,
  removeEdit,
  runComparison,
  select,
  setDose,
  setMaxTokens,
  setMode,
  setPrompt,
  setTopThisLayer,
  stopComparison,
  summary,
  syncModel,
  toggleEnabled,
  undo,
  useNeurons,
  type Branch,
} from './state';
import './neurons.css';

const mono: CSSProperties = { ...S.mono };
const heading: CSSProperties = { ...S.heading };
const panel: CSSProperties = {
  display: 'flex',
  flexDirection: 'column',
  minHeight: 0,
  minWidth: 0,
  overflow: 'auto',
  background: 'var(--bg-primary)',
};

/** Signed dose for display: "+0.80", "−1.20". */
function fmtDose(d: number): string {
  return `${d > 0 ? '+' : d < 0 ? '−' : ''}${Math.abs(d).toFixed(2)}`;
}

function polarityColor(v: number): string {
  return v > 0 ? 'var(--nrn-pos)' : v < 0 ? 'var(--nrn-neg)' : 'var(--text-secondary)';
}

export function NeuronsBody() {
  const engine = useEngineState();
  const st = useNeurons();

  // Follow the window's engine: its model is this section's model.
  useEffect(() => {
    if (engine.kind === 'ready' && engine.engine === 'gguf' && engine.shape) {
      syncModel({
        id: engine.model,
        file: modelFile(engine.model),
        layers: engine.shape.layers,
        ffn: engine.shape.ffn,
        arch: engine.shape.arch,
        thinking: engine.thinking === true,
      });
    } else if (engine.kind !== 'loading') {
      syncModel(null);
    }
  }, [engine]);

  const m = st.model;
  return (
    <div className="nrn" style={S.frame}>
      <div style={S.strip}>
        <span style={S.stripLabel}>Neurons</span>
        {m ? (
          <>
            <span style={S.stripFact} title={m.id}>
              {m.file}
            </span>
            <span style={S.stripFact}>
              {m.arch} · {m.layers} layers × {m.ffn.toLocaleString()} neurons
            </span>
            <span
              style={{
                ...S.stripFact,
                marginLeft: 'auto',
                color: st.busy ? 'var(--accent)' : undefined,
              }}
            >
              {st.busy ?? (st.medians ? 'ready · normalized' : 'ready')}
            </span>
          </>
        ) : (
          <span style={S.stripFact}>
            {engine.kind === 'loading'
              ? `loading ${modelFile(engine.model)}…`
              : 'no GGUF model loaded'}
          </span>
        )}
      </div>
      {m ? <Studio key={`${m.id}:${m.layers}:${m.ffn}`} /> : <Empty engineKind={engine.kind} />}
    </div>
  );
}

function Empty({ engineKind }: { engineKind: string }) {
  return (
    <div style={{ ...S.home, display: 'grid', alignContent: 'start', gap: 12 }}>
      <h2 style={S.homeTitle}>Every neuron, every layer</h2>
      <p style={S.lede}>
        Load a GGUF model in the WebML playground and it appears here as a stack of layers, one
        point per MLP neuron. Pick a neuron, give it a dose, and compare the model’s reply with and
        without it — all on this GPU. Llama, Qwen 2/3, Gemma 3 and SmolLM3 files run.
      </p>
      <div>
        <button type="button" onClick={() => void openPane('webml.playground')}>
          {engineKind === 'loading' ? 'Watch it load' : 'Open the WebML playground'}
        </button>
      </div>
    </div>
  );
}

function Studio() {
  const st = useNeurons();
  const m = st.model!;
  const quality = useGraphicsQuality();
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const labelsRef = useRef<HTMLDivElement>(null);
  const stackRef = useRef<NeuronStack | null>(null);
  const [hover, setHover] = useState<Hover | null>(null);
  const [detail, setDetail] = useState(false);
  const [isolated, setIsolated] = useState(false);
  const [sound, setSound] = useState(!isMuted());
  const fileRef = useRef<HTMLInputElement>(null);

  // The scene: built once per model shape, torn down with the section.
  useEffect(() => {
    const canvas = canvasRef.current;
    const labels = labelsRef.current;
    if (!canvas || !labels) return;
    const dpr = Math.min(devicePixelRatio || 1, quality === 'performance' ? 1 : 2);
    const stack = new NeuronStack(
      canvas,
      labels,
      m.layers,
      m.ffn,
      {
        onSelect: (s) => {
          select(s.layer, s.neuron);
          neuronSound.select(s.layer);
        },
        onLayer: (layer) => {
          neuronSound.layer(layer - neuronStore.get().selection.layer);
          select(layer, neuronStore.get().selection.neuron);
        },
        onHover: (h) => {
          setHover(h);
          if (h) neuronSound.hover(h.layer, h.neuron);
        },
        onDetail: setDetail,
      },
      { pixelRatio: dpr },
    );
    stack.setSelection(neuronStore.get().selection, false);
    stackRef.current = stack;
    return () => {
      stack.destroy();
      stackRef.current = null;
    };
  }, [m.layers, m.ffn, quality]);

  // Selection, marks and heat into the scene.
  useEffect(() => {
    stackRef.current?.setSelection(st.selection);
  }, [st.selection]);
  useEffect(() => {
    stackRef.current?.setMarks(
      st.recipe
        .filter((e) => e.enabled)
        .map((e) => {
          const v = influence(e);
          return {
            layer: e.layer,
            neuron: e.neuron,
            polarity: (v > 0 ? 1 : v < 0 ? -1 : 0) as -1 | 0 | 1,
            amount: Math.min(1, Math.abs(v) / MAX_DOSE),
          };
        }),
    );
  }, [st.recipe]);
  useEffect(() => {
    const stack = stackRef.current;
    if (!stack) return;
    const cap = st.capture;
    if (!cap) stack.clearHeat();
    else for (let l = 0; l < m.layers; l++) stack.setHeat(l, heatOf(cap, l));
  }, [st.capture, m.layers]);
  useEffect(() => {
    stackRef.current?.setIsolated(isolated);
  }, [isolated]);

  // Keybindings reach the scene through the bind handle.
  useEffect(() => {
    const go = (dl: number) => {
      const s = neuronStore.get().selection;
      const layer = Math.max(0, Math.min(m.layers - 1, s.layer + dl));
      if (layer !== s.layer) {
        neuronSound.layer(dl);
        select(layer, s.neuron);
      }
    };
    bindNeuronActions({
      left: () => stackRef.current?.step(-1, 0),
      right: () => stackRef.current?.step(1, 0),
      up: () => stackRef.current?.step(0, -1),
      down: () => stackRef.current?.step(0, 1),
      orbitLeft: () => stackRef.current?.orbit(-0.12, 0),
      orbitRight: () => stackRef.current?.orbit(0.12, 0),
      orbitUp: () => stackRef.current?.orbit(0, -0.1),
      orbitDown: () => stackRef.current?.orbit(0, 0.1),
      previousLayer: () => go(-1),
      nextLayer: () => go(1),
      zoomIn: () => stackRef.current?.zoomBy(1.2),
      zoomOut: () => stackRef.current?.zoomBy(1 / 1.2),
      inspect: () => stackRef.current?.inspect(),
      resetView: () => {
        stackRef.current?.reset();
        setIsolated(false);
      },
      addSelected: () => {
        addSelected();
        neuronSound.click();
      },
      undo,
    });
    return () => bindNeuronActions(null);
  }, [m.layers]);

  const sel = st.selection;
  const selEdit = editAt(sel.layer, sel.neuron);
  const selAct = activationOf(st.capture, sel.layer, sel.neuron);

  const exportRecipe = () => {
    const json = exportJson();
    if (!json) return;
    const url = URL.createObjectURL(new Blob([json], { type: 'application/json' }));
    const a = document.createElement('a');
    a.href = url;
    a.download = `${m.file.replace(/\.gguf$/i, '')}.neurons.json`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  return (
    <>
      <div className="nrn-main">
        <RecipePanel onImport={() => fileRef.current?.click()} onExport={exportRecipe} />
        <div className="nrn-stage">
          <canvas
            ref={canvasRef}
            className="nrn-canvas"
            tabIndex={0}
            aria-label="Neuron layers in 3D. Drag to orbit, Shift-drag to pan, scroll to zoom, click a plate to traverse, double-click to inspect. Arrow keys select adjacent neurons; Alt and arrows orbit; Page Up and Page Down change layer."
          />
          <div ref={labelsRef} className="nrn-labels" />
          <StageOverlay
            detail={detail}
            isolated={isolated}
            sound={sound}
            onIsolate={() => setIsolated((v) => !v)}
            onInspect={() => stackRef.current?.inspect()}
            onReset={() => {
              stackRef.current?.reset();
              setIsolated(false);
            }}
            onZoom={(f) => stackRef.current?.zoomBy(f)}
            onSound={() => {
              setMuted(sound);
              setSound(!sound);
            }}
          />
          {hover && !detail && <Loupe hover={hover} />}
        </div>
        <ComparePanel />
      </div>
      <Dock selEdit={selEdit} selAct={selAct} />
      <input
        ref={fileRef}
        type="file"
        accept="application/json,.json"
        hidden
        onChange={(e) => {
          const f = e.target.files?.[0];
          e.target.value = '';
          if (f) void f.text().then(importJson);
        }}
      />
    </>
  );
}

// ── the stage's furniture ────────────────────────────────────────────────────

function StageOverlay(props: {
  detail: boolean;
  isolated: boolean;
  sound: boolean;
  onIsolate: () => void;
  onInspect: () => void;
  onReset: () => void;
  onZoom: (f: number) => void;
  onSound: () => void;
}) {
  const st = useNeurons();
  const m = st.model!;
  const { layer, neuron } = st.selection;
  const { row, col } = cellOf(neuron, { ffn: m.ffn, cols: Math.ceil(Math.sqrt(m.ffn)), rows: 0 });
  const go = (dl: number) => {
    const l = Math.max(0, Math.min(m.layers - 1, layer + dl));
    if (l !== layer) {
      neuronSound.layer(dl);
      select(l, neuron);
    }
  };
  return (
    <>
      {/* Top-left: the selected coordinate, with its grid cell beneath. */}
      <div style={{ position: 'absolute', left: 14, top: 12, display: 'grid', gap: 2 }}>
        <span
          style={{
            ...heading,
            fontSize: 18,
            fontFamily: 'var(--font-mono)',
            color: 'var(--text-primary)',
            letterSpacing: '0.06em',
          }}
        >
          L{layer}
          <span style={{ color: 'var(--text-secondary)' }}> · </span>N{neuron}
        </span>
        <span style={mono}>
          row {row} · col {col}
          {st.capture && ` · act ${activationOf(st.capture, layer, neuron)?.toFixed(3)}`}
        </span>
      </div>
      {/* Bottom-left: layer navigation. */}
      <div style={{ position: 'absolute', left: 12, bottom: 12, display: 'flex', gap: 4 }}>
        <button
          type="button"
          className="nrn-chip"
          title="Previous layer (Page Up)"
          onClick={() => go(-1)}
          disabled={layer === 0}
        >
          <Chevron up />
        </button>
        <span className="nrn-chip" style={{ ...mono, cursor: 'default', minWidth: 74 }}>
          LAYER {layer}/{m.layers - 1}
        </span>
        <button
          type="button"
          className="nrn-chip"
          title="Next layer (Page Down)"
          onClick={() => go(1)}
          disabled={layer === m.layers - 1}
        >
          <Chevron />
        </button>
        <button
          type="button"
          className="nrn-chip"
          aria-pressed={props.detail}
          title="Inspect individual neurons (I, or double-click)"
          onClick={props.onInspect}
        >
          <Magnify />
        </button>
        <button
          type="button"
          className="nrn-chip"
          aria-pressed={props.isolated}
          title="Isolate this layer"
          onClick={props.onIsolate}
        >
          <Cube />
        </button>
      </div>
      {/* Bottom-right: view controls. */}
      <div style={{ position: 'absolute', right: 12, bottom: 12, display: 'flex', gap: 4 }}>
        <button
          type="button"
          className="nrn-chip"
          title="Zoom out (−)"
          onClick={() => props.onZoom(1 / 1.25)}
        >
          −
        </button>
        <button
          type="button"
          className="nrn-chip"
          title="Zoom in (=)"
          onClick={() => props.onZoom(1.25)}
        >
          +
        </button>
        <button type="button" className="nrn-chip" title="Reset view (R)" onClick={props.onReset}>
          <ResetIcon />
        </button>
        <button
          type="button"
          className="nrn-chip"
          aria-pressed={props.sound}
          title={props.sound ? 'Sound on' : 'Sound off'}
          onClick={props.onSound}
        >
          <SoundIcon on={props.sound} />
        </button>
      </div>
      {!props.detail && (
        <div
          className="nrn-hint"
          style={{
            ...mono,
            position: 'absolute',
            // The free corner: top-left carries the coordinate, the bottom the controls.
            right: 14,
            top: 14,
            maxWidth: 'min(240px, 45%)',
            textAlign: 'right',
            pointerEvents: 'none',
          }}
        >
          Drag to orbit · click a plate to traverse · scroll to zoom · double-click to inspect
        </div>
      )}
      {props.detail && (
        <div style={{ ...mono, position: 'absolute', right: 14, top: 14 }}>
          INSPECT · drag to pan · zoom &gt; {DETAIL_ZOOM}
        </div>
      )}
    </>
  );
}

/** The 7×7 neighbourhood under the pointer, coloured by steering or activation. */
function Loupe({ hover }: { hover: Hover }) {
  const st = useNeurons();
  const m = st.model!;
  const cols = Math.ceil(Math.sqrt(m.ffn));
  const r0 = Math.floor(hover.neuron / cols);
  const c0 = hover.neuron % cols;
  const heat = st.capture ? heatOf(st.capture, hover.layer) : null;
  const cells = [];
  for (let dr = -3; dr <= 3; dr++) {
    for (let dc = -3; dc <= 3; dc++) {
      const r = r0 + dr;
      const c = c0 + dc;
      const idx = r * cols + c;
      const inside = r >= 0 && c >= 0 && c < cols && idx < m.ffn;
      const edit = inside ? editAt(hover.layer, idx) : undefined;
      const v = edit?.enabled ? influence(edit) / MAX_DOSE : heat && inside ? heat[idx] : 0;
      const pct = Math.round(Math.min(1, Math.abs(v)) * 100);
      cells.push(
        <span
          key={`${dr}:${dc}`}
          style={{
            width: 10,
            height: 10,
            borderRadius: '50%',
            justifySelf: 'center',
            alignSelf: 'center',
            visibility: inside ? 'visible' : 'hidden',
            background: `color-mix(in srgb, ${polarityColor(v)} ${Math.max(pct, 0)}%, var(--text-secondary))`,
            opacity: 0.35 + 0.65 * Math.min(1, Math.abs(v)),
            outline: dr === 0 && dc === 0 ? '1px solid var(--text-primary)' : undefined,
            outlineOffset: 2,
          }}
        />,
      );
    }
  }
  const left = Math.max(8, hover.x + 24);
  const top = Math.max(8, hover.y - 170);
  return (
    <div
      style={{
        position: 'absolute',
        left,
        top,
        width: 150,
        padding: 10,
        background: 'color-mix(in srgb, var(--bg-primary) 94%, transparent)',
        borderTop: '2px solid var(--accent)',
        clipPath: 'polygon(0 0, calc(100% - 10px) 0, 100% 10px, 100% 100%, 0 100%)',
        pointerEvents: 'none',
        display: 'grid',
        gap: 8,
      }}
    >
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 18px)', gridAutoRows: 18 }}>
        {cells}
      </div>
      <div style={{ ...mono, textAlign: 'center', color: 'var(--text-primary)' }}>
        L{hover.layer} · N{hover.neuron}
      </div>
    </div>
  );
}

// ── the recipe ───────────────────────────────────────────────────────────────

function RecipePanel({ onImport, onExport }: { onImport: () => void; onExport: () => void }) {
  const st = useNeurons();
  const active = st.recipe.filter((e) => e.enabled && influence(e) !== 0).length;
  const selKey = `${st.selection.layer}:${st.selection.neuron}`;
  return (
    <aside className="nrn-recipe" style={{ ...panel, borderRight: '1px solid var(--border)' }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, padding: '12px 12px 8px' }}>
        <span style={heading}>Steering recipe</span>
        <span style={{ ...mono, marginLeft: 'auto' }}>
          <RollingCounter value={active} /> active · {st.recipe.length}/{MAX_EDITS}
        </span>
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, padding: '0 12px 10px' }}>
        <button
          type="button"
          className="nrn-chip"
          onClick={() => addSelected()}
          disabled={!!editAt(st.selection.layer, st.selection.neuron)}
          title="Add the selected neuron (A)"
        >
          + L{st.selection.layer}·N{st.selection.neuron}
        </button>
        <button
          type="button"
          className="nrn-chip"
          onClick={undo}
          disabled={!st.canUndo}
          title="Undo (Ctrl/Cmd Z)"
        >
          Undo
        </button>
        <button
          type="button"
          className="nrn-chip"
          onClick={() => {
            clearRecipe();
            neuronSound.reset();
          }}
          disabled={!st.recipe.length}
        >
          Clear
        </button>
        <button type="button" className="nrn-chip" onClick={onExport} disabled={!st.recipe.length}>
          Export
        </button>
        <button type="button" className="nrn-chip" onClick={onImport}>
          Import
        </button>
      </div>
      {st.error && (
        <button
          type="button"
          className="nrn-row"
          onClick={clearError}
          style={{
            ...mono,
            textAlign: 'left',
            margin: '0 12px 8px',
            padding: '6px 8px',
            border: 'none',
            borderLeft: '2px solid var(--danger)',
            background: 'var(--bg-secondary)',
            color: 'var(--text-primary)',
            cursor: 'pointer',
          }}
          title="Dismiss"
        >
          {st.error}
        </button>
      )}
      {!st.recipe.length ? (
        <p style={{ ...mono, padding: '4px 12px', lineHeight: 1.5 }}>
          Select a neuron in the stack and set its strength below — or press A to add it at zero.
        </p>
      ) : (
        <div style={{ display: 'grid' }}>
          {st.recipe.map((e, i) => (
            <RecipeRow
              key={`${e.layer}:${e.neuron}`}
              edit={e}
              index={i}
              focused={`${e.layer}:${e.neuron}` === selKey}
            />
          ))}
        </div>
      )}
      <p style={{ ...mono, padding: '12px', marginTop: 'auto', lineHeight: 1.5, fontSize: 10 }}>
        Edits the activation before <code>down_proj</code> on every token. Additive doses are in
        units of the layer’s median residual norm ÷ the neuron’s down-column norm; multiply 0
        ablates.
      </p>
    </aside>
  );
}

function RecipeRow({
  edit,
  index,
  focused,
}: {
  edit: NeuronEdit;
  index: number;
  focused: boolean;
}) {
  const v = influence(edit);
  const min = edit.mode === 'mul' ? 0 : -MAX_DOSE;
  return (
    <article
      {...entranceProps(Math.min(index, 8))}
      className={`nrn-row${focused ? ' focused' : ''}${edit.enabled ? '' : ' off'}`}
      style={{ display: 'grid', gap: 4, padding: '8px 12px', borderTop: '1px solid var(--border)' }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <i style={{ width: 3, alignSelf: 'stretch', background: polarityColor(v) }} />
        <button
          type="button"
          className="nrn-chip"
          style={{
            border: 'none',
            padding: '0 4px',
            fontFamily: 'var(--font-mono)',
            color: 'var(--text-primary)',
          }}
          onClick={() => select(edit.layer, edit.neuron)}
          title="Fly to this neuron"
        >
          L{edit.layer} · N{edit.neuron}
        </button>
        <span style={{ ...mono, marginLeft: 'auto', color: polarityColor(v) }}>
          {edit.mode === 'mul' ? `×${edit.dose.toFixed(2)}` : fmtDose(edit.dose)}
        </span>
        <button
          type="button"
          className="nrn-chip"
          aria-pressed={edit.enabled}
          title={edit.enabled ? 'Disable' : 'Enable'}
          onClick={() => toggleEnabled(edit.layer, edit.neuron)}
        >
          {edit.enabled ? 'on' : 'off'}
        </button>
        <button
          type="button"
          className="nrn-chip"
          title="Remove"
          onClick={() => removeEdit(edit.layer, edit.neuron)}
        >
          <CloseIcon />
        </button>
      </div>
      <input
        type="range"
        min={min}
        max={MAX_DOSE}
        step={0.05}
        value={edit.dose}
        aria-label={`Dose for layer ${edit.layer} neuron ${edit.neuron}`}
        onPointerDown={beginDose}
        onKeyDown={beginDose}
        onChange={(e) => {
          const d = Number(e.target.value);
          setDose(edit.layer, edit.neuron, d, false);
          neuronSound.dose(d);
        }}
        style={{ width: '100%', accentColor: 'var(--accent)' }}
      />
      <div style={{ display: 'flex', alignItems: 'center', ...mono, fontSize: 10 }}>
        <span>{edit.mode === 'mul' ? '×0' : '−3'}</span>
        <button
          type="button"
          className="nrn-chip"
          style={{
            margin: '0 auto',
            height: 20,
            fontSize: 10,
            letterSpacing: '0.1em',
            textTransform: 'uppercase',
          }}
          onClick={() => setMode(edit.layer, edit.neuron, edit.mode === 'add' ? 'mul' : 'add')}
          title="Switch between adding an offset and scaling the activation"
        >
          {edit.mode === 'add' ? 'additive' : 'multiply'}
        </button>
        <span>{edit.mode === 'mul' ? '×3' : '+3'}</span>
      </div>
    </article>
  );
}

// ── the comparison ───────────────────────────────────────────────────────────

function ComparePanel() {
  const st = useNeurons();
  const c = st.comparison;
  return (
    <section className="nrn-compare" style={{ ...panel, borderLeft: '1px solid var(--border)' }}>
      <div style={{ padding: '12px 12px 6px', display: 'flex', alignItems: 'baseline' }}>
        <span style={heading}>Compare replies</span>
        {c && <span style={{ ...mono, marginLeft: 'auto' }}>greedy · same prompt</span>}
      </div>
      {!c ? (
        <p style={{ ...mono, padding: '0 12px', lineHeight: 1.5 }}>
          Ask something below. The original reply is written first, then the steered one; both
          decode greedily, so the recipe is the only difference. Click any token to light the stack
          by what fired there.
        </p>
      ) : (
        <>
          <ReplyCard which="baseline" title="Original" branch={c.baseline} note="No intervention" />
          <ReplyCard which="steered" title="Steered" branch={c.steered} note={summary(c.recipe)} />
        </>
      )}
      {st.capture && <TopNeurons />}
    </section>
  );
}

function ReplyCard({
  which,
  title,
  branch,
  note,
}: {
  which: 'baseline' | 'steered';
  title: string;
  branch: Branch;
  note: string;
}) {
  const st = useNeurons();
  const picked = st.capture?.branch === which ? st.capture.index : null;
  const accent = which === 'steered' ? 'var(--nrn-pos)' : 'var(--text-secondary)';
  return (
    <article
      style={{
        margin: '6px 12px',
        background: 'var(--bg-secondary)',
        borderTop: `2px solid ${accent}`,
        clipPath: 'polygon(0 0, calc(100% - 10px) 0, 100% 10px, 100% 100%, 0 100%)',
        display: 'grid',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, padding: '8px 10px 4px' }}>
        <span style={{ ...heading, color: 'var(--text-primary)' }}>{title}</span>
        <span style={{ ...mono, marginLeft: 'auto' }}>
          <RollingCounter value={branch.tokens.length} /> tok{branch.done ? '' : ' …'}
        </span>
      </div>
      <div
        style={{
          padding: '2px 10px 8px',
          fontSize: 13,
          lineHeight: 1.55,
          color: 'var(--text-primary)',
          maxHeight: 260,
          overflow: 'auto',
        }}
      >
        {branch.tokens.length === 0 && !branch.done ? (
          <span style={mono}>waiting…</span>
        ) : (
          branch.tokens.map((t, i) => {
            // A button never breaks its parent's line, whatever its white-space: a
            // token's newlines are drawn as a visible ↵ inside and real breaks after.
            const breaks = t.split('\n').length - 1;
            return (
              <Fragment key={i}>
                <button
                  type="button"
                  className={`nrn-token${picked === i ? ' picked' : ''}`}
                  disabled={!branch.done || !!st.busy}
                  title={`Token ${i} · ${showToken(t, 24)} — read every layer’s activations where it was predicted`}
                  onClick={() => void captureAt(which, i)}
                >
                  {breaks ? t.replace(/\n/g, '↵') : t}
                </button>
                {Array.from({ length: breaks }, (_, k) => (
                  <br key={k} />
                ))}
              </Fragment>
            );
          })
        )}
      </div>
      <div
        style={{
          ...mono,
          padding: '4px 10px 8px',
          borderTop: '1px solid var(--border)',
          fontSize: 10,
        }}
      >
        {note}
      </div>
    </article>
  );
}

function TopNeurons() {
  const st = useNeurons();
  const cap = st.capture!;
  const max = Math.max(1e-9, ...st.top.map((t) => Math.abs(t.value)));
  return (
    <div style={{ padding: '10px 12px 14px', display: 'grid', gap: 6 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
        <span style={heading}>Fired hardest</span>
        <span style={{ ...mono, marginLeft: 'auto' }}>
          {cap.branch} · tok {cap.index} → {showToken(cap.predicted, 12)}
        </span>
      </div>
      <div style={{ display: 'flex', gap: 4 }}>
        <button
          type="button"
          className="nrn-chip"
          aria-pressed={!st.topThisLayer}
          onClick={() => setTopThisLayer(false)}
        >
          All layers
        </button>
        <button
          type="button"
          className="nrn-chip"
          aria-pressed={st.topThisLayer}
          onClick={() => setTopThisLayer(true)}
        >
          Layer {st.selection.layer}
        </button>
        <button
          type="button"
          className="nrn-chip"
          style={{ marginLeft: 'auto' }}
          onClick={clearCapture}
          title="Clear the heat"
        >
          <CloseIcon />
        </button>
      </div>
      <div style={{ display: 'grid' }}>
        {st.top.map((t, i) => (
          <button
            key={`${t.layer}:${t.neuron}`}
            type="button"
            className="nrn-top"
            {...entranceProps(Math.min(i, 10))}
            onClick={() => {
              select(t.layer, t.neuron);
              neuronSound.select(t.layer);
            }}
            style={{
              ...entranceProps(Math.min(i, 10)).style,
              display: 'grid',
              gridTemplateColumns: '78px 1fr 64px',
              alignItems: 'center',
              gap: 8,
              height: 22,
              padding: '0 4px',
              border: 'none',
              borderRadius: 0,
              background: 'none',
              cursor: 'pointer',
              ...mono,
              textAlign: 'left',
            }}
          >
            <span style={{ color: 'var(--text-primary)' }}>
              L{t.layer}·N{t.neuron}
            </span>
            <span style={{ height: 4, background: 'var(--border)', position: 'relative' }}>
              <span
                style={{
                  position: 'absolute',
                  left: 0,
                  top: 0,
                  bottom: 0,
                  width: `${(Math.abs(t.value) / max) * 100}%`,
                  background: polarityColor(t.value),
                }}
              />
            </span>
            <span style={{ textAlign: 'right' }}>{t.value.toFixed(2)}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

// ── the dock ─────────────────────────────────────────────────────────────────

function Dock({ selEdit, selAct }: { selEdit: NeuronEdit | undefined; selAct: number | null }) {
  const st = useNeurons();
  const sel = st.selection;
  const running = !!st.busy && !!st.comparison && !st.comparison.steered.done;
  const dose = selEdit?.dose ?? 0;
  const mul = selEdit?.mode === 'mul';
  const label = useMemo(() => {
    if (!selEdit) return 'Neutral · additive';
    const v = influence(selEdit);
    const kind = selEdit.mode === 'mul' ? 'multiply' : 'additive';
    return `${v > 0 ? 'Pushes' : v < 0 ? 'Pulls' : 'Neutral'} · ${kind}${selEdit.enabled ? '' : ' · off'}`;
  }, [selEdit]);
  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: 'minmax(0, 1fr) auto',
        gap: '8px 16px',
        padding: '10px 12px',
        borderTop: '1px solid var(--border)',
        background: 'var(--bg-secondary)',
        alignItems: 'center',
      }}
    >
      <form
        style={{ display: 'flex', gap: 6, minWidth: 0 }}
        onSubmit={(e) => {
          e.preventDefault();
          if (running) stopComparison();
          else void runComparison();
        }}
      >
        <input
          type="text"
          value={st.prompt}
          maxLength={20000}
          placeholder="Ask a question — the original and steered replies appear side by side"
          onChange={(e) => setPrompt(e.target.value)}
          style={{ flex: 1, minWidth: 0, padding: '0 0.6rem' }}
          aria-label="Prompt"
        />
        <button type="submit" disabled={!running && (!st.prompt.trim() || !!st.busy)}>
          {running ? 'Stop' : 'Compare'}
        </button>
      </form>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ ...heading, fontSize: 10 }}>Max tokens</span>
        <input
          type="number"
          min={1}
          max={4096}
          value={st.maxTokens}
          onChange={(e) => setMaxTokens(Number(e.target.value))}
          style={{ width: 72, padding: '0 0.6rem' }}
          aria-label="Max new tokens"
        />
      </div>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          gridColumn: '1 / -1',
          minWidth: 0,
        }}
      >
        <span style={{ ...heading, fontSize: 10, flex: 'none' }}>
          Strength · L{sel.layer}·N{sel.neuron}
        </span>
        <input
          type="range"
          min={mul ? 0 : -MAX_DOSE}
          max={MAX_DOSE}
          step={0.05}
          value={dose}
          onPointerDown={beginDose}
          onKeyDown={beginDose}
          onChange={(e) => {
            const d = Number(e.target.value);
            setDose(sel.layer, sel.neuron, d, false);
            neuronSound.dose(d);
          }}
          style={{ flex: 1, minWidth: 80, accentColor: 'var(--accent)' }}
          aria-label="Strength of the selected neuron"
        />
        <input
          type="number"
          min={mul ? 0 : -MAX_DOSE}
          max={MAX_DOSE}
          step={0.01}
          value={dose}
          onChange={(e) => {
            const d = Number(e.target.value);
            if (Number.isFinite(d)) setDose(sel.layer, sel.neuron, d);
          }}
          style={{ width: 76, padding: '0 0.6rem' }}
          aria-label="Exact strength of the selected neuron"
        />
        <span
          style={{
            ...mono,
            flex: 'none',
            minWidth: 140,
            color: polarityColor(selEdit ? influence(selEdit) : 0),
          }}
        >
          {label}
        </span>
        {selAct !== null && <span style={{ ...mono, flex: 'none' }}>act {selAct.toFixed(3)}</span>}
      </div>
    </div>
  );
}

// ── glyphs: vector strokes on currentColor ──────────────────────────────────

const svg: CSSProperties = {
  width: 14,
  height: 14,
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.5,
  strokeLinecap: 'round',
  strokeLinejoin: 'round',
};

function Chevron({ up }: { up?: boolean }) {
  return (
    <svg viewBox="0 0 24 24" style={svg} aria-hidden="true">
      <path d={up ? 'm6 14 6-6 6 6' : 'm6 10 6 6 6-6'} />
    </svg>
  );
}

function Magnify() {
  return (
    <svg viewBox="0 0 24 24" style={svg} aria-hidden="true">
      <circle cx="10.5" cy="10.5" r="6.5" />
      <path d="m15.3 15.3 4.7 4.7M10.5 7.75v5.5M7.75 10.5h5.5" />
    </svg>
  );
}

function Cube() {
  return (
    <svg viewBox="0 0 24 24" style={svg} aria-hidden="true">
      <path d="m12 3 9 5v8l-9 5-9-5V8l9-5Zm0 9 9-4M3 8l9 4v9" />
    </svg>
  );
}

function ResetIcon() {
  return (
    <svg viewBox="0 0 24 24" style={svg} aria-hidden="true">
      <path d="M4 10a8 8 0 1 1 1 8M4 4v6h6" />
    </svg>
  );
}

function SoundIcon({ on }: { on: boolean }) {
  return (
    <svg viewBox="0 0 24 24" style={svg} aria-hidden="true">
      <path
        d={
          on ? 'M11 4 6 8H3v8h3l5 4V4Zm4 4a6 6 0 0 1 0 8' : 'M11 4 6 8H3v8h3l5 4V4Zm5 5 5 6m0-6-5 6'
        }
      />
    </svg>
  );
}

function CloseIcon() {
  return (
    <svg viewBox="0 0 24 24" style={{ ...svg, width: 12, height: 12 }} aria-hidden="true">
      <path d="m6 6 12 12M6 18 18 6" />
    </svg>
  );
}
