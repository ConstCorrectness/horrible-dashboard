"""Scrive's agent side: section edits, templates, outlines, the tool group, review
and search.

The rules under test are the quiet ones: an edit touches only the bytes it names
(line endings included), a fill never overwrites a written section, a stale
revision is refused, approval is the only way an outline becomes a page, agent
writes are marked for the editor, and no tool in the group can publish.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.modules.scrive import (
    critique,
    index,
    search,
    sections,
    store,
    templates,
    watcher,
)

DOC = """---
title: Hi
tags: [a]
---

Intro para.

## One

Body one.

```python
# not a heading
```

:::{note}
## Not a heading either
:::

### One.sub

Sub body.

## Two

:::{pending}
Explain two.
:::

## Three
Three body.
"""


def run(coro):
    return asyncio.run(coro)


# --- sections -------------------------------------------------------------------------


def test_headings_skip_everything_inside_fences() -> None:
    assert [(h.level, h.text) for h in sections.headings(DOC)] == [
        (2, "One"),
        (3, "One.sub"),
        (2, "Two"),
        (2, "Three"),
    ]


def test_a_section_runs_to_the_next_heading_of_its_level() -> None:
    one = sections.read_section(DOC, "one")
    assert one.startswith("## One\n") and "Sub body." in one and "## Two" not in one


def test_an_edit_leaves_every_other_byte_alone() -> None:
    out = sections.replace_section(DOC, "## Three", "New body.")
    assert out.startswith(DOC.split("## Three")[0])
    assert out.endswith("## Three\n\nNew body.\n")


def test_crlf_files_stay_crlf() -> None:
    crlf = DOC.replace("\n", "\r\n")
    out = sections.fill_pending(crlf, "Two", "Line one.\nLine two.")
    assert "Line one.\r\nLine two." in out
    assert "\n" not in out.replace("\r\n", "")
    assert out.startswith(crlf.split("## Two")[0])


def test_fill_refuses_a_section_that_is_already_written() -> None:
    with pytest.raises(sections.EditError, match="already written"):
        sections.fill_pending(DOC, "One", "overwrite")
    filled = sections.fill_pending(DOC, "Two", "Two, written.")
    assert "{pending}" not in filled
    assert "## Two\n\nTwo, written.\n\n## Three" in filled


def test_the_lead_is_filled_by_an_empty_heading() -> None:
    doc = "---\ntitle: T\n---\n\n:::{pending}\nLead.\n:::\n\n## A\n\nText.\n"
    out = sections.fill_pending(doc, "", "The opening.")
    assert out == "---\ntitle: T\n---\n\nThe opening.\n\n## A\n\nText.\n"


def test_insert_after_text_never_lands_inside_a_directive() -> None:
    out = sections.insert(DOC, "Inserted.", after_text="Not a heading either")
    assert ":::\n\nInserted.\n\n### One.sub" in out


def test_replace_text_must_be_unambiguous() -> None:
    with pytest.raises(sections.EditError, match="occurs 2 times"):
        sections.replace_text("a b a", "a", "c")
    with pytest.raises(sections.EditError, match="not found"):
        sections.replace_text("a b", "z", "c")
    assert sections.replace_text("a b", "b", "c") == "a c"


def test_set_field_edits_one_key_and_keeps_the_rest() -> None:
    out = sections.set_field(DOC, "tags", ["x", "y: z"])
    assert "tags: [x, 'y: z']" in out and "title: Hi" in out
    assert sections.set_field(DOC, "tags", None).count("tags") == 0
    assert (
        sections.set_field("Body\n", "title", "A: b")
        == "---\ntitle: 'A: b'\n---\n\nBody\n"
    )


def test_ops_apply_all_or_nothing() -> None:
    with pytest.raises(sections.EditError, match="op 2"):
        sections.apply_ops(
            DOC,
            [
                {"op": "setFrontmatter", "key": "status", "value": "draft"},
                {"op": "replaceSection", "heading": "Missing", "text": "x"},
            ],
        )
    text, summary = sections.apply_ops(
        DOC,
        [
            {"op": "deleteSection", "heading": "One"},
            {"op": "insert", "text": "## Four\n\nEnd.", "at": "end"},
        ],
    )
    assert (
        "## One" not in text
        and "One.sub" not in text
        and text.endswith("## Four\n\nEnd.\n")
    )
    assert len(summary) == 2


def test_a_missing_heading_lists_what_exists() -> None:
    with pytest.raises(sections.EditError, match="'One', 'One.sub', 'Two', 'Three'"):
        sections.find_heading(DOC, "Nope")


# --- sites on disk ---------------------------------------------------------------------


@pytest.fixture
def scrive_root(tmp_path) -> Path:
    data_dir = Path(os.environ["HORRIBLE_DATA_DIR"])
    root = tmp_path / "scrive"
    (data_dir / "settings.json").write_text(
        json.dumps({"scrive.root": str(root), "scrive.semanticSearch": False})
    )
    return root


@pytest.fixture
def client(scrive_root) -> TestClient:
    from backend.app import app

    return TestClient(app)


@pytest.fixture
def site(client) -> str:
    assert client.post("/api/scrive/sites", json={"id": "blog"}).status_code == 200
    return "blog"


def tool(tool_name: str, /, **args):
    from backend.sdk.registry import registry

    return run(registry.agent_tools[tool_name].handler(args))


# --- templates ----------------------------------------------------------------------------


def test_builtin_templates_carry_inputs_and_sections() -> None:
    ids = {t.id for t in templates.list_templates()}
    assert {
        "tutorial",
        "deep-dive",
        "paper-review",
        "devlog",
        "release-notes",
        "thread-first",
    } <= ids
    info, text, skill = templates.get_template(None, "deep-dive")
    assert info.inputs["idea"].required and "The figure" in info.sections
    assert "{pending}" in text and skill.strip()


def test_instantiating_fills_inputs_and_drops_the_template_block(site) -> None:
    with pytest.raises(templates.TemplateError, match="needs: topic"):
        templates.instantiate(site, "tutorial", {})
    _info, text = templates.instantiate(site, "tutorial", {"topic": "caching"})
    assert "template:" not in text and "caching" in text and "{{" not in text
    assert "developers who have not done this before" in text  # a default


def test_a_page_from_a_template_over_http(client, site) -> None:
    res = client.post(
        f"/api/scrive/sites/{site}/pages",
        json={
            "kind": "post",
            "title": "Cache it",
            "template": "tutorial",
            "inputs": {"topic": "caching"},
        },
    )
    assert res.status_code == 200, res.text
    content = res.json()["content"]
    assert "title: Cache it" in content and "status: draft" in content
    assert "## What you will build" in content
    bad = client.post(
        f"/api/scrive/sites/{site}/pages", json={"title": "x", "template": "tutorial"}
    )
    assert bad.status_code == 400


def test_save_as_template_and_a_site_template_wins(client, site, scrive_root) -> None:
    page = store.create_page(
        site, "post", "Mine", body="---\ntags: [x]\n---\n\n## Shape\n\nText.\n"
    )
    res = client.post(
        f"/api/scrive/sites/{site}/templates",
        json={"path": page.meta.path, "id": "tutorial", "name": "My tutorial"},
    )
    assert res.status_code == 200, res.text
    saved = (scrive_root / site / "templates" / "tutorial.md").read_text()
    assert "template:" in saved and "title:" not in saved and "tags: [x]" in saved
    listed = client.get(f"/api/scrive/templates?site={site}").json()
    tutorial = next(t for t in listed if t["id"] == "tutorial")
    assert tutorial["source"] == "site" and tutorial["name"] == "My tutorial"
    # Templates are never pages.
    assert all(
        not p["path"].startswith("templates/")
        for p in client.get(f"/api/scrive/sites/{site}/pages").json()
    )


# --- outlines ------------------------------------------------------------------------------


def test_an_outline_becomes_a_page_only_when_approved(
    client, site, scrive_root
) -> None:
    before = {m.path for m in store.list_pages(site)}
    result = tool(
        "scrive.proposeOutline",
        site=site,
        title="Priors, visually",
        lead="Why priors matter",
        tags=["stats"],
        sections=[
            {"heading": "The intuition", "intent": "Plain words first"},
            {
                "heading": "## The figure",
                "intent": "A scene",
                "figure": "posterior surface",
            },
        ],
    )
    outline_id = result["outline_id"]
    assert {m.path for m in store.list_pages(site)} == before  # nothing written yet

    listed = client.get(f"/api/scrive/outlines?site={site}&status=proposed").json()
    assert [o["id"] for o in listed] == [outline_id]

    edited = client.put(
        f"/api/scrive/sites/{site}/outlines/{outline_id}",
        json={"title": "Priors, seen"},
    )
    assert edited.json()["title"] == "Priors, seen"

    res = client.post(
        f"/api/scrive/sites/{site}/outlines/{outline_id}/approve", json={}
    )
    assert res.status_code == 200, res.text
    page = res.json()["page"]
    text = page["content"]
    assert "title: Priors, seen" in text and "tags: [stats]" in text
    assert "## The figure" in text and "Figure: posterior surface" in text
    assert [p.heading for p in sections.pending_blocks(text)] == [
        "",
        "The intuition",
        "The figure",
    ]
    assert res.json()["outline"]["page"] == page["meta"]["path"]

    again = client.post(
        f"/api/scrive/sites/{site}/outlines/{outline_id}/approve", json={}
    )
    assert again.status_code == 400


def test_an_outline_from_a_template_checks_its_inputs(site) -> None:
    result = tool(
        "scrive.proposeOutline",
        site=site,
        title="x",
        template="tutorial",
        sections=[{"heading": "A", "intent": "b"}],
    )
    assert "needs: topic" in result["error"]


def test_a_discarded_outline_cannot_be_approved(client, site) -> None:
    outline_id = tool("scrive.proposeOutline", site=site, title="t", sections=["Only"])[
        "outline_id"
    ]
    assert (
        client.post(
            f"/api/scrive/sites/{site}/outlines/{outline_id}/discard"
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/scrive/sites/{site}/outlines/{outline_id}/approve", json={}
        ).status_code
        == 400
    )


# --- the tool group --------------------------------------------------------------------------


def test_no_scrive_tool_publishes() -> None:
    """The group's whole surface, pinned: adding a tool means deciding here that it
    does not send anything anywhere."""
    from backend.sdk.registry import registry

    names = sorted(n for n in registry.agent_tools if n.startswith("scrive."))
    assert names == [
        "scrive.createPage",
        "scrive.critiquePage",
        "scrive.draftSocial",
        "scrive.editPage",
        "scrive.fillSection",
        "scrive.listPages",
        "scrive.makeClip",
        "scrive.proposeOutline",
        "scrive.readPage",
        "scrive.searchSite",
        "scrive.writeAppFile",
        "scrive.writeScene",
    ]
    for name in names:
        assert not any(
            w in name.lower()
            for w in ("publish", "send", "post", "push", "upload", "approve")
        )
    writers = {n for n in names if registry.agent_tools[n].side_effect}
    assert writers == {
        "scrive.createPage",
        "scrive.draftSocial",
        "scrive.editPage",
        "scrive.fillSection",
        "scrive.makeClip",
        "scrive.writeAppFile",
        "scrive.writeScene",
    }


def test_fill_section_walks_the_placeholders_and_marks_agent_writes(
    site, scrive_root
) -> None:
    created = tool(
        "scrive.createPage",
        site=site,
        title="Cache it",
        template="devlog",
        inputs={"project": "Scrive"},
    )
    path = created["path"]
    assert created["skill"] and [p["heading"] for p in created["pending"]] == [
        "(lead)",
        "What shipped",
        "What I learned",
        "What is next",
    ]
    step = tool(
        "scrive.fillSection", site=site, path=path, heading="", content="The lead."
    )
    assert step["remaining"][0]["heading"] == "What shipped"
    assert "error" in tool(
        "scrive.fillSection", site=site, path=path, heading="", content="again"
    )

    # The watcher reports the write as the agent's — once.
    base = scrive_root
    event = watcher.event_for(base, base / site / path, "modified")
    assert (
        event is not None
        and event.origin == "agent"
        and event.revision == step["revision"]
    )
    assert watcher.event_for(base, base / site / path, "modified").origin == ""


def test_edit_page_refuses_a_stale_revision(site) -> None:
    page = store.create_page(site, "post", "Edit me", body="## A\n\nOld.\n")
    stale = tool(
        "scrive.editPage",
        site=site,
        path=page.meta.path,
        base_revision="0" * 16,
        ops=[{"op": "replaceText", "find": "Old.", "text": "New."}],
    )
    assert "changed since you read it" in stale["error"]
    ok = tool(
        "scrive.editPage",
        site=site,
        path=page.meta.path,
        base_revision=page.meta.revision,
        ops=[{"op": "replaceText", "find": "Old.", "text": "New."}],
    )
    assert "error" not in ok and store.read_page(site, page.meta.path).content.endswith(
        "## A\n\nNew.\n"
    )


def test_read_page_reports_outline_and_pending(site) -> None:
    page = store.create_page(site, "page", "Doc", body=DOC)
    read = tool("scrive.readPage", site=site, path=page.meta.path)
    assert read["revision"] == page.meta.revision
    assert [o["heading"] for o in read["outline"]] == ["One", "One.sub", "Two", "Three"]
    assert read["pending"] == [{"heading": "Two", "intent": "Explain two."}]
    section = tool("scrive.readPage", site=site, path=page.meta.path, section="Three")
    assert section["text"] == "## Three\nThree body.\n"
    assert tool("scrive.readPage", site=site, template="devlog")["skill"]


def test_write_scene_stays_in_scenes_and_does_not_clobber(site, scrive_root) -> None:
    source = "export default function S() { return null; }\n"
    done = tool("scrive.writeScene", site=site, name="orbit-demo", source=source)
    assert done["path"] == "scenes/orbit-demo.tsx"
    assert (scrive_root / site / "scenes" / "orbit-demo.tsx").read_text() == source
    assert (
        "already exists"
        in tool("scrive.writeScene", site=site, name="orbit-demo", source=source)[
            "error"
        ]
    )
    assert "error" in tool("scrive.writeScene", site=site, name="../x", source=source)
    assert (
        "export default"
        in tool("scrive.writeScene", site=site, name="y", source="const a = 1")["error"]
    )


def test_the_site_is_inferred_only_when_there_is_one(client, site) -> None:
    assert tool("scrive.listPages")["site"] == site
    client.post("/api/scrive/sites", json={"id": "other"})
    assert "sites" in tool("scrive.listPages")
    assert "which site" in tool("scrive.readPage", path="index.md")["error"]


# --- review ---------------------------------------------------------------------------------


def test_critique_flags_what_an_editor_would(site, scrive_root) -> None:
    (scrive_root / site / "media").mkdir(exist_ok=True)
    (scrive_root / site / "media" / "ok.png").write_bytes(b"x")
    text = """---
