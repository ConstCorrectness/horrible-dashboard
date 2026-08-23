/**
 * The shared *card* primitive — one item in a feed you configure and act on.
 *
 * `DataList` gave "a record you read" one shape, and `Primitives` gave the
 * controls around a list one shape. Neither covers the third thing this app keeps
 * drawing by hand: an item that is a **small form**. A registry entry, a
 * marketplace plugin, a downloadable model — each has an identity, some prose, a
 * command you may want to read before you commit, two or three inputs that decide
 * how it gets installed, and a pair of buttons. A `DataRow` cannot hold that
 * without its head line collapsing, so every such feed grew its own `<div>` with
 * its own padding, its own font sizes and its own idea of where the buttons go.
 *
 * The design lives in the type contract, as it does in the other two files:
 *
 * - **The order of the props is the order on screen.** Identity, then content,
 *   then configuration, then actions. A caller cannot put the buttons above the
 *   summary, because there is no prop that would do it.
 * - **Nothing takes a colour, a size or a `style`.** `kind` is a *meaning* drawn
 *   from `RowKind`, the same vocabulary a row's verdict uses, so `ok` is the same
 *   green in a card as in a row and the reader learns it once.
 * - **Every section is optional and renders nothing when empty.** That is what
 *   makes the component survive the conditional cases — an entry with no install
 *   options, a remote with no arguments, a card with no snippet — instead of
 *   leaving a gap where a border used to be.
 *
 * See `resourcecard.css` for what each part is doing and why.
 */
import type { CSSProperties, ReactNode } from 'react';

import './resourcecard.css';
import { staggerIndex, type RowKind } from './DataList';

/**
 * The feed.
 *
 * A `<ul>` rather than a stack of `<div>`s: a list of peers is a list, and the
 * count is worth announcing. Cards are considerably taller than rows, so the
 * gutter is the card gutter (`--space-5`) rather than the row's hairline gap.
 */
export function ResourceCardList({
  children,
  label,
  style,
}: {
  children: ReactNode;
  /** Accessible name. A catalog and an installed list are different landmarks
   * even when they are drawn the same. */
  label?: string;
  style?: CSSProperties;
}) {
  return (
    <ul className="hd-cardfeed" aria-label={label} style={style}>
      {children}
    </ul>
  );
}

/**
 * A read-only fragment of code — a command, an endpoint, a parameter.
 *
 * It exists so that a command stops being *prose*. `npx -y @foo/bar --root /x`
 * set inline in a sentence is unreadable at a glance and unselectable without
 * catching the words either side of it; in a chip it is one object with one
 * boundary, and the eye finds it without reading.
 *
 * `block` is the multi-line form. It scrolls itself rather than wrapping,
 * because a wrapped command line is a command you will paste wrong.
 *
 * `lead` is the protocol or runner the line starts with — `npx`, `uvx`, `https`.
 * It is a separate prop rather than something parsed out of the string, because
 * only the caller knows whether the first word is a runner or an argument, and a
 * regex that guessed would eventually colour a filename.
 */
export function CodeChip({
  children,
  lead,
  block,
  title,
}: {
  children: ReactNode;
  /** The leading runner/protocol token, set in the accent. */
  lead?: ReactNode;
  block?: boolean;
  title?: string;
}) {
  return (
    <code className="hd-codechip" data-block={block ? 'true' : undefined} title={title}>
      {lead && <span className="hd-codechip-lead">{lead} </span>}
      {children}
    </code>
  );
}

/**
 * A row of inline controls that line up.
 *
 * An auto-fitting grid rather than a flex row, deliberately: a card in this app
 * is as likely to be 300px wide in a side dock as 900px in a document tab, and a
 * flex row of three inputs at 300px produces three unusable 60px boxes. Here they
 * reflow to one per line and stay operable. A `Field` marked `span="full"`
 * (the extra-arguments box, a path) takes the whole width at any size.
 *
 * The uniform height everything shares is `--control-h`, imposed in
 * `primitives.css` on inputs, selects and buttons alike — a select, a text input
 * and a small button have three different intrinsic heights, and a row where they
 * disagree is the loudest sign of an unfinished form.
 */
export function ControlRow({ children }: { children: ReactNode }) {
  return <div className="hd-controlrow">{children}</div>;
}

/**
 * A control that grows, and the buttons that act on it.
 *
 * The search-box-plus-button shape, which every pane had been writing as a flex
 * div with its own gap. Distinct from `ControlRow`: that one lays out *peers*
 * that reflow, this one is a single subject with its operations pinned beside it,
 * and the buttons must never wrap under the input — a "Search" button on its own
 * line reads as unrelated to the box above it.
 */
export function ControlBar({ children }: { children: ReactNode }) {
  return <div className="hd-controlbar">{children}</div>;
}

/**
 * A vertical stack with the card gutter.
 *
 * The container a feed and its controls live in — a search bar, a degraded-state
 * caption, then the cards. It exists so that "the space between the things in
 * this pane" is one number rather than a `marginBottom` on each of them, which is
 * what it was and which is why removing one element left a hole.
 */
