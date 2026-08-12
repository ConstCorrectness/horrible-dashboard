"""A minimal, self-checking ctypes binding for the ggml tensor ABI.

`llama-cpp-python` declares the `cb_eval` callback type and nothing else: the
callback hands you a `void *` pointing at a `struct ggml_tensor` and the wheel
exposes **zero ggml accessors**, so without this module a trace is a stream of
opaque pointers.

`struct ggml_tensor` is internal C with no stability promise, which is the single
biggest risk in the trace path. Three things contain it:

- **Mirror as little as possible.** Only the first four fields are declared here
  (`type`, `buffer`, `ne[4]`, `nb[4]`) — the prefix that has been stable for
  years and that every layout question depends on. Everything else (name, op,
  data) is read through *functions* ggml exports, which is the same instinct as
  serving `plane_order` instead of hardcoding it: ask the thing that knows.
- **A self-check gates every trace.** `self_check` recomputes `nbytes` from
  `(type, ne, nb)` using ggml's own published formula and compares it against
  `ggml_nbytes()`, and compares a dimension count derived from `ne` against
  `ggml_n_dims()`. A field inserted or resized shifts `ne`/`nb` and the two
  disagree immediately. On mismatch the caller aborts and writes nothing —
  garbage that parses is worse than no trace.
- **The caller runs in a subprocess.** Anything this gets wrong is a segfault,
  and a segfault inside a ggml callback must be an exit code rather than a dead
  FastAPI backend mid-turn.

Nothing here imports `llama_cpp`; `bind()` takes the loaded library object, so
the whole module is exercisable in tests against a stub library and ctypes
structures built in Python memory.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
from typing import Any

#: ggml's fixed dimensionality. Not a guess — a tensor is always 4-D in the
#: struct, with trailing dimensions of 1.
GGML_MAX_DIMS = 4


class GgmlTensor(ctypes.Structure):
    """The first four fields of `struct ggml_tensor`.

    Deliberately *not* the whole struct. Declaring the tail (op params, `src[]`,
    `view_src`, `data`, `name`) would mean tracking sizes that genuinely change
    between releases, in exchange for information that ggml already exports a
    function for.
    """

    _fields_ = [
        ("type", ctypes.c_int),
        ("buffer", ctypes.c_void_p),
        ("ne", ctypes.c_int64 * GGML_MAX_DIMS),
        ("nb", ctypes.c_size_t * GGML_MAX_DIMS),
    ]


TensorPtr = ctypes.POINTER(GgmlTensor)


class AbiMismatch(RuntimeError):
    """The mirrored struct disagrees with the library. Never write a trace."""


@dataclass
class GgmlApi:
    """The handful of ggml entry points a trace needs, already bound."""

    lib: Any
    type_name: Any
    type_size: Any
    blck_size: Any
    nbytes: Any
    n_dims: Any
    nelements: Any
    get_name: Any
    op_desc: Any
    is_quantized: Any
    backend_tensor_get: Any

    def tensor(self, pointer: Any) -> GgmlTensor:
        """The mirrored struct behind a `cb_eval` pointer."""
        return ctypes.cast(pointer, TensorPtr).contents

    def name_of(self, pointer: Any) -> str:
        raw = self.get_name(pointer)
        return raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)

    def op_of(self, pointer: Any) -> str:
        raw = self.op_desc(pointer)
        return raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)

    def type_of(self, tensor: GgmlTensor) -> str:
        raw = self.type_name(tensor.type)
        return raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)

    def read(self, pointer: Any, size: int) -> bytes:
        """Copy a tensor's data out of whatever backend buffer holds it.

        `ggml_backend_tensor_get` and not `tensor->data`: on a non-CPU backend
        that pointer is device memory, and dereferencing it from Python is a
        crash on a machine the developer probably doesn't have.
        """
        buffer = (ctypes.c_char * size)()
        self.backend_tensor_get(pointer, buffer, 0, ctypes.c_size_t(size))
        return bytes(buffer)


def _fn(lib: Any, *names: str) -> Any:
    """The first of `names` the library exports.

    Several of these have been renamed upstream while keeping the old symbol as
    an alias for a release or two; taking the first that exists means a rename
    costs one entry here rather than a hard import failure.
    """
    for name in names:
        fn = getattr(lib, name, None)
        if fn is not None:
            return fn
    raise AbiMismatch(f"ggml exports none of {', '.join(names)}")


def bind(lib: Any) -> GgmlApi:
    """Declare prototypes against an already-loaded ggml/llama shared library.

    `lib` is duck-typed: a `ctypes.CDLL` in production, a stub in tests. Argument
    types are only applied when the attribute looks like a ctypes function, so a
    stub made of plain Python callables binds unchanged.
    """
    api = GgmlApi(
        lib=lib,
        type_name=_fn(lib, "ggml_type_name"),
        type_size=_fn(lib, "ggml_type_size"),
        blck_size=_fn(lib, "ggml_blck_size"),
        nbytes=_fn(lib, "ggml_nbytes"),
        n_dims=_fn(lib, "ggml_n_dims"),
        nelements=_fn(lib, "ggml_nelements"),
        get_name=_fn(lib, "ggml_get_name"),
        op_desc=_fn(lib, "ggml_op_desc"),
        is_quantized=_fn(lib, "ggml_is_quantized"),
        backend_tensor_get=_fn(lib, "ggml_backend_tensor_get"),
    )
    _declare(api.type_name, [ctypes.c_int], ctypes.c_char_p)
    _declare(api.type_size, [ctypes.c_int], ctypes.c_size_t)
    _declare(api.blck_size, [ctypes.c_int], ctypes.c_int64)
    _declare(api.nbytes, [ctypes.c_void_p], ctypes.c_size_t)
    _declare(api.n_dims, [ctypes.c_void_p], ctypes.c_int)
    _declare(api.nelements, [ctypes.c_void_p], ctypes.c_int64)
    _declare(api.get_name, [ctypes.c_void_p], ctypes.c_char_p)
    _declare(api.op_desc, [ctypes.c_void_p], ctypes.c_char_p)
    _declare(api.is_quantized, [ctypes.c_int], ctypes.c_bool)
    _declare(
        api.backend_tensor_get,
        [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_size_t],
        None,
    )
    return api


def _declare(fn: Any, argtypes: list[Any], restype: Any) -> None:
    if not hasattr(fn, "argtypes"):  # a test stub, or an already-wrapped callable
        return
    fn.argtypes = argtypes
    fn.restype = restype


def expected_nbytes(api: GgmlApi, tensor: GgmlTensor) -> int:
    """ggml's own `ggml_nbytes` formula, recomputed from the mirrored fields.

    Transcribed from `ggml.c`. The block-size branch is not an optimisation: a
    quantized type packs `blck_size` elements into one `type_size` block, so
    row 0's contribution is `ne[0]*nb[0]/blck_size` rather than a stride.
    """
    blck = int(api.blck_size(tensor.type))
    if blck <= 0:
        raise AbiMismatch(f"ggml_blck_size returned {blck}")
    if blck == 1:
        total = int(api.type_size(tensor.type))
        for i in range(GGML_MAX_DIMS):
            total += (int(tensor.ne[i]) - 1) * int(tensor.nb[i])
    else:
        total = int(tensor.ne[0]) * int(tensor.nb[0]) // blck
        for i in range(1, GGML_MAX_DIMS):
            total += (int(tensor.ne[i]) - 1) * int(tensor.nb[i])
    return total


def expected_n_dims(tensor: GgmlTensor) -> int:
    """ggml's `ggml_n_dims`: the highest non-unit dimension, at least 1."""
    for i in reversed(range(GGML_MAX_DIMS)):
        if int(tensor.ne[i]) > 1:
            return i + 1
    return 1


def self_check(api: GgmlApi, pointer: Any) -> None:
    """Verify the mirror against the library, or raise `AbiMismatch`.

    Called on the *first* tensor of a trace and never again — it is a layout
    question, not a per-node one, and the answer cannot change mid-process.
    """
    tensor = api.tensor(pointer)
    actual_bytes = int(api.nbytes(pointer))
    ours = expected_nbytes(api, tensor)
    if actual_bytes != ours:
        raise AbiMismatch(
            "ggml ABI mismatch: ggml_nbytes reports "
            f"{actual_bytes} for this tensor, the mirrored struct implies {ours}. "
            "struct ggml_tensor has changed shape — refusing to write a trace."
        )
    actual_dims = int(api.n_dims(pointer))
    ours_dims = expected_n_dims(tensor)
    if actual_dims != ours_dims:
        raise AbiMismatch(
            f"ggml ABI mismatch: ggml_n_dims reports {actual_dims}, the mirrored "
            f"ne[] implies {ours_dims}."
        )
