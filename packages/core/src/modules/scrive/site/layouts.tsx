/**
 * The four site layouts: the page around a published page's content.
 *
 * One document structure for all four; what differs is a body class (`site-<id>`),
 * which `site.css` styles, and two structural choices: `book` puts a table of
 * contents beside every page, `article` lists other pages at the end of the home
 * page instead of in the header. Content typography is the theme's tokens over
 * `page.css`, the same stylesheet the in-app Preview uses.
 *
 * Every href here is a *site path* turned into a URL by `url()`, relative to the
 * file being written (`urls.ts`), so a layout never needs to know where it is served.
 */
import type { CSSProperties, ReactNode } from 'react';

export const LAYOUTS = ['minimal', 'tactical', 'book', 'article'] as const;
export type LayoutId = (typeof LAYOUTS)[number];

export function layoutOf(id: string): LayoutId {
  return (LAYOUTS as readonly string[]).includes(id) ? (id as LayoutId) : 'minimal';
}

export interface NavLink {
  title: string;
  /** A site path: `` for the home page, `posts/x/` for a page, `feed.xml`. */
  path: string;
}

export interface TocGroup {
  title: string;
  links: NavLink[];
}

export interface Entry {
  title: string;
  path: string;
  date: string;
  description: string;
  tags: NavLink[];
}

export interface DocumentProps {
  layout: LayoutId;
  siteTitle: string;
  /** The page's own title; the home page passes the site's. */
  title: string;
  description: string;
  /** This page's site path, for `aria-current`. */
  path: string;
  /** A site path → its URL from this file. */
  url: (path: string) => string;
  /** Absolute URLs (they contain `SITE_URL`). */
  canonical: string;
  image?: string;
  type: 'website' | 'article';
  date?: string;
  nav: NavLink[];
  toc?: TocGroup[];
  /** KaTeX's stylesheet, only on pages with math. */
  katexCss?: string;
  year: number;
  children: ReactNode;
}

export type HeadProps = Pick<
  DocumentProps,
  | 'siteTitle'
  | 'title'
  | 'description'
  | 'url'
  | 'canonical'
  | 'image'
  | 'type'
  | 'date'
  | 'katexCss'
>;

/** Everything in a page's `<head>`: also what a theme's own layout template gets as
 * `{{{head}}}`, so it never has to rebuild the title, share tags or stylesheets. */
export function HeadTags({
  siteTitle,
  title,
  description,
  url,
  canonical,
  image,
  type,
  date,
  katexCss,
}: HeadProps) {
  const fullTitle = title && title !== siteTitle ? `${title} · ${siteTitle}` : siteTitle;
  return (
    <>
      <meta charSet="utf-8" />
      <meta name="viewport" content="width=device-width, initial-scale=1" />
      <title>{fullTitle}</title>
      {description && <meta name="description" content={description} />}
      <link rel="canonical" href={canonical} />
      <meta property="og:site_name" content={siteTitle} />
      <meta property="og:title" content={title || siteTitle} />
      {description && <meta property="og:description" content={description} />}
      <meta property="og:type" content={type} />
      <meta property="og:url" content={canonical} />
      {image && <meta property="og:image" content={image} />}
      {date && type === 'article' && <meta property="article:published_time" content={date} />}
      <meta name="twitter:card" content={image ? 'summary_large_image' : 'summary'} />
      <link rel="alternate" type="application/rss+xml" title={siteTitle} href={url('feed.xml')} />
      <link rel="stylesheet" href={url('_scrive/site.css')} />
      {katexCss && <link rel="stylesheet" href={katexCss} crossOrigin="anonymous" />}
      <meta name="generator" content="Scrive" />
    </>
  );
}

