/**
 * The outline an agent proposed for a new page, for a person to review before
 * anything is written (Kombai's plan mode).
 *
 * Every part is editable — title, lead, description, tags, and the sections
 * (heading, level, intent, planned figure), which can be reordered, added and
 * dropped. Edits are saved to the outline as they are made. **Approve and write**
 * creates the page (headings over `{pending}` placeholders), opens it, and sends
 * the agent the instruction to fill it, so the sections arrive one by one in the
 * open editor. **Discard** drops it. Neither is something the agent can do itself.
 */
import { useCallback, useEffect, useRef, useState } from 'react';

import { usePaneParams } from '../../../panes';
import { subscribeChannel } from '../../../ws';
import { sendInChat } from '../../agent/openSession';
import {
  approveOutline,
  discardOutline,
  getOutline,
  SCRIVE_CHANNEL,
  updateOutline,
  type Outline,
  type OutlineChanged,
  type OutlineEdit,
  type OutlineSection,
} from '../api';
import { CheckIcon, CloseIcon, PlusIcon, SortDownIcon, SortUpIcon } from '../icons';
import { openScrivePage } from '../open';
import { fillPrompt } from '../prompts';
import '../scrive.css';

/** How long edits settle before they are saved to the outline. */
const SAVE_DEBOUNCE_MS = 500;
const STAGGER_MS = 24;
const STAGGER_CAP = 10;

