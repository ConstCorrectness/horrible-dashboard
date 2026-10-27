/**
 * The interpretability suite's agent tools — the `interp` group.
 *
 * Declared on the suite pane, so they are in the manifest whether or not the pane
 * is open, and progressively disclosed: they cost nothing until a turn loads the
 * group (the `interp` roster agent preloads it). Three verbs, all read-only:
 *
 * - `interp.state` — the full digest of what the suite is looking at.
 * - `interp.attention` — the per-layer attention layout, for a range of layers.
 * - `interp.goto` — show a section, and move the shared model locus.
 *
 * Their usage notes and the domain primer live in the group's guide
 * (`backend/modules/agent/guides/interp.md`), delivered once the group is loaded.
 */
import type { AgentToolDecl } from '@horribledashboard/sdk';

import { revealSection } from '../../layout/controller';
import { setModelLocus } from '../../model-locus';
import { interpretabilityStore } from '../interpretability/store';
import { SUITE_SECTIONS, SUITE_VIEW, type SuiteSection } from './ids';
import { setSubSection } from './SubSections';
import { describeLayer, primeDigest, suiteDigest } from './suite-state';

const SECTIONS = Object.values(SUITE_SECTIONS);

/** Rows to return when a range is not given — a 60-layer model fits whole. */
const DEFAULT_ROWS = 64;

export const interpAgentTools: AgentToolDecl[] = [
  {
    name: 'interp.state',
    description:
      'What the interpretability suite is looking at now: the loaded model and where its ' +
      'description came from, its per-layer attention pattern and KV cache, the shared ' +
      'locus (layer / position / trace) every tool follows and what that layer is, the ' +
      'latest captured agent turn, the in-window lens run, and caveats about how each ' +
      'fact is known. Read this before explaining anything the user is looking at.',
    handler: async () => {
      await primeDigest();
      return { sections: SECTIONS, ...suiteDigest('full') };
    },
  },
  {
    name: 'interp.attention',
    description:
      "The loaded model's per-layer attention layout, from its GGUF header: each block's " +
      'mixer (global, sliding, chunked, linear, ssm, conv, mla), window, heads and head ' +
      'widths, KV sharing, K=V, gated output, and how each fact is known (metadata, ' +
      'family rule, tensors). Plus the repeating pattern, the KV cache by context, and ' +
      'any conflicts between the sources.',
    params: {
      type: 'object',
      properties: {
        from: { type: 'integer', description: 'First layer (default 0).' },
        to: { type: 'integer', description: 'Last layer, inclusive.' },
      },
    },
    handler: async (args) => {
      await primeDigest();
      const layout = interpretabilityStore.getAttention();
      if (!layout) return { error: 'No attention layout yet — the backend did not answer.' };
      if (layout.error) return { error: layout.error };
      const from = Math.max(0, Number(args.from ?? 0) || 0);
      const to = Math.min(
        layout.layers.length - 1,
        args.to === undefined ? from + DEFAULT_ROWS - 1 : Number(args.to),
      );
      return {
        file: layout.path,
        family: layout.family,
        pattern: layout.pattern,
        counts: layout.counts,
        layers: layout.layers.slice(from, to + 1).map(describeLayer),
        truncated: to < layout.layers.length - 1 ? `layers ${to + 1}+ not shown` : undefined,
        kvCache: layout.kvCache.map((p) => ({
          context: p.context,
          bytes: p.bytes,
          bytesIfAllGlobal: p.bytesIfGlobal,
        })),
        conflicts: layout.conflicts,
        notes: layout.notes,
      };
    },
  },
  {
    name: 'interp.goto',
    description:
      'Show the user something in the interpretability suite: open a section and, ' +
      'optionally, point the shared locus at a layer and prompt position so the model ' +
      'explorer, the stepper and the lens all follow. Use it to show what you are ' +
      'explaining.',
    params: {
      type: 'object',
      properties: {
        section: { type: 'string', enum: SECTIONS, description: 'The rail section to show.' },
        tab: {
          type: 'string',
          description:
            "A tab inside the section: runs/harness/forks/duels for 'runs'; " +
            "trace/reply/guess/compare for 'lens'.",
        },
        layer: { type: 'integer', description: 'Block to select (-1 = the embedding).' },
        position: { type: 'integer', description: 'Prompt position to select.' },
      },
      required: ['section'],
    },
    handler: (args) => {
      const section = String(args.section) as SuiteSection;
      if (!SECTIONS.includes(section)) {
        return { ok: false, error: `Unknown section '${section}'. One of: ${SECTIONS.join(', ')}` };
      }
      if (typeof args.tab === 'string' && args.tab) setSubSection(section, args.tab);
      const instanceId = revealSection(section, SUITE_VIEW);
      const locus: { layer?: number; position?: number } = {};
      if (typeof args.layer === 'number') locus.layer = args.layer;
      if (typeof args.position === 'number') locus.position = args.position;
      if (Object.keys(locus).length) setModelLocus(locus, 'agent');
      return { ok: instanceId !== null, section, ...locus };
    },
  },
];
