/**
 * Home & Playbooks: the self-teaching front door of the Interpretability IDE.
 *
 * Replaces a flat icon grid with:
 * 1. Interactive Goal-Driven Playbooks that configure paired side-by-side views
 *    for the six core AI-research questions.
 * 2. Live workbench status tiles (Agent model, llama.cpp runtime, WebML GPU,
 *    captured turns, shared locus) — each clickable to configure.
 * 3. The complete tool atlas across ALL groups (`SUITE_GROUPS` + `Library`),
 *    fixing the bug where Features, Circuits and Library were omitted.
 * 4. Deep-links to external notebook/corpus companions (Datasets, HF Hub,
 *    Training, LocalTrack, Papers).
 */
import { useEffect, useState, useSyncExternalStore, type ReactNode } from 'react';

import { IconSplit, NAMED_GLYPHS } from '../../glyphs';
import { useModelLocus } from '../../model-locus';
import { registry } from '../../registry';
import { RollingCounter } from '../../viz/RollingCounter';
import { RESEARCH_PANE_GUIDE } from '../interpretability/ResearchGuide';
import { interpretabilityStore } from '../interpretability/store';
import { getLlamaStatus, type LlamaStatus } from '../llamacpp/api';
import { useEngineState } from '../webml/engine';
import { SUITE_SECTIONS, SUITE_VIEW } from './ids';
import { locusLabel } from './locus-label';
import { launchPlaybook, SUITE_PLAYBOOKS } from './playbooks';
import { openSuiteTab } from './stage';
import { S } from './styles';
import { useSuiteAgentContext } from './SuiteFrame';
import { SUITE_GROUPS, SUITE_TOOLS, toolForSection } from './tools';

function Tile({
  label,
  onClick,
  title,
  children,
  index,
}: {
  label: string;
  onClick?: () => void;
  title?: string;
  children: ReactNode;
  index: number;
}) {
  if (onClick) {
    return (
      <button
        type="button"
        className="interp-card-button"
        title={title}
        onClick={onClick}
        style={{
          ...S.tile,
          ...S.stagger(index),
          border: '1px solid var(--border)',
          borderTop: '2px solid var(--accent)',
          textAlign: 'left',
          cursor: 'pointer',
          font: 'inherit',
        }}
      >
        <div style={S.heading}>{label}</div>
        <div style={S.tileValue}>{children}</div>
      </button>
    );
  }
  return (
    <div style={{ ...S.tile, ...S.stagger(index) }}>
      <div style={S.heading}>{label}</div>
      <div style={S.tileValue}>{children}</div>
    </div>
  );
}

