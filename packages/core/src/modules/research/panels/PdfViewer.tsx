/**
 * PDF viewer pane (`research.pdfViewer`) — pdf.js over an artifact-store blob.
 *
 * Multi-instance; each instance is opened with params:
 *   `{ artifactId?: string; sourceId?: string; url?: string; page?: number }`
 * (`sourceId` resolves to the source's artifact; `url` is any absolute PDF URL,
 * used for one-off viewing without storing).
 *
 * The worker is instantiated Vite-natively (`new Worker(new URL(...))` +
 * `workerPort`) — no asset-copy plugin, works in both the web and Tauri builds.
 * Rendering is canvas per page with a selectable text layer; find is
 * text-layer-based: it walks page texts and jumps to the next matching page.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { usePaneParams } from '../../../panes';
import { apiGet } from '../../../api';
import type { SourceModel } from '../../library/api';
import { artifactUrl } from '../api';

import * as pdfjs from 'pdfjs-dist';
import type { PDFDocumentProxy, PDFPageProxy } from 'pdfjs-dist';
import 'pdfjs-dist/web/pdf_viewer.css';

let workerReady = false;
function ensureWorker(): void {
  if (workerReady) return;
  workerReady = true;
  pdfjs.GlobalWorkerOptions.workerPort = new Worker(
    new URL('pdfjs-dist/build/pdf.worker.min.mjs', import.meta.url),
    { type: 'module' },
  );
}

const ZOOMS = [0.5, 0.75, 1, 1.25, 1.5, 2, 3];

interface PageView {
  pageNumber: number;
  page: PDFPageProxy;
}

function PdfPage({
  view,
  scale,
  registerText,
}: {
  view: PageView;
  scale: number;
  registerText: (pageNumber: number, text: string) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const textRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    const canvas = canvasRef.current;
    const textDiv = textRef.current;
    if (!canvas || !textDiv) return;
    const viewport = view.page.getViewport({ scale });
    const dpr = window.devicePixelRatio || 1;
    canvas.width = viewport.width * dpr;
    canvas.height = viewport.height * dpr;
    canvas.style.width = `${viewport.width}px`;
    canvas.style.height = `${viewport.height}px`;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    const renderTask = view.page.render({
      canvasContext: ctx,
      viewport,
      transform: dpr === 1 ? undefined : [dpr, 0, 0, dpr, 0, 0],
    });

    textDiv.replaceChildren();
    textDiv.style.width = `${viewport.width}px`;
    textDiv.style.height = `${viewport.height}px`;
    void view.page.getTextContent().then((textContent) => {
      if (cancelled) return;
      registerText(
        view.pageNumber,
        textContent.items.map((it) => ('str' in it ? it.str : '')).join(' '),
      );
      const layer = new pdfjs.TextLayer({
        textContentSource: textContent,
        container: textDiv,
        viewport,
      });
      void layer.render();
    });

    return () => {
      cancelled = true;
      renderTask.cancel();
    };
  }, [view, scale, registerText]);

  return (
    <div
      data-pdf-page={view.pageNumber}
      style={{ position: 'relative', margin: '0 auto 12px', width: 'fit-content' }}
    >
      <canvas ref={canvasRef} style={{ display: 'block', background: 'white' }} />
      <div ref={textRef} className="textLayer" />
    </div>
  );
}

export function PdfViewer() {
  const params = usePaneParams();
  const artifactIdParam = typeof params.artifactId === 'string' ? params.artifactId : null;
  const sourceId = typeof params.sourceId === 'string' ? params.sourceId : null;
  const urlParam = typeof params.url === 'string' ? params.url : null;
  const initialPage = typeof params.page === 'number' ? params.page : 1;

  const [doc, setDoc] = useState<PDFDocumentProxy | null>(null);
  const [views, setViews] = useState<PageView[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [scale, setScale] = useState(1.25);
  const [find, setFind] = useState('');
  const [findStatus, setFindStatus] = useState('');
  const pageTexts = useRef(new Map<number, string>());
  const scrollRef = useRef<HTMLDivElement>(null);
  const lastFindPage = useRef(0);

  const registerText = useCallback((pageNumber: number, text: string) => {
    pageTexts.current.set(pageNumber, text.toLowerCase());
  }, []);

  useEffect(() => {
    ensureWorker();
    let cancelled = false;
    setError(null);
    setDoc(null);
    setViews([]);
    pageTexts.current.clear();

    (async () => {
      let src = urlParam;
      if (!src && artifactIdParam) src = artifactUrl(artifactIdParam);
      if (!src && sourceId) {
        const source = await apiGet<SourceModel>(`/library/sources/${sourceId}`);
        if (!source.artifact_id) throw new Error('source has no stored PDF');
        src = artifactUrl(source.artifact_id);
      }
      if (!src) throw new Error('open a PDF from the library, ArXiv, or an upload');
      const loaded = await pdfjs.getDocument(src).promise;
      if (cancelled) return;
      setDoc(loaded);
      const pages: PageView[] = [];
      for (let i = 1; i <= loaded.numPages; i += 1) {
        pages.push({ pageNumber: i, page: await loaded.getPage(i) });
        if (cancelled) return;
      }
      setViews(pages);
    })().catch((err: unknown) => {
      if (!cancelled) setError(err instanceof Error ? err.message : String(err));
    });

    return () => {
      cancelled = true;
    };
  }, [artifactIdParam, sourceId, urlParam]);

  const scrollToPage = useCallback((pageNumber: number) => {
    const el = scrollRef.current?.querySelector(`[data-pdf-page="${pageNumber}"]`);
    el?.scrollIntoView({ block: 'start' });
  }, []);

  // Jump to the requested page once pages exist.
  useEffect(() => {
    if (views.length === 0 || initialPage <= 1) return;
    scrollToPage(initialPage);
  }, [views.length, initialPage, scrollToPage]);

  const runFind = useCallback(() => {
    const needle = find.trim().toLowerCase();
    if (!needle || !doc) return;
    const total = doc.numPages;
    for (let offset = 1; offset <= total; offset += 1) {
      const pageNumber = ((lastFindPage.current + offset - 1) % total) + 1;
      const text = pageTexts.current.get(pageNumber);
      if (text && text.includes(needle)) {
        lastFindPage.current = pageNumber;
        scrollToPage(pageNumber);
        setFindStatus(`p.${pageNumber}`);
        return;
      }
    }
    setFindStatus('no match');
  }, [find, doc, scrollToPage]);

  const zoomLabel = useMemo(() => `${Math.round(scale * 100)}%`, [scale]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '0.5rem',
          padding: '0.25rem 0.5rem',
          borderBottom: '1px solid var(--border)',
          fontSize: '0.75rem',
        }}
      >
        <span style={{ color: 'var(--text-dim)' }}>
          {doc ? `${doc.numPages} pages` : error ? '' : 'loading…'}
        </span>
        <button
          onClick={() => setScale((s) => ZOOMS[Math.max(0, ZOOMS.indexOf(s) - 1)] ?? s)}
          title="Zoom out"
        >
          −
        </button>
        <span style={{ minWidth: '3em', textAlign: 'center' }}>{zoomLabel}</span>
        <button
          onClick={() =>
            setScale((s) => ZOOMS[Math.min(ZOOMS.length - 1, ZOOMS.indexOf(s) + 1)] ?? s)
          }
          title="Zoom in"
        >
          +
        </button>
        <input
          value={find}
          placeholder="Find…"
          onChange={(e) => setFind(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') runFind();
          }}
          style={{ marginLeft: 'auto', width: '10rem' }}
        />
        <span style={{ color: 'var(--text-dim)', minWidth: '4em' }}>{findStatus}</span>
      </div>
      <div ref={scrollRef} style={{ flex: 1, overflow: 'auto', padding: '12px 0' }}>
        {error && <div style={{ padding: '1rem', color: 'var(--text-dim)' }}>{error}</div>}
        {views.map((view) => (
          <PdfPage key={view.pageNumber} view={view} scale={scale} registerText={registerText} />
        ))}
      </div>
    </div>
  );
}
