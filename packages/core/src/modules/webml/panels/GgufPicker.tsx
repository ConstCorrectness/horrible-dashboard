/**
 * Choosing a GGUF file from the Hub for the playground: a repo, its `.gguf` files
 * with their sizes, and — before anything large downloads — what the chosen file's
 * header says: the architecture and quantization, or every reason this window's
 * engine cannot run it.
 */
import { useEffect, useState } from 'react';

import {
  formatBytes,
  ggufModelId,
  inspectHubGguf,
  listRepoGgufs,
  parseGgufModelId,
  type GgufInspection,
  type HubGguf,
} from '@horrible/webml';

export interface GgufChoice {
  /** `gguf:` model id, or '' while nothing is chosen. */
  id: string;
  /** The header verdict; null while it is read (or for a file already on disk). */
  inspection: GgufInspection | null;
}

type Listing =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'ok'; files: HubGguf[] }
  | { kind: 'error'; message: string };

export function GgufPicker({
  initial,
  onChange,
}: {
  /** A `gguf:` id to start from (the default model setting). */
  initial: string;
  onChange: (choice: GgufChoice) => void;
}) {
  const start = parseGgufModelId(initial);
  const [repo, setRepo] = useState(start?.repo ?? '');
  const [listing, setListing] = useState<Listing>({ kind: 'idle' });
  const [file, setFile] = useState(start?.file ?? '');
  const [inspection, setInspection] = useState<GgufInspection | null>(null);
  const [inspectError, setInspectError] = useState<string | null>(null);

  const id = repo.trim() && file ? ggufModelId(repo.trim(), file) : '';

  useEffect(() => {
    onChange({ id, inspection });
  }, [id, inspection, onChange]);

  // Read the chosen file's header (a few MB over Range requests).
  useEffect(() => {
    setInspection(null);
    setInspectError(null);
    if (!id) return;
    const ref = parseGgufModelId(id);
    if (!ref) return;
    let live = true;
    inspectHubGguf(ref.repo, ref.file)
      .then((r) => live && setInspection(r))
      .catch(
        (err: unknown) => live && setInspectError(err instanceof Error ? err.message : String(err)),
      );
    return () => {
      live = false;
    };
  }, [id]);

  const find = async () => {
    const r = repo.trim();
    if (!r) return;
    setListing({ kind: 'loading' });
    try {
      const files = (await listRepoGgufs(r)).filter((f) => !f.isProjector);
      setListing({ kind: 'ok', files });
      if (!files.some((f) => f.path === file)) setFile(files[0]?.path ?? '');
    } catch (err) {
      const why = err instanceof Error ? err.message : String(err);
      setListing({ kind: 'error', message: `could not list ${r} on huggingface.co: ${why}` });
    }
  };

  return (
    <>
      <div className="webml-row">
        <input
          type="text"
          aria-label="Hub repo with GGUF files"
          placeholder="owner/name (GGUF repo)"
          value={repo}
          onChange={(e) => setRepo(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') void find();
          }}
        />
        <button
          type="button"
          onClick={() => void find()}
          disabled={!repo.trim() || listing.kind === 'loading'}
        >
          {listing.kind === 'loading' ? 'Finding…' : 'Find'}
        </button>
      </div>
      {listing.kind === 'error' && <span className="webml-error">{listing.message}</span>}
      {listing.kind === 'ok' &&
        (listing.files.length === 0 ? (
          <span className="webml-meta">no .gguf files in this repo</span>
        ) : (
          <div className="webml-row">
            <select aria-label="GGUF file" value={file} onChange={(e) => setFile(e.target.value)}>
              {listing.files.map((f) => (
                <option key={f.path} value={f.path}>
                  {f.path} · {formatBytes(f.size)}
                </option>
              ))}
            </select>
          </div>
        ))}
      {listing.kind === 'idle' && file && <span className="webml-meta">{file}</span>}
      {id && !inspection && !inspectError && (
        <span className="webml-meta">reading the file’s header…</span>
      )}
      {inspectError && (
        <span className="webml-error">could not read the header: {inspectError}</span>
      )}
      {inspection &&
        (inspection.ok ? (
          <span className="webml-meta">
            {[
              inspection.arch,
              inspection.quant,
              inspection.contextLength
                ? `${inspection.contextLength.toLocaleString()} context`
                : null,
              inspection.tools ? 'tools' : null,
              inspection.thinking ? 'thinking' : null,
            ]
              .filter(Boolean)
              .join(' · ')}
          </span>
        ) : (
          <ul className="webml-error webml-reasons">
            {inspection.reasons.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        ))}
    </>
  );
}
