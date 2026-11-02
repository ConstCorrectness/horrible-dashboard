/**
 * One provider call, as the provider received it.
 *
 * Left, a minimap: the request top to bottom in send order (`tools[]`, then every
 * message, then the window left over), each cell as tall as its tokens. Hovering a
 * cell opens a popover — role, index, where it came from, tokens, new or carried,
 * the call it answers — and clicking scrolls the raw pane to it. Right, the raw
 * request: each message's JSON, with a joined system message tinted by the parts it
 * was joined from. Hovering a tool result lights up the call it answers, and back.
 *
 * Roles are the wire's four. "Skill catalog" is a tint on a `system` message, never
 * a fifth role.
 */
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';

import { IconCopy } from '../../../glyphs';
import { RollingCounter } from '../../../viz/RollingCounter';
import type { RequestDetail, RequestMessage } from '../../interpretability/requests';
import { mono } from './common';
import {
  buildMinimap,
  contentPieces,
  contentText,
  messageFamily,
  newIndexes,
  pairToolCalls,
  partnersOf,
  popoverFor,
  requestLabel,
  templateGap,
  type Minimap,
  type PopoverModel,
} from './request-model';

const fmt = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(Math.round(n)));
/** Strings longer than this fold behind a "more" control. */
const FOLD = 6000;

