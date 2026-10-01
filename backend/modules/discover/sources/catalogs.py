"""The app's own two catalogs: MCP servers and plugins.

Both already have a module that owns them — `mcp.catalog` (the official registry
plus a curated overlay) and the plugins module (a local catalog directory). These
adapters only translate; installing stays in those modules' own panes, because an MCP
install has secret environment variables to collect and a plugin install needs a
reload banner, and neither belongs in a generic browser.
"""

from __future__ import annotations

from backend.modules.discover.models import (
    Badge,
    DiscoverDetail,
    DiscoverItem,
    Fact,
    KindSpec,
    Link,
    SourceSpec,
)
from backend.modules.discover.sources.base import Query, SourceResult, clip

# --- MCP ------------------------------------------------------------------------


def mcp_item(entry: object) -> DiscoverItem:
    from backend.modules.mcp.catalog import CatalogEntry  # noqa: PLC0415

    assert isinstance(entry, CatalogEntry)
    badges: list[Badge] = []
    if entry.source == "curated":
        badges.append(Badge(label="curated", tone="info", title=entry.note or None))
    # "npm: @scope/pkg" → "npm"; a remote is "hosted".
    kinds = sorted(
        {
            "hosted" if i.kind == "remote" else i.label.split(":", 1)[0]
            for i in entry.installs
        }
    )
    for label in kinds[:3]:
        badges.append(Badge(label=label))
    if entry.installs and all(i.unsupported for i in entry.installs):
        badges.append(Badge(label="no runnable option", tone="warn"))
    facts = [Fact(label="registry name", value=entry.name)]
    if entry.version:
        facts.append(Fact(label="version", value=entry.version))
    for option in entry.installs:
        how = option.url or " ".join([option.command, *option.args]).strip()
        value = how or option.unsupported or "—"
        secrets = [e.name for e in option.env if e.secret]
        if secrets:
            value += f" · secrets: {', '.join(secrets)}"
        facts.append(Fact(label=f"install · {option.label}", value=value))
    return DiscoverItem(
        source="mcp",
        kind="server",
        id=entry.name,
        title=entry.title or entry.name,
        subtitle=entry.name + (f" · v{entry.version}" if entry.version else ""),
        description=clip(entry.note or entry.description, 400),
        url=entry.repository or None,
        badges=badges,
        facts=facts,
    )


class McpSource:
    id = "mcp"

    async def spec(self) -> SourceSpec:
        return SourceSpec(
            id=self.id,
            label="MCP servers",
            kinds=[
                KindSpec(
                    id="server",
                    label="Servers",
                    search_placeholder="Search the MCP registry…",
                )
            ],
        )

    async def list(self, query: Query) -> SourceResult:
        from backend.modules.mcp import catalog  # noqa: PLC0415

        live = await catalog.search_registry(query.q, limit=60)
        curated = [e for e in catalog.curated_entries() if catalog.matches(e, query.q)]
        entries = catalog.merge(curated, live)
        degraded = not live
        return SourceResult(
            items=[mcp_item(e) for e in entries],
            feed_label=(
                f"MCP servers matching “{query.q}”"
                if query.q
                else "Curated MCP servers, then the official registry's latest"
            ),
            total=None,
            status="degraded" if degraded else "ok",
            message=(
                "The official MCP registry didn't answer; showing the curated list only."
                if degraded
                else None
            ),
        )

    async def detail(
        self, kind: str, item_id: str, known: DiscoverItem | None
    ) -> DiscoverDetail:
        from backend.modules.mcp import catalog  # noqa: PLC0415

        item = known
        body = None
        for entry in catalog.curated_entries():
            if entry.name == item_id:
                item = mcp_item(entry)
                body = "\n\n".join(x for x in (entry.note, entry.description) if x)
        if item is None:
            for entry in await catalog.search_registry(item_id, limit=10):
                if entry.name == item_id:
                    item = mcp_item(entry)
                    body = entry.description
        if item is None:
            from backend.modules.discover.sources.base import SourceUnavailable  # noqa: PLC0415

            raise SourceUnavailable(f"{item_id} isn't in the registry")
        links = [Link(label="Repository", url=item.url)] if item.url else []
        return DiscoverDetail(
            item=item,
            body=body or item.description,
            body_format="text",
            facts=item.facts,
            links=links,
        )


# --- plugins --------------------------------------------------------------------


class PluginsSource:
    id = "plugins"

    async def spec(self) -> SourceSpec:
        return SourceSpec(
            id=self.id,
            label="Plugins",
            kinds=[
                KindSpec(
                    id="plugin", label="Plugins", search_placeholder="Filter plugins…"
                )
            ],
        )

    def _items(self) -> list[DiscoverItem]:
        from backend.modules.plugins import routes as plugins  # noqa: PLC0415

        catalog = {m.id: m for m in plugins.catalog().plugins}
        installed = {p.manifest.id: p for p in plugins.installed().plugins}
        items: list[DiscoverItem] = []
        for plugin_id in sorted(set(catalog) | set(installed)):
            meta = (
                installed[plugin_id].manifest
                if plugin_id in installed
                else catalog[plugin_id]
            )
            badges: list[Badge] = []
            if plugin_id in installed:
                enabled = installed[plugin_id].enabled
                badges.append(
                    Badge(
                        label="enabled" if enabled else "disabled",
                        tone="ok" if enabled else "idle",
                    )
                )
                if plugin_id in catalog and catalog[plugin_id].version != meta.version:
                    badges.append(
                        Badge(
                            label=f"update → v{catalog[plugin_id].version}", tone="info"
                        )
                    )
            elif plugin_id in catalog:
                badges.append(Badge(label="available"))
            items.append(
                DiscoverItem(
                    source="plugins",
                    kind="plugin",
                    id=plugin_id,
                    title=meta.name,
                    subtitle=f"v{meta.version}"
                    + (f" · {meta.author}" if meta.author else ""),
                    description=clip(meta.description, 400),
                    author=meta.author or None,
                    badges=badges,
                    facts=[
                        Fact(label="id", value=plugin_id),
                        Fact(
                            label="permissions",
                            value=", ".join(meta.permissions) or "none",
                        ),
                        Fact(label="SDK version", value=str(meta.sdk_version)),
                    ],
                )
            )
        return items

    async def list(self, query: Query) -> SourceResult:
        items = self._items()
        if needle := query.q.lower():
            items = [
                i
                for i in items
                if needle in f"{i.title} {i.description} {i.id}".lower()
            ]
        return SourceResult(
            items=items,
            feed_label="Plugins in this node's catalog and installed",
            total=len(items),
            status="ok" if items or query.q else "degraded",
            message=None
            if items or query.q
            else "No plugin catalog is configured. Point HORRIBLE_PLUGIN_CATALOG at a directory of plugin packages.",
        )

    async def detail(
        self, kind: str, item_id: str, known: DiscoverItem | None
    ) -> DiscoverDetail:
        item = known or next((i for i in self._items() if i.id == item_id), None)
        if item is None:
            from backend.modules.discover.sources.base import SourceUnavailable  # noqa: PLC0415

            raise SourceUnavailable(f"no plugin {item_id}")
        return DiscoverDetail(
            item=item, body=item.description, body_format="text", facts=item.facts
        )
