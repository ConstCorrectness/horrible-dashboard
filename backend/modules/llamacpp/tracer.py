"""The tracer: run a forward pass and capture ggml's per-node tensors.

Run as a subprocess — `python -m backend.modules.llamacpp.tracer <spec.json>` —
never in the backend process. Everything below reaches into a C library through
a struct mirror that upstream makes no promise about, and a segfault inside a
ggml callback must be an exit code the runner can report, not a dead FastAPI
backend in the middle of somebody's turn. It is the same shape
`training/envs.py` uses for the same reason.

Five things here are non-obvious and each fails in its own quiet way:

- **The `CFUNCTYPE` instance is held in a module global.** A callback built
  inline is garbage-collected the moment the call that created it returns, and
  the next graph node jumps into freed memory.
- **Flash attention is disabled explicitly.** With it on, `ggml_flash_attn_ext`
  is a single fused node and the score matrix never materialises — attention
  capture would silently produce nothing at all rather than fail.
- **The ABI self-check runs on the first tensor and aborts the whole run.** A
  mismatch cannot be recovered from and a trace that parses but is garbage is
  worse than no trace, so nothing is written.
- **Quantized tensors are recorded as metadata only.** Dequantizing in Python
  would be a second implementation of ggml's quant formats — the kind of
  duplicate that is wrong in one corner and silently plausible everywhere else.
- **No chat template is applied.** The prompt is traced as raw text; wrapping it
  in a template we render ourselves would put a *different* token sequence
  through the model than the one shown in the pane. The manifest says so.

The graph is not a stream of anonymous nodes: llama.cpp names them (`ffn_out-15`,
`attn_norm-0`), so the capture set is a name filter and the layer is parsed from
the name.
"""

from __future__ import annotations

import ctypes
import json
import math
import sys
import time
from pathlib import Path
from typing import Any

from backend.modules.llamacpp import ggml_abi, traces

#: Node names worth keeping by default — the residual stream at each stage
#: rather than every intermediate. Substring matches, because the block suffix
#: varies.
DEFAULT_CAPTURE = (
    "inp_embd",
    "attn_norm",
    "kqv_out",
    "ffn_inp",
    "ffn_norm",
    "ffn_out",
    "l_out",
    "result_norm",
    "result_output",
)

#: Added when attention capture is on. These are the nodes that only exist with
#: flash attention disabled.
ATTENTION_CAPTURE = ("kq_soft_max", "kq-", "kq_mask")

#: State-space (Mamba / Mamba2 / Jamba / Falcon-H1) nodes.
#:
#: `DEFAULT_CAPTURE` above is a *transformer* capture set — `attn_norm`, `kqv_out`,
#: `ffn_*`. On an SSM model none of those exist, so tracing one used to yield the
#: residual stream and **nothing whatsoever about the mechanism**, which reads as
#: "tracing is broken on this model" rather than "we never asked for those nodes".
#:
#: Prefixes rather than an enumeration, because the mechanism selection is already a
#: substring match (`Tracer.wanted`) and the exact names vary by architecture:
#: `ssm_conv1d` and `ssm_conv1d_q` are both real, `ssm_scan` is not. The names were
#: verified against the literals in the installed llama.dll (b10448) rather than
#: assumed — a capture set that matches nothing fails silently, which is the exact
#: failure this exists to fix.
SSM_CAPTURE = ("ssm_",)

#: RWKV's time-mixing and channel-mixing blocks, the same idea for that family.
RWKV_CAPTURE = ("time_mix_", "channel_mix_", "token_shift")

#: `general.architecture` prefix → the extra capture group it needs. Matched as a
#: prefix so `mamba2`, `rwkv6`, `rwkv6qwen2` and `rwkv7` come along without a list
#: that has to be revised every time upstream adds a variant.
ARCH_CAPTURE: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("mamba", SSM_CAPTURE),
    ("jamba", SSM_CAPTURE),
    ("falcon-h", SSM_CAPTURE),
    ("falcon_h", SSM_CAPTURE),
    ("granitemoehybrid", SSM_CAPTURE),
    ("plamo2", SSM_CAPTURE),
    ("nemotron_h", SSM_CAPTURE),
    ("rwkv", RWKV_CAPTURE),
    ("arwkv", RWKV_CAPTURE),
)


