from pydantic import BaseModel, Field

# The stored blob's schema tag and version, so a future format change can be
# migrated rather than silently misread (mirrors the frame layout blob).
KEYMAP_SCHEMA = "horrible.keymap"
KEYMAP_VERSION = 1


class KeymapBinding(BaseModel):
    """One user customization.

    Three shapes, all of them this model:
      * add       — `key` + `command`
      * rebind    — an add, plus a `disabled` entry for the default it replaces
      * disable   — `key` + `command` + `disabled: true`

    Disabling is explicit rather than VS Code's `-command` prefix idiom, because a
    leading `-` inside a command id is indistinguishable from a command that
    starts with one.
    """

    key: str = Field(min_length=1, max_length=120)
    command: str = Field(min_length=1, max_length=200)
    when: str | None = Field(default=None, max_length=400)
    disabled: bool = False


class Keymap(BaseModel):
    """The whole user keymap. Defaults and validation live on the frontend, the
    same split the settings store uses — this endpoint stores what it is given."""

    schema_: str = Field(default=KEYMAP_SCHEMA, alias="schema")
    version: int = KEYMAP_VERSION
    bindings: list[KeymapBinding] = Field(default_factory=list, max_length=1000)

    model_config = {"populate_by_name": True}
