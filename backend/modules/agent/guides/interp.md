# The interpretability suite

One window, a rail of tools, all reading **the same model** and following **one locus** (layer · position · trace). Pick a layer in one tool and the others follow.

| Group       | Section     | Answers                                                                                 |
| ----------- | ----------- | --------------------------------------------------------------------------------------- |
| Prompt      | `context`   | What the model was handed: blocks, real token counts, tools dropped by the budget       |
| Prompt      | `runs`      | One agent turn, round by round; forks and duels (tabs `runs` `harness` `forks` `duels`) |
| Weights     | `model`     | Block structure, tensor inventory, quantization, where the bytes sit                    |
| Weights     | `attention` | Each layer's mixer, window, head widths, KV sharing, the KV cache it costs              |
| Weights     | `design`    | A node-graph design that generates a PyTorch module                                     |
| Activations | `traces`    | Record a forward pass (llama.cpp)                                                       |
| Activations | `stepper`   | Step a trace node by node: attention maps, residual deltas, MoE routing                 |
| Activations | `lens`      | Each layer's residual read as words (tabs `trace` `lenses` `reply` `guess` `compare`)   |
| Activations | `heads`     | Every attention head of a trace, scored: induction, previous-token, duplicate, sink     |
| Features    | `features`  | SAE features of a trace: per token, labelled, with a reconstruction and a label check   |
| Features    | `steer`     | Push a feature into the model; default and steered generations side by side            |
| Circuits    | `graph`     | Attribution graphs: which features, through which paths, made the prediction            |
| —           | `library`   | SAEs, transcoders and lenses: installed, what they fit, the catalog; the GPU worker     |

## Tools

