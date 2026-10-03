/**
 * Export the page as a PDF: pick an engine and a paper size, export, open the file.
 *
 * The print engine needs the page's print build, made here in the browser
 * (`site/prepare.ts` `buildPrintBundle`, the published site's own renderer); the typst
 * engine reads the page from disk and only uses the build's list of embedded files.
 * Either way the page on disk is what exports, so unsaved edits are saved first.
 */
import { useEffect, useState } from 'react';

import { openExternal } from '../../../external';
import {
  exportFileUrl,
  exportPage,
  exportStatus,
  type ExportOutcome,
  type ExportStatus,
  type PdfEngine,
} from '../api';
import { CloseIcon, ExternalIcon } from '../icons';
import { buildPrintBundle } from '../site/prepare';

function message(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

function size(bytes: number): string {
  return bytes < 1024 * 1024
    ? `${Math.max(1, Math.round(bytes / 1024))} KB`
    : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function ExportStrip({
  site,
  path,
  beforeExport,
  onClose,
}: {
  site: string;
  path: string;
  /** Saves unsaved edits; the export reads the page from disk. */
  beforeExport: () => Promise<void>;
  onClose: () => void;
}) {
  const [status, setStatus] = useState<ExportStatus | null>(null);
  const [engine, setEngine] = useState<PdfEngine>('auto');
  const [paper, setPaper] = useState<'a4' | 'letter'>('a4');
  const [busy, setBusy] = useState<string | null>(null);
  const [result, setResult] = useState<ExportOutcome | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    exportStatus().then(setStatus, () => {});
  }, []);

  const typstReady = !!status?.myst && !!status?.typst;
  const run = async () => {
    setError(null);
    setResult(null);
    try {
      setBusy('saving');
      await beforeExport();
      setBusy('building the print page');
      const bundle = await buildPrintBundle(site, path);
      const chosen = engine === 'auto' ? (status?.auto ?? 'print') : engine;
      setBusy(chosen === 'typst' ? 'typesetting with mystmd and Typst' : 'printing in Chromium');
      setResult(await exportPage(site, path, engine, paper, bundle));
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="scrive-findings scrive-export">
      <div className="scrive-export-bar">
        <span className="scrive-head">PDF</span>
        <select
          aria-label="Engine"
          value={engine}
          onChange={(e) => setEngine(e.target.value as PdfEngine)}
          style={{ padding: '0 0.6rem' }}
        >
          <option value="auto">
            Auto ({status ? (status.auto === 'typst' ? 'Typst' : 'print') : '…'})
          </option>
          <option value="print">Print: looks like the site</option>
          <option value="typst" disabled={!typstReady}>
            Typst: typeset{typstReady ? '' : ' (needs myst + typst)'}
          </option>
        </select>
        <select
          aria-label="Paper"
          value={paper}
          onChange={(e) => setPaper(e.target.value as 'a4' | 'letter')}
          style={{ padding: '0 0.6rem' }}
        >
          <option value="a4">A4</option>
          <option value="letter">Letter</option>
        </select>
        <button
          type="button"
          disabled={!!busy}
          onClick={() => void run()}
          style={{ borderColor: 'var(--accent)', color: 'var(--accent)' }}
        >
          Export
        </button>
        <span className="scrive-meta" style={{ flex: 1, minWidth: 0 }} role="status">
          {busy ? `${busy}…` : ''}
        </span>
        <button
          type="button"
          className="btn-mini"
          aria-label="Close PDF export"
          title="Close"
          onClick={onClose}
        >
          <CloseIcon size={12} />
        </button>
      </div>
      {status && !typstReady && (
        <p className="scrive-meta scrive-export-note">
          The Typst engine typesets the page like a paper (numbered equations, cross-references). It
          needs <code>npm install -g mystmd</code> and Typst from typst.app on PATH.
        </p>
      )}
      {error && (
        <p className="scrive-export-note" role="alert" style={{ color: 'var(--danger)' }}>
          {error}
        </p>
      )}
      {result && (
        <>
          <button
            type="button"
            className="scrive-row"
            onClick={() => void openExternal(exportFileUrl(site, result.path))}
            title="Open the PDF"
          >
            <span className="scrive-meta" style={{ flex: 'none' }}>
              {result.engine}
            </span>
            <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis' }}>
              {result.path}
            </span>
            <span className="scrive-meta">
              {size(result.bytes)} · {result.seconds.toFixed(1)} s
            </span>
            <ExternalIcon size={12} />
          </button>
          {result.note && <p className="scrive-meta scrive-export-note">{result.note}</p>}
          {result.findings.map((f, i) => (
            <div key={i} className="scrive-row" data-severity={f.severity}>
              {f.file && <span className="scrive-meta">{f.file}</span>}
              <span style={{ flex: 1, minWidth: 0 }}>{f.message}</span>
            </div>
          ))}
        </>
      )}
    </div>
  );
}