function message(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export function OutlinePanel() {
  const params = usePaneParams();
  const site = String(params.site ?? '');
  const id = String(params.id ?? '');
  if (!site || !id) {
    return <div style={{ padding: 'var(--space-4)', color: 'var(--text-dim)' }}>No outline.</div>;
  }
  return <OutlineEditor key={`${site}/${id}`} site={site} id={id} />;
}

function OutlineEditor({ site, id }: { site: string; id: string }) {
  const [outline, setOutline] = useState<Outline | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const pending = useRef<OutlineEdit>({});
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const load = useCallback(() => {
    getOutline(site, id).then(setOutline, (e: unknown) => setError(message(e)));
  }, [site, id]);

  useEffect(load, [load]);
  // Approved or discarded somewhere else (another window): show it.
  useEffect(
    () =>
      subscribeChannel(SCRIVE_CHANNEL, (msg) => {
        if (msg.event !== 'outline.changed') return;
        const event = msg.data as OutlineChanged;
        if (event.site === site && event.id === id && event.status !== 'proposed') load();
      }),
    [site, id, load],
  );

  const flush = useCallback(async () => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    const edit = pending.current;
    pending.current = {};
    if (!Object.keys(edit).length) return;
    try {
      await updateOutline(site, id, edit);
    } catch (e) {
      setError(message(e));
    }
  }, [site, id]);

  useEffect(
    () => () => {
      void flush();
    },
    [flush],
  );

  const edit = (patch: OutlineEdit) => {
    setOutline((o) => (o ? { ...o, ...patch } : o));
    pending.current = { ...pending.current, ...patch };
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => void flush(), SAVE_DEBOUNCE_MS);
  };

  if (!outline) {
    return (
      <div
        style={{ padding: 'var(--space-4)', color: error ? 'var(--danger)' : 'var(--text-dim)' }}
      >
        {error ?? 'Loading…'}
      </div>
    );
  }

  const editable = outline.status === 'proposed';
  const sections = outline.sections;
  const setSections = (next: OutlineSection[]) => edit({ sections: next });
  const setSection = (i: number, patch: Partial<OutlineSection>) =>
    setSections(sections.map((s, j) => (j === i ? { ...s, ...patch } : s)));
  const move = (i: number, by: number) => {
    const j = i + by;
    if (j < 0 || j >= sections.length) return;
    const next = sections.slice();
    [next[i], next[j]] = [next[j], next[i]];
    setSections(next);
  };

  const approve = async () => {
    setBusy(true);
    if (timer.current) clearTimeout(timer.current);
    pending.current = {};
    try {
      const { outline: done, page } = await approveOutline(site, id, {
        title: outline.title,
        lead: outline.lead,
        description: outline.description,
        tags: outline.tags,
        sections: outline.sections,
      });
      setOutline(done);
      openScrivePage(site, page.meta.path);
      sendInChat(fillPrompt(done, page.meta.path));
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  };

  const discard = async () => {
    setBusy(true);
    if (timer.current) clearTimeout(timer.current);
    pending.current = {};
    try {
      setOutline(await discardOutline(site, id));
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="scrive-outline">
      <header className="scrive-outline-bar">
        <div style={{ minWidth: 0 }}>
          <div className="scrive-head">Outline</div>
          <div
            className="scrive-meta"
            style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}
          >
            {site} · {outline.kind}
            {outline.template && ` · template ${outline.template}`} · {outline.status}
          </div>
        </div>
        {editable ? (
          <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
            <button type="button" onClick={() => void discard()} disabled={busy}>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                <CloseIcon /> Discard
              </span>
            </button>
            <button
              type="button"
              style={{ borderColor: 'var(--accent)', color: 'var(--accent)' }}
              onClick={() => void approve()}
              disabled={busy || !outline.title.trim() || !sections.some((s) => s.heading.trim())}
              title="Create the page from this outline and ask the agent to write it"
            >
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                <CheckIcon /> Approve and write
              </span>
            </button>
          </div>
        ) : (
          outline.page && (
            <button type="button" onClick={() => openScrivePage(site, outline.page)}>
              Open page
            </button>
          )
        )}
      </header>

      {error && (
        <div style={{ padding: 'var(--space-2) var(--space-3)', color: 'var(--danger)' }}>
          {error}
        </div>
      )}

      <div className="scrive-outline-body">
        <fieldset className="scrive-outline-fields" disabled={!editable}>
          <label className="scrive-outline-field is-title">
            <span className="scrive-head">Title</span>
            <input
              type="text"
              value={outline.title}
              onChange={(e) => edit({ title: e.target.value })}
              style={{ padding: '0 0.6rem' }}
            />
          </label>
          <label className="scrive-outline-field">
            <span className="scrive-head">Lead</span>
            <textarea
              className="scrive-outline-text"
              rows={2}
              placeholder="What the opening paragraph says"
              value={outline.lead}
              onChange={(e) => edit({ lead: e.target.value })}
            />
          </label>
          <div className="scrive-outline-pair">
            <label className="scrive-outline-field">
              <span className="scrive-head">Description</span>
              <input
                type="text"
                value={outline.description}
                placeholder="One sentence for link cards"
                onChange={(e) => edit({ description: e.target.value })}
                style={{ padding: '0 0.6rem' }}
              />
            </label>
            <label className="scrive-outline-field">
              <span className="scrive-head">Tags</span>
              <input
                type="text"
                value={outline.tags.join(', ')}
                placeholder="comma, separated"
                onChange={(e) =>
                  edit({
                    tags: e.target.value
                      .split(',')
                      .map((t) => t.trim())
                      .filter(Boolean),
                  })
                }
                style={{ padding: '0 0.6rem' }}
              />
            </label>
          </div>

          <div className="scrive-outline-sections-head">
            <span className="scrive-head">Sections</span>
            <span className="scrive-meta">{sections.length}</span>
          </div>
          <ol className="scrive-outline-sections">
            {sections.map((section, i) => (
              <li
                key={i}
                className="scrive-outline-section"
                data-level={section.level}
                style={{ animationDelay: `${Math.min(i, STAGGER_CAP) * STAGGER_MS}ms` }}
              >
                <div className="scrive-outline-row">
                  <span className="scrive-meta scrive-outline-index">
                    {String(i + 1).padStart(2, '0')}
                  </span>
                  <select
                    aria-label="Heading level"
                    value={section.level}
                    onChange={(e) => setSection(i, { level: Number(e.target.value) })}
                    style={{ padding: '0 0.6rem', width: '4.2rem' }}
                  >
                    {[2, 3, 4].map((level) => (
                      <option key={level} value={level}>
                        H{level}
                      </option>
                    ))}
                  </select>
                  <input
                    type="text"
                    aria-label="Heading"
                    className="scrive-outline-heading"
                    value={section.heading}
                    onChange={(e) => setSection(i, { heading: e.target.value })}
                    style={{ padding: '0 0.6rem' }}
                  />
                  <button
                    type="button"
                    className="btn-mini"
                    aria-label="Move up"
                    title="Move up"
                    disabled={i === 0}
                    onClick={() => move(i, -1)}
                  >
                    <SortUpIcon size={12} />
                  </button>
                  <button
                    type="button"
                    className="btn-mini"
                    aria-label="Move down"
                    title="Move down"
                    disabled={i === sections.length - 1}
                    onClick={() => move(i, 1)}
                  >
                    <SortDownIcon size={12} />
                  </button>
                  <button
                    type="button"
                    className="btn-mini"
                    aria-label="Remove section"
                    title="Remove section"
                    onClick={() => setSections(sections.filter((_, j) => j !== i))}
                  >
                    <CloseIcon size={12} />
                  </button>
                </div>
                <textarea
                  className="scrive-outline-text"
                  aria-label="What this section says"
                  rows={2}
                  placeholder="What this section says or shows"
                  value={section.intent}
                  onChange={(e) => setSection(i, { intent: e.target.value })}
                />
                <input
                  type="text"
                  aria-label="Planned figure"
                  className="scrive-outline-figure"
                  placeholder="Figure, scene, table or code cell (optional)"
                  value={section.figure}
                  onChange={(e) => setSection(i, { figure: e.target.value })}
                  style={{ padding: '0 0.6rem' }}
                />
              </li>
            ))}
          </ol>
          <button
            type="button"
            className="scrive-outline-add"
            onClick={() =>
              setSections([
                ...sections,
                { heading: 'New section', intent: '', level: 2, figure: '' },
              ])
            }
          >
            <PlusIcon size={12} /> add section
          </button>
        </fieldset>
        {outline.prompt && (
          <p className="scrive-meta scrive-outline-request">request: {outline.prompt}</p>
        )}
      </div>
    </div>
  );
}
