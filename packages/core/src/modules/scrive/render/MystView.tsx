/**
 * MyST mdast → React. One renderer for the in-app Preview and the static site build
 * (`site/build.tsx` renders these same components to HTML), so what you preview is
 * what gets published. Under a `StaticRenderContext` the blocks that reach outside
 * the page — embeds, links to other pages, code cells, diagrams, scenes — ask it
 * instead of the backend (`static-context.ts`).
 *
 * Safety: every string is rendered as text by React except four, each of which is
 * produced by something that escapes or sanitizes it — KaTeX (`trust: false`), the
 * Lezer highlighter (escapes every token), Mermaid (`securityLevel: 'strict'`), and
 * raw HTML through `sanitizeHtml`'s allowlist. Links and embeds go through
 * `safeHref` / `safeEmbedSrc`.
 *
 * Unknown directives and roles render **visibly**, as their source in a labelled
 * box: a page that uses something Scrive doesn't draw yet must say so, not quietly
 * lose a paragraph.
 */
import {
  createContext,
  Fragment,
  useContext,
  useId,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

import { renderHighlightedCodeBlock } from '../../../docs/markdown';
import { renderMath } from '../../../notebook/math';
import { parseMyst, type MystNode, type MystRoot } from '../myst/parse';
import { assetUrl, safeEmbedSrc, safeHref, slug, splitDirectiveBody } from './directives';
import { BackRefIcon } from '../icons';
import { CellOutputs, CellRunBar, StoredOutputs, usePageCells } from './CellOutputs';
import { MermaidBlock } from './MermaidBlock';
import { PageAgentContext } from './page-agent';
import { pendingPrompt } from '../prompts';
import { paramValues, parseParams, SceneFrame } from './SceneFrame';
import { SpaceEmbed } from './SpaceEmbed';
import { AppFrame, parseAppRef } from './AppFrame';
import { openApp } from '../open';
import { TokenViz } from './TokenViz';
import { WebLlm, webLlmOptions } from './WebLlm';
import { catalogEntry } from '@horrible/webml';
import { groupInlineHtml } from './inline-html';
import { sanitizeHtml } from './sanitize';
import { StaticRenderContext } from './static-context';
import './page.css';

interface RenderContext {
  site: string;
  pagePath: string;
  /** Footnote identifier → its number, in order of first reference. */
  footnotes: Map<string, number>;
  /** A code cell's occurrence of its own source on the page (`cells.ts`). */
  cellOccurrence: Map<MystNode, number>;
}

const Ctx = createContext<RenderContext>({
  site: '',
  pagePath: '',
  footnotes: new Map(),
  cellOccurrence: new Map(),
});

/** True inside phrasing content (a paragraph, heading, cell): raw HTML there is an
 * inline fragment and must not open a block element inside a `<p>`. */
const InlineCtx = createContext(false);

/** Children of a node whose content is phrasing (inline) content. */
function InlineKids({ node }: { node: MystNode }) {
  return (
    <InlineCtx.Provider value={true}>
      <Kids node={node} />
    </InlineCtx.Provider>
  );
}

/** Plain text of a subtree, for heading anchors and titles. */
export function toText(node: MystNode | undefined): string {
  if (!node) return '';
  if (typeof node.value === 'string' && !node.children) return node.value;
  return (node.children ?? []).map(toText).join('');
}

function walk(node: MystNode, visit: (n: MystNode) => void): void {
  visit(node);
  for (const child of node.children ?? []) walk(child, visit);
}

/** Children in order — a balanced run of raw inline HTML tags rendered as one
 * fragment (`inline-html.ts`), so `<kbd>Ctrl</kbd>` keeps its text inside. */
function Kids({ node }: { node: MystNode }) {
  return (
    <>
      {groupInlineHtml(node.children ?? []).map((item, i) =>
        'node' in item ? <N key={i} node={item.node} /> : <RawHtml key={i} html={item.html} />,
      )}
    </>
  );
}

function RawHtml({ html }: { html: string }) {
  const Wrap = useContext(InlineCtx) ? 'span' : 'div';
  return (
    <Wrap
      className="scrive-html"
      // Allowlist-sanitized: no script, no handlers, no embeds.
      dangerouslySetInnerHTML={{ __html: sanitizeHtml(html) }}
    />
  );
}

function str(value: unknown): string | undefined {
  return typeof value === 'string' && value ? value : undefined;
}

/** One node. The switch is the whole vocabulary; anything else falls to the end. */
function N({ node }: { node: MystNode }): ReactNode {
  const ctx = useContext(Ctx);
  const published = useContext(StaticRenderContext);
  switch (node.type) {
    case 'text':
      return node.value ?? '';
    case 'paragraph':
      return (
        <p>
          <InlineKids node={node} />
        </p>
      );
    case 'heading': {
      const depth = Math.min(6, Math.max(1, Number(node.depth) || 1));
      const Tag = `h${depth}` as 'h1';
      return (
        <Tag id={str(node.identifier) ?? slug(toText(node))}>
          <InlineKids node={node} />
        </Tag>
      );
    }
    case 'emphasis':
      return (
        <em>
          <Kids node={node} />
        </em>
      );
    case 'strong':
      return (
        <strong>
          <Kids node={node} />
        </strong>
      );
    case 'delete':
      return (
        <del>
          <Kids node={node} />
        </del>
      );
    case 'underline':
      return (
        <u>
          <Kids node={node} />
        </u>
      );
    case 'subscript':
      return (
        <sub>
          <Kids node={node} />
        </sub>
      );
    case 'superscript':
      return (
        <sup>
          <Kids node={node} />
        </sup>
      );
    case 'smallcaps':
      return (
        <span style={{ fontVariant: 'small-caps' }}>
          <Kids node={node} />
        </span>
      );
    case 'abbreviation':
      return (
        <abbr title={str(node.title)}>
          <Kids node={node} />
        </abbr>
      );
    case 'inlineCode':
      return <code>{node.value}</code>;
    case 'break':
      return <br />;
    case 'link': {
      const safe = safeHref(node.url);
      const href = published && safe ? published.link(ctx.pagePath, safe) : safe;
      const external = href !== undefined && !href.startsWith('#');
      return (
        <a
          href={href}
          title={str(node.title)}
          {...(external ? { target: '_blank', rel: 'noopener noreferrer' } : {})}
        >
          <Kids node={node} />
        </a>
      );
    }
    case 'image': {
      const url = str(node.url);
      return url ? (
        <img
          src={
            published ? published.asset(ctx.pagePath, url) : assetUrl(ctx.site, ctx.pagePath, url)
          }
          alt={str(node.alt) ?? ''}
          title={str(node.title)}
          style={{ width: str(node.width) }}
          loading="lazy"
        />
      ) : null;
    }
    case 'list': {
      const items = (node.children ?? []).map((child, i) => (
        <ListItem key={i} node={child} tight={!node.spread} />
      ));
      return node.ordered ? (
        <ol start={typeof node.start === 'number' && node.start !== 1 ? node.start : undefined}>
          {items}
        </ol>
      ) : (
        <ul>{items}</ul>
      );
    }
    case 'listItem':
      return <ListItem node={node} tight={false} />;
    case 'blockquote':
      return (
        <blockquote>
          <Kids node={node} />
        </blockquote>
      );
    case 'thematicBreak':
      return <hr />;
    case 'code':
      return <CodeBlock value={node.value ?? ''} lang={str(node.lang) ?? ''} />;
    case 'math':
      return (
        <div
          className="scrive-math"
          id={str(node.label)}
          // KaTeX output (`trust: false`): escaped TeX, no script.
          dangerouslySetInnerHTML={{ __html: renderMath(node.value ?? '', true) }}
        />
      );
    case 'inlineMath':
      return (
        <span
          className="scrive-math-inline"
          dangerouslySetInnerHTML={{ __html: renderMath(node.value ?? '', false) }}
        />
      );
    case 'table':
      return (
        <div className="scrive-table">
          <table>
            <tbody>
              <Kids node={node} />
            </tbody>
          </table>
        </div>
      );
    case 'tableRow':
      return (
        <tr>
          <Kids node={node} />
        </tr>
      );
    case 'tableCell': {
      const Cell = node.header ? 'th' : 'td';
      const align = str(node.align) as 'left' | 'right' | 'center' | undefined;
      return (
        <Cell style={align ? { textAlign: align } : undefined}>
          <InlineKids node={node} />
        </Cell>
      );
    }
    case 'html':
      return <RawHtml html={node.value ?? ''} />;
    case 'comment':
    case 'footnoteDefinition': // collected and rendered after the body
    case 'outputs': // code-cell outputs arrive with execution (Phase 3)
      return null;
    case 'mystTarget':
      return <span id={str(node.label)} className="scrive-target" />;
    case 'crossReference':
      return (
        <a href={`#${str(node.identifier) ?? ''}`}>
          {node.children?.length ? <Kids node={node} /> : (str(node.label) ?? '')}
        </a>
      );
    case 'cite':
      return <cite>[{str(node.label) ?? '?'}]</cite>;
    case 'citeGroup':
      return <Kids node={node} />;
    case 'footnoteReference': {
      const id = str(node.identifier) ?? '';
      return (
        <sup className="scrive-fnref" id={`fnref-${id}`}>
          <a href={`#fn-${id}`}>{ctx.footnotes.get(id) ?? '?'}</a>
        </sup>
      );
    }
    case 'definitionList':
      return (
        <dl>
          <Kids node={node} />
        </dl>
      );
    case 'definitionTerm':
      return (
        <dt>
          <Kids node={node} />
        </dt>
      );
    case 'definitionDescription':
      return (
        <dd>
          <Kids node={node} />
        </dd>
      );
    case 'scriveNotebookCell':
      return <StoredCell node={node} />;
    case 'block':
      if (node.kind === 'notebook-code')
        return published ? <PublishedCodeCell node={node} /> : <CodeCell node={node} />;
      return <Kids node={node} />;
    case 'admonition':
      return <Admonition node={node} />;
    case 'container':
      return <Container node={node} />;
    case 'caption':
    case 'legend':
      return (
        <figcaption>
          <Kids node={node} />
        </figcaption>
      );
    case 'details':
      return (
        <details className="scrive-details">
          <Kids node={node} />
        </details>
      );
    case 'summary':
      return (
        <summary>
          <Kids node={node} />
        </summary>
      );
    case 'iframe':
      return <Embed src={node.src} width={str(node.width)} />;
    case 'mermaid': {
      if (!published) return <MermaidBlock code={node.value ?? ''} />;
      const svg = published.mermaid(node.value ?? '');
      return svg ? (
        // Drawn ahead of time by Mermaid with `securityLevel: 'strict'`.
        <figure className="scrive-mermaid" dangerouslySetInnerHTML={{ __html: svg }} />
      ) : (
        <figure className="scrive-mermaid is-error">
          <pre>{node.value}</pre>
        </figure>
      );
    }
    case 'mystDirective':
      return <Directive node={node} />;
    case 'mystRole':
      return node.children?.length ? (
        <Kids node={node} />
      ) : (
        <code className="scrive-raw-inline" title="A role Scrive does not render yet">
          {`{${String(node.name)}}\`${node.value ?? ''}\``}
        </code>
      );
    case 'scriveParseError':
      return (
        <RawBox label="This page could not be parsed" detail={String(node.message ?? '')}>
          {node.value}
        </RawBox>
      );
    default:
      if (node.children) return <Kids node={node} />;
      return node.value ?? null;
  }
}

function ListItem({ node, tight }: { node: MystNode; tight: boolean }) {
  const task = typeof node.checked === 'boolean';
  // A tight list's paragraphs are not paragraphs: no block margins between items.
  const body = (node.children ?? []).map((child, i) =>
    tight && child.type === 'paragraph' ? (
      <InlineKids key={i} node={child} />
    ) : (
      <N key={i} node={child} />
    ),
  );
  return (
    <li className={task ? 'scrive-task' : undefined}>
      {task && <input type="checkbox" checked={Boolean(node.checked)} readOnly aria-label="Task" />}
      {body}
    </li>
  );
}

function CodeBlock({ value, lang, label }: { value: string; lang: string; label?: string }) {
  const html = useMemo(() => renderHighlightedCodeBlock(value, lang), [value, lang]);
  return (
    <div className="scrive-code">
      {/* `label=""`: the caller draws its own header (a code cell's run bar). */}
      {label !== '' && (label || lang) && <div className="scrive-code-label">{label ?? lang}</div>}
      {/* Lezer-highlighted: every token escaped. */}
      <div dangerouslySetInnerHTML={{ __html: html }} />
    </div>
  );
}

/** A notebook page's code cell: the source and the outputs saved in the file. */
function StoredCell({ node }: { node: MystNode }) {
  const lang = str(node.lang) ?? 'python';
  const count = typeof node.executionCount === 'number' ? `[${node.executionCount}]` : '';
  return (
    <div className="scrive-cell">
      <div className="scrive-cell-bar">
        <span className="scrive-code-label">{lang}</span>
        <span className="scrive-cell-state">{count}</span>
      </div>
      <CodeBlock value={String(node.value ?? '')} lang={lang} label="" />
      <StoredOutputs outputs={(node.outputs as Record<string, unknown>[] | undefined) ?? []} />
    </div>
  );
}

/** A `{code-cell}` on a published page: its source and the outputs it last made. */
function PublishedCodeCell({ node }: { node: MystNode }) {
  const published = useContext(StaticRenderContext);
  const { pagePath, cellOccurrence } = useContext(Ctx);
  const code = node.children?.find((c) => c.type === 'code');
  const source = String(code?.value ?? '');
  const lang = str(code?.lang) ?? 'python';
  const outputs = published?.cellOutputs(pagePath, source, cellOccurrence.get(node) ?? 0) ?? [];
  return (
    <div className="scrive-cell">
      <div className="scrive-cell-bar">
        <span className="scrive-code-label">{lang}</span>
      </div>
      <CodeBlock value={source} lang={lang} label="" />
      <StoredOutputs outputs={outputs} />
    </div>
  );
}

/** A `{code-cell}`: its source, a run bar, and its outputs (live or cached). */
function CodeCell({ node }: { node: MystNode }) {
  const { site, pagePath, cellOccurrence } = useContext(Ctx);
  const cells = usePageCells(site, pagePath);
  const code = node.children?.find((c) => c.type === 'code');
  const source = String(code?.value ?? '');
  const lang = str(code?.lang) ?? 'python';
  const occurrence = cellOccurrence.get(node) ?? 0;
  return (
    <div className="scrive-cell">
      <CellRunBar cells={cells} source={source} occurrence={occurrence} language={lang} />
      <CodeBlock value={source} lang={lang} label="" />
      <CellOutputs cells={cells} source={source} occurrence={occurrence} />
    </div>
  );
}

function Admonition({ node, dropdown }: { node: MystNode; dropdown?: boolean }) {
  const kind = str(node.kind) ?? 'note';
  const children = node.children ?? [];
  const titleNode = children.find((c) => c.type === 'admonitionTitle');
  const body = children.filter((c) => c !== titleNode);
  const title = titleNode ? <Kids node={titleNode} /> : kind;
  const content = (
    <div className="scrive-admonition-body">
      {body.map((child, i) => (
        <N key={i} node={child} />
      ))}
    </div>
  );
  if (dropdown) {
    return (
      <details className={`scrive-admonition is-${kind}`}>
        <summary className="scrive-admonition-title">{title}</summary>
        {content}
      </details>
    );
  }
  return (
    <aside className={`scrive-admonition is-${kind}`}>
      <div className="scrive-admonition-title">{title}</div>
      {content}
    </aside>
  );
}

function Container({ node }: { node: MystNode }) {
  const children = node.children ?? [];
  const media = children.filter(
    (c) => c.type === 'image' || c.type === 'iframe' || c.type === 'table',
  );
  const caption = children.filter((c) => !media.includes(c));
  return (
    <figure className="scrive-figure" id={str(node.identifier) ?? str(node.label)}>
      {media.map((child, i) => (
        <N key={i} node={child} />
      ))}
      {caption.length > 0 && (
        <figcaption>
          {caption.map((child, i) =>
            child.type === 'paragraph' ? (
              <InlineKids key={i} node={child} />
            ) : (
              <N key={i} node={child} />
            ),
          )}
        </figcaption>
      )}
    </figure>
  );
}

function Embed({ src, width }: { src: unknown; width?: string }) {
  const url = safeEmbedSrc(src);
  if (!url)
    return (
      <RawBox label="Embed refused: only https:// sources are embedded">{String(src ?? '')}</RawBox>
    );
  return (
    <div className="scrive-embed" style={{ width: width ?? '100%' }}>
      <iframe
        src={url}
        title={url}
        loading="lazy"
        referrerPolicy="strict-origin-when-cross-origin"
        // A third-party player needs scripts and its own origin, never ours.
        sandbox="allow-scripts allow-same-origin allow-presentation allow-popups"
        allow="fullscreen; picture-in-picture; encrypted-media"
      />
    </div>
  );
}

function RawBox({
  label,
  detail,
  children,
}: {
  label: string;
  detail?: string;
  children: ReactNode;
}) {
  return (
    <figure className="scrive-raw">
      <figcaption>
        {label}
        {detail && <span className="scrive-raw-detail"> · {detail}</span>}
      </figcaption>
      <pre>{children}</pre>
    </figure>
  );
}

/** A sub-document: the body of a directive myst-parser left unprocessed. */
function Body({ source }: { source: string }) {
  const tree = useMemo(() => parseMyst(source), [source]);
  return <Kids node={tree} />;
}

function directiveSource(node: MystNode): string {
  const head = `\`\`\`{${String(node.name)}}${node.args ? ` ${String(node.args)}` : ''}`;
  return `${head}\n${node.value ?? ''}\n\`\`\``;
}

function Directive({ node }: { node: MystNode }) {
  const ctx = useContext(Ctx);
  const published = useContext(StaticRenderContext);
  const name = String(node.name ?? '');
  const options = (node.options ?? {}) as Record<string, unknown>;

  // Processed by the parser: render what it built.
  if (node.children?.length) {
    const first = node.children[0];
    if (
      first.type === 'admonition' &&
      String(options.class ?? '')
        .split(/\s+/)
        .includes('dropdown')
    ) {
      return <Admonition node={first} dropdown />;
    }
    return <Kids node={node} />;
  }

  const { options: parsed, body } = splitDirectiveBody(node.value);
  switch (name) {
    case 'tab-set':
      return published ? <StaticTabSet source={body} /> : <TabSet source={body} />;
    case 'tab-item':
      return (
        <section className="scrive-tab-solo">
          <div className="scrive-tab-solo-title">{String(node.args ?? '')}</div>
          <Body source={body} />
        </section>
      );
    case 'grid': {
      const counts = String(node.args ?? '').match(/\d+/g);
      const columns = counts ? Math.max(1, Math.min(6, Number(counts[counts.length - 1]))) : 2;
      return (
        <div
          className="scrive-grid"
          style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}
        >
          <Body source={body} />
        </div>
      );
    }
    case 'card':
      return (
        <section className="scrive-card">
          {node.args ? <div className="scrive-card-title">{String(node.args)}</div> : null}
          <Body source={body} />
        </section>
      );
    case 'video': {
      const src = str(node.args);
      return src ? (
        <video
          className="scrive-video"
          src={
            published ? published.asset(ctx.pagePath, src) : assetUrl(ctx.site, ctx.pagePath, src)
          }
          controls
          preload="metadata"
          style={{ width: parsed.width }}
        />
      ) : null;
    }
    case 'r3f': {
      // A scene runs in a sandboxed frame (SceneFrame). The Preview offers its tweaks;
      // capturing a poster or a recording is in Write, where the result can be saved
      // into the block.
      const { site, pagePath } = ctx;
      if (published) {
        const spec = parseParams(parsed.params);
        const src = String(node.args ?? '');
        return (
          <figure className="scrive-scene" data-status="ready">
            <iframe
              title={`3D scene ${src}`}
              // The published scene page runs the same runtime, sandboxed the same way.
              sandbox="allow-scripts"
              loading="lazy"
              src={published.scene(
                pagePath,
                src,
                typeof spec === 'string' ? {} : paramValues(spec),
              )}
              style={{ height: Number(parsed.height) || 360 }}
            />
          </figure>
        );
      }
      return (
        <SceneFrame
          site={site}
          pagePath={pagePath}
          src={String(node.args ?? '')}
          height={Number(parsed.height) || 360}
          params={parsed.params}
        />
      );
    }
    case 'space':
      return <SpaceEmbed arg={node.args} options={parsed} published={Boolean(published)} />;
    case 'webllm': {
      if (published) {
        const o = webLlmOptions(node.args, parsed);
        if (!o.model) return null;
        return (
          <figure className="scrive-llm" data-status="ready">
            <iframe
              title={`In-browser model ${o.model}`}
              src={published.webllm(ctx.pagePath, {
                model: o.model,
                dtype: o.dtype ?? '',
                system: o.system,
                tokens: o.showTokens,
                lens: o.showLens,
                max: o.maxTokens,
                // The catalog's download sizes, so the page can say what a click costs.
                sizes: catalogEntry(o.model)?.sizes ?? {},
              })}
              loading="lazy"
              style={{ height: Number(parsed.height) || 420 }}
            />
          </figure>
        );
      }
      return <WebLlm arg={node.args} options={parsed} />;
    }
    case 'tokenviz':
      return <TokenViz site={ctx.site} pagePath={ctx.pagePath} src={String(node.args ?? '').trim()} />;
    case 'app': {
      // A web app from the site's apps/ folder. In the app it runs on the apps origin
      // with live reload and its console; published, it is the copy under _scrive/apps/.
      const height = Number(parsed.height) || 600;
      const name = parseAppRef(node.args);
      if (published) {
        return name ? (
          <figure className="scrive-app" data-status="ready">
            <iframe
              title={`Web app ${name}`}
              src={published.app(ctx.pagePath, name)}
              loading="lazy"
              style={{ height }}
              allow="cross-origin-isolated; fullscreen; clipboard-write"
            />
          </figure>
        ) : null;
      }
      return <AppFrame site={ctx.site} name={node.args} height={height} onPreview={name ? () => openApp(ctx.site, name) : undefined} />;
    }
    case 'pending':
      // A section still to write never reaches readers (preflight says so).
      return published ? null : <Pending intent={body} />;
    default:
      return (
        <RawBox
          label={`{${name}} — Scrive does not render this directive yet; it is kept as written`}
        >
          {directiveSource(node)}
        </RawBox>
      );
  }
}

