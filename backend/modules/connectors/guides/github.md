# Using the GitHub tools

The connected account's own repos are reachable, private ones included.

## Pick the right tool

- Know the repo and path? Use `github.readFile`. Don't search for a file you can already name.
- Looking for where something is _defined or used_? `github.searchCode`.
- Looking for a _project_? `github.searchRepos`.
- "My repos", "what am I working on" → `github.listRepos`.

## github.searchCode has sharp edges

These are the failure modes, not style preferences:

- **It only indexes the default branch.** Code on a feature branch is invisible. To read from a branch, use `github.readFile` with `ref`.
- **A bare common word returns nothing useful.** Scope it: pass `repo` ("owner/name"), or put `org:name` / `language:python` / `path:src/` in the query.
- **It matches whole words, not substrings.** `auth` will not match `authenticate`. Search the term that actually appears in the source.
- **No regex.** `filename:` and `extension:` work; wildcards don't.

Useful qualifiers inside `query`: `repo:owner/name`, `org:name`, `language:python`, `path:backend/`, `filename:routes.py`, `extension:ts`.

## Reading files

`github.readFile` takes `repo` ("owner/name") and `path` (repo-relative, no leading slash). A directory path returns its entries instead of content — that's the way to explore an unfamiliar repo. Files are truncated at 100 KB; `truncated: true` says so.

## Issues

`github.listIssues` returns pull requests too — that's GitHub's REST API, not a bug. Check `is_pull_request` before telling the user something is an issue.

## When a tool returns an error

- "isn't connected" / "rejected the stored token" → the user must connect or reconnect GitHub from the home page. You cannot fix this yourself; say so and stop.
- "rate limit" → don't retry in a loop; report it.
