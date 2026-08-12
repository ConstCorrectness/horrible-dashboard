from typing import Any

from pydantic import BaseModel

# Settings keys are namespaced like contributed ids: `<module>.<name>` (plugins
# use `<pluginId>.<name>`). The backend stores values opaquely — the schema,
# types, and defaults live on the frontend (see docs/modules/settings.md).
SETTING_KEY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"

#: Key suffixes whose value is never handed back over HTTP.
#:
#: `GET /api/settings` returns the whole bag to whatever asked for it — which
#: includes every third-party frontend plugin, all of which load unsandboxed at
#: boot. A Hugging Face token or a Kaggle API key sitting in that response is a
#: credential handed to code the user installed from a catalog.
#:
#: The long-run answer is that such values belong in a connector (Fernet-encrypted
#: in `secrets.db`, never handed to the browser at all), and the ones here predate
#: connectors. Until each is migrated, the value stays writable and stays readable
#: *by the backend* (`get_value` reads the file, not this route) and simply stops
#: being served. Matching on the key shape rather than on a declaration is
#: deliberate: declarations live on the frontend, so a backend that trusted them
#: would be asking the untrusted side which of its own values are sensitive.
SECRET_KEY_SUFFIXES = (
    ".key",
    ".token",
    ".secret",
    ".password",
    ".apikey",
    "secret",
    "token",
    "password",
)


def is_secret_key(key: str) -> bool:
    lower = key.lower()
    return any(lower.endswith(suffix) for suffix in SECRET_KEY_SUFFIXES)


class SettingsValues(BaseModel):
    """The bag of user-overridden setting values, keyed by setting key.

    Not quite the full bag: values whose key is secret-shaped are blanked (see
    `SECRET_KEY_SUFFIXES`). `secret_keys` names the ones that *are* set, so the UI
    can say "saved" without ever holding the credential.
    """

    values: dict[str, Any]
    secretKeys: list[str] = []


class SettingValue(BaseModel):
    """A single setting's value (request body for PUT, echoed on response)."""

    value: Any
