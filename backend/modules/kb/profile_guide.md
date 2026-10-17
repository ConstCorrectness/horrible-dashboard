# Writing a site import profile

A profile tells `kb.import` how to turn one website into an offline, searchable, progress-tracked set. Write one when a site has no built-in profile and a generic crawl of the URL's directory would get it wrong.

## 1. Look before you write

- **robots.txt** (`search.read https://site/robots.txt`): disallowed paths, `Crawl-delay`, `Sitemap:` lines, and any `Content-Signal`. If it says `ai-input=no`, an import archives pages but does not index their text for you — tell the user.
- **Bot protection.** If the site answers the headless browser with a bot check (Cloudflare's "Just a moment..."), the import stops at the first page and archives nothing. Don't try to get around it: tell the user the site can't be imported, and that they can still save pages they read in the browser.
- **Where the list of pages is.** A home or archive page that links every article → `frontier: "bfs"` with that page in `start_urls` _and_ in `index_pages` (navigated for links, never kept). A sitemap → `frontier: "sitemap"` (+ `sitemap_url`). Pages only reachable by client-side navigation need the sitemap.
- **The URL shape of a content page** (`/primers/<slug>/`, `/courses/<course>/<chapter>/<lesson>`) — this becomes `allow_patterns`.
- **The content element.** Open one page (`browser.open`, then `browser.snapshot` or `browser.scrape`) and find the element holding the article body without navigation and footer — `article.post-content`, `div.course-content`, `main`. That is `content_selector`. Supported syntax: `tag`, `.class`, `#id`, combinations (`div.course-content`) and descendant spaces (`main article`) — nothing else.

## 2. Fields

| field                              | meaning                                                                                                                                             |
| ---------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| `id`                               | lowercase-dashed, unique; built-in ids can't be replaced                                                                                            |
| `label`, `description`             | shown in the import dialog                                                                                                                          |
| `match`                            | regexes on full URLs that select this profile; named groups (`(?P<course>…)`) become `{course}` placeholders in the other fields                    |
| `root`                             | `{title, url, kind}` — the catalog item standing for the whole import (`kind`: site or course)                                                      |
| `start_urls`                       | where the crawl starts                                                                                                                              |
| `allow_domains`                    | required; the crawl never leaves these                                                                                                              |
| `allow_patterns` / `deny_patterns` | regexes on the URL **path**; a page must match one allow and no deny                                                                                |
| `index_pages`                      | path regexes for frontier-only pages (listing pages)                                                                                                |
| `frontier`                         | `bfs`, `sitemap`, `llms_txt` or `auto` (sitemap if there is one, else links)                                                                        |
| `max_depth`, `max_pages`           | link depth from the start; page cap (≤ 2000)                                                                                                        |
| `keep_scripts`                     | `true` for pages that need their JavaScript to render (SPA lessons, interactive figures); `false` for static pages — smaller, inert archives        |
| `content_selector`                 | the article element (above)                                                                                                                         |
| `reader_view`                      | `true` to show pages in the Docs pane as the `content_selector` element alone, for app-shell sites whose nav, sidebar and overlays bury the article |
| `hierarchy`                        | `flat` (every page under the root), `url_path` (nest by path: needs `path_root` and `path_kinds`), or `links` (nest under the page that linked it)  |
| `path_root`, `path_kinds`          | for `url_path`: e.g. `/courses/{course}` and `["course", "chapter", "lesson"]`                                                                      |
| `page_kind`                        | kind for pages: `article`, `primer`, `lesson`, `paper`…                                                                                             |
| `collections`, `tags`              | where imported items are filed                                                                                                                      |
| `title_strip`                      | regexes removed from page titles (site names, "Primers •")                                                                                          |

## 3. Dry run, then import

1. `kb.saveProfile` with the profile — it is validated (bad regex, unsupported selector, missing domains) before saving.
2. `kb.import` with `profileId` and `maxPages: 3`.
3. When those pages have landed, `kb.search` for something on one of them. Check the passages: real article text (not menus), sensible sections, TeX intact.
4. Fix the profile if not, then `kb.import` again without `maxPages` — it resumes, skipping what is already captured.

Ask the user before a large import (hundreds of pages): it runs a headless browser against their site for a while.