/**
 * A section still to write: what a template or an approved outline left for the
 * agent. Shown as a placeholder card with its intent, never as if it were content —
 * and the critique pass flags any left in a page.
 */
function Pending({ intent }: { intent: string }) {
  const ask = useContext(PageAgentContext);
  const { site, pagePath } = useContext(Ctx);
  return (
    <aside className="scrive-pending" aria-label="Section still to write">
      <div className="scrive-pending-head">
        <span className="scrive-head">To write</span>
        {ask && (
          <button
            type="button"
            className="scrive-pending-ask"
            title="Ask the agent to write this section"
            onMouseDown={(e) => {
              // Inside Write, a click would select the block and open its source.
              e.preventDefault();
              e.stopPropagation();
            }}
            onClick={() => ask(pendingPrompt(site, pagePath, intent))}
          >
            write with agent
          </button>
        )}
      </div>
      <div className="scrive-pending-intent">{intent.trim() || 'Write this section.'}</div>
    </aside>
  );
}

function TabSet({ source }: { source: string }) {
  const tabs = useMemo(
    () =>
      parseMyst(source)
        .children.filter((c) => c.type === 'mystDirective' && c.name === 'tab-item')
        .map((c) => ({ title: String(c.args ?? 'Tab'), body: splitDirectiveBody(c.value).body })),
    [source],
  );
  const [active, setActive] = useState(0);
  if (!tabs.length) return null;
  const current = tabs[Math.min(active, tabs.length - 1)];
  return (
    <div className="scrive-tabs">
      <div className="scrive-tab-strip" role="tablist">
        {tabs.map((tab, i) => (
          <button
            key={i}
            type="button"
            role="tab"
            className="scrive-tab"
            aria-selected={i === active}
            onClick={() => setActive(i)}
          >
            {tab.title}
          </button>
        ))}
      </div>
      <div role="tabpanel" className="scrive-tab-panel">
        <Body source={current.body} />
      </div>
    </div>
  );
}

