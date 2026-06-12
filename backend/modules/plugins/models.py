"""Models for the plugin marketplace: catalog, install state, scoped storage."""

from pathlib import PurePosixPath
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

PLUGIN_ID_PATTERN = r"^[a-z0-9][a-z0-9-]{0,63}$"
STORAGE_KEY_PATTERN = r"^[A-Za-z0-9._-]{1,128}$"
MANIFEST_FILENAME = "horrible-plugin.json"


class PluginManifest(BaseModel):
    """A plugin package's `horrible-plugin.json` (camelCase on disk and wire)."""

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(pattern=PLUGIN_ID_PATTERN)
    name: str
    version: str
    description: str = ""
    author: str = ""
    entry: str
    sdk_version: int = Field(alias="sdkVersion")
    required_capabilities: list[str] = Field(
        default_factory=list, alias="requiredCapabilities"
    )
    permissions: list[str] = Field(default_factory=list)

    @field_validator("entry")
    @classmethod
    def _entry_stays_inside_package(cls, value: str) -> str:
        if value.startswith(("/", "\\")) or ":" in value:
            raise ValueError("entry must be a relative path")
        if ".." in PurePosixPath(value.replace("\\", "/")).parts:
            raise ValueError("entry must not contain '..' segments")
        return value


class CatalogResponse(BaseModel):
    plugins: list[PluginManifest]


class InstalledPlugin(BaseModel):
    manifest: PluginManifest
    enabled: bool


class InstalledListResponse(BaseModel):
    plugins: list[InstalledPlugin]


class InstallRequest(BaseModel):
    id: str = Field(pattern=PLUGIN_ID_PATTERN)


class EnabledRequest(BaseModel):
    enabled: bool


class StorageValue(BaseModel):
    """PUT body for a storage entry; `value` is an opaque JSON value."""

    value: Any


class StorageEntry(BaseModel):
    key: str
    value: Any


class OkResponse(BaseModel):
    ok: bool = True
