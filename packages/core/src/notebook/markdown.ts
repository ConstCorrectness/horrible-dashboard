/**
 * A tiny Markdown → HTML renderer for notebook markdown cells. Covers the common
 * subset (headings, bold/italic, inline + fenced code, links, lists, blockquotes,
 * hr, paragraphs) plus `$…$` / `$$…$$` math. Input is HTML-escaped first, so the
 * output is safe to inject even though the trusted-local posture (the user's own
 * notes) would already permit it. Not a spec-complete parser — enough for note cells.
 *
 * Math is lifted out **before** anything else touches the source and put back at the
 * very end. Every other rule in this file would otherwise corrupt it in a way that
 * produces a plausible-looking wrong result rather than an error: `escapeHtml` turns
 * a raw `<` into an entity, the italic rule eats a lone `*`, and the backtick rule
 * claims anything between two of them.
 */
import { renderMath } from './math';

function escapeHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

/**
 * Pull math spans out and leave an opaque placeholder.
 *
 * The placeholder is a private-use codepoint plus an index: it survives `escapeHtml`
 * (nothing to escape), matches none of the inline rules, and cannot occur in real
 * prose. A NUL-style marker would be stripped by some editors on the way in, and a
 * text marker like `@@MATH0@@` is something a user could type.
 *
 * `$$` is tried before `$`, and an inline span must open on a non-space, close on a
 * non-space, stay on one line, and not be followed by a digit. All four exist to keep
 * `it costs $5 and then $10` prose: those two dollars are a perfectly plausible
 * inline pair, and typesetting the words between them is the classic false positive.
 * The close-on-non-space rule rejects that one (`then ` ends in a space) and the
 * trailing-digit rule catches the variants that do not, like `$5 and $10`.
 */
const MATH_MARK = '\uE000';

function extractMath(src: string): { text: string; spans: string[] } {
  const spans: string[] = [];
  const mark = (html: string): string => {
    spans.push(html);
    return `${MATH_MARK}${spans.length - 1}${MATH_MARK}`;
  };
  const text = src
    .replace(/\$\$([\s\S]+?)\$\$/g, (_, body: string) => mark(renderMath(body.trim(), true)))
    .replace(/\$(?!\s)((?:[^$\n\\]|\\.)+?)(?<!\s)\$(?!\d)/g, (_, body: string) =>
      mark(renderMath(body.trim(), false)),
    );
  return { text, spans };
}

function restoreMath(html: string, spans: string[]): string {
  if (spans.length === 0) return html;
  return html.replace(
    new RegExp(`${MATH_MARK}(\\d+)${MATH_MARK}`, 'g'),
    (whole, i: string) => spans[Number(i)] ?? whole,
  );
}

/**
 * Is `lines[i]` a pipe-table header?
 *
 * The *separator* below it is the identifying signal, not the pipes: a line of
 * prose containing a `|` is common, and a table that started on the header alone
 * would claim it.
 */
const TABLE_SEPARATOR = /^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$/;

function isTableHeader(lines: readonly string[], i: number): boolean {
  return i + 1 < lines.length && lines[i].includes('|') && TABLE_SEPARATOR.test(lines[i + 1]);
}

function inline(text: string): string {
  return text
    .replace(/`([^`]+)`/g, (_, c) => `<code>${c}</code>`)
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>')
    .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_, label, href) => {
      const safe = /^(https?:|mailto:|#|\/)/i.test(href) ? href : '#';
      return `<a href="${safe}" target="_blank" rel="noreferrer noopener">${label}</a>`;
    });
}

export function renderMarkdown(src: string): string {
  const { text, spans } = extractMath(src);
  const lines = escapeHtml(text).split('\n');
  const out: string[] = [];
  let i = 0;
  let listType: 'ul' | 'ol' | null = null;

  const closeList = () => {
    if (listType) {
      out.push(`</${listType}>`);
      listType = null;
    }
  };

  while (i < lines.length) {
    const line = lines[i];

    // Fenced code block.
    const fence = /^```(\w*)\s*$/.exec(line);
    if (fence) {
      closeList();
      const body: string[] = [];
      i++;
      while (i < lines.length && !/^```\s*$/.test(lines[i])) {
        body.push(lines[i]);
        i++;
      }
      i++; // consume closing fence
      out.push(`<pre><code>${body.join('\n')}</code></pre>`);
      continue;
    }

    // Heading.
    const heading = /^(#{1,6})\s+(.*)$/.exec(line);
    if (heading) {
      closeList();
      const level = heading[1].length;
      out.push(`<h${level}>${inline(heading[2])}</h${level}>`);
      i++;
      continue;
    }

    // Horizontal rule.
    if (/^(---|\*\*\*|___)\s*$/.test(line)) {
      closeList();
      out.push('<hr />');
      i++;
      continue;
    }

    // Blockquote.
    const quote = /^>\s?(.*)$/.exec(line);
    if (quote) {
      closeList();
      out.push(`<blockquote>${inline(quote[1])}</blockquote>`);
      i++;
      continue;
    }

    // Pipe table: a header row, a `|---|---|` separator, then body rows.
    //
    // The separator is what identifies one — a line of pipes on its own is just
    // prose with pipes in it, and a table that renders from the header alone would
    // claim any sentence containing a `|`.
    if (isTableHeader(lines, i)) {
      closeList();
      const cells = (row: string): string[] =>
        row
          .replace(/^\s*\|/, '')
          .replace(/\|\s*$/, '')
          .split('|')
          .map((c) => inline(c.trim()));
      const head = cells(line);
      i += 2;
      const body: string[][] = [];
      while (i < lines.length && /\|/.test(lines[i]) && !/^\s*$/.test(lines[i])) {
        body.push(cells(lines[i]));
        i++;
      }
      out.push(
        `<table><thead><tr>${head.map((c) => `<th>${c}</th>`).join('')}</tr></thead>` +
          `<tbody>${body
            .map((row) => `<tr>${row.map((c) => `<td>${c}</td>`).join('')}</tr>`)
            .join('')}</tbody></table>`,
      );
      continue;
    }

    // List items.
    const ul = /^[-*+]\s+(.*)$/.exec(line);
    const ol = /^\d+\.\s+(.*)$/.exec(line);
    if (ul || ol) {
      const wanted: 'ul' | 'ol' = ul ? 'ul' : 'ol';
      if (listType !== wanted) {
        closeList();
        out.push(`<${wanted}>`);
        listType = wanted;
      }
      out.push(`<li>${inline((ul ?? ol)![1])}</li>`);
      i++;
      continue;
    }

    // Blank line.
    if (/^\s*$/.test(line)) {
      closeList();
      i++;
      continue;
    }

    // Paragraph (gather consecutive non-empty, non-special lines).
    closeList();
    const para: string[] = [line];
    i++;
    while (
      i < lines.length &&
      !/^\s*$/.test(lines[i]) &&
      !/^(#{1,6}\s|```|>|[-*+]\s|\d+\.\s|(---|\*\*\*|___)\s*$)/.test(lines[i]) &&
      // A table header immediately after a paragraph, with no blank line between:
      // without this the paragraph run swallows it and the table never starts.
      !isTableHeader(lines, i)
    ) {
      para.push(lines[i]);
      i++;
    }
    out.push(`<p>${inline(para.join('<br />'))}</p>`);
  }

  closeList();
  return restoreMath(out.join('\n'), spans);
}
