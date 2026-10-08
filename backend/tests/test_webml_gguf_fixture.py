"""The browser GGUF reader's conformance fixture, read from this side.

`packages/webml/src/gguf/__tests__/fixtures/tiny-qwen3.gguf` was written by
llama.cpp's own writer, and `tiny-qwen3.expected.json` beside it is what
`gguf.py` read from it (scripts/gen_webml_gguf_fixture.py). The TS suite checks
`parse.ts` against that JSON; this test checks `gguf.py` still produces it. A
change to either reader that changes what a file means fails one of the two.
"""

from __future__ import annotations

import json
from pathlib import Path

from backend.modules.interpretability.gguf import read_header

FIXTURES = (
    Path(__file__).resolve().parents[2] / "packages/webml/src/gguf/__tests__/fixtures"
)


def _jsonable(value: object) -> object:
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, int) and not isinstance(value, bool) and abs(value) > 2**53:
        return {"bigint": str(value)}
    return value


def test_gguf_py_still_reads_the_fixture_as_recorded() -> None:
    expected = json.loads((FIXTURES / "tiny-qwen3.expected.json").read_text("utf-8"))
    h = read_header(FIXTURES / "tiny-qwen3.gguf")
    assert {
        "version": h.version,
        "alignment": h.alignment,
        "dataOffset": h.data_offset,
        "fileSize": h.file_size,
        "metadata": {k: _jsonable(v) for k, v in h.metadata.items()},
        "tensors": [
            {
                "name": t.name,
                "shape": list(t.shape),
                "type": t.type_id,
                "typeName": t.type_name,
                "offset": t.offset,
                "elements": t.elements,
                "bytes": t.n_bytes,
            }
            for t in h.tensors
        ],
    } == expected
