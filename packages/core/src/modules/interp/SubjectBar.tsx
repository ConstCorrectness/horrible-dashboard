/**
 * SubjectBar: the single IDE chrome bar rendered above the kept-alive stage.
 *
 * Replaces repeating `Model · Locus · Trace` inside each split child with one
 * authoritative bar across the top of `interpretability.suite`:
 * 1. Open stage tabs + 1-click side-by-side Split / Swap / Close Split controls.
 * 2. Live status & 1-click navigation for the three engines (Agent model,
 *    llama.cpp local server, WebML GPU) + the shared model locus + Ask Agent.
 */
import { useContext, useEffect, useState, useSyncExternalStore } from 'react';

import { PaneInstanceContext } from '../../agent-context';
import { IconClose, IconComment, IconRepeat, IconSplit, NAMED_GLYPHS } from '../../glyphs';
import { activeSectionOf, setPaneSection } from '../../layout/controller';
import { findPaneAnywhere } from '../../layout/model';
import { layoutStore } from '../../layout/store';
import { clearModelLocus, useModelLocus } from '../../model-locus';
import { interpretabilityStore } from '../interpretability/store';
import { getLlamaStatus, type LlamaStatus } from '../llamacpp/api';
import { useEngineState } from '../webml/engine';
import { askInterp } from './ask';
import { SUITE_SECTIONS, type SuiteSection } from './ids';
import { locusLabel } from './locus-label';
import {
  closeSuiteTab,
  isSuiteSection,
  NATURAL_COMPANIONS,
  parseStageState,
  splitSuiteWith,
  swapSuiteSplit,
} from './stage';
import { S } from './styles';
import { SUITE_TOOLS, toolForSection } from './tools';

