/**
 * The interpretability suite's agent tools — the `interp` group.
 *
 * Declared on the suite pane, so they are in the manifest whether or not the pane
 * is open, and progressively disclosed: they cost nothing until a turn loads the
 * group (the `interp` roster agent preloads it). Five verbs, all read-only:
 *
 * - `interp.state` — the full digest of what the suite is looking at.
 * - `interp.attention` — the per-layer attention layout, for a range of layers.
 * - `interp.heads` — a trace's attention heads, scored and ranked.
 * - `interp.library` — installed artifacts and what they fit, the catalog, the worker.
 * - `interp.goto` — show a section, and move the shared model locus.
 *
 * `interp.library` reads and never downloads: a download is consent to a size, and
 * the user gives it in the Library section, not the agent on their behalf.
 *
 * Their usage notes and the domain primer live in the group's guide
 * (`backend/modules/agent/guides/interp.md`), delivered once the group is loaded.
 */
import type { AgentToolDecl } from '@horribledashboard/sdk';

import { revealSection } from '../../layout/controller';
import { setModelLocus } from '../../model-locus';
import { interpretabilityStore } from '../interpretability/store';
import { sameFile } from './attention/model';
import {
  headsStore,
  measurable,
  openTrace,
  rankHeads,
  refreshTraces,
  type HeadMetric,
} from './heads/state';
import { SUITE_SECTIONS, SUITE_VIEW, type SuiteSection } from './ids';
import { libraryStore, refreshLibrary } from './library/state';
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
    name: 'interp.heads',
    description:
      'The attention heads of a traced pass, scored and ranked: induction (copies the ' +
      'token that followed an earlier copy), previous-token, duplicate, sink, self and ' +
      'entropy, with each head’s layer kind when the trace is of the loaded model. ' +
      'Induction and duplicate are null when the trace repeats nothing — say so, and ' +
      'suggest the induction probe in the Heads section; do not read null as zero.',
    params: {
      type: 'object',
      properties: {
        traceId: { type: 'string', description: 'Trace to score (default: the one shown).' },
        metric: {
          type: 'string',
          enum: ['induction', 'prevToken', 'duplicate', 'sink', 'selfWeight', 'entropy'],
          description: 'Rank by this score (default induction, or prevToken if unmeasurable).',
        },
        top: { type: 'integer', description: 'How many heads to return (default 10).' },
      },
    },
    handler: async (args) => {
      if (typeof args.traceId === 'string' && args.traceId) await openTrace(args.traceId);
      else if (!headsStore.get().table) await refreshTraces();
      const table = headsStore.get().table;
      if (!table) return { error: 'No trace with attention on this node yet.' };
      if (table.error) return { error: table.error };
      const wanted = (typeof args.metric === 'string' ? args.metric : '') as HeadMetric;
      const metric: HeadMetric =
        wanted && measurable(table, wanted)
          ? wanted
          : measurable(table, 'induction')
            ? 'induction'
            : 'prevToken';
      await primeDigest();
      const layout = interpretabilityStore.getAttention();
      const kinds = layout && sameFile(layout.path, table.modelPath) ? layout.layers : null;
      const top = Math.max(1, Math.min(40, Number(args.top ?? 10) || 10));
      return {
        trace: table.traceId,
        model: table.model,
        prompt: table.prompt,
        tokens: table.tokens,
        repeatedPositions: table.repeatedPositions,
        inductionMeasurable: measurable(table, 'induction'),
        rankedBy: metric,
        layerKinds: kinds
          ? 'from the attention layout (same file)'
          : 'unknown: trace is of another file',
        heads: rankHeads(table.heads ?? [], metric, top).map(
          (h) =>
            `L${h.layer} H${h.head}: ${metric} ${(h[metric] ?? 0).toFixed(3)}` +
            ` · prev ${h.prevToken.toFixed(2)} · sink ${h.sink.toFixed(2)}` +
            (kinds?.[h.layer] ? ` · ${kinds[h.layer].kind}` : '') +
            (h.rows < 2 ? ' · one row only' : ''),
        ),
      };
    },
  },
  {
    name: 'interp.library',
    description:
      'The interpretability library: installed SAEs, transcoders and lenses, each with what ' +
      'it states about itself (layer, input width, dictionary width, and where each fact was ' +
      'read) and whether it fits the agent’s GGUF and the worker’s model — compatible, ' +
      'incompatible (with the contradiction) or unknown; the disk budget; the catalog of ' +
      'what can be fetched; and the GPU worker (environment, state, held model, VRAM). ' +
      'Read-only: downloads are confirmed by the user in the Library section.',
    handler: async () => {
      await refreshLibrary();
      const { library, worker, catalog } = libraryStore.get();
      return {
        artifacts: (library?.artifacts ?? []).map((a) => ({
          id: a.id,
          kind: a.kind,
          trainedOn: a.modelRepo,
          facts: a.facts,
          fits: a.compat.map((c) => `${c.target} (${c.source}): ${c.state}` +
            (c.reasons.length ? ` — ${c.reasons.join('; ')}` : '')),
        })),
        checkedAgainst: library?.targets ?? [],
        disk: library ? { usedBytes: library.usedBytes, budgetBytes: library.budgetBytes } : null,
        catalog: catalog.map((e) => `${e.id}: ${e.title} (${e.repo})`),
        worker: worker
          ? {
              state: worker.state,
              envGroups: worker.env.groups,
              envInstall: worker.env.state,
              holding: worker.status?.loaded ?? null,
              vramAllocatedBytes: worker.status?.vramAllocated ?? null,
              device: worker.ping?.device ?? null,
            }
          : null,
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
