"""The Assistant Axis on llama.cpp: build one, watch a conversation along it, cap it.

Run as a subprocess — `python -m backend.modules.interp.axis_runner <spec.json>` —
for the tracer's reason (a ggml callback can take the process down) and through the
tracer's runner, so progress arrives as NDJSON and a crash is an event, not a hang.
The model loads once per run: building an axis is hundreds of generations, and a
trace per generation would spend most of its time loading weights and writing
blobs nobody reads.

Three modes, `spec["mode"]`:

- **build** — every (condition, question) pair goes through the model's own chat
  template and is answered greedily; the residual (`l_out`) of each *generated*
  token is kept at every layer. The response means, assistant vs roles, become the
  axis (`axis.fit`), checked on questions held out of the fit.
- **monitor** — a message list, templated, decoded once; each token's projection at
  the requested layers.
- **cap** — a default and a capped generation of the same messages. llama.cpp's
  control vector can only *add*, so a floor is two passes per token: decode, read
  the projections, roll the token back out of the KV cache, decode again with the
  shortfall added at each layer that fell short. Every capped token's projection
  is read back from that second pass, so "it held" is measured, not assumed.

The chat template matters here in a way it does not for a trace: the axis is the
difference between the model answering *as the assistant* and answering in a role,
and both are defined by the turn structure the model was tuned on.
"""

from __future__ import annotations

import ctypes
import json
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

from backend.modules.interp import axis
from backend.modules.llamacpp import ggml_abi, tracer

#: Keep the callback alive for the life of the process (see `tracer._CALLBACK`).
_CALLBACK: Any = None

emit = tracer.emit


class Capture:
    """`cb_eval` that keeps `l_out` at a set of layers, as float32 [tokens, n_embd]."""

    def __init__(self, api: ggml_abi.GgmlApi, layers: set[int]) -> None:
        self.api = api
        self.layers = layers
        #: Per layer, one array per micro-batch of the current decode.
        self.out: dict[int, list[np.ndarray]] = {}
        self.checked = False
        self.error = ""

    def on_node(self, ptr: Any, ask: bool, _user: Any) -> bool:
        try:
            if not self.checked:
                ggml_abi.self_check(self.api, ptr)
                self.checked = True
            name = self.api.name_of(ptr)
            if not name.startswith("l_out-"):
                return not ask
            try:
                layer = int(name[len("l_out-") :])
            except ValueError:
                return not ask
            if layer not in self.layers:
                return not ask
            if ask:
                return True
            tensor = self.api.tensor(ptr)
            dtype = self.api.type_of(tensor)
            if dtype != "f32":
                self.error = f"l_out-{layer} is {dtype}, expected f32"
                return True
            n_embd, n_tok = int(tensor.ne[0]), int(tensor.ne[1])
            raw = self.api.read(ptr, n_embd * n_tok * 4)
            self.out.setdefault(layer, []).append(
                np.frombuffer(raw, dtype=np.float32).reshape(n_tok, n_embd).copy()
            )
        except ggml_abi.AbiMismatch as exc:
            self.error = str(exc)
            return False
        except Exception as exc:  # noqa: BLE001 — never raise across the C boundary
            self.error = f"capture failed: {exc}"
        return True