export function RequestInspector({
  detail,
  prev,
  focus,
}: {
  detail: RequestDetail;
  /** The call before this one in the turn, for new-vs-carried. */
  prev: RequestDetail | null;
  /** Scroll to and flash a message. `nonce` re-triggers the same index. */
  focus?: { index: number; nonce: number } | null;
}) {
  const fresh = useMemo(
    () => newIndexes(prev?.messages, detail.messages),
    [prev?.messages, detail.messages],
  );
  const pairing = useMemo(() => pairToolCalls(detail.messages), [detail.messages]);
  const map = useMemo(() => buildMinimap(detail, fresh), [detail, fresh]);
  const [hovered, setHovered] = useState<number | null>(null);
  const [popover, setPopover] = useState<{ model: PopoverModel; top: number } | null>(null);
  const [escaped, setEscaped] = useState(false);
  const [flash, setFlash] = useState<number | null>(null);

  const bodyRef = useRef<HTMLDivElement>(null);
  const rawRef = useRef<HTMLDivElement>(null);
  const rawEls = useRef(new Map<string, HTMLElement>());
  const cellEls = useRef(new Map<string, HTMLElement>());
  const [viewport, setViewport] = useState<{ top: number; height: number } | null>(null);

  const hot = useMemo(() => {
    if (hovered == null) return new Set<number>();
    return new Set([hovered, ...partnersOf(hovered, pairing)]);
  }, [hovered, pairing]);

  const scrollTo = (index: number) => {
    const el = rawEls.current.get(`m${index}`);
    if (!el || !rawRef.current) return;
    rawRef.current.scrollTop = el.offsetTop - 8;
    setFlash(index);
  };

  useEffect(() => {
    if (focus) scrollTo(focus.index);
  }, [focus?.index, focus?.nonce, detail.id]);

  useEffect(() => {
    if (flash == null) return;
    const t = setTimeout(() => setFlash(null), 1400);
    return () => clearTimeout(t);
  }, [flash]);

  // The viewport indicator: which stretch of the request the raw pane is showing,
  // drawn on the minimap. Mapped element by element, because the two columns scale
  // differently (the map by tokens, the pane by rendered lines).
  const syncViewport = () => {
    const raw = rawRef.current;
    if (!raw) return;
    const entries = map.items
      .filter((it) => it.key !== 'free')
      .map((it) => [rawEls.current.get(it.key), cellEls.current.get(it.key)] as const)
      .filter((pair): pair is readonly [HTMLElement, HTMLElement] => !!pair[0] && !!pair[1]);
    if (!entries.length) return setViewport(null);
    const mapY = (y: number) => {
      for (const [r, c] of entries) {
        if (y < r.offsetTop + r.offsetHeight) {
          const frac = Math.min(1, Math.max(0, (y - r.offsetTop) / Math.max(1, r.offsetHeight)));
          return c.offsetTop + frac * c.offsetHeight;
        }
      }
      const [, last] = entries[entries.length - 1];
      return last.offsetTop + last.offsetHeight;
    };
    const top = mapY(raw.scrollTop);
    const bottom = mapY(raw.scrollTop + raw.clientHeight);
    setViewport({ top, height: Math.max(4, bottom - top) });
  };

  useLayoutEffect(() => {
    syncViewport();
    const raw = rawRef.current;
    if (!raw || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(() => syncViewport());
    ro.observe(raw);
    return () => ro.disconnect();
  }, [detail.id, map, escaped]);

  const openPopover = (index: number, cell: HTMLElement) => {
    const model = popoverFor(detail, index, pairing, fresh);
    if (!model) return;
    const bodyHeight = bodyRef.current?.clientHeight ?? 400;
    const top = Math.max(0, Math.min(cell.offsetTop, bodyHeight - 280));
    setPopover({ model, top });
  };

  const gap = templateGap(map.used, detail.promptTokens);

  return (
    <div className="traj-insp">
      <InspectorHead detail={detail} map={map} gap={gap} escaped={escaped} onEscaped={setEscaped} />
      {detail.reconstructed ? (
        <div className="traj-insp-banner" role="note">
          <strong>Reconstructed · not literal</strong>
          {detail.notes.map((n) => (
            <span key={n}>{n}</span>
          ))}
        </div>
      ) : null}
      <div className="traj-insp-body" ref={bodyRef}>
        <div className="traj-insp-map" aria-label="Request minimap">
          {map.items.map((it) => (
            <button
              type="button"
              key={it.key}
              ref={(el) => {
                if (el) cellEls.current.set(it.key, el);
                else cellEls.current.delete(it.key);
              }}
              className="traj-insp-cell"
              data-family={it.family}
              data-new={it.isNew || undefined}
              data-hot={(it.index != null && hot.has(it.index)) || undefined}
              style={{ flexGrow: it.grow }}
              aria-label={`${it.label}, ${fmt(it.tokens)} tokens`}
              onMouseEnter={(e) => {
                if (it.index == null) return;
                setHovered(it.index);
                openPopover(it.index, e.currentTarget);
              }}
              onMouseLeave={() => {
                setHovered(null);
                setPopover(null);
              }}
              onClick={() => {
                if (it.index != null) scrollTo(it.index);
                else if (it.key === 'tools' && rawRef.current) rawRef.current.scrollTop = 0;
              }}
            />
          ))}
          {viewport ? (
            <span
              className="traj-insp-viewport"
              style={{ top: viewport.top, height: viewport.height }}
              aria-hidden
            />
          ) : null}
        </div>
        <div className="traj-insp-raw" ref={rawRef} onScroll={syncViewport}>
          <ToolsBlock
            detail={detail}
            escaped={escaped}
            refCb={(el) => {
              if (el) rawEls.current.set('tools', el);
              else rawEls.current.delete('tools');
            }}
          />
          {detail.messages.map((m, i) => (
            <MessageBlock
              key={`${m.hash}-${m.index}`}
              message={m}
              index={i}
              escaped={escaped}
              isNew={fresh?.has(m.index) ?? false}
              hot={hot.has(m.index)}
              flash={flash === m.index}
              partner={pairing.callFor.get(m.index)}
              onHover={setHovered}
              refCb={(el) => {
                if (el) rawEls.current.set(`m${m.index}`, el);
                else rawEls.current.delete(`m${m.index}`);
              }}
            />
          ))}
        </div>
        {popover ? <Popover model={popover.model} top={popover.top} /> : null}
      </div>
    </div>
  );
}

function InspectorHead({
  detail,
  map,
  gap,
  escaped,
  onEscaped,
}: {
  detail: RequestDetail;
  map: Minimap;
  gap: number | null;
  escaped: boolean;
  onEscaped: (v: boolean) => void;
}) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    const body = {
      ...detail.params,
      messages: detail.messages.map((m) => m.body),
      ...(detail.tools.length ? { tools: detail.tools } : {}),
    };
    try {
      await navigator.clipboard.writeText(JSON.stringify(body, null, 2));
      setCopied(true);
      setTimeout(() => setCopied(false), 1200);
    } catch {
      // No clipboard (an embedded pane without the permission): nothing to report.
    }
  };
  const params = Object.entries(detail.params).filter(
    ([k]) => k !== 'model' && k !== 'stream' && k !== 'stream_options',
  );
  return (
    <div className="traj-insp-head">
      <div className="traj-insp-head-row">
        <span className="traj-insp-req">{requestLabel(detail)}</span>
        <span style={mono}>
          {detail.dialect || detail.provider} · {detail.model}
        </span>
        <span style={mono} className="traj-insp-status" data-status={detail.status}>
          {detail.status}
        </span>
        <span style={{ flex: 1 }} />
        <button
          type="button"
          className="traj-follow-pin"
          aria-pressed={escaped}
          onClick={() => onEscaped(!escaped)}
          title="Show strings exactly as JSON-escaped on the wire"
        >
          {escaped ? 'escaped' : 'readable'}
        </button>
        <button
          type="button"
          className="traj-follow-pin"
          onClick={() => void copy()}
          title="Copy the request body as JSON"
          aria-label="Copy request JSON"
        >
          <IconCopy width={12} />
          {copied ? 'copied' : 'json'}
        </button>
      </div>
      <div className="traj-insp-head-row traj-insp-counts">
        <span className="traj-insp-count">
          <span className="traj-insp-k">Est</span>
          <RollingCounter value={map.used} format={fmt} className="traj-follow-num" />
          {detail.exact ? null : <span className="traj-insp-dim">est</span>}
        </span>
        <span className="traj-insp-count">
          <span className="traj-insp-k">Provider</span>
          {detail.promptTokens != null ? (
            <RollingCounter value={detail.promptTokens} format={fmt} className="traj-follow-num" />
          ) : (
            <span className="traj-insp-dim">not reported</span>
          )}
        </span>
        {gap != null ? (
          <span
            className="traj-insp-count"
            title="Provider count minus the estimate: the chat template's own role headers and special tokens, plus whatever the estimate missed"
          >
            <span className="traj-insp-k">Gap</span>
            <span className="traj-follow-num">
              {gap >= 0 ? '+' : ''}
              {fmt(gap)}
            </span>
          </span>
        ) : null}
        <span className="traj-insp-count">
          <span className="traj-insp-k">Window</span>
          <span className="traj-follow-num">
            {map.window
              ? `${Math.round((map.used / map.window) * 100)}% of ${fmt(map.window)}`
              : 'unknown'}
          </span>
        </span>
        {detail.completionTokens != null ? (
          <span className="traj-insp-count">
            <span className="traj-insp-k">Out</span>
            <span className="traj-follow-num">{fmt(detail.completionTokens)}</span>
          </span>
        ) : null}
      </div>
      {params.length ? (
        <div className="traj-insp-params" style={mono}>
          {params.map(([k, v]) => (
            <span key={k}>
              {k}={typeof v === 'object' ? JSON.stringify(v) : String(v)}
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function Popover({ model, top }: { model: PopoverModel; top: number }) {
  return (
    <div className="traj-insp-pop" style={{ top }} role="tooltip">
      <div className="traj-insp-pop-title">
        <span>{model.title}</span>
        <span style={{ flex: 1 }} />
        {model.status ? (
          <span className="traj-insp-pop-status" data-status={model.status}>
            {model.status}
          </span>
        ) : null}
        <span className="traj-follow-num">
          {fmt(model.tokens)} tok{model.exact ? '' : ' est'}
        </span>
      </div>
      {model.roleNote ? <div className="traj-insp-pop-note">{model.roleNote}</div> : null}
      {model.provenance.length ? (
        <div className="traj-insp-pop-prov">
          {model.provenance.map((p, i) => (
            <span key={i} data-family={p.family} className="traj-insp-pop-src">
              {p.label}
              {p.toolName ? ` · ${p.toolName}` : ''}
              <span className="traj-insp-dim"> {fmt(p.chars)} ch</span>
            </span>
          ))}
        </div>
      ) : null}
      {model.partner ? <div className="traj-insp-pop-note">{model.partner}</div> : null}
      <pre className="traj-insp-pop-pre">
        {model.preview.join('\n')}
        {model.more ? `\n… ${model.more} more lines` : ''}
      </pre>
    </div>
  );
}

function ToolsBlock({
  detail,
  escaped,
  refCb,
}: {
  detail: RequestDetail;
  escaped: boolean;
  refCb: (el: HTMLElement | null) => void;
}) {
  const [open, setOpen] = useState(false);
  if (!detail.tools.length) return null;
  const json = JSON.stringify(detail.tools, null, escaped ? undefined : 2);
  return (
    <section className="traj-insp-msg" data-family="tools" ref={refCb}>
      <button
        type="button"
        className="traj-insp-msg-head"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        <span className="traj-insp-role">tools[]</span>
        <span style={mono}>
          {detail.tools.length} schemas · a separate field of the request, not a message
        </span>
        <span style={{ flex: 1 }} />
        <span style={mono}>{fmt(detail.toolsTokens)} tok</span>
      </button>
      {open ? (
        <pre className="traj-insp-pre">
          <Folded text={json} />
        </pre>
      ) : null}
    </section>
  );
}

function MessageBlock({
  message: m,
  index,
  escaped,
  isNew,
  hot,
  flash,
  partner,
  onHover,
  refCb,
}: {
  message: RequestMessage;
  index: number;
  escaped: boolean;
  isNew: boolean;
  hot: boolean;
  flash: boolean;
  partner: { message: number; name: string } | undefined;
  onHover: (i: number | null) => void;
  refCb: (el: HTMLElement | null) => void;
}) {
  const family = messageFamily(m);
  return (
    <section
      ref={refCb}
      className="traj-insp-msg traj-in"
      data-family={family}
      data-new={isNew || undefined}
      data-hot={hot || undefined}
      data-flash={flash || undefined}
      style={{ ['--traj-i' as string]: Math.min(index, 10) }}
      onMouseEnter={() => onHover(m.index)}
      onMouseLeave={() => onHover(null)}
    >
      <div className="traj-insp-msg-head">
        <span style={mono}>#{m.index}</span>
        <span className="traj-insp-role">{m.role}</span>
        {m.fromRole ? (
          <span className="traj-insp-was" title="The orchestrator assembled it with this role">
            was {m.fromRole}
          </span>
        ) : null}
        {m.segments.length === 1 ? (
          <span className="traj-insp-src">{m.segments[0].label}</span>
        ) : m.segments.length > 1 ? (
          <span className="traj-insp-src">joined from {m.segments.length} parts</span>
        ) : null}
        {partner ? (
          <span style={mono}>
            ← #{partner.message} {partner.name}
          </span>
        ) : null}
        <span style={{ flex: 1 }} />
        {isNew ? <span className="traj-follow-new">new</span> : null}
        <span style={mono}>{fmt(m.tokens)} tok</span>
      </div>
      <pre className="traj-insp-pre">
        {escaped ? <Folded text={JSON.stringify(m.body)} /> : <ReadableBody message={m} />}
      </pre>
    </section>
  );
}

/** A message's JSON with its string values shown as text rather than one escaped
 *  line, so a 4000-character system prompt reads as a prompt. Key order is the
 *  body's own; the `escaped` toggle shows the strict form. */
function ReadableBody({ message: m }: { message: RequestMessage }) {
  const entries = Object.entries(m.body);
  return (
    <>
      {'{\n'}
      {entries.map(([key, value], i) => {
        const comma = i < entries.length - 1 ? ',' : '';
        if (key === 'content' && typeof value === 'string') {
          return (
            <span key={key}>
              {`  "content": `}
              <Content text={contentText(m.body)} message={m} />
              {`${comma}\n`}
            </span>
          );
        }
        if (typeof value === 'string' && value.length > 120) {
          return (
            <span key={key}>
              {`  "${key}": `}
              <span className="traj-insp-str">
                <Folded text={value} />
              </span>
              {`${comma}\n`}
            </span>
          );
        }
        const json = JSON.stringify(value, null, 2).replace(/\n/g, '\n  ');
        return <span key={key}>{`  "${key}": ${json}${comma}\n`}</span>;
      })}
      {'}'}
    </>
  );
}

function Content({ text, message }: { text: string; message: RequestMessage }) {
  const pieces = contentPieces(text, message.segments);
  if (pieces.length === 1) {
    return (
      <span className="traj-insp-str">
        <Folded text={text} />
      </span>
    );
  }
  return (
    <span className="traj-insp-str">
      {pieces.map((p, i) =>
        p.family ? (
          <span
            key={i}
            className="traj-insp-piece"
            data-family={p.family}
            title={p.label ?? undefined}
          >
            <span className="traj-insp-piece-tag">
              {p.label}
              {p.clipped ? ' · clipped' : ''}
            </span>
            <Folded text={p.text} />
          </span>
        ) : (
          <span key={i}>{p.text}</span>
        ),
      )}
    </span>
  );
}

function Folded({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  if (text.length <= FOLD || open) return <>{text}</>;
  return (
    <>
      {text.slice(0, FOLD)}
      <button type="button" className="traj-insp-more" onClick={() => setOpen(true)}>
        … {text.length - FOLD} more chars
      </button>
    </>
  );
}