export function Document({
  layout,
  siteTitle,
  title,
  description,
  path,
  url,
  canonical,
  image,
  type,
  date,
  nav,
  toc,
  katexCss,
  year,
  children,
}: DocumentProps) {
  return (
    <html lang="en">
      <head>
        <HeadTags
          siteTitle={siteTitle}
          title={title}
          description={description}
          url={url}
          canonical={canonical}
          image={image}
          type={type}
          date={date}
          katexCss={katexCss}
        />
      </head>
      <body className={`site site-${layout}`}>
        <a className="site-skip" href="#main">
          Skip to content
        </a>
        <header className="site-header">
          <div className="site-header-inner">
            <a className="site-brand" href={url('')}>
              {siteTitle}
            </a>
            {nav.length > 0 && (
              <nav className="site-nav" aria-label="Site">
                {nav.map((link) => (
                  <a
                    key={link.path}
                    href={url(link.path)}
                    aria-current={link.path === path ? 'page' : undefined}
                  >
                    {link.title}
                  </a>
                ))}
              </nav>
            )}
          </div>
        </header>
        <div className="site-frame">
          {toc && (
            <aside className="site-toc">
              <nav aria-label="Contents">
                {toc.map((group) => (
                  <div key={group.title} className="site-toc-group">
                    <div className="site-toc-title">{group.title}</div>
                    <ul>
                      {group.links.map((link) => (
                        <li key={link.path}>
                          <a
                            href={url(link.path)}
                            aria-current={link.path === path ? 'page' : undefined}
                          >
                            {link.title}
                          </a>
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}
              </nav>
            </aside>
          )}
          <main id="main" className="site-main">
            {children}
          </main>
        </div>
        <footer className="site-footer">
          <span>
            © {year} {siteTitle}
          </span>
          <a href={url('feed.xml')}>RSS</a>
          <span className="site-meta">Published with Scrive</span>
        </footer>
      </body>
    </html>
  );
}

export function TagList({ tags, url }: { tags: NavLink[]; url: (path: string) => string }) {
  if (!tags.length) return null;
  return (
    <ul className="site-tags">
      {tags.map((tag) => (
        <li key={tag.path}>
          <a href={url(tag.path)}>{tag.title}</a>
        </li>
      ))}
    </ul>
  );
}

export function PostHeader({
  title,
  showTitle,
  date,
  minutes,
  description,
  tags,
  url,
}: {
  title: string;
  /** False when the page's body opens with its own `#` heading. */
  showTitle: boolean;
  date: string;
  minutes: number;
  description: string;
  tags: NavLink[];
  url: (path: string) => string;
}) {
  const meta = [date, minutes > 0 ? `${minutes} min read` : ''].filter(Boolean).join(' · ');
  return (
    <header className="site-post-head">
      {meta && <div className="site-meta">{meta}</div>}
      {showTitle && <h1 className="site-title">{title}</h1>}
      {description && <p className="site-lede">{description}</p>}
      <TagList tags={tags} url={url} />
    </header>
  );
}

export function EntryList({ entries, url }: { entries: Entry[]; url: (path: string) => string }) {
  return (
    <ul className="site-entries">
      {entries.map((entry, i) => (
        <li key={entry.path} className="site-entry" style={{ '--i': i } as CSSProperties}>
          {entry.date ? (
            <time className="site-meta" dateTime={entry.date}>
              {entry.date}
            </time>
          ) : (
            <span className="site-meta">page</span>
          )}
          <a className="site-entry-title" href={url(entry.path)}>
            {entry.title}
          </a>
          {entry.description && <p className="site-entry-desc">{entry.description}</p>}
          <TagList tags={entry.tags} url={url} />
        </li>
      ))}
    </ul>
  );
}

export function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="site-section">
      <h2 className="site-section-title">{title}</h2>
      {children}
    </section>
  );
}

export function PrevNext({
  prev,
  next,
  url,
}: {
  prev?: NavLink;
  next?: NavLink;
  url: (path: string) => string;
}) {
  if (!prev && !next) return null;
  return (
    <nav className="site-prevnext" aria-label="More posts">
      {prev && (
        <a className="is-prev" href={url(prev.path)}>
          <span className="site-meta">Newer</span>
          {prev.title}
        </a>
      )}
      {next && (
        <a className="is-next" href={url(next.path)}>
          <span className="site-meta">Older</span>
          {next.title}
        </a>
      )}
    </nav>
  );
}
