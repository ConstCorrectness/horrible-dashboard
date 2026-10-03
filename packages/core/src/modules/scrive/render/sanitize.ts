/**
 * Raw HTML in a page → HTML that is safe to put on the app's own origin.
 *
 * Markdown allows raw HTML (`<details>`, `<kbd>`, a `<div>` wrapper) and authors use
 * it. But a Scrive page may be agent-written, and the preview runs on the origin
 * that holds the app's session — so a page must not be able to run script there.
 * This is an **allowlist**: unknown tags are unwrapped (their text kept), unknown
 * attributes dropped, URLs checked with the same rules as Markdown links. Embeds
 * (`iframe`, `script`, `object`, forms) are removed with their content; the `{iframe}`
 * directive is the supported way to embed, and it renders sandboxed.
 */
import { safeHref } from './directives';

const ALLOWED_TAGS = new Set([
  'a',
  'abbr',
  'b',
  'blockquote',
  'br',
  'caption',
  'cite',
  'code',
  'dd',
  'del',
  'details',
  'div',
  'dl',
  'dt',
  'em',
  'figcaption',
  'figure',
  'h1',
  'h2',
  'h3',
  'h4',
  'h5',
  'h6',
  'hr',
  'i',
  'img',
  'ins',
  'kbd',
  'li',
  'mark',
  'ol',
  'p',
  'pre',
  'q',
  's',
  'samp',
  'small',
  'span',
  'strong',
  'sub',
  'summary',
  'sup',
  'table',
  'tbody',
  'td',
  'tfoot',
  'th',
  'thead',
  'tr',
  'u',
  'ul',
  'var',
]);

/** Removed with everything inside them. */
const DROPPED_TAGS = new Set([
  'script',
  'style',
  'iframe',
  'object',
  'embed',
  'form',
  'input',
  'button',
  'textarea',
  'select',
  'link',
  'meta',
  'base',
  'svg',
  'math',
  'template',
  'noscript',
  'frame',
  'frameset',
]);

const ALLOWED_ATTRS = new Set([
  'alt',
  'title',
  'colspan',
  'rowspan',
  'align',
  'open',
  'width',
  'height',
  'id',
  'lang',
  'dir',
]);

function clean(node: Element): void {
  for (const child of Array.from(node.children)) {
    const tag = child.tagName.toLowerCase();
    if (DROPPED_TAGS.has(tag)) {
      child.remove();
      continue;
    }
    clean(child);
    if (!ALLOWED_TAGS.has(tag)) {
      // Unwrap: keep the (already cleaned) content, lose the element.
      child.replaceWith(...Array.from(child.childNodes));
      continue;
    }
    for (const attr of Array.from(child.attributes)) {
      const name = attr.name.toLowerCase();
      if (name === 'href' && tag === 'a') {
        const href = safeHref(attr.value);
        if (href) {
          child.setAttribute('href', href);
          child.setAttribute('rel', 'noopener noreferrer');
          child.setAttribute('target', '_blank');
        } else child.removeAttribute(attr.name);
      } else if (name === 'src' && tag === 'img') {
        if (!/^(?:https:\/\/|data:image\/(?:png|gif|jpe?g|webp);)/i.test(attr.value.trim())) {
          child.removeAttribute(attr.name);
        }
      } else if (!ALLOWED_ATTRS.has(name)) {
        child.removeAttribute(attr.name);
      }
    }
  }
}

export function sanitizeHtml(html: string): string {
  if (typeof DOMParser === 'undefined') return '';
  const doc = new DOMParser().parseFromString(`<body>${html}</body>`, 'text/html');
  clean(doc.body);
  return doc.body.innerHTML;
}
