import { useState, useCallback } from 'react';
import { registry } from '../../registry';
import { revealSection } from '../../layout/controller';

export interface ResearchStage {
  id: string;
  title: string;
  icon: string;
  category: string;
  paneId: string;
  sectionId?: string;
  altPaneId?: string;
  altSectionId?: string;
  altLabel?: string;
  question: string;
  when: string;
  tip: string;
}

export const RESEARCH_PANE_GUIDE: ResearchStage[] = [
  {
    id: 'papers',
    title: 'Literature & Papers',
    icon: '🔬',
    category: 'Literature',
    paneId: 'research.console',
    altPaneId: 'research.arxiv',
    altLabel: 'arXiv Search',
    question: 'What is the state of the art or underlying technique?',
    when: 'When researching new architectures, reading arXiv preprints, or collecting citations.',
    tip: 'Save captured HTML pages or PDFs directly to your local library to search them with vector embeddings.',
  },
  {
    id: 'datasets',
    title: 'Datasets & Curation',
    icon: '🗃️',
    category: 'Data',
    paneId: 'datasets.browser',
    altPaneId: 'lab.hub',
    altLabel: 'Hugging Face Hub',
    question: 'What data is available, and what is its split and token length distribution?',
    when: 'When selecting benchmark sets, inspecting fine-tuning material, or verifying column schemas.',
    tip: 'Always verify token length distribution before training; outliers can trigger silent context truncations.',
  },
  {
    id: 'context',
    title: 'Prompt & Context Window',
    icon: '🔍',
    category: 'Interpretability (Prompt)',
    paneId: 'interpretability.context',
    question: 'What was the model actually fed before generating an answer?',
    when: 'When debugging prompt bloat, unexplained tool failures, or missing instructions.',
    tip: 'Tool schemas often consume 60%+ of the context budget. Look out for the red "tools dropped" warning.',
  },
  {
    id: 'architecture',
    title: 'Weights & Architecture',
    icon: '🧬',
    category: 'Interpretability (Weights)',
    paneId: 'interpretability.architecture',
    question: 'How is the neural network structured, sized, and quantized?',
    when: 'When inspecting attention heads (MHA/GQA), calculating KV cache VRAM footprint, or ablating layers.',
    tip: 'GQA (Grouped-Query Attention) shares KV heads across query heads, drastically lowering VRAM needs during generation.',
  },
  {
    id: 'activations',
    title: 'Activations & Logit Lens',
    icon: '🔭',
    category: 'Interpretability (Activations)',
    paneId: 'llamacpp.server',
    sectionId: 'lens',
    altSectionId: 'stepper',
    altLabel: 'Layer Stepper',
    question: 'What representations form across layers during a forward pass?',
    when: 'When analyzing at which layer a model figures out the answer, or stepping through layer execution.',
    tip: 'The Logit Lens decodes intermediate hidden states into vocabulary tokens to observe thought progression.',
  },
  {
    id: 'evals',
    title: 'Evals & Benchmarks',
    icon: '🎯',
    category: 'Evaluation',
    paneId: 'evals.hub',
    question: 'Does the model perform accurately and reliably on standardized tasks?',
    when: 'Before and after fine-tuning or modifying prompts to check for regressions and measure tool use.',
    tip: 'Run smaller canary suites first. Compare run-to-run diffs in the Compare tab rather than reading raw scores.',
  },
  {
    id: 'training',
    title: 'Training & Tracking',
    icon: '🧠',
    category: 'Training',
    paneId: 'training.notebook',
    altPaneId: 'localtrack.workspace',
    altLabel: 'LocalTrack',
    question: 'How does fine-tuning converge, and which hyperparameter was best?',
    when: 'When executing training recipes, hyperparameter sweeps, and monitoring loss curves.',
    tip: 'Watch for loss spikes and validation diverges early to abort unpromising sweep runs.',
  },
];

export const GLOSSARY_ENTRIES: Record<
  string,
  { title: string; explanation: string; tip?: string }
