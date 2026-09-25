/**
 * What a notebook cell is made of, in words — the Learn strip's "This cell".
 *
 * Two sources, deliberately split:
 *
 * - **Knobs** (`learning_rate`, `lora_r`, …) come from the server: they are the
 *   recipe form's own `help` text (`GET /api/training/learn/glossary`), never a
 *   second copy here.
 * - **Concepts** — the classes and calls a knob is passed *to* (`SFTTrainer`,
 *   `get_peft_model`, `loss.backward()`) — have no row in the recipe form, so they
 *   are written here, short, and say why each one matters rather than restating
 *   the API.
 *
 * Matching is by identifier with word boundaries, so `lr` never fires inside
 * `clr_scheduler`. Pure, so it is tested without a notebook.
 */

export interface GlossaryTerm {
  name: string;
  label: string;
  help: string;
  aliases?: string[];
  group?: string;
}

export interface Concept {
  /** Identifiers that mean this concept in code. */
  match: string[];
  title: string;
  what: string;
  why: string;
}

export const CONCEPTS: Concept[] = [
  {
    match: ['AutoModelForCausalLM', 'from_pretrained'],
    title: 'Loading a pretrained model',
    what: 'Downloads (or reads from cache) weights someone else trained, into an architecture that predicts the next token.',
    why: 'Fine-tuning starts from here: you are adjusting a model that already knows language, which is why a few hundred examples can change its behaviour.',
  },
  {
    match: ['AutoTokenizer', 'tokenizer'],
    title: 'Tokenizer',
    what: 'Turns text into the integer ids the model reads, and back.',
    why: 'It must be the one the model was trained with. Its chat template decides how a conversation is laid out, and a mismatched one silently degrades every answer.',
  },
  {
    match: ['load_dataset'],
    title: 'Loading a dataset',
    what: 'Fetches a dataset from the Hugging Face Hub (or local files) as splits of rows.',
    why: 'The columns decide which trainer can use it: SFT wants text or messages, DPO wants chosen/rejected pairs.',
  },
  {
    match: ['SFTTrainer', 'SFTConfig'],
    title: 'Supervised fine-tuning (SFT)',
    what: 'Trains the model to reproduce example completions token by token, minimising cross-entropy loss.',
    why: 'The simplest way to teach a format or a skill by example. It imitates — it does not learn which of two answers is better; that is what preference methods like DPO add.',
  },
  {
    match: ['DPOTrainer', 'DPOConfig'],
    title: 'Direct preference optimisation (DPO)',
    what: 'Trains on pairs of answers, pushing the model towards the preferred one relative to a frozen reference.',
    why: 'Teaches preference without a separate reward model. `beta` sets how far the model may drift from the reference.',
  },
  {
    match: ['LoraConfig', 'get_peft_model', 'peft'],
    title: 'LoRA adapters',
    what: 'Freezes the model and trains small low-rank matrices added beside chosen layers.',
    why: 'Trains a fraction of a percent of the parameters, so a large model fits on one GPU and the result is a small file you can swap in and out. The rank `r` trades capacity for size.',
  },
  {
    match: ['Trainer', 'TrainingArguments'],
    title: 'The training loop',
    what: 'Runs batches through the model, computes the loss, backpropagates and steps the optimiser — repeatedly, with logging and checkpoints.',
    why: 'Every config knob changes one part of that loop; reading them as parts of it is what makes them less arbitrary.',
  },
  {
    match: ['backward'],
    title: 'Backpropagation',
    what: '`loss.backward()` computes, for every weight, how much the loss would change if that weight moved.',
    why: 'Those gradients are what the optimiser follows. Their size is what the gradient norm on the Metrics strip measures.',
  },
  {
    match: ['zero_grad', 'optimizer', 'AdamW'],
    title: 'The optimiser',
    what: 'Uses the gradients to update the weights, scaled by the learning rate. AdamW adapts the step per weight and adds weight decay.',
    why: 'Gradients accumulate unless cleared, which is why a hand-written loop calls `zero_grad()` every step.',
  },
  {
    // Not a bare `eval`: it is a string in every split name and config value.
    match: ['no_grad', 'inference_mode'],
    title: 'Evaluation mode',
    what: 'Runs the model without recording gradients (and with dropout off).',
    why: 'Faster and uses less memory; a held-out evaluation is how you learn whether falling training loss means the model generalises.',
  },
  {
    match: ['bf16', 'fp16', 'torch_dtype', 'bfloat16', 'float16'],
    title: 'Mixed precision',
    what: 'Stores and computes in 16-bit floats instead of 32.',
    why: 'Halves memory and speeds training. bf16 keeps fp32’s range and rarely overflows; fp16 can, which shows up as a NaN loss.',
  },
  {
    match: ['softmax', 'cross_entropy', 'CrossEntropyLoss'],
    title: 'Cross-entropy loss',
    what: 'How surprised the model was by the correct next token, averaged over tokens (softmax turns scores into probabilities first).',
    why: 'It is the "loss" on the chart. exp(loss) is perplexity: a loss of 2.0 means the model was about as unsure as choosing among ~7 tokens.',
  },
];

function escape(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function mentions(source: string, identifier: string): boolean {
  return new RegExp(`(?<![A-Za-z0-9_])${escape(identifier)}(?![A-Za-z0-9_])`).test(source);
}

/** Knobs from the served glossary that the cell names, in cell order. */
export function knobsIn(source: string, glossary: GlossaryTerm[]): GlossaryTerm[] {
  const hits = glossary
    .map((t) => {
      const names = [t.name, ...(t.aliases ?? [])];
      const at = Math.min(
        ...names
          .filter((n) => mentions(source, n))
          .map((n) => source.search(new RegExp(escape(n)))),
      );
      return { t, at };
    })
    .filter((h) => Number.isFinite(h.at));
  return hits.sort((a, b) => a.at - b.at).map((h) => h.t);
}

/** Concepts the cell uses. */
export function conceptsIn(source: string): Concept[] {
  return CONCEPTS.filter((c) => c.match.some((m) => mentions(source, m)));
}
