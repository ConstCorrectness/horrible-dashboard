# Web search

Five tools. Reach for them in this order.

## Pick the right tool

| Want                                              | Tool                                    | Cost    |
| ------------------------------------------------- | --------------------------------------- | ------- |
| A URL you don't have, or current information      | `search.web`                            | < 1s    |
| The full text of a page you have a URL for        | `search.read`                           | ~1s     |
| Framework/API/library documentation               | `search.index` first, then `search.web` | instant |
| A hard question where `search.web` came back thin | `search.deep`                           | 2–8s    |
| To refresh the local index (only when asked)      | `search.crawl`                          | minutes |

**`search.web` is the default.** It fans out across every configured engine in
parallel and fuses the rankings, so one call already covers several engines — running
it three times with reworded queries duplicates work the pipeline does internally.

**`search.deep` is not "search.web but better".** It rewrites the query, reads the
top pages and reranks them, and it takes seconds. Use it when the cheap search
genuinely failed, not as a first move.

## Searching well

- Search with the words a **page answering the question would contain**, not the
  words the user asked with. "why is my loss nan pytorch" finds forum noise;
  "pytorch nan loss debugging gradient overflow" finds the answer.
- Include versions, exact error strings and proper names verbatim.
- `site` narrows to one domain (`site: "arxiv.org"`). Use it when you know where the
  answer lives; drop it the moment a search comes back empty, because a wrong `site`
  looks exactly like a topic nobody has written about.
- `freshness` (`day`/`week`/`month`/`year`) is for genuinely time-sensitive
  questions. On an evergreen topic it silently hides the best sources.

## Reading results

Each hit carries `found_by` — which engines surfaced it. A page several independent
engines agree on is a stronger bet than one that appeared once.

A snippet is not evidence. Before you state something as fact or quote it, call
`search.read` on the URL. Snippets are truncated mid-sentence and frequently describe
a page's navigation rather than its content.

Always cite the URL you actually read.

## The local index

`search.index` searches pages this node crawled itself — a curated set of ML sites,
blogs and API docs. It's instant and free, so it's the right first call for
documentation questions.

Its one trap: **empty means "not in the index", never "not on the web."** The index
only covers seeded sites. Fall through to `search.web` rather than concluding
anything from a miss.

## When search doesn't work

Results come back with a `notes` array. Read it. `"tavily: no API key"` means the
user hasn't configured that provider — search still works through the keyless
fallback, so don't report it as broken; mention it only if results were poor.
`"no search provider is available"` is the real failure, and the fix is the Search
connector on the home page.

Never tell the user their answer doesn't exist on the web because one search was
empty. Reword and try once more first.
