---
name: kb-curator
description: Add a website to the horrible-dashboard knowledge base — write and test a site import profile (scope, content selector, hierarchy), then import it for offline reading and RAG. Use when asked to grab, import, archive or index a blog, course or docs site into the knowledge base.
---

# Curating the knowledge base

The knowledge base (`backend/modules/kb/`, docs in `docs/modules/kb.mdx`) imports whole sites through **profiles**: a crawl recipe plus the catalog shape. Built-ins live in `backend/modules/kb/profiles.json`; user profiles in the `kb_profiles` table.

**The field reference and the authoring steps are in `backend/modules/kb/profile_guide.md` — read it first.** The in-app agent gets the same file from `kb.profiles` with `howToWrite: true`, so there is one guide, not two.

## Against a running backend

The dev backend listens on `http://127.0.0.1:8100` (see the `running-the-app` skill).

```bash
curl -s "http://127.0.0.1:8100/api/kb/profiles/match?url=https://example.com/blog/post"
```

```bash
curl -s -X POST http://127.0.0.1:8100/api/kb/profiles -H "Content-Type: application/json" -d @profile.json
```

```bash
curl -s -X POST http://127.0.0.1:8100/api/kb/imports -H "Content-Type: application/json" -d "{\"profile_id\": \"my-site\", \"max_pages\": 3}"
```

Then check what landed:

```bash
curl -s -X POST http://127.0.0.1:8100/api/kb/search -H "Content-Type: application/json" -d "{\"query\": \"something on one of those pages\"}"
```

## Adding a built-in profile

For a site worth shipping to everyone, add it to `profiles.json` and extend `test_builtin_profiles_validate` coverage in `backend/tests/test_kb.py` if it uses a new field. Keep `allow_domains` tight and `max_pages` honest; profiles crawl real sites.

## Rules

- Respect the site: robots.txt is honoured automatically, `docviewer.crawlDelay` (≥ 1 s) applies, and `Content-Signal: ai-input=no` stops text indexing. Don't work around any of these.
- Imported content is for the user's personal offline reading and RAG — never publish or share it.
