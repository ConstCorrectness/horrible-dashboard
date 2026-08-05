/**
 * A small, safe markdown renderer for documentation popups.
 *
 * Handles the subset a docstring actually uses — fenced and inline code, headings,
 * bullet lists, bold/italics, paragraphs — and **escapes every user-supplied span
 * before any tag is added**, so the built string carries only our own markup. That
 * is the whole security argument: a docstring comes from an installed package, and
 * a `web` doc body comes from an arbitrary page.
 *
 * Lifted out of the editor's `lsp.ts`, where it started life as the renderer for
 * hover and completion doc panes. A notebook cell has no LSP at all but needs the
 * same rendering, and importing the editor module to get it would have pulled a
 * language-server client into a component that will never have one.
 */

const NAMED_ENTITIES: Record<string, string> = {
  amp: '&',
  lt: '<',
  gt: '>',
  quot: '"',
  apos: "'",
  nbsp: ' ',
  hellip: '…',
  mdash: '—',
  ndash: '–',
  lsquo: '‘',
  rsquo: '’',
  ldquo: '“',
  rdquo: '”',
  times: '×',
  copy: '©',
};

/** Decode named + numeric (`&#123;` / `&#x1F;`) HTML entities to their characters.
 * Only well-formed `&name;`/`&#num;` refs are touched; a bare `&` is left as-is.
 * Runs *before* `escapeHtml`, so a decoded `<`/`>`/`&` is re-escaped to visible
 * text — decoding never opens an injection hole. */
function decodeEntities(s: string): string {
  if (!s.includes('&')) return s;
  return s.replace(/&(#x[0-9a-fA-F]+|#[0-9]+|[a-zA-Z][a-zA-Z0-9]*);/g, (m, body: string) => {
    if (body[0] === '#') {
      const cp =
        body[1] === 'x' || body[1] === 'X'
          ? parseInt(body.slice(2), 16)
          : parseInt(body.slice(1), 10);
      return Number.isFinite(cp) && cp > 0 && cp <= 0x10ffff ? String.fromCodePoint(cp) : m;
    }
    return NAMED_ENTITIES[body] ?? m;
  });
}

/** Escape the HTML-significant characters so text can't inject markup. Any HTML
 * entities the source already carries are decoded first (see `decodeEntities`) —
 * decode-then-escape renders them as their characters, safely. */
export function escapeHtml(s: string): string {
  return decodeEntities(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/** Inline markdown → HTML on an already-escaped string: inline code, bold, italics,
 * and links flattened to their text (doc panes aren't a place to navigate away). */
export function renderInline(escaped: string): string {
  return escaped
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*])\*([^*]+)\*/g, '$1<em>$2</em>')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1');
}

/** Render a small subset of markdown (fenced/inline code, headings, bullet lists,
 * bold/italics, paragraphs) to a safe DOM node. Every user-supplied span is
 * HTML-escaped before any tag is added, so the built string carries only our own
 * markup — no XSS surface from a docstring. Used for completion doc panes and hover. */
export function renderMarkdown(md: string): HTMLElement {
  const container = document.createElement('div');
  const lines = md.replace(/\r\n/g, '\n').split('\n');
  let html = '';
  let listOpen = false;
  let i = 0;
  const closeList = (): void => {
    if (listOpen) {
      html += '</ul>';
      listOpen = false;
    }
  };
  const isBlockStart = (l: string): boolean =>
    /^\s*```/.test(l) || /^#{1,6}\s/.test(l) || /^\s*[-*+]\s/.test(l);
  while (i < lines.length) {
    const line = lines[i];
    if (/^\s*```/.test(line)) {
      closeList();
      const buf: string[] = [];
      i++;
      while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) buf.push(lines[i++]);
      i++; // skip the closing fence
      html += `<pre><code>${escapeHtml(buf.join('\n'))}</code></pre>`;
      continue;
    }
    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      closeList();
      const level = heading[1].length;
      html += `<h${level}>${renderInline(escapeHtml(heading[2]))}</h${level}>`;
      i++;
      continue;
    }
    const item = line.match(/^\s*[-*+]\s+(.*)$/);
    if (item) {
      if (!listOpen) {
        html += '<ul>';
        listOpen = true;
      }
      html += `<li>${renderInline(escapeHtml(item[1]))}</li>`;
      i++;
      continue;
    }
    if (line.trim() === '') {
      closeList();
      i++;
      continue;
    }
    closeList();
    const para: string[] = [line];
    i++;
    while (i < lines.length && lines[i].trim() !== '' && !isBlockStart(lines[i])) {
      para.push(lines[i++]);
    }
    html += `<p>${renderInline(escapeHtml(para.join(' ')))}</p>`;
  }
  closeList();
  container.innerHTML = html;
  return container;
}