def capture_for(architecture: str) -> tuple[str, ...]:
    """The capture set for a model, as a **union** with the transformer defaults.

    Union and never replacement: a hybrid (Jamba, Falcon-H1, Nemotron-H) interleaves
    attention blocks with SSM blocks, so swapping the list on an architecture match
    would blind the trace to half of every such model — and would do it quietly,
    since what is left still produces a plausible-looking trace.
    """
    lowered = (architecture or "").strip().lower()
    extra: list[str] = []
    for prefix, group in ARCH_CAPTURE:
        if not lowered.startswith(prefix):
            continue
        extra.extend(p for p in group if p not in extra)
    return DEFAULT_CAPTURE + tuple(extra)


#: A single record larger than this is summarised rather than stored. One node
#: is never worth a fifth of the whole trace budget.
MAX_RECORD_BYTES = 64 * 1024 * 1024

#: Keep the callback alive for the lifetime of the process. See the docstring.
_CALLBACK: Any = None


def emit(event: dict[str, Any]) -> None:
    """One NDJSON line to stdout — the runner's only channel back."""
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()


#: The symbol every candidate library is probed for. A library that lacks it is
#: not the one holding ggml's API, however plausible its name.
_GGML_PROBE = "ggml_nbytes"


def _exports_ggml(lib: Any) -> bool:
    # ctypes raises AttributeError for a missing symbol, so getattr's default
    # answers the question without a try/except.
    return lib is not None and getattr(lib, _GGML_PROBE, None) is not None


def _ggml_candidates() -> Any:
    """Every plausible holder of the ggml symbols, cheapest first.

    Modern wheels split the build into several shared libraries and the ggml API
    lives in **ggml-base** alone: `llama.dll` re-exports none of it, and neither
    does `ggml.dll` (the backend registry) or `ggml-cpu.dll`. Older wheels put
    everything in the llama library. Each candidate is yielded and probed rather
    than assumed, because guessing wrong surfaces as `AbiMismatch` from `bind()`
    ("ggml exports none of ggml_type_name") rather than as a missing file.
    """
    import llama_cpp
    import llama_cpp.llama_cpp as low

    try:
        from llama_cpp import _ggml  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 — older wheels have no _ggml at all
        _ggml = None
    if _ggml is not None:
        # The attribute has been named both `lib` and `libggml`; scanning every
        # loaded CDLL on the module costs nothing and outlives the next rename.
        for value in vars(_ggml).values():
            if isinstance(value, ctypes.CDLL):
                yield value

    lib_dir = Path(llama_cpp.__file__).parent / "lib"
    for path in sorted(lib_dir.glob("*ggml-base*")):
        if path.suffix in (".dll", ".so", ".dylib") or ".so." in path.name:
            try:
                yield ctypes.CDLL(str(path))
            except OSError:
                continue

    yield getattr(low, "_lib", None)


def _load_libs() -> tuple[Any, Any]:
    """The `llama_cpp` module and the shared library holding the ggml symbols."""
    import llama_cpp

    for candidate in _ggml_candidates():
        if _exports_ggml(candidate):
            return llama_cpp, candidate
    raise RuntimeError(
        "could not locate the ggml shared library in llama_cpp: no library in "
        f"{Path(llama_cpp.__file__).parent / 'lib'} exports {_GGML_PROBE}"
    )


def _sym(module: Any, *names: str) -> Any:
    for name in names:
        fn = getattr(module, name, None)
        if fn is not None:
            return fn
    raise RuntimeError(f"llama_cpp exports none of {', '.join(names)}")