title: T
---

Our method is 40% faster than everything.

## A

![](../media/ok.png)

![a missing one](../media/nope.png)

#### Deep

$$
x = y
$$

```
no language
```

:::{pending}
Write me.
:::
"""
    rules = {
        (f.rule, f.severity)
        for f in critique.critique(text, scrive_root / site, "posts/x.md")
    }
    assert ("dead-link", "error") in rules
    assert ("alt-text", "warning") in rules
    assert ("heading-skip", "warning") in rules
    assert ("pending", "warning") in rules
    assert ("uncited", "info") in rules
    assert ("unexplained-math", "info") in rules
    assert ("code-language", "info") in rules
    assert ("description", "info") in rules
    clean = "---\ntitle: T\ndescription: d\ntags: [a]\n---\n\nIt is [40% faster](https://x.org).\n"
    assert critique.critique(clean, scrive_root / site, "posts/y.md") == []


# --- search ---------------------------------------------------------------------------------


def test_keyword_search_finds_posts_and_scenes(site, scrive_root) -> None:
    store.create_page(
        site,
        "post",
        "Bayesian priors",
        body="## Shrinkage\n\nA prior pulls estimates in.\n",
    )
    store.create_page(site, "post", "Cooking", body="Pasta.\n")
    (scrive_root / site / "scenes").mkdir(exist_ok=True)
    (scrive_root / site / "scenes" / "prior-surface.tsx").write_text(
        "// prior surface\nexport default 1\n"
    )
    result = run(search.search_site(site, "prior", 5))
    paths = [h["path"] for h in result["hits"]]
    assert (
        paths[0].endswith("bayesian-priors.md") and "scenes/prior-surface.tsx" in paths
    )
    assert not any("cooking" in p for p in paths)
    assert "semantic search is off" in result["note"]


def test_index_chunks_say_where_they_come_from() -> None:
    chunks = index.chunks("posts/a.md", DOC)
    assert chunks[0].startswith("Hi\nIntro para.")
    assert any(c.startswith("Hi — One\n") for c in chunks)


def test_the_index_never_stores_hash_fallback_vectors(site, monkeypatch) -> None:
    store.create_page(site, "post", "Indexed", body="Words.\n")
    written = []

    async def fallback(texts, **_kw):
        return [[0.0] * 4 for _ in texts], "local-fallback"

    monkeypatch.setattr(index, "get_embeddings", fallback)
    monkeypatch.setattr(index, "_write", lambda *a: written.append(a))
    paths = {m.path for m in store.list_pages(site)}
    assert run(index.index_pages(site, paths)) is False
    assert written == [] and "keywords only" in index.unavailable[site]
