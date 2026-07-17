# Using the Google Drive tools

Read-only access to the connected account's Drive. Two tools, and they're meant to be
used in order: `google.driveSearch` finds a file id, `google.driveRead` reads it.

## driveSearch

- It searches **file names and document text together**. You pass plain words — do
  **not** pass Drive `q` syntax (`name contains '…'`), that's built for you.
- Matching is on **whole words**, not substrings: `budget` won't match `budgeting`.
- `fullText` only covers documents Drive has actually indexed. A distinctive phrase
  from inside the document beats a generic one.
- It returns `id`, `name`, `type`, `modified`, `url`. The `id` is the only thing
  `driveRead` needs — don't try to read by name or URL.
- `readable_only` defaults to true, so you get back files whose text can actually be
  read. Set it false only when the user wants to know what _exists_ (images, folders,
  sheets) rather than to read it.

## driveRead

Takes a `file_id` from `driveSearch`. It handles the format for you:

| Type                                  | What happens                           |
| ------------------------------------- | -------------------------------------- |
| Google Doc                            | exported as plain text                 |
| PDF                                   | parsed for its text layer              |
| `.txt` / `.md` / `.csv` / JSON / HTML | downloaded as text                     |
| anything else                         | an error — including Sheets and Slides |

Long files are cut at 100 000 characters and come back with `truncated: true`. Say so
if you summarise one; don't imply you read the whole thing.

## Errors worth reading carefully

- **"isn't connected" / "rejected the stored token"** — the user must connect or
  reconnect Google on the home page. You can't fix this; say so and stop.
- **"has no extractable text (probably scanned)"** — a scanned PDF is images. There's
  no OCR here, so the content is genuinely unavailable. Don't report it as empty.
- **"password-protected"** — same: report it, don't retry.
- **"rate limit"** — don't retry in a loop.

## The thing to remember

This account's Drive is often the user's _own private documents_. Read what the task
needs and nothing more, and quote from documents rather than inventing summaries of
files you couldn't read.