class Tracer:
    def __init__(self, spec: dict[str, Any]) -> None:
        self.spec = spec
        self.fidelity = str(spec.get("fidelity") or "fp16")
        self.layers = {int(v) for v in spec.get("layers") or []}
        self.attention = bool(spec.get("attention"))
        patterns = [str(p) for p in spec.get("capture") or []]
        # An explicit capture list is taken as given; otherwise the default set is
        # chosen by architecture, so a Mamba or RWKV model records its own mechanism
        # instead of only the residual stream. See `capture_for`.
        self.patterns = (
            tuple(patterns)
            if patterns
            else capture_for(str(spec.get("architecture") or ""))
        )
        if self.attention:
            self.patterns = self.patterns + ATTENTION_CAPTURE
        self.byte_budget = int(spec.get("byteBudget") or 0)
        self.pass_index = 0
        self.capturing = False
        self.written = 0
        self.checked = False
        self.abort: str = ""
        self.over_budget = False
        self.api: ggml_abi.GgmlApi | None = None
        self.writer: traces.TraceWriter | None = None

    # -- selection ---------------------------------------------------------

    def wanted(self, name: str) -> bool:
        if not any(p in name for p in self.patterns):
            return False
        if self.layers:
            layer = traces.layer_of(name)
            # A node outside any block (embeddings, the output head) is always
            # kept: filtering it out with a layer selection would silently drop
            # the input and the logits, which are the two ends of the story.
            if layer is not None and layer not in self.layers:
                return False
        return True

    # -- the callback ------------------------------------------------------

    def on_node(self, tensor_ptr: Any, ask: bool, _user: Any) -> bool:
        """ggml's `cb_eval`. Called with `ask=True` to offer a node, then again
        with `ask=False` once it has been computed."""
        if self.abort or not self.capturing:
            return False
        api = self.api
        writer = self.writer
        if api is None or writer is None:
            return False
        try:
            if not self.checked:
                ggml_abi.self_check(api, tensor_ptr)
                self.checked = True
            name = api.name_of(tensor_ptr)
            if ask:
                return self.wanted(name)
            if not self.wanted(name):
                return True
            self._capture(api, writer, tensor_ptr, name)
        except ggml_abi.AbiMismatch as exc:
            self.abort = str(exc)
            return False
        except Exception as exc:  # noqa: BLE001 — never raise across the C boundary
            self.abort = f"trace callback failed: {exc}"
            return False
        return True

    def _capture(
        self, api: ggml_abi.GgmlApi, writer: traces.TraceWriter, ptr: Any, name: str
    ) -> None:
        tensor = api.tensor(ptr)
        dtype = api.type_of(tensor)
        ne = [int(tensor.ne[i]) for i in range(ggml_abi.GGML_MAX_DIMS)]
        nb = [int(tensor.nb[i]) for i in range(ggml_abi.GGML_MAX_DIMS)]
        nbytes = int(api.nbytes(ptr))
        op = api.op_of(ptr)

        def meta_only(reason: dict[str, float]) -> None:
            writer.append(
                name=name,
                op=op,
                dtype=dtype,
                ne=ne,
                nb=nb,
                pass_index=self.pass_index,
                fidelity="summary",
                summary=reason,
            )

        # A quantized tensor is a weight, not an activation, and dequantizing it
        # here would mean reimplementing ggml's block formats in Python.
        if bool(api.is_quantized(tensor.type)) or dtype not in ("f32", "f16"):
            meta_only({"bytes": float(nbytes)})
            return
        if self.byte_budget and self.written >= self.byte_budget:
            if not self.over_budget:
                self.over_budget = True
                emit({"status": "budget", "bytes": self.written})
            meta_only({"bytes": float(nbytes)})
            return
        if nbytes > MAX_RECORD_BYTES:
            values = traces.decode(api.read(ptr, nbytes), dtype)
            meta_only(traces.summarize(values))
            return

        raw = api.read(ptr, nbytes)
        if self.fidelity == "summary":
            meta_only(traces.summarize(traces.decode(raw, dtype)))
            return
        if self.fidelity == "fp16" and dtype == "f32":
            payload = traces.encode_f16(traces.decode(raw, "f32"))
            stored = "f16"
        else:
            payload = raw
            stored = dtype
        writer.append(
            name=name,
            op=op,
            dtype=stored,
            ne=ne,
            nb=nb,
            pass_index=self.pass_index,
            fidelity="fp16" if stored == "f16" and dtype == "f32" else "full",
            payload=payload,
        )
        self.written += len(payload)


