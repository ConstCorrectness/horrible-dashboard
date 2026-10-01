"""Every Discover source, by id. One adapter per upstream catalog."""

from __future__ import annotations

from backend.modules.discover.sources.arxiv import ArxivSource
from backend.modules.discover.sources.base import DiscoverSource
from backend.modules.discover.sources.catalogs import McpSource, PluginsSource
from backend.modules.discover.sources.docs import DocsSource
from backend.modules.discover.sources.github import GitHubSource
from backend.modules.discover.sources.hf import HubSource
from backend.modules.discover.sources.kaggle import KaggleSource
from backend.modules.discover.sources.papers import PapersSource
from backend.modules.discover.sources.skills import SkillsSource

SOURCES: dict[str, DiscoverSource] = {
    s.id: s
    for s in (
        HubSource(),
        PapersSource(),
        ArxivSource(),
        GitHubSource(),
        KaggleSource(),
        McpSource(),
        PluginsSource(),
        SkillsSource(),
        DocsSource(),
    )
}