class Session:
    """One loaded model and context, with the few llama.cpp calls the modes need."""

    def __init__(self, spec: dict[str, Any], layers: set[int], n_ctx: int) -> None:
        global _CALLBACK
        model_path = Path(str(spec["modelPath"])).expanduser()
        if not model_path.is_file():
            raise RuntimeError(f"no such GGUF: {model_path}")
        self.path = model_path
        self.ll, lib = tracer._load_libs()
        self.api = ggml_abi.bind(lib)
        ll = self.ll
        ll.llama_backend_init()
        mparams = ll.llama_model_default_params()
        mparams.n_gpu_layers = int(spec.get("gpuLayers") or 0)
        self.model = tracer._sym(
            ll, "llama_model_load_from_file", "llama_load_model_from_file"
        )(str(model_path).encode("utf-8"), mparams)
        if not self.model:
            raise RuntimeError(f"llama.cpp could not load {model_path.name}")
        self.vocab = tracer._vocab(ll, self.model)
        self.n_vocab = tracer._n_vocab(ll, self.vocab)
        self.n_embd = int(ll.llama_model_n_embd(self.model))
        self.n_layer = int(ll.llama_model_n_layer(self.model))
        bad = [layer for layer in layers if not 0 <= layer < self.n_layer]
        if bad:
            raise RuntimeError(f"layers {bad} are outside 0..{self.n_layer - 1}")
        self.capture = Capture(self.api, layers)

        cparams = ll.llama_context_default_params()
        cparams.n_ctx = n_ctx
        cparams.n_batch = n_ctx
        cparams.n_ubatch = min(n_ctx, 512)
        tracer._disable_flash_attn(ll, cparams)
        cb_type = dict(ll.llama_context_params._fields_).get("cb_eval")
        if cb_type is None:
            raise RuntimeError("this llama_cpp build has no cb_eval")
        _CALLBACK = cb_type(self.capture.on_node)
        cparams.cb_eval = _CALLBACK
        cparams.cb_eval_user_data = None
        self.ctx = tracer._sym(
            ll, "llama_init_from_model", "llama_new_context_with_model"
        )(self.model, cparams)
        if not self.ctx:
            raise RuntimeError("llama.cpp could not create a context")
        self.mem = ll.llama_get_memory(self.ctx)
        self.cvec_on = False

    def close(self) -> None:
        ll = self.ll
        ll.llama_free(self.ctx)
        tracer._sym(ll, "llama_model_free", "llama_free_model")(self.model)
        ll.llama_backend_free()

    # ── text in ──────────────────────────────────────────────────────────────

    def template(self, messages: list[dict[str, str]], open_turn: bool = True) -> str:
        """The model's own chat template over `messages`, ending on an open
        assistant turn. llama.cpp recognises the template from the GGUF's
        Jinja source; one it does not know is refused rather than guessed."""
        ll = self.ll
        tmpl = ll.llama_model_chat_template(self.model, None)
        if not tmpl:
            raise RuntimeError("this GGUF carries no chat template")
        keep: list[bytes] = []
        arr = (ll.llama_chat_message * len(messages))()
        for i, m in enumerate(messages):
            role, content = m["role"].encode(), m["content"].encode("utf-8")
            keep += [role, content]
            arr[i].role = role
            arr[i].content = content
        size = 4096 + sum(len(b) for b in keep) * 2
        for _ in range(2):
            buf = ctypes.create_string_buffer(size)
            n = ll.llama_chat_apply_template(
                tmpl, arr, len(messages), open_turn, buf, size
            )
            if n < 0:
                raise RuntimeError(
                    "llama.cpp does not recognise this model's chat template"
                )
            if n <= size:
                return buf.raw[:n].decode("utf-8", "replace")
            size = n + 16
        raise RuntimeError("chat template output kept growing")

    def tokenize(self, text: str) -> list[int]:
        """Tokens of templated text. Templates that already write BOS get no
        second one from the tokenizer."""
        ll = self.ll
        bos = ll.llama_vocab_bos(self.vocab)
        bos_piece = tracer._piece(ll, self.vocab, bos) if bos >= 0 else ""
        add_special = not (bos_piece and text.startswith(bos_piece))
        raw = text.encode("utf-8")
        buffer = (ll.llama_token * (len(raw) + 16))()
        count = ll.llama_tokenize(
            self.vocab, raw, len(raw), buffer, len(buffer), add_special, True
        )
        if count < 0:
            raise RuntimeError("tokenization overflowed its buffer")
        return [int(buffer[i]) for i in range(count)]

    def piece(self, token: int) -> str:
        return tracer._piece(self.ll, self.vocab, token)

    def text(self, tokens: list[int]) -> str:
        """Tokens as text, decoded once: a character split across two tokens
        decodes per token as two replacement marks."""
        out = b""
        for token in tokens:
            buffer = ctypes.create_string_buffer(64)
            n = self.ll.llama_token_to_piece(self.vocab, token, buffer, 64, 0, True)
            out += buffer.raw[: max(n, 0)]
        return out.decode("utf-8", "replace")

    # ── decoding ─────────────────────────────────────────────────────────────

    def reset(self) -> None:
        self.ll.llama_memory_clear(self.mem, True)

    def decode(self, tokens: list[int]) -> dict[int, np.ndarray]:
        """Decode `tokens` after whatever is in the cache; the captured `l_out`s."""
        ll = self.ll
        self.capture.out = {}
        array = (ll.llama_token * len(tokens))(*tokens)
        if ll.llama_decode(self.ctx, ll.llama_batch_get_one(array, len(tokens))) != 0:
            raise RuntimeError("llama_decode failed")
        if self.capture.error:
            raise RuntimeError(self.capture.error)
        # A long batch is computed in micro-batches, each calling back per layer.
        return {k: np.concatenate(v) for k, v in self.capture.out.items()}

    def argmax(self) -> int:
        logits = self.ll.llama_get_logits_ith(self.ctx, -1)
        return int(np.ctypeslib.as_array(logits, shape=(self.n_vocab,)).argmax())

    def is_eog(self, token: int) -> bool:
        return tracer._is_eog(self.ll, self.vocab, token)

    def rollback(self, position: int) -> None:
        """Drop the cache from `position` on, so that token can be decoded again."""
        if not self.ll.llama_memory_seq_rm(self.mem, 0, position, -1):
            raise RuntimeError("llama.cpp could not roll the cache back")

    def set_cvec(self, rows: dict[int, np.ndarray]) -> None:
        """Add `rows[layer]` after block `layer` for the next decode (empty: none).
        The buffer starts at layer 1 — see `tracer._apply_steer`."""
        ll = self.ll
        setter = tracer._sym(ll, "llama_set_adapter_cvec", "llama_apply_adapter_cvec")
        if not rows:
            if self.cvec_on:
                setter(self.ctx, None, 0, self.n_embd, -1, -1)
                self.cvec_on = False
            return
        first, last = min(rows), max(rows)
        buffer = (ctypes.c_float * (self.n_embd * self.n_layer))()
        flat = np.ctypeslib.as_array(buffer)
        for layer, row in rows.items():
            offset = (layer - 1) * self.n_embd
            flat[offset : offset + self.n_embd] = row
        code = setter(
            self.ctx, buffer, self.n_embd * self.n_layer, self.n_embd, first, last
        )
        if code != 0:
            raise RuntimeError(f"llama.cpp refused the control vector (code {code})")
        self.cvec_on = True