/**
 * Tabs without script, for a published page: one radio per tab, and CSS
 * (`.scrive-tabs-static` in page.css) shows the panel after the checked one.
 */
function StaticTabSet({ source }: { source: string }) {
  const name = `tabs-${useId().replace(/[^\w-]/g, '')}`;
  const tabs = useMemo(
    () =>
      parseMyst(source)
        .children.filter((c) => c.type === 'mystDirective' && c.name === 'tab-item')
        .map((c) => ({ title: String(c.args ?? 'Tab'), body: splitDirectiveBody(c.value).body })),
    [source],
  );
  return (
    <div className="scrive-tabs scrive-tabs-static">
      {tabs.map((tab, i) => (
        <Fragment key={i}>
          <input
            type="radio"
            name={name}
            id={`${name}-${i}`}
            className="scrive-tab-radio"
            defaultChecked={i === 0}
          />
          <label htmlFor={`${name}-${i}`} className="scrive-tab">
            {tab.title}
          </label>
          <div className="scrive-tab-panel">
            <Body source={tab.body} />
          </div>
        </Fragment>
      ))}
    </div>
  );
}

export interface MystViewProps {
  tree: MystRoot;
  site: string;
  /** The page's path in the site — what relative media paths resolve against. */
  pagePath: string;
  /** Added to body-relative node lines to give file lines (the frontmatter's span). */
  lineOffset?: number;
  /** A top-level block was clicked: its first line in the file. */
  onBlockClick?: (line: number) => void;
  header?: ReactNode;
}

