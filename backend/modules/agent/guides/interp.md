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
| Activations | `lens`      | Each layer's residual read as words (tabs `trace` `reply` `guess` `compare`)            |
| Activations | `heads`     | Every attention head of a trace, scored: induction, previous-token, duplicate, sink     |

## Tools

- `interp.state` — the full digest: model and where its description came from, the attention pattern, the KV cache, the locus and what that layer is, the last captured turn, the in-window lens run, caveats. **Call it before explaining the screen** unless the pane context in this turn already answers.
- `interp.attention {from, to}` — per-layer rows: `L17 sliding, window 512, 8q/1kv × 256, KV from L13, (metadata)`. Use for "which layers…", "why is the cache small", "what changes at layer N".
- `interp.heads {traceId?, metric?, top?}` — a trace's heads ranked by a score, each with its layer kind when the trace is of the loaded model. Induction and duplicate are `null` on a trace that repeats nothing: say they are not measurable and point the user at **Run induction probe** in the Heads section (it records a random word sequence, twice). Never read `null` as zero.
- `interp.goto {section, tab?, layer?, position?}` — show it. Moving the locus moves every tool.
- For trace contents (what the model was disposed to say at a layer), use the `lens` group.

## How a fact is known — always say which

Every layer row ends with its **basis**:

- `metadata` — the GGUF states it per layer (Gemma 4's `sliding_window_pattern` array, a per-layer `head_count_kv` with zeros for non-attention blocks, `full_attention_interval`, `shared_kv_layers`). State it plainly.
- `rule` — inferred from the family's llama.cpp loader because the file does not say (Gemma 3's 5:1 pattern, Llama 4's 8192 chunk). Say "by the family rule".
- `tensors` — only the tensor directory shows it (a missing `attn_v` → K=V; `ssm_*` → Mamba; `shortconv` → conv).

A **conflict** means the sources disagree; report it, don't resolve it. A lens grid marked unverified is not a reading. Token counts flagged estimated are estimates.

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
**MoE** — a router sends each token to the top-k of N expert FFNs; total parameters set memory, active ones set compute.

## Pitfalls

- The architecture's single head count is a **headline**; per-layer rows are the fact. Gemma 4's global layers have 512-wide heads and a quarter of the KV heads of its local ones.
- The KV-cache figures are estimates: fp16, K and V, K=V counted once — not what a quantized-cache server allocates.
- A trace's model can differ from the agent's loaded model; the stepper only draws a window edge when they are the same file. Don't attribute one model's layout to another's trace.
- Quantized GGUF activations are not the bf16 activations papers report; magnitudes differ.