# ── build ────────────────────────────────────────────────────────────────────


def _generate(
    s: Session, prompt: list[int], max_tokens: int
) -> tuple[list[int], dict[int, list[np.ndarray]]]:
    """Greedy answer to a templated prompt; each generated token's `l_out` by layer.

    A generated token's residual is captured on the pass that *reads* it, so the
    last token's is one extra decode — kept, since it is what the answer ended on.
    """
    s.reset()
    s.decode(prompt)
    out: list[int] = []
    acts: dict[int, list[np.ndarray]] = {layer: [] for layer in s.capture.layers}
    for _ in range(max_tokens):
        token = s.argmax()
        if s.is_eog(token):
            break
        out.append(token)
        for layer, m in s.decode([token]).items():
            acts[layer].append(m[-1])
    return out, acts


def build(spec: dict[str, Any]) -> dict[str, Any]:
    roles = [r for r in spec.get("roles") or axis.default_roles()]
    questions = [q for q in spec.get("questions") or axis.DEFAULT_QUESTIONS]
    held_out = int(spec.get("heldOut") or axis.DEFAULT_HELD_OUT)
    if not 1 <= held_out < len(questions):
        raise RuntimeError("held-out questions must leave at least one to fit on")
    max_tokens = int(spec.get("maxTokens") or axis.DEFAULT_RESPONSE_TOKENS)
    conditions = [{"name": "assistant", "system": p} for p in axis.ASSISTANT_PROMPTS]
    conditions += [{"name": r["name"], "system": axis.role_prompt(r)} for r in roles]

    emit({"status": "loading"})
    probe = Session(spec, set(), 64)
    n_layer = probe.n_layer
    probe.close()
    s = Session(spec, set(range(n_layer)), 2048)
    started = time.time()
    try:
        total = len(conditions) * len(questions)
        means: list[np.ndarray] = []
        labels: list[str] = []
        held: list[bool] = []
        held_tokens: list[tuple[str, np.ndarray]] = []
        examples: dict[str, str] = {}
        done = 0
        for cond in conditions:
            for qi, question in enumerate(questions):
                messages = (
                    [{"role": "system", "content": cond["system"]}]
                    if cond["system"]
                    else []
                ) + [{"role": "user", "content": question}]
                prompt = s.tokenize(s.template(messages))
                tokens, acts = _generate(s, prompt, max_tokens)
                done += 1
                if not tokens:
                    emit({"status": "skip", "condition": cond["name"], "question": qi})
                    continue
                stack = np.stack(
                    [np.stack(acts[layer]) for layer in range(n_layer)], axis=1
                )  # [T, L, D]
                means.append(stack.mean(axis=0))
                labels.append(cond["name"])
                is_held = qi >= len(questions) - held_out
                held.append(is_held)
                if is_held:
                    held_tokens.append((cond["name"], stack.astype(np.float16)))
                if qi == 0:
                    examples.setdefault(cond["name"], s.text(tokens))
                emit(
                    {
                        "status": "sample",
                        "done": done,
                        "total": total,
                        "condition": cond["name"],
                        "elapsed": round(time.time() - started, 1),
                    }
                )
    finally:
        s.close()

    fitted = axis.fit(np.stack(means), labels, np.asarray(held), held_tokens)
    out_dir = Path(str(spec["outDir"]))
    out_dir.mkdir(parents=True, exist_ok=True)
    np.save(out_dir / "axis.npy", fitted.pop("vectors"))
    meta = {
        **fitted,
        "modelPath": str(spec["modelPath"]),
        "modelName": Path(str(spec["modelPath"])).stem,
        "nLayer": n_layer,
        "nEmbd": int(np.stack(means).shape[2]),
        "roles": [r["name"] for r in roles],
        "assistantPrompts": axis.ASSISTANT_PROMPTS,
        "questions": questions,
        "heldOut": held_out,
        "maxTokens": max_tokens,
        "examples": examples,
        "elapsed": round(time.time() - started, 1),
        "createdAt": time.time(),
    }
    (out_dir / "axis.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return {"outDir": str(out_dir), "defaultLayer": meta["defaultLayer"]}


# ── monitor ──────────────────────────────────────────────────────────────────


def monitor(spec: dict[str, Any]) -> dict[str, Any]:
    """Each token's projection at `layers` over a templated conversation, and where
    each message starts (templating each prefix and counting its tokens)."""
    vectors = {int(k): np.asarray(v, np.float32) for k, v in spec["vectors"].items()}
    messages = list(spec["messages"])
    cap = int(spec.get("tokenCap") or 4096)
    emit({"status": "loading"})
    s = Session(spec, set(vectors), cap + 16)
    try:
        # No open assistant turn: the conversation is read as it stands.
        text = s.template(messages, open_turn=False)
        tokens = s.tokenize(text)
        n_full = len(tokens)
        clipped = len(tokens) > cap
        if clipped:
            tokens = tokens[-cap:]  # the end of a conversation is where drift is
        starts = []
        for i in range(1, len(messages) + 1):
            prefix = s.tokenize(s.template(messages[:i], open_turn=False))
            starts.append(len(prefix))
        offset = n_full - len(tokens)  # positions dropped from the front
        emit({"status": "decode", "tokens": len(tokens)})
        s.reset()
        captured = s.decode(tokens)
        projections = {
            str(layer): [round(float(v), 3) for v in captured[layer] @ vectors[layer]]
            for layer in vectors
            if layer in captured
        }
        pieces = [s.piece(t) for t in tokens]
    finally:
        s.close()
    bounds, prev = [], 0
    for i, end in enumerate(starts):
        bounds.append(
            {
                "role": messages[i]["role"],
                "start": max(0, prev - offset),
                "end": max(0, end - offset),
            }
        )
        prev = end
    return {
        "tokens": pieces,
        "projections": projections,
        "messages": bounds,
        "clipped": offset,
    }


# ── cap ──────────────────────────────────────────────────────────────────────


def _cap_rows(
    proj: dict[int, float], vectors: dict[int, np.ndarray], tau: dict[int, float]
) -> dict[int, float]:
    """How much to add along each layer's axis to lift `proj` to the floor, bottom-up.

    What is added at layer l is still in the residual at l+1 (the stream is a
    sum), so a lower layer's push already covers part of a higher layer's
    shortfall: `carried` is that part, read through the angle between the two
    layers' axes. Linear, so approximate — the next pass measures the truth.
    """
    pushes: dict[int, float] = {}
    for layer in sorted(vectors):
        carried = sum(
            amount * float(vectors[low] @ vectors[layer])
            for low, amount in pushes.items()
        )
        shortfall = tau[layer] - (proj[layer] + carried)
        if shortfall > 0:
            pushes[layer] = shortfall
    return pushes


def _step_capped(
    s: Session,
    token: int,
    position: int,
    vectors: dict[int, np.ndarray],
    tau: dict[int, float],
    passes: int,
) -> tuple[dict[int, float], dict[int, float], dict[int, float]]:
    """Decode one token under the floor: (projections uncapped, after, pushes).

    Each extra pass rolls the token back and decodes it again with the pushes so
    far plus what the last measurement still fell short by."""
    s.set_cvec({})
    out = s.decode([token])
    first = {layer: float(out[layer][-1] @ vectors[layer]) for layer in vectors}
    proj, pushed = dict(first), {}
    for _ in range(passes):
        extra = _cap_rows(proj, vectors, tau)
        if not extra:
            break
        for layer, amount in extra.items():
            pushed[layer] = pushed.get(layer, 0.0) + amount
        s.rollback(position)
        s.set_cvec({layer: vectors[layer] * amount for layer, amount in pushed.items()})
        out = s.decode([token])
        proj = {layer: float(out[layer][-1] @ vectors[layer]) for layer in vectors}
    s.set_cvec({})
    return first, proj, pushed


def cap(spec: dict[str, Any]) -> dict[str, Any]:
    """Default and capped generations of the same messages, token by token."""
    vectors = {int(k): np.asarray(v, np.float32) for k, v in spec["vectors"].items()}
    tau = {int(k): float(v) for k, v in spec["tau"].items()}
    if min(vectors) < 1:
        raise RuntimeError("llama.cpp cannot add a vector after block 0")
    max_tokens = int(spec.get("maxTokens") or 48)
    passes = int(spec.get("passes") or 2)
    emit({"status": "loading"})
    s = Session(spec, set(vectors), 4096)
    try:
        prompt = s.tokenize(s.template(list(spec["messages"])))
        runs: dict[str, Any] = {}
        for mode in ("default", "capped"):
            emit({"status": "run", "run": mode})
            s.reset()
            s.set_cvec({})
            # The prompt is read as it is, in both runs: the floor was measured on
            # the assistant's *answers*, and template markers and a system prompt
            # sit below it by nature — capping them pushes on text the model
            # did not write (and on BOS, whose residual is many times the norm).
            out = s.decode(prompt)
            rows: list[dict[str, Any]] = [
                {
                    "token": s.piece(token),
                    "generated": False,
                    "projection": {
                        str(layer): round(float(out[layer][i] @ vectors[layer]), 3)
                        for layer in vectors
                    },
                }
                for i, token in enumerate(prompt)
            ]
            generated: list[int] = []
            capped_tokens = 0
            for step in range(max_tokens):
                token = s.argmax()
                if s.is_eog(token):
                    break
                generated.append(token)
                # The answer token's residual is computed on the pass that reads it
                # in — the residual the bands were measured on — and capping it
                # shapes the prediction that follows.
                if mode == "default":
                    out = s.decode([token])
                    proj = {
                        layer: float(out[layer][-1] @ vectors[layer])
                        for layer in vectors
                    }
                    before, pushed = proj, {}
                else:
                    before, proj, pushed = _step_capped(
                        s, token, len(prompt) + step, vectors, tau, passes
                    )
                    capped_tokens += bool(pushed)
                row: dict[str, Any] = {
                    "token": s.piece(token),
                    "generated": True,
                    "projection": {str(k): round(v, 3) for k, v in proj.items()},
                }
                if pushed:
                    row["uncapped"] = {str(k): round(v, 3) for k, v in before.items()}
                    row["pushed"] = {str(k): round(v, 3) for k, v in pushed.items()}
                rows.append(row)
                if step % 8 == 0:
                    emit({"status": "token", "run": mode, "generated": len(generated)})
            runs[mode] = {
                "text": s.text(generated),
                "tokens": rows,
                "promptTokens": len(prompt),
                "cappedTokens": capped_tokens,
            }
    finally:
        s.close()
    return runs


MODES = {"build": build, "monitor": monitor, "cap": cap}


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        emit({"error": "usage: python -m backend.modules.interp.axis_runner <spec>"})
        return 2
    try:
        spec = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        result = MODES[str(spec.get("mode"))](spec)
    except ImportError as exc:
        emit({"error": f"llama-cpp-python is not installed here ({exc})"})
        return 1
    except Exception as exc:  # noqa: BLE001 — the runner's only report is this line
        emit({"error": f"{type(exc).__name__}: {exc}"})
        return 1
    emit({"status": "done", "result": result})
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