def run(spec: dict[str, Any]) -> dict[str, Any]:
    model_path = Path(str(spec["modelPath"])).expanduser()
    if not model_path.is_file():
        raise RuntimeError(f"no such GGUF: {model_path}")
    directory = traces.traces_root() / str(spec["traceId"])

    emit({"status": "loading", "model": model_path.name})
    llama_cpp, lib = _load_libs()
    api = ggml_abi.bind(lib)

    global _CALLBACK
    tracer = Tracer(spec)
    tracer.api = api

    llama_cpp.llama_backend_init()
    mparams = llama_cpp.llama_model_default_params()
    mparams.n_gpu_layers = int(spec.get("gpuLayers") or 0)
    load_model = _sym(
        llama_cpp, "llama_model_load_from_file", "llama_load_model_from_file"
    )
    model = load_model(str(model_path).encode("utf-8"), mparams)
    if not model:
        raise RuntimeError(f"llama.cpp could not load {model_path.name}")

    try:
        vocab = _vocab(llama_cpp, model)
        tokens = _tokenize(llama_cpp, vocab, str(spec.get("prompt") or ""))
        cap = min(
            int(spec.get("tokenCap") or traces.MAX_TRACE_TOKENS),
            traces.MAX_TRACE_TOKENS,
        )
        if len(tokens) > cap:
            tokens = tokens[:cap]
        if not tokens:
            raise RuntimeError("the prompt tokenized to nothing")
        gen_tokens = max(0, int(spec.get("maxTokens") or 0))

        cparams = llama_cpp.llama_context_default_params()
        cparams.n_ctx = max(len(tokens) + gen_tokens + 8, 64)
        cparams.n_batch = max(len(tokens), 32)
        _disable_flash_attn(llama_cpp, cparams)
        cb_type = dict(llama_cpp.llama_context_params._fields_).get("cb_eval")
        if cb_type is None:
            raise RuntimeError("this llama_cpp build has no cb_eval — cannot trace")
        _CALLBACK = cb_type(tracer.on_node)
        cparams.cb_eval = _CALLBACK
        cparams.cb_eval_user_data = None

        make_ctx = _sym(
            llama_cpp, "llama_init_from_model", "llama_new_context_with_model"
        )
        context = make_ctx(model, cparams)
        if not context:
            raise RuntimeError("llama.cpp could not create a context")

        meta = {
            "traceId": str(spec["traceId"]),
            "createdAt": time.time(),
            "modelPath": str(model_path),
            "modelName": model_path.stem,
            "modelSha": traces.model_sha(model_path),
            "llamaBuild": _build_id(llama_cpp),
            "flashAttn": False,
            "byteOrder": "little",
            "fidelity": tracer.fidelity,
            "attention": tracer.attention,
            "layers": sorted(tracer.layers),
            "prompt": str(spec.get("prompt") or ""),
            "promptTokens": len(tokens),
            "maxTokens": gen_tokens,
            "chatTemplate": False,
            "note": (
                "Raw prompt — no chat template was applied, so these tokens are "
                "exactly the text above."
            ),
        }
        writer = traces.TraceWriter(directory, meta)
        tracer.writer = writer

        try:
            emit({"status": "prompt", "tokens": len(tokens)})
            token_records = _decode_passes(
                llama_cpp, context, vocab, tokens, gen_tokens, tracer
            )
            if tracer.abort:
                raise RuntimeError(tracer.abort)
            manifest = writer.close(token_records)
        finally:
            llama_cpp.llama_free(context)
        return manifest
    finally:
        free_model = _sym(llama_cpp, "llama_model_free", "llama_free_model")
        free_model(model)
        llama_cpp.llama_backend_free()


def _vocab(llama_cpp: Any, model: Any) -> Any:
    getter = getattr(llama_cpp, "llama_model_get_vocab", None)
    return getter(model) if getter is not None else model


def _tokenize(llama_cpp: Any, vocab: Any, text: str) -> list[int]:
    raw = text.encode("utf-8")
    buffer = (llama_cpp.llama_token * (len(raw) + 16))()
    count = llama_cpp.llama_tokenize(
        vocab, raw, len(raw), buffer, len(buffer), True, True
    )
    if count < 0:
        raise RuntimeError("tokenization overflowed its buffer")
    return [int(buffer[i]) for i in range(count)]


def _disable_flash_attn(llama_cpp: Any, cparams: Any) -> None:
    """Turn flash attention off, whichever way this build spells it.

    Not optional when attention capture is on: fused attention computes the
    scores inside one kernel and the matrix the pane wants to show never exists
    as a graph node. Recent llama.cpp *auto-enables* it, so leaving the default
    would mean attention capture that returns nothing and looks like a bug in
    the filter.
    """
    fields = dict(type(cparams)._fields_)
    if "flash_attn_type" in fields:
        disabled = getattr(llama_cpp, "LLAMA_FLASH_ATTN_TYPE_DISABLED", 0)
        cparams.flash_attn_type = disabled
    elif "flash_attn" in fields:
        cparams.flash_attn = False