export function HomeSection() {
  useSuiteAgentContext('Home');
  const arch = useSyncExternalStore(
    interpretabilityStore.subscribe,
    interpretabilityStore.getArchitecture,
  );
  const turns = useSyncExternalStore(
    interpretabilityStore.subscribe,
    interpretabilityStore.getSnapshot,
  );
  const locus = useModelLocus();
  const webml = useEngineState();
  const [llama, setLlama] = useState<LlamaStatus | null>(null);

  useEffect(() => {
    let alive = true;
    void getLlamaStatus()
      .then((s) => alive && setLlama(s))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, []);

  // Research stages served outside this IDE (Papers, Datasets, Training/LocalTrack).
  const elsewhere = RESEARCH_PANE_GUIDE.filter((stage) => stage.paneId !== SUITE_VIEW);
  const libraryTool = toolForSection(SUITE_SECTIONS.library);

  return (
    <div style={S.home}>
      <h2 style={S.homeTitle}>Interpretability &amp; Model Lab IDE</h2>
      <p style={S.lede}>
        Test models in-browser or on llama.cpp, debug agent runs and tool evaluations, and inspect
        prompts, weights, activations, features, and circuits in one workspace. Open any two tools
        side-by-side — clicking a layer, token, or turn synchronizes every view on screen.
      </p>

      {/* Live clickable status tiles */}
      <div style={S.tiles}>
        <Tile
          label="Agent Model"
          index={0}
          title="Open Model Explorer"
          onClick={() => openSuiteTab(SUITE_SECTIONS.model)}
        >
          {arch && !arch.error
            ? `${arch.model}${arch.layers != null ? ` · ${arch.layers}L` : ''}`
            : 'no model resolved → inspect'}
        </Tile>
        <Tile
          label="llama.cpp Server"
          index={1}
          title="Manage local GGUF runtime & weights"
          onClick={() => openSuiteTab(SUITE_SECTIONS.runtime)}
        >
          {llama?.ready
            ? llama.model || llama.modelPath?.split(/[\\/]/).pop() || 'serving'
            : llama?.running
              ? 'loading…'
              : 'stopped → configure'}
        </Tile>
        <Tile
          label="WebML (Browser GPU)"
          index={2}
          title="Open WebGPU Playground"
          onClick={() => openSuiteTab(SUITE_SECTIONS.playground)}
        >
          {webml.kind === 'ready'
            ? (webml.model.split('/').pop() ?? webml.model)
            : webml.kind === 'loading'
              ? 'loading…'
              : 'idle → load model'}
        </Tile>
        <Tile
          label="Captured Turns"
          index={3}
          title="Inspect agent runs & prompts"
          onClick={() => openSuiteTab(SUITE_SECTIONS.runs)}
        >
          <RollingCounter value={turns.length} />
        </Tile>
        <Tile label="Shared Locus" index={4}>
          {locusLabel(locus) ?? 'click any layer / token'}
        </Tile>
      </div>

      {/* Goal-Driven Playbooks */}
      <section>
        <h3 style={S.groupHead}>Guided Playbooks — Start from a question (opens side-by-side)</h3>
        <div style={S.cards}>
          {SUITE_PLAYBOOKS.map((pb, i) => {
            const Icon = NAMED_GLYPHS[pb.glyph];
            const primaryLabel = toolForSection(pb.primary)?.label ?? pb.primary;
            const companionLabel = toolForSection(pb.splitWith)?.label ?? pb.splitWith;
            return (
              <button
                key={pb.id}
                type="button"
                className="interp-playbook-card"
                style={{
                  ...S.card,
                  ...S.stagger(i + 2),
                  borderTop: '2px solid var(--accent)',
                }}
                onClick={() => launchPlaybook(pb.id)}
              >
                <span style={S.cardTitle}>
                  {Icon ? <Icon /> : null}
                  <span>{pb.title}</span>
                </span>
                <span style={{ ...S.cardText, color: 'var(--text-primary)', fontWeight: 600 }}>
                  {pb.question}
                </span>
                <span style={S.cardText}>{pb.summary}</span>
                <span
                  style={{
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 6,
                    marginTop: 2,
                    fontFamily: 'var(--font-mono)',
                    fontSize: 10.5,
                    color: 'var(--accent)',
                    ['--icon-size' as string]: '12px',
                  }}
                >
                  <IconSplit />
                  {primaryLabel} + {companionLabel}
                </span>
              </button>
            );
          })}
        </div>
      </section>

      {/* Complete Tool Atlas across all progressive groups */}
      {SUITE_GROUPS.map((group) => (
        <section key={group}>
          <h3 style={S.groupHead}>{group}</h3>
          <div style={S.cards}>
            {SUITE_TOOLS.filter((t) => t.group === group).map((tool, i) => {
              const Icon = NAMED_GLYPHS[tool.glyph];
              return (
                <button
                  key={tool.section}
                  type="button"
                  className="interp-card-button"
                  style={{ ...S.card, ...S.stagger(i + 4) }}
                  onClick={() => openSuiteTab(tool.section)}
                >
                  <span style={S.cardTitle}>
                    {Icon ? <Icon /> : null}
                    {tool.label}
                  </span>
                  <span style={{ ...S.cardText, color: 'var(--text-primary)' }}>
                    {tool.question}
                  </span>
                  <span style={S.cardText}>{tool.when}</span>
                </button>
              );
            })}
          </div>
        </section>
      ))}

      {/* Library & Worker */}
      {libraryTool ? (
        <section>
          <h3 style={S.groupHead}>Artifacts &amp; GPU Worker</h3>
          <div style={S.cards}>
            <button
              type="button"
              className="interp-card-button"
              style={S.card}
              onClick={() => openSuiteTab(libraryTool.section)}
            >
              <span style={S.cardTitle}>
                {NAMED_GLYPHS[libraryTool.glyph] ? NAMED_GLYPHS[libraryTool.glyph]({}) : null}
                {libraryTool.label}
              </span>
              <span style={{ ...S.cardText, color: 'var(--text-primary)' }}>
                {libraryTool.question}
              </span>
              <span style={S.cardText}>{libraryTool.when}</span>
            </button>
          </div>
        </section>
      ) : null}

      {/* External AI Research Companions */}
      <h3 style={S.groupHead}>Connected Research Surfaces (Data, Hub &amp; Training)</h3>
      <div style={S.cards}>
        {elsewhere.map((stage, i) => (
          <button
            key={stage.id}
            type="button"
            className="interp-card-button"
            style={{ ...S.card, ...S.stagger(i + 8) }}
            onClick={() => registry.openPanel(stage.paneId)}
          >
            <span style={S.cardTitle}>{stage.title}</span>
            <span style={{ ...S.cardText, color: 'var(--text-primary)' }}>{stage.question}</span>
            <span style={S.cardText}>{stage.tip}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
