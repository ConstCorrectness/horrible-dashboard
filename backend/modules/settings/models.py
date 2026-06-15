from typing import Any

from pydantic import BaseModel

# Settings keys are namespaced like contributed ids: `<module>.<name>` (plugins
# use `<pluginId>.<name>`). The backend stores values opaquely — the schema,
# types, and defaults live on the frontend (see docs/modules/settings.md).
SETTING_KEY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"


class SettingsValues(BaseModel):
    """The full bag of user-overridden setting values, keyed by setting key."""

    values: dict[str, Any]


class SettingValue(BaseModel):
    """A single setting's value (request body for PUT, echoed on response)."""

    value: Any
