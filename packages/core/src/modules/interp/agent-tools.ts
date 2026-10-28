/**
 * The interpretability suite's agent tools — the `interp` group.
 *
 * Declared on the suite pane, so they are in the manifest whether or not the pane
 * is open, and progressively disclosed: they cost nothing until a turn loads the
 * group (the `interp` roster agent preloads it). Nine verbs; all but `interp.steer` read-only:
 *
 * - `interp.state` — the full digest of what the suite is looking at.
 * - `interp.attention` — the per-layer attention layout, for a range of layers.
 * - `interp.heads` — a trace's attention heads, scored and ranked.
 * - `interp.features` — a trace's SAE features, with the reconstruction check and labels.
 * - `interp.graph` — the open attribution graph's strongest paths into its target logit.
 * - `interp.lenses` — one position read through every lens for the model (logit lens
 *   and fitted Jacobian / J++), and a token's rank per layer under each.
 * - `interp.steer` — push a feature into the model and compare generations (runs the
 *   model and records two traces, so it is a side effect the permission engine sees).
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
import {
  encode as encodeFeatures,
  featuresStore,
  labelOf,
  loadCard,
  openTrace as openFeatureTrace,
  refreshFeatureTraces,
} from './features/state';
import { getGraphSummary } from './graph/api';
import { graphStore, refreshGraphs } from './graph/state';
import {
  lensCompareStore,
  openCompareTrace,
  readAt,
  refreshCompareTraces,
  topByLayer,
  track as trackLens,
} from './lenses/state';
import { libraryStore, refreshLibrary } from './library/state';
import { searchVocab } from '../llamacpp/api';
import { run as runSteer, setDraft as setSteerDraft, steerStore } from './steer/state';
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
    name: 'interp.features',
    description:
      'What a Gemma Scope–style SAE reads in a llama.cpp trace: the reconstruction ' +
      'self-check first (verified / drifted / not_checked, with FVU, cosine and L0 against ' +
      'the L0 the SAE was trained to), then the strongest features in the trace and at a ' +
      'position, each with its Neuronpedia label when there is one. A feature flagged ' +
      'firstToken peaks on the token after BOS, where Neuronpedia labels were not drawn. ' +
      'Report the check before any feature; a drifted check means the readings are suspect.',
    params: {
      type: 'object',
      properties: {
        traceId: { type: 'string', description: 'Trace to read (default: the one shown).' },
        position: {
          type: 'integer',
          description: 'Also list the features at this token position.',
        },
        top: { type: 'integer', description: 'How many trace-wide features (default 10).' },
      },
    },
    handler: async (args) => {
      if (typeof args.traceId === 'string' && args.traceId) await openFeatureTrace(args.traceId);
      else if (!featuresStore.get().result) await refreshFeatureTraces();
      let r = featuresStore.get().result;
      if (!r && featuresStore.get().traceId) r = await encodeFeatures();
      const st = featuresStore.get();
      if (!r) return { error: st.error ?? 'No trace has an installed SAE that fits it.' };
      const top = Math.max(1, Math.min(30, Number(args.top ?? 10) || 10));
      const picked = r.top.slice(0, top);
      await Promise.all(picked.map((f) => loadCard(f.index)));
      const cards = featuresStore.get().cards;
      const tok = (p: number) => r.tokens.find((t) => t.position === p)?.text ?? `#${p}`;
      const describe = (index: number, act: number) => {
        const label = labelOf(cards, index);
        return `F${index}${label ? ` "${label}"` : ''} ${act.toFixed(1)}`;
      };
      const at =
        typeof args.position === 'number'
          ? r.perToken.find((p) => p.position === args.position)
          : undefined;
      return {
        trace: r.traceId,
        model: r.model,
        sae: `${r.artifactId} (${r.arch}, layer ${r.layer}, width ${r.width})`,
        check: r.check,
        fit: r.compat,
        labels: r.neuronpedia
          ? `Neuronpedia ${r.neuronpedia.model}/${r.neuronpedia.source}`
          : 'none: Neuronpedia hosts no pages for this dictionary',
        strongest: picked.map(
          (f) =>
            `${describe(f.index, f.peak)} at "${tok(f.position)}" (#${f.position}, ${f.tokens} tokens)` +
            (f.firstToken ? ' · firstToken' : ''),
        ),
        atPosition: at
          ? {
              position: at.position,
              token: tok(at.position),
              active: at.active,
              features: at.top.map((f) => describe(f.index, f.act)),
            }
          : undefined,
      };
    },
  },
  {
    name: 'interp.graph',
    description:
      'The attribution graph open in the Graph section, as words: the target logit, the ' +
      "strongest excitatory paths into it from the prompt's embeddings through transcoder " +
      "features (each with its label when it has one), and the reader's supernodes. " +
      'pinnedOnly walks only the pinned subgraph. A path is the greedy strongest chain, ' +
      'not the whole circuit — say so, and name the weakest link.',
    params: {
      type: 'object',
      properties: {
        pinnedOnly: { type: 'boolean', description: 'Walk only pinned nodes (default false).' },
      },
    },
    handler: async (args) => {
      if (!graphStore.get().id) await refreshGraphs();
      const id = graphStore.get().id;
      if (!id)
        return {
          error: 'No attribution graph on this node yet — import one in the Graph section.',
        };
      const summary = await getGraphSummary(id, Boolean(args.pinnedOnly));
      return { graph: id, pinned: graphStore.get().pinned.length, ...summary };
    },
  },
  {
    name: 'interp.lenses',
    description:
      'Read one position of a trace through every lens installed for its model, layer by ' +
      'layer: the logit lens (what the model would say if it stopped there) and fitted ' +
      'Jacobian / J++ lenses (what the layer holds, transported to the final layer). Give ' +
      '`token` to get its rank at every layer under each lens — e.g. a two-hop bridge ' +
      'concept that the Jacobian lens surfaces many layers before the logit lens. A fitted ' +
      "lens's grid is never self-verified (only the logit lens can be); say so.",
    params: {
      type: 'object',
      properties: {
        traceId: { type: 'string', description: 'Trace to read (default: the one shown).' },
        position: { type: 'integer', description: 'Prompt position (default: the last).' },
        token: {
          type: 'string',
          description: 'A word to track across layers, e.g. " Japan" (leading space matters).',
        },
      },
    },
    handler: async (args) => {
      if (typeof args.traceId === 'string' && args.traceId) await openCompareTrace(args.traceId);
      else if (!lensCompareStore.get().traceId) await refreshCompareTraces();
      if (typeof args.position === 'number') await readAt(args.position);
      let st = lensCompareStore.get();
      if (!st.traceId) return { error: 'No trace on this node yet.' };
      if (typeof args.token === 'string' && args.token) {
        const trace = st.traces.find((t) => t.traceId === st.traceId);
        const found = trace ? await searchVocab(trace.modelPath, args.token.trim(), 20) : null;
        const want = args.token;
        const entry =
          found?.tokens.find((e) => e.text === want) ??
          found?.tokens.find((e) => e.text.trim() === want.trim());
        if (!entry) return { error: `"${want}" is not one token in this model's vocabulary.` };
        await trackLens(entry.id, entry.text);
        st = lensCompareStore.get();
      }
      return {
        trace: st.traceId,
        position: st.position,
        lenses: st.lenses.map((l) => ({
          id: l.id,
          label: l.label,
          kind: l.kind,
          verified: st.grids[l.id]?.verified,
          topByLayer: st.grids[l.id] ? topByLayer(st.grids[l.id]) : null,
          trackedRanks: st.track?.ranks[l.id] ?? null,
        })),
        tracked: st.track ? st.track.text : null,
      };
    },
  },
  {
    name: 'interp.steer',
    description:
      "Steer the traced GGUF with an SAE feature: add its direction at the SAE's layer (a " +
      'llama.cpp control vector) and generate greedily with and without it. strength is in ' +
      "units of the feature's own peak on the prompt: 1 is as strong as it fires; coherent " +
      'text survives about -1.5..1.5, and negative suppresses. Returns whether the feature ' +
      'moved the way it was pushed (report this first), how the first prediction shifted ' +
      '(gained and lost tokens, KL) and both texts. Runs the model and records two traces. ' +
      'Defaults to the feature, SAE and trace selected in Features.',
    sideEffect: true,
    specifierTemplate: 'F{feature} x{strength}',
    params: {
      type: 'object',
      properties: {
        feature: { type: 'integer', description: 'Feature index (default: the selected one).' },
        strength: { type: 'number', description: "Multiple of the feature's peak (default 1)." },
        prompt: { type: 'string', description: "Prompt to steer (default: the trace's)." },
        traceId: { type: 'string', description: 'Trace whose model to use (default: Features).' },
        maxTokens: { type: 'integer', description: 'Tokens to generate (default 32).' },
      },
    },
    handler: async (args) => {
      const f = featuresStore.get();
      const r = f.result;
      const draft = steerStore.get().draft;
      const feature =
        typeof args.feature === 'number' ? args.feature : (f.feature ?? draft.feature);
      const traceId =
        typeof args.traceId === 'string' && args.traceId
          ? args.traceId
          : draft.traceId || r?.traceId || '';
      const artifactId = draft.artifactId || r?.artifactId || '';
      if (feature === null || !traceId || !artifactId) {
        return { error: 'Nothing to steer: open a trace with an SAE in Features first.' };
      }
      setSteerDraft({
        feature,
        traceId,
        artifactId,
        strength: typeof args.strength === 'number' ? args.strength : 1,
        prompt:
          typeof args.prompt === 'string' && args.prompt
            ? args.prompt
            : draft.prompt || (f.traces.find((t) => t.traceId === traceId)?.prompt ?? ''),
        maxTokens: typeof args.maxTokens === 'number' ? args.maxTokens : draft.maxTokens,
        label: labelOf(f.cards, feature) ?? '',
      });
      const out = await runSteer();
      if (!out) return { error: steerStore.get().error ?? 'The run did not finish.' };
      const nt = out.nextToken;
      return {
        feature: out.feature,
        layer: out.layer,
        strength: out.strength,
        scale: out.scale,
        arrived: out.arrived,
        firstPrediction: nt
          ? {
              default: `${nt.defaultTop.token} ${(nt.defaultTop.default * 100).toFixed(1)}%`,
              steered: `${nt.steeredTop.token} ${(nt.steeredTop.steered * 100).toFixed(1)}%`,
              kl: nt.kl,
              gained: nt.gained.slice(0, 5).map((m) => m.token),
              lost: nt.lost.slice(0, 5).map((m) => m.token),
            }
          : null,
        defaultText: out.default.text,
        steeredText: out.steered.text,
        traces: { default: out.defaultTraceId, steered: out.steeredTraceId },
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
          fits: a.compat.map(
            (c) =>
              `${c.target} (${c.source}): ${c.state}` +
              (c.reasons.length ? ` — ${c.reasons.join('; ')}` : ''),
          ),
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