> = {
  gqa: {
    title: 'Grouped-Query Attention (GQA)',
    explanation:
      'Several query heads share a single Key/Value head. Compared to Multi-Head Attention (MHA), GQA drastically reduces memory bandwidth and KV Cache footprint with virtually zero quality loss.',
    tip: 'Look at the "Group ratio": an 8:1 ratio means 8 query heads share 1 KV head, cutting KV cache size by 8×.',
  },
  mha: {
    title: 'Multi-Head Attention (MHA)',
    explanation:
      'Every query head has its own Key and Value head (1:1 ratio). Highest KV cache memory consumption during autoregressive generation.',
  },
  mqa: {
    title: 'Multi-Query Attention (MQA)',
    explanation:
      'All query heads share a single Key and Value head across the entire layer. Maximum memory savings, but can degrade nuanced multi-concept recall.',
  },
  kv_cache: {
    title: 'KV Cache Footprint',
    explanation:
      'Past Key and Value states retained in GPU VRAM so previous tokens do not need to be recomputed on every new generated token. Scales as: 2 × layers × kv_heads × head_dim × context_length × precision_bytes.',
    tip: 'At long context lengths (e.g. 32k or 128k), KV cache can exceed the weight size of the model itself.',
  },
  swiglu: {
    title: 'SwiGLU Gated Feed-Forward Network',
    explanation:
      'Modern LLM feed-forward layers use SwiGLU gating: two parallel up-projections (gate and up) multiplied before down-projection, replacing traditional single-matrix ReLU/GELU activations.',
    tip: 'Gated FFNs require 3 weight matrices per block instead of 2, adding parameter capacity without increasing layer depth.',
  },
  moe: {
    title: 'Mixture of Experts (MoE)',
    explanation:
      'Replaces the single dense FFN with multiple specialized "expert" sub-networks. A router routes each token to a subset of experts (e.g. top 2 of 8).',
    tip: 'Total parameters determine disk/VRAM size, while active parameters determine compute FLOPs and generation speed.',
  },
  rope: {
    title: 'RoPE Base Frequency (θ)',
    explanation:
      'Rotary Position Embedding frequency base (theta). Models designed for extended context (e.g., 32k, 128k) scale this base frequency (e.g., from 10,000 up to 500,000 or 1,000,000) to distinguish distant tokens.',
  },
  quants: {
    title: 'GGUF Quantization Formats',
    explanation:
      'Lower-precision representations of weight tensors. Standard formats: Q4_K_M (good quality/VRAM balance for local execution), Q8_0 (near-lossless 8-bit), and BF16/FP16 (full unquantized weights).',
    tip: 'K-quants (like Q4_K_M) use mixed precision, keeping critical attention and norm tensors at higher bit-depths.',
  },
  tool_budget: {
    title: 'Agent Tool Budget & Truncation',
    explanation:
      'The agent orchestrator selects tools relevant to the active context up to a safety budget (TOOL_BUDGET = 44). Excess tools are dropped to avoid overwhelming the model context window.',
    tip: 'If the agent "forgets" a tool, inspect the Context Window pane to see if it was dropped due to tool budget.',
  },
  context_window: {
    title: 'Context Length: Requested vs Real',
    explanation:
      'num_ctx is the context length requested by the application. The model has a fixed physical architecture limit (trained context length). Requesting more than the model supports will clamp to the model limit.',
  },
};

/**
 * Glossary helper badge rendering a compact (?) icon with an informative tooltip.
 */
export function GlossaryBadge({
  topic,
  label,
}: {
  topic: keyof typeof GLOSSARY_ENTRIES;
  label?: string;
}) {
  const [open, setOpen] = useState(false);
  const entry = GLOSSARY_ENTRIES[topic];
  if (!entry) return null;

  return (
    <span className="interp-glossary-wrap">
      {label && <span className="interp-glossary-label">{label}</span>}
      <button
        type="button"
        className="interp-glossary-badge"
        onClick={() => setOpen((prev) => !prev)}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        title={entry.explanation}
        aria-label={`Explain ${entry.title}`}
      >
        ?
      </button>
      {open && (
        <span className="interp-glossary-tooltip" role="tooltip">
          <span className="interp-glossary-title">{entry.title}</span>
          <span className="interp-glossary-desc">{entry.explanation}</span>
          {entry.tip && <span className="interp-glossary-tip">💡 {entry.tip}</span>}
        </span>
      )}
    </span>
  );
}

/**
 * Modal dialog explaining the AI research lifecycle and which pane to use when.
 */