export function MystView({
  tree,
  site,
  pagePath,
  lineOffset = 0,
  onBlockClick,
  header,
}: MystViewProps) {
  const { footnotes, definitions } = useMemo(() => {
    const order = new Map<string, number>();
    const defs: MystNode[] = [];
    walk(tree, (n) => {
      if (n.type === 'footnoteReference') {
        const id = str(n.identifier);
        if (id && !order.has(id)) order.set(id, order.size + 1);
      } else if (n.type === 'footnoteDefinition') defs.push(n);
    });
    defs.sort(
      (a, b) =>
        (order.get(str(a.identifier) ?? '') ?? 1e9) - (order.get(str(b.identifier) ?? '') ?? 1e9),
    );
    return { footnotes: order, definitions: defs };
  }, [tree]);

  // Top level, `+++` blocks flattened, each tagged with its file line.
  const blocks = useMemo(
    () =>
      tree.children.flatMap((n) =>
        n.type === 'block' && n.kind !== 'notebook-code' ? (n.children ?? []) : [n],
      ),
    [tree],
  );

  // Code cells, numbered by how many times their exact source has appeared.
  const cellOccurrence = useMemo(() => {
    const out = new Map<MystNode, number>();
    const seen = new Map<string, number>();
    walk(tree, (n) => {
      if (n.type !== 'block' || n.kind !== 'notebook-code') return;
      const source = String(n.children?.find((c) => c.type === 'code')?.value ?? '');
      const count = seen.get(source) ?? 0;
      seen.set(source, count + 1);
      out.set(n, count);
    });
    return out;
  }, [tree]);

  const ctx = useMemo(
    () => ({ site, pagePath, footnotes, cellOccurrence }),
    [site, pagePath, footnotes, cellOccurrence],
  );
  // A published page has no editor to jump to: no line anchors.
  const published = useContext(StaticRenderContext) !== null;

  return (
    <Ctx.Provider value={ctx}>
      <article
        className="scrive-page"
        onClick={(e) => {
          if (
            !onBlockClick ||
            (e.target as HTMLElement).closest('a, button, summary, video, input')
          )
            return;
          const line = (e.target as HTMLElement).closest<HTMLElement>('[data-line]')?.dataset.line;
          if (line) onBlockClick(Number(line));
        }}
      >
        {header}
        {blocks.map((node, i) => {
          if (node.type === 'footnoteDefinition') return null;
          const line = node.position?.start.line;
          return (
            <div
              key={i}
              className="scrive-block"
              data-line={line && !published ? line + lineOffset : undefined}
            >
              <N node={node} />
            </div>
          );
        })}
        {definitions.length > 0 && (
          <section className="scrive-footnotes">
            <ol>
              {definitions.map((def) => {
                const id = str(def.identifier) ?? '';
                return (
                  <li key={id} id={`fn-${id}`} value={footnotes.get(id)}>
                    <Kids node={def} />{' '}
                    <a href={`#fnref-${id}`} aria-label="Back to text" className="scrive-fnback">
                      <BackRefIcon size={12} />
                    </a>
                  </li>
                );
              })}
            </ol>
          </section>
        )}
      </article>
    </Ctx.Provider>
  );
}
