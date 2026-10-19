"""Generate the settings-schema fixture for HorribleAssault.

    PYTHONPATH=. uv run python scripts/gen_hassault_settings_vectors.py

`settings_schema.py` is the one table of every `hassault.*` key: type, range,
default, scope. This writes it out for the two suites that cannot import Python:

- `settings-schema.test.ts` checks the pane manifest declares a row for every
  key with the same type and default;
- `apps/native-fps/tests/conformance.rs` feeds each key the native client reads
  to `Settings::from_values` below, above and inside its range.

Regenerate after any change to the schema, then fix whichever side disagrees.
"""

from __future__ import annotations

import json
from pathlib import Path

from backend.modules.hassault import settings_schema

OUT = Path("packages/core/src/modules/hassault/__tests__/settings-vectors.json")


def main() -> None:
    doc = {
        "generator": "scripts/gen_hassault_settings_vectors.py",
        **settings_schema.to_dict(),
    }
    OUT.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {OUT} ({len(settings_schema.SCHEMA)} keys)")


if __name__ == "__main__":
    main()