export function Stack({ children }: { children: ReactNode }) {
  return <div className="hd-stack">{children}</div>;
}

/**
 * A quiet line of explanatory text.
 *
 * The home for the sentence that qualifies something — a caveat, a degraded
 * state, "this runs code on your machine". Set small and muted so it reads as an
 * aside; the same sentence at body size reads as an error message, which is what
 * these had all become.
 */
export function Caption({
  children,
  tone = 'muted',
}: {
  children: ReactNode;
  /** `warn` and `danger` are for a caption that is a *state*, not an aside. */
  tone?: 'muted' | 'warn' | 'danger';
}) {
  return (
    <p className="hd-caption" data-tone={tone === 'muted' ? undefined : tone}>
      {children}
    </p>
  );
}

/** A tag pinned to a card's header line. `kind` is a meaning, not a colour. */
export interface CardTag {
  label: ReactNode;
  kind?: RowKind;
  /** Leading status dot — for a tag that is a *state* rather than a label. */
  dot?: boolean;
  /** Hover text. The place for the sentence that would otherwise be a caption. */
  title?: string;
}

export interface ResourceCardProps {
  /** Identity. Uppercase and tracked, so keep it short. */
  title: ReactNode;
  /**
   * The canonical machine name — a package id, a fully-qualified server name.
   * Monospace and muted, pinned to the end of the header line, because it is the
   * thing you copy rather than the thing you read.
   */
  identifier?: ReactNode;
  /** Status pills and version tags. Rendered in order, after the title. */
  tags?: CardTag[];
  /** Tints the card's leading rail. `idle` (the default) draws it neutral. */
  kind?: RowKind;
  /** One concise sentence. The publisher's own description belongs here. */
  summary?: ReactNode;
  /**
   * The secondary line — a caveat, a provenance note, a warning. Muted and
   * separate from `summary` rather than appended to it, since the two have
   * different authors and the reader needs to know which is which.
   */
  note?: ReactNode;
  /** `warn` for "this will not work as you expect", `danger` for a failure. */
  noteTone?: 'muted' | 'warn' | 'danger';
  /** The command, endpoint or parameter this item resolves to. A `CodeChip`. */
  snippet?: ReactNode;
  /** Configuration controls. A `ControlRow`, or several. */
  children?: ReactNode;
  /**
   * A quiet caption in the footer, left of the buttons.
   *
   * For the sentence that qualifies an action — "Inspect runs this package on
   * your machine". Set small and muted rather than as body text spanning the
   * action bar, which is what it looked like before and which read as an error.
   */
  caption?: ReactNode;
  /** Buttons. Give exactly one `intent="primary"`, or none. */
  actions?: ReactNode;
  /**
   * Full-width content below the footer: a result, an error, an expanded
   * inspection. Outside the footer because it is an outcome, not a control.
   */
  footer?: ReactNode;
  /** Position in the feed, for the entrance stagger. Capped by `staggerIndex`. */
  index?: number;
}

/**
 * One item in the feed.
 *
 * A `<li>` holding four bands in a fixed order — header, body, configuration,
 * footer — each of which disappears entirely when it has nothing in it.
 */
export function ResourceCard({
  title,
  identifier,
  tags,
  kind = 'idle',
  summary,
  note,
  noteTone = 'muted',
  snippet,
  children,
  caption,
  actions,
  footer,
  index = 0,
}: ResourceCardProps) {
  const hasBody = Boolean(summary || note || snippet);
  const hasFoot = Boolean(caption || actions);
  const style = { '--hd-i': staggerIndex(index) } as CSSProperties;

  return (
    <li className="hd-card" data-kind={kind === 'idle' ? undefined : kind} style={style}>
      <div className="hd-card-head">
        <span className="hd-card-title">{title}</span>
        {tags && tags.length > 0 && (
          <span className="hd-card-tags">
            {tags.map((t, i) => (
              <span
                key={i}
                className="hd-chip"
                data-kind={t.kind && t.kind !== 'idle' ? t.kind : undefined}
                title={t.title}
              >
                {t.dot && <span className="hd-chip-dot" />}
                {t.label}
              </span>
            ))}
          </span>
        )}
        <span className="hd-card-spacer" />
        {identifier && <code className="hd-card-id">{identifier}</code>}
      </div>

      {hasBody && (
        <div className="hd-card-body">
          {summary && <p className="hd-card-summary">{summary}</p>}
          {note && (
            <p className="hd-card-note" data-tone={noteTone === 'muted' ? undefined : noteTone}>
              {note}
            </p>
          )}
          {snippet}
        </div>
      )}

      {children && <div className="hd-card-config">{children}</div>}

      {hasFoot && (
        <div className="hd-card-foot">
          {caption && <span className="hd-card-caption">{caption}</span>}
          <span className="hd-card-spacer" />
          {actions && <span className="hd-card-actions">{actions}</span>}
        </div>
      )}

      {footer && <div className="hd-card-extra">{footer}</div>}
    </li>
  );
}
