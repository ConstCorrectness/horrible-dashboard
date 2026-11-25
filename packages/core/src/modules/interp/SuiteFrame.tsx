/**
 * Per-tool frame inside the Interpretability IDE.
 *
 * Global model/runtime/locus state now lives once in `SubjectBar` above the stage
 * so side-by-side splits never duplicate it. Each tool's own header carries:
 * - Its title and short question,
 * - An inline prerequisite readiness pill + 1-click setup action when a required
 *   input (agent turn, GGUF, trace, WebML GGUF, or SAE) isn't ready yet,
 * - A collapsible `? How to use` banner so every visualization explains itself in
 *   plain English right where you use it.
 */
import { useContext, useState, useSyncExternalStore, type ReactNode } from 'react';

import { SectionInstanceContext, useAgentContext } from '../../agent-context';
import { IconComment } from '../../glyphs';
import { useModelLocus } from '../../model-locus';
import { interpretabilityStore } from '../interpretability/store';
import { useEngineState } from '../webml/engine';
import { playgroundSource } from '../webml/playground-state';
import { askInterp } from './ask';
import { libraryStore } from './library/state';
import { SUITE_SECTIONS, type SuiteSection } from './ids';
import { openSuiteTab } from './stage';
import { S } from './styles';
import { suiteDigest } from './suite-state';
import { toolForSection, type SuitePrerequisite } from './tools';

const GUIDE_KEY = 'interp.guideOpen:';

function readGuideOpen(key: string): boolean {
  try {
    return localStorage.getItem(GUIDE_KEY + key) === '1';
  } catch {
    return false;
  }
}

function writeGuideOpen(key: string, open: boolean): void {
  try {
    localStorage.setItem(GUIDE_KEY + key, open ? '1' : '0');
  } catch {
    /* blocked storage */
  }
}

/**
 * Publish the suite digest as this section's agent context. Every section does,
 * so whichever one is focused answers "what am I looking at?" with the full
 * IDE state plus its own view name.
 */
export function useSuiteAgentContext(view: string): void {
  useAgentContext(() => ({ view, ...suiteDigest() }));
}

function PrerequisiteHint({ needs }: { needs: SuitePrerequisite }) {
  const arch = useSyncExternalStore(
    interpretabilityStore.subscribe,
    interpretabilityStore.getArchitecture,
  );
  const turns = useSyncExternalStore(
    interpretabilityStore.subscribe,
    interpretabilityStore.getSnapshot,
  );
  const webml = useEngineState();
  const locus = useModelLocus();
  const lib = useSyncExternalStore(libraryStore.subscribe, libraryStore.get);

  if (needs === 'none') return null;

  if (needs === 'agent-turn' && turns.length === 0) {
    return (
      <button
        type="button"
        className="interp-mini-action"
        style={hintStyle}
        onClick={() => openSuiteTab(SUITE_SECTIONS.runs)}
        title="No live agent turns in memory yet — browse stored runs"
      >
        Needs: Agent turn → Browse Runs
      </button>
    );
  }
  if (needs === 'gguf' && (!arch || arch.error)) {
    return (
      <button
        type="button"
        className="interp-mini-action"
        style={hintStyle}
        onClick={() => openSuiteTab(SUITE_SECTIONS.runtime)}
        title="Serve or configure a local GGUF model"
      >
        Needs: Local GGUF → Open Models & llama.cpp
      </button>
    );
  }
  if (needs === 'trace' && !locus.traceId) {
    return (
      <button
        type="button"
        className="interp-mini-action"
        style={hintStyle}
        onClick={() => openSuiteTab(SUITE_SECTIONS.traces)}
        title="Record or pick a forward-pass snapshot in Traces"
      >
        Tip: Record a trace in Traces
      </button>
    );
  }
  if (needs === 'webml-gguf' && !(webml.kind === 'ready' && webml.engine === 'gguf')) {
    return (
      <button
        type="button"
        className="interp-mini-action"
        style={hintStyle}
        onClick={() => {
          playgroundSource.requestGguf();
          openSuiteTab(SUITE_SECTIONS.playground, { splitWith: SUITE_SECTIONS.neurons });
        }}
        title="Open Playground side-by-side to load a GGUF on WebGPU"
      >
        Needs: WebGPU GGUF → Pair Playground
      </button>
    );
  }
  if (needs === 'sae' && !lib.library?.artifacts.some((a) => a.kind === 'sae')) {
    return (
      <button
        type="button"
        className="interp-mini-action"
        style={hintStyle}
        onClick={() => openSuiteTab(SUITE_SECTIONS.library)}
        title="Download a compatible Gemma Scope SAE in Library"
      >
        Needs: Installed SAE → Open Library
      </button>
    );
  }
  return null;
}