function shortModel(name: string | undefined | null): string {
  if (!name) return 'none';
  const tail = name.replace(/^gguf:[^/]+\/[^/]+\//, '').replace(/^node-gguf:/, '');
  const base = tail.split(/[\\/]/).pop() ?? tail;
  return base.length > 28 ? `${base.slice(0, 26)}…` : base;
}

function StatusDot({ tone }: { tone: 'ok' | 'warn' | 'idle' }) {
  const color =
    tone === 'ok' ? 'var(--success)' : tone === 'warn' ? 'var(--warning)' : 'var(--text-secondary)';
  return (
    <span
      aria-hidden
      style={{
        display: 'inline-block',
        width: 6,
        height: 6,
        borderRadius: '50%',
        background: color,
        flex: 'none',
      }}
    />
  );
}

export function SubjectBar() {
  const instanceId = useContext(PaneInstanceContext);
  const pane = useSyncExternalStore(
    layoutStore.subscribe,
    () => (instanceId ? findPaneAnywhere(layoutStore.getSnapshot().frame, instanceId)?.pane : null),
    () => null,
  );
  const arch = useSyncExternalStore(
    interpretabilityStore.subscribe,
    interpretabilityStore.getArchitecture,
  );
  const webml = useEngineState();
  const locus = useModelLocus();
  const [llama, setLlama] = useState<LlamaStatus | null>(null);

  const rawActive = pane ? activeSectionOf(pane) : SUITE_SECTIONS.home;
  const active: SuiteSection = isSuiteSection(rawActive) ? rawActive : SUITE_SECTIONS.home;
  const stage = parseStageState(pane?.params, active);

  // Persist newly visited active/split sections into `openTabs` so the IDE tab strip
  // remembers every tool you opened in this session.
  useEffect(() => {
    if (!instanceId || !pane) return;
    const stored = Array.isArray(pane.params?.openTabs) ? pane.params.openTabs : [];
    const same =
      stored.length === stage.openTabs.length && stage.openTabs.every((t, i) => stored[i] === t);
    if (!same) {
      layoutStore.dispatch({
        type: 'SET_PANE_PARAMS',
        instanceId,
        params: { ...(pane.params ?? {}), openTabs: stage.openTabs },
      });
    }
  }, [instanceId, pane, stage.openTabs]);

  // Lightweight status check for local llama.cpp server when mounted / switching tabs.
  useEffect(() => {
    let alive = true;
    void getLlamaStatus()
      .then((s) => alive && setLlama(s))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [active]);

  if (!instanceId || !pane) return null;

  const where = locusLabel(locus);
  const natural = NATURAL_COMPANIONS[active];
  const activeTool = toolForSection(active);
  const splitTool = stage.splitSection ? toolForSection(stage.splitSection) : undefined;
  const askLabel = splitTool
    ? `${activeTool?.label ?? active} + ${splitTool.label}`
    : (activeTool?.label ?? active);

  const llamaTone: 'ok' | 'warn' | 'idle' = llama?.ready ? 'ok' : llama?.running ? 'warn' : 'idle';
  const webmlTone: 'ok' | 'warn' | 'idle' =
    webml.kind === 'ready' ? 'ok' : webml.kind === 'loading' ? 'warn' : 'idle';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', flex: 'none' }}>
      {/* Row 1: IDE Stage Tabs + Split View Controls */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 2,
          minHeight: 30,
          padding: '0 8px',
          borderBottom: '1px solid var(--border)',
          background: 'var(--bg-secondary)',
          overflowX: 'auto',
        }}
      >
        <div
          role="tablist"
          aria-label="Open interpretability tabs"
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 2,
            flex: 1,
            minWidth: 0,
            overflowX: 'auto',
          }}
        >
          {stage.openTabs.map((sec) => {
            const tool = toolForSection(sec);
            const Icon = tool?.glyph ? NAMED_GLYPHS[tool.glyph] : undefined;
            const isPrimary = sec === active;
            const isSplit = sec === stage.splitSection;
            return (
              <div
                key={sec}
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  height: 28,
                  borderBottom: isPrimary
                    ? '2px solid var(--accent)'
                    : isSplit
                      ? '2px solid color-mix(in srgb, var(--accent) 50%, var(--border))'
                      : '2px solid transparent',
                  background: isPrimary || isSplit ? 'var(--bg-primary)' : 'transparent',
                  flex: 'none',
                }}
              >
                <button
                  type="button"
                  role="tab"
                  aria-selected={isPrimary}
                  className="interp-stage-tab"
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 6,
                    height: 26,
                    padding: '0 8px',
                    border: 'none',
                    borderRadius: 0,
                    background: 'none',
                    color: isPrimary || isSplit ? 'var(--text-primary)' : 'var(--text-secondary)',
                    fontSize: 11.5,
                    fontWeight: isPrimary ? 700 : 500,
                    cursor: 'pointer',
                    ['--icon-size' as string]: '13px',
                  }}
                  onClick={() => {
                    if (isSplit) {
                      // Focus the already-visible split slot without losing the split.
                      const nextSide = stage.splitPrimarySide === 'left' ? 'right' : 'left';
                      layoutStore.dispatch({
                        type: 'SET_PANE_PARAMS',
                        instanceId,
                        params: {
                          ...(pane.params ?? {}),
                          splitSection: active,
                          splitPrimarySide: nextSide,
                        },
                      });
                    }
                    setPaneSection(instanceId, sec);
                  }}
                  onAuxClick={(e) => {
                    if (e.button === 1 && stage.openTabs.length > 1) {
                      e.preventDefault();
                      closeSuiteTab(instanceId, sec);
                    }
                  }}
                  title={isSplit ? `${tool?.label ?? sec} (side-by-side)` : (tool?.label ?? sec)}
                >
                  {Icon ? <Icon /> : null}
                  <span>{tool?.label ?? sec}</span>
                </button>
                {stage.openTabs.length > 1 ? (
                  <button
                    type="button"
                    className="interp-stage-tab-close"
                    aria-label={`Close ${tool?.label ?? sec} tab`}
                    title="Close tab"
                    style={{
                      display: 'inline-flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      width: 18,
                      height: 24,
                      padding: 0,
                      border: 'none',
                      borderRadius: 0,
                      background: 'none',
                      color: 'var(--text-secondary)',
                      cursor: 'pointer',
                      ['--icon-size' as string]: '10px',
                    }}
                    onClick={() => closeSuiteTab(instanceId, sec)}
                  >
                    <IconClose />
                  </button>
                ) : null}
              </div>
            );
          })}
        </div>

        {/* Side-by-side Split Controls */}
        <div
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 6,
            flex: 'none',
            paddingLeft: 6,
          }}
        >
          {stage.splitSection ? (
            <>
              <button
                type="button"
                className="interp-mini-action"
                title="Swap left and right split views"
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 4,
                  height: 22,
                  padding: '0 7px',
                  border: '1px solid var(--border)',
                  borderRadius: 3,
                  background: 'var(--bg-primary)',
                  color: 'var(--text-secondary)',
                  fontSize: 10.5,
                  fontWeight: 600,
                  cursor: 'pointer',
                  ['--icon-size' as string]: '12px',
                }}
                onClick={() => swapSuiteSplit(instanceId)}
              >
                <IconRepeat />
                Swap
              </button>
              <button
                type="button"
                className="interp-mini-action"
                title="Close side-by-side split"
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 4,
                  height: 22,
                  padding: '0 7px',
                  border: '1px solid var(--border)',
                  borderRadius: 3,
                  background: 'var(--bg-primary)',
                  color: 'var(--text-primary)',
                  fontSize: 10.5,
                  fontWeight: 600,
                  cursor: 'pointer',
                  ['--icon-size' as string]: '11px',
                }}
                onClick={() => splitSuiteWith(instanceId, null)}
              >
                <IconClose />
                Single view
              </button>
            </>
          ) : (
            <>
              {natural ? (
                <button
                  type="button"
                  className="interp-mini-action"
                  title={`Open ${natural.label} side-by-side with ${activeTool?.label ?? active}`}
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 5,
                    height: 22,
                    padding: '0 8px',
                    border: '1px solid var(--border)',
                    borderRadius: 3,
                    background: 'var(--bg-primary)',
                    color: 'var(--text-primary)',
                    fontSize: 10.5,
                    fontWeight: 600,
                    cursor: 'pointer',
                    ['--icon-size' as string]: '12px',
                  }}
                  onClick={() => splitSuiteWith(instanceId, natural.section)}
                >
                  <IconSplit />
                  Pair: {natural.label}
                </button>
              ) : null}
              <select
                aria-label="Pair side-by-side with another tool"
                value=""
                onChange={(e) => {
                  const val = e.target.value;
                  if (isSuiteSection(val)) splitSuiteWith(instanceId, val);
                }}
                style={{
                  width: 126,
                  padding: '0 6px',
                  fontSize: 11,
                }}
              >
                <option value="">Split beside…</option>
                {SUITE_TOOLS.filter(
                  (t) => t.section !== active && t.section !== SUITE_SECTIONS.home,
                ).map((t) => (
                  <option key={t.section} value={t.section}>
                    {t.label}
                  </option>
                ))}
              </select>
            </>
          )}
        </div>
      </div>

      {/* Row 2: Unified Runtime, Model & Shared Locus Strip */}
      <div style={S.strip}>
        <button
          type="button"
          className="interp-chip-btn"
          title={arch?.sourceDetail || 'Inspect active model architecture'}
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 6,
            height: 22,
            padding: '0 6px',
            border: 'none',
            borderRadius: 3,
            background: 'none',
            color: 'var(--text-primary)',
            cursor: 'pointer',
            fontFamily: 'var(--font-mono)',
            fontSize: 11,
          }}
          onClick={() => setPaneSection(instanceId, SUITE_SECTIONS.model)}
        >
          <StatusDot tone={arch && !arch.error ? 'ok' : 'idle'} />
          <span style={S.stripLabel}>Agent Model</span>
          <span>{shortModel(arch?.model)}</span>
        </button>

        <button
          type="button"
          className="interp-chip-btn"
          title="Manage local llama.cpp server & GGUF weights"
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 6,
            height: 22,
            padding: '0 6px',
            border: 'none',
            borderRadius: 3,
            background: 'none',
            color: 'var(--text-primary)',
            cursor: 'pointer',
            fontFamily: 'var(--font-mono)',
            fontSize: 11,
          }}
          onClick={() => setPaneSection(instanceId, SUITE_SECTIONS.runtime)}
        >
          <StatusDot tone={llamaTone} />
          <span style={S.stripLabel}>llama.cpp</span>
          <span>
            {llama?.ready
              ? shortModel(llama.modelPath || llama.model || 'serving')
              : llama?.running
                ? 'loading…'
                : 'idle'}
          </span>
        </button>

        <button
          type="button"
          className="interp-chip-btn"
          title="Manage in-browser WebGPU model in Playground"
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 6,
            height: 22,
            padding: '0 6px',
            border: 'none',
            borderRadius: 3,
            background: 'none',
            color: 'var(--text-primary)',
            cursor: 'pointer',
            fontFamily: 'var(--font-mono)',
            fontSize: 11,
          }}
          onClick={() => setPaneSection(instanceId, SUITE_SECTIONS.playground)}
        >
          <StatusDot tone={webmlTone} />
          <span style={S.stripLabel}>WebML GPU</span>
          <span>
            {webml.kind === 'ready'
              ? shortModel(webml.model)
              : webml.kind === 'loading'
                ? 'loading…'
                : 'idle'}
          </span>
        </button>

        {where || locus.traceId ? (
          <span
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
              padding: '0 7px',
              height: 20,
              background: 'color-mix(in srgb, var(--accent) 12%, transparent)',
              borderLeft: '2px solid var(--accent)',
              fontFamily: 'var(--font-mono)',
              fontSize: 11,
              color: 'var(--text-primary)',
            }}
          >
            <span style={S.stripLabel}>Locus</span>
            <span>
              {[where, locus.traceId ? `trace ${locus.traceId.slice(0, 8)}` : '']
                .filter(Boolean)
                .join(' · ')}
            </span>
            <button
              type="button"
              className="interp-stage-tab-close"
              aria-label="Clear shared model locus"
              title="Clear shared model locus"
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                border: 'none',
                padding: 0,
                width: 14,
                height: 14,
                background: 'none',
                color: 'var(--text-secondary)',
                cursor: 'pointer',
                ['--icon-size' as string]: '10px',
              }}
              onClick={() => clearModelLocus()}
            >
              <IconClose />
            </button>
          </span>
        ) : null}

        <button
          type="button"
          className="interp-ask"
          style={S.askButton}
          title="Ask the specialist interpretability agent about what is on screen"
          onClick={() => askInterp(askLabel)}
        >
          <IconComment />
          Ask Agent
        </button>
      </div>
    </div>
  );
}