- `interp.state` — the full digest: model and where its description came from, the attention pattern, the KV cache, the locus and what that layer is, the last captured turn, the in-window lens run, caveats. **Call it before explaining the screen** unless the pane context in this turn already answers.
- `interp.attention {from, to}` — per-layer rows: `L17 sliding, window 512, 8q/1kv × 256, KV from L13, (metadata)`. Use for "which layers…", "why is the cache small", "what changes at layer N".
- `interp.heads {traceId?, metric?, top?}` — a trace's heads ranked by a score, each with its layer kind when the trace is of the loaded model. Induction and duplicate are `null` on a trace that repeats nothing: say they are not measurable and point the user at **Run induction probe** in the Heads section (it records a random word sequence, twice). Never read `null` as zero.
- `interp.features {traceId?, position?, top?}` — what an SAE reads in a trace. **Report its `check` first**: `verified` (FVU at or under the threshold) or `drifted` (the SAE explains too little of these activations; every feature below is suspect), with L0 beside the L0 the SAE was trained to. Then the strongest features with their Neuronpedia labels. A feature marked `firstToken` peaks on the token right after BOS; its label was drawn from other positions, so say so. Suggest **Check the label** in the section when a reading matters: it traces Neuronpedia's own example through this GGUF and compares.
- `interp.graph {pinnedOnly?}` — the open attribution graph as words: its target logit, the strongest *excitatory* paths into it (embedding → features → logit, each feature with its label when it has one, and the path's weakest link), and the reader's supernodes. A path is the greedy strongest chain, not the whole circuit; inhibitory edges and error nodes are part of the graph but not of a path. With pins, `pinnedOnly` walks the reader's own subgraph.
- `interp.lenses {traceId?, position?, token?}` — one position read through every lens installed for the trace's model, layer by layer, and (with `token`) that word's rank per layer under each. The **logit lens** reads what the model would say if it stopped at a layer; a fitted **Jacobian lens** transports the residual to the final layer first and reads what the layer *holds*. Only the logit lens is self-verified against the model's own output — say a fitted lens's reading is not. Fitted lenses come from the Library (Neuronpedia's, installed per GGUF).
- `interp.steer {feature?, strength?, prompt?, traceId?, maxTokens?}` — add a feature's direction at its SAE's layer and generate with and without it (greedy). `strength` is a multiple of the feature's own peak on the prompt: **1 is the natural strength; coherent text survives roughly −1.5..1.5**, beyond that Gemma 3 1B collapses into punctuation. Negative suppresses. **Report `arrived` first** — whether the feature itself moved the way it was pushed — then the first prediction's shift (gained and lost tokens, KL), then the texts. It runs the model and records two traces, so it asks permission like any acting tool; do not call it to answer a question the Features section already answers.
- `interp.library` — installed artifacts with their stated facts (layer, input width, dictionary width, and where each was read) and a fit verdict per model; the disk budget; the catalog; the GPU worker (state, held model, VRAM). It never downloads: to get something, name the catalog entry and send the user to the Library section, where they confirm its size.
- `interp.goto {section, tab?, layer?, position?}` — show it. Moving the locus moves every tool.
- For trace contents (what the model was disposed to say at a layer), use the `lens` group.

## How a fact is known — always say which

Every layer row ends with its **basis**:

- `metadata` — the GGUF states it per layer (Gemma 4's `sliding_window_pattern` array, a per-layer `head_count_kv` with zeros for non-attention blocks, `full_attention_interval`, `shared_kv_layers`). State it plainly.
- `rule` — inferred from the family's llama.cpp loader because the file does not say (Gemma 3's 5:1 pattern, Llama 4's 8192 chunk). Say "by the family rule".
- `tensors` — only the tensor directory shows it (a missing `attn_v` → K=V; `ssm_*` → Mamba; `shortconv` → conv).

A **conflict** means the sources disagree; report it, don't resolve it. A lens grid marked unverified is not a reading. Token counts flagged estimated are estimates.

## Does an artifact fit? Three answers

- `compatible` — trained on this model's repo, and no stated fact contradicts it.
- `incompatible` — a stated fact contradicts it (another repo, an input width that is not `d_model`, a layer the model lacks). Say which; an SAE on the wrong model still produces numbers, all wrong.
- `unknown` — nothing contradicts it but not enough is stated: most often a GGUF whose header names no base model. Do not round this up to compatible.

A width read from a **folder name** is a weaker claim than one read from a config; the artifact's `facts.source` says which.

Worker replies are `ran`, `failed` (the traceback is the answer — quote the last line) or `not_checked` (it declined, and says why: no model loaded, a model not in the Hugging Face cache). `not_checked` is not a failure.

## Reading features

- A feature is a direction the SAE learned; its **label** is Neuronpedia's (often automated) explanation from the bf16 model's top examples. A label is a hypothesis, not a definition.
- Activations are on the SAE's own scale: compare a feature with itself across tokens, not with other features.
- The **label check** is the strongest evidence that a label applies here: Neuronpedia's example, traced through this quantized GGUF, should activate the feature about as much (ratio near 1). Measured on Gemma 3 1B IT Q8_0 with Scope 2 L13 16k: 7 of 8 features within ~1%.
- BOS (position 0) is left out of the reconstruction check and the rankings: its residual is the attention sink, ~9× the norm of other tokens.

## Reading an attribution graph

- Nodes are the prompt's **embeddings** (bottom), **transcoder features** (labelled by Neuronpedia when the graph came from there), **error nodes** (what the transcoders failed to explain at that layer and position — a large one means the explanation has a hole there), and the output **logits** (top).
- Edge weights are **direct effects** through the frozen-attention linearisation: how much a node's activation pushed another's. Positive excites, negative inhibits.
- The famous reading — "Dallas → Texas → say a capital → Austin" — is a two-hop path: a state feature on " Dallas", then capital-related features at the last position, both feeding " Austin". Narrate paths like that, with the labels, and say which links are weak.

## Reading a fitted lens

- **Not a better next-token predictor.** Measured on Gemma 3 1B IT: the Jacobian lens's top-1 matches the next token *less* often than the logit lens in middle layers. That is not what it is for.
- **It reads concepts early.** "Mount Fuji is in a country whose national language is": at layer 11, " Japan" is rank 4 205 under the logit lens and 88 under the J-lens; " Japanese" at layer 15 is 519 vs 3. Two-hop bridge entities (Japan, India, Italy) surface many layers earlier — that is the reading to offer.
- Layer *k* of the lens reads `l_out-k` (the residual after block *k*); the final block has no map because it already is the final layer, and reads through identity.

## Reading a steered run

- **Steering is evidence about what a feature does, not proof.** A push of 1× that turns a weekend into "the botanical gardens" says the feature carries "places to visit"; a push that only breaks the text says the strength was wrong.
- **arrived** is the control: the SAE reads the steered residual back. `did_not_arrive` means the push was absorbed or misplaced — do not interpret the text. `nothing_to_suppress` means a negative push on a feature that was not active.
- **The first prediction is the most sensitive reading.** Measured: the France feature at −1× on "The Eiffel Tower is in the capital of" moved " France" 99.6% → 47%, with " Japan", " Germany", " Tokyo" gaining — while the greedy text still said France.
- llama.cpp adds the vector at every position, BOS included. Measured: leaving BOS unsteered changed nothing on Gemma 3 1B.

## Glossary

**Residual stream** — the per-position vector every block reads from and adds to; the lens reads it.
**Global (full) attention** — each query sees every earlier position; KV cache grows with context.
**Sliding-window attention (SWA)** — each query sees only the last _w_ positions; that layer's cache stops growing at _w_. Interleaved with global layers (Gemma 2/3/4, gpt-oss, Cohere 2) to keep long context cheap while some layers still see everything. Pattern convention: of every _n_ layers, all but the last are windowed.
**Chunked attention** — queries attend only within fixed chunks (Llama 4, 8192); nothing crosses a boundary.
**NoPE** — a layer with no rotary embedding (Llama 4's global layers) — position comes from the causal mask alone.
**GQA / MQA** — several query heads share one K/V head (GQA), or all do (MQA); the cache shrinks by the group ratio.
**KV sharing** — a layer computes no K/V and attends over an earlier layer's cache (Gemma 3n / Gemma 4 E2B/E4B's last N layers); its cache cost is zero.
**K=V** — the layer has no V projection; the value is the key (Gemma 4 global layers).
**MLA** — DeepSeek's latent attention: a compressed KV latent is cached instead of per-head K and V.
**Linear attention / Gated DeltaNet** — no attention matrix; a fixed-size state is updated per token (Qwen3-Next, Qwen3.5 run 3 of these per full-attention layer). No KV cache growth, but a lossy memory.
**SSM (Mamba)** — a selective state-space scan; also a recurrent state (granite-h, Jamba, Nemotron-H).
**Gated output** — a sigmoid gate on the attention output; Qwen3.5's Q projection is doubled to produce it.
**Attention sinks** — learned per-head logits that absorb attention mass (gpt-oss); otherwise most heads park weight on token 0.
**MTP head** — an extra block for multi-token prediction (speculative drafting), not the main pass.
**Reach** — an upper bound on how far back information can have travelled by a layer: a windowed layer extends it by its window, a global or recurrent one reaches the whole context.
**Logit lens** — unembed (final norm → LM head) the residual after each layer: what the model would say if it stopped there. **J-lens / J++** transport the residual through a fitted Jacobian first and read early layers better.
**Induction head** — attends from a token to the token that _followed_ its previous occurrence, and copies it forward: the in-context copying circuit. Paired with an earlier **previous-token head**, which lets each position know what came before it.
**Sink head** — parks most of its weight on position 0 when it has nothing to attend to; high `sink` with low entropy is usually that, not a meaningful pattern.
**SAE (sparse autoencoder)** — a wide dictionary trained to rewrite one site's activations (e.g. `resid_post` at layer 13) as a few active **features**. **Width** is the dictionary size (16k … 1M); **L0** is how many fire per token. Gemma Scope 2's are **JumpReLU**: a feature fires only above its own learned threshold.
**Transcoder** — like an SAE, but reads an MLP's input and predicts its output, so features compose across layers; per-layer sets (and cross-layer **CLTs**) are what attribution graphs are built from.
**MoE** — a router sends each token to the top-k of N expert FFNs; total parameters set memory, active ones set compute.

## Pitfalls

- The architecture's single head count is a **headline**; per-layer rows are the fact. Gemma 4's global layers have 512-wide heads and a quarter of the KV heads of its local ones.
- The KV-cache figures are estimates: fp16, K and V, K=V counted once — not what a quantized-cache server allocates.
- A trace's model can differ from the agent's loaded model; the stepper only draws a window edge when they are the same file. Don't attribute one model's layout to another's trace.
- Quantized GGUF activations are not the bf16 activations papers report; magnitudes differ.