const hintStyle = {
  display: 'inline-flex',
  alignItems: 'center',
  height: 20,
  padding: '0 7px',
  border: '1px solid color-mix(in srgb, var(--warning) 45%, var(--border))',
  borderRadius: 3,
  background: 'color-mix(in srgb, var(--warning) 10%, transparent)',
  color: 'var(--text-primary)',
  fontFamily: 'var(--font-mono)',
  fontSize: 10.5,
  cursor: 'pointer',
  flex: 'none' as const,
};

export function SuiteFrame({ title, children }: { title: string; children: ReactNode }) {
  const sectionId = useContext(SectionInstanceContext) as SuiteSection | null;
  const tool = toolForSection(sectionId ?? undefined);
  const key = sectionId ?? title;
  const [guideOpen, setGuideOpen] = useState(() => readGuideOpen(key));
  useSuiteAgentContext(title);

  const toggleGuide = () => {
    setGuideOpen((prev) => {
      writeGuideOpen(key, !prev);
      return !prev;
    });
  };

  return (
    <div style={S.frame} role="region" aria-label={title}>
      <div style={S.strip}>
        <span style={{ ...S.heading, color: 'var(--text-primary)', fontSize: 10.5 }}>
          {tool?.label ?? title}
        </span>
        {tool ? (
          <span
            style={{
              ...S.stripFact,
              color: 'var(--text-secondary)',
              fontFamily: 'inherit',
              fontSize: 11.5,
            }}
            title={tool.question}
          >
            {tool.question}
          </span>
        ) : null}
        <span style={{ flex: 1 }} />
        {tool ? <PrerequisiteHint needs={tool.needs} /> : null}
        {tool ? (
          <button
            type="button"
            className="interp-mini-action"
            aria-expanded={guideOpen}
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              height: 20,
              padding: '0 7px',
              border: '1px solid var(--border)',
              borderRadius: 3,
              background: guideOpen
                ? 'color-mix(in srgb, var(--accent) 14%, transparent)'
                : 'var(--bg-secondary)',
              color: guideOpen ? 'var(--text-primary)' : 'var(--text-secondary)',
              fontSize: 10.5,
              fontWeight: 600,
              cursor: 'pointer',
              flex: 'none',
            }}
            onClick={toggleGuide}
          >
            ? How to use
          </button>
        ) : null}
        <button
          type="button"
          className="interp-ask"
          style={{ ...S.askButton, marginLeft: 0 }}
          title="Ask the interpretability agent about this view"
          onClick={() => askInterp(title)}
        >
          <IconComment />
          Ask
        </button>
      </div>

      {tool && guideOpen ? (
        <div
          style={{
            display: 'grid',
            gap: 4,
            padding: '8px 12px',
            borderBottom: '1px solid var(--border)',
            borderLeft: '2px solid var(--accent)',
            background: 'var(--bg-secondary)',
            fontSize: 12,
            lineHeight: 1.45,
            flex: 'none',
          }}
        >
          <div style={{ color: 'var(--text-primary)', fontWeight: 600 }}>
            How to use: <span style={{ fontWeight: 400 }}>{tool.howToUse}</span>
          </div>
          <div style={{ color: 'var(--text-secondary)' }}>When to reach for it: {tool.when}</div>
        </div>
      ) : null}

      <div style={S.body}>{children}</div>
    </div>
  );
}