def _build_id(llama_cpp: Any) -> str:
    """A version string for the libllama actually loaded here.

    Which is *not* the `llama-server` build the chat path downloaded — that is
    exactly why a trace may only be overlaid on a turn when the two agree.
    """
    for name in ("llama_print_system_info", "__version__"):
        value = getattr(llama_cpp, name, None)
        if isinstance(value, str):
            return value
    return f"llama-cpp-python {getattr(llama_cpp, '__version__', 'unknown')}"


def _decode_passes(
    llama_cpp: Any,
    context: Any,
    vocab: Any,
    tokens: list[int],
    gen_tokens: int,
    tracer: Tracer,
) -> list[dict[str, Any]]:
    """The prompt pass, then one pass per generated token.

    Greedy sampling by argmax over the logits rather than a sampler chain: the
    point is a *reproducible* forward pass to look inside, and a temperature
    would make the trace unrepeatable for no benefit.
    """
    records: list[dict[str, Any]] = [
        {"index": i, "id": t, "text": _piece(llama_cpp, vocab, t), "generated": False}
        for i, t in enumerate(tokens)
    ]

    array = (llama_cpp.llama_token * len(tokens))(*tokens)
    tracer.capturing = True
    tracer.pass_index = 0
    batch = llama_cpp.llama_batch_get_one(array, len(tokens))
    if llama_cpp.llama_decode(context, batch) != 0:
        raise RuntimeError("llama_decode failed on the prompt")
    emit({"status": "pass", "pass": 0, "bytes": tracer.written})

    n_vocab = _n_vocab(llama_cpp, vocab)
    position = len(tokens)
    for step in range(gen_tokens):
        if tracer.abort:
            break
        token = _argmax(llama_cpp, context, n_vocab)
        records.append(
            {
                "index": position,
                "id": token,
                "text": _piece(llama_cpp, vocab, token),
                "generated": True,
            }
        )
        position += 1
        if _is_eog(llama_cpp, vocab, token):
            break
        tracer.pass_index = step + 1
        one = (llama_cpp.llama_token * 1)(token)
        if llama_cpp.llama_decode(context, llama_cpp.llama_batch_get_one(one, 1)) != 0:
            raise RuntimeError("llama_decode failed while generating")
        emit({"status": "pass", "pass": step + 1, "bytes": tracer.written})
    tracer.capturing = False
    return records


def _n_vocab(llama_cpp: Any, vocab: Any) -> int:
    fn = _sym(llama_cpp, "llama_vocab_n_tokens", "llama_n_vocab")
    return int(fn(vocab))


def _argmax(llama_cpp: Any, context: Any, n_vocab: int) -> int:
    logits = llama_cpp.llama_get_logits_ith(context, -1)
    best = 0
    best_value = -math.inf
    for i in range(n_vocab):
        value = float(logits[i])
        if value > best_value:
            best_value = value
            best = i
    return best


def _piece(llama_cpp: Any, vocab: Any, token: int) -> str:
    buffer = ctypes.create_string_buffer(64)
    count = llama_cpp.llama_token_to_piece(vocab, token, buffer, len(buffer), 0, True)
    if count < 0:
        return ""
    return buffer.raw[:count].decode("utf-8", "replace")


def _is_eog(llama_cpp: Any, vocab: Any, token: int) -> bool:
    fn = getattr(llama_cpp, "llama_vocab_is_eog", None) or getattr(
        llama_cpp, "llama_token_is_eog", None
    )
    try:
        return bool(fn(vocab, token)) if fn else False
    except Exception:  # noqa: BLE001 — an unknown signature must not end the run
        return False


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        emit({"error": "usage: python -m backend.modules.llamacpp.tracer <spec.json>"})
        return 2
    try:
        spec = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        emit({"error": f"unreadable spec: {exc}"})
        return 2
    try:
        manifest = run(spec)
    except ImportError as exc:
        emit(
            {
                "error": (
                    "llama-cpp-python is not installed in this environment "
                    f"({exc}). Run: uv sync --extra llamacpp"
                )
            }
        )
        return 1
    except Exception as exc:  # noqa: BLE001 — the runner's only report is this line
        emit({"error": str(exc)})
        return 1
    emit(
        {
            "status": "done",
            "traceId": manifest.get("traceId"),
            "records": manifest.get("recordCount"),
            "bytes": manifest.get("blobBytes"),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