export function ResearchCompassModal({ onClose }: { onClose: () => void }) {
  const openPane = useCallback((paneId: string, sectionId?: string) => {
    if (sectionId) {
      revealSection(sectionId, paneId);
    } else {
      registry.openPanel(paneId);
    }
  }, []);

  return (
    <div className="interp-guide-backdrop" onClick={onClose} role="dialog" aria-modal="true">
      <div className="interp-guide-modal" onClick={(e) => e.stopPropagation()}>
        <div className="interp-guide-header">
          <div className="interp-guide-header-title">
            <span className="interp-guide-icon">🧭</span>
            <div>
              <h3>AI Research Compass: Which Pane When & Why</h3>
              <p className="interp-dim">
                A roadmap of the research lifecycle and dashboard panes for beginners and practitioners.
              </p>
            </div>
          </div>
          <button type="button" className="btn-mini interp-guide-close" onClick={onClose}>
            ✕ Close
          </button>
        </div>

        <div className="interp-guide-grid">
          {RESEARCH_PANE_GUIDE.map((stage) => (
            <div key={stage.id} className="interp-guide-card">
              <div className="interp-guide-card-head">
                <span className="interp-guide-card-icon">{stage.icon}</span>
                <div className="interp-guide-card-titles">
                  <h4>{stage.title}</h4>
                  <span className="interp-chip interp-kind-agent">{stage.category}</span>
                </div>
              </div>

              <div className="interp-guide-qa">
                <div className="interp-guide-question">
                  <span className="interp-guide-q-label">Answers:</span> {stage.question}
                </div>
                <div className="interp-guide-when">
                  <span className="interp-guide-w-label">When to use:</span> {stage.when}
                </div>
                <div className="interp-guide-tip">
                  <span className="interp-guide-t-label">Tip:</span> {stage.tip}
                </div>
              </div>

              <div className="interp-guide-actions">
                <button
                  type="button"
                  className="interp-guide-open-btn"
                  onClick={() => {
                    openPane(stage.paneId, stage.sectionId);
                    onClose();
                  }}
                >
                  Open {stage.paneId.split('.').pop()}
                </button>
                {stage.altPaneId && (
                  <button
                    type="button"
                    className="btn-mini"
                    onClick={() => {
                      openPane(stage.altPaneId!, stage.altSectionId);
                      onClose();
                    }}
                  >
                    {stage.altLabel ?? stage.altPaneId.split('.').pop()}
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/**
 * Top header strip for interpretability panes providing quick links across the 3 surfaces:
 * 1. Prompt Context Window (Prompt)
 * 2. Model Explorer (Weights)
 * 3. Logit Lens & Stepper (Activations)
 * Plus the Research Compass trigger.
 */
export function ResearchWorkflowHeader({
  current,
}: {
  current: 'context' | 'architecture' | 'lens' | 'stepper';
}) {
  const [guideOpen, setGuideOpen] = useState(false);

  const navigateTo = useCallback((surface: 'context' | 'architecture' | 'lens' | 'stepper') => {
    switch (surface) {
      case 'context':
        registry.openPanel('interpretability.context');
        break;
      case 'architecture':
        registry.openPanel('interpretability.architecture');
        break;
      case 'lens':
        revealSection('lens', 'llamacpp.server');
        break;
      case 'stepper':
        revealSection('stepper', 'llamacpp.server');
        break;
    }
  }, []);

  return (
    <>
      <div className="interp-workflow-bar" role="navigation" aria-label="Interpretability layers">
        <span className="interp-workflow-label">
          <span className="interp-workflow-icon">🔬</span> Interpretability:
        </span>

        <div className="interp-workflow-tabs" role="tablist">
          <button
            type="button"
            role="tab"
            aria-selected={current === 'context'}
            className={`interp-workflow-tab${current === 'context' ? ' interp-workflow-active' : ''}`}
            onClick={() => navigateTo('context')}
            title="Inspect prompts, tokens, tool schemas, and history fed to the model"
          >
            1. Context Window <span className="interp-workflow-sub">(Prompt)</span>
          </button>

          <button
            type="button"
            role="tab"
            aria-selected={current === 'architecture'}
            className={`interp-workflow-tab${current === 'architecture' ? ' interp-workflow-active' : ''}`}
            onClick={() => navigateTo('architecture')}
            title="Inspect static weights, layer structures, attention heads, and GGUF tensors"
          >
            2. Model Explorer <span className="interp-workflow-sub">(Weights)</span>
          </button>

          <button
            type="button"
            role="tab"
            aria-selected={current === 'lens' || current === 'stepper'}
            className={`interp-workflow-tab${current === 'lens' || current === 'stepper' ? ' interp-workflow-active' : ''}`}
            onClick={() => navigateTo('lens')}
            title="Inspect dynamic hidden activations and logit lens projections across layers"
          >
            3. Lens & Stepper <span className="interp-workflow-sub">(Activations)</span>
          </button>
        </div>

        <button
          type="button"
          className="interp-guide-btn"
          onClick={() => setGuideOpen(true)}
          title="Open AI Research Compass: Which pane to use when & why"
        >
          🧭 Compass
        </button>
      </div>

      {guideOpen && <ResearchCompassModal onClose={() => setGuideOpen(false)} />}
    </>
  );
}
