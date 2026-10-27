/**
 * Home: what the suite is looking at, and a map of the tools that look at it.
 *
 * It replaces two things. The workflow strip that sat atop the context view and
 * the explorer, jumping between three panes — the rail is that strip now. And the
 * Compass modal, which explained which pane to use when; that is this page's map,
 * drawn from the same list as the rail (`tools.ts`), with the research stages that
 * live outside this window kept underneath.
 */
import { useSyncExternalStore, type ReactNode } from 'react';

import { NAMED_GLYPHS } from '../../glyphs';
import { revealSection } from '../../layout/controller';
import { useModelLocus } from '../../model-locus';
import { registry } from '../../registry';
import { RollingCounter } from '../../viz/RollingCounter';
import { RESEARCH_PANE_GUIDE } from '../interpretability/ResearchGuide';
import { interpretabilityStore } from '../interpretability/store';
import { SUITE_VIEW } from './ids';
import { S } from './styles';
import { locusLabel } from './SuiteFrame';
import { SUITE_TOOLS } from './tools';

function Tile({ label, children, index }: { label: string; children: ReactNode; index: number }) {
  return (
    <div style={{ ...S.tile, ...S.stagger(index) }}>
      <div style={S.heading}>{label}</div>
      <div style={S.tileValue}>{children}</div>
    </div>
  );
}

export function HomeSection() {
  const arch = useSyncExternalStore(
    interpretabilityStore.subscribe,
    interpretabilityStore.getArchitecture,
  );
  const turns = useSyncExternalStore(
    interpretabilityStore.subscribe,
    interpretabilityStore.getSnapshot,
  );
  const locus = useModelLocus();

  const groups = ['Prompt', 'Weights', 'Activations'] as const;
  // Research stages served by this window are on the map above; the rest point out.
  const elsewhere = RESEARCH_PANE_GUIDE.filter((stage) => stage.paneId !== SUITE_VIEW);

  return (
    <div style={S.home}>
      <h2 style={S.homeTitle}>Interpretability</h2>
      <p style={S.lede}>
        One model, read at three depths: the prompt it was handed, the weights it is made of, and
        the activations of a forward pass. Every tool here shares one model and one locus — pick a
        layer in one and the others follow.
      </p>

      <div style={S.tiles}>
        <Tile label="Model" index={0}>
          {arch ? `${arch.model}${arch.family ? ` · ${arch.family}` : ''}` : 'no model resolved'}
        </Tile>
        <Tile label="Blocks" index={1}>
          {arch?.layers != null ? <RollingCounter value={arch.layers} /> : '—'}
        </Tile>
        <Tile label="Captured turns" index={2}>
          <RollingCounter value={turns.length} />
        </Tile>
        <Tile label="Locus" index={3}>
          {locusLabel(locus) ?? 'not set'}
        </Tile>
      </div>

      {groups.map((group) => (
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
                  onClick={() => revealSection(tool.section, SUITE_VIEW)}
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

      <h3 style={S.groupHead}>Beyond this window</h3>
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
